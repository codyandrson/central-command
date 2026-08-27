"""An assigned task that is ambiguous ASKS instead of guessing (tier 2).

Until 2026-07-25 `ask_operator` lived only in the `orchestrate` pack, so only
the orchestrator could ask and only inside a project loop; every other agent's
options on an ambiguous task were to guess or to give up. the operator: "all agents
should be able to ask."

That change is also what turned a latent bug live. `extract_deferred` returns
the FIRST deferred call whatever tool it is, and `run_task` assumed every
deferral was a proposal — an assumption that held only while nothing but the
orchestrator could ask. The same shape broke chat the day before.
"""

from __future__ import annotations

import pytest

from central_command.config import settings
from central_command.db import repo
from central_command.runtime.run import run_task
from central_command.runtime.spike_model import make_deferred_tool_model
from tests.conftest import needs_pg


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "executor_mode", "dry_run")


def test_the_classifier_is_one_seam_not_six():
    """Six paths call `extract_deferred`; each must classify before acting.

    A local tool-name set per call site is how the next path repeats the
    omission — the same reasoning that gave proposal creation one seam.
    """
    from central_command.runtime.durable import classify_deferred

    class _Call:
        def __init__(self, name):
            self.tool_name = name

    assert classify_deferred(_Call("propose_action")) == "proposal"
    assert classify_deferred(_Call("propose_litellm_change")) == "proposal"
    assert classify_deferred(_Call("ask_operator")) == "ask"
    assert classify_deferred(_Call("submit_plan")) == "project"
    assert classify_deferred(_Call("assign_work")) == "project"
    assert classify_deferred(_Call("something_new")) == "unknown"


def test_every_taskable_agent_can_ask():
    """A teammate that cannot say "I don't understand" can only guess."""
    from central_command.runtime import packs

    for agent_id in ("inbox-triage", "jira-expert", "litellm-manager"):
        assert "ask-operator" in packs.DEFAULT_PACKS[agent_id]
    # The orchestrator already holds ask_operator via `orchestrate`; granting a
    # second copy would be two packs claiming one tool.
    assert "ask-operator" not in packs.DEFAULT_PACKS["orchestrator"]
    assert "ask_operator" in packs.PACKS["orchestrate"].tool_names


def test_the_tool_offers_a_channel_that_now_exists():
    """The tier choice was deliberately withheld while tier 3 was unbuilt — a
    tool advertising a channel nothing implements is the failure this codebase
    keeps finding. Tier 3 exists now, so the parameter is back AND the thing it
    names is real: `conclude_discussion` is a tool, and the classifier routes it.
    """
    import inspect

    from central_command.runtime import packs
    from central_command.runtime.durable import classify_deferred
    from central_command.runtime.tools import ask_operator, conclude_discussion

    params = inspect.signature(ask_operator).parameters
    assert "needs_discussion" in params and "why" in params

    class _Call:
        tool_name = "conclude_discussion"

    assert classify_deferred(_Call()) == "conclude"
    assert conclude_discussion is not None
    # An agent that can ask for a discussion can end one.
    assert "conclude_discussion" in packs.PACKS["ask-operator"].tool_names


@needs_pg
async def test_an_ambiguous_task_asks_and_the_answer_resumes_it():
    """The full loop: ask → paused run + operator item → answer → resume.

    The answer is returned to the deferred call VERBATIM — it is trusted
    operator input, and no agent-authored paraphrase sits between the
    operator's words and the work.
    """
    from central_command.runtime.questions import resume_with_answer

    model = make_deferred_tool_model(
        "ask_operator", {"question": "TASKS-12 or TASKS-13?"}
    )
    out = await run_task("jira-expert", "Update the issue.", model=model)

    # Parked on a question, NOT parsed as a proposal.
    assert out["asked"] is True
    assert "proposal_id" not in out
    assert out["question"] == "TASKS-12 or TASKS-13?"

    item = await repo.get_operator_item(out["item_id"])
    assert item["kind"] == "question" and item["status"] == "OPEN"
    assert item["agent_id"] == "jira-expert"
    assert await repo.load_paused_session(out["session_id"]) is not None

    # The ask is on the record, which is what makes it coachable.
    asked = [e for e in await repo.list_events_of_kinds(["agent.asked"])
             if e["ref_id"] == out["item_id"]]
    assert len(asked) == 1
    assert asked[0]["actor"] == "agent:jira-expert"

    # Answering resumes the run; the stub model closes out in text.
    await repo.answer_operator_item(out["item_id"], "TASKS-12", None)
    resumed = await resume_with_answer(out["item_id"], "TASKS-12", model=model)
    assert resumed["deferred"] is False
    assert await repo.session_status(out["session_id"]) == "DONE"


