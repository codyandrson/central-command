"""The email path parks an `ask_operator` deferral as a QUESTION, never a
Proposal.

The incident (2026-08-13 evening): inbox-triage's charter v9 invited it to ask
the operator when genuinely uncertain about a dismissal, and the agent has held
the ask-operator pack since 2026-07-25 — but `ingest_and_propose` classified
only "consult" deferrals and handed everything else to `proposal_from_call`.
The first borderline email raised `ValidationError: 4 validation errors for
Proposal`, the dispatcher retried a deterministic break three times, and four
emails parked FAILED. Same failure class `run_task` (2026-07-25) and the coach
each fixed on their own paths: "deferred = proposal" stops holding the moment a
second deferral kind reaches the call site.
"""

from __future__ import annotations

import pytest

from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from central_command.db import repo
from central_command.runtime.run import ingest_and_propose
from tests.conftest import needs_pg

pytestmark = needs_pg


def _asking_model() -> FunctionModel:
    def respond(messages, info):
        return ModelResponse(parts=[
            ToolCallPart(tool_name="ask_operator", args={
                "question": "Should I reply to this sender before dismissing it?",
                "why": "The charter says to ask when genuinely uncertain.",
            })
        ])

    return FunctionModel(respond)


async def test_an_ask_on_the_email_path_parks_a_question_not_a_proposal():
    run = await ingest_and_propose(
        "FW: insurance quote follow-up — no obvious Jira action.",
        model=_asking_model(),
    )

    assert run["deferred"] is True
    assert run.get("asked") is True
    assert "proposal_id" not in run

    # The question is in the operator's queue, tied to the paused session.
    item = await repo.get_operator_item(run["item_id"])
    assert item["kind"] == "question"
    assert item["session_id"] == run["session_id"]
    assert "before dismissing" in item["body"]


def _undrivable_model() -> FunctionModel:
    """A deferral tool the email path holds no driver for."""

    def respond(messages, info):
        return ModelResponse(parts=[
            ToolCallPart(tool_name="conclude_discussion", args={
                "summary": "wrapping up a discussion that does not exist",
            })
        ])

    return FunctionModel(respond)


async def test_an_undrivable_deferral_lands_for_review_instead_of_crashing():
    run = await ingest_and_propose(
        "FW: another borderline email.", model=_undrivable_model(),
    )

    # `deferred: False` routes the item to DISMISS_PENDING in the dispatcher —
    # parked in front of the operator, not crashed into the retry machinery.
    assert run["deferred"] is False
    assert "conclude_discussion" in run["output"]
