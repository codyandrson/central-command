"""The outage doctrine, end to end (epic-outage-doctrine story 13).

A dependency goes down, every surface parks and backs off, the banner's counts
match, retry-now drives them, a kill between park and resume loses nothing, and
the embedder holds the graph. The deterministic test model drives the task runs
and chat turns; the operator's side goes through the real REST handlers
(`api/outages.py`), the `/ws` RPC (`nerve_gateway._dispatch`) and the real
gateway; the clock is the `not_before` timestamp (`make_parks_due`), never a
sleep. Nothing here calls a model. Fixtures and builders are the ones the
epic's earlier files define.

The scenario, in order (the numbers are the story's):

1. the graph goes down while a task run, an approved curation proposal, a ledger
   item and an ingest job all need it — each parks, nothing is FAILED, no agent
   is told to "proceed without it";
2. `GET /api/outages` counts them and the log holds exactly one `dependency.down`;
3. sweeps with the probe down spend no attempts; with the probe up the backoff
   gaps double;
4. retry-now with the probe still down drives nothing and says so;
5. the dependency returns: one `dependency.up`, `outage.retry_requested` BEFORE any
   `*.retried`, and everything converges to the result a run with no outage gives;
6. a process kill between a park and its resume (the startup landing) loses
   nothing and the interrupted write resumes from its cursor;
7. a chat turn parks on the model and lands once;
8. a unit failing against a healthy probe is listed and reported once;
9. a NO fails at once on every surface;
10. dismissing a waiting proposal withdraws it.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.encoders import jsonable_encoder
from neo4j.exceptions import ServiceUnavailable
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelRequest, ToolReturnPart

from central_command.api import nerve_gateway, orchestration, outages, routes
from central_command.config import settings
from central_command.contract import DependencyDown, retry_not_before
from central_command.db import repo
from central_command.gateway import gateway
from central_command.heartbeat import probes
from central_command.ingest import dispatcher, ledger
from central_command.integrations import graphiti, graphiti_ingest, neo4j_writer
from central_command.runtime import durable, models, tools
from central_command.runtime.spike_model import make_deferred_tool_model
from tests.conftest import aresolve, event_kinds, make_parks_due, needs_pg
from tests.test_execution_retry import OUTAGE_TEXT, _graph_spy, _jira_spy, _parked_run
from tests.test_outage_doctrine import (  # noqa: F401 — builders and the graph double
    DEGRADED, FACT, _Graph, _graph_return, _model, _parked_task_run, _task, graph,
)
from tests.test_outage_requeue import _boom_handler
from tests.test_outage_wire import _parked
from tests.test_restart_landing import (  # noqa: F401 — the startup landing's builders
    _claimed_resume, _executing_with_cursor, _lifespan_sweeps, _running_task_run,
    settle_leftovers,
)
from tests.test_turn_parking import (  # noqa: F401
    DOWN, _lane, _messages, _route_models, _Script, _send, _text, _user_prompts,
)

pytestmark = needs_pg

OUTAGE = DependencyDown(("graph",), "e2e", RuntimeError("graph is down"))


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    monkeypatch.setattr(settings, "test_model", True)
    monkeypatch.setattr(settings, "dispatch_approval_limit", 10_000)
    monkeypatch.setattr(settings, "auditor_enabled", False)
    monkeypatch.setattr(settings, "retry_backoff_cap_seconds", 3600)

    async def _no_wait(delay):
        return None

    monkeypatch.setattr(tools, "_sleep", _no_wait)


@pytest.fixture(autouse=True)
async def clean_e2e_rows():
    async def settle():
        conn = await repo._conn()
        try:
            await conn.execute("delete from graph_ingest_job where payload ->> 'e2e' = '1'")
            await conn.execute("delete from work_item where message_id like '<cc-e2e-%'")
        finally:
            await conn.close()

    await settle()
    yield
    await settle()


class _Health:
    """The graph's health as the probe sees it, switched by the test."""

    def __init__(self, monkeypatch, up: bool = False):
        self.up = up

        async def _probe() -> bool:
            return self.up

        monkeypatch.setitem(probes.PROBES, "graph", _probe)


