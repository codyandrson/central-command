"""The world-moved redraft end to end (epic-world-changed-redraft, story 9).

On the test model and fake integrations, through the PUBLIC seams only: the
cockpit's approve and dismiss RPCs (`proposals.approve` → the approve route,
detached closing turn and all), the outage retry-now (`outages.retry` →
probe, then the restricted retry sweep), the lifespan's startup sweeps, and
the Decisions RPC (`decisions.history`). No test calls a gateway internal.

(a) a page that moves before every execution: three redrafts, each parked for
    the operator and linked, the fourth failure terminal and loud, the chain
    and its mark on the Decisions history;
(b) an approved update parked by a DOWN Confluence, the page moved in the
    outage: the retry-now's re-check takes the redraft path, no PUT;
(c) an interrupted execution retried by the operator re-checks first and the
    report says it was interrupted by a restart;
(d) an override-decided proposal's redraft parks for the operator;
(e) a Jira 400 keeps today's resume and never counts toward the limit;
(f) Dismiss of a redraft never resumes the drafter;
(g) a death between the FAILED status and the drafter's resume: the startup
    sweeps resume it exactly once from the armed record, linked and counted;
and a `graph.delete_episode` whose preview changed lands world-moved through
the approve route (owed from story 4); and (epic-small-fixes story 11.7, R24,
the operator's Decision 6) a coached charter edit the operator overtook through
the charter route lands world-moved on Approve, the coach redrafts against the
charter the report showed it, and the chain stops at the limit.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi.encoders import jsonable_encoder

from central_command import events
from central_command.api import nerve_gateway, orchestration, routes
from central_command.config import settings
from central_command.db import repo
from central_command.gateway import executor, gateway
from central_command.heartbeat import probes
from central_command.runtime import agent as agent_mod
from tests.conftest import needs_pg
from tests.world_moved_helpers import (  # noqa: F401 — pinned, confluence_grant and graph_world are fixtures
    AGENT,
    _Coach,
    _coached_target,
    _count,
    _counting_confluence,
    _delete_action,
    _draft,
    _Drafter,
    _drafting_model,
    _event,
    _page_update,
    _Persistent,
    _preview,
    _redrafts_of,
    _task_session,
    confluence_grant,
    graph_world,
    pinned,
)

pytestmark = needs_pg


@pytest.fixture(autouse=True)
def limit_three(monkeypatch):
    monkeypatch.setattr(settings, "world_moved_redraft_limit", 3)


@pytest.fixture(autouse=True)
async def settle_leftovers():
    """The retry-now and the startup sweeps are GLOBAL: land what other tests
    left running, executing or parked first, so these drive only their own rows
    (the test database is throwaway) — the same settle tests/test_restart_landing.py
    uses."""
    async def settle():
        conn = await repo._conn()
        try:
            await conn.execute("update session set status = 'DONE' "
                               "where status in ('RUNNING', 'AWAITING_RESUME')")
            await conn.execute("update proposal set status = 'FAILED', retry_state = null "
                               "where status in ('EXECUTING', 'RETRY_PENDING')")
            await conn.execute("update task set status = 'CANCELLED' "
                               "where status in ('IN_PROGRESS', 'ASSIGNED')")
        finally:
            await conn.close()

    await settle()
    yield
    await orchestration.sweep_settle()
    await settle()


async def _rpc(method: str, params: dict | None = None) -> dict:
    return await nerve_gateway._dispatch(method, params or {}, None)


def _session_model(monkeypatch, model) -> None:
    """The approve route resolves the session's model itself: hand it the fake."""
    async def fixed(*_a, **_kw):
        return model

    monkeypatch.setattr(routes, "resolve_session_model", fixed)


async def _approve(pid: str) -> dict:
    """The cockpit's Approve click, then the detached closing turn it started."""
    out = await _rpc("proposals.approve", {"id": pid})
    await asyncio.gather(*list(gateway._detached_resumes))
    return out


class _Confluence:
    """Confluence's health as its probe answers, switched by the test."""

    def __init__(self, monkeypatch, up: bool = True):
        self.up = up

        async def _probe() -> bool:
            return self.up

        monkeypatch.setitem(probes.PROBES, "confluence", _probe)


async def _kinds(ref_id: str) -> list[str]:
    return [e["kind"] for e in await repo.list_events(ref_id=ref_id, limit=1000)]


async def _only_redraft(of: str) -> dict:
    redrafts = await _redrafts_of(of)
    assert len(redrafts) == 1, [r["id"] for r in redrafts]
    return redrafts[0]


