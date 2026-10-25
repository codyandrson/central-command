"""await-result: a creator waiting on the task it created (doctrine Decision 5,
2026-08-22).

It began as `task.create`'s `await_result=True`: the proposer parked on the task
the operator approved instead of being resumed the instant it existed — the
"wait for the teammate's answer" shape consult-and-wait gave specialists,
pointed at a created task rather than a specialist's session.

Since story 4.7 task creation is the direct `create_task` trigger and there is
no proposal to approve, so the half that SET the wait (the Executor's handler
and the gateway's approve tail) went with the gate. Story 4.8 gives the wait a
non-gateway entry: `create_task(await_result=True)` ends the run with a
deferral, and the driver parks the session through
`consult.park_task_wait_deferred`. The half that ENDS the wait — `task_wait` in
`run_state`, `resume_task_wait`, the sweep — is unchanged and pinned below on a
session parked the way `consult.park_task_wait` parks it.

Mirrors `tests/test_consult_wait.py` (the sibling feature).
"""

from __future__ import annotations

import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

import uuid
from types import SimpleNamespace

from central_command.api import orchestration, routes
from central_command.config import settings
from central_command.db import repo
from central_command.runtime.consult import park_task_wait
from central_command.runtime.deps import TriageDeps
from central_command.runtime.run import ingest_and_propose
from central_command.runtime.tools import create_task
from tests.conftest import needs_pg

pytestmark = needs_pg


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "dispatch_approval_limit", 10_000)


@pytest.fixture(autouse=True)
def no_detached_runs(monkeypatch):
    """The created child task must never actually run in this suite — same
    guard `test_task_propose.py` uses, for the same reason."""
    from central_command.api import routes

    async def noop(task, actor, **_kw):
        return None

    monkeypatch.setattr(routes, "_run_assigned_task_detached", noop)


def _proposal() -> dict:
    """A gated draft the triage agent holds: the run pauses at this call, which
    is where a creator waiting on its task sits."""
    return {
        "intent": "comment on TASKS-9 once jira-expert has checked its due date",
        "actions": [{
            "capability": "jira.add_comment",
            "arguments": {"issue_key": "TASKS-9", "body": "Due date checked."},
            "target_ref": {"system": "jira", "id": "TASKS-9", "read_version": "unknown"},
            "reversibility": "reversible",
        }],
        "evidence": [{
            "kind": "email", "source_ref": "gmail:msg_1",
            "locator": "email.get_message(msg_1)",
            "claim": "Sender asks whether TASKS-9 has a due date.",
        }],
        "expected_effect": "TASKS-9 carries the answer",
    }


def _make_task_wait_model() -> FunctionModel:
    """First call pauses on `propose_action`; once resumed with that call's
    result (here: the distilled task-wait outcome), closes out in text quoting
    it — so a test can assert on what the agent was actually told."""
    proposal = _proposal()

    def _respond(messages, info: AgentInfo) -> ModelResponse:
        for m in messages:
            for p in getattr(m, "parts", []):
                if isinstance(p, ToolReturnPart) and p.tool_name == "propose_action":
                    return ModelResponse(parts=[TextPart(content=f"Closed — {p.content}")])
        return ModelResponse(
            parts=[ToolCallPart(tool_name="propose_action", args={"proposal": proposal})]
        )

    return FunctionModel(_respond)


async def _parked_on_a_task(model):
    """A session paused at its call and parked on a task, the way
    `park_task_wait` parks one: AWAITING_AGENTS, `run_state.task_wait` keyed on
    the task id and the paused call."""
    run = await ingest_and_propose(
        "Please have jira-expert check TASKS-9 and tell me its due date.",
        model=model,
    )
    task_id = (await routes.create_and_run_task(
        "Check TASKS-9 for a due date.", None, "jira-expert", background=True,
    ))["task_id"]
    run_state = await repo.load_paused_session(run["session_id"])
    await park_task_wait(
        session_id=run["session_id"], agent_id="inbox-triage",
        tool_call_id=run_state["tool_call_id"], task_id=task_id,
    )
    return run, task_id


# --- story 8: the creator asks to wait ---------------------------------------


