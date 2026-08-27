"""Replay the resume half of a reject whose RPC was cancelled mid-flight.

2026-08-13: the cockpit's WS dropped ~25s after `proposals.reject` on
prop_075d96b53965 started, and the gateway's disconnect handler cancelled the
in-flight RPC task inside `agent.run()`. The decision half had already
committed (proposal REJECTED, `proposal.decided` id 9663 with the operator's
feedback) but the coach was never resumed — sess_10e406047961 stuck
AWAITING_HUMAN. This replays exactly the second half of
`gateway.reject_with_feedback`: feed the recorded feedback as a ToolDenied
and let `finish_decision_resume` land the redraft. It does NOT re-emit
`proposal.decided` — the decision already happened once.

Run from the repo root: .venv/bin/python scripts/oneoff/replay_cancelled_reject_resume.py
"""

import asyncio

from pydantic_ai import DeferredToolResults, ToolDenied

from central_command.db import repo
from central_command import events
from central_command.gateway import gateway
from central_command.runtime import resume_park
from central_command.runtime.agent import build_agent_for
from central_command.runtime.deps import TriageDeps
from central_command.runtime.durable import load_messages
from central_command.runtime.models import resolve_session_model

PROPOSAL_ID = "prop_075d96b53965"
SESSION_ID = "sess_10e406047961"
DECIDED_EVENT_ID = 9663


async def main() -> None:
    prop = await repo.load_proposal(PROPOSAL_ID)
    assert prop and prop["status"] == "REJECTED", f"unexpected proposal state: {prop and prop['status']}"
    session = await repo.get_session(SESSION_ID)
    assert session and session["status"] == "AWAITING_HUMAN", (
        f"session is not stuck awaiting (status {session and session['status']}) — nothing to replay"
    )
    run_state = await repo.load_paused_session(SESSION_ID)
    assert run_state, "no paused run_state — cannot resume"

    decided = await repo.get_event(DECIDED_EVENT_ID)
    assert decided and decided["kind"] == "proposal.decided", "decided event not found"
    feedback = decided["payload"]["feedback"]
    print(f"replaying resume with recorded feedback ({len(feedback)} chars)")

    model = await resolve_session_model(SESSION_ID, prop["agent_id"])
    messages = load_messages(run_state)
    results = DeferredToolResults()
    results.calls[run_state["tool_call_id"]] = ToolDenied(feedback)
    pending = resume_park.pending_resume(
        kind="denial", text=feedback, tool_call_id=run_state["tool_call_id"],
        after="rejected", proposal_id=PROPOSAL_ID, decided_event_id=DECIDED_EVENT_ID,
    )
    agent = await build_agent_for(prop["agent_id"], model=model)

    final = None
    try:
        final = await agent.run(
            message_history=messages,
            deferred_tool_results=results,
            model=model,
            deps=TriageDeps(agent_id=prop["agent_id"], session_id=SESSION_ID),
        )
    except Exception as e:  # noqa: BLE001 — same contract as reject_with_feedback
        if await resume_park.park_if_transient(SESSION_ID, e, pending):
            print("resume parked as transient — the retry driver owns it now")
            return
        await events.emit(
            "session.resume_failed", ref_id=SESSION_ID,
            payload={"error": str(e), "proposal_id": PROPOSAL_ID,
                     "after": "rejected", "transient": False},
            actor="system",
        )

    out = await gateway.finish_decision_resume(
        session_id=SESSION_ID, agent_id=prop["agent_id"],
        proposal_id=PROPOSAL_ID, after="rejected", final=final, agent=agent,
        model=model, feedback=feedback, decided_event_id=DECIDED_EVENT_ID,
        pending=pending,
    )
    print(f"done: {out}")


if __name__ == "__main__":
    asyncio.run(main())