# --- (a) the chain to its limit ------------------------------------------------------


@pytest.mark.usefixtures("confluence_grant")
async def test_a_page_moving_before_every_execution_redrafts_three_times_then_stops_loud(
    monkeypatch,
):
    page = _counting_confluence(monkeypatch, version=4)
    # The drafter reads the page as it stands, so every draft is current when
    # drafted; the page then moves on before the operator's approval runs.
    drafter = _Persistent(lambda _made: _page_update(page["version"]))
    first, task_id = await _task_session(monkeypatch, drafter)
    sid = first["session_id"]
    _session_model(monkeypatch, drafter.model)

    ids = [first["id"]]
    for n in (1, 2, 3):
        page["version"] += 1  # someone else edits the page
        out = await _approve(ids[-1])
        assert out["ok"] is False and "redraft_proposal_id" not in out
        redraft = await _only_redraft(ids[-1])
        assert redraft["status"] == "AWAITING_HUMAN", "every redraft waits for the operator"
        assert redraft["redraft"] == {"of": ids[-1], "root": first["id"],
                                      "cause": "world_moved", "world_moved_count": n}
        ids.append(redraft["id"])

    page["version"] += 1
    end = await _approve(ids[-1])
    assert end["chain_exhausted"] is True
    assert await _redrafts_of(ids[-1]) == []
    assert "PUT" not in page["calls"], "nothing was ever written"
    assert len(drafter.calls) == 4  # the draft plus three resumes; none at the end

    # Decision before failure on every link; one limit event, on the end.
    for pid in ids:
        kinds = await _kinds(pid)
        assert kinds.count("proposal.decided") == 1 and kinds.count("proposal.failed") == 1
        assert kinds.index("proposal.decided") < kinds.index("proposal.failed")
    assert sum([await _count("proposal.redraft_limit_reached", pid) for pid in ids]) == 1
    assert (await _event("proposal.redraft_limit_reached", ids[-1]))["payload"] == {
        "root": first["id"], "count": 3, "limit": 3, "proposal_id": ids[-1]}
    assert (await _event("proposal.failed", ids[-1]))["payload"]["chain_exhausted"] is True

    # Loud: the session rests flagged exec_failed, the task is FAILED.
    session = await repo.get_session(sid)
    assert session["status"] == "AWAITING_OPERATOR"
    assert "redraft limit 3" in session["exec_failure"]
    assert await _count("session.exec_failed", sid) == 1
    task = await repo.get_task(task_id)
    assert task["status"] == "FAILED" and "redraft limit 3" in task["outcome"]

    # The Decisions history shows the chain behind the end and marks it.
    history = jsonable_encoder(await _rpc("decisions.history", {"limit": 200}))
    rows = {r["id"]: r for r in history["proposals"]}
    assert rows[ids[-1]]["chain_exhausted"] is True
    assert [link["id"] for link in rows[ids[-1]]["chain"]] == ids[:3]
    assert rows[ids[-1]]["redraft_limit"] == 3
    for pid in ids[:3]:
        assert rows[pid]["chain_exhausted"] is False


# --- (b) the page moved during an outage -------------------------------------------


@pytest.mark.usefixtures("confluence_grant")
async def test_an_update_parked_by_a_down_confluence_redrafts_when_the_page_moved_meanwhile(
    monkeypatch,
):
    page = _counting_confluence(monkeypatch, version=4)
    drafter = _Drafter(_page_update(4), _page_update(6))
    row = await _draft(monkeypatch, drafter)
    _session_model(monkeypatch, drafter.model)
    _drafting_model(monkeypatch, drafter)
    health = _Confluence(monkeypatch, up=False)

    page["down"] = True
    out = await _approve(row["id"])
    assert out["execution"] == "parked"
    parked = await repo.load_proposal(row["id"])
    assert parked["status"] == "RETRY_PENDING"
    assert parked["retry_state"]["dependencies"] == ["confluence"]

    # The operator's retry-now while it is still down drives nothing.
    seen = len(page["calls"])
    assert (await _rpc("outages.retry", {"dependency": "confluence"}))["probe"] == "down"
    assert len(page["calls"]) == seen
    assert (await repo.load_proposal(row["id"]))["status"] == "RETRY_PENDING"

    # Confluence comes back with the page moved on; the probe flips.
    page["down"], page["version"] = False, 6
    health.up = True
    outage_over = len(page["calls"])
    back = await _rpc("outages.retry", {"dependency": "confluence"})
    await orchestration.sweep_settle()
    assert back["probe"] == "up" and back["driven"]["proposals"] == 1

    assert "PUT" not in page["calls"][outage_over:], "the re-check wrote nothing"
    assert (await repo.load_proposal(row["id"]))["status"] == "FAILED"
    moved = (await _event("proposal.failed", row["id"]))["payload"]["world_moved"]
    assert (moved["expected"], moved["actual"]) == (4, 6)
    redraft = await _only_redraft(row["id"])
    assert redraft["status"] == "AWAITING_HUMAN"
    assert redraft["redraft"]["cause"] == "world_moved"
    assert redraft["redraft"]["world_moved_count"] == 1