def _creating_model(assignee: str = "jira-expert", **extra) -> FunctionModel:
    """Calls `create_task` once; once resumed with that call's result, closes
    out in text quoting it, so a test can assert what the agent was told."""
    args = {"agent_id": assignee, "instructions": "Check TASKS-9 for a due date.",
            **extra}

    def _respond(messages, info: AgentInfo) -> ModelResponse:
        for m in messages:
            for p in getattr(m, "parts", []):
                if isinstance(p, ToolReturnPart) and p.tool_name == "create_task":
                    return ModelResponse(parts=[TextPart(content=f"Closed — {p.content}")])
        return ModelResponse(parts=[ToolCallPart(tool_name="create_task", args=args)])

    return FunctionModel(_respond)


async def _child_of(session_id: str) -> dict:
    (child,) = [t for t in await repo.list_tasks()
                if t.get("parent_session_id") == session_id]
    return child


async def test_await_result_parks_the_creator_on_its_task_and_resumes_with_the_outcome():
    model = _creating_model(await_result=True)
    run = await ingest_and_propose("Please have jira-expert check TASKS-9.", model=model)
    sid = run["session_id"]

    child = await _child_of(sid)
    assert run.get("task_waiting") is True
    assert await repo.session_status(sid) == "AWAITING_AGENTS"
    rs = await repo.get_session_run_state(sid)
    assert rs["task_wait"]["task_id"] == child["id"]
    assert rs["task_wait"]["proposal_id"] is None
    assert rs["messages"]  # a fresh process can resume it
    assert "agent.task_waiting" in [e["kind"] for e in await repo.list_events(ref_id=sid)]
    assert "session.completed" not in [e["kind"] for e in await repo.list_events(ref_id=sid)]
    # No proposal exists for the creation; the child is the creator's own task.
    assert await orchestration.resume_task_wait(sid, model=model) is None  # still running

    await repo.resolve_task(child["id"], "DONE", outcome="TASKS-9 is due 2026-08-30.")
    out = await orchestration.resume_task_wait(sid, model=model)

    assert out["resumed"] is True
    assert await repo.session_status(sid) == "DONE"
    assert "TASKS-9 is due 2026-08-30" in out["final_output"]


async def test_the_wake_hook_resumes_the_creator_when_the_child_goes_terminal():
    """The task going terminal is what wakes the creator (`child_resolved`
    dispatches on `task_wait`), with no sweep involved."""
    model = _creating_model(await_result=True)
    run = await ingest_and_propose("Please have jira-expert check TASKS-9.", model=model)
    sid = run["session_id"]
    child = await _child_of(sid)

    await repo.resolve_task(child["id"], "DONE", outcome="done")
    await orchestration.child_resolved(await repo.get_task(child["id"]))

    # Resumed (the hook builds its own demo model, which proposes again):
    # whatever it ended on, it is no longer parked on the task.
    assert await repo.session_status(sid) != "AWAITING_AGENTS"
    assert "agent.task_resumed" in [e["kind"] for e in await repo.list_events(ref_id=sid)]


async def test_a_child_that_finished_before_the_park_still_wakes_the_creator(monkeypatch):
    """The child runs detached while the creator's turn is still ending: a
    terminal task at park time is woken by the park itself."""
    import asyncio

    model = _creating_model(await_result=True)
    real_create = routes.create_and_run_task

    async def finishing(*a, **kw):
        out = await real_create(*a, **kw)
        await repo.resolve_task(out["task_id"], "FAILED", outcome="died at once")
        return out

    monkeypatch.setattr(routes, "create_and_run_task", finishing)
    run = await ingest_and_propose("Please have jira-expert check TASKS-9.", model=model)
    for _ in range(100):
        if "agent.task_resumed" in [
                e["kind"] for e in await repo.list_events(ref_id=run["session_id"])]:
            break
        await asyncio.sleep(0.05)
    assert "agent.task_resumed" in [
        e["kind"] for e in await repo.list_events(ref_id=run["session_id"])]


