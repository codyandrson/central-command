"""await-result: a creator waiting on the task it created (doctrine Decision 5,
2026-08-22).

It began as `task.create`'s `await_result=True`: the proposer parked on the task
the operator approved instead of being resumed the instant it existed — the
"wait for the teammate's answer" shape consult-and-wait gave specialists,
pointed at a created task rather than a specialist's session.

Since story 4.7 task creation is the direct `create_task` trigger and there is
no proposal to approve, so the half that SET the wait (the Executor's handler
and the gateway's approve tail) went with the gate. The flag on the new tool is
story 8; its tests are below as strict xfails, so they turn red the moment it
lands and must be unmarked then. The half that ENDS the wait — `task_wait` in
`run_state`, `resume_task_wait`, the sweep — still stands, and is pinned here
on a session parked the way `consult.park_task_wait` parks it.

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


_STORY_8 = pytest.mark.xfail(
    strict=True, raises=TypeError,
    reason="await_result on the direct create_task tool is story 8 of "
           "epic-task-backstops; until then the tool takes no such argument",
)


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


# --- story 8: the creator asks to wait (pending) -------------------------------


@_STORY_8
async def test_await_result_parks_the_creator_on_its_task():
    session = f"sess_wait_{uuid.uuid4().hex[:8]}"
    await repo.create_running_session(session, "inbox-triage")
    ctx = SimpleNamespace(deps=TriageDeps(agent_id="inbox-triage", session_id=session))
    await create_task(ctx, "jira-expert", "Check TASKS-9 for a due date.",
                      await_result=True)
    rs = await repo.get_session_run_state(session)
    assert "task_wait" in rs


@_STORY_8
async def test_await_result_is_ignored_for_a_consulted_specialist():
    """A specialist answering a consult cannot also wait: the consultation
    already ends its turn (the gated path ignored the flag with a note)."""
    session = f"sess_wait_{uuid.uuid4().hex[:8]}"
    await repo.create_running_session(session, "jira-expert")
    ctx = SimpleNamespace(deps=TriageDeps(
        agent_id="jira-expert", session_id=session, consult_chain=("inbox-triage",)))
    out = await create_task(ctx, "litellm-manager", "Check spend.", await_result=True)
    assert "await_result ignored" in out


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
