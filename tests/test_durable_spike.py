"""M2 regression: an agent that defers a proposal can be serialized, torn down,
rebuilt from data alone, and resumed to completion. This is the durable
pause/resume mechanic that the whole runtime bet rests on — no DB, no API key."""

from pydantic_ai import DeferredToolResults
from pydantic_ai.messages import ModelMessagesTypeAdapter

from central_command.runtime.agent import build_agent
from central_command.runtime.durable import extract_deferred, proposal_from_call
from central_command.runtime.spike_model import make_spike_model


async def test_pause_serialize_restart_resume():
    # --- run 1: the agent proposes and defers ---
    agent = await build_agent(model=make_spike_model())
    result = await agent.run("Dana: TEAM-142 deadline slipped to Aug 3.", model=make_spike_model())

    call = extract_deferred(result)
    assert call is not None, "agent should have deferred a proposal"
    proposal = proposal_from_call(call)
    assert proposal.actions[0].capability == "jira.set_due_date"

    # --- persist to plain JSON-able data, then drop ALL in-memory state ---
    blob = ModelMessagesTypeAdapter.dump_python(result.all_messages(), mode="json")
    tool_call_id = call.tool_call_id
    del agent, result, proposal, call

    # --- run 2 (simulated fresh process): rebuild from the blob alone ---
    agent2 = await build_agent(model=make_spike_model())
    messages = ModelMessagesTypeAdapter.validate_python(blob)
    results = DeferredToolResults()
    results.calls[tool_call_id] = "TEAM-142.duedate set to 2026-08-03"
    final = await agent2.run(
        message_history=messages, deferred_tool_results=results, model=make_spike_model()
    )

    assert isinstance(final.output, str)
    assert "2026-08-03" in final.output
