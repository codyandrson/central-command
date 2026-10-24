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


# --- story 2: cycle and self ------------------------------------------------


def _teammates(detail: str) -> list[str]:
    """The ids a refusal names after 'instead:'."""
    return [t.strip() for t in detail.split("instead:")[1].rstrip(".").split(",")]


async def test_a_to_b_to_a_is_refused_as_a_cycle_naming_teammates():
    a, b, c, d = AGENTS[:4]
    root = await _operator_root(a)
    t1 = await _approve_task_create(await _running(a, root), a, b)
    before = len(await repo.list_tasks())
    since = await repo.latest_event_id()

    with pytest.raises(HTTPException) as exc:
        await _approve_task_create(await _running(b, t1), b, a)

    assert exc.value.status_code == 422
    assert exc.value.detail.startswith("task refused (cycle):")
    named = _teammates(exc.value.detail)
    assert c in named and d in named
    assert a not in named and b not in named
    assert len(await repo.list_tasks()) == before
    (refused,) = await _refusals(since)
    assert refused["ref_id"] == t1
    assert refused["payload"]["reason"] == "cycle"
    assert refused["payload"]["chain"] == [a, b]
    assert refused["payload"]["agent_id"] == a
    assert not [e for e in await repo.list_events(since_id=since, kind="task")
                if e["kind"] == "task.created"]


async def test_a_to_a_is_refused_as_self_naming_teammates():
    a, b = AGENTS[:2]
    root = await _operator_root(a)
    before = len(await repo.list_tasks())
    since = await repo.latest_event_id()

    with pytest.raises(HTTPException) as exc:
        await _approve_task_create(await _running(a, root), a, a)

    assert exc.value.detail.startswith("task refused (self):")
    named = _teammates(exc.value.detail)
    assert b in named and a not in named
    assert len(await repo.list_tasks()) == before
    (refused,) = await _refusals(since)
    assert refused["payload"]["reason"] == "self"
    assert refused["payload"]["chain"] == [a]


async def test_orchestrator_to_orchestrator_is_refused_as_self():
    since = await repo.latest_event_id()
    with pytest.raises(HTTPException) as exc:
        await _approve_task_create(await _running("orchestrator"),
                                   "orchestrator", "orchestrator")
    assert exc.value.detail.startswith("task refused (self):")
    assert "orchestrator" not in _teammates(exc.value.detail)
    (refused,) = await _refusals(since)
    assert refused["payload"]["reason"] == "self"
    assert refused["payload"]["creator"] == "agent:orchestrator"


async def test_the_orchestrator_in_the_chain_is_refused_as_a_cycle():
    a = AGENTS[0]
    t1 = await _approve_task_create(await _running("orchestrator"), "orchestrator", a)
    with pytest.raises(HTTPException) as exc:
        await _approve_task_create(await _running(a, t1), a, "orchestrator")
    assert exc.value.detail.startswith("task refused (cycle):")


async def test_b_to_c_both_new_to_the_chain_is_not_refused():
    a, b, c = AGENTS[:3]
    root = await _operator_root(a)
    t1 = await _approve_task_create(await _running(a, root), a, b)
    since = await repo.latest_event_id()

    t2 = await _approve_task_create(await _running(b, t1), b, c)

    assert (await repo.get_task(t2))["creator_chain"] == [a, b]
    assert not await _refusals(since)


async def test_operator_and_heartbeat_tasks_to_any_agent_are_never_cycles():
    a = AGENTS[0]
    since = await repo.latest_event_id()
    for actor in ("operator", "heartbeat:nightly"):
        out = await routes.create_and_run_task("work", None, a, actor=actor)
        assert out["task_id"]
        out = await routes.create_and_run_task("again", None, a, actor=actor)
        assert out["task_id"]
    assert not await _refusals(since)


