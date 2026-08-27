"""await-result task proposals (doctrine Decision 5, 2026-08-22).

`task.create`'s `await_result=True` lets the proposer park on the task it just
had approved, instead of being resumed the instant the task exists — the same
"wait for the teammate's answer" shape consult-and-wait gave specialists,
pointed at a created task rather than a specialist's session.

Mirrors `tests/test_consult_wait.py` (the sibling feature) and
`tests/test_task_propose.py` (the capability this extends).
"""

from __future__ import annotations

import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from central_command.api import orchestration
from central_command.config import settings
from central_command.contract.models import Action, Reversibility
from central_command.db import repo
from central_command.gateway import executor, gateway
from central_command.runtime.run import ingest_and_propose
from tests.conftest import needs_pg

pytestmark = needs_pg


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "dispatch_approval_limit", 10_000)
    # The whole feature only exists in the LIVE executor path — `dry_run` logs
    # `task.create` instead of running it, so no task and no `task_wait` ever
    # exist and every assertion here is vacuous. Pinned for the file rather
    # than per-test: the ambient `CC_EXECUTOR_MODE` in `.env` is dry_run, and
    # the four gateway-path tests inherited it (green only on a live-mode box).
    monkeypatch.setattr(settings, "executor_mode", "live")


@pytest.fixture(autouse=True)
def no_detached_runs(monkeypatch):
    """The created child task must never actually run in this suite — same
    guard `test_task_propose.py` uses, for the same reason."""
    from central_command.api import routes

    async def noop(task, actor, **_kw):
        return None

    monkeypatch.setattr(routes, "_run_assigned_task_detached", noop)


def _action(agent_id="jira-expert", await_result=True) -> Action:
    args = {"agent_id": agent_id, "instructions": "Check TASKS-9 for a due date."}
    if await_result is not None:
        args["await_result"] = await_result
    return Action(
        capability="task.create",
        arguments=args,
        target_ref={"system": "central_command", "id": agent_id, "read_version": "unknown"},
        reversibility=Reversibility.reversible,
    )


def _proposal(agent_id="jira-expert", await_result=True) -> dict:
    return {
        "intent": f"ask {agent_id} to check TASKS-9 and wait for the answer",
        "actions": [{
            "capability": "task.create",
            "arguments": {
                "agent_id": agent_id,
                "instructions": "Check TASKS-9 for a due date.",
                "await_result": await_result,
            },
            "target_ref": {"system": "central_command", "id": agent_id, "read_version": "unknown"},
            "reversibility": "reversible",
        }],
        "evidence": [{
            "kind": "email", "source_ref": "gmail:msg_1",
            "locator": "email.get_message(msg_1)",
            "claim": "Sender asks whether TASKS-9 has a due date.",
        }],
        "expected_effect": f"{agent_id} reports TASKS-9's due date",
    }


def _make_task_wait_model(agent_id="jira-expert", await_result=True) -> FunctionModel:
    """First call proposes `task.create`; once resumed with the propose call's
    result (approval outcome, or the distilled task-wait outcome), closes out
    in text quoting it — so a test can assert on what the agent was actually
    told."""
    proposal = _proposal(agent_id, await_result)

    def _respond(messages, info: AgentInfo) -> ModelResponse:
        for m in messages:
            for p in getattr(m, "parts", []):
                if isinstance(p, ToolReturnPart) and p.tool_name == "propose_action":
                    return ModelResponse(parts=[TextPart(content=f"Closed — {p.content}")])
        return ModelResponse(
            parts=[ToolCallPart(tool_name="propose_action", args={"proposal": proposal})]
        )

    return FunctionModel(_respond)


def _make_redraft_refusing_model(agent_id="jira-expert") -> FunctionModel:
    """Proposes once, then closes out in text on ANY resume — used to prove
    the reject path is unaffected (no redraft, no task ever created)."""
    proposal = _proposal(agent_id, await_result=True)

    def _respond(messages, info: AgentInfo) -> ModelResponse:
        proposed = any(
            isinstance(p, ToolCallPart) and p.tool_name == "propose_action"
            for m in messages for p in getattr(m, "parts", [])
        )
        if not proposed:
            return ModelResponse(
                parts=[ToolCallPart(tool_name="propose_action", args={"proposal": proposal})]
            )
        return ModelResponse(parts=[TextPart(content="Understood, closing out.")])

    return FunctionModel(_respond)


# --- the Executor: setting (and ignoring) the wait -----------------------------


async def test_await_result_sets_task_wait_on_the_outcome(monkeypatch):
    monkeypatch.setattr(settings, "executor_mode", "live")
    outcome = await executor.execute(
        [_action(await_result=True)], approver="human:lee", source_refs=[],
    )
    assert outcome.task_wait
    task = await repo.get_task(outcome.task_wait["task_id"])
    assert task["agent_id"] == "jira-expert"


async def test_without_the_flag_behaves_exactly_as_before(monkeypatch):
    monkeypatch.setattr(settings, "executor_mode", "live")
    outcome = await executor.execute(
        [_action(await_result=None)], approver="human:lee", source_refs=[],
    )
    assert outcome.task_wait is None
    assert "created and assigned to jira-expert" in outcome.result_text