def _item_raw() -> str:
    return (f"Message-ID: <cc-e2e-{uuid.uuid4().hex[:8]}@example.com>\n"
            "From: sender@example.com\nSubject: End to end\n"
            "Date: Wed, 1 Jul 2026 09:00:00 +0000\n\nBody.\n")


async def _queue_job(dependency: str = "graph") -> int:
    conn = await repo._conn()
    try:
        return await conn.fetchval(
            """insert into graph_ingest_job (group_id, payload, status, not_before,
                                              last_error, dependencies)
               values ('central_command', '{"e2e": "1"}'::jsonb, 'QUEUED',
                       now() + interval '30 minutes', $1, $2::jsonb)
               returning id""",
            f"{dependency} down", json.dumps([dependency]))
    finally:
        await conn.close()


def _worker() -> graphiti_ingest.IngestWorker:
    w = graphiti_ingest.IngestWorker()
    w._recovered = w._indices_ok = w._cutover_done = True
    w._patches_gate = lambda: True
    return w


def _by_name(payload: dict) -> dict[str, dict]:
    return {d["name"]: d for d in payload["dependencies"]}


async def _rpc(method: str, params: dict | None = None) -> dict:
    return await nerve_gateway._dispatch(method, params or {}, None)


async def _payloads(ref_id: str, kind: str) -> list[dict]:
    return [e["payload"] for e in await repo.list_events(ref_id=ref_id, limit=1000)
            if e["kind"] == kind]


def _gap(record: dict) -> int:
    return round((datetime.fromisoformat(record["not_before"])
                  - datetime.fromisoformat(record["parked_at"])).total_seconds())


async def _graph_proposal(spy: list[str], landed: list[str], monkeypatch,
                          health: _Health) -> tuple[str, str, object]:
    """An approved-to-be `graph.create_node` proposal drafted by a REAL task run
    (so its drafter is paused on it and resumes after the write), with the bolt
    writer standing in for the graph: it raises the real driver's outage while
    the graph is down."""
    from tests.test_gate_overrides import _run

    async def create_node(name, labels, summary, group_id):
        spy.append(name)
        if not health.up:
            raise ServiceUnavailable("Couldn't connect to graph:7687")
        landed.append(name)
        return "node-1"

    monkeypatch.setattr(neo4j_writer, "create_node", create_node)
    proposal = {
        "intent": "Record Jane Doe in the graph",
        "actions": [{"capability": "graph.create_node",
                     "arguments": {"name": "Jane Doe", "group_id": "central_command",
                                   "summary": "Works at Example Co"},
                     "target_ref": {"system": "graphiti"}, "reversibility": "reversible"}],
        "evidence": [{"kind": "operator", "source_ref": "operator:task",
                      "locator": "test task", "claim": "Jane Doe works at Example Co"}],
        "expected_effect": "the graph knows Jane Doe",
    }
    run = await _run(monkeypatch, "jira-expert", proposal)
    model = make_deferred_tool_model("propose_action", {"proposal": proposal})
    return run["proposal_id"], run["session_id"], model


# --- 1-5: the graph goes down and comes back ---------------------------------------