async def test_a_consult_specialist_inherits_the_asking_tasks_chain():
    """A task-less specialist session drafts a task from inside a task chain:
    the chain carries on from the asker's task, so the count cannot reset."""
    a, b, c, d = AGENTS[:4]
    root = await _operator_root(a)
    t1 = await _approve_task_create(await _running(a, root), a, b)  # chain [a]
    asker = await _running(b, t1)
    specialist = await _running(c)
    await repo.save_proposal(
        _uid("prop"), specialist, c, "consulted", [], [], "",
        idempotency_key=_uid("key"),
        origin={"consulted_by": b, "caller_session_id": asker},
    )

    t2 = await _approve_task_create(specialist, c, d)

    assert (await repo.get_task(t2))["creator_chain"] == [a, c]

    # ...and the inherited chain is what the cycle check reads.
    specialist2 = await _running(c)
    await repo.save_proposal(
        _uid("prop"), specialist2, c, "consulted", [], [], "",
        idempotency_key=_uid("key"),
        origin={"consulted_by": b, "caller_session_id": asker},
    )
    with pytest.raises(HTTPException) as exc:
        await _approve_task_create(specialist2, c, a)
    assert exc.value.detail.startswith("task refused (cycle):")


async def test_a_specialist_inside_a_depth_two_chain_is_counted_at_depth_three():
    a, b, c, e, d = AGENTS  # d (inbox-triage) only creates, e is taskable
    root = await _operator_root(a)
    t1 = await _approve_task_create(await _running(a, root), a, b)
    t2 = await _approve_task_create(await _running(b, t1), b, c)  # chain [a, b]
    asker = await _running(c, t2)
    specialist = await _running(d)
    await repo.save_proposal(
        _uid("prop"), specialist, d, "consulted", [], [], "",
        idempotency_key=_uid("key"),
        origin={"consulted_by": c, "caller_session_id": asker},
    )

    t3 = await _approve_task_create(specialist, d, e)

    assert (await repo.get_task(t3))["creator_chain"] == [a, b, d]  # depth 3: allowed
    with pytest.raises(HTTPException) as exc:  # its next hop is depth 4
        await _approve_task_create(await _running(e, t3), e, c)
    assert exc.value.detail.startswith("task refused (depth):")


# --- fan-out (story 3) ---------------------------------------------------------


async def _created_under(parent_task: str) -> int:
    return await repo.count_child_tasks(parent_task_id=parent_task)


async def test_the_limit_plus_one_creation_from_one_task_is_refused(monkeypatch):
    monkeypatch.setattr(settings, "task_fanout_limit", 3)
    a, b = AGENTS[:2]
    root = await _operator_root(a)
    session = await _running(a, root)

    for _ in range(3):  # the first `limit` are not refused
        assert await _approve_task_create(session, a, b)
    assert await _created_under(root) == 3

    before = len(await repo.list_tasks())
    since = await repo.latest_event_id()
    with pytest.raises(HTTPException) as exc:
        await _approve_task_create(session, a, b)

    assert exc.value.status_code == 422
    assert exc.value.detail.startswith("task refused (fan-out):")
    assert len(await repo.list_tasks()) == before  # no row for the refused one
    (refused,) = await _refusals(since)
    assert refused["ref_id"] == root
    assert refused["payload"]["reason"] == "fan-out"
    assert refused["payload"]["chain"] == [a]
    assert refused["payload"]["limit"] == 3 and refused["payload"]["created"] == 3
    assert not [e for e in await repo.list_events(since_id=since, kind="task")
                if e["kind"] == "task.created"]


async def test_the_fan_out_limit_defaults_to_25():
    assert settings.task_fanout_limit == 25