@needs_pg
async def test_a_discussion_opens_seeded_with_the_question_and_unblocks_the_task(
    monkeypatch,
):
    """Tier 3, end to end.

    The agent judges a single answer would not unblock it, so a clarification
    CONVERSATION opens — already showing its question, because a chat that
    opens empty makes the operator hunt for what they were asked. Concluding it
    hands a summary back to the paused task, re-checked against what the
    operator actually said.
    """
    from central_command.db import repo as _repo
    from central_command.runtime.durable import simplify_messages
    from central_command.runtime.questions import conclude_discussion

    resumed: dict = {}

    async def fake_resume(item_id, answer):
        resumed["item_id"] = item_id
        resumed["answer"] = answer

    from central_command.api import orchestration

    monkeypatch.setattr(orchestration, "_resume_task_question", fake_resume)

    model = make_deferred_tool_model("ask_operator", {
        "question": "Should this epic be split, or tracked as one issue?",
        "needs_discussion": True,
        "why": "the answer changes how I structure everything after it",
    })
    out = await run_task("jira-expert", "Restructure the epic.", model=model)

    assert out["tier"] == "discussion"
    disc = out["discussion_session_id"]
    assert disc

    # The conversation opens showing the agent's question, not empty.
    seeded = simplify_messages(await _repo.get_session_run_state(disc) or {})
    assert any("Should this epic be split" in t["content"] for t in seeded)
    assert any("the answer changes how I structure" in t["content"] for t in seeded)
    assert await _repo.session_status(disc) == "AWAITING_OPERATOR"

    # The item knows its discussion, and the task's own run stays paused.
    item = await _repo.get_operator_item(out["item_id"])
    assert item["discussion_session_id"] == disc
    assert await _repo.load_paused_session(out["session_id"]) is not None

    # The operator answers in the conversation.
    await _repo.update_session_run_state(disc, {
        "messages": (await _repo.get_session_run_state(disc))["messages"] + [
            {"kind": "request", "parts": [
                {"part_kind": "user-prompt", "content": "Split it into three issues."}
            ]},
        ],
    })

    done = await conclude_discussion(disc, "Split it into three issues.", "jira-expert")

    assert done["claim_matches_source"] is True, "a faithful summary verifies"
    assert await _repo.session_status(disc) == "DONE"
    assert (await _repo.get_operator_item(out["item_id"]))["status"] == "ANSWERED"
    assert resumed["item_id"] == out["item_id"]


# --- the resumed run that reaches for `conclude_discussion` (2026-07-30) -------
# Observed live on the EA's first digest run (sess_bbc8e24e7324): the resumed
# run believed it had opened a discussion lane, so it deferred
# `conclude_discussion`. `resume_with_answer` classified deferrals as
# "proposal" or "ask" ONLY, so the call fell to the close-out branch — which
# stored `str(DeferredToolRequests(calls=[...]))` as the task outcome and told
# the agent nothing at all.


def _ask_then(tool_name: str, args: dict, ask_args: dict | None = None):
    """Ask first; on the resume reach for `tool_name`; then close out in text.

    The transcript is the only state — the same discriminator every stand-in
    here uses, so the branching is the model's, not the test's.
    """
    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
    from pydantic_ai.models.function import AgentInfo, FunctionModel

    asked_with = {"question": "TASKS-12 or TASKS-13?", **(ask_args or {})}

    def _respond(messages, info: AgentInfo) -> ModelResponse:
        called = {
            p.tool_name
            for m in messages
            for p in getattr(m, "parts", [])
            if isinstance(p, ToolCallPart)
        }
        if "ask_operator" not in called:
            return ModelResponse(parts=[
                ToolCallPart(tool_name="ask_operator", args=asked_with),
            ])
        if tool_name not in called:
            return ModelResponse(parts=[ToolCallPart(tool_name=tool_name, args=args)])
        return ModelResponse(parts=[TextPart(content="Closing out.")])

    return FunctionModel(_respond)