async def test_without_await_result_the_creator_carries_on():
    model = _creating_model()
    run = await ingest_and_propose("Please have jira-expert check TASKS-9.", model=model)
    assert run["deferred"] is False and not run.get("task_waiting")
    assert "created and assigned to jira-expert" in run["output"]
    assert await repo.session_status(run["session_id"]) == "DONE"


async def test_a_refused_creation_cannot_be_waited_on():
    """The wait on a task whose chain cycles back to the waiter cannot happen:
    the cycle is refused at creation, so no task exists and nothing parks."""
    model = _creating_model(assignee="inbox-triage", await_result=True)
    run = await ingest_and_propose("Please have yourself check TASKS-9.", model=model)
    assert not run.get("task_waiting")
    assert "Task NOT created" in run["output"]
    assert await repo.session_status(run["session_id"]) == "DONE"

    # A real cycle: A (jira-expert) created B's task; B asks A, waiting.
    root = await routes.create_and_run_task("root", None, "jira-expert")
    a_sess = f"sess_wait_{uuid.uuid4().hex[:8]}"
    await repo.create_running_session(a_sess, "jira-expert")
    await repo.start_task(root["task_id"], a_sess)
    child = await routes.create_and_run_task(
        "x", None, "litellm-manager", actor="agent:jira-expert",
        background=True, parent_session_id=a_sess)
    b_sess = f"sess_wait_{uuid.uuid4().hex[:8]}"
    await repo.create_running_session(b_sess, "litellm-manager")
    await repo.start_task(child["task_id"], b_sess)
    ctx = SimpleNamespace(deps=TriageDeps(agent_id="litellm-manager", session_id=b_sess))
    out = await create_task(ctx, "jira-expert", "loop back", await_result=True)
    assert out.startswith("Task NOT created — task refused (cycle)")


async def test_await_result_is_ignored_for_a_consulted_specialist():
    """A specialist answering a consult cannot also wait: the consultation
    already ends its turn."""
    session = f"sess_wait_{uuid.uuid4().hex[:8]}"
    await repo.create_running_session(session, "jira-expert")
    ctx = SimpleNamespace(deps=TriageDeps(
        agent_id="jira-expert", session_id=session, consult_chain=("inbox-triage",)))
    out = await create_task(ctx, "litellm-manager", "Check spend.", await_result=True)
    assert "await_result ignored" in out


def test_an_advisory_consult_never_holds_the_tool():
    """The structural half: a consulted specialist's sub-run is built from
    `advisory_packs`, which drops the pack holding `create_task`."""
    from central_command.runtime import packs

    kept = packs.advisory_packs(("task-create", "consult", "graph-read"))
    assert "task-create" not in kept
    toolset = packs.toolset_for(("task-create",))
    tool = toolset.tools["create_task"]
    assert tool.sequential is True


async def test_a_creator_resumed_with_one_outcome_can_wait_on_another():
    """A chain of waits: the resumed leg creates a second task with
    await_result and re-parks on it instead of closing the session."""
    calls = {"n": 0}

    def _respond(messages, info: AgentInfo) -> ModelResponse:
        returns = [p for m in messages for p in getattr(m, "parts", [])
                   if isinstance(p, ToolReturnPart) and p.tool_name == "create_task"]
        if len(returns) >= 2:
            return ModelResponse(parts=[TextPart(content="Both done.")])
        calls["n"] += 1
        return ModelResponse(parts=[ToolCallPart(tool_name="create_task", args={
            "agent_id": "jira-expert", "instructions": f"step {calls['n']}",
            "await_result": True})])

    model = FunctionModel(_respond)
    run = await ingest_and_propose("Do two things in order.", model=model)
    sid = run["session_id"]
    first = (await repo.get_session_run_state(sid))["task_wait"]["task_id"]

    await repo.resolve_task(first, "DONE", outcome="one")
    out = await orchestration.resume_task_wait(sid, model=model)
    assert out.get("task_waiting") is True
    assert await repo.session_status(sid) == "AWAITING_AGENTS"
    second = (await repo.get_session_run_state(sid))["task_wait"]["task_id"]
    assert second != first

    await repo.resolve_task(second, "DONE", outcome="two")
    out = await orchestration.resume_task_wait(sid, model=model)
    assert await repo.session_status(sid) == "DONE"
    assert out["final_output"] == "Both done."