async def test_the_count_survives_a_retry_parked_rerun_in_a_new_session(monkeypatch):
    """A retry-parked task re-runs in a NEW session (run.py re-links the task to
    it): the count is by task, so the new session continues it and the old
    session's creations still count."""
    monkeypatch.setattr(settings, "task_fanout_limit", 3)
    a, b = AGENTS[:2]
    root = await _operator_root(a)
    first = await _running(a, root)
    for _ in range(2):
        assert await _approve_task_create(first, a, b)

    second = await _running(a, root)  # the re-run: same task, new session
    assert await _approve_task_create(second, a, b)  # the 3rd of 3
    with pytest.raises(HTTPException) as exc:
        await _approve_task_create(second, a, b)
    assert exc.value.detail.startswith("task refused (fan-out):")
    assert await _created_under(root) == 3


async def test_two_tasks_count_separately(monkeypatch):
    monkeypatch.setattr(settings, "task_fanout_limit", 1)
    a, b = AGENTS[:2]
    root1, root2 = await _operator_root(a), await _operator_root(a)
    assert await _approve_task_create(await _running(a, root1), a, b)
    assert await _approve_task_create(await _running(a, root2), a, b)
    with pytest.raises(HTTPException):
        await _approve_task_create(await _running(a, root1), a, b)


async def test_a_creator_with_no_task_is_counted_by_its_session(monkeypatch):
    monkeypatch.setattr(settings, "task_fanout_limit", 2)
    a, b = AGENTS[:2]
    session = await _running(a)
    for _ in range(2):
        assert await _approve_task_create(session, a, b)
    since = await repo.latest_event_id()
    with pytest.raises(HTTPException) as exc:
        await _approve_task_create(session, a, b)
    assert exc.value.detail.startswith("task refused (fan-out):")
    (refused,) = await _refusals(since)
    assert refused["ref_id"] == session
    # Another conversation starts its own count.
    assert await _approve_task_create(await _running(a), a, b)


async def test_an_inherited_chain_counts_toward_the_ancestor_task(monkeypatch):
    """A task-less specialist inside a task chain gets the ancestor as its
    parent task, so its creations and the asker's share one count — and the
    refusal hangs on the ancestor task."""
    monkeypatch.setattr(settings, "task_fanout_limit", 2)
    a, b, c, d = AGENTS[:4]
    root = await _operator_root(a)
    t1 = await _approve_task_create(await _running(a, root), a, b)
    asker = await _running(b, t1)
    assert await _approve_task_create(asker, b, d)  # 1 under t1

    specialist = await _running(c)
    await repo.save_proposal(
        _uid("prop"), specialist, c, "consulted", [], [], "",
        idempotency_key=_uid("key"),
        origin={"consulted_by": b, "caller_session_id": asker},
    )
    t3 = await _approve_task_create(specialist, c, d)  # 2 under t1
    row = await repo.get_task(t3)
    assert row["parent_task_id"] == t1
    assert await _created_under(t1) == 2

    since = await repo.latest_event_id()
    with pytest.raises(HTTPException) as exc:  # the asker's next is the 3rd
        await _approve_task_create(asker, b, d)
    assert exc.value.detail.startswith("task refused (fan-out):")
    (refused,) = await _refusals(since)
    assert refused["ref_id"] == t1


async def test_a_mid_chain_consult_session_finds_its_askers_task_chain(monkeypatch):
    """`consult._park_mid_chain` mints the session with the asker as its
    `parent_session_id`: such a specialist, resumed as a full agent, creates a
    task inside the asker's chain rather than a fresh one."""
    monkeypatch.setattr(settings, "task_fanout_limit", 1)
    a, b, c, d = AGENTS[:4]
    root = await _operator_root(a)
    t1 = await _approve_task_create(await _running(a, root), a, b)
    asker = await _running(b, t1)
    mid = _uid("sess")
    await repo.create_running_session(mid, c, parent_session_id=asker)

    t2 = await _approve_task_create(mid, c, d)

    row = await repo.get_task(t2)
    assert row["creator_chain"] == [a, c]
    assert row["parent_task_id"] == t1
    with pytest.raises(HTTPException) as exc:
        await _approve_task_create(asker, b, d)  # t1's count is now 1 of 1
    assert exc.value.detail.startswith("task refused (fan-out):")
