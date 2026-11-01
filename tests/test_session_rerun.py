"""Re-run a finished task run under the current charter (epic-cockpit-rerun R1, R2).

The old session closes with its record kept, the same task runs again in a fresh
session under the charter current NOW, and both rows name each other. The tests
drive the RPC where they can, so what they prove is what the cockpit gets.
"""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from fastapi import HTTPException
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from central_command.api import nerve_gateway, routes
from central_command.config import settings
from central_command.db import repo
from central_command.gateway import gateway, rerun
from central_command.runtime import converse
from tests.conftest import aresolve, needs_pg
from tests.fake_world import fake_world

pytestmark = needs_pg

AGENT = "jira-expert"


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    monkeypatch.setattr(settings, "test_model", True)
    fake_world(monkeypatch)
    monkeypatch.setattr(settings, "auditor_enabled", False)
    monkeypatch.setattr(
        routes, "resolve_model",
        aresolve(lambda *a, **k: FunctionModel(
            lambda m, i: ModelResponse(parts=[TextPart("the answer")]))),
    )


async def _noop(*a, **k):
    return None


async def _finished_task() -> tuple[str, str]:
    task_id = "task_" + uuid.uuid4().hex[:12]
    await repo.create_task(task_id, "Rerun me", "Answer the question.", AGENT)
    out = await routes._run_assigned_task(await repo.get_task(task_id))
    return task_id, out["session_id"]


async def _proposal(session_id: str, status: str = "AWAITING_HUMAN") -> str:
    proposal_id = "prop_" + uuid.uuid4().hex[:12]
    await repo.save_proposal(
        proposal_id, session_id, AGENT, "Do a thing", [], [], "an effect",
        "idem_" + proposal_id)
    if status != "AWAITING_HUMAN":
        await repo.set_proposal_status(proposal_id, status)
    return proposal_id


async def _rpc_rerun(session_id: str) -> dict:
    return await nerve_gateway._dispatch("sessions.rerun", {"sessionId": session_id}, _noop)


async def _settled(new_session_id: str) -> dict:
    """The new run started detached: wait for its row to land."""
    for _ in range(400):
        row = await repo.get_session(new_session_id)
        if row and row["status"] != "RUNNING":
            return row
        await asyncio.sleep(0.02)
    raise AssertionError(f"{new_session_id} never landed")


def _kinds_after(events: list[dict], *kinds: str) -> list[str]:
    return [e["kind"] for e in sorted(events, key=lambda e: e["id"]) if e["kind"] in kinds]


async def _events_since(after_id: int, *ref_ids: str) -> list[dict]:
    conn = await repo._conn()
    try:
        rows = await conn.fetch(
            "select id, kind, ref_id, payload from audit_event where id > $1 and ref_id = any($2::text[])",
            after_id, list(ref_ids),
        )
        return [dict(r) for r in rows]
    finally:
        await conn.close()


async def _last_event_id() -> int:
    conn = await repo._conn()
    try:
        return await conn.fetchval("select coalesce(max(id), 0) from audit_event")
    finally:
        await conn.close()


# ── the action ──────────────────────────────────────────────────────────────


async def test_a_rerun_closes_the_old_session_and_runs_under_the_current_charter():
    task_id, old = await _finished_task()
    before = await repo.get_session_run_state(old)
    charter = await repo.save_charter_version(AGENT, "The charter as it is now.", "test")
    mark = await _last_event_id()

    out = await _rpc_rerun(old)
    new = out["sessionId"]
    assert out["sessionKey"] == f"agent:{AGENT}:{new}"
    row = await _settled(new)

    closed = await repo.get_session(old)
    assert (closed["status"], closed["closed_reason"]) == ("DONE", "rerun")
    assert closed["rerun_by"] == new
    assert row["rerun_of"] == old
    assert (await repo.get_session_run_state(old))["messages"] == before["messages"], (
        "sessions are never destroyed: the old transcript stays as it was"
    )

    task = await repo.get_task(task_id)
    assert task["session_id"] == new
    assert task["status"] == "DONE"
    assert task["retry_state"] is None

    first = (await repo.get_session_run_state(new))["messages"][0]
    system = [p["content"] for p in first["parts"] if p["part_kind"] == "system-prompt"]
    # The one system part is composed around the charter (a team preamble before
    # it, the generated capability sections after), so the text is contained.
    assert len(system) == 1
    assert charter["content"] in system[0]
    old_first = before["messages"][0]
    assert not any(charter["content"] in p.get("content", "") for p in old_first["parts"]
                   if p["part_kind"] == "system-prompt"), "the old record is what it was told"

    events = await _events_since(mark, old, task_id, new)
    assert _kinds_after(events, "session.rerun", "task.started")[:2] == [
        "session.rerun", "task.started"]
    reruns = [e for e in events if e["kind"] == "session.rerun"]
    assert len(reruns) == 1
    assert reruns[0]["ref_id"] == old
    payload = reruns[0]["payload"]
    payload = payload if isinstance(payload, dict) else json.loads(payload)
    assert payload == {"new_session_id": new, "task_id": task_id,
                       "charter_version": charter["version"]}