async def test_a_graph_outage_parks_everything_and_retry_now_converges_it(
    monkeypatch, graph,
):
    start = await repo.latest_event_id()
    health = _Health(monkeypatch, up=False)
    await orchestration.probe_and_record(["graph"])        # the sweep's first sight

    # -- 1. every surface parks -----------------------------------------------------
    task, out = await _parked_task_run(monkeypatch)        # a read parks mid-turn
    read_sid = out["session_id"]
    assert out["parked"] is True
    assert await repo.session_status(read_sid) == "AWAITING_RESUME"

    attempts: list[str] = []
    landed: list[str] = []
    pid, prop_sid, prop_model = await _graph_proposal(attempts, landed, monkeypatch, health)
    approved = await gateway.approve_and_execute(prop_sid, pid, model=prop_model)
    assert approved["execution"] == "parked"
    prop = await repo.load_proposal(pid)
    assert prop["status"] == "RETRY_PENDING"
    assert prop["retry_state"]["dependencies"] == ["graph", "embedder"]
    assert landed == [], "nothing was written while the graph was down"

    orig_handler = dispatcher.HANDLERS["email"]
    _boom_handler(monkeypatch, OUTAGE)
    await ledger.enroll_email(_item_raw())
    item = await repo.claim_next_work_item("sess_e2e")
    released = await dispatcher.process_claimed(item)
    assert released["transient"] is True
    conn = await repo._conn()
    try:
        row = await conn.fetchrow(
            "select state, attempts, dependencies from work_item where id = $1", item["id"])
    finally:
        await conn.close()
    assert row["state"] == "UNPROCESSED" and json.loads(row["dependencies"]) == ["graph"]

    job_id = await _queue_job()
    claimed_jobs: list[int] = []

    async def run_job(job):
        claimed_jobs.append(job["id"])

    monkeypatch.setattr(graphiti_ingest, "run_job", run_job)
    worker = _worker()
    assert await worker.tick() == [], "the ingest worker takes nothing while the graph is down"
    assert claimed_jobs == []

    # No row is FAILED anywhere, and no agent was told to carry on without the read.
    assert await repo.session_status(read_sid) == "AWAITING_RESUME"
    assert (await repo.get_task(task["id"]))["status"] == "IN_PROGRESS"
    assert prop["status"] != "FAILED"
    rs = await repo.get_session_run_state(read_sid)
    assert DEGRADED not in json.dumps(rs["messages"])
    sibling = [p for m in durable.load_messages(rs) if isinstance(m, ModelRequest)
               for p in m.parts if isinstance(p, ToolReturnPart)
               and p.tool_call_id == "call_sibling"]
    assert len(sibling) == 1, "the sibling call's result is kept in the record"
    assert not await _payloads(task["id"], "task.run_failed")

    # -- 2. the banner's counts, over REST and over the RPC, and one down edge -------
    listed = jsonable_encoder(await outages.get_outages())
    assert listed == jsonable_encoder(await _rpc("outages.list"))
    g = _by_name(listed)["graph"]
    assert g["state"] == "down" and g["down_since"]
    assert g["waiting"] == {"sessions": 1, "proposals": 1, "tasks": 0,
                            "work_items": 1, "ingest_jobs": 1}
    assert g["total"] == 4 and g["interrupted"] == 0 and g["failing"] == []
    emb = _by_name(listed)["embedder"]
    assert emb["state"] == "up" and emb["waiting"]["proposals"] == 1, \
        "the curation waits on the embedder too (it embeds before it writes)"
    downs = [e for e in await repo.list_events(kind="dependency.down", since_id=start, limit=500)
             if e["ref_id"] == "graph"]
    assert len(downs) == 1

    # -- 3a. sweeps with the probe down spend nothing ---------------------------------
    calls, prop_attempts = graph.calls, prop["retry_state"]["attempts"]
    for _ in range(3):
        await make_parks_due()
        result = await orchestration.retry_sweep()
        await orchestration.sweep_settle()
        gated = {s["id"] for s in result["skipped_gated"]}
        assert {read_sid, pid} <= gated
        assert read_sid not in result["resumed"]
    assert graph.calls == calls, "a gated skip never touches the dependency"
    assert len(attempts) == 1, "nor does it replay the write"
    assert (await repo.get_session_run_state(read_sid))["pending_resume"]["attempts"] == 0
    assert (await repo.load_proposal(pid))["retry_state"]["attempts"] == prop_attempts
    assert await worker.tick() == [] and claimed_jobs == []
    assert len([e for e in await repo.list_events(kind="dependency.down", since_id=start,
                                                  limit=500) if e["ref_id"] == "graph"]) == 1

    # -- 4. retry-now with the probe still down ---------------------------------------
    pre = jsonable_encoder(await outages.list_outages())
    rejected = await outages.post_outage_retry("graph")
    assert rejected == {"probe": "down", "driven": {
        "sessions": 0, "proposals": 0, "tasks": 0, "work_items": 0, "ingest_jobs": 0}}
    assert jsonable_encoder(await outages.list_outages()) == pre
    assert len(await _payloads("graph", "outage.retry_requested")) >= 1

    # -- 5. the dependency comes back --------------------------------------------------
    graph.down = False
    health.up = True
    monkeypatch.setitem(dispatcher.HANDLERS, "email", orig_handler)
    monkeypatch.setattr(models, "resolve_model", aresolve(lambda *a, **k: _model()))
    mark = await repo.latest_event_id()

    driven = await outages.post_outage_retry("graph")
    await orchestration.sweep_settle()
    assert driven["probe"] == "up"
    assert driven["driven"] == {"sessions": 1, "proposals": 1, "tasks": 0,
                                "work_items": 1, "ingest_jobs": 1}

    new = await repo.list_events(since_id=mark, limit=2000)
    kinds = [e["kind"] for e in new]
    assert kinds.count("dependency.up") == 1
    assert len([e for e in await repo.list_events(kind="dependency.up", since_id=start,
                                                  limit=500) if e["ref_id"] == "graph"]) == 1
    requested = next(e["id"] for e in new if e["kind"] == "outage.retry_requested")
    retried = [e["id"] for e in new if e["kind"].endswith("_retried")]
    assert retried and all(requested < r for r in retried), \
        "the request is on the log before anything it authorises"

    # The session resumed in place with the read's real result, sibling intact.
    assert await repo.session_status(read_sid) == "DONE"
    rs = await repo.get_session_run_state(read_sid)
    returns = [p for m in durable.load_messages(rs) if isinstance(m, ModelRequest)
               for p in m.parts if isinstance(p, ToolReturnPart)]
    by_call = {p.tool_call_id: p for p in returns}
    assert FACT in str(by_call["call_graph"].content)
    assert "call_sibling" in by_call
    assert DEGRADED not in json.dumps(rs["messages"])
    done = await repo.get_task(task["id"])
    assert done["status"] == "DONE"

    # …the same result as a run that never saw the outage.
    clean_task = await _task("Renewal check, no outage")
    monkeypatch.setattr(routes, "resolve_model", aresolve(lambda *a, **k: _model()))
    await routes._run_assigned_task(clean_task)
    assert done["outcome"] == (await repo.get_task(clean_task["id"]))["outcome"]

    # The proposal ran from its first incomplete action, once, under one decision.
    final = await repo.load_proposal(pid)
    assert final["status"] == "EXECUTED"
    assert landed == ["Jane Doe"] and len(attempts) == 2
    pkinds = await event_kinds(pid)
    assert pkinds.count("proposal.decided") == 1
    assert "proposal.execution_retried" in pkinds and pkinds.count("proposal.executed") == 1

    # The ledger item and the job were brought due and are taken by their loops.
    claim = await repo.claim_next_work_item("sess_e2e_2")
    assert claim is not None and claim["id"] == item["id"]
    out_item = await dispatcher.process_claimed(claim)
    assert out_item.get("transient") is not True
    conn = await repo._conn()
    try:
        state = await conn.fetchval("select state from work_item where id = $1", item["id"])
    finally:
        await conn.close()
    assert state not in ("UNPROCESSED", "FAILED", "CLAIMED"), state
    # Foreign queued jobs left by earlier tests may be taken too: assert on ours.
    assert job_id in await worker.tick()
    await worker.settle()
    assert job_id in claimed_jobs and claimed_jobs.count(job_id) == 1

    # The banner goes quiet.
    after = _by_name(jsonable_encoder(await outages.list_outages()))["graph"]
    assert after["state"] == "up" and after["total"] == 0 and after["down_since"] is None


