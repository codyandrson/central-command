"""Task backstops — provenance and the depth cap at the one create path.

Story 1 of epic-task-backstops, the tracer bullet: a task records who created it
and its chain of agent-created ancestors, and a hop past `task_max_depth` is
refused loudly. Since story 4.7 the agent's caller is the direct `create_task`
tool (task.create left the gate), so the chain here is built the way production
builds it: a running session of the creating agent, linked to the task it is
running (`repo.start_task`, as `run.py` does at run start), calls the tool, and
the tool hands the seam only that session.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from central_command.api import routes
from central_command.config import settings
from central_command.db import repo
from central_command.runtime.deps import TriageDeps
from central_command.runtime.tools import create_task
from tests.conftest import needs_pg

pytestmark = needs_pg

# Four taskable agents, then one that is not: the backstops are decided before
# the assignee's taskability, so the refused hop may name any roster id.
AGENTS = ["jira-expert", "litellm-manager", "confluence-expert", "wiki-agent",
          "inbox-triage"]


@pytest.fixture(autouse=True)
def pinned(monkeypatch, no_detached_runs):
    monkeypatch.setattr(settings, "test_model", True)


def _uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


async def _running(agent_id: str, task_id: str | None = None) -> str:
    """A session for `agent_id`, linked to `task_id` the way run.py links it."""
    session_id = _uid("sess")
    await repo.create_running_session(session_id, agent_id)
    if task_id:
        await repo.start_task(task_id, session_id)
    return session_id


_REFUSED = "Task NOT created — "


class Refused(Exception):
    """The tool's refusal text (the seam's 422 detail), raised so a refusal
    reads in these tests the way the seam's own HTTPException does."""

    status_code = 422

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


async def _agent_creates(session_id: str, creator: str, assignee: str) -> str | None:
    """`creator`'s running session calls `create_task` — what an agent does.
    Returns the created task id; raises `Refused` with the refusal text."""
    since = await repo.latest_event_id()
    ctx = SimpleNamespace(deps=TriageDeps(agent_id=creator, session_id=session_id))
    out = await create_task(ctx, assignee, "do the thing")
    if out.startswith(_REFUSED):
        raise Refused(out[len(_REFUSED):])
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


async def test_a_task_records_creator_parent_and_chain_and_no_approver():
    root = await _operator_root(AGENTS[0])
    session = await _running(AGENTS[0], root)

    child = await _agent_creates(session, AGENTS[0], AGENTS[1])

    row = await repo.get_task(child)
    creating_session = await repo.get_session(session)
    assert row["created_by"] == f"agent:{creating_session['agent_id']}"
    assert row["approved_by"] is None  # ungated: nobody approved the creation
    assert row["parent_task_id"] == root
    assert row["creator_chain"] == [AGENTS[0]]
    assert row["parent_session_id"] == session
    assert row["agent_id"] == AGENTS[1]


async def test_the_event_actor_is_the_creating_agent():
    """GATE-033 made the approver the actor while the gate was on; ungated, the
    creating agent is (FR-6: the actor of a task is its creator)."""
    session = await _running(AGENTS[0])
    since = await repo.latest_event_id()
    child = await _agent_creates(session, AGENTS[0], AGENTS[1])
    created = [e for e in await repo.list_events(since_id=since, kind="task")
               if e["kind"] == "task.created" and e["ref_id"] == child]
    assert created[0]["actor"] == f"agent:{AGENTS[0]}"


async def test_a_session_with_no_task_gets_a_chain_of_just_its_creator():
    chat = await _running(AGENTS[0])  # chat, triage, a consult specialist: no task

    child = await _agent_creates(chat, AGENTS[0], AGENTS[1])

    row = await repo.get_task(child)
    assert row["creator_chain"] == [AGENTS[0]]
    assert row["parent_task_id"] is None
    assert row["created_by"] == f"agent:{AGENTS[0]}"


async def test_provenance_is_never_taken_from_the_tool_call():
    """The tool carries no provenance parameter, and the seam takes none either
    beyond the creating session: creator, parent and chain come from its row."""
    import inspect

    assert set(inspect.signature(create_task).parameters) == {
        "ctx", "agent_id", "instructions", "title", "await_result"}
    seam = set(inspect.signature(routes.create_and_run_task).parameters)
    assert not seam & {"created_by", "creator_chain", "parent_task_id", "approved_by"}

    session = await _running(AGENTS[0])
    row = await repo.get_task(await _agent_creates(session, AGENTS[0], AGENTS[1]))
    assert row["created_by"] == f"agent:{AGENTS[0]}"
    assert row["creator_chain"] == [AGENTS[0]]
    assert row["parent_task_id"] is None


