"""Task backstops — provenance and the depth cap at the one create path.

Story 1 of epic-task-backstops, the tracer bullet: a task records who created it
and its chain of agent-created ancestors, and a hop past `task_max_depth` is
refused loudly. The first caller is the still-gated `task.create` handler, so the
chain here is built the way production builds it: an approved proposal row names
the proposing session, that session is linked to the task it is running
(`repo.start_task`, as `run.py` does at run start), and the Executor hands the
seam only the approver.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException

from central_command.api import routes
from central_command.config import settings
from central_command.db import repo
from central_command.gateway import executor
from tests.conftest import needs_pg

pytestmark = needs_pg

# Four taskable agents, then one that is not: the backstops are decided before
# the assignee's taskability, so the refused hop may name any roster id.
AGENTS = ["jira-expert", "litellm-manager", "confluence-expert", "wiki-agent",
          "inbox-triage"]


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "executor_mode", "live")

    async def noop(task, actor, **_kw):
        return None

    # The create path is under test; never let a real run detach from the test.
    monkeypatch.setattr(routes, "_run_assigned_task_detached", noop)


def _uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


async def _running(agent_id: str, task_id: str | None = None) -> str:
    """A session for `agent_id`, linked to `task_id` the way run.py links it."""
    session_id = _uid("sess")
    await repo.create_running_session(session_id, agent_id)
    if task_id:
        await repo.start_task(task_id, session_id)
    return session_id


async def _approve_task_create(session_id: str, proposer: str, assignee: str,
                               args_extra: dict | None = None,
                               approver: str = "human:lee") -> str | None:
    """Plant the proposal row for `session_id`, then run the Executor's handler
    under it — what an approval does. Returns the created task id."""
    proposal_id = _uid("prop")
    await repo.save_proposal(
        proposal_id, session_id, proposer, "delegate", [], [], "",
        idempotency_key=proposal_id,
    )
    args = {"agent_id": assignee, "instructions": "do the thing", **(args_extra or {})}
    since = await repo.latest_event_id()
    token = executor._current_proposal_id.set(proposal_id)
    try:
        await executor.HANDLERS["task.create"](args, approver=approver,
                                               proposer=proposer)
    finally:
        executor._current_proposal_id.reset(token)
    created = [e for e in await repo.list_events(since_id=since, kind="task")
               if e["kind"] == "task.created"]
    return created[-1]["ref_id"] if created else None


async def _operator_root(agent_id: str) -> str:
    out = await routes.create_and_run_task("root work", None, agent_id)
    return out["task_id"]


async def _refusals(since: int) -> list[dict]:
    return [e for e in await repo.list_events(since_id=since, kind="task")
            if e["kind"] == "task.refused"]


async def test_operator_and_heartbeat_creations_are_roots_with_an_empty_chain():
    op = await repo.get_task(await _operator_root(AGENTS[0]))
    assert op["created_by"] == "operator"
    assert op["creator_chain"] == []
    assert op["parent_task_id"] is None and op["approved_by"] is None

    out = await routes.create_and_run_task(
        "tick work", None, AGENTS[0], actor="heartbeat:nightly")
    hb = await repo.get_task(out["task_id"])
    assert hb["created_by"] == "heartbeat:nightly"
    assert hb["creator_chain"] == []


async def test_a_task_records_creator_approver_parent_and_chain():
    root = await _operator_root(AGENTS[0])
    session = await _running(AGENTS[0], root)

    child = await _approve_task_create(session, AGENTS[0], AGENTS[1])

    row = await repo.get_task(child)
    proposal_row_session = await repo.get_session(session)
    assert row["created_by"] == f"agent:{proposal_row_session['agent_id']}"
    assert row["approved_by"] == "human:lee"
    assert row["parent_task_id"] == root
    assert row["creator_chain"] == [AGENTS[0]]
    assert row["parent_session_id"] == session
    assert row["agent_id"] == AGENTS[1]


async def test_the_event_actor_stays_the_approver_while_gated():
    session = await _running(AGENTS[0])
    since = await repo.latest_event_id()
    child = await _approve_task_create(session, AGENTS[0], AGENTS[1])
    created = [e for e in await repo.list_events(since_id=since, kind="task")
               if e["kind"] == "task.created" and e["ref_id"] == child]
    assert created[0]["actor"] == "human:lee"


async def test_a_session_with_no_task_gets_a_chain_of_just_its_creator():
    chat = await _running(AGENTS[0])  # chat, triage, a consult specialist: no task

    child = await _approve_task_create(chat, AGENTS[0], AGENTS[1])

    row = await repo.get_task(child)
    assert row["creator_chain"] == [AGENTS[0]]
    assert row["parent_task_id"] is None
    assert row["created_by"] == f"agent:{AGENTS[0]}"


async def test_an_args_supplied_creator_is_ignored():
    session = await _running(AGENTS[0])

    child = await _approve_task_create(session, AGENTS[0], AGENTS[1], args_extra={
        "created_by": "operator", "creator_chain": [], "parent_task_id": "task_x",
        "approved_by": "nobody", "actor": "operator",
    })

    row = await repo.get_task(child)
    assert row["created_by"] == f"agent:{AGENTS[0]}"
    assert row["creator_chain"] == [AGENTS[0]]
    assert row["parent_task_id"] is None
    assert row["approved_by"] == "human:lee"


async def test_a_chain_of_approved_proposals_stops_at_the_cap():
    """operator task -> T1 -> T2 -> T3 is allowed; T4 is refused (cap 3)."""
    assert settings.task_max_depth == 3
    task = await _operator_root(AGENTS[0])
    chain: list[str] = []
    for hop in range(1, 4):
        creator = AGENTS[hop - 1]
        session = await _running(creator, task)
        task = await _approve_task_create(session, creator, AGENTS[hop])
        chain.append(creator)
        assert (await repo.get_task(task))["creator_chain"] == chain

    before = len(await repo.list_tasks())
    since = await repo.latest_event_id()
    last_session = await _running(AGENTS[3], task)
    with pytest.raises(HTTPException) as exc:
        await _approve_task_create(last_session, AGENTS[3], AGENTS[4])

    assert exc.value.status_code == 422
    assert "depth" in exc.value.detail
    assert len(await repo.list_tasks()) == before  # no row for the refused hop
    (refused,) = await _refusals(since)
    assert refused["ref_id"] == task  # the parent task
    assert refused["payload"]["reason"] == "depth"
    assert refused["payload"]["chain"] == AGENTS[:4]
    assert refused["payload"]["creator"] == f"agent:{AGENTS[3]}"
    assert refused["payload"]["agent_id"] == AGENTS[4]
    # No task.created for the refused hop.
    assert not [e for e in await repo.list_events(since_id=since, kind="task")
                if e["kind"] == "task.created"]


async def test_a_refusal_by_a_session_without_a_task_hangs_on_the_session(monkeypatch):
    monkeypatch.setattr(settings, "task_max_depth", 0)
    session = await _running(AGENTS[0])
    since = await repo.latest_event_id()

    with pytest.raises(HTTPException):
        await _approve_task_create(session, AGENTS[0], AGENTS[1])

    (refused,) = await _refusals(since)
    assert refused["ref_id"] == session
    assert refused["payload"]["chain"] == [AGENTS[0]]


async def test_the_cap_is_a_setting(monkeypatch):
    monkeypatch.setattr(settings, "task_max_depth", 1)
    root = await _operator_root(AGENTS[0])
    t1 = await _approve_task_create(await _running(AGENTS[0], root), AGENTS[0], AGENTS[1])
    with pytest.raises(HTTPException):
        await _approve_task_create(await _running(AGENTS[1], t1), AGENTS[1], AGENTS[2])


async def test_an_agent_created_task_with_no_assignee_is_refused():
    session = await _running(AGENTS[0])
    before = len(await repo.list_tasks())
    since = await repo.latest_event_id()

    with pytest.raises(HTTPException) as exc:
        await routes.create_and_run_task(
            "unrouted", None, None, actor="human:lee", background=True,
            parent_session_id=session, approved_by="human:lee")

    assert exc.value.status_code == 422 and "assignee" in exc.value.detail
    assert len(await repo.list_tasks()) == before
    (refused,) = await _refusals(since)
    assert refused["payload"]["reason"] == "no-assignee"
    assert refused["payload"]["agent_id"] is None


async def test_an_operator_task_with_no_assignee_is_still_allowed(monkeypatch):
    from central_command.runtime import orchestrator

    async def no_recommend(task):
        return None

    monkeypatch.setattr(orchestrator, "recommend", no_recommend)
    out = await routes.create_and_run_task("route me", None, None)
    assert (await repo.get_task(out["task_id"]))["status"] == "NEW"


async def test_a_creating_session_that_does_not_exist_is_refused_without_a_row():
    before = len(await repo.list_tasks())
    with pytest.raises(HTTPException) as exc:
        await routes.create_and_run_task(
            "x", None, AGENTS[1], parent_session_id="sess_missing")
    assert exc.value.status_code == 422
    assert len(await repo.list_tasks()) == before