async def test_the_rpc_returns_the_new_key_without_awaiting_the_run():
    _, old = await _finished_task()
    lock = routes._agent_task_lock(AGENT)
    await lock.acquire()
    try:
        out = await asyncio.wait_for(_rpc_rerun(old), timeout=5)
        await asyncio.sleep(0.05)
        task = await repo.task_for_session(old)
        assert task["status"] == "ASSIGNED", "queued behind its agent's lane"
        assert await repo.get_session(out["sessionId"]) is None
    finally:
        lock.release()
    await _settled(out["sessionId"])


async def test_undecided_work_is_withdrawn():
    _, old = await _finished_task()
    proposal = await _proposal(old)
    item = "item_" + uuid.uuid4().hex[:12]
    await repo.create_operator_item(item, "question", old, AGENT, "Which one?")

    out = await _rpc_rerun(old)
    await _settled(out["sessionId"])

    assert (await repo.load_proposal(proposal))["status"] == "WITHDRAWN"
    assert item not in [i["id"] for i in await repo.list_operator_items("OPEN")]


async def test_the_shared_wind_down_releases_folds_for_the_rerun_too(monkeypatch):
    _, old = await _finished_task()
    proposal = await _proposal(old)
    released = []

    async def fake_release(pid, reason):
        released.append((pid, reason))
        return 0

    monkeypatch.setattr(gateway, "release_folds_for", fake_release)
    out = await _rpc_rerun(old)
    await _settled(out["sessionId"])
    assert released == [(proposal, "covering proposal withdrawn")]


async def test_a_second_rerun_of_the_new_session_extends_the_chain():
    task_id, first = await _finished_task()
    second = (await _rpc_rerun(first))["sessionId"]
    await _settled(second)
    third = (await _rpc_rerun(second))["sessionId"]
    await _settled(third)

    assert (await repo.get_session(second))["rerun_of"] == first
    assert (await repo.get_session(second))["rerun_by"] == third
    for sid in (first, second, third):
        assert (await repo.task_for_session(sid))["id"] == task_id


async def test_a_rerun_closes_with_no_reflection(monkeypatch):
    from central_command.runtime import reflection

    async def refuse(*a, **k):
        raise AssertionError("a re-run close must not reflect")

    monkeypatch.setattr(reflection, "reflect_on_close", refuse)
    monkeypatch.setattr(settings, "test_model", False)
    monkeypatch.setattr(settings, "reflection_enabled", True)
    session_id = "sess_" + uuid.uuid4().hex[:12]
    await repo.create_running_session(session_id, AGENT)
    await converse.close_session(session_id, agent_id=AGENT, reason="rerun", actor="operator")
    await asyncio.sleep(0)
    assert not converse._REFLECTIONS


# ── the refusals change nothing ─────────────────────────────────────────────


async def _assert_refused(session_id: str, needle: str) -> None:
    before = await repo.get_session(session_id)
    task_before = await repo.task_for_session(session_id)
    with pytest.raises(HTTPException) as e:
        await _rpc_rerun(session_id)
    assert e.value.status_code == 409
    assert needle in e.value.detail
    assert await repo.get_session(session_id) == before
    assert await repo.task_for_session(session_id) == task_before


async def test_a_running_session_is_refused():
    task_id = "task_" + uuid.uuid4().hex[:12]
    await repo.create_task(task_id, "Live", "Work.", AGENT)
    session_id = "sess_" + uuid.uuid4().hex[:12]
    await repo.create_running_session(session_id, AGENT)
    await repo.start_task(task_id, session_id)
    await _assert_refused(session_id, "stop it first")


