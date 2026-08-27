"""The per-agent task-run queue (operator decision, 2026-08-21): a large
backlog on the board drains incrementally per agent, never all at once.
Scope is ASSIGNED-TASK RUNS ONLY — chats, consultations, resumes, coach runs
and orchestrator recommendations are untouched.

Two pieces:
  1. `routes._agent_task_lock` — one asyncio.Lock per (running loop, agent_id),
     held for the whole run, acquired at the top of `_run_assigned_task_inner`
     so every entry (sync create, detached, assign-and-run, the retry driver)
     gets it for free.
  2. `routes.startup_task_sweep` — a STARTUP-ONLY relaunch of tasks that were
     queued (plain ASSIGNED, no retry_state) but never reached `task.started`
     before the process died.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from central_command.api import orchestration, routes
from central_command.db import repo
from tests.conftest import needs_pg


# --- serialization, stubbed run body (no DB, no model) -----------------------


@pytest.fixture()
def recorder(monkeypatch):
    """Stub out the actual run body (`_run_one_assigned_task`) with something
    that records enter/exit order and can be made to yield the event loop, so
    two concurrent calls for the SAME agent can only interleave if the lock
    fails to serialize them."""
    events: list[tuple[str, str]] = []

    async def fake_run_one(task, actor="operator", *, model_name=None, thinking=None):
        events.append(("enter", task["id"]))
        await asyncio.sleep(0)  # yield — lets a racing task jump in if unguarded
        await asyncio.sleep(0)
        events.append(("exit", task["id"]))
        return {"task_id": task["id"]}

    monkeypatch.setattr(routes, "_run_one_assigned_task", fake_run_one)
    return events


def _task(agent_id: str, n: int) -> dict:
    return {"id": f"task_q_{agent_id}_{n}", "agent_id": agent_id, "title": "t"}


async def test_same_agent_task_runs_serialize(recorder):
    t1, t2 = _task("jira-expert", 1), _task("jira-expert", 2)
    await asyncio.gather(
        routes._run_assigned_task_inner(t1),
        routes._run_assigned_task_inner(t2),
    )
    # One task's exit must precede the other's enter — no interleaving.
    order = recorder
    # The first "enter" for the SECOND task id must come after the FIRST
    # task's "exit" — i.e. the two runs never overlap.
    ids_in_order = [e[1] for e in order]
    first_task_id = ids_in_order[0]
    other_id = t2["id"] if first_task_id == t1["id"] else t1["id"]
    exit_index_of_first = order.index(("exit", first_task_id))
    enter_index_of_other = order.index(("enter", other_id))
    assert enter_index_of_other > exit_index_of_first


async def test_different_agents_run_concurrently(recorder):
    t1, t2 = _task("jira-expert", 1), _task("litellm-manager", 1)
    await asyncio.gather(
        routes._run_assigned_task_inner(t1),
        routes._run_assigned_task_inner(t2),
    )
    order = recorder
    # Both enters happen before either exit — they overlapped.
    enters = [i for i, e in enumerate(order) if e[0] == "enter"]
    first_exit = min(i for i, e in enumerate(order) if e[0] == "exit")
    assert max(enters) < first_exit


# --- retry path serializes too (needs a real task row + claim) ---------------


@pytest.fixture()
async def _cleanup_tasks():
    made: list[str] = []
    yield made
    if made:
        conn = await repo._conn()
        try:
            await conn.execute("delete from task where id = any($1::text[])", made)
        finally:
            await conn.close()


@needs_pg
async def test_retry_path_serializes_on_the_same_lock(recorder, _cleanup_tasks):
    agent_id = "jira-expert"
    ids = []
    for i in range(2):
        task_id = "task_q_retry_" + uuid.uuid4().hex[:8]
        await repo.create_task(task_id, "queue test", "x", agent_id)
        await repo.park_task_retry(task_id, {"kind": "run", "attempts": 1})
        ids.append(task_id)
        _cleanup_tasks.append(task_id)

    # retry_parked_task claims (ASSIGNED+retry_state -> IN_PROGRESS) then
    # calls `_run_assigned_task_inner` directly — the same lock this stub
    # exercises above.
    await asyncio.gather(
        orchestration.retry_parked_task(ids[0]),
        orchestration.retry_parked_task(ids[1]),
    )
    order = recorder
    exit_index_of_first = order.index(("exit", order[0][1]))
    other_id = ids[1] if order[0][1] == ids[0] else ids[0]
    enter_index_of_other = order.index(("enter", other_id))
    assert enter_index_of_other > exit_index_of_first


# --- the lock registry is reached from exactly one call site -----------------


def test_lock_registry_is_touched_only_by_the_task_run_entry():
    """Cheapest honest guard against scope creep: `_agent_task_lock(` must
    appear in `routes.py` exactly where `_run_assigned_task_inner` acquires
    it — never from a chat/consult/resume/coach/orchestrator-recommendation
    path. A source walk, not a runtime check: those paths don't share a
    single choke point the way task runs do (`_run_assigned_task_inner`),
    so the only reliable assertion is that nothing else in the file calls it."""
    import inspect

    src = inspect.getsource(routes)
    assert src.count("_agent_task_lock(") == 2  # the def, and the one call site
    call_sites = [
        line for line in src.splitlines()
        if "_agent_task_lock(" in line and "def _agent_task_lock" not in line
    ]
    assert len(call_sites) == 1
    assert "async with _agent_task_lock(task[\"agent_id\"]):" in call_sites[0]


# --- startup sweep ------------------------------------------------------------


@needs_pg
async def test_startup_sweep_relaunches_plain_assigned_in_created_order(monkeypatch, _cleanup_tasks):
    agent_id = "jira-expert"
    plain_ids = []
    for i in range(3):
        task_id = "task_q_startup_" + uuid.uuid4().hex[:8]
        await repo.create_task(task_id, "startup sweep test", "x", agent_id)
        plain_ids.append(task_id)
        _cleanup_tasks.append(task_id)

    # Force a deterministic created_at order (clock resolution can't be
    # trusted for three inserts in the same test).
    conn = await repo._conn()
    try:
        for i, task_id in enumerate(plain_ids):
            await conn.execute(
                "update task set created_at = now() - ($2 || ' seconds')::interval where id = $1",
                task_id, str(10 - i),
            )
    finally:
        await conn.close()

    # A retry-parked task and a NEW task must NOT be picked up.
    retry_id = "task_q_startup_retry_" + uuid.uuid4().hex[:8]
    await repo.create_task(retry_id, "retry, not startup", "x", agent_id)
    await repo.park_task_retry(retry_id, {"kind": "run", "attempts": 1})
    _cleanup_tasks.append(retry_id)

    new_id = "task_q_startup_new_" + uuid.uuid4().hex[:8]
    await repo.create_task(new_id, "unassigned, not startup", "x", None)
    _cleanup_tasks.append(new_id)

    orphaned = await repo.tasks_orphaned_at_startup()
    orphaned_ids = [t["id"] for t in orphaned if t["id"] in plain_ids]
    assert orphaned_ids == plain_ids  # oldest first, per the update above
    assert retry_id not in [t["id"] for t in orphaned]
    assert new_id not in [t["id"] for t in orphaned]

    launched = []

    async def fake_detached(task, actor, **kw):
        launched.append(task["id"])

    monkeypatch.setattr(routes, "_run_assigned_task_detached", fake_detached)
    await routes.startup_task_sweep()
    # `startup_task_sweep` fires `asyncio.create_task` for each row (a
    # background relaunch, same shape as every other detached launch in this
    # codebase) — yield the loop so the scheduled stubs actually run before
    # asserting on what they recorded.
    for _ in range(10):
        await asyncio.sleep(0)

    launched_plain = [t for t in launched if t in plain_ids]
    assert launched_plain == plain_ids
    assert retry_id not in launched
    assert new_id not in launched
