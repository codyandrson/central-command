"""Story 8 of epic-outage-doctrine: a waiting approved proposal is LISTED, says
what it waits on, and can be dismissed without re-running anything.

The cockpit hand-declares its interfaces over untyped payloads, so a field the
gateway never sends is invisible to `tsc` and to every frontend test
(.claude/rules/cockpit.md). These pin, from the backend, the `waiting` block on
GET /proposals/{id} and on the `decisions.list` waiting rows, the list's own
shape (and that it is NOT the pending list), and the dismiss rule:

- dismissing a RETRY_PENDING proposal withdraws the EXECUTION: WITHDRAWN, folds
  released, drafter session closed and never resumed, `retry_state` kept, a
  distinct `proposal.execution_dismissed` and NO second `proposal.decided`;
- the task resolves FAILED with the honest count (the operator's ruling,
  2026-10-27), never DONE "no action needed";
- the transition is a SQL guard, so a dismiss racing a retry's claim cannot both
  withdraw and execute.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder

from central_command import events
from central_command.api import nerve_gateway, routes
from central_command.config import settings
from central_command.db import repo
from central_command.gateway import gateway
from tests.conftest import needs_pg

pytestmark = needs_pg

WAITING_FIELDS = {
    "dependencies", "down_since", "attempts", "next_retry", "interrupted",
    "completed_actions", "failing_despite_probe", "last_error",
}


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "executor_mode", "dry_run")


async def _parked(*, state: dict | None = None, actions: int = 2,
                  with_task: bool = False) -> dict:
    """An approved proposal parked RETRY_PENDING: the operator's decision is on
    the log, the first action landed, the second waits."""
    pid = "prop_wait_" + uuid.uuid4().hex[:10]
    sid = "sess_wait_" + uuid.uuid4().hex[:8]
    await repo.create_running_session(sid, "jira-expert")
    await repo.save_proposal(
        pid, sid, "jira-expert", "Comment on DEMO-1, then record it",
        [{"capability": "jira.add_comment" if i == 0 else "graph.add_episode",
          "arguments": {}, "target_ref": {"system": "x"}, "reversibility": "reversible"}
         for i in range(actions)],
        [], "DEMO-1 is commented", pid,
    )
    await events.emit("proposal.decided", ref_id=pid,
                      payload={"verdict": "approved", "session_id": sid},
                      actor="human:lee")
    retry_state = {
        "completed_results": ["DEMO-1 commented"], "next_action_index": 1,
        "approver": "human:lee", "attempts": 2, "failed_capability": "graph.add_episode",
        "dependencies": ["graph", "embedder"], "last_error": "Connection refused",
        "not_before": (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat(),
        **(state or {}),
    }
    await repo.park_proposal_retry(pid, retry_state)
    out = {"proposal_id": pid, "session_id": sid}
    if with_task:
        task_id = "task_" + uuid.uuid4().hex[:12]
        await repo.create_task(task_id, "wait test", "x", "jira-expert")
        await repo.start_task(task_id, sid)
        out["task_id"] = task_id
    return out


# --- the wire shape --------------------------------------------------------------------


async def test_get_proposal_carries_the_waiting_block():
    parked = await _parked()
    await repo.record_dependency_probe("embedder", False, "refused")
    down_since = (await repo.dependency_probe_states())["embedder"]["down_since"]

    row = jsonable_encoder(await routes.get_proposal(parked["proposal_id"]))

    assert row["status"] == "RETRY_PENDING"
    waiting = row["waiting"]
    assert set(waiting) == WAITING_FIELDS, set(waiting) ^ WAITING_FIELDS
    assert waiting["dependencies"] == ["graph", "embedder"]
    assert waiting["down_since"] == down_since.isoformat(), "the earliest among the down ones"
    assert waiting["attempts"] == 2
    assert waiting["next_retry"]
    assert waiting["interrupted"] is False
    assert waiting["completed_actions"] == 1
    assert waiting["failing_despite_probe"] is False
    assert waiting["last_error"] == "Connection refused"


async def test_waiting_is_null_for_every_other_status_and_down_since_for_an_up_dependency():
    parked = await _parked()
    # Nothing recorded down: a missing row is up, so there is no "down since".
    row = jsonable_encoder(await routes.get_proposal(parked["proposal_id"]))
    assert row["waiting"]["down_since"] is None

    await repo.set_proposal_status(parked["proposal_id"], "EXECUTED")
    assert jsonable_encoder(await routes.get_proposal(parked["proposal_id"]))["waiting"] is None


async def test_an_interrupted_proposal_says_so():
    parked = await _parked(state={"interrupted": True})
    row = jsonable_encoder(await routes.get_proposal(parked["proposal_id"]))
    assert row["waiting"]["interrupted"] is True


async def test_failing_despite_a_healthy_probe_comes_from_the_loud_rule():
    parked = await _parked(state={"attempts": settings.retry_loud_after_attempts})
    row = jsonable_encoder(await routes.get_proposal(parked["proposal_id"]))
    assert row["waiting"]["failing_despite_probe"] is True

    # A dependency recorded down is an outage, not a unit failing despite a
    # healthy probe.
    await repo.record_dependency_probe("graph", False, "refused")
    row = jsonable_encoder(await routes.get_proposal(parked["proposal_id"]))
    assert row["waiting"]["failing_despite_probe"] is False


async def test_decisions_list_carries_a_waiting_list_that_is_not_the_pending_list():
    parked = await _parked()
    payload = jsonable_encoder(await nerve_gateway._decisions_list())

    ids = [p["id"] for p in payload["waiting"]]
    assert parked["proposal_id"] in ids
    assert parked["proposal_id"] not in [p["id"] for p in payload["proposals"]], (
        "a decision already made does not need one: the 'needs your decision' "
        "badge counts `proposals`, never `waiting`")
    mine = next(p for p in payload["waiting"] if p["id"] == parked["proposal_id"])
    assert mine["status"] == "RETRY_PENDING"
    assert set(mine["waiting"]) == WAITING_FIELDS
    assert mine["waiting"]["dependencies"] == ["graph", "embedder"]


async def test_the_waiting_list_is_oldest_first():
    first = await _parked()
    second = await _parked()
    conn = await repo._conn()
    try:
        await conn.execute(
            "update proposal set decided_at = now() - interval '1 hour' where id = $1",
            first["proposal_id"])
    finally:
        await conn.close()
    ids = [p["id"] for p in jsonable_encoder(await nerve_gateway._decisions_list())["waiting"]]
    assert ids.index(first["proposal_id"]) < ids.index(second["proposal_id"])


async def test_the_session_wait_line_names_the_dependencies():
    parked = await _parked()
    line = await nerve_gateway.parked_proposal_wait_line(parked["proposal_id"])
    assert "graph, embedder" in line and line.startswith("paused")

    interrupted = await _parked(state={"interrupted": True})
    assert "interrupted" in await nerve_gateway.parked_proposal_wait_line(
        interrupted["proposal_id"])


# --- dismissing a waiting execution -------------------------------------------------------------


async def test_dismissing_a_waiting_proposal_withdraws_the_execution(monkeypatch):
    parked = await _parked(with_task=True)
    pid, sid, task_id = parked["proposal_id"], parked["session_id"], parked["task_id"]
    before = (await repo.load_proposal(pid))["retry_state"]

    def _boom(*a, **k):
        raise AssertionError("dismiss must not resume the agent")

    monkeypatch.setattr(gateway, "build_agent_for", _boom)

    out = await gateway.dismiss_proposal(sid, pid, "not needed any more")

    assert out["ok"] is True and out["status"] == "WITHDRAWN"
    assert out["completed_actions"] == 1 and out["actions_total"] == 2
    row = await repo.load_proposal(pid)
    assert row["status"] == "WITHDRAWN"
    assert row["retry_state"] == before, "retry_state is kept on the row for the record"
    session = await repo.get_session(sid)
    assert session["status"] == "DONE" and session["closed_reason"] == "dismissed"

    kinds = [e["kind"] for e in await repo.list_events(ref_id=pid, limit=500)]
    assert kinds.count("proposal.decided") == 1, "the decision stands; no second one"
    dismissed = [e for e in await repo.list_events(ref_id=pid, limit=500)
                 if e["kind"] == "proposal.execution_dismissed"]
    assert len(dismissed) == 1
    assert dismissed[0]["payload"]["proposal"] == pid
    assert dismissed[0]["payload"]["completed_actions"] == 1
    assert dismissed[0]["payload"]["actions_total"] == 2
    assert dismissed[0]["actor"] == "operator"

    task = await repo.get_task(task_id)
    assert task["status"] == "FAILED", "never DONE 'no action needed' for a waiting execution"
    assert task["outcome"] == (
        "operator withdrew the waiting execution; 1 of 2 actions had landed")


async def test_the_event_precedes_the_task_resolution_it_authorises():
    parked = await _parked(with_task=True)
    await gateway.dismiss_proposal(parked["session_id"], parked["proposal_id"])
    dismissed = await repo.list_events(ref_id=parked["proposal_id"], limit=500)
    failed = await repo.list_events(ref_id=parked["task_id"], limit=500)
    first = next(e for e in dismissed if e["kind"] == "proposal.execution_dismissed")
    second = next(e for e in failed if e["kind"] == "task.failed")
    assert first["id"] < second["id"]


async def test_a_dismiss_after_the_retry_claimed_it_is_refused():
    parked = await _parked()
    pid = parked["proposal_id"]
    claimed = await repo.claim_proposal_retry(pid)          # RETRY_PENDING → EXECUTING
    assert claimed is not None

    with pytest.raises(gateway.GatewayError):
        await gateway.dismiss_proposal(parked["session_id"], pid)

    assert (await repo.load_proposal(pid))["status"] == "EXECUTING", "nothing was withdrawn"
    assert "proposal.execution_dismissed" not in [
        e["kind"] for e in await repo.list_events(ref_id=pid, limit=500)]


async def test_the_sql_guard_lets_exactly_one_of_a_dismiss_and_a_claim_win():
    import asyncio

    parked = await _parked()
    pid = parked["proposal_id"]
    withdrawn, claimed = await asyncio.gather(
        repo.withdraw_proposal_if_open(pid), repo.claim_proposal_retry(pid))
    assert (withdrawn is not None) != (claimed is not None), (withdrawn, claimed)


async def test_dismissing_an_interrupted_proposal_works_the_same_way():
    parked = await _parked(state={"interrupted": True}, with_task=True)
    out = await gateway.dismiss_proposal(parked["session_id"], parked["proposal_id"])
    assert out["status"] == "WITHDRAWN"
    dismissed = [e for e in await repo.list_events(ref_id=parked["proposal_id"], limit=500)
                 if e["kind"] == "proposal.execution_dismissed"]
    assert dismissed[0]["payload"]["interrupted"] is True


async def test_the_awaiting_human_dismiss_keeps_its_outcome():
    """The other half of the rule: an AWAITING_HUMAN dismiss still resolves the
    task DONE 'no action needed' and still records a `proposal.decided`."""
    pid = "prop_wait_" + uuid.uuid4().hex[:10]
    sid = "sess_wait_" + uuid.uuid4().hex[:8]
    await repo.create_running_session(sid, "jira-expert")
    await repo.save_proposal(pid, sid, "jira-expert", "do a thing", [], [], "x", pid)
    task_id = "task_" + uuid.uuid4().hex[:12]
    await repo.create_task(task_id, "plain dismiss", "x", "jira-expert")
    await repo.start_task(task_id, sid)

    await gateway.dismiss_proposal(sid, pid, "no")

    task = await repo.get_task(task_id)
    assert task["status"] == "DONE"
    assert task["outcome"] == "operator dismissed the drafted proposal; no action needed"
    kinds = [e["kind"] for e in await repo.list_events(ref_id=pid, limit=500)]
    assert "proposal.decided" in kinds and "proposal.execution_dismissed" not in kinds


async def test_dismissing_a_waiting_proposal_releases_its_folds(monkeypatch):
    """A withdrawn execution covered nothing, so any sibling it claimed to cover
    goes back in the queue (a fold is a claim of coverage, never an outcome)."""
    released: list[tuple[str, str]] = []
    real = gateway.release_folds_for

    async def _spy(proposal_id, why):
        released.append((proposal_id, why))
        return await real(proposal_id, why)

    monkeypatch.setattr(gateway, "release_folds_for", _spy)
    parked = await _parked()
    await gateway.dismiss_proposal(parked["session_id"], parked["proposal_id"])
    assert [p for p, _ in released] == [parked["proposal_id"]]


# --- story 9: GET /api/outages and POST /api/outages/{name}/retry ------------------------------

from central_command.api import orchestration, outages  # noqa: E402
from central_command.contract import DependencyDown  # noqa: E402
from central_command.contract.dependencies import DEPENDENCIES  # noqa: E402
from central_command.heartbeat import probes  # noqa: E402
from central_command.ingest import ledger  # noqa: E402
from central_command.runtime import resume_park  # noqa: E402

DEP_FIELDS = {"name", "state", "checked_at", "down_since", "waiting", "interrupted",
              "failing", "total"}
KIND_FIELDS = {"sessions", "proposals", "tasks", "work_items", "ingest_jobs"}


def _down(dep: str) -> DependencyDown:
    return DependencyDown((dep,), "probe", RuntimeError(f"{dep} is down"))


async def _park_session(dep: str) -> str:
    sid = "sess_out_" + uuid.uuid4().hex[:8]
    await repo.create_running_session(sid, "jira-expert")
    await resume_park.park_resume(
        sid, {"kind": "turn", "after": "task", "attempts": 0, "dependencies": [dep]},
        _down(dep))
    return sid


async def _park_task(dep: str, attempts: int = 1) -> str:
    task_id = "task_out_" + uuid.uuid4().hex[:8]
    await repo.create_task(task_id, "outage wire", "x", "jira-expert")
    assert await orchestration.park_task_retry(task_id, _down(dep), attempts=attempts)
    return task_id


async def _park_item(dep: str) -> str:
    raw = (f"Message-ID: <cc-out-{uuid.uuid4().hex[:8]}@example.com>\n"
           "From: sender@example.com\nSubject: Outage wire\n"
           "Date: Wed, 1 Jul 2026 09:00:00 +0000\n\nBody.\n")
    await ledger.enroll_email(raw)
    item = await repo.claim_next_work_item("sess_out")
    await repo.release_work_item(
        item["id"], error=f"{dep} down", count_attempt=False,
        not_before=datetime.now(timezone.utc) + timedelta(minutes=30),
        dependencies=(dep,))
    return item["id"]


async def _park_job(dep: str) -> int:
    conn = await repo._conn()
    try:
        return await conn.fetchval(
            """insert into graph_ingest_job (group_id, payload, status, not_before,
                                              last_error, dependencies)
               values ('central_command', '{}'::jsonb, 'QUEUED',
                       now() + interval '30 minutes', $1, $2::jsonb)
               returning id""",
            f"{dep} down", f'["{dep}"]')
    finally:
        await conn.close()


async def _park_on(dep: str) -> dict:
    """One of each kind, all waiting on `dep`."""
    proposal = await _parked(state={"dependencies": [dep]})
    return {
        "session": await _park_session(dep),
        "proposal": proposal["proposal_id"],
        "task": await _park_task(dep),
        "item": await _park_item(dep),
        "job": await _park_job(dep),
    }


@pytest.fixture(autouse=True)
async def clean_outage_rows():
    async def settle():
        conn = await repo._conn()
        try:
            await conn.execute("delete from graph_ingest_job where group_id = 'central_command' "
                               "and payload = '{}'::jsonb")
            await conn.execute("delete from work_item where message_id like '<cc-out-%'")
        finally:
            await conn.close()

    await settle()
    yield
    await settle()


def _by_name(payload: dict) -> dict[str, dict]:
    return {d["name"]: d for d in payload["dependencies"]}


async def test_get_outages_counts_each_kind_per_dependency():
    await _park_on("graph")
    await _parked(state={"dependencies": ["jira"]})

    payload = jsonable_encoder(await outages.list_outages())
    deps = _by_name(payload)

    assert [d["name"] for d in payload["dependencies"]] == list(DEPENDENCIES)
    for d in deps.values():
        assert set(d) == DEP_FIELDS, set(d) ^ DEP_FIELDS
        assert set(d["waiting"]) == KIND_FIELDS
    graph = deps["graph"]
    assert graph["waiting"] == {k: 1 for k in KIND_FIELDS}
    assert graph["total"] == 5 and graph["interrupted"] == 0 and graph["failing"] == []
    assert graph["state"] == "up" and graph["checked_at"] is None and graph["down_since"] is None
    assert deps["jira"]["total"] == 1 and deps["jira"]["waiting"]["proposals"] == 1
    for name, d in deps.items():
        if name not in ("graph", "jira"):
            assert d["total"] == 0, name
    assert deps["local"]["state"] == "n/a"


async def test_a_unit_on_two_dependencies_counts_once_under_each_and_none_reads_llm():
    both = await _parked(state={"dependencies": ["graph", "embedder"]})
    none = await _parked(state={"dependencies": []})
    deps = _by_name(jsonable_encoder(await outages.list_outages()))
    assert deps["graph"]["waiting"]["proposals"] == 1
    assert deps["embedder"]["waiting"]["proposals"] == 1
    assert deps["llm"]["waiting"]["proposals"] == 1, "a record with none reads llm"
    assert both and none


async def test_an_interrupted_proposal_shows_under_interrupted_not_waiting():
    await _parked(state={"dependencies": ["graph"], "interrupted": True})
    graph = _by_name(jsonable_encoder(await outages.list_outages()))["graph"]
    assert graph["interrupted"] == 1
    assert graph["waiting"]["proposals"] == 0
    assert graph["total"] == 1


async def test_a_down_dependency_reports_its_state_and_down_since():
    await repo.record_dependency_probe("jira", False, "refused")
    jira = _by_name(jsonable_encoder(await outages.list_outages()))["jira"]
    assert jira["state"] == "down" and jira["down_since"] and jira["checked_at"]


async def test_a_unit_past_the_loud_threshold_shows_under_failing_with_its_error():
    task_id = await _park_task("jira", attempts=settings.retry_loud_after_attempts)
    jira = _by_name(jsonable_encoder(await outages.list_outages()))["jira"]
    mine = [f for f in jira["failing"] if f["id"] == task_id]
    assert len(mine) == 1
    assert set(mine[0]) == {"kind", "id", "attempts", "last_error"}
    assert mine[0]["kind"] == "task" and "jira is down" in mine[0]["last_error"]
    assert mine[0]["attempts"] == settings.retry_loud_after_attempts


def _stub_drivers(monkeypatch) -> list[tuple[str, str]]:
    """Recording stand-ins for the three drivers that SETTLE their unit and say
    so on the log (`test.retried`), so the ordering against the retry request
    is observable and the counts can drop."""
    driven: list[tuple[str, str]] = []

    async def _settle(sql: str, *args):
        conn = await repo._conn()
        try:
            await conn.execute(sql, *args)
        finally:
            await conn.close()

    async def _session(sid, model=None):
        driven.append(("session", sid))
        await events.emit("test.retried", ref_id=sid, payload={}, actor="system")
        await _settle("update session set status = 'DONE' where id = $1", sid)
        return {"resumed": True}

    async def _task(tid, model=None):
        driven.append(("task", tid))
        await events.emit("test.retried", ref_id=tid, payload={}, actor="system")
        await _settle("update task set status = 'DONE', retry_state = null where id = $1", tid)
        return {"retried": True}

    async def _proposal(pid, model=None):
        driven.append(("proposal", pid))
        await events.emit("test.retried", ref_id=pid, payload={}, actor="system")
        await _settle("update proposal set status = 'EXECUTED' where id = $1", pid)
        return {"retried": True}

    monkeypatch.setattr(orchestration, "resume_parked_turn", _session)
    monkeypatch.setattr(orchestration, "retry_parked_task", _task)
    monkeypatch.setattr(gateway, "retry_execution", _proposal)
    return driven


async def test_retry_with_the_probe_down_drives_nothing(monkeypatch):
    parked = await _park_on("graph")
    driven = _stub_drivers(monkeypatch)

    async def _graph_down():
        return False

    monkeypatch.setitem(probes.PROBES, "graph", _graph_down)
    out = await outages.retry_outage("graph")

    assert out == {"probe": "down", "driven": {k: 0 for k in KIND_FIELDS}}
    assert driven == []
    kinds = [e["kind"] for e in await repo.list_events(ref_id="graph", limit=500)]
    assert "outage.retry_requested" in kinds and "dependency.down" in kinds
    assert _by_name(jsonable_encoder(await outages.list_outages()))["graph"]["total"] == 5
    assert parked


async def test_retry_with_the_probe_up_drives_the_five_and_the_counts_drop(monkeypatch):
    parked = await _park_on("graph")
    other = await _parked(state={"dependencies": ["jira"]})
    driven = _stub_drivers(monkeypatch)
    # Parks are not due yet (the curve): the operator's retry ignores that.
    assert _by_name(jsonable_encoder(await outages.list_outages()))["graph"]["total"] == 5

    out = await outages.retry_outage("graph")
    await orchestration.sweep_settle()

    assert out["probe"] == "up"
    assert out["driven"] == {k: 1 for k in KIND_FIELDS}
    assert {d for d in driven} == {("session", parked["session"]),
                                   ("task", parked["task"]),
                                   ("proposal", parked["proposal"])}
    assert ("proposal", other["proposal_id"]) not in driven, "only the graph's units"

    requested = [e for e in await repo.list_events(ref_id="graph", limit=500)
                 if e["kind"] == "outage.retry_requested"][-1]
    assert requested["payload"] == {"dependency": "graph", "include_interrupted": False,
                                    "actor": "operator"}
    assert requested["actor"] == "operator"
    for ref in (parked["session"], parked["task"], parked["proposal"]):
        retried = [e for e in await repo.list_events(ref_id=ref, limit=500)
                   if e["kind"] == "test.retried"]
        assert retried and requested["id"] < retried[0]["id"], "emit-before"

    deps = _by_name(jsonable_encoder(await outages.list_outages()))
    assert deps["graph"]["total"] == 0, deps["graph"]
    assert deps["jira"]["total"] == 1


async def test_an_interrupted_proposal_is_driven_only_when_asked_and_resumes_at_its_cursor(
    monkeypatch,
):
    from tests.test_execution_retry import _graph_spy, _parked_run

    monkeypatch.setattr(settings, "executor_mode", "live")
    run = await _parked_run(monkeypatch)             # jira landed, the graph write parked
    pid = run["proposal_id"]
    conn = await repo._conn()
    try:
        await conn.execute(
            "update proposal set retry_state = jsonb_set(jsonb_set(retry_state, "
            "'{interrupted}', 'true'), '{dependencies}', '[\"graph\"]') where id = $1", pid)
    finally:
        await conn.close()
    graph_calls = _graph_spy(monkeypatch)            # the graph is back

    out = await outages.retry_outage("graph")
    await orchestration.sweep_settle()
    assert out["driven"]["proposals"] == 0, "an interrupted write waits for the operator"
    assert (await repo.load_proposal(pid))["status"] == "RETRY_PENDING"
    assert graph_calls == []

    out = await outages.retry_outage("graph", include_interrupted=True)
    await orchestration.sweep_settle()

    assert out["driven"]["proposals"] == 1
    assert (await repo.load_proposal(pid))["status"] == "EXECUTED"
    assert len(graph_calls) == 1
    assert run["jira_calls"] == [("DEMO-1", "2026-08-03")], "the kept cursor: jira ran once"


async def test_unknown_and_local_names_are_refused():
    with pytest.raises(HTTPException) as unknown:
        await outages.retry_outage("nonesuch")
    assert unknown.value.status_code == 404
    with pytest.raises(HTTPException) as local:
        await outages.retry_outage("local")
    assert local.value.status_code == 400


async def test_the_http_routes_and_the_rpc_return_the_same_shapes(monkeypatch):
    await _park_on("graph")
    driven = _stub_drivers(monkeypatch)
    rest_payload = jsonable_encoder(await outages.get_outages())
    rpc_payload = jsonable_encoder(await nerve_gateway._dispatch("outages.list", {}, None))
    assert rest_payload == rpc_payload

    rpc_out = await nerve_gateway._dispatch(
        "outages.retry", {"dependency": "graph", "includeInterrupted": False}, None)
    await orchestration.sweep_settle()
    assert rpc_out["probe"] == "up"
    assert set(rpc_out["driven"]) == KIND_FIELDS and rpc_out["driven"]["sessions"] == 1
    assert driven

    rest_out = await outages.post_outage_retry("graph", outages.OutageRetryIn())
    assert set(rest_out) == {"probe", "driven"} and set(rest_out["driven"]) == KIND_FIELDS

    from central_command.api.app import app

    paths = app.openapi()["paths"]
    assert "get" in paths["/api/outages"]
    assert "post" in paths["/api/outages/{name}/retry"]
