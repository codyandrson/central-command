"""An email quote is re-checked against the mail the session was fed (2026-11-04).

The triage charter has asked for a verbatim email quote since v0, and AGENTS.md
says no agent claim is trusted unverified on every path that makes one — but
until v2.82.3 the mechanism was narrower than the rule: the redraft path checked
operator quotes, the coach checked citations, the steward checked document
quotes, and a triage proposal citing the email it was handling reached the
Inbox with `claim_matches_source` ABSENT, rendering as unremarkable.

Three seams, three guards: the pure checker (`proposals.verified_email_evidence`),
the first draft (`run.ingest_and_propose`, where the source is in hand because
the work item is linked to its session only after the run returns), and the
later legs (`gateway._recheck_against_session_sources`, through the link).
"""

from __future__ import annotations

import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from central_command.config import settings
from central_command.contract.models import Evidence
from central_command.db import repo
from central_command.runtime.proposals import verified_email_evidence
from central_command.runtime.spike_model import _PROPOSAL, make_spike_model
from tests.conftest import needs_pg
from tests.fake_world import fake_world

EMAIL = "Dana: DEMO-1 slipped, due Aug 3."
PARAPHRASE = "Sender states DEMO-1 deadline moves Jul 27 to Aug 3."


def _ev(kind: str, claim: str) -> Evidence:
    return Evidence(kind=kind, source_ref="gmail:msg_1846d2", locator="email", claim=claim)


# --- the pure checker (no database) -------------------------------------------


def test_a_verbatim_email_quote_verifies():
    out = verified_email_evidence([_ev("email", "DEMO-1 slipped, due Aug 3.")], [EMAIL])
    assert out[0]["claim_matches_source"] is True


def test_a_paraphrase_is_flagged():
    """A faithful paraphrase is still not a quote — the reviewer is told."""
    out = verified_email_evidence([_ev("email", PARAPHRASE)], [EMAIL])
    assert out[0]["claim_matches_source"] is False


def test_other_kinds_are_left_not_checked():
    """`None` means NOT CHECKED, never "checked and inconclusive" — judging a
    kind this checker cannot read would cry wolf."""
    out = verified_email_evidence(
        [_ev("jira_state", "DEMO-1 is open"), _ev("operator", "make it Friday")], [EMAIL]
    )
    assert all(e.get("claim_matches_source") is None for e in out)


def test_no_sources_leaves_the_flag_absent():
    """A session with no mail (a chat, a task) has nothing to check against."""
    out = verified_email_evidence([_ev("email", PARAPHRASE)], [])
    assert out[0].get("claim_matches_source") is None


def test_a_recorded_verdict_is_never_overwritten():
    """The gateway runs this over evidence another checker already judged."""
    already = {**_ev("email", "DEMO-1 slipped, due Aug 3.").model_dump(),
               "claim_matches_source": False}
    out = verified_email_evidence([already], [EMAIL])
    assert out[0]["claim_matches_source"] is False


def test_a_surveyed_siblings_words_are_a_source():
    """A folded sibling's words are legitimately quotable (the charter says
    "cite every folded email as evidence")."""
    out = verified_email_evidence(
        [_ev("email", "actually Friday")], [EMAIL, "Re: moved to Thursday. Actually Friday."]
    )
    assert out[0]["claim_matches_source"] is True


def test_each_message_is_checked_on_its_own():
    """The `_event_source_texts` lesson: a quote must not span two messages."""
    out = verified_email_evidence(
        [_ev("email", "due Aug 3. Re: moved")], [EMAIL, "Re: moved to Thursday."]
    )
    assert out[0]["claim_matches_source"] is False


# --- the seams (database) ------------------------------------------------------


pytestmark_db = needs_pg


@pytest.fixture
async def demo(monkeypatch):
    # Pin the test model so a real key in .env cannot spend money here.
    monkeypatch.setattr(settings, "test_model", True)
    fake_world(monkeypatch)
    monkeypatch.setattr(settings, "dispatch_approval_limit", 10_000)
    monkeypatch.setattr(settings, "auditor_enabled", False)
    yield


def _triage_model(claim: str, *, redraft_claim: str | None = None) -> FunctionModel:
    """The spike model's shape with the email claim under test control: propose
    once; on a rejection naming 2026-08-10 (only when `redraft_claim` is set),
    redraft re-citing the MAIL; once executed or otherwise, finish in text."""
    first = {**_PROPOSAL, "evidence": [{**_PROPOSAL["evidence"][0], "claim": claim}]}
    redraft = {
        **_PROPOSAL,
        "intent": "Set the due date on DEMO-1 to Aug 10 — per the operator's feedback.",
        "actions": [{**_PROPOSAL["actions"][0],
                     "arguments": {"issue_key": "DEMO-1", "due_date": "2026-08-10"}}],
        "evidence": [{**_PROPOSAL["evidence"][0], "claim": redraft_claim or claim}],
        "expected_effect": "DEMO-1.duedate == 2026-08-10",
    }

    def _respond(messages, info: AgentInfo) -> ModelResponse:
        made = sum(
            isinstance(p, ToolCallPart) and p.tool_name == "propose_action"
            for m in messages for p in getattr(m, "parts", [])
        )
        if made == 0:
            return ModelResponse(parts=[ToolCallPart(tool_name="propose_action",
                                                     args={"proposal": first})])
        transcript = "\n".join(
            str(getattr(p, "content", ""))
            for m in messages for p in getattr(m, "parts", [])
        )
        if redraft_claim is not None and made == 1 and "2026-08-10" in transcript:
            return ModelResponse(parts=[ToolCallPart(tool_name="propose_action",
                                                     args={"proposal": redraft})])
        for m in messages:
            for p in getattr(m, "parts", []):
                if isinstance(p, ToolReturnPart) and p.tool_name == "propose_action":
                    return ModelResponse(parts=[TextPart(content=f"Done — {p.content}")])
        return ModelResponse(parts=[TextPart(content="Done — closing out.")])

    return FunctionModel(_respond)