async def test_a_chain_of_agent_created_tasks_stops_at_the_cap():
    """operator task -> T1 -> T2 -> T3 is allowed; T4 is refused (cap 3)."""
    assert settings.task_max_depth == 3
    task = await _operator_root(AGENTS[0])
    chain: list[str] = []
    for hop in range(1, 4):
        creator = AGENTS[hop - 1]
        session = await _running(creator, task)
        task = await _agent_creates(session, creator, AGENTS[hop])
        chain.append(creator)
        assert (await repo.get_task(task))["creator_chain"] == chain

    before = len(await repo.list_tasks())
    since = await repo.latest_event_id()
    last_session = await _running(AGENTS[3], task)
    with pytest.raises(Refused) as exc:
        await _agent_creates(last_session, AGENTS[3], AGENTS[4])

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

    with pytest.raises(Refused):
        await _agent_creates(session, AGENTS[0], AGENTS[1])

    (refused,) = await _refusals(since)
    assert refused["ref_id"] == session
    assert refused["payload"]["chain"] == [AGENTS[0]]


async def test_the_cap_is_a_setting(monkeypatch):
    monkeypatch.setattr(settings, "task_max_depth", 1)
    root = await _operator_root(AGENTS[0])
    t1 = await _agent_creates(await _running(AGENTS[0], root), AGENTS[0], AGENTS[1])
    with pytest.raises(Refused):
        await _agent_creates(await _running(AGENTS[1], t1), AGENTS[1], AGENTS[2])


async def test_an_agent_created_task_with_no_assignee_is_refused():
    session = await _running(AGENTS[0])
    before = len(await repo.list_tasks())
    since = await repo.latest_event_id()

    with pytest.raises(HTTPException) as exc:
        await routes.create_and_run_task(
            "unrouted", None, None, actor=f"agent:{AGENTS[0]}", background=True,
            parent_session_id=session)

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
    t1 = await _agent_creates(await _running(a, root), a, b)
    before = len(await repo.list_tasks())
    since = await repo.latest_event_id()

    with pytest.raises(Refused) as exc:
        await _agent_creates(await _running(b, t1), b, a)

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

    with pytest.raises(Refused) as exc:
        await _agent_creates(await _running(a, root), a, a)

    assert exc.value.detail.startswith("task refused (self):")
    named = _teammates(exc.value.detail)
    assert b in named and a not in named
    assert len(await repo.list_tasks()) == before
    (refused,) = await _refusals(since)
    assert refused["payload"]["reason"] == "self"
    assert refused["payload"]["chain"] == [a]


async def test_orchestrator_to_orchestrator_is_refused_as_self():
    since = await repo.latest_event_id()
    with pytest.raises(Refused) as exc:
        await _agent_creates(await _running("orchestrator"),
                                   "orchestrator", "orchestrator")
    assert exc.value.detail.startswith("task refused (self):")
    assert "orchestrator" not in _teammates(exc.value.detail)
    (refused,) = await _refusals(since)
    assert refused["payload"]["reason"] == "self"
    assert refused["payload"]["creator"] == "agent:orchestrator"


async def test_the_orchestrator_in_the_chain_is_refused_as_a_cycle():
    a = AGENTS[0]
    t1 = await _agent_creates(await _running("orchestrator"), "orchestrator", a)
    with pytest.raises(Refused) as exc:
        await _agent_creates(await _running(a, t1), a, "orchestrator")
    assert exc.value.detail.startswith("task refused (cycle):")


async def test_b_to_c_both_new_to_the_chain_is_not_refused():
    a, b, c = AGENTS[:3]
    root = await _operator_root(a)
    t1 = await _agent_creates(await _running(a, root), a, b)
    since = await repo.latest_event_id()

    t2 = await _agent_creates(await _running(b, t1), b, c)

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
    t1 = await _agent_creates(await _running(a, root), a, b)  # chain [a]
    asker = await _running(b, t1)
    specialist = await _running(c)
    await repo.save_proposal(
        _uid("prop"), specialist, c, "consulted", [], [], "",
        idempotency_key=_uid("key"),
        origin={"consulted_by": b, "caller_session_id": asker},
    )

    t2 = await _agent_creates(specialist, c, d)

    assert (await repo.get_task(t2))["creator_chain"] == [a, c]

    # ...and the inherited chain is what the cycle check reads.
    specialist2 = await _running(c)
    await repo.save_proposal(
        _uid("prop"), specialist2, c, "consulted", [], [], "",
        idempotency_key=_uid("key"),
        origin={"consulted_by": b, "caller_session_id": asker},
    )
    with pytest.raises(Refused) as exc:
        await _agent_creates(specialist2, c, a)
    assert exc.value.detail.startswith("task refused (cycle):")


