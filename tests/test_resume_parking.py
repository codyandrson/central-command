"""Slice 2 of outage equivalence: a decision resume that fails TRANSIENTLY
parks instead of closing.

The evidence base (2026-07-31): a LiteLLM cooldown 429 arriving during a
decision resume closed the drafter's session with its final turn lost. The
decision itself was never in doubt — the world change had already happened and
was recorded — but the agent's closing turn was gone for good and nothing could
get it back.

The property these tests pin: **a transient failure costs latency, never work.**
The session parks AWAITING_RESUME carrying the deferred result it is still owed;
the sweep (or the driver directly) hands that result back once the provider is
healthy; and what the retried run produces goes exactly where a no-outage run's
would have — including the operator-evidence re-check on a redraft, which is
the one thing a second copy of the post-resume tail would silently lose.

A SEMANTIC failure keeps today's behaviour byte-for-byte: loud and terminal.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from pydantic_ai.exceptions import ModelHTTPError

from central_command.api import orchestration
from central_command.config import settings
from central_command.db import repo
from central_command.gateway import gateway
from central_command.runtime.run import ingest_and_propose, run_task
from central_command.runtime.spike_model import (
    make_deferred_tool_model,
    make_redraft_model,
    make_spike_model,
)
from tests.conftest import needs_pg

pytestmark = needs_pg

OUTAGE = ModelHTTPError(429, "cc-default", body="No deployments available")


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "executor_mode", "dry_run")
    # A parked session is not AWAITING_HUMAN, but proposals accumulate across
    # the file all the same — pin the valve open so nothing drains-blocks.
    monkeypatch.setattr(settings, "dispatch_approval_limit", 10_000)


class _BoomAgent:
    """An agent whose turn raises — the provider being down, from the caller's
    point of view. `raises` is exhausted in order; once empty it delegates to
    the real agent, so a test can script "down, then back up"."""

    def __init__(self, real, *raises):
        self._real = real
        self._raises = list(raises)

    def __getattr__(self, name):
        return getattr(self._real, name)

    async def run(self, *a, **k):
        if self._raises:
            raise self._raises.pop(0)
        return await self._real.run(*a, **k)


def _boom_on_build(monkeypatch, module, *raises):
    """Make the next agent `module` builds fail its turn."""
    real_build = module.build_agent_for

    async def build(agent_id, model=None):
        return _BoomAgent(await real_build(agent_id, model=model), *raises)

    monkeypatch.setattr(module, "build_agent_for", build)


async def _pending(session_id: str) -> dict | None:
    return (await repo.get_session_run_state(session_id) or {}).get("pending_resume")


async def _kinds(ref_id: str) -> list[str]:
    return [e["kind"] for e in await repo.list_events(ref_id=ref_id)]


async def _task_for(session_id: str, agent_id: str = "inbox-triage") -> str:
    task_id = "task_" + uuid.uuid4().hex[:12]
    await repo.create_task(task_id, "outage test", "do the thing", agent_id)
    await repo.start_task(task_id, session_id)
    return task_id


# --- 1 + 2: the approve leg ----------------------------------------------------


async def test_a_transient_resume_failure_parks_the_session(monkeypatch):
    """The world change stands; the agent's final turn is DEFERRED, not lost."""
    model = make_spike_model()
    run = await ingest_and_propose("Dana: DEMO-1 slipped, due Aug 3.", model=model)
    task_id = await _task_for(run["session_id"])

    async def resume_boom(*a, **k):
        raise OUTAGE

    monkeypatch.setattr(gateway, "resume", resume_boom)
    out = await gateway.approve_and_execute(
        run["session_id"], run["proposal_id"], model=model
    )

    assert out["resume_parked"] is True
    # The decision and its execution are untouched — this is only about the turn.
    assert (await repo.load_proposal(run["proposal_id"]))["status"] == "EXECUTED"
    assert await repo.session_status(run["session_id"]) == "AWAITING_RESUME"

    pending = await _pending(run["session_id"])
    assert pending["kind"] == "result"
    assert pending["after"] == "approved"
    assert pending["proposal_id"] == run["proposal_id"]
    assert pending["attempts"] == 0
    assert "2026-08-03" in pending["text"]
    assert pending["tool_call_id"]
    assert pending["parked_at"] and pending["last_error"]

    # Nothing terminal happened: no completion, no task resolution.
    kinds = await _kinds(run["session_id"])
    assert "session.resume_parked" in kinds
    assert "session.completed" not in kinds
    assert "session.resume_failed" not in kinds
    assert (await repo.get_task(task_id))["status"] == "IN_PROGRESS"

    parked = [e for e in await repo.list_events(ref_id=run["session_id"])
              if e["kind"] == "session.resume_parked"][0]
    assert parked["payload"]["transient"] is True
    assert parked["payload"]["after"] == "approved"