@pytest.mark.parametrize("status", ["APPROVED", "EXECUTING", "RETRY_PENDING"])
async def test_decided_work_still_landing_is_refused(status):
    _, old = await _finished_task()
    await _proposal(old, status)
    await _assert_refused(old, "still landing")


async def test_a_session_already_rerun_is_refused():
    _, old = await _finished_task()
    new = (await _rpc_rerun(old))["sessionId"]
    await _settled(new)
    await _assert_refused(old, f"already re-run as {new}")


async def test_two_presses_claim_once():
    _, old = await _finished_task()
    results = await asyncio.gather(_rpc_rerun(old), _rpc_rerun(old), return_exceptions=True)
    won = [r for r in results if isinstance(r, dict)]
    lost = [r for r in results if isinstance(r, HTTPException)]
    assert (len(won), len(lost)) == (1, 1), results
    await _settled(won[0]["sessionId"])
    assert (await repo.get_session(old))["rerun_by"] == won[0]["sessionId"]


async def test_other_kinds_refuse_as_not_yet():
    session_id = "sess_" + uuid.uuid4().hex[:12]
    await repo.create_running_session(session_id, AGENT)
    await repo.mark_session_done(session_id)
    await _assert_refused(session_id, "only a task run")

    conversation = "sess_" + uuid.uuid4().hex[:12]
    await repo.create_conversation_session(conversation, AGENT)
    await repo.mark_session_awaiting_operator(conversation)
    await _assert_refused(conversation, "not built yet")


async def test_an_orchestrator_project_refuses_as_not_yet():
    task_id = "task_" + uuid.uuid4().hex[:12]
    await repo.create_task(task_id, "Project", "Plan it.", "orchestrator")
    session_id = "sess_" + uuid.uuid4().hex[:12]
    await repo.create_running_session(session_id, "orchestrator")
    await repo.start_task(task_id, session_id)
    await repo.mark_session_done(session_id)
    await _assert_refused(session_id, "not built yet")


async def test_an_unknown_session_is_a_404():
    with pytest.raises(HTTPException) as e:
        await _rpc_rerun("sess_nothing_here")
    assert e.value.status_code == 404


async def test_a_claim_lost_to_a_moved_task_rolls_the_link_back():
    _, old = await _finished_task()
    assert not await repo.claim_rerun(old, "sess_new", "task_not_this_one")
    assert (await repo.get_session(old))["rerun_by"] is None


# ── the old session stays readable ──────────────────────────────────────────


async def test_the_old_session_keeps_its_task_and_its_kind_and_locks():
    task_id, old = await _finished_task()
    new = (await _rpc_rerun(old))["sessionId"]
    await _settled(new)

    assert (await repo.task_for_session(old))["id"] == task_id

    history = await nerve_gateway._dispatch(
        "chat.history", {"sessionKey": f"agent:{AGENT}:{old}"}, _noop)
    composer = history["composer"]
    assert composer["enabled"] is False
    assert composer["code"] == "rerun"
    assert composer["kind"] == "task run"
    assert new in composer["message"]
    assert composer["task"]["id"] == task_id
    assert composer["session"]["rerunBy"] == new
    assert composer["rerun"]["eligible"] is False
    assert history["messages"], "the transcript stays readable"

    with pytest.raises(HTTPException) as e:
        await nerve_gateway._dispatch(
            "chat.send", {"sessionKey": f"agent:{AGENT}:{old}", "message": "hello"}, _noop)
    assert e.value.status_code == 409


async def test_the_composer_offers_a_rerun_only_where_the_server_says_so():
    _, old = await _finished_task()
    history = await nerve_gateway._dispatch(
        "chat.history", {"sessionKey": f"agent:{AGENT}:{old}"}, _noop)
    assert history["composer"]["rerun"] == {"eligible": True, "reason": None}
    assert history["composer"]["session"]["rerunOf"] is None

    new = (await _rpc_rerun(old))["sessionId"]
    await _settled(new)
    fresh = await nerve_gateway._dispatch(
        "chat.history", {"sessionKey": f"agent:{AGENT}:{new}"}, _noop)
    assert fresh["composer"]["session"]["rerunOf"] == old
    assert fresh["composer"]["rerun"]["eligible"] is True

    conversation = "sess_" + uuid.uuid4().hex[:12]
    await repo.create_conversation_session(conversation, AGENT)
    await repo.mark_session_awaiting_operator(conversation)
    open_lane = await nerve_gateway._dispatch(
        "chat.history", {"sessionKey": f"agent:{AGENT}:{conversation}"}, _noop)
    assert open_lane["composer"]["enabled"] is True
    assert open_lane["composer"]["rerun"]["eligible"] is False


