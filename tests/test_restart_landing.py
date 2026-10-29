"""Story 5 of epic-outage-doctrine: a restart lands nothing FAILED.

What a dead process leaves mid-flight is landed ONCE, at startup, by what it
owes (`orchestration.land_orphans_at_startup` → `repo.park_orphaned_sessions`,
`repo.park_interrupted_executions`, then `routes.startup_task_sweep` and the
ledger reclaim — the lifespan's order):

- a killed RESUME goes back to AWAITING_RESUME with its record intact, due now;
- a killed TASK RUN re-parks its task for a fresh re-run, due now, and its
  transcript is kept, closed STOPPED with a reason naming the task;
- a sessionless IN_PROGRESS task re-parks the same way;
- an EXECUTING proposal parks RETRY_PENDING `interrupted` (its known-complete
  prefix kept) and NO sweep moves it — the interim hold;
- a CLAIMED ledger item goes back to UNPROCESSED;
- a chat lane lands by where its record stops;
- the mid-life `Dispatcher.start` touches no session.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException

from central_command.api import orchestration, routes
from central_command.config import settings
from central_command.db import repo
from central_command.gateway import gateway
from central_command.ingest import ledger
from central_command.runtime.run import ingest_and_propose
from central_command.runtime.spike_model import make_sc1_model, make_spike_model
from tests.conftest import event_kinds as _kinds, needs_pg
from tests.test_execution_retry import _graph_spy, _jira_spy, _parked_run
from tests.test_outage_requeue import OUTAGE
from tests.test_resume_parking import _pending

pytestmark = needs_pg


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    monkeypatch.setattr(settings, "test_model", True)
    monkeypatch.setattr(settings, "dispatch_approval_limit", 10_000)


@pytest.fixture(autouse=True)
async def settle_leftovers():
    """Every sweep here is GLOBAL: land what other tests left RUNNING,
    EXECUTING or parked first, so this file's assertions see only its own rows,
    and settle its own afterwards (the test database is throwaway)."""
    async def settle():
        conn = await repo._conn()
        try:
            await conn.execute("update session set status = 'DONE' "
                               "where status in ('RUNNING', 'AWAITING_RESUME')")
            await conn.execute("update proposal set status = 'FAILED', retry_state = null "
                               "where status in ('EXECUTING', 'RETRY_PENDING')")
            await conn.execute("update task set status = 'CANCELLED' "
                               "where status in ('IN_PROGRESS', 'ASSIGNED')")
            await conn.execute("delete from work_item where message_id like '<cc-restart-%'")
        finally:
            await conn.close()

    await settle()
    yield
    await settle()


async def _sql(query: str, *args):
    conn = await repo._conn()
    try:
        return await conn.execute(query, *args)
    finally:
        await conn.close()


async def _running_task_run() -> tuple[str, str]:
    task_id = "task_" + uuid.uuid4().hex[:12]
    session_id = "sess_" + uuid.uuid4().hex[:12]
    await repo.create_task(task_id, "restart test", "x", "jira-expert")
    await repo.create_running_session(session_id, "jira-expert")
    await repo.start_task(task_id, session_id)
    return task_id, session_id


async def _claimed_resume(monkeypatch) -> str:
    """A session parked AWAITING_RESUME, then CLAIMED by a driver (RUNNING)
    that died mid-turn: the owed call is still the record's unanswered tail."""
    _jira_spy(monkeypatch)
    model = make_spike_model()
    run = await ingest_and_propose("Dana: DEMO-1 slipped, due Aug 3.", model=model)

    async def resume_boom(*a, **k):
        raise OUTAGE

    monkeypatch.setattr(gateway, "resume", resume_boom)
    await gateway.approve_and_execute(run["session_id"], run["proposal_id"], model=model)
    assert await repo.session_status(run["session_id"]) == "AWAITING_RESUME"
    assert await repo.claim_parked_resume(run["session_id"]) is not None
    return run["session_id"]