@needs_pg
async def test_a_conclude_with_no_lane_is_denied_and_the_run_closes_in_text():
    """The observed case: nothing to conclude, so the agent is TOLD that and
    the run continues to a real ending — never a stored deferral repr."""
    from central_command.runtime.questions import resume_with_answer

    model = _ask_then("conclude_discussion", {"summary": "We agreed on TASKS-12."})
    out = await run_task("jira-expert", "Update the issue.", model=model)
    assert out["asked"] is True and out.get("discussion_session_id") is None

    await repo.answer_operator_item(out["item_id"], "TASKS-12", None)
    resumed = await resume_with_answer(out["item_id"], "TASKS-12", model=model)

    assert resumed["deferred"] is False
    assert resumed["output"] == "Closing out."
    assert "DeferredToolRequests" not in resumed["output"]
    assert await repo.session_status(out["session_id"]) == "DONE"


@needs_pg
async def test_a_conclude_with_a_real_lane_concludes_it(monkeypatch):
    """The other half: the run DID hold a lane (the operator answered from the
    Inbox without the lane being closed), so the conclusion is honoured through
    the one conclude seam — lane terminal, record written, task session DONE."""
    from central_command.runtime.questions import resume_with_answer

    resumed_again: list = []

    async def _fake_resume(item_id, answer):  # a lane conclude may re-resume
        resumed_again.append((item_id, answer))

    from central_command.api import orchestration

    monkeypatch.setattr(orchestration, "_resume_task_question", _fake_resume)

    model = _ask_then(
        "conclude_discussion", {"summary": "Split it into three."},
        ask_args={"question": "Split the epic, or track it as one?",
                  "needs_discussion": True,
                  "why": "the answer will raise the next one"},
    )
    out = await run_task("jira-expert", "Restructure the epic.", model=model)
    lane = out["discussion_session_id"]
    assert lane, "tier 3 must have opened a lane for this test to mean anything"

    await repo.answer_operator_item(out["item_id"], "Split it into three.", None)
    since = await repo.latest_event_id()
    resumed = await resume_with_answer(
        out["item_id"], "Split it into three.", model=model
    )

    assert resumed["deferred"] is False
    assert resumed["concluded_discussion_session_id"] == lane
    assert resumed["output"] == "Split it into three."

    session = await repo.get_session(lane)
    assert session["status"] == "DONE" and session["closed_reason"] == "concluded"
    concluded = [e for e in await repo.list_events(since_id=since)
                 if e["kind"] == "agent.discussion_concluded"
                 and e["ref_id"] == out["item_id"]]
    assert len(concluded) == 1
    assert concluded[0]["payload"]["discussion_session_id"] == lane
    assert await repo.session_status(out["session_id"]) == "DONE"
    # The item was ANSWERED before this resume, so the conclude seam must not
    # schedule a second resume of the same task.
    assert resumed_again == []


@needs_pg
async def test_a_proposal_on_the_resumed_run_is_still_parked(monkeypatch):
    """Regression: the other two endings are untouched by the conclude branch."""
    from central_command.runtime.questions import resume_with_answer
    from central_command.runtime.spike_model import _PROPOSAL

    monkeypatch.setattr(settings, "dispatch_approval_limit", 999, raising=False)
    model = _ask_then("propose_action", {"proposal": _PROPOSAL})
    out = await run_task("jira-expert", "Update the issue.", model=model)

    await repo.answer_operator_item(out["item_id"], "TASKS-12", None)
    resumed = await resume_with_answer(out["item_id"], "TASKS-12", model=model)

    assert resumed["deferred"] is True and resumed["proposal_id"]
    assert (await repo.load_proposal(resumed["proposal_id"]))["status"] == "AWAITING_HUMAN"
    assert await repo.session_status(out["session_id"]) == "AWAITING_HUMAN"