async def test_a_specialist_inside_a_depth_two_chain_is_counted_at_depth_three():
    a, b, c, e, d = AGENTS  # d (inbox-triage) only creates, e is taskable
    root = await _operator_root(a)
    t1 = await _agent_creates(await _running(a, root), a, b)
    t2 = await _agent_creates(await _running(b, t1), b, c)  # chain [a, b]
    asker = await _running(c, t2)
    specialist = await _running(d)
    await repo.save_proposal(
        _uid("prop"), specialist, d, "consulted", [], [], "",
        idempotency_key=_uid("key"),
        origin={"consulted_by": c, "caller_session_id": asker},
    )

    t3 = await _agent_creates(specialist, d, e)

    assert (await repo.get_task(t3))["creator_chain"] == [a, b, d]  # depth 3: allowed
    with pytest.raises(Refused) as exc:  # its next hop is depth 4
        await _agent_creates(await _running(e, t3), e, c)
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
        assert await _agent_creates(session, a, b)
    assert await _created_under(root) == 3

    before = len(await repo.list_tasks())
    since = await repo.latest_event_id()
    with pytest.raises(Refused) as exc:
        await _agent_creates(session, a, b)

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


async def test_concurrent_creations_from_one_creator_hit_the_limit_exactly(monkeypatch):
    """The count and the insert are one critical section (`repo.fanout_lock`):
    limit+1 creations fired at once from ONE creating task leave exactly
    `limit` rows and exactly one `task.refused` — an exact bound, where the
    check-then-insert seam used to allow a few too many."""
    import asyncio

    limit = 6
    monkeypatch.setattr(settings, "task_fanout_limit", limit)
    a, b = AGENTS[:2]
    root = await _operator_root(a)
    session = await _running(a, root)
    since = await repo.latest_event_id()

    ctx = SimpleNamespace(deps=TriageDeps(agent_id=a, session_id=session))
    outs = await asyncio.gather(*[
        create_task(ctx, b, f"do thing {i}") for i in range(limit + 1)])

    created = [o for o in outs if not o.startswith(_REFUSED)]
    refused = [o for o in outs if o.startswith(_REFUSED)]
    assert (len(created), len(refused)) == (limit, 1)
    assert await _created_under(root) == limit
    (event,) = await _refusals(since)
    assert event["payload"]["reason"] == "fan-out"
    assert event["payload"]["created"] == limit


async def test_concurrent_creations_from_a_taskless_session_are_counted_exactly(monkeypatch):
    import asyncio

    limit = 4
    monkeypatch.setattr(settings, "task_fanout_limit", limit)
    a, b = AGENTS[:2]
    session = await _running(a)
    ctx = SimpleNamespace(deps=TriageDeps(agent_id=a, session_id=session))
    outs = await asyncio.gather(*[
        create_task(ctx, b, f"do thing {i}") for i in range(limit + 2)])
    assert sum(not o.startswith(_REFUSED) for o in outs) == limit
    assert await repo.count_child_tasks(parent_session_id=session) == limit


async def test_the_fan_out_lock_is_per_creator(monkeypatch):
    """Two creators do not serialise on each other: the key is the fan-out
    reference, so holding one creator's lock never blocks another's."""
    import asyncio

    async with repo.fanout_lock("creator-one"):
        async with asyncio.timeout(5):
            async with repo.fanout_lock("creator-two"):
                pass


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
        assert await _agent_creates(first, a, b)

    second = await _running(a, root)  # the re-run: same task, new session
    assert await _agent_creates(second, a, b)  # the 3rd of 3
    with pytest.raises(Refused) as exc:
        await _agent_creates(second, a, b)
    assert exc.value.detail.startswith("task refused (fan-out):")
    assert await _created_under(root) == 3


async def test_two_tasks_count_separately(monkeypatch):
    monkeypatch.setattr(settings, "task_fanout_limit", 1)
    a, b = AGENTS[:2]
    root1, root2 = await _operator_root(a), await _operator_root(a)
    assert await _agent_creates(await _running(a, root1), a, b)
    assert await _agent_creates(await _running(a, root2), a, b)
    with pytest.raises(Refused):
        await _agent_creates(await _running(a, root1), a, b)