async def _executing_with_cursor(monkeypatch) -> str:
    """Decided by the operator; jira landed, graph parked it; then a retry
    claimed it (EXECUTING) and the process died — the cursor is at action 1."""
    parked = await _parked_run(monkeypatch)
    assert await repo.claim_proposal_retry(parked["proposal_id"]) is not None
    return parked["proposal_id"]


async def _executing_fresh() -> str:
    """Decided by the operator and claimed for execution, then the process
    died before the first action — in the gateway's own order: the decision
    event is written before the Executor runs."""
    from central_command import events

    model = make_sc1_model()
    run = await ingest_and_propose("Dana: DEMO-1 slipped, due Aug 3.", model=model)
    assert await repo.claim_proposal_for_execution(run["proposal_id"]) is not None
    await events.emit("proposal.decided", ref_id=run["proposal_id"],
                      payload={"verdict": "approved", "session_id": run["session_id"]},
                      actor="human:lee")
    return run["proposal_id"]


async def _lifespan_sweeps(monkeypatch) -> None:
    """The lifespan's startup sweeps, in its order. The never-started ASSIGNED
    relaunch is recorded rather than run: these tests are about landing."""
    launched: list[str] = []

    async def _record(task, actor, **kw):
        launched.append(task["id"])

    monkeypatch.setattr(routes, "_run_assigned_task_detached", _record)
    await orchestration.land_orphans_at_startup()
    await routes.startup_task_sweep()
    await repo.reclaim_stale_work_items(0)


# --- the whole startup -------------------------------------------------------------


async def test_a_restart_lands_nothing_failed(monkeypatch):
    task_run, task_session = await _running_task_run()
    resumed_session = await _claimed_resume(monkeypatch)
    pending_before = await _pending(resumed_session)
    sessionless = "task_" + uuid.uuid4().hex[:12]
    await repo.create_task(sessionless, "sessionless", "x", "jira-expert")
    await repo.start_task(sessionless, None)
    with_cursor = await _executing_with_cursor(monkeypatch)
    cursor_before = (await repo.load_proposal(with_cursor))["retry_state"]
    fresh = await _executing_fresh()
    raw = (f"Message-ID: <cc-restart-{uuid.uuid4().hex[:8]}@example.com>\n"
           "From: sender@example.com\nSubject: Restart test\n"
           "Date: Wed, 1 Jul 2026 09:00:00 +0000\n\nBody.\n")
    await ledger.enroll_email(raw)
    item = await repo.claim_next_work_item("sess_restart")
    assert item is not None

    await _lifespan_sweeps(monkeypatch)
    now = datetime.now(timezone.utc)

    # Nothing FAILED.
    for tid in (task_run, sessionless):
        row = await repo.get_task(tid)
        assert row["status"] == "ASSIGNED", tid
        assert row["retry_state"]["kind"] == "restart"
        assert datetime.fromisoformat(row["retry_state"]["not_before"]) <= now
        assert "task.run_failed" not in await _kinds(tid)
        assert "task.run_parked" in await _kinds(tid)
    assert await repo.session_status(task_session) == "STOPPED"
    assert task_run in (await repo.get_session(task_session))["closed_reason"]
    stopped = [e for e in await repo.list_events(ref_id=task_session)
               if e["kind"] == "session.stopped"][-1]
    assert stopped["payload"]["task_id"] == task_run

    assert await repo.session_status(resumed_session) == "AWAITING_RESUME"
    pending_after = await _pending(resumed_session)
    assert {k: v for k, v in pending_after.items() if k != "not_before"} == {
        k: v for k, v in pending_before.items() if k != "not_before"}
    assert datetime.fromisoformat(pending_after["not_before"]) <= now

    first = await repo.load_proposal(with_cursor)
    assert first["status"] == "RETRY_PENDING"
    assert first["retry_state"]["interrupted"] is True
    assert first["retry_state"]["next_action_index"] == 1
    assert first["retry_state"]["completed_results"] == cursor_before["completed_results"]
    assert first["retry_state"]["attempts"] == cursor_before["attempts"]
    second = await repo.load_proposal(fresh)
    assert second["status"] == "RETRY_PENDING"
    assert second["retry_state"]["interrupted"] is True
    assert second["retry_state"]["next_action_index"] == 0
    assert second["retry_state"]["completed_results"] == []
    assert second["retry_state"]["approver"] == "human:lee", "the decision's approver"
    for pid in (with_cursor, fresh):
        assert "proposal.execution_interrupted" in await _kinds(pid)

    conn = await repo._conn()
    try:
        state = await conn.fetchval("select state from work_item where id = $1", item["id"])
    finally:
        await conn.close()
    assert state == "UNPROCESSED"

    # The next sweep, every probe up: both tasks re-run, the owed resume is
    # re-driven, and both interrupted proposals are left exactly where they are.
    driven: list[tuple[str, str]] = []

    async def _task(tid, model=None):
        driven.append(("task", tid))
        return {"task_id": tid}

    async def _session(sid, model=None):
        driven.append(("session", sid))
        return {"session_id": sid}

    async def _no_replay(*a, **k):
        raise AssertionError("a sweep replayed an interrupted execution")

    monkeypatch.setattr(orchestration, "retry_parked_task", _task)
    monkeypatch.setattr(orchestration, "resume_parked_session", _session)
    monkeypatch.setattr(gateway, "retry_execution", _no_replay)
    result = await orchestration.retry_sweep()
    await orchestration.sweep_settle()

    assert {task_run, sessionless} <= set(result["retried_tasks"])
    assert resumed_session in result["resumed"]
    assert ("session", resumed_session) in driven
    assert {("task", task_run), ("task", sessionless)} <= set(driven)
    for pid in (with_cursor, fresh):
        assert pid not in result["retried_executions"]
        assert (await repo.load_proposal(pid))["status"] == "RETRY_PENDING"

    # And the startup resume_sweep skips them too.
    swept = await orchestration.resume_sweep()
    for pid in (with_cursor, fresh):
        assert pid not in swept["retried"] and pid not in swept["still_pending"]


