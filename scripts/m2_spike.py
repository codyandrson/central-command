"""M2 durable spike — run as TWO separate processes to prove a paused agent
survives a full restart.

    python scripts/m2_spike.py propose     # process 1: agent proposes, defers, persists, exits
    python scripts/m2_spike.py approve      # process 2 (fresh): loads from Postgres, resumes, finishes

The `approve` process shares NOTHING in memory with `propose` — it reconstructs
the agent's paused state entirely from Postgres. That is the durability proof.
"""

import argparse
import asyncio

from central_command.contract import Proposal
from central_command.db import repo
from central_command.runtime.agent import build_agent
from central_command.runtime.durable import (
    dump_run_state,
    extract_deferred,
    load_messages,
    proposal_from_call,
    resume,
)
from central_command.runtime.spike_model import make_spike_model

SESSION_ID = "sess_m2_spike"
AGENT_ID = "inbox-triage"
EMAIL = "New email from Dana: the TEAM-142 deadline has slipped a week, now Aug 3."


async def do_propose() -> None:
    await repo.cleanup_spike()
    await repo.upsert_agent(AGENT_ID, "Inbox Triage", "spike-model", "triage email into Jira proposals")

    model = make_spike_model()
    agent = await build_agent(model=model)
    result = await agent.run(EMAIL, model=model)

    call = extract_deferred(result)
    if call is None:
        raise SystemExit("FAIL: agent did not defer a proposal")

    proposal = proposal_from_call(call)
    run_state = dump_run_state(result, call.tool_call_id)

    await repo.save_paused_session(SESSION_ID, AGENT_ID, run_state)
    await repo.save_proposal(
        proposal_id="prop_m2_spike",
        session_id=SESSION_ID,
        agent_id=AGENT_ID,
        intent=proposal.intent,
        actions=[a.model_dump() for a in proposal.actions],
        evidence=[e.model_dump() for e in proposal.evidence],
        expected_effect=proposal.expected_effect,
        idempotency_key="prop_m2_spike:act_1",
    )

    print("PROPOSE  ┃ agent ran, called propose_action, and DEFERRED.")
    print(f"PROPOSE  ┃ proposal.intent: {proposal.intent}")
    print(f"PROPOSE  ┃ pending tool_call_id: {call.tool_call_id}")
    print(f"PROPOSE  ┃ persisted session {SESSION_ID} -> AWAITING_HUMAN.")
    print("PROPOSE  ┃ this process now exits. Nothing is held in memory.")


async def do_approve() -> None:
    status = await repo.session_status(SESSION_ID)
    print(f"APPROVE  ┃ fresh process. Loaded session status from Postgres: {status}")
    run_state = await repo.load_paused_session(SESSION_ID)
    if run_state is None:
        raise SystemExit("FAIL: no paused session found — run `propose` first")

    messages = load_messages(run_state)
    tool_call_id = run_state["tool_call_id"]

    # The control-plane Executor would perform the real Jira write here (via the
    # n8n façade), holding the credentials. We simulate its result for the spike.
    executor_result = "TEAM-142.duedate set to 2026-08-03 (executed once, provenance stamped)"

    model = make_spike_model()
    agent = await build_agent(model=model)
    final = await resume(agent, messages, tool_call_id, executor_result, model=model)

    await repo.set_proposal_status("prop_m2_spike", "EXECUTED")
    await repo.mark_session_done(SESSION_ID)

    print("APPROVE  ┃ rebuilt the paused agent from Postgres alone and resumed it.")
    print(f"APPROVE  ┃ agent final output: {final.output}")
    print(f"APPROVE  ┃ session {SESSION_ID} -> DONE.  ✓ pause survived a full restart.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["propose", "approve"])
    args = parser.parse_args()
    asyncio.run(do_propose() if args.command == "propose" else do_approve())


if __name__ == "__main__":
    main()