# --- 3b: the backoff doubles -------------------------------------------------------------


async def test_with_the_probe_up_a_failing_unit_waits_twice_as_long_each_time(
    monkeypatch, graph,
):
    """The probe says up but the call still fails (the doctrine's `failing despite
    the probe` case, which spends attempts): the gap before the next try doubles.
    Not-yet-due units are not offered at all."""
    _Health(monkeypatch, up=True)
    _, out = await _parked_task_run(monkeypatch)
    sid = out["session_id"]
    calls = graph.calls

    result = await orchestration.retry_sweep()                # not due: nothing offered
    await orchestration.sweep_settle()
    assert sid not in result["resumed"] and graph.calls == calls

    gaps = []
    for _ in range(4):
        pending = (await repo.get_session_run_state(sid))["pending_resume"]
        gaps.append(_gap(pending))
        await make_parks_due()
        result = await orchestration.retry_sweep()
        await orchestration.sweep_settle()
        assert sid in result["resumed"]
    pending = (await repo.get_session_run_state(sid))["pending_resume"]
    gaps.append(_gap(pending))
    assert gaps == [60, 120, 240, 480, 960]
    assert pending["attempts"] == 4
    assert await repo.session_status(sid) == "AWAITING_RESUME"

    # The proposal surface doubles on the same curve.
    parked = await _parked_run(monkeypatch)
    pgaps = [_gap((await repo.load_proposal(parked["proposal_id"]))["retry_state"])]
    for _ in range(3):
        _graph_spy(monkeypatch, fail=OUTAGE_TEXT)
        await gateway.retry_execution(parked["proposal_id"], model=parked["model"])
        pgaps.append(_gap((await repo.load_proposal(parked["proposal_id"]))["retry_state"]))
    assert pgaps == [60, 120, 240, 480]