# --- the pieces ------------------------------------------------------------------------


async def test_a_stale_pending_resume_is_not_owed():
    """`pending_resume` is never cleared after a successful retry. A later run
    of the same session that dies must not be mistaken for the old resume:
    the owed call is no longer the record's unanswered tail."""
    task_id, session_id = await _running_task_run()
    await repo.update_session_run_state(session_id, {
        "pending_resume": {"kind": "result", "tool_call_id": "call_old",
                           "after": "approved", "attempts": 1},
        "messages": [
            {"kind": "response", "parts": [
                {"part_kind": "tool-call", "tool_name": "propose_action",
                 "tool_call_id": "call_old", "args": {}}]},
            {"kind": "request", "parts": [
                {"part_kind": "tool-return", "tool_call_id": "call_old",
                 "tool_name": "propose_action", "content": "done"}]},
            {"kind": "response", "parts": [{"part_kind": "text", "content": "ok"}]},
        ],
    })
    landed = {r["session_id"]: r for r in await repo.park_orphaned_sessions()}
    assert landed[session_id]["outcome"] == "task_rerun"


async def _conversation(record: list[dict]) -> str:
    session_id = "sess_" + uuid.uuid4().hex[:12]
    await repo.create_running_session(session_id, "jira-expert")
    await _sql("update session set mode = 'conversation' where id = $1", session_id)
    await repo.update_session_run_state(session_id, {"messages": record})
    return session_id


_ASK = {"kind": "request", "parts": [{"part_kind": "user-prompt", "content": "hi"}]}


async def test_a_chat_lane_whose_turn_finished_is_landed_open():
    sid = await _conversation([
        _ASK, {"kind": "response", "parts": [{"part_kind": "text", "content": "hello"}]}])
    await orchestration.land_orphans_at_startup()
    assert await repo.session_status(sid) == "AWAITING_OPERATOR"


