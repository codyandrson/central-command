"""Rejection feedback tests.

The property that matters: **operator feedback is trusted input, and a redraft
made against it must land back in the inbox — never vanish.** Before this
existed, `reject_with_feedback` discarded whatever the resumed agent produced:
a redrafted proposal had nowhere to go, and the session sat in AWAITING_HUMAN
forever. Driven by a deterministic redraft model, so no API key is needed.
"""

from __future__ import annotations

import pytest

from central_command.config import settings
from central_command.db import repo
from central_command.gateway import gateway
from central_command.runtime.run import ingest_and_propose
from central_command.runtime.spike_model import make_redraft_model, make_spike_model
from tests.conftest import needs_pg

FEEDBACK = "Wrong — I spoke to Dana, make it 2026-08-10."


pytestmark = needs_pg


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    monkeypatch.setattr(settings, "executor_mode", "dry_run")  # never touch real Jira
    monkeypatch.setattr(settings, "demo_mode", True)


async def test_reject_with_instruction_persists_a_redraft():
    model = make_redraft_model()
    run = await ingest_and_propose("Dana: DEMO-1 slipped, due Aug 3.", model=model)
    assert run["deferred"]

    out = await gateway.reject_with_feedback(
        run["session_id"], run["proposal_id"], FEEDBACK, model=model
    )

    # The redraft is a real proposal row awaiting its own review …
    redraft_id = out["redraft_proposal_id"]
    row = await repo.load_proposal(redraft_id)
    assert row is not None
    assert row["status"] == "AWAITING_HUMAN"
    assert row["actions"][0]["arguments"]["due_date"] == "2026-08-10"
    # … grounded in the operator's word, not an email — and the agent's account
    # of that word is itself verified, never trusted. The control plane stamps
    # the authoritative pointer (the decided event holding the feedback verbatim)
    # and checks the quoted claim against it.
    ev = row["evidence"][0]
    assert ev["kind"] == "operator"
    assert ev["source_ref"].startswith("event:")
    assert ev["claim_matches_source"] is True
    decided = await repo.get_event(int(ev["source_ref"][6:]))
    assert decided["kind"] == "proposal.decided"
    assert decided["payload"]["feedback"] == FEEDBACK

    # A redraft announces itself like any other proposal, keeping the link back
    # to what it replaces — the context had to survive the move to the one park
    # path (`runtime/proposals.park_proposal`, 2026-07-25).
    created = [
        e for e in await repo.list_events_of_kinds(["proposal.created"])
        if e["ref_id"] == redraft_id
    ]
    assert len(created) == 1
    assert created[0]["payload"]["redraft_of"] == run["proposal_id"]

    # The original is rejected, and the session is parked again, not done.
    original = await repo.load_proposal(run["proposal_id"])
    assert original["status"] == "REJECTED"
    assert await repo.session_status(run["session_id"]) == "AWAITING_HUMAN"

    # The loop closes: approving the redraft executes it and completes the session.
    result = await gateway.approve_and_execute(run["session_id"], redraft_id, model=model)
    assert "2026-08-10" in result["result_text"]
    assert (await repo.load_proposal(redraft_id))["status"] == "EXECUTED"
    assert await repo.session_status(run["session_id"]) == "DONE"


async def test_hallucinated_operator_claim_is_flagged():
    """An agent inventing what the operator said must be caught: the claim is
    checked against the recorded feedback, and a fabricated one is flagged for
    the reviewer (not silently trusted)."""
    model = make_redraft_model(claim="The operator asked to move it to 2026-09-01.")
    run = await ingest_and_propose("Dana: DEMO-1 slipped, due Aug 3.", model=model)

    out = await gateway.reject_with_feedback(
        run["session_id"], run["proposal_id"], FEEDBACK, model=model
    )

    ev = (await repo.load_proposal(out["redraft_proposal_id"]))["evidence"][0]
    assert ev["claim_matches_source"] is False


async def test_reject_without_redraft_completes_the_session():
    model = make_spike_model()  # never redrafts — answers in text after a denial
    run = await ingest_and_propose("Dana: DEMO-1 slipped, due Aug 3.", model=model)
    assert run["deferred"]
    before = len((await repo.get_session_run_state(run["session_id"]))["messages"])

    out = await gateway.reject_with_feedback(
        run["session_id"], run["proposal_id"], "Not needed after all.", model=model
    )

    assert out.get("redraft_proposal_id") is None
    assert isinstance(out["final_output"], str)
    assert await repo.session_status(run["session_id"]) == "DONE"
    # The closing turn SURVIVES: this landing wrote no transcript at all, so
    # the stored history ended at the original propose call and the operator
    # read a session that never answered them (2026-08-26).
    after = (await repo.get_session_run_state(run["session_id"]))["messages"]
    assert len(after) > before


def test_framed_denial_carries_the_words_and_both_branches():
    from central_command.runtime.resume_park import framed_denial

    text = framed_denial(FEEDBACK)
    assert FEEDBACK in text          # verbatim, never paraphrased
    assert "propose_action" in text  # branch 1: redraft
    assert "NOT NEEDED" in text      # branch 2: stand down


