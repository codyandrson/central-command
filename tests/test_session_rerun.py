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
_ENROLLED: list[str] = []


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


@pytest.fixture(autouse=True)
async def clean_charter():
    """A charter saved here must not become every later test's CURRENT one."""
    yield
    conn = await repo._conn()
    try:
        await conn.execute("delete from guidance_version where agent_id = $1", AGENT)
    finally:
        await conn.close()


@pytest.fixture(autouse=True)
async def enrolled_items():
    """An item left CLAIMED, or PROCESSED over an open proposal, holds the
    relatedness lock against every later item of its sender family, and
    `example.com` is one family. What a test enrolled leaves with it."""
    yield
    if _ENROLLED:
        conn = await repo._conn()
        try:
            await conn.execute("delete from work_item where id = any($1::text[])", _ENROLLED)
        finally:
            await conn.close()
        _ENROLLED.clear()


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
            # The row lands before the starter's own tail resolves the task.
            await asyncio.gather(*list(gateway._detached_resumes), return_exceptions=True)
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


async def test_an_unknown_session_is_a_404():
    with pytest.raises(HTTPException) as e:
        await _rpc_rerun("sess_nothing_here")
    assert e.value.status_code == 404


async def test_a_claim_lost_to_a_moved_task_rolls_the_link_back():
    _, old = await _finished_task()
    proposal = await _proposal(old)
    assert not await repo.claim_rerun(old, "sess_new", "task_not_this_one")
    row = await repo.get_session(old)
    assert row["rerun_by"] is None
    assert row["closed_reason"] is None
    assert (await repo.load_proposal(proposal))["status"] == "AWAITING_HUMAN"


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
    assert open_lane["composer"]["rerun"] == {"eligible": False, "reason": rerun.NO_STORED_INPUTS}


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


async def test_a_cancel_while_queued_voids_the_start():
    """The starter waited on the lane holding the row as it was. A cancelled task
    must stay cancelled, a re-run or an ordinary one."""
    for rerun_it in (True, False):
        if rerun_it:
            task_id, old = await _finished_task()
            await _proposal(old)
        else:
            task_id = "task_" + uuid.uuid4().hex[:12]
            await repo.create_task(task_id, "Queued", "Work.", AGENT)
        lock = routes._agent_task_lock(AGENT)
        await lock.acquire()
        try:
            if rerun_it:
                await _rpc_rerun(old)
            else:
                queued = asyncio.create_task(
                    routes._run_assigned_task(await repo.get_task(task_id)))
            await asyncio.sleep(0.02)
            await routes.cancel_task(task_id)
        finally:
            lock.release()
        if not rerun_it:
            out = await queued
            assert out["superseded"] == "CANCELLED"
        await asyncio.sleep(0.1)
        await lock.acquire()
        lock.release()
        task = await repo.get_task(task_id)
        assert task["status"] == "CANCELLED"


async def test_a_retry_start_waiting_on_the_lane_yields_to_a_rerun():
    """The sweep claimed the task and waits on the lane holding an IN_PROGRESS row;
    a re-run takes the task meanwhile. One run, the re-run's."""
    task_id, old = await _finished_task()
    claimed_row = {**(await repo.get_task(task_id)), "status": "IN_PROGRESS"}
    lock = routes._agent_task_lock(AGENT)
    await lock.acquire()
    try:
        stale = asyncio.create_task(routes._run_assigned_task_inner(claimed_row, actor="system"))
        await asyncio.sleep(0.02)
        new = (await _rpc_rerun(old))["sessionId"]
    finally:
        lock.release()
    assert (await stale)["superseded"] == "ASSIGNED"
    await _settled(new)
    assert (await repo.get_task(task_id))["session_id"] == new


async def test_nothing_can_land_on_the_old_session_once_claimed():
    """The claim closes and withdraws in its own transaction, so an approval or a
    parked resume arriving right after it finds nothing to claim."""
    task_id, old = await _finished_task()
    proposal = await _proposal(old)
    conn = await repo._conn()
    try:
        await conn.execute("update session set status = 'AWAITING_RESUME' where id = $1", old)
    finally:
        await conn.close()

    claimed = await repo.claim_rerun(old, "sess_" + uuid.uuid4().hex[:12], task_id)

    assert claimed["proposals"] == [proposal]
    assert await repo.claim_proposal_for_execution(proposal) is None
    assert await repo.claim_parked_resume(old) is None
    row = await repo.get_session(old)
    assert (row["status"], row["closed_reason"]) == ("DONE", "rerun")