async def test_a_semantic_resume_failure_keeps_todays_behaviour(monkeypatch):
    """Regression: only a transient failure parks. A real error stays loud."""
    model = make_spike_model()
    run = await ingest_and_propose("Dana: DEMO-1 slipped, due Aug 3.", model=model)
    task_id = await _task_for(run["session_id"])

    async def resume_boom(*a, **k):
        raise ValueError("the message history is malformed")

    monkeypatch.setattr(gateway, "resume", resume_boom)
    await gateway.approve_and_execute(
        run["session_id"], run["proposal_id"], model=model
    )

    assert await repo.session_status(run["session_id"]) == "DONE"
    # The ARMED marker survives (run_state is a key-level merge, and it is the
    # record of what the run was owed). What must not happen is a PARK: the
    # session is terminal, so no sweep worklist can see it — `resume_park.arm`
    # is a note, only a status flip is a retry.
    assert run["session_id"] not in await repo.sessions_awaiting_resume()
    assert run["session_id"] not in [
        r["id"] for r in await repo.sessions_with_orphaned_resume(0)
    ]
    kinds = await _kinds(run["session_id"])
    assert "session.resume_failed" in kinds
    assert "session.completed" in kinds
    assert "session.resume_parked" not in kinds
    assert (await repo.get_task(task_id))["status"] == "DONE"

    failed = [e for e in await repo.list_events(ref_id=run["session_id"])
              if e["kind"] == "session.resume_failed"][0]
    assert failed["payload"]["transient"] is False


# --- 3: the retry converges ----------------------------------------------------


async def test_the_driver_finishes_a_parked_approved_resume(monkeypatch):
    model = make_spike_model()
    run = await ingest_and_propose("Dana: DEMO-1 slipped, due Aug 3.", model=model)
    task_id = await _task_for(run["session_id"])

    async def resume_boom(*a, **k):
        raise OUTAGE

    monkeypatch.setattr(gateway, "resume", resume_boom)
    await gateway.approve_and_execute(run["session_id"], run["proposal_id"], model=model)
    assert await repo.session_status(run["session_id"]) == "AWAITING_RESUME"

    # The provider is back.
    monkeypatch.undo()
    monkeypatch.setattr(settings, "demo_mode", True)
    out = await orchestration.resume_parked_session(run["session_id"], model=model)

    assert out["resumed"] is True and out["attempts"] == 1
    assert await repo.session_status(run["session_id"]) == "DONE"
    assert await _pending(run["session_id"]) is not None  # the record keeps the outage
    kinds = await _kinds(run["session_id"])
    assert "session.completed" in kinds
    assert "session.resume_retried" in kinds

    # The task resolved exactly as a no-outage run would have left it …
    task = await repo.get_task(task_id)
    assert task["status"] == "DONE"
    assert "2026-08-03" in (task["outcome"] or "")
    # … and the agent's final turn is in the transcript.
    assert out["final_output"]


# --- 4: the rejected leg keeps the operator-evidence re-check ------------------