async def test_a_task_run_that_waits_holds_its_task_in_review():
    """The routes tail: a task whose agent parks on a created task is held
    (REVIEW, session linked), neither resolved nor failed."""
    root = (await routes.create_and_run_task("root", None, "jira-expert"))["task_id"]
    from central_command.runtime.run import run_task

    result = await run_task("jira-expert", "Wait on litellm-manager.",
                            model=_creating_model(assignee="litellm-manager",
                                                  await_result=True),
                            task_id=root)
    assert result.get("task_waiting") is True
    assert (await repo.get_task(root))["status"] != "FAILED"


# --- the wait itself: woken when the task goes terminal ------------------------


async def test_a_parked_creator_is_awaiting_agents_on_its_task():
    run, task_id = await _parked_on_a_task(_make_task_wait_model())

    assert await repo.session_status(run["session_id"]) == "AWAITING_AGENTS"
    rs = await repo.get_session_run_state(run["session_id"])
    assert rs["task_wait"]["task_id"] == task_id
    kinds = [e["kind"] for e in await repo.list_events(ref_id=run["session_id"])]
    assert "agent.task_waiting" in kinds
    assert "session.completed" not in kinds


async def test_task_completion_resumes_the_waiter_with_the_distilled_outcome():
    model = _make_task_wait_model()
    run, task_id = await _parked_on_a_task(model)

    await repo.resolve_task(task_id, "DONE", outcome="TASKS-9 is due 2026-08-30.")

    out = await orchestration.resume_task_wait(run["session_id"], model=model)

    assert out["resumed"] is True
    assert await repo.session_status(run["session_id"]) == "DONE"
    assert "TASKS-9 is due 2026-08-30" in out["final_output"]
    assert str(task_id) in out["final_output"] or "DONE" in out["final_output"]


async def test_resume_task_wait_keeps_waiting_while_the_task_is_still_running():
    model = _make_task_wait_model()
    run, _ = await _parked_on_a_task(model)

    out = await orchestration.resume_task_wait(run["session_id"], model=model)
    assert out is None
    assert await repo.session_status(run["session_id"]) == "AWAITING_AGENTS"


async def test_a_task_create_draft_is_refused_in_run_as_no_capability():
    """There is nothing left to reject: a model that still drafts a gated
    `task.create` is handed it back in-run as an invented capability, so no
    such proposal ever reaches the inbox."""
    from pydantic_ai import ModelRetry

    from central_command.contract.models import Proposal
    from central_command.runtime.tools import _validate_proposal

    draft = _proposal()
    draft["actions"][0].update(capability="task.create", arguments={
        "agent_id": "jira-expert", "instructions": "Check TASKS-9."})
    ctx = SimpleNamespace(deps=TriageDeps(agent_id="inbox-triage", session_id=None))
    with pytest.raises(ModelRetry, match="not a real capability"):
        await _validate_proposal(ctx, Proposal.model_validate(draft))


# --- restart resilience: the sweep -----------------------------------------


def test_resume_sweep_dispatches_task_waits():
    import inspect

    src = inspect.getsource(orchestration.resume_sweep)
    assert "task_wait" in src and "resume_task_wait" in src


async def test_sweep_wakes_a_waiter_whose_task_went_terminal_offline():
    """The orphan sweep lands a dead task's run FAILED with no idea a waiter
    exists; `resume_sweep`'s AWAITING_AGENTS worklist is what actually wakes
    it, on the very next sweep — durable, no polling loop."""
    model = _make_task_wait_model()
    run, task_id = await _parked_on_a_task(model)

    # Simulate the orphan sweep landing the child task's own dead run FAILED —
    # exactly what `repo.fail_stale_running_sessions` does, without needing a
    # second live session to go stale in this test.
    await repo.resolve_task(task_id, "FAILED", outcome="run died with its process")

    swept = await orchestration.resume_sweep()

    assert run["session_id"] in swept["resumed"]
    assert await repo.session_status(run["session_id"]) == "DONE"