async def test_the_list_row_carries_the_pair_and_the_old_row_keeps_its_title():
    _, old = await _finished_task()
    new = (await _rpc_rerun(old))["sessionId"]
    await _settled(new)

    rows = {r["id"]: r for r in await repo.list_sessions(limit=200, agent_id=AGENT)}
    assert rows[old]["rerun_by"] == new
    assert rows[new]["rerun_of"] == old
    assert rows[old]["task_title"] == "Rerun me"
    assert rows[new]["task_title"] == "Rerun me"


async def test_a_late_landing_of_the_old_session_does_not_resolve_the_task():
    task_id, old = await _finished_task()
    lock = routes._agent_task_lock(AGENT)
    await lock.acquire()
    try:
        new = (await _rpc_rerun(old))["sessionId"]
        await gateway._resolve_task_after(old, "DONE", "late")
        assert (await repo.get_task(task_id))["status"] == "ASSIGNED"
    finally:
        lock.release()
    await _settled(new)
    assert (await repo.get_task(task_id))["outcome"] != "late"


# ── one starter, and a restart keeps the promise ────────────────────────────


async def test_the_sweep_never_sees_a_queued_rerun():
    task_id, old = await _finished_task()
    lock = routes._agent_task_lock(AGENT)
    await lock.acquire()
    try:
        new = (await _rpc_rerun(old))["sessionId"]
        retryable = [t["id"] for t in await repo.tasks_awaiting_retry(due_only=False)]
        assert task_id not in retryable
    finally:
        lock.release()
    await _settled(new)


async def test_a_restart_while_queued_relaunches_under_the_promised_id(monkeypatch):
    """The in-memory queue dies with the process. The startup relaunch finds the
    task plain ASSIGNED and the record still says which id it owes."""
    task_id, old = await _finished_task()
    new = "sess_" + uuid.uuid4().hex[:12]
    assert await repo.claim_rerun(old, new, task_id)

    await routes._run_assigned_task(await repo.get_task(task_id))

    assert (await repo.get_session(new))["rerun_of"] == old
    assert (await repo.get_task(task_id))["session_id"] == new


async def test_a_restart_before_the_new_row_exists_keeps_the_chain():
    """The window between `task.started` and the session row. A plain task is
    sessionless here; a re-run still names its old session, and the landing that
    finds a sessionless run must find this one too."""
    task_id, old = await _finished_task()
    new = "sess_" + uuid.uuid4().hex[:12]
    assert await repo.claim_rerun(old, new, task_id)
    await repo.start_task(task_id, old)

    parked = [t["id"] for t in await repo.park_sessionless_in_progress_tasks()]
    assert task_id in parked
    task = await repo.get_task(task_id)
    assert task["status"] == "ASSIGNED"
    assert task["session_id"] == old
    assert (await repo.pending_rerun(old)) == {"rerun_of": old, "session_id": new}


async def test_an_ordinary_task_start_owes_no_rerun():
    assert await repo.pending_rerun(None) is None
    _, old = await _finished_task()
    assert await repo.pending_rerun(old) is None


# ── the action and the offer agree, and the schema ──────────────────────────


async def test_the_shape_of_the_refusal_is_the_one_eligibility_gave():
    _, old = await _finished_task()
    await _proposal(old, "APPROVED")
    state = await rerun.eligibility(await repo.get_session(old))
    assert state["eligible"] is False
    with pytest.raises(rerun.RerunRefused, match=state["reason"]):
        await rerun.rerun_session(old)


async def test_the_columns_exist_on_the_shipped_schema():
    conn = await repo._conn()
    try:
        names = {r["column_name"] for r in await conn.fetch(
            "select column_name from information_schema.columns where table_name = 'session'")}
    finally:
        await conn.close()
    assert {"rerun_of", "rerun_by"} <= names