@pytestmark_db
async def test_the_first_draft_records_a_verified_email_quote(demo):
    """The spike model's quote is verbatim from the fixture mail, so the suite
    exercises a live pairing — the same discipline `test_steward` keeps."""
    from central_command.runtime.run import ingest_and_propose

    run = await ingest_and_propose(EMAIL, model=make_spike_model())
    ev = (await repo.load_proposal(run["proposal_id"]))["evidence"][0]
    assert ev["kind"] == "email"
    assert ev["claim_matches_source"] is True


@pytestmark_db
async def test_a_paraphrased_email_quote_is_flagged_and_parks_an_override(demo):
    """A `false` verdict is what the Inbox shows as "unverified quote" and what
    `overrides.review_flags` parks on — an override never decides what a
    reviewer would have seen flagged."""
    from central_command.gateway import overrides
    from central_command.runtime.run import ingest_and_propose

    run = await ingest_and_propose(EMAIL, model=_triage_model(PARAPHRASE))
    row = await repo.load_proposal(run["proposal_id"])
    assert row["evidence"][0]["claim_matches_source"] is False
    # `review_flags` reports the FIRST reason it finds, and the fixture's Aug 3
    # due date trips the past-due policy first; move the date so the evidence
    # verdict is the only thing left to park on.
    action = row["actions"][0]
    future = {**row, "actions": [
        {**action, "arguments": {**action["arguments"], "due_date": "2099-01-01"}}
    ]}
    assert "does not match its recorded source" in await overrides.review_flags(future)


def _raw(body: str, n: int = 1) -> str:
    # Distinct sender domain AND subject per item: the relatedness hold parks
    # anything related (thread, subject, sender, shared entity key) to an item
    # still busy, and the first test's proposal is left awaiting the operator.
    return "\n".join([
        f"Message-ID: <cc-evidence-{n}@example.com>",
        f"From: dana@example{n}.com",
        f"Subject: Evidence check {n}",
        "Date: Wed, 1 Jul 2026 09:00:00 +0000",
    ]) + f"\n\n{body}\n"


@pytestmark_db
async def test_the_dispatcher_checks_against_the_stored_mail(demo):
    """Through the ledger the agent is shown a FRAMED prompt (recurrence
    context, pending-proposals note); the quote is checked against the stored
    message text itself, which the dispatcher passes on its own."""
    from central_command.ingest import dispatcher, ledger

    enrolled = await ledger.enroll_email(_raw(EMAIL, n=11))
    result = await dispatcher.dispatch_item(enrolled["item_id"], model=make_spike_model())
    assert result and result["deferred"] is True
    ev = (await repo.load_proposal(result["proposal_id"]))["evidence"][0]
    assert ev["claim_matches_source"] is True


@pytestmark_db
async def test_a_redraft_that_re_cites_the_mail_is_checked_through_the_session_link(demo):
    """The later legs cannot see the source in hand: the work item is linked to
    the session by then, and the gateway reads it through that link. A redraft
    after a Reject that paraphrases the mail is flagged exactly as a first
    draft would be."""
    from central_command.gateway import gateway
    from central_command.ingest import dispatcher, ledger

    # A different issue in the body than the first-draft test's, so the two
    # items share no entity key and the hold leaves this one free to claim.
    mail = "Dana: DEMO-2 slipped, due Aug 3."
    model = _triage_model("DEMO-2 slipped, due Aug 3.",
                          redraft_claim="Dana says DEMO-2's deadline is now August 3rd.")
    enrolled = await ledger.enroll_email(_raw(mail, n=12))
    result = await dispatcher.dispatch_item(enrolled["item_id"], model=model)
    assert result and result["deferred"] is True
    first = (await repo.load_proposal(result["proposal_id"]))["evidence"][0]
    assert first["claim_matches_source"] is True

    out = await gateway.reject_with_feedback(
        result["session_id"], result["proposal_id"],
        "Wrong — I spoke to Dana, make it 2026-08-10.", model=model,
    )
    redraft = await repo.load_proposal(out["redraft_proposal_id"])
    assert redraft["evidence"][0]["kind"] == "email"
    assert redraft["evidence"][0]["claim_matches_source"] is False