async def test_the_resumed_agent_is_told_what_a_rejection_means():
    """Raw feedback with no framing let a drafting prompt's "exactly as given"
    outrank the operator (2026-08-26). The denial the model reads must name
    both branches — while the RECORD keeps the verbatim words, because
    `_redraft_evidence` re-checks the agent's quote against them."""
    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
    from pydantic_ai.models.function import AgentInfo, FunctionModel

    from central_command.runtime.spike_model import _PROPOSAL

    seen: list[str] = []

    def _respond(messages, info: AgentInfo) -> ModelResponse:
        returned = {
            part.tool_name
            for message in messages
            for part in getattr(message, "parts", [])
            if isinstance(part, ToolReturnPart)
        }
        if "propose_action" not in returned:
            return ModelResponse(parts=[
                ToolCallPart(tool_name="propose_action", args={"proposal": _PROPOSAL}),
            ])
        seen.append("\n".join(
            str(getattr(part, "content", ""))
            for message in messages for part in getattr(message, "parts", [])
        ))
        return ModelResponse(parts=[TextPart(content="Understood.")])

    model = FunctionModel(_respond)
    run = await ingest_and_propose("Dana: DEMO-1 slipped, due Aug 3.", model=model)
    out = await gateway.reject_with_feedback(
        run["session_id"], run["proposal_id"], FEEDBACK, model=model
    )

    assert seen, "the resumed model was never called"
    assert FEEDBACK in seen[0]
    assert "REJECTED" in seen[0] and "NOT NEEDED" in seen[0]

    decided = next(
        e for e in await repo.list_events_of_kinds(["proposal.decided"])
        if e["ref_id"] == run["proposal_id"]
    )
    assert decided["payload"]["feedback"] == FEEDBACK  # RAW on the record
    assert out.get("redraft_proposal_id") is None


def test_charter_tells_the_agent_todays_date():
    """Supervised test #2: with no stated 'today', real Claude resolved 'Aug 10'
    to 2024 — a past date — and it survived both machine checks and human review.
    The agent must always be told what day it is."""
    from datetime import date

    from central_command.runtime.agent import build_charter

    assert f"Today's date is {date.today().isoformat()}" in build_charter()


async def test_rejecting_twice_is_refused():
    model = make_spike_model()
    run = await ingest_and_propose("Dana: DEMO-1 slipped, due Aug 3.", model=model)

    await gateway.reject_with_feedback(
        run["session_id"], run["proposal_id"], "No.", model=model
    )
    with pytest.raises(gateway.GatewayError):
        await gateway.reject_with_feedback(
            run["session_id"], run["proposal_id"], "No again.", model=model
        )


async def test_a_non_proposal_deferral_on_the_reject_path_resolves_mechanically():
    """The resumed agent holds its FULL toolset, so it can reach for
    `ask_operator` mid-redraft. Both decision paths used to hand ANY deferral to
    `proposal_from_call`, which tried to parse a question as a Proposal and
    raised out of the gateway (recorded 2026-07-30; consult slice 2 widened the
    exposure by resuming specialists here). It must be answered mechanically and
    the run allowed to continue — the same idiom converse/run_task use."""
    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
    from pydantic_ai.models.function import AgentInfo, FunctionModel

    from central_command.runtime.spike_model import _PROPOSAL

    def _respond(messages, info: AgentInfo) -> ModelResponse:
        returned = {
            part.tool_name
            for message in messages
            for part in getattr(message, "parts", [])
            if isinstance(part, ToolReturnPart)
        }
        if "propose_action" not in returned:
            return ModelResponse(parts=[
                ToolCallPart(tool_name="propose_action",
                             args={"proposal": _PROPOSAL}),
            ])
        if "ask_operator" not in returned:
            return ModelResponse(parts=[
                ToolCallPart(tool_name="ask_operator",
                             args={"question": "Which date did Dana mean?"}),
            ])
        return ModelResponse(parts=[TextPart(content="Understood — standing down.")])

    model = FunctionModel(_respond)
    run = await ingest_and_propose("Dana: DEMO-1 slipped, due Aug 3.", model=model)

    out = await gateway.reject_with_feedback(
        run["session_id"], run["proposal_id"], FEEDBACK, model=model
    )

    # No exception, no mis-parsed question, and the run got to close out.
    assert out.get("redraft_proposal_id") is None
    assert out["final_output"] == "Understood — standing down."
    assert await repo.session_status(run["session_id"]) == "DONE"


async def test_the_close_out_note_carries_the_unanswerable_question():
    """When the resumed agent keeps reaching for `ask_operator` past the
    mechanical-resolution limit, the closing note named only the tool — the
    question TEXT was discarded (recorded 2026-08-05, stopgap built same day).
    The operator reading the close-out deserves what was asked."""
    from pydantic_ai.messages import ModelResponse, ToolCallPart, ToolReturnPart
    from pydantic_ai.models.function import AgentInfo, FunctionModel

    from central_command.runtime.spike_model import _PROPOSAL

    question = "Which date did Dana actually mean?"

    def _respond(messages, info: AgentInfo) -> ModelResponse:
        returned = {
            part.tool_name
            for message in messages
            for part in getattr(message, "parts", [])
            if isinstance(part, ToolReturnPart)
        }
        if "propose_action" not in returned:
            return ModelResponse(parts=[
                ToolCallPart(tool_name="propose_action",
                             args={"proposal": _PROPOSAL}),
            ])
        # Never settles: reaches for the operator on every round.
        return ModelResponse(parts=[
            ToolCallPart(tool_name="ask_operator", args={"question": question}),
        ])

    model = FunctionModel(_respond)
    run = await ingest_and_propose("Dana: DEMO-1 slipped, due Aug 3.", model=model)

    out = await gateway.reject_with_feedback(
        run["session_id"], run["proposal_id"], FEEDBACK, model=model
    )

    assert out.get("redraft_proposal_id") is None
    assert question in out["final_output"]
    assert await repo.session_status(run["session_id"]) == "DONE"