async def test_a_retried_rejection_redraft_still_gets_the_evidence_check(monkeypatch):
    """THE dedup test. A redraft made on the RETRY path must be persisted with
    the control plane's authoritative pointer at the decided event and the
    agent's quote re-checked against it — which only holds because both paths
    end in the same `finish_decision_resume`."""
    feedback = "Wrong — I spoke to Dana, make it 2026-08-10."
    model = make_redraft_model()
    run = await ingest_and_propose("Dana: DEMO-1 slipped, due Aug 3.", model=model)

    _boom_on_build(monkeypatch, gateway, OUTAGE)
    out = await gateway.reject_with_feedback(
        run["session_id"], run["proposal_id"], feedback, model=model
    )
    assert out["resume_parked"] is True
    assert (await repo.load_proposal(run["proposal_id"]))["status"] == "REJECTED"

    pending = await _pending(run["session_id"])
    assert pending["kind"] == "denial"          # a ToolDenied, not a result
    assert pending["text"] == feedback
    assert pending["after"] == "rejected"
    assert pending["decided_event_id"]

    monkeypatch.undo()
    monkeypatch.setattr(settings, "demo_mode", True)
    driven = await orchestration.resume_parked_session(run["session_id"], model=model)

    redraft = await repo.load_proposal(driven["redraft_proposal_id"])
    assert redraft["status"] == "AWAITING_HUMAN"
    assert redraft["actions"][0]["arguments"]["due_date"] == "2026-08-10"
    ev = redraft["evidence"][0]
    assert ev["kind"] == "operator"
    assert ev["source_ref"] == f"event:{pending['decided_event_id']}"
    assert ev["claim_matches_source"] is True
    decided = await repo.get_event(pending["decided_event_id"])
    assert decided["payload"]["feedback"] == feedback
    # The session is parked on the redraft, not terminal.
    assert await repo.session_status(run["session_id"]) == "AWAITING_HUMAN"


# --- 5: the answered-question leg ----------------------------------------------


async def test_a_transient_answer_resume_parks_without_failing_the_task(monkeypatch):
    from central_command.runtime import durable

    model = make_deferred_tool_model("ask_operator", {"question": "A or B?"})
    task_id = "task_" + uuid.uuid4().hex[:12]
    await repo.create_task(task_id, "outage test", "Update the issue.", "jira-expert")
    out = await run_task("jira-expert", "Update the issue.", model=model,
                         task_id=task_id)
    assert out["asked"] is True
    await repo.start_task(task_id, out["session_id"])

    async def resume_boom(*a, **k):
        raise OUTAGE

    monkeypatch.setattr(durable, "resume", resume_boom)
    await orchestration.answer_item(out["item_id"], "TASKS-12")
    # answer_item resumes in the background; wait for the park to land.
    for _ in range(100):
        if await repo.session_status(out["session_id"]) == "AWAITING_RESUME":
            break
        await asyncio.sleep(0.05)

    assert await repo.session_status(out["session_id"]) == "AWAITING_RESUME"
    pending = await _pending(out["session_id"])
    assert pending["after"] == "answered"
    assert pending["kind"] == "result"
    assert pending["text"] == "TASKS-12"     # the operator's words, verbatim
    assert pending["item_id"] == out["item_id"]
    # The answer bookkeeping stands, and the task was NOT failed.
    assert (await repo.get_operator_item(out["item_id"]))["status"] == "ANSWERED"
    assert (await repo.get_task(task_id))["status"] == "IN_PROGRESS"

    monkeypatch.undo()
    monkeypatch.setattr(settings, "demo_mode", True)
    driven = await orchestration.resume_parked_session(out["session_id"], model=model)

    assert driven["resumed"] is True
    assert await repo.session_status(out["session_id"]) == "DONE"
    task = await repo.get_task(task_id)
    assert task["status"] == "DONE"
    assert task["outcome"]


# --- 6: bounded retries, then a visible promotion -------------------------------


