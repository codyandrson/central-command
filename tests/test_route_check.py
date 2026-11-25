"""The route check (DL-213): one question, once per session, when an agent
proposes an episode whose own words date something AFTER its source.

A date later than the reference_time is something still to happen or still
running when the source was written — a flight, a stay, a delivery, a
scheduled payment. That is work in flight, whose home is the tracker or the
calendar. The briefing says so; a replay of recorded drafts showed the
briefing alone leaves most of them, and the same sentence handed back at the
moment of the draft does not.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic_ai import ModelRetry
from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelRequest, ToolReturnPart

from central_command.contract.models import Proposal
from central_command.runtime import tools


def _episode(body: str, reference_time: str = "2026-08-01T10:00:00Z", name: str = "a fact") -> dict:
    return {
        "capability": "graph.add_episode",
        "arguments": {
            "name": name,
            "episode_body": body,
            "scope": "shared",
            "reference_time": reference_time,
            "source_description": "email from travel@example.com",
        },
        "target_ref": {"system": "graphiti", "id": "central_command"},
        "reversibility": "reversible",
    }


_JIRA = {
    "capability": "jira.add_comment",
    "arguments": {"issue_key": "TASKS-12", "body": "Hotel booked."},
    "target_ref": {"system": "jira", "id": "TASKS-12"},
    "reversibility": "reversible",
}


def _proposal(*actions: dict) -> Proposal:
    return Proposal.model_validate(
        {
            "intent": "record what the mail says",
            "actions": list(actions),
            "evidence": [
                {
                    "kind": "email",
                    "source_ref": "travel@example.com",
                    "locator": "email",
                    "claim": "booked",
                }
            ],
            "expected_effect": "the record is current",
            "confidence": {"level": "high", "rationale": "the mail states it"},
        }
    )


def _ctx(messages=()):
    return SimpleNamespace(
        messages=list(messages),
        deps=SimpleNamespace(session_id="sess_test", agent_id="inbox-triage"),
    )


def test_a_date_after_the_source_is_asked_about():
    asked = tools._route_check(
        _ctx(),
        _proposal(
            _JIRA,
            _episode(
                "Jane Doe has lodging booked from 2026-08-12 through 2026-08-14.",
                name="Munich lodging",
            ),
        ),
    )
    assert asked is not None and asked.startswith(tools._ROUTE_CHECK_MARK)
    # The draft's own name and dates are quoted back, and the three exits named.
    assert "'Munich lodging' (2026-08-12, 2026-08-14)" in asked
    for exit_ in (
        "(a) drop the episode",
        "(b) if the source ALSO teaches",
        "(c) if the later date IS",
    ):
        assert exit_ in asked


@pytest.mark.parametrize(
    "body",
    [
        "Jane Doe's checking account at Example Credit Union ends in 7731.",
        "Jane Doe held a store card from 2023-10-15 until 2026-07-19.",
        "Example Heating installed a heat pump at Jane Doe's home on 2026-07-29.",
        "Jane Doe has had Example Health insurance since 2022-09.",
    ],
)
def test_a_lasting_or_finished_fact_is_not_asked_about(body):
    assert tools._route_check(_ctx(), _proposal(_episode(body))) is None


def test_the_same_day_as_the_source_is_not_after_it():
    assert (
        tools._route_check(
            _ctx(), _proposal(_episode("As of 2026-08-01, Jane Doe's plan costs $40 a month."))
        )
        is None
    )


def test_it_is_asked_once_per_session_from_the_record():
    """The marker is read from the run's own messages, so it survives a pause
    and a resume: a redraft the operator asked for is never second-guessed."""
    proposal = _proposal(_episode("Jane Doe flies to Tampa on 2026-08-25."))
    first = tools._route_check(_ctx(), proposal)
    assert first is not None
    # Since pydantic-ai 2.60 the ModelRetry answering the call is recorded as a
    # retried tool return; a history stored before that holds a `retry-prompt`,
    # which loads as the same thing.
    earlier = ModelRequest(
        parts=[ToolReturnPart("propose_action", first, tool_call_id="c1", outcome="retried")]
    )
    assert tools._route_check(_ctx([earlier]), proposal) is None
    stored = ModelMessagesTypeAdapter.validate_python(
        [
            {
                "kind": "request",
                "parts": [
                    {
                        "part_kind": "retry-prompt",
                        "tool_name": "propose_action",
                        "tool_call_id": "c1",
                        "content": first,
                    }
                ],
            }
        ]
    )
    assert tools._route_check(_ctx(stored), proposal) is None
    # Some other retry in the record does not count as having asked, nor does
    # the mark in an ordinary tool result (a mail quoting it, say).
    other = ModelRequest(
        parts=[
            ToolReturnPart("propose_action", "fix the args", tool_call_id="c1", outcome="retried")
        ]
    )
    assert tools._route_check(_ctx([other]), proposal) is not None
    quoted = ModelRequest(parts=[ToolReturnPart("mail_read", first, tool_call_id="c2")])
    assert tools._route_check(_ctx([quoted]), proposal) is not None


async def test_a_real_run_asks_once_across_a_redraft_and_a_resume(monkeypatch):
    """Through pydantic-ai itself: the first draft is handed back with the
    question, the redraft parks, and after the operator's decision a SECOND
    episode proposal in the same session parks without being asked again.
    Under pydantic-ai 2.60+ the retry is no `retry-prompt`, and a check that
    looked for one asked on every draft until `max_retries` failed the run."""
    from pydantic_ai import Agent, DeferredToolRequests, DeferredToolResults, Tool
    from pydantic_ai.messages import ModelResponse, ToolCallPart
    from pydantic_ai.models.function import AgentInfo, FunctionModel

    from central_command import events

    emitted: list[str] = []

    async def emit(kind, **kw):
        emitted.append(kind)

    monkeypatch.setattr(events, "emit", emit)
    first = _proposal(_episode("Jane Doe flies to Tampa on 2026-08-25.", name="Tampa trip"))
    second = _proposal(
        _episode("Jane Doe's lodging is booked for 2026-08-26.", name="Tampa lodging")
    )
    drafts = iter([first, first, second])
    seen: list = []

    def model(messages, info: AgentInfo):
        seen.append(messages)
        p = next(drafts)
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "propose_action",
                    {"proposal": p.model_dump(mode="json")},
                    tool_call_id=f"draft-{len(seen)}",
                )
            ]
        )

    agent = Agent(
        FunctionModel(model),
        output_type=[str, DeferredToolRequests],
        tools=[Tool(tools.propose_action, max_retries=10)],
    )
    deps = SimpleNamespace(session_id="sess_route_real", agent_id="inbox-triage")
    run1 = await agent.run("mail: Jane Doe flies to Tampa", deps=deps)
    assert isinstance(run1.output, DeferredToolRequests), "the redraft parks"
    assert emitted.count("proposal.route_checked") == 1
    handed_back = [p for p in seen[1][-1].parts if isinstance(p, ToolReturnPart)]
    assert handed_back and handed_back[0].outcome == "retried"
    assert str(handed_back[0].content).startswith(tools._ROUTE_CHECK_MARK)

    results = DeferredToolResults()
    results.calls[run1.output.calls[0].tool_call_id] = "approved: executed"
    run2 = await agent.run(
        message_history=run1.all_messages(), deferred_tool_results=results, deps=deps
    )
    assert isinstance(run2.output, DeferredToolRequests), "the second proposal parks unasked"
    assert durable_proposal(run2) == "Tampa lodging"
    assert emitted.count("proposal.route_checked") == 1, "asked once per session"


def durable_proposal(result) -> str:
    from central_command.runtime import durable

    call = durable.extract_deferred(result)
    return durable.proposal_from_call(call).actions[0].arguments["name"]


def test_a_proposal_without_an_episode_or_a_readable_reference_time_is_left_alone():
    assert tools._route_check(_ctx(), _proposal(_JIRA)) is None
    assert (
        tools._route_check(
            _ctx(),
            _proposal(
                _episode("Jane Doe flies to Tampa on 2026-08-25.", reference_time="not a time")
            ),
        )
        is None
    )


async def test_the_propose_tool_hands_the_draft_back_and_records_it(monkeypatch):
    from central_command import events

    emitted: list[tuple] = []

    async def emit(kind, **kw):
        emitted.append((kind, kw))

    monkeypatch.setattr(events, "emit", emit)
    proposal = _proposal(
        _JIRA, _episode("Jane Doe has lodging booked from 2026-08-12 through 2026-08-14.")
    )
    with pytest.raises(ModelRetry) as exc:
        await tools._validate_proposal(_ctx(), proposal, agent_authored=True)
    assert str(exc.value).startswith(tools._ROUTE_CHECK_MARK)
    assert [k for k, _ in emitted] == ["proposal.route_checked"]
    payload = emitted[0][1]["payload"]
    assert payload["agent_id"] == "inbox-triage" and payload["asked"].startswith(
        tools._ROUTE_CHECK_MARK
    )


async def test_a_tool_built_proposal_is_never_asked(monkeypatch):
    """Only a draft the model wrote is questioned; a proposal a tool builds
    from plain arguments has no drafting judgment to hand back."""
    from central_command import events

    async def emit(kind, **kw):  # pragma: no cover - must not be called
        raise AssertionError(f"unexpected event {kind}")

    monkeypatch.setattr(events, "emit", emit)
    proposal = _proposal(_episode("Jane Doe flies to Tampa on 2026-08-25."))
    await tools._validate_proposal(_ctx(), proposal, agent_authored=False)