# --- (c) an interrupted execution ----------------------------------------------------


@pytest.mark.usefixtures("confluence_grant")
async def test_an_interrupted_execution_retried_by_the_operator_re_checks_and_says_so(
    monkeypatch,
):
    page = _counting_confluence(monkeypatch, version=4)
    drafter = _Drafter(_page_update(4), _page_update(6))
    row = await _draft(monkeypatch, drafter)
    _drafting_model(monkeypatch, drafter)
    _Confluence(monkeypatch, up=True)

    # Decided and claimed for execution, then the process died before the
    # write — staged in the gateway's own order, as the restart landing tests do.
    assert await repo.claim_proposal_for_execution(row["id"]) is not None
    await events.emit("proposal.decided", ref_id=row["id"],
                      payload={"verdict": "approved", "session_id": row["session_id"]},
                      actor="human:doe")
    await orchestration.land_orphans_at_startup()
    held = await repo.load_proposal(row["id"])
    assert held["status"] == "RETRY_PENDING" and held["retry_state"]["interrupted"] is True

    page["version"] = 6  # moved while the execution was cut off
    plain = await _rpc("outages.retry", {"dependency": "confluence"})
    await orchestration.sweep_settle()
    assert plain["driven"]["proposals"] == 0, "the hold is the operator's to lift"
    assert (await repo.load_proposal(row["id"]))["status"] == "RETRY_PENDING"

    out = await _rpc("outages.retry", {"dependency": "confluence",
                                       "includeInterrupted": True})
    await orchestration.sweep_settle()
    assert out["driven"]["proposals"] == 1

    assert "PUT" not in page["calls"]
    assert (await repo.load_proposal(row["id"]))["status"] == "FAILED"
    assert (await _event("proposal.failed", row["id"]))["payload"]["world_moved"]["actual"] == 6
    report = drafter.seen[-1]["returns"][-1]
    assert "interrupted by a restart while confluence.update_page (action 1)" in report
    redraft = await _only_redraft(row["id"])
    assert redraft["status"] == "AWAITING_HUMAN"
    assert redraft["redraft"]["cause"] == "world_moved"


# --- (d) an override never decides a redraft --------------------------------------


@pytest.mark.usefixtures("confluence_grant")
async def test_an_override_decided_proposal_whose_world_moved_redrafts_for_the_operator(
    monkeypatch,
):
    override = await repo.create_gate_override(
        AGENT, "confluence.update_page", shadow_runs=0, acknowledged_irreversible=True)
    try:
        page = _counting_confluence(monkeypatch, version=6)  # moved since the drafter read v4
        drafter = _Drafter(_page_update(4), _page_update(6))
        first = await _draft(monkeypatch, drafter)
        await orchestration.sweep_settle()

        decided = await _event("proposal.decided", first["id"])
        assert decided["actor"] == f"override:{override['id']}"
        assert (await repo.load_proposal(first["id"]))["status"] == "FAILED"
        redraft = await _only_redraft(first["id"])
        assert redraft["status"] == "AWAITING_HUMAN", "the override never decides a redraft"
        assert redraft["override_eval"]["reason"] == "redraft"
        assert "proposal.decided" not in await _kinds(redraft["id"])

        # The operator approves the redraft, which lands against the page as it is.
        _session_model(monkeypatch, drafter.model)
        await _approve(redraft["id"])
        assert (await repo.load_proposal(redraft["id"]))["status"] == "EXECUTED"
        assert page["calls"].count("PUT") == 1
        assert (await _event("proposal.decided", redraft["id"]))["actor"].startswith("human:")
    finally:
        await repo.revoke_gate_override(override["id"], reason="test teardown")


# --- (e) a plain NO never counts ---------------------------------------------------