@needs_pg
async def test_a_second_question_on_the_resumed_run_is_still_parked():
    """Regression: answering one question can raise the next one."""
    from pydantic_ai.messages import ModelResponse, ToolCallPart
    from pydantic_ai.models.function import FunctionModel

    from central_command.runtime.questions import resume_with_answer

    def _always_ask(messages, info):
        return ModelResponse(parts=[
            ToolCallPart(tool_name="ask_operator", args={"question": "A or B?"}),
        ])

    asking = FunctionModel(_always_ask)
    out = await run_task("jira-expert", "Do the thing.", model=asking)
    await repo.answer_operator_item(out["item_id"], "A", None)
    again = await resume_with_answer(out["item_id"], "A", model=asking)

    assert again["asked"] is True and again["item_id"] != out["item_id"]
    assert (await repo.get_operator_item(again["item_id"]))["status"] == "OPEN"


@needs_pg
async def test_a_summary_the_operator_never_said_is_flagged():
    """The summary is the agent's account of the operator's words, so it rides
    the same re-check as a redraft's quote. Advisory, never blocking.

    Stated residual: this validates CITATION, not comprehension — a faithful
    quote of a MISREAD conversation still passes.
    """
    from central_command.db import repo as _repo
    from central_command.runtime.questions import conclude_discussion

    model = make_deferred_tool_model("ask_operator", {
        "question": "A or B?", "needs_discussion": True, "why": "unclear",
    })
    out = await run_task("jira-expert", "Do the thing.", model=model)
    disc = out["discussion_session_id"]

    await _repo.update_session_run_state(disc, {
        "messages": (await _repo.get_session_run_state(disc))["messages"] + [
            {"kind": "request", "parts": [
                {"part_kind": "user-prompt", "content": "Go with A."}
            ]},
        ],
    })

    done = await conclude_discussion(
        disc, "The operator authorised deleting the project.", "jira-expert"
    )
    assert done["claim_matches_source"] is False


@needs_pg
async def test_the_lane_exchange_rides_the_resume_not_just_the_answer_field():
    """An operator who discusses in the lane and concludes with a short line
    has SAID more than the answer field holds. Observed 2026-08-13: a
    five-question check-in lane held the full answers, the concluding line was
    one sentence, and the resumed agent — fed only that sentence — judged the
    check-in incomplete and never recorded it.
    """
    from central_command.db import repo as _repo
    from central_command.runtime.questions import lane_exchange, resume_with_answer

    assert await lane_exchange({"discussion_session_id": None}) == ""

    model = make_deferred_tool_model("ask_operator", {
        "question": "How did the week go?", "needs_discussion": True,
        "why": "five questions need real answers",
    })
    out = await run_task("jira-expert", "Run the check-in.", model=model)
    disc = out["discussion_session_id"]

    await _repo.update_session_run_state(disc, {
        "messages": (await _repo.get_session_run_state(disc))["messages"] + [
            {"kind": "request", "parts": [
                {"part_kind": "user-prompt",
                 "content": "All sessions happened; Barley needs direction."}
            ]},
        ],
    })

    from pydantic_ai.messages import ModelResponse, TextPart, ToolReturnPart
    from pydantic_ai.models.function import FunctionModel

    seen: list[str] = []

    def _respond(messages, info) -> ModelResponse:
        seen.extend(
            str(p.content) for m in messages
            for p in getattr(m, "parts", [])
            if isinstance(p, ToolReturnPart)
        )
        return ModelResponse(parts=[TextPart(content="Recorded.")])

    await _repo.answer_operator_item(out["item_id"], "Yes, all good.", None)
    resumed = await resume_with_answer(out["item_id"], "Yes, all good.",
                                       model=FunctionModel(_respond))
    assert resumed["deferred"] is False

    returned = "\n".join(seen)
    assert "Yes, all good." in returned
    assert "operator: All sessions happened; Barley needs direction." in returned