async def test_a_claim_refused_by_a_landing_proposal_changes_nothing():
    task_id, old = await _finished_task()
    undecided = await _proposal(old)
    await _proposal(old, "EXECUTING")

    assert await repo.claim_rerun(old, "sess_new", task_id) is None

    assert (await repo.load_proposal(undecided))["status"] == "AWAITING_HUMAN"
    assert (await repo.get_session(old))["rerun_by"] is None


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


# ── the table, a test per row ───────────────────────────────────────────────


def _text_model(text: str = "the answer") -> FunctionModel:
    return FunctionModel(lambda m, i: ModelResponse(parts=[TextPart(text)]))


async def _closed_oneshot(agent_id: str) -> str:
    session_id = "sess_" + uuid.uuid4().hex[:12]
    await repo.create_running_session(session_id, agent_id)
    await repo.mark_session_done(session_id)
    return session_id


async def _conversation(opening: str = "What is on my plate?") -> str:
    out = await converse.start_conversation(AGENT, opening, model=_text_model())
    return out["session_id"]


def _first_user_prompt(run_state: dict) -> str:
    return next(p["content"] for m in run_state["messages"] for p in m["parts"]
                if p["part_kind"] == "user-prompt")


async def _composer_rerun(session: dict) -> dict:
    history = await nerve_gateway._dispatch(
        "chat.history", {"sessionKey": f"agent:{session['agent_id']}:{session['id']}"}, _noop)
    return history["composer"]["rerun"]


async def _enrolled_email(subject: str) -> str:
    item_id = "wi_" + uuid.uuid4().hex[:12]
    _ENROLLED.append(item_id)
    conn = await repo._conn()
    try:
        await conn.execute(
            """
            insert into work_item (id, message_id, feed, source, subject, payload, kind)
            values ($1, $2, 'live', 'test', $3, $4::jsonb, 'email')
            """,
            item_id, f"<{item_id}@example.com>", subject,
            json.dumps({"text": f"Subject: {subject}\n\nPlease look.", "from": "pat@example.com"}),
        )
    finally:
        await conn.close()
    return item_id


async def _triaged_email(subject: str) -> tuple[str, str]:
    """An email through the real dispatcher path, to its terminal mark."""
    from central_command.ingest import dispatcher

    item_id = await _enrolled_email(subject)
    out = await dispatcher.dispatch_item(item_id)
    assert out and out.get("session_id"), out
    return item_id, out["session_id"]


async def _landed_email(subject: str) -> tuple[str, str]:
    """A landed item and its session, planted: the claim's lock is not under test."""
    session_id = await _closed_oneshot("inbox-triage")
    item_id = await _enrolled_email(subject)
    conn = await repo._conn()
    try:
        await conn.execute(
            "update work_item set state = 'PROCESSED', session_id = $2 where id = $1",
            item_id, session_id)
    finally:
        await conn.close()
    return item_id, session_id


async def _item_session(item_id: str) -> tuple[str, str]:
    conn = await repo._conn()
    try:
        row = await conn.fetchrow("select session_id, state from work_item where id = $1", item_id)
        return row["session_id"], row["state"]
    finally:
        await conn.close()


def test_every_row_says_where_its_inputs_live_and_what_starts_it_or_why_not():
    assert set(rerun.KINDS) == {"task", "orchestrator", "conversation", "email", "discussion",
                                "document", "wiki-repair", "agent-run"}
    for row in rerun.KINDS.values():
        assert row.inputs
        assert bool(row.start) != bool(row.refusal), row.name