def test_retry_not_before_conventions_and_the_first_failure_waits_a_minute():
    """The five prior-failures conventions in `retry_not_before`'s docstring: each
    surface hands in the failures BEFORE the one it records, so every first
    failure waits 60 s and each later one doubles."""
    now = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)
    surfaces = {
        "session resume (pending_resume.attempts)": lambda recorded: recorded,
        "proposal (attempts - 1)": lambda attempts: attempts - 1,
        "task (attempts - 1)": lambda attempts: attempts - 1,
        "ledger item (transient_releases)": lambda releases: releases,
        "ingest job (attempts - 1, seconds to SQL)": lambda attempts: attempts - 1,
    }
    first = {"session resume (pending_resume.attempts)": 0, "proposal (attempts - 1)": 1,
             "task (attempts - 1)": 1, "ledger item (transient_releases)": 0,
             "ingest job (attempts - 1, seconds to SQL)": 1}
    for name, translate in surfaces.items():
        for failure, want in enumerate([60, 120, 240, 480]):
            counter = first[name] + failure
            seconds, not_before = retry_not_before(translate(counter), now=now)
            assert seconds == want, (name, failure)
            assert not_before == now + timedelta(seconds=want), (name, failure)
    capped, when = retry_not_before(40, now=now, cap=300)
    assert capped == 300 and when == now + timedelta(seconds=300)


async def test_the_first_failure_waits_sixty_seconds_on_every_surface(monkeypatch, graph):
    _Health(monkeypatch, up=True)
    _, out = await _parked_task_run(monkeypatch)
    session_gap = _gap((await repo.get_session_run_state(out["session_id"]))["pending_resume"])

    parked = await _parked_run(monkeypatch)
    proposal_gap = _gap((await repo.load_proposal(parked["proposal_id"]))["retry_state"])

    task_id = "task_" + uuid.uuid4().hex[:12]
    await repo.create_task(task_id, "first failure", "x", "jira-expert")
    assert await orchestration.park_task_retry(task_id, ConnectionError("refused"), attempts=1)
    task_gap = _gap((await repo.get_task(task_id))["retry_state"])

    _boom_handler(monkeypatch, OUTAGE)
    await ledger.enroll_email(_item_raw())
    item = await repo.claim_next_work_item("sess_e2e_gap")
    await dispatcher.process_claimed(item)
    released = (await _payloads(item["id"], "work.released"))[-1]

    assert [session_gap, proposal_gap, task_gap, released["backoff_seconds"]] == [60] * 4


# --- 6: a kill between a park and its resume --------------------------------------------


