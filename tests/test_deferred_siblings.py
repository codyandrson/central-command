"""A resume must answer EVERY deferred call in the parking response.

Live 2026-08-30: the onboarding tour's closing turn deferred `current_time`
(executed) + two `propose_action`s. `park_proposal` kept the first proposal's
id; on approval the resume supplied that one result, pydantic-ai refused the
run ("Tool call results need to be provided for all deferred tool calls"), a
`session.resume_failed` was written, and the session completed anyway — the
second draft was never shown to the operator.

`durable.deferred_results` is the one seam every resume path now uses: the
decided call gets its result, every sibling a `ToolFailed` that tells the
model it was never seen and to raise it again.
"""

from __future__ import annotations

import pytest
from pydantic_ai.exceptions import ToolFailed
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from central_command.config import settings
from central_command.db import repo
from central_command.gateway import gateway
from central_command.runtime import converse
from central_command.runtime.durable import (
    SIBLING_NOT_PARKED,
    deferred_results,
    unanswered_calls,
)
from tests.conftest import needs_pg


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "executor_mode", "dry_run")
    monkeypatch.setattr(settings, "dispatch_approval_limit", 10_000)
    monkeypatch.setattr(settings, "auditor_enabled", False)


def _episode(name: str) -> dict:
    return {
        "intent": f"Record {name}.",
        "actions": [{
            "capability": "graph.add_episode",
            "arguments": {"name": name, "episode_body": f"{name} happened.",
                          "scope": "shared"},
            "target_ref": {"system": "graphiti", "id": f"episode:{name}",
                           "read_version": "n/a"},
            "reversibility": "reversible",
        }],
        "evidence": [{"kind": "record", "source_ref": "chat", "locator": "chat",
                      "claim": f"{name} happened."}],
        "expected_effect": f"episode {name} exists",
    }


# The live transcript's shape: one executed call, two deferred, first one parked.
_TOUR_TURN = [
    ModelRequest(parts=[UserPromptPart(content="close out the tour")]),
    ModelResponse(parts=[
        ToolCallPart(tool_name="current_time", args={}, tool_call_id="t-time"),
        ToolCallPart(tool_name="propose_action", args={"proposal": _episode("A")},
                     tool_call_id="t-first"),
        ToolCallPart(tool_name="propose_action", args={"proposal": _episode("B")},
                     tool_call_id="t-second"),
    ]),
    ModelRequest(parts=[ToolReturnPart(tool_name="current_time", content="noon",
                                       tool_call_id="t-time")]),
]


def test_unanswered_calls_excludes_what_the_next_request_already_returned():
    assert [c.tool_call_id for c in unanswered_calls(_TOUR_TURN)] == ["t-first", "t-second"]
    assert unanswered_calls([]) == []


async def test_the_decided_call_gets_its_result_and_each_sibling_a_bounce():
    results = await deferred_results(_TOUR_TURN, "t-first", "executed")
    assert results.calls["t-first"] == "executed"
    bounce = results.calls["t-second"]
    assert isinstance(bounce, ToolFailed)
    assert bounce.message == SIBLING_NOT_PARKED.format(tool="propose_action")
    assert "t-time" not in results.calls, "an executed call must not be re-answered"


async def test_a_single_deferred_call_is_answered_exactly_as_before():
    single = _TOUR_TURN[:2]
    single[1] = ModelResponse(parts=[_TOUR_TURN[1].parts[1]])
    results = await deferred_results(single, "t-first", "executed")
    assert results.calls == {"t-first": "executed"}


def _two_proposal_model(seen: dict) -> FunctionModel:
    """Turn 1 defers two proposals in one response. Turn 2 (the resume)
    records what came back for each and closes out in text."""
    def _respond(messages, info: AgentInfo) -> ModelResponse:
        for m in messages:
            for p in getattr(m, "parts", []):
                if isinstance(p, ToolReturnPart) and p.tool_name == "propose_action":
                    seen[p.tool_call_id] = (p.content, getattr(p, "outcome", None))
        if seen:
            return ModelResponse(parts=[TextPart(content="Closing out.")])
        return ModelResponse(parts=[
            ToolCallPart(tool_name="propose_action", args={"proposal": _episode("A")},
                         tool_call_id="e2e-first"),
            ToolCallPart(tool_name="propose_action", args={"proposal": _episode("B")},
                         tool_call_id="e2e-second"),
        ])
    return FunctionModel(_respond)


@needs_pg
async def test_approving_the_parked_proposal_resumes_past_its_sibling():
    seen: dict = {}
    model = _two_proposal_model(seen)
    out = await converse.start_conversation("jira-expert", "record both", model=model)
    assert out.get("proposed") is True
    sid, pid = out["session_id"], out["proposal_id"]

    before = max((e["id"] for e in await repo.list_events_of_kinds(
        ["session.resume_failed", "session.deferred_siblings_bounced"])), default=0)
    res = await gateway.approve_and_execute(sid, pid, model=model)
    assert "resume_parked" not in res

    failed = [e for e in await repo.list_events_of_kinds(["session.resume_failed"], since_id=before)
              if e["ref_id"] == sid]
    assert failed == [], failed
    bounced = [e for e in await repo.list_events_of_kinds(
        ["session.deferred_siblings_bounced"], since_id=before) if e["ref_id"] == sid]
    assert len(bounced) == 1 and bounced[0]["payload"]["bounced"] == ["propose_action"]

    # The model's resumed turn saw BOTH results: the executed one and the bounce.
    assert set(seen) == {"e2e-first", "e2e-second"}
    assert seen["e2e-second"][1] == "failed"
    assert "was NOT processed" in str(seen["e2e-second"][0])
    assert (await repo.load_proposal(pid))["status"] == "EXECUTED"