async def test_retries_are_bounded_and_exhaustion_promotes(monkeypatch):
    monkeypatch.setattr(settings, "resume_retry_limit", 2)
    model = make_spike_model()
    run = await ingest_and_propose("Dana: DEMO-1 slipped, due Aug 3.", model=model)
    task_id = await _task_for(run["session_id"])

    async def resume_boom(*a, **k):
        raise OUTAGE

    monkeypatch.setattr(gateway, "resume", resume_boom)
    await gateway.approve_and_execute(run["session_id"], run["proposal_id"], model=model)
    assert (await _pending(run["session_id"]))["attempts"] == 0

    # The provider is still down for the driver too.
    _boom_on_build(monkeypatch, orchestration_agent_module(), OUTAGE)
    first = await orchestration.resume_parked_session(run["session_id"], model=model)
    assert first["parked"] is True
    assert await repo.session_status(run["session_id"]) == "AWAITING_RESUME"
    assert (await _pending(run["session_id"]))["attempts"] == 1

    _boom_on_build(monkeypatch, orchestration_agent_module(), OUTAGE)
    second = await orchestration.resume_parked_session(run["session_id"], model=model)
    assert second.get("parked") is not True     # the budget is spent

    # Promotion, not a loop: terminal state + an operator item naming the outage.
    assert await repo.session_status(run["session_id"]) == "DONE"
    kinds = await _kinds(run["session_id"])
    assert "session.resume_exhausted" in kinds
    assert "session.resume_failed" in kinds
    assert "session.completed" in kinds
    assert (await repo.get_task(task_id))["status"] == "DONE"

    exhausted = [e for e in await repo.list_events(ref_id=run["session_id"])
                 if e["kind"] == "session.resume_exhausted"][0]
    item = await repo.get_operator_item(exhausted["payload"]["operator_item_id"])
    assert item["status"] == "OPEN" and item["kind"] == "question"
    assert run["proposal_id"] in item["body"]


def orchestration_agent_module():
    """The module whose `build_agent_for` the driver calls (it imports the
    factory lazily, so the patch target is the runtime module itself)."""
    from central_command.runtime import agent

    return agent


# --- 7: the claim is the SQL flip ----------------------------------------------


async def test_two_concurrent_drivers_produce_exactly_one_resume(monkeypatch):
    model = make_spike_model()
    run = await ingest_and_propose("Dana: DEMO-1 slipped, due Aug 3.", model=model)

    async def resume_boom(*a, **k):
        raise OUTAGE

    monkeypatch.setattr(gateway, "resume", resume_boom)
    await gateway.approve_and_execute(run["session_id"], run["proposal_id"], model=model)

    monkeypatch.undo()
    monkeypatch.setattr(settings, "demo_mode", True)
    a, b = await asyncio.gather(
        orchestration.resume_parked_session(run["session_id"], model=model),
        orchestration.resume_parked_session(run["session_id"], model=model),
    )
    assert [bool(a), bool(b)].count(True) == 1, "the AWAITING_RESUME→RUNNING flip is the lock"
    retried = [k for k in await _kinds(run["session_id"])
               if k == "session.resume_retried"]
    assert len(retried) == 1


# --- cockpit honesty: a parked session never reads as finished ------------------


async def test_the_cockpit_notice_tells_the_truth_about_a_parked_run(monkeypatch):
    """No new refusal code is needed (a conversation parked AWAITING_RESUME is
    already `turn_in_progress` — see tests/test_chat_composer.py), but the
    read-only notice for a WORK session fell through to "finished". A run whose
    last turn is still owed is not finished."""
    from central_command.api import nerve_gateway

    model = make_spike_model()
    run = await ingest_and_propose("Dana: DEMO-1 slipped, due Aug 3.", model=model)

    async def resume_boom(*a, **k):
        raise OUTAGE

    monkeypatch.setattr(gateway, "resume", resume_boom)
    await gateway.approve_and_execute(run["session_id"], run["proposal_id"], model=model)

    session = await repo.get_session(run["session_id"])
    notice = await nerve_gateway._run_session_context(session)
    assert "finished" not in notice["message"]
    assert "outage" in notice["message"]


# --- 8: the sweep drives parked resumes ----------------------------------------