async def test_a_kill_between_park_and_resume_loses_nothing(monkeypatch):
    task_run, task_session = await _running_task_run()
    resumed = await _claimed_resume(monkeypatch)
    with_cursor = await _executing_with_cursor(monkeypatch)
    jira_calls = _jira_spy(monkeypatch)                       # fresh: counts replays only
    graph_calls = _graph_spy(monkeypatch)

    await _lifespan_sweeps(monkeypatch)                       # the simulated restart
    # (land_orphans_at_startup, startup_task_sweep, reclaim_stale_work_items(0))

    # An EXECUTING proposal with its cursor at 1 comes back interrupted at 1.
    row = await repo.load_proposal(with_cursor)
    assert row["status"] == "RETRY_PENDING"
    assert row["retry_state"]["interrupted"] is True
    assert row["retry_state"]["next_action_index"] == 1
    # A claimed resume comes back AWAITING_RESUME with its record.
    assert await repo.session_status(resumed) == "AWAITING_RESUME"
    # A RUNNING task run comes back as a retry-parked task, transcript STOPPED, linked.
    task = await repo.get_task(task_run)
    assert task["status"] == "ASSIGNED" and task["retry_state"]["kind"] == "restart"
    assert await repo.session_status(task_session) == "STOPPED"
    assert task_run in (await repo.get_session(task_session))["closed_reason"]
    stopped = [e for e in await repo.list_events(ref_id=task_session)
               if e["kind"] == "session.stopped"][-1]
    assert stopped["payload"]["task_id"] == task_run
    # Nothing FAILED.
    for kinds in (await event_kinds(task_run), await event_kinds(with_cursor),
                  await event_kinds(resumed), await event_kinds(task_session)):
        assert not [k for k in kinds if k in ("task.run_failed", "proposal.failed",
                                              "session.failed")], kinds
    # The background catch-up re-drives the owed resume, and never the interrupted write.
    swept = await orchestration.resume_sweep()
    await orchestration.sweep_settle()
    assert with_cursor not in swept["retried"]
    assert await repo.session_status(resumed) != "FAILED"
    assert "session.failed" not in await event_kinds(resumed)
    assert (await repo.load_proposal(with_cursor))["status"] == "RETRY_PENDING"
    assert graph_calls == [] and jira_calls == []

    # The banner counts it under `interrupted`, not `waiting`.
    dep = _by_name(jsonable_encoder(await outages.list_outages()))
    assert sum(d["interrupted"] for d in dep.values()) >= 1

    # Plain retry-now leaves the interrupted proposal alone…
    await outages.retry_outage("graph")
    await orchestration.sweep_settle()
    assert (await repo.load_proposal(with_cursor))["status"] == "RETRY_PENDING"
    assert graph_calls == []
    # …and with include_interrupted it resumes at action 1, never replaying 0.
    out = await outages.retry_outage("graph", include_interrupted=True)
    await orchestration.sweep_settle()
    assert out["probe"] == "up" and out["driven"]["proposals"] == 1
    final = await repo.load_proposal(with_cursor)
    assert final["status"] == "EXECUTED"
    assert jira_calls == [], "action 0 had landed before the kill and is not repeated"
    assert len(graph_calls) == 1
    assert (await event_kinds(with_cursor)).count("proposal.decided") == 1


# --- 7: a chat turn parks on the model --------------------------------------------------


async def test_a_chat_turn_parks_on_the_model_and_lands_once(monkeypatch):
    script = _Script(DOWN, _text("DEMO-1 is due Aug 3."))
    _route_models(monkeypatch, script)
    sid = await _lane()
    ask = "When is DEMO-1 due?"
    await _send(sid, ask)

    assert await repo.session_status(sid) == "AWAITING_RESUME"
    pending = (await repo.get_session_run_state(sid))["pending_resume"]
    assert pending["kind"] == "turn" and pending["dependencies"] == ["llm"]
    llm = _by_name(jsonable_encoder(await outages.list_outages()))["llm"]
    assert llm["waiting"]["sessions"] == 1

    await make_parks_due()
    out = await orchestration.retry_sweep()
    await orchestration.sweep_settle()
    assert sid in out["resumed"]
    assert await repo.session_status(sid) == "AWAITING_OPERATOR"
    messages = await _messages(sid)
    assert _user_prompts(messages, ask) == 1, "the operator's turn appears once"
    assert messages[-1]["kind"] == "response"
    assert "DEMO-1 is due Aug 3." in str(messages[-1]["parts"])
    assert "session.failed" not in await event_kinds(sid)
    assert _by_name(jsonable_encoder(await outages.list_outages()))["llm"]["total"] == 0


# --- 8: failing despite a healthy probe ---------------------------------------------------


