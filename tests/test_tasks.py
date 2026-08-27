"""First-class Tasks (SC-2 groundwork): an unassigned task parks visibly
awaiting the orchestrator (D7 — not built, the slot exists); an assigned task
runs its agent over the SAME durable spine as email work — proposal behind the
gate, task resolved by the gateway when the session resolves.
"""

from __future__ import annotations

import asyncio

import pytest

from central_command.api import routes
from central_command.api.routes import (
    TaskAssignIn,
    TaskCreateIn,
    TaskIn,
    assign_task,
    cancel_task,
    create_task,
    task_agent,
)
from central_command.config import settings
from central_command.db import repo
from central_command.gateway import executor, gateway
from central_command.runtime.spike_model import make_jira_task_model
from tests.conftest import aresolve, needs_pg

pytestmark = needs_pg


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "executor_mode", "dry_run")
    monkeypatch.setattr(routes, "resolve_model", aresolve(lambda *a, **k: make_jira_task_model()))


async def test_unassigned_task_parks_awaiting_orchestrator(monkeypatch):
    # No agent, no run: any model call would be a bug.
    async def explode(*a, **k):
        raise AssertionError("an unassigned task must not run an agent")

    monkeypatch.setattr(routes, "resolve_model", explode)
    since = await repo.latest_event_id()

    out = await create_task(TaskCreateIn(instructions="Audit the TASKS board weekly."))
    assert out == {"deferred": False, "task_id": out["task_id"], "status": "NEW"}

    task = await repo.get_task(out["task_id"])
    assert task["status"] == "NEW" and task["agent_id"] is None
    assert task["session_id"] is None

    created = await repo.list_events(since_id=since, kind="task.created")
    assert [e["ref_id"] for e in created] == [out["task_id"]]


async def test_assigned_task_runs_to_review_and_approval_completes_it():
    since = await repo.latest_event_id()
    out = await create_task(
        TaskCreateIn(instructions="Add a dependency note to TASKS-12.",
                     agent_id="jira-expert")
    )
    assert out["deferred"] is True

    task = await repo.get_task(out["task_id"])
    assert task["status"] == "REVIEW"
    assert task["session_id"] == out["session_id"]
    prop = await repo.load_proposal(out["proposal_id"])
    assert prop["status"] == "AWAITING_HUMAN" and prop["agent_id"] == "jira-expert"

    # started BEFORE review on the append-only log.
    kinds = [e["kind"] for e in await repo.list_events(since_id=since, kind="task")]
    assert kinds == ["task.created", "task.started", "task.review"]

    result = await gateway.approve_and_execute(
        out["session_id"], out["proposal_id"], model=make_jira_task_model()
    )
    assert result["ok"] is True
    task = await repo.get_task(out["task_id"])
    assert task["status"] == "DONE"
    assert task["outcome"] == result["result_text"]
    completed = await repo.list_events(since_id=since, kind="task.completed")
    assert [e["ref_id"] for e in completed] == [out["task_id"]]


async def test_text_answer_completes_the_task_on_the_spot():
    out = await create_task(
        TaskCreateIn(instructions="no change needed — just advice please",
                     agent_id="jira-expert")
    )
    assert out["deferred"] is False
    task = await repo.get_task(out["task_id"])
    assert task["status"] == "DONE"
    assert "No Jira change" in task["outcome"]


async def test_assigning_a_new_task_runs_it():
    parked = await create_task(TaskCreateIn(instructions="Comment on TASKS-12."))
    out = await assign_task(parked["task_id"], TaskAssignIn(agent_id="jira-expert"))
    assert out["deferred"] is True
    task = await repo.get_task(parked["task_id"])
    assert task["status"] == "REVIEW" and task["agent_id"] == "jira-expert"

    # Only NEW tasks are assignable — a running task's assignment is history.
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        await assign_task(parked["task_id"], TaskAssignIn(agent_id="jira-expert"))


async def test_cancel_winds_down_a_task_in_review():
    """Cancel is the WIND-DOWN, not the pause (2026-08-02). It used to be
    NEW-only, which left a task parked in REVIEW behind a proposal the operator
    no longer wanted with nothing but reject-and-hope as a way out. Now it
    withdraws the undecided proposal, closes the session and resolves the task —
    a terminal task is still never re-resolved, and a task with a LIVE run is
    still refused (stop it first; `test_stop_resume.py` holds that end)."""
    from fastapi import HTTPException

    parked = await create_task(TaskCreateIn(instructions="Never mind this one."))
    assert (await cancel_task(parked["task_id"]))["ok"] is True
    assert (await repo.get_task(parked["task_id"]))["status"] == "CANCELLED"
    with pytest.raises(HTTPException):  # already terminal
        await cancel_task(parked["task_id"])

    running = await create_task(
        TaskCreateIn(instructions="Comment on TASKS-12.", agent_id="jira-expert")
    )
    assert (await repo.get_task(running["task_id"]))["status"] == "REVIEW"
    out = await cancel_task(running["task_id"])
    assert out["prior_status"] == "REVIEW"
    assert out["withdrawn_proposals"] == [running["proposal_id"]]
    assert (await repo.get_task(running["task_id"]))["status"] == "CANCELLED"
    assert (await repo.load_proposal(running["proposal_id"]))["status"] == "WITHDRAWN"