async def test_a_jira_400_keeps_its_resume_and_never_counts_toward_the_limit(monkeypatch):
    from central_command.runtime.run import ingest_and_propose
    from central_command.runtime.spike_model import _PROPOSAL

    async def refused(issue_key, due_date):
        raise RuntimeError("400 due date is not a valid date")

    monkeypatch.setattr(executor.jira, "set_due_date", refused)
    drafter = _Persistent(lambda made: {**_PROPOSAL, "intent": f"Redraft {made}"})
    run = await ingest_and_propose("Dana: DEMO-1 slipped, due Aug 3.", model=drafter.model)
    _session_model(monkeypatch, drafter.model)

    pid = run["proposal_id"]
    for _ in range(4):  # past the world-moved limit of 3
        out = await _approve(pid)
        assert "chain_exhausted" not in out
        payload = (await _event("proposal.failed", pid))["payload"]
        assert payload["world_moved"] is None and "chain_exhausted" not in payload
        assert await _count("proposal.redraft_limit_reached", pid) == 0
        redraft = await _only_redraft(pid)
        assert redraft["status"] == "AWAITING_HUMAN"
        assert redraft["redraft"]["cause"] == "failed"
        assert redraft["redraft"]["world_moved_count"] == 0
        pid = redraft["id"]
    assert len(drafter.calls) == 5  # the draft plus one resume per failure


# --- (f) Dismiss never resumes ------------------------------------------------------


@pytest.mark.usefixtures("confluence_grant")
async def test_dismissing_a_redraft_never_resumes_the_drafter(monkeypatch):
    page = _counting_confluence(monkeypatch, version=4)
    drafter = _Drafter(_page_update(4), _page_update(5))
    first = await _draft(monkeypatch, drafter)
    _session_model(monkeypatch, drafter.model)
    _drafting_model(monkeypatch, drafter)
    page["version"] = 5
    await _approve(first["id"])
    redraft = await _only_redraft(first["id"])
    turns = len(drafter.seen)

    def _boom(*_a, **_kw):
        raise AssertionError("a dismissed redraft must never resume its drafter")

    monkeypatch.setattr(gateway, "build_agent_for", _boom)
    monkeypatch.setattr(agent_mod, "build_agent_for", _boom)

    out = await _rpc("proposals.dismiss", {"id": redraft["id"], "reason": "not needed"})
    assert out["status"] == "WITHDRAWN"
    # Nothing later picks it up either: the startup and outage sweeps find nothing.
    await orchestration.resume_sweep()
    await _rpc("outages.retry", {"dependency": "confluence"})
    await orchestration.sweep_settle()

    assert (await repo.load_proposal(redraft["id"]))["status"] == "WITHDRAWN"
    session = await repo.get_session(first["session_id"])
    assert session["status"] == "DONE" and session["closed_reason"] == "dismissed"
    assert len(drafter.seen) == turns
    assert await _redrafts_of(redraft["id"]) == []
    assert "PUT" not in page["calls"]


# --- (g) a death between the FAILED status and the resume ---------------------------


@pytest.mark.usefixtures("confluence_grant")
async def test_a_death_before_the_drafter_resumes_is_resumed_once_by_the_startup_sweeps(
    monkeypatch,
):
    page = _counting_confluence(monkeypatch, version=4)
    drafter = _Drafter(_page_update(4), _page_update(6))
    first = await _draft(monkeypatch, drafter)
    sid = first["session_id"]
    _session_model(monkeypatch, drafter.model)
    _drafting_model(monkeypatch, drafter)
    page["version"] = 6

    # The approve route records the failure, arms the resume and returns; the
    # process dies before the detached resume runs a single step.
    out = await _rpc("proposals.approve", {"id": first["id"]})
    assert out["resume"] == "detached"
    for t in list(gateway._detached_resumes):
        t.cancel()
    await asyncio.gather(*list(gateway._detached_resumes), return_exceptions=True)
    assert (await repo.load_proposal(first["id"]))["status"] == "FAILED"
    armed = (await repo.get_session_run_state(sid))["pending_resume"]
    assert armed["proposal_id"] == first["id"] and armed["redraft_cause"] == "world_moved"
    assert await _redrafts_of(first["id"]) == []
    turns = len(drafter.seen)
    # An armed resume reads as dead (not in flight) only once it is older than
    # `dispatch_stale_claim_seconds` (`repo.sessions_with_orphaned_resume`):
    # age the record past it, as the time a real restart takes would.
    conn = await repo._conn()
    try:
        await conn.execute(
            "update session set updated_at = now() - ($2 || ' seconds')::interval "
            "where id = $1", sid, str(settings.dispatch_stale_claim_seconds + 60))
    finally:
        await conn.close()

    # The lifespan's startup sweeps, in its order; the resume sweep twice, to
    # show the second finds nothing owed.
    await orchestration.land_orphans_at_startup()
    await routes.startup_task_sweep()
    await orchestration.resume_sweep()
    await orchestration.sweep_settle()
    await orchestration.resume_sweep()
    await orchestration.sweep_settle()

    resumed = drafter.seen[turns:]
    assert len(resumed) == 1, "the drafter is resumed exactly once"
    assert "the world moved" in resumed[0]["returns"][-1]
    redraft = await _only_redraft(first["id"])
    assert redraft["redraft"] == {"of": first["id"], "root": first["id"],
                                  "cause": "world_moved", "world_moved_count": 1}
    assert redraft["status"] == "AWAITING_HUMAN"
    assert "PUT" not in page["calls"]