async def test_row_orchestrator_runs_a_fresh_project_session_linked_both_ways(monkeypatch):
    from central_command.api import orchestration

    calls = []
    task_id = "task_" + uuid.uuid4().hex[:12]
    await repo.create_task(task_id, "Project", "Plan it.", "orchestrator")
    old = "sess_" + uuid.uuid4().hex[:12]
    await repo.create_running_session(old, "orchestrator")
    await repo.start_task(task_id, old)
    await repo.mark_session_done(old)
    await repo.resolve_task(task_id, "DONE", outcome="planned")

    async def fake_project(task, model=None, *, session_id=None, rerun_of=None):
        calls.append((task["id"], session_id, rerun_of))
        await repo.create_running_session(session_id, "orchestrator", rerun_of=rerun_of)
        await repo.start_task(task["id"], session_id)
        await repo.mark_session_done(session_id)
        return {"session_id": session_id}

    monkeypatch.setattr(orchestration, "run_project", fake_project)
    assert await _composer_rerun(await repo.get_session(old)) == {"eligible": True, "reason": None}

    new = (await _rpc_rerun(old))["sessionId"]
    await _settled(new)

    assert calls == [(task_id, new, old)]
    assert (await repo.get_session(old))["rerun_by"] == new
    assert (await repo.get_session(new))["rerun_of"] == old


async def test_the_real_run_project_creates_its_session_under_the_owed_id():
    """The fake above proves the wiring; this proves the entry takes the id."""
    from central_command.api import orchestration

    task_id = "task_" + uuid.uuid4().hex[:12]
    await repo.create_task(task_id, "Project", "Plan it.", "orchestrator")
    old = "sess_" + uuid.uuid4().hex[:12]
    new = "sess_" + uuid.uuid4().hex[:12]
    await orchestration.run_project(
        await repo.get_task(task_id), model=None, session_id=new, rerun_of=old)
    row = await repo.get_session(new)
    assert row["rerun_of"] == old
    assert (await repo.get_task(task_id))["session_id"] == new


async def test_row_orchestrator_refuses_while_a_child_is_not_terminal():
    task_id = "task_" + uuid.uuid4().hex[:12]
    await repo.create_task(task_id, "Project", "Plan it.", "orchestrator")
    old = "sess_" + uuid.uuid4().hex[:12]
    await repo.create_running_session(old, "orchestrator")
    await repo.start_task(task_id, old)
    await repo.mark_session_done(old)
    child = "task_" + uuid.uuid4().hex[:12]
    await repo.create_task(child, "Child", "Do it.", AGENT, parent_session_id=old)

    await _assert_refused(old, "children still running")
    assert (await _composer_rerun(await repo.get_session(old)))["eligible"] is False
    # The claim holds it too, for a child that arrives after the check.
    assert await repo.claim_rerun(old, "sess_x", task_id, refuse_live_children=True) is None

    await repo.resolve_task(child, "DONE", outcome="done")
    assert (await rerun.eligibility(await repo.get_session(old)))["eligible"] is True


async def test_row_conversation_opens_a_new_lane_from_the_opening_message():
    old = await _conversation("What is on my plate?")
    await converse.conversation_turn(old, "And after that?", model=_text_model())
    conn = await repo._conn()
    try:
        await conn.execute(
            "update session set model_override = 'cc-special', thinking_override = 'low' "
            "where id = $1", old)
    finally:
        await conn.close()
    assert await _composer_rerun(await repo.get_session(old)) == {"eligible": True, "reason": None}

    new = (await _rpc_rerun(old))["sessionId"]
    row = await _settled(new)

    assert row["mode"] == "conversation"
    assert row["rerun_of"] == old
    assert (row["model_override"], row["thinking_override"]) == ("cc-special", "low")
    assert _first_user_prompt(await repo.get_session_run_state(new)) == "What is on my plate?"
    prompts = [p["content"] for m in (await repo.get_session_run_state(new))["messages"]
               for p in m["parts"] if p["part_kind"] == "user-prompt"]
    assert prompts == ["What is on my plate?"], "the opening message only, not every turn"

    closed = await repo.get_session(old)
    assert (closed["status"], closed["closed_reason"], closed["rerun_by"]) == ("DONE", "rerun", new)
    assert (await nerve_gateway._latest_conversation(AGENT, open_only=True))["id"] == new

    history = await nerve_gateway._dispatch(
        "chat.history", {"sessionKey": f"agent:{AGENT}:{old}"}, _noop)
    composer = history["composer"]
    assert (composer["enabled"], composer["code"], composer["kind"]) == (False, "rerun", "conversation")
    assert new in composer["message"]
    assert history["messages"], "the old lane stays readable"