async def test_execution_failure_fails_the_task(monkeypatch):
    out = await create_task(
        TaskCreateIn(instructions="Comment on TASKS-12.", agent_id="jira-expert")
    )

    async def boom(actions, approver, source_refs, proposer=None, proposal_id=None):
        raise executor.ExecutionFailed("jira.add_comment", "facade 500", [])

    monkeypatch.setattr(gateway.executor, "execute", boom)
    result = await gateway.approve_and_execute(
        out["session_id"], out["proposal_id"], model=make_jira_task_model()
    )
    assert result["ok"] is False
    task = await repo.get_task(out["task_id"])
    assert task["status"] == "FAILED"
    assert "jira.add_comment" in task["outcome"]


async def _wait_until(pred, timeout=5.0):
    """Poll an async predicate until truthy — background runs land on the
    record, not the response, so tests watch the record."""
    for _ in range(int(timeout / 0.05)):
        result = await pred()
        if result:
            return result
        await asyncio.sleep(0.05)
    raise AssertionError("condition not reached in time")


async def test_background_create_returns_before_the_run_lands():
    since = await repo.latest_event_id()
    out = await create_task(
        TaskCreateIn(instructions="Add a dependency note to TASKS-12.",
                     agent_id="jira-expert", background=True)
    )
    # The POST answered before the run resolved: no proposal/session keys yet.
    assert out["background"] is True and out["deferred"] is None
    assert "proposal_id" not in out

    # The detached run still lands REVIEW on the record via the same path.
    task = await _poll_status(out["task_id"], "REVIEW")
    assert task["session_id"] is not None

    # started BEFORE review on the append-only log, same as the sync path —
    # scoped to THIS task's events (stray background tasks from earlier tests
    # may land their own task.* events in the window).
    #
    # Wait for the EVENT, not the task row: the detached run flips the row to
    # REVIEW and then emits, so polling on status alone can read the log before
    # `task.review` lands and see a short sequence. That is the same defect
    # already root-caused for `test_background_run_failure_fails_the_task` —
    # this sibling was left on the old pattern and failed the same way.
    await _poll_events(out["task_id"], "task.review", since)
    kinds = [
        e["kind"]
        for e in await repo.list_events(since_id=since, kind="task")
        if e["ref_id"] == out["task_id"]
    ]
    assert kinds == ["task.created", "task.started", "task.review"]


async def _poll_status(task_id: str, status: str, timeout=5.0) -> dict:
    return await _wait_until(
        lambda: _task_if_status(task_id, status), timeout=timeout
    )


async def _task_if_status(task_id: str, status: str):
    task = await repo.get_task(task_id)
    return task if task and task["status"] == status else None


async def _poll_events(task_id: str, kind: str, since: int, timeout=5.0) -> list[dict]:
    """Wait for a task's own events, scoped to its ref_id.

    The failure path resolves the task and THEN emits (correctly — a failure
    record authorises nothing), so polling on status alone can win the race and
    read the log before the event lands. That is the documented
    passes-alone/fails-under-load flake; waiting for the event itself removes
    it, and the ref_id scope keeps a concurrent background task from bleeding
    into the assertion.
    """
    async def mine():
        rows = await repo.list_events(since_id=since, kind=kind)
        return [e for e in rows if e["ref_id"] == task_id] or None

    return await _wait_until(mine, timeout=timeout)


async def test_background_run_failure_fails_the_task(monkeypatch):
    async def explode(*a, **k):
        raise RuntimeError("model resolution exploded")

    monkeypatch.setattr(routes, "resolve_model", explode)
    since = await repo.latest_event_id()
    out = await create_task(
        TaskCreateIn(instructions="Comment on TASKS-12.",
                     agent_id="jira-expert", background=True)
    )
    assert out["background"] is True

    task = await _poll_status(out["task_id"], "FAILED")
    assert "model resolution exploded" in task["outcome"]
    failed = await _poll_events(out["task_id"], "task.run_failed", since)
    assert len(failed) == 1


async def test_workspace_tasking_is_a_first_class_task_now():
    """POST /agents/{id}/task is sugar for an assigned task — same record,
    same board visibility, response keys unchanged for the Workspace box."""
    out = await task_agent("jira-expert", TaskIn(instruction="Comment on TASKS-12."))
    assert out["deferred"] is True and "intent" in out
    assert (await repo.get_task(out["task_id"]))["status"] == "REVIEW"

    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        await task_agent("inbox-triage", TaskIn(instruction="do something"))


async def test_list_tasks_filters_by_agent(monkeypatch):
    """The workspace Tasks panel scopes the board to its selected agent, so
    `/api/tasks?agent_id=` must filter — asserted on rows this test created,
    never on global counts (the dev DB is shared)."""
    async def explode(*a, **k):
        raise AssertionError("these tasks must not run an agent")

    monkeypatch.setattr(routes, "resolve_model", explode)

    mine = await create_task(TaskCreateIn(instructions="Scoped to jira-expert."))
    theirs = await create_task(TaskCreateIn(instructions="Scoped to the coach."))
    await repo.assign_task(mine["task_id"], "jira-expert")
    await repo.assign_task(theirs["task_id"], "coach")

    ids = {t["id"] for t in (await routes.list_tasks(agent_id="jira-expert"))["tasks"]}
    assert mine["task_id"] in ids
    assert theirs["task_id"] not in ids

    # Unfiltered stays unfiltered — the full board must not inherit the scope.
    all_ids = {t["id"] for t in (await routes.list_tasks())["tasks"]}
    assert {mine["task_id"], theirs["task_id"]} <= all_ids