# --- graph.delete_episode through the gateway (owed from story 4) -------------------


async def test_a_changed_deletion_preview_lands_world_moved_through_the_approve_route(
    graph_world,
):
    sid = "sess_wme_" + uuid.uuid4().hex[:8]
    await repo.create_running_session(sid, AGENT)
    pid = "prop_wme_" + uuid.uuid4().hex[:10]
    action = _delete_action(_preview("d1")).model_dump(mode="json")
    await repo.save_proposal(
        pid, sid, AGENT, "Delete episode X", [action],
        [{"kind": "operator", "source_ref": "operator:task", "locator": "test",
          "claim": "Operator asked for the deletion."}],
        "episode X is gone", pid,
    )
    graph_world["preview"] = _preview("d2", facts=["F1"])  # a fact landed since

    out = await _rpc("proposals.approve", {"id": pid})
    await asyncio.gather(*list(gateway._detached_resumes))

    assert out["ok"] is False
    assert (await repo.load_proposal(pid))["status"] == "FAILED"
    payload = (await _event("proposal.failed", pid))["payload"]
    assert payload["transient"] is False
    assert (payload["world_moved"]["expected"], payload["world_moved"]["actual"]) == (
        "d1", "d2")
    assert payload["world_moved"]["target"] == "graph:episode:X"
    kinds = await _kinds(pid)
    assert kinds.index("proposal.decided") < kinds.index("proposal.failed")
    assert graph_world["queued"] == [], "nothing was deleted"


# --- a coached charter edit the operator overtook (epic-small-fixes story 11.7, R24) --


async def test_an_operator_charter_edit_before_approval_fails_world_moved_and_the_coach_redrafts(
    monkeypatch,
):
    """The operator's ruling of 2026-10-29 (Decision 6), through the public
    seams: a coach run drafts an edit of v1, the operator saves the charter
    through its route before approving, and the cockpit's Approve lands FAILED
    world-moved; the coach redrafts against the text the report showed it, and
    an operator who keeps editing meets the redraft limit, loud."""
    from central_command.runtime.run import run_coach
    from tests.fake_world import fake_world

    fake_world(monkeypatch, real=("charter.update",))
    target = await _coached_target("wme-charter")
    coach = _Coach(target)
    _session_model(monkeypatch, coach.model)
    out = await run_coach(target, model=coach.model, note="Answer briefly.", note_event_id=0)
    first = await repo.load_proposal(out["proposal_id"])

    ids = [first["id"]]
    for n in (1, 2, 3):
        await routes.save_charter(target, routes.CharterIn(
            content=f"OPERATOR EDIT {n}", rationale="the operator's own edit"))
        approved = await _approve(ids[-1])
        assert approved["ok"] is False
        assert (await _event("proposal.failed", ids[-1]))["payload"]["world_moved"][
            "what_moved"] == f"{target}'s charter moved v{n} -> v{n + 1}"
        assert (await repo.current_charter(target))["content"] == f"OPERATOR EDIT {n}"
        redraft = await _only_redraft(ids[-1])
        assert redraft["status"] == "AWAITING_HUMAN"
        assert redraft["redraft"] == {"of": ids[-1], "root": first["id"],
                                      "cause": "world_moved", "world_moved_count": n}
        args = redraft["actions"][0]["arguments"]
        assert args["base_version"] == n + 1, "stamped with the version the report showed"
        assert f"OPERATOR EDIT {n}" in args["content"]
        ids.append(redraft["id"])

    await routes.save_charter(target, routes.CharterIn(content="OPERATOR EDIT 4"))
    end = await _approve(ids[-1])
    assert end["chain_exhausted"] is True
    assert await _redrafts_of(ids[-1]) == []
    assert (await repo.current_charter(target))["content"] == "OPERATOR EDIT 4"
    assert await _count("proposal.redraft_limit_reached", ids[-1]) == 1