async def test_row_conversation_opening_survives_compaction():
    assert rerun._opening_message({
        "archived": [{"kind": "request", "parts": [{"part_kind": "user-prompt", "content": "first"}]}],
        "messages": [{"kind": "request", "parts": [{"part_kind": "user-prompt", "content": "later"}]}],
    }) == "first"


async def test_row_conversation_with_no_opening_message_refuses():
    lane = (await converse.start_conversation(AGENT))["session_id"]
    await _assert_refused(lane, rerun.NO_STORED_INPUTS)


async def test_row_discussion_refuses():
    lane = await _conversation()
    item = "item_" + uuid.uuid4().hex[:12]
    await repo.create_operator_item(item, "question", lane, AGENT, "Which one?")
    await repo.link_operator_item_discussion(item, lane)
    await _assert_refused(lane, rerun.NO_STORED_INPUTS)


async def test_row_email_keeps_the_link_until_the_new_runs_terminal_mark(monkeypatch):
    from central_command.ingest import dispatcher

    subject = "Rerun subject " + uuid.uuid4().hex[:8]
    item_id, old = await _triaged_email(subject)
    assert await _item_session(item_id) == (old, "PROCESSED")
    assert await _composer_rerun(await repo.get_session(old)) == {"eligible": True, "reason": None}

    seen_during_the_run = []
    real = dispatcher.ingest_and_propose

    async def watching(*a, **k):
        seen_during_the_run.append(await _item_session(item_id))
        return await real(*a, **k)

    monkeypatch.setattr(dispatcher, "ingest_and_propose", watching)
    new = (await _rpc_rerun(old))["sessionId"]
    row = await _settled(new)

    assert seen_during_the_run == [(old, "CLAIMED")], "the old session keeps its item"
    assert row["rerun_of"] == old
    assert (await repo.get_session(old))["rerun_by"] == new
    for _ in range(200):
        if (await _item_session(item_id))[0] == new:
            break
        await asyncio.sleep(0.02)
    assert (await _item_session(item_id))[0] == new, "the new run's terminal mark moves it"

    rows = {r["id"]: r for r in await repo.list_sessions(limit=200, agent_id="inbox-triage")}
    assert rows[old]["work_subject"] == subject
    assert rows[new]["work_subject"] == subject
    assert [i["id"] for i in await repo.work_items_for_session(old)] == [item_id]
    assert (await repo.work_items_for_sessions([old]))[old]["id"] == item_id
    composer = (await nerve_gateway._dispatch(
        "chat.history", {"sessionKey": f"agent:inbox-triage:{old}"}, _noop))["composer"]
    assert composer["kind"] == "triage run"


async def test_row_email_requeue_is_rule_exempt_and_keeps_the_link():
    item_id, old = await _landed_email("Exempt " + uuid.uuid4().hex[:8])
    new = "sess_" + uuid.uuid4().hex[:12]
    assert await repo.claim_rerun(old, new, work_item_id=item_id)
    assert await _item_session(item_id) == (old, "UNPROCESSED")
    assert (await repo.pending_rerun_for_work_item(item_id)) == {"rerun_of": old, "session_id": new}
    conn = await repo._conn()
    try:
        payload = await conn.fetchval("select payload from work_item where id = $1", item_id)
    finally:
        await conn.close()
    payload = json.loads(payload) if isinstance(payload, str) else payload
    assert payload["rule_exempt"] is True


@pytest.mark.parametrize("state", ["CLAIMED", "UNPROCESSED"])
async def test_row_email_refuses_while_its_item_is_in_the_queue(state):
    item_id, old = await _landed_email("Busy " + uuid.uuid4().hex[:8])
    conn = await repo._conn()
    try:
        await conn.execute("update work_item set state = $2 where id = $1", item_id, state)
    finally:
        await conn.close()
    await _assert_refused(old, "already queued")


