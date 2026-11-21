"""Generate tests/fixtures/pai_2_18_paused_session.json with pydantic-ai 2.18.0.

Run with the 2.18 venv's interpreter:
  <a pydantic-ai 2.18.0 venv>/bin/python -I tests/fixtures/gen_pai_2_18_paused_session.py \
      tests/fixtures/pai_2_18_paused_session.json
"""
import asyncio
import json
import sys

import pydantic_ai
from pydantic_ai import Agent, CallDeferred, DeferredToolRequests, ModelRetry, Tool
from pydantic_ai.capabilities import Capability
from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

assert pydantic_ai.__version__ == "2.18.0", pydantic_ai.__version__

CHARTER = "ORIGINAL CHARTER (2.18): you triage mail for the operator."
GUIDANCE = "SKILL: Jira guide. Always quote the issue key."
ROUTE = ("ROUTE CHECK (asked once, then never again in this session): the episode "
         "'Tampa trip' (2026-08-25) states a date AFTER its own reference_time.")
PROPOSAL = {
    "intent": "record the trip",
    "actions": [{
        "capability": "graph.add_episode",
        "arguments": {"name": "Tampa trip", "episode_body": "Jane Doe flies to Tampa on 2026-08-25.",
                      "scope": "shared", "reference_time": "2026-08-01T10:00:00Z",
                      "source_description": "email from travel@example.com"},
        "target_ref": {"system": "graphiti", "id": "central_command"},
        "reversibility": "reversible",
    }],
    "evidence": [{"kind": "email", "source_ref": "travel@example.com", "locator": "email",
                  "claim": "Jane Doe flies to Tampa on 2026-08-25."}],
    "expected_effect": "the trip is on record",
    "confidence": {"level": "high", "rationale": "the mail states it"},
}
step = {"n": 0}


def model(messages, info: AgentInfo):
    step["n"] += 1
    n = step["n"]
    if n == 1:
        return ModelResponse(parts=[ToolCallPart("load_capability", {"id": "jira-guide"}, tool_call_id="call-load")])
    if n == 2:
        return ModelResponse(parts=[ToolCallPart("skill_jira_guide", {"query": "issue key"}, tool_call_id="call-skill")])
    if n == 3:
        return ModelResponse(parts=[TextPart("I think I am done.")])
    if n == 4:
        return ModelResponse(parts=[ToolCallPart("propose_action", {"proposal": PROPOSAL}, tool_call_id="call-draft-1")])
    if n == 5:
        return ModelResponse(parts=[ToolCallPart("propose_action", {"proposal": PROPOSAL}, tool_call_id="call-draft-2")])
    raise AssertionError(n)


cap = Capability(id="jira-guide", description="How this Jira is used", instructions=GUIDANCE, defer_loading=True)


def skill_jira_guide(query: str) -> str:
    """Search the Jira guide."""
    return "[guide] Quote the issue key, e.g. TASKS-12."


cap.tool_plain(name="skill_jira_guide")(skill_jira_guide)
drafts = {"n": 0}


def propose_action(proposal: dict) -> str:
    """Propose a gated action."""
    drafts["n"] += 1
    if drafts["n"] == 1:
        raise ModelRetry(ROUTE)
    raise CallDeferred(metadata={"kind": "proposal"})


agent = Agent(FunctionModel(model), system_prompt=CHARTER, output_type=[str, DeferredToolRequests],
              capabilities=[cap], tools=[Tool(propose_action, max_retries=10)])


@agent.output_validator
def must_act(output):
    if isinstance(output, str):
        raise ModelRetry("Finish by proposing, or answer in plain text why nothing is needed.")
    return output


result = asyncio.run(agent.run("Mail from travel@example.com: Jane Doe flies to Tampa on 2026-08-25."))
assert isinstance(result.output, DeferredToolRequests)
call = result.output.calls[0]
state = {
    "generated_by": "pydantic-ai 2.18.0, tests/fixtures/gen_pai_2_18_paused_session.py",
    "messages": ModelMessagesTypeAdapter.dump_python(result.all_messages(), mode="json"),
    "tool_call_id": call.tool_call_id,
}
json.dump(state, open(sys.argv[1], "w"), indent=1, sort_keys=True)
for m in state["messages"]:
    print(m["kind"], [(p["part_kind"], p.get("tool_name"), p.get("tool_kind")) for p in m["parts"]])