async def test_resume_sweep_picks_up_a_parked_resume(monkeypatch):
    """The sweep is the "after a restart, or whenever in doubt" lever — restart
    recovery must cover parked resumes from day one."""
    model = make_spike_model()
    run = await ingest_and_propose("Dana: DEMO-1 slipped, due Aug 3.", model=model)

    async def resume_boom(*a, **k):
        raise OUTAGE

    monkeypatch.setattr(gateway, "resume", resume_boom)
    await gateway.approve_and_execute(run["session_id"], run["proposal_id"], model=model)
    assert run["session_id"] in await repo.sessions_awaiting_resume()

    monkeypatch.undo()
    monkeypatch.setattr(settings, "demo_mode", True)
    swept = await orchestration.resume_sweep()

    assert run["session_id"] in swept["resumed"]
    assert await repo.session_status(run["session_id"]) == "DONE"


# --- 9: the resume that left no park record at all -----------------------------
#
# Slice 2 covers a resume that FAILED and got to run its handler. This covers the
# one that never got to run anything: uvicorn hung on shutdown 2026-08-15 and
# systemd SIGKILLed it 15s into a batch of answered questions, leaving twelve
# triage sessions AWAITING_HUMAN over questions already marked ANSWERED. No
# OPEN Inbox item, no parked status, no worklist — invisible and permanent.
#
# `CancelledError` is the faithful stand-in: it is a BaseException, so the
# `except Exception` handlers that write the park record do NOT run, which is
# exactly what a killed background task looks like from the database's side.


async def test_a_killed_resume_is_recovered_by_the_sweep(monkeypatch):
    """The armed marker is what makes a dead resume findable at all."""
    model = make_spike_model()
    run = await ingest_and_propose("Dana: DEMO-1 slipped, due Aug 3.", model=model)

    async def resume_killed(*a, **k):
        raise asyncio.CancelledError()

    monkeypatch.setattr(gateway, "resume", resume_killed)
    with pytest.raises(asyncio.CancelledError):
        await gateway.approve_and_execute(
            run["session_id"], run["proposal_id"], model=model
        )

    # Exactly the state the restart left behind: the write landed, the session
    # is still parked on its proposal, and nothing parked it for retry.
    assert await repo.session_status(run["session_id"]) == "AWAITING_HUMAN"
    assert run["session_id"] not in await repo.sessions_awaiting_resume()

    monkeypatch.undo()
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "dispatch_stale_claim_seconds", 0)
    swept = await orchestration.resume_sweep()

    assert run["session_id"] in swept["orphaned"]
    assert run["session_id"] in swept["resumed"]
    assert await repo.session_status(run["session_id"]) == "DONE"


async def test_a_stale_marker_beside_a_new_call_is_not_re_driven(monkeypatch):
    """run_state is a key-level MERGE, so a session that resumed and then
    RE-PARKED (a redraft) still carries the previous decision's marker. Driving
    it would hand the redraft's deferred call the OLD outcome. The tool_call_id
    match is what tells the two apart — without it this sweep would corrupt
    every redraft rather than recover anything."""
    model = make_redraft_model()
    run = await ingest_and_propose("Dana: DEMO-1 slipped, due Aug 3.", model=model)

    # A rejection arms `after='rejected'`, resumes, and the agent redrafts —
    # parking a NEW proposal on a NEW tool_call_id in the same session.
    await gateway.reject_with_feedback(
        run["session_id"], run["proposal_id"],
        "Wrong — I spoke to Dana, make it 2026-08-10.", model=model,
    )

    session_id = run["session_id"]
    assert await repo.session_status(session_id) == "AWAITING_HUMAN"
    state = await repo.get_session_run_state(session_id)
    assert state.get("pending_resume"), "the rejection should have armed a marker"
    assert state["pending_resume"]["tool_call_id"] != state["tool_call_id"]

    monkeypatch.setattr(settings, "dispatch_stale_claim_seconds", 0)
    orphans = [r["id"] for r in await repo.sessions_with_orphaned_resume(0)]
    assert session_id not in orphans