@pytest.mark.parametrize("kind", ["document", "wiki_repair"])
async def test_rows_document_and_wiki_repair_refuse(kind):
    session_id = await _closed_oneshot("inbox-triage")
    item_id = await _enrolled_email("Kind " + uuid.uuid4().hex[:8])
    conn = await repo._conn()
    try:
        await conn.execute(
            "update work_item set kind = $2, state = 'PROCESSED', session_id = $3 where id = $1",
            item_id, kind, session_id)
    finally:
        await conn.close()
    await _assert_refused(session_id, rerun.NO_STORED_INPUTS)
    assert (await _composer_rerun(await repo.get_session(session_id)))["reason"] == rerun.NO_STORED_INPUTS


async def test_a_kind_the_table_does_not_list_refuses():
    session_id = await _closed_oneshot("inbox-triage")
    item_id = await _enrolled_email("Kind " + uuid.uuid4().hex[:8])
    conn = await repo._conn()
    try:
        await conn.execute(
            "update work_item set kind = 'not_a_kind', state = 'PROCESSED', session_id = $2 "
            "where id = $1", item_id, session_id)
    finally:
        await conn.close()
    await _assert_refused(session_id, "cannot be re-run")


@pytest.mark.parametrize("agent_id", ["coach", "wiki-agent", "rerun-test-specialist"])
async def test_row_agent_run_refuses_a_session_with_no_stored_inputs(agent_id):
    """Coach, heartbeat, steward, wiki and consult sessions: no task, no item.
    The third is private: `upsert_agent` over a seeded id would change the roster."""
    if agent_id == "rerun-test-specialist":
        await repo.upsert_agent(agent_id, agent_id, "", "")
    session_id = await _closed_oneshot(agent_id)
    await _assert_refused(session_id, rerun.NO_STORED_INPUTS)
    assert (await _composer_rerun(await repo.get_session(session_id))) == {
        "eligible": False, "reason": rerun.NO_STORED_INPUTS}


async def test_a_running_session_says_so_on_the_composer_too():
    session_id = "sess_" + uuid.uuid4().hex[:12]
    await repo.create_running_session(session_id, "coach")
    await _assert_refused(session_id, "stop it first")
    assert (await _composer_rerun(await repo.get_session(session_id)))["reason"] == (
        "it is running; stop it first")


async def test_an_update_hold_refuses_every_kind(monkeypatch):
    from central_command.runtime import hold

    _, old = await _finished_task()
    monkeypatch.setattr(hold, "active", True)
    await _assert_refused(old, "update")


async def test_a_landing_proposal_refuses_a_conversation_too():
    lane = await _conversation()
    await _proposal(lane, "APPROVED")
    await _assert_refused(lane, "still landing")


# ── the charter stamp ───────────────────────────────────────────────────────


async def test_a_rerun_carries_the_current_version_and_the_old_keeps_its_own():
    before = await repo.save_charter_version(AGENT, "The charter before.", "test")
    task_id, old = await _finished_task()
    after = await repo.save_charter_version(AGENT, "The charter after.", "test")

    stale = await repo.get_session(old)
    assert (stale["charter_version"], stale["charter_current"]) == (before["version"], after["version"])

    new = (await _rpc_rerun(old))["sessionId"]
    row = await _settled(new)

    assert row["charter_version"] == after["version"]
    assert (await repo.get_session(old))["charter_version"] == before["version"]
    composer = (await nerve_gateway._dispatch(
        "chat.history", {"sessionKey": f"agent:{AGENT}:{old}"}, _noop))["composer"]
    assert composer["session"]["charterVersion"] == before["version"]
    assert composer["session"]["charterCurrent"] == after["version"]


async def test_a_conversation_rerun_carries_the_current_version_too():
    before = await repo.save_charter_version(AGENT, "The lane charter before.", "test")
    old = await _conversation()
    after = await repo.save_charter_version(AGENT, "The lane charter after.", "test")

    new = (await _rpc_rerun(old))["sessionId"]
    row = await _settled(new)

    assert row["charter_version"] == after["version"]
    assert (await repo.get_session(old))["charter_version"] == before["version"]