async def test_a_unit_failing_three_times_against_a_healthy_probe_is_listed_and_reported(
    monkeypatch,
):
    task_id = "task_" + uuid.uuid4().hex[:12]
    await repo.create_task(task_id, "loud", "x", "jira-expert")
    deny = ModelHTTPError(403, "cc-default", body="denied by the edge firewall")
    for attempt in (1, 2, 3):
        assert await orchestration.park_task_retry(task_id, deny, attempts=attempt)

    llm = _by_name(jsonable_encoder(await outages.list_outages()))["llm"]
    assert [f["id"] for f in llm["failing"]] == [task_id]
    assert llm["failing"][0]["kind"] == "task" and llm["failing"][0]["attempts"] == 3
    assert llm["waiting"]["tasks"] == 1

    await orchestration.retry_sweep()
    await orchestration.retry_sweep()
    reported = await _payloads(task_id, "retry.failing_despite_probe")
    assert len(reported) == 1
    assert (await repo.get_task(task_id))["status"] == "ASSIGNED", "reporting is not failing"


# --- 9: a NO is terminal at once ------------------------------------------------------------


async def test_a_no_from_a_read_returns_honest_text_and_the_task_lands(monkeypatch, graph):
    async def _no(query, **kw):
        raise ValueError("center_node_uuid is not a node")

    monkeypatch.setattr(graphiti, "search_facts", _no)
    _, out = await _parked_task_run(monkeypatch)
    assert out.get("parked") is not True
    ret = _graph_return(durable.load_messages(
        await repo.get_session_run_state(out["session_id"])))
    assert ret is not None and "knowledge graph search failed (ValueError)" in str(ret.content)
    assert await repo.session_status(out["session_id"]) != "AWAITING_RESUME"


async def test_a_no_on_every_other_surface_is_terminal_at_once(monkeypatch):
    # An execution: the proposal FAILS on the first attempt.
    parked = await _parked_run(monkeypatch, fail="400 invalid episode body")
    row = await repo.load_proposal(parked["proposal_id"])
    assert row["status"] == "FAILED" and row["retry_state"] is None
    assert "proposal.execution_parked" not in await event_kinds(parked["proposal_id"])

    # A ledger item: semantic, the attempt is spent and there is no backoff.
    _boom_handler(monkeypatch, ValueError("404 mailbox does not exist"))
    await ledger.enroll_email(_item_raw())
    item = await repo.claim_next_work_item("sess_e2e_no")
    out = await dispatcher.process_claimed(item)
    assert out["transient"] is False
    conn = await repo._conn()
    try:
        r = await conn.fetchrow(
            "select attempts, transient_releases, not_before from work_item where id = $1",
            item["id"])
    finally:
        await conn.close()
    assert (r["attempts"], r["transient_releases"], r["not_before"]) == (1, 0, None)
    assert (await _payloads(item["id"], "work.released"))[-1]["transient"] is False

    # A model turn: the session lands FAILED with its reason, parked nowhere.
    _route_models(monkeypatch, _Script(ModelHTTPError(400, "cc-default", body="bad request")))
    sid = await _lane()
    await _send(sid, "hello?")
    assert await repo.session_status(sid) == "FAILED"
    failed = [e for e in await repo.list_events(ref_id=sid) if e["kind"] == "session.failed"]
    assert failed and failed[-1]["payload"]["reason"]
    assert sid not in await repo.sessions_awaiting_resume()


# --- 10: dismissing a waiting proposal ---------------------------------------------------------


async def test_dismissing_a_waiting_proposal_withdraws_it_and_fails_the_task(monkeypatch):
    parked = await _parked(with_task=True)
    pid, sid, task_id = parked["proposal_id"], parked["session_id"], parked["task_id"]
    waiting = await _rpc("decisions.list")
    assert pid in {p["id"] for p in waiting["waiting"]}
    assert pid not in {p["id"] for p in waiting["proposals"]}

    await gateway.dismiss_proposal(sid, pid, "not needed any more")

    assert (await repo.load_proposal(pid))["status"] == "WITHDRAWN"
    kinds = await event_kinds(pid)
    assert kinds.count("proposal.execution_dismissed") == 1
    assert kinds.count("proposal.decided") == 1
    task = await repo.get_task(task_id)
    assert task["status"] == "FAILED"
    assert task["outcome"] == (
        "operator withdrew the waiting execution; 1 of 2 actions had landed")
    assert pid not in {p["id"] for p in (await _rpc("decisions.list"))["waiting"]}