async def test_a_creator_with_no_task_is_counted_by_its_session(monkeypatch):
    monkeypatch.setattr(settings, "task_fanout_limit", 2)
    a, b = AGENTS[:2]
    session = await _running(a)
    for _ in range(2):
        assert await _agent_creates(session, a, b)
    since = await repo.latest_event_id()
    with pytest.raises(Refused) as exc:
        await _agent_creates(session, a, b)
    assert exc.value.detail.startswith("task refused (fan-out):")
    (refused,) = await _refusals(since)
    assert refused["ref_id"] == session
    # Another conversation starts its own count.
    assert await _agent_creates(await _running(a), a, b)


async def test_an_inherited_chain_counts_toward_the_ancestor_task(monkeypatch):
    """A task-less specialist inside a task chain gets the ancestor as its
    parent task, so its creations and the asker's share one count — and the
    refusal hangs on the ancestor task."""
    monkeypatch.setattr(settings, "task_fanout_limit", 2)
    a, b, c, d = AGENTS[:4]
    root = await _operator_root(a)
    t1 = await _agent_creates(await _running(a, root), a, b)
    asker = await _running(b, t1)
    assert await _agent_creates(asker, b, d)  # 1 under t1

    specialist = await _running(c)
    await repo.save_proposal(
        _uid("prop"), specialist, c, "consulted", [], [], "",
        idempotency_key=_uid("key"),
        origin={"consulted_by": b, "caller_session_id": asker},
    )
    t3 = await _agent_creates(specialist, c, d)  # 2 under t1
    row = await repo.get_task(t3)
    assert row["parent_task_id"] == t1
    assert await _created_under(t1) == 2

    since = await repo.latest_event_id()
    with pytest.raises(Refused) as exc:  # the asker's next is the 3rd
        await _agent_creates(asker, b, d)
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
    t1 = await _agent_creates(await _running(a, root), a, b)
    asker = await _running(b, t1)
    mid = _uid("sess")
    await repo.create_running_session(mid, c, parent_session_id=asker)

    t2 = await _agent_creates(mid, c, d)

    row = await repo.get_task(t2)
    assert row["creator_chain"] == [a, c]
    assert row["parent_task_id"] == t1
    with pytest.raises(Refused) as exc:
        await _agent_creates(asker, b, d)  # t1's count is now 1 of 1
    assert exc.value.detail.startswith("task refused (fan-out):")


async def test_a_refusal_is_in_the_activity_feed_and_marks_the_board_card(monkeypatch):
    """Story 5: GET /events?kind=task.refused returns the refusal and the board's
    task payload carries the creator, the approver, the parent and the mark."""
    monkeypatch.setattr(settings, "task_max_depth", 1)
    root = await _operator_root(AGENTS[0])
    first = await _agent_creates(await _running(AGENTS[0], root), AGENTS[0], AGENTS[1])
    since = await repo.latest_event_id()
    with pytest.raises(Refused):
        await _agent_creates(await _running(AGENTS[1], first), AGENTS[1], AGENTS[2])

    feed = await routes.get_events(since=since, kind="task.refused")
    (refused,) = feed["events"]
    assert refused["ref_id"] == first and refused["payload"]["reason"] == "depth"

    board = {t["id"]: t for t in (await routes.list_tasks())["tasks"]}
    assert board[first]["refusals"]["count"] == 1
    assert board[first]["refusals"]["latest_reason"] == "depth"
    assert board[first]["created_by"] == f"agent:{AGENTS[0]}"
    assert board[first]["approved_by"] is None  # created ungated
    assert board[first]["parent_task_id"] == root
    assert board[root]["refusals"] is None  # only the refusing task is marked
    assert board[root]["created_by"] == "operator"

    # A task approved while the gate was on keeps its approver on the board.
    gated = _uid("task")
    await repo.create_task(gated, "approved before ungating", "x", AGENTS[1],
                           created_by=f"agent:{AGENTS[0]}", approved_by="human:lee",
                           parent_task_id=root, creator_chain=[AGENTS[0]])
    board = {t["id"]: t for t in (await routes.list_tasks())["tasks"]}
    assert board[gated]["approved_by"] == "human:lee"


async def test_the_mark_counts_every_refusal_and_names_the_latest_reason(monkeypatch):
    task = await _operator_root(AGENTS[0])
    session = await _running(AGENTS[0], task)
    monkeypatch.setattr(settings, "task_max_depth", 0)
    for assignee in (AGENTS[1], AGENTS[2]):
        with pytest.raises(Refused):
            await _agent_creates(session, AGENTS[0], assignee)
    with pytest.raises(Refused):
        await _agent_creates(session, AGENTS[0], AGENTS[0])  # self

    mark = {t["id"]: t for t in await repo.list_tasks()}[task]["refusals"]
    assert mark["count"] == 3
    assert mark["latest_reason"] == "self"
    assert mark["latest_at"]