# ── an attempt that died keeps the chain (found by rehearsal R9) ────────────


async def test_an_email_rerun_that_met_a_down_dependency_keeps_the_chain(monkeypatch):
    """The dependency being DOWN is the case the doctrine promises: the item goes back
    to the queue and its retry ran under a new id with no link, so the old session lost
    its email and its subject at the retry's terminal mark."""
    import httpx

    from central_command.ingest import dispatcher

    subject = "Down " + uuid.uuid4().hex[:8]
    item_id, old = await _triaged_email(subject)

    async def down(*a, **k):
        def refuse(messages, info):
            raise httpx.ConnectError("connection refused")
        return FunctionModel(refuse)

    real = dispatcher.resolve_model
    monkeypatch.setattr(dispatcher, "resolve_model", down)
    first_attempt = (await _rpc_rerun(old))["sessionId"]
    await _settled(first_attempt)
    assert await _item_session(item_id) == (old, "UNPROCESSED"), "released, link kept"

    monkeypatch.setattr(dispatcher, "resolve_model", real)
    out = await dispatcher.dispatch_item(item_id)
    retry = out["session_id"]

    assert retry != first_attempt
    assert (await repo.get_session(retry))["rerun_of"] == first_attempt
    assert (await repo.get_session(first_attempt))["rerun_by"] == retry
    assert (await _item_session(item_id))[0] == retry
    assert [i["id"] for i in await repo.work_items_for_session(old)] == [item_id]
    rows = {r["id"]: r for r in await repo.list_sessions(limit=200, agent_id="inbox-triage")}
    assert rows[old]["work_subject"] == subject


async def test_a_task_rerun_started_again_keeps_the_chain():
    """A restart or a retry starts a dead run fresh, under a new id."""
    task_id, old = await _finished_task()
    attempt = (await _rpc_rerun(old))["sessionId"]
    await _settled(attempt)

    conn = await repo._conn()
    try:
        await conn.execute("update task set status = 'ASSIGNED' where id = $1", task_id)
    finally:
        await conn.close()
    out = await routes._run_assigned_task(await repo.get_task(task_id))
    retry = out["session_id"]

    assert (await repo.get_session(retry))["rerun_of"] == attempt
    assert (await repo.get_session(attempt))["rerun_by"] == retry
    assert (await repo.task_for_session(old))["id"] == task_id


async def test_a_session_in_no_chain_starts_with_no_link():
    _, plain = await _finished_task()
    assert await repo.pending_rerun(plain) is None
    assert (await repo.get_session(plain))["rerun_by"] is None


# ── a queued re-run says so (found by rehearsal R9) ─────────────────────────


async def test_a_queued_reruns_lane_says_it_is_queued_not_that_nothing_is_there():
    _, old = await _finished_task()
    lock = routes._agent_task_lock(AGENT)
    await lock.acquire()
    try:
        new = (await _rpc_rerun(old))["sessionId"]
        composer = (await nerve_gateway._dispatch(
            "chat.history", {"sessionKey": f"agent:{AGENT}:{new}"}, _noop))["composer"]
    finally:
        lock.release()
    assert composer["code"] == "no_session"
    assert old in composer["message"] and "queued" in composer["message"]
    assert composer["rerun"] == {"eligible": False, "reason": None}, (
        "a lane with no row yet has nothing to refuse"
    )
    await _settled(new)


async def test_a_key_nothing_names_still_says_there_is_no_session():
    composer = (await nerve_gateway._dispatch(
        "chat.history", {"sessionKey": f"agent:{AGENT}:sess_nothing_here"}, _noop))["composer"]
    assert composer["message"] == "no session sess_nothing_here"


async def test_a_charter_frame_names_its_agent_in_ref_id():
    """The cockpit reloads the header on this frame, reading the agent from `ref_id`."""
    mark = await _last_event_id()
    await routes.save_charter(AGENT, routes.CharterIn(content="A pushed charter.", rationale="test"))
    events = await _events_since(mark, AGENT)
    assert [e["kind"] for e in events] == ["charter.updated"]