async def test_await_result_is_ignored_on_a_consult_drafted_proposal(monkeypatch):
    """The acceptable simplification the design calls out: a task.create drafted
    DURING a consultation cannot also park that same turn — the consultation
    already ends on this decision."""
    import uuid

    monkeypatch.setattr(settings, "executor_mode", "live")
    suffix = uuid.uuid4().hex[:8]
    sid, pid = f"sess_specialist_{suffix}", f"prop_specialist_{suffix}"
    await repo.upsert_agent("jira-expert", "jira-expert", "demo", "test charter")
    await repo.save_paused_session(sid, "jira-expert", {})
    await repo.save_proposal(
        proposal_id=pid, session_id=sid, agent_id="jira-expert",
        intent="drafted while consulted", actions=[], evidence=[],
        expected_effect="", idempotency_key=f"{pid}:0",
        origin={"consulted_by": "inbox-triage", "caller_session_id": "sess_asker"},
    )

    outcome = await executor.execute(
        [_action(await_result=True)], approver="human:lee", source_refs=[],
        proposer="jira-expert", proposal_id=pid,
    )
    assert outcome.task_wait is None
    assert "await_result ignored" in outcome.result_text


# --- the gateway: parking the proposer, and waking it back up -----------------


async def _propose_and_approve(model):
    run = await ingest_and_propose(
        "Please have jira-expert check TASKS-9 and tell me its due date.",
        model=model,
    )
    out = await gateway.approve_and_execute(
        run["session_id"], run["proposal_id"], model=model,
    )
    return run, out


async def test_approve_with_await_result_parks_the_proposer_not_resumes_it():
    model = _make_task_wait_model()
    run, out = await _propose_and_approve(model)

    assert out["task_waiting"] is True
    assert await repo.session_status(run["session_id"]) == "AWAITING_AGENTS"
    rs = await repo.get_session_run_state(run["session_id"])
    assert "task_wait" in rs
    assert rs["task_wait"]["proposal_id"] == run["proposal_id"]

    task = await repo.get_task(rs["task_wait"]["task_id"])
    assert task["agent_id"] == "jira-expert"
    assert task["status"] == "ASSIGNED"

    # The proposal itself is EXECUTED — the decision is not what is deferred.
    assert (await repo.load_proposal(run["proposal_id"]))["status"] == "EXECUTED"
    # And the session did NOT get a final closing turn yet.
    kinds = [e["kind"] for e in await repo.list_events(ref_id=run["session_id"])]
    assert "session.completed" not in kinds
    assert "agent.task_waiting" in kinds


async def test_task_completion_resumes_the_waiter_with_the_distilled_outcome():
    model = _make_task_wait_model()
    run, _ = await _propose_and_approve(model)
    rs = await repo.get_session_run_state(run["session_id"])
    task_id = rs["task_wait"]["task_id"]

    await repo.resolve_task(task_id, "DONE", outcome="TASKS-9 is due 2026-08-30.")

    out = await orchestration.resume_task_wait(run["session_id"], model=model)

    assert out["resumed"] is True
    assert await repo.session_status(run["session_id"]) == "DONE"
    assert "TASKS-9 is due 2026-08-30" in out["final_output"]
    assert str(task_id) in out["final_output"] or "DONE" in out["final_output"]


async def test_resume_task_wait_keeps_waiting_while_the_task_is_still_running():
    model = _make_task_wait_model()
    run, _ = await _propose_and_approve(model)

    out = await orchestration.resume_task_wait(run["session_id"], model=model)
    assert out is None
    assert await repo.session_status(run["session_id"]) == "AWAITING_AGENTS"


# --- reject / dismiss are unaffected -------------------------------------------


async def test_reject_still_redrafts_and_never_creates_the_task():
    model = _make_redraft_refusing_model()
    run = await ingest_and_propose(
        "Please have jira-expert check TASKS-9.", model=model,
    )
    before = len(await repo.list_tasks())

    out = await gateway.reject_with_feedback(
        run["session_id"], run["proposal_id"], "Not needed after all.", model=model,
    )

    assert (await repo.load_proposal(run["proposal_id"]))["status"] == "REJECTED"
    assert len(await repo.list_tasks()) == before  # the Executor never ran
    assert await repo.session_status(run["session_id"]) == "DONE"
    assert out["final_output"] == "Understood, closing out."


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
    run, _ = await _propose_and_approve(model)
    rs = await repo.get_session_run_state(run["session_id"])
    task_id = rs["task_wait"]["task_id"]

    # Simulate the orphan sweep landing the child task's own dead run FAILED —
    # exactly what `repo.fail_stale_running_sessions` does, without needing a
    # second live session to go stale in this test.
    await repo.resolve_task(task_id, "FAILED", outcome="run died with its process")

    swept = await orchestration.resume_sweep()

    assert run["session_id"] in swept["resumed"]
    assert await repo.session_status(run["session_id"]) == "DONE"