async def test_a_chat_lane_killed_at_an_unsent_request_parks_its_turn_due_now():
    """Rewritten for story 6 (it pinned story 5's interim STOPPED-and-resumable
    seam): the record ends in exactly a parked turn's shape, so the lane parks
    AWAITING_RESUME kind `turn` after `converse`, due now, and the sweep
    re-sends the operator's message — no click."""
    sid = await _conversation([_ASK])
    await orchestration.land_orphans_at_startup()
    assert await repo.session_status(sid) == "AWAITING_RESUME"
    pending = (await repo.get_session_run_state(sid))["pending_resume"]
    assert pending["kind"] == "turn" and pending["after"] == "converse"
    assert datetime.fromisoformat(pending["not_before"]) <= datetime.now(timezone.utc)
    assert sid in await repo.sessions_awaiting_resume(due_only=True)


async def test_a_chat_lane_with_unanswered_calls_loses_the_turn_not_the_lane():
    sid = await _conversation([_ASK, {"kind": "response", "parts": [
        {"part_kind": "tool-call", "tool_name": "jira_get_issue",
         "tool_call_id": "c1", "args": {}}]}])
    await orchestration.land_orphans_at_startup()
    assert await repo.session_status(sid) == "DONE"
    assert "say it again" in (await repo.get_session(sid))["closed_reason"]
    assert "session.turn_lost" in await _kinds(sid)
    assert "session.failed" not in await _kinds(sid)


async def test_any_other_run_closes_stopped_and_cannot_be_re_entered():
    sid = "sess_" + uuid.uuid4().hex[:12]
    await repo.create_running_session(sid, "jira-expert")
    await orchestration.land_orphans_at_startup()
    assert await repo.session_status(sid) == "STOPPED"
    assert await orchestration.resume_stopped_session(sid) is None
    assert await repo.session_status(sid) == "STOPPED", "the refusal claims nothing"


async def test_resuming_a_restart_closed_task_run_is_refused_and_says_why():
    task_id, session_id = await _running_task_run()
    await orchestration.land_orphans_at_startup()
    with pytest.raises(HTTPException) as refused:
        await routes.resume_session(session_id)
    assert refused.value.status_code == 409
    assert task_id in refused.value.detail


async def test_a_restart_closed_specialist_counts_as_finished_for_its_asker():
    sid = "sess_" + uuid.uuid4().hex[:12]
    await repo.create_running_session(sid, "jira-expert")
    await orchestration.land_orphans_at_startup()
    assert await orchestration._finished_for_waiters(await repo.get_session(sid))


async def test_the_mid_life_dispatcher_start_leaves_a_running_session_running(monkeypatch):
    from central_command.ingest import dispatcher as dispatcher_mod

    sid = "sess_" + uuid.uuid4().hex[:12]
    await repo.create_running_session(sid, "jira-expert")
    await _sql("update session set updated_at = now() - interval '3 hours' where id = $1", sid)

    async def _idle(self):
        return None

    monkeypatch.setattr(dispatcher_mod.Dispatcher, "_loop", _idle)
    d = dispatcher_mod.Dispatcher()
    try:
        await d.start()
        assert await repo.session_status(sid) == "RUNNING"
    finally:
        await d._release_lease()


async def test_a_retry_now_on_an_interrupted_proposal_runs_from_its_cursor(monkeypatch):
    """The hold is the SWEEPS' — the operator's own retry (story 9's lever) is
    `gateway.retry_execution`, and it replays from the kept cursor."""
    pid = await _executing_with_cursor(monkeypatch)
    await orchestration.land_orphans_at_startup()
    _graph_spy(monkeypatch)
    out = await gateway.retry_execution(pid, model=make_sc1_model())
    assert out["retried"] is True
    assert (await repo.load_proposal(pid))["status"] == "EXECUTED"


def test_the_old_failing_sweep_is_gone_from_the_tree():
    """`grep -rn` for the removed function over central_command/ and tests/
    finds nothing. The name is assembled so this file does not match itself."""
    names = ["fail_stale" + "_running_sessions", "sweep_orphaned" + "_sessions",
             "fail_sessionless" + "_in_progress_tasks"]
    pattern = re.compile("|".join(names))
    root = Path(__file__).resolve().parents[1]
    hits = [f"{p.relative_to(root)}:{i}"
            for base in ("central_command", "tests")
            for p in (root / base).rglob("*.py")
            for i, line in enumerate(p.read_text(errors="ignore").splitlines(), 1)
            if pattern.search(line)]
    assert hits == []
