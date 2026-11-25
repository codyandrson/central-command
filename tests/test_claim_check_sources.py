"""The claim check's rules and sources (DL-230, 2026-11-22).

A read of the record found the "unverified quote" flag wrong more often than
right: quotes joined from verbatim passages with an ellipsis (or with nothing
between them), quotes from a mail the agent opened with `mail_read` (never in
the source set), operator quotes from an EARLIER answer in the same session
(checked against the latest feedback only), tool-built descriptions judged as
if they were quotes, and proposals parked after an operator answer with no
check at all. Every text here is synthetic.

  A1 — `contract.quote_supported`: ordered verbatim segments.
  A2 — the sources: mail the session's record shows it read; every operator
       word of the session; the after-question park.
  A3 — a tool-built description is not a quote.
  A4 — the field says what a claim is.
"""

from __future__ import annotations

import asyncio

import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from central_command import events
from central_command.config import settings
from central_command.contract import Evidence, Proposal, claim_supported, quote_supported
from central_command.db import repo
from central_command.runtime import tools
from central_command.runtime.proposals import (
    read_mail_sources,
    verified_email_evidence,
    verified_operator_evidence,
)
from central_command.runtime.spike_model import _PROPOSAL
from central_command.runtime.tools import LEGACY_MAIL_TEXT_MARKER, MAIL_TEXT_METADATA_KEY
from tests.conftest import needs_pg
from tests.fake_world import fake_world

SOURCE = (
    "Hello team. The invoice for October is attached. Please pay the balance "
    "by Friday the 14th. If you have questions, call the billing desk on "
    "weekdays. The invoice for October is final."
)


# --- A1: ordered verbatim segments ----------------------------------------------


def test_one_contiguous_passage_is_supported_whatever_its_length():
    assert quote_supported("Friday", SOURCE)  # one word: as today
    assert quote_supported("pay the balance", SOURCE)  # three words: as today
    assert quote_supported("Please pay the balance by Friday the 14th.", SOURCE)


@pytest.mark.parametrize("marker", ["...", "…", "[...]", "[…]", ". . ."])
def test_passages_joined_by_an_ellipsis_are_supported(marker):
    claim = f"The invoice for October is attached {marker} call the billing desk on weekdays"
    assert quote_supported(claim, SOURCE)
    assert not claim_supported(claim, SOURCE)  # the old checker refused it


def test_passages_run_together_with_nothing_between_are_supported():
    claim = "Please pay the balance by Friday the 14th call the billing desk on weekdays"
    assert quote_supported(claim, SOURCE)


def test_a_leading_or_trailing_ellipsis_is_only_a_marker():
    assert quote_supported("... Please pay the balance by Friday ...", SOURCE)


def test_segments_out_of_order_fail():
    claim = "call the billing desk on weekdays ... Please pay the balance by Friday"
    assert not quote_supported(claim, SOURCE)


def test_a_three_word_segment_fails():
    assert not quote_supported("The invoice for October is attached ... pay the balance", SOURCE)
    # Silently joined, the same: "pay the balance" cannot stand as a segment.
    assert not quote_supported("The invoice for October is attached pay the balance", SOURCE)


def test_invented_words_between_real_segments_fail():
    claim = (
        "Please pay the balance by Friday and do not worry about it "
        "call the billing desk on weekdays"
    )
    assert not quote_supported(claim, SOURCE)


def test_framing_words_and_a_paraphrase_fail():
    assert not quote_supported("The sender says: Please pay the balance by Friday", SOURCE)
    assert not quote_supported("The balance must be paid by Friday the 14th", SOURCE)


def test_a_phrase_twice_in_the_source_is_two_occurrences_never_one_used_twice():
    # Both occurrences, in source order.
    assert quote_supported(
        "The invoice for October is attached ... The invoice for October is final", SOURCE
    )
    # Reversed: the "final" occurrence is the later one.
    assert not quote_supported(
        "The invoice for October is final ... The invoice for October is attached", SOURCE
    )
    # One occurrence cannot serve as two segments.
    once = "Hello team. Please pay the balance by Friday."
    assert not quote_supported(
        "Please pay the balance by Friday ... Please pay the balance by Friday", once
    )


def test_segmentation_is_exact_not_greedy():
    """Greedy longest-first would take "a b c d e f" and leave "g h" (two
    words) stranded; the exact search finds "a b c d" + "e f g h"."""
    source = "a b c d e f x x x a b c d y y y e f g h"
    assert quote_supported("a b c d e f g h", source)


def test_the_work_is_bounded_and_fails_closed():
    """A pathological claim never runs long: past the search ceiling the
    answer is a flag (False), never a pass."""
    source = " ".join(["w"] * 4000)
    claim = " ".join(["w"] * 3000) + " z"
    assert quote_supported(claim, source) is False


def test_the_email_checker_uses_the_segment_rule():
    ev = Evidence(
        kind="email",
        source_ref="msg",
        locator="email",
        claim="The invoice for October is attached … call the billing desk on weekdays",
    )
    assert verified_email_evidence([ev], [SOURCE])[0]["claim_matches_source"] is True


# --- A2: the mail the session's own record shows it read ----------------------


def _returned(tool: str, content: str, call_id: str = "c1") -> dict:
    return {
        "kind": "request",
        "parts": [
            {
                "part_kind": "tool-return",
                "tool_name": tool,
                "content": content,
                "tool_call_id": call_id,
            },
        ],
    }


def _recorded(tool: str, tool_return) -> list:
    """The session record a real agent run writes when `tool` answers
    `tool_return`: the framework, not the test, builds the tool-return part,
    and it goes through the stored JSON form and back."""
    from pydantic_ai import Agent
    from pydantic_ai.messages import ModelMessagesTypeAdapter

    def model(messages, info: AgentInfo):
        if len(messages) == 1:
            return ModelResponse(parts=[ToolCallPart(tool, {}, tool_call_id="c1")])
        return ModelResponse(parts=[TextPart("done")])

    agent = Agent(FunctionModel(model))
    agent.tool_plain(name=tool)(lambda: tool_return)
    stored = ModelMessagesTypeAdapter.dump_python(agent.run_sync("go").all_messages(), mode="json")
    return ModelMessagesTypeAdapter.validate_python(stored)


def test_a_mail_read_return_counts_its_metadata_and_never_the_head():
    head = (
        "- wi_1 [PROCESSED] 2025-01-02 from billing@example.com: Invoice\n"
        "dismissal rationale: the agent wrote this reason itself\n"
        "audit: concur — the auditor wrote this"
    )
    record = _recorded("mail_read", tools._mail_return(head, SOURCE))
    texts = read_mail_sources(record)
    assert texts == [SOURCE]
    assert LEGACY_MAIL_TEXT_MARKER not in str(record)  # nothing writes the old form
    ev = [
        Evidence(kind="email", source_ref="wi_1", locator="mail_read", claim=c)
        for c in ("call the billing desk on weekdays", "the agent wrote this reason itself")
    ]
    out = verified_email_evidence(ev, texts)
    assert out[0]["claim_matches_source"] is True
    assert out[1]["claim_matches_source"] is False


def test_metadata_on_another_tool_or_an_empty_list_is_no_source():
    assert (
        read_mail_sources(_recorded("search_knowledge_graph", tools._mail_return("", SOURCE))) == []
    )
    # A mail tool's plain answer (an error line) records an empty list, and the
    # legacy reader is NOT consulted for it, even if the text had the marker.
    plain = tools.ToolReturn(
        return_value=f"{LEGACY_MAIL_TEXT_MARKER}\n{SOURCE}", metadata={MAIL_TEXT_METADATA_KEY: []}
    )
    assert read_mail_sources(_recorded("mail_read", plain)) == []


def test_a_parked_read_resumed_with_its_result_keeps_the_metadata():
    """The parked-read resume hands the re-run read's `ToolReturn` to the run as
    its deferred result (`api/orchestration.py`, `durable.deferred_results`):
    the framework records it with its metadata, through the stored form."""
    from pydantic_ai import Agent, CallDeferred, DeferredToolRequests, DeferredToolResults
    from pydantic_ai.messages import ModelMessagesTypeAdapter

    def model(messages, info: AgentInfo):
        if len(messages) == 1:
            return ModelResponse(parts=[ToolCallPart("mail_read", {}, tool_call_id="c1")])
        return ModelResponse(parts=[TextPart("done")])

    agent = Agent(FunctionModel(model), output_type=[str, DeferredToolRequests])

    @agent.tool_plain(name="mail_read")
    def parked() -> str:
        raise CallDeferred(metadata={"kind": "read"})

    adapter = ModelMessagesTypeAdapter
    paused = adapter.validate_python(
        adapter.dump_python(agent.run_sync("go").all_messages(), mode="json")
    )
    results = DeferredToolResults()
    results.calls["c1"] = tools._mail_return("head", SOURCE)
    resumed = agent.run_sync(message_history=paused, deferred_tool_results=results)
    stored = adapter.dump_python(resumed.all_messages(), mode="json")
    assert read_mail_sources(stored) == [SOURCE]


def test_a_clipped_read_counts_only_the_mail_the_agent_was_shown(monkeypatch):
    from central_command.runtime import context

    monkeypatch.setattr(context, "tool_result_ceiling", lambda: 60)
    ret = tools._mail_return("head line", SOURCE)
    (seen,) = ret.metadata[MAIL_TEXT_METADATA_KEY]
    assert ret.return_value.startswith("head line\n\n" + seen)
    assert SOURCE.startswith(seen) and len(seen) == 60 - len("head line\n\n")


def test_a_search_snippet_containing_the_line_separator_counts_whole(monkeypatch):
    """The snippet is the mail's own words, " — " and all: the old text reader
    split the line at its LAST " — " and kept only the tail."""
    from central_command.db import repo
    from central_command.integrations import email_facade

    snippet = "The balance — all of it — was received in full on the 12th."

    async def list_refs(query):
        return [{"uuid": "u7"}]

    async def items(message_ids):
        return []

    async def get_message(uuid):
        return {
            "date": "2025-01-03",
            "from": "billing@example.com",
            "to": "doe@example.com",
            "subject": "Re: Invoice",
            "snippet": snippet,
        }

    monkeypatch.setattr(email_facade, "list_refs", list_refs)
    monkeypatch.setattr(email_facade, "get_message", get_message)
    monkeypatch.setattr(repo, "mail_items_for_message_ids", items)
    ret = asyncio.run(tools.mail_search(None, "from:billing@example.com"))
    assert f"Re: Invoice — {snippet}" in ret.return_value
    texts = read_mail_sources(_recorded("mail_search", ret))
    assert texts == [snippet]
    ev = Evidence(
        kind="email",
        source_ref="u7",
        locator="mail_search",
        claim="The balance — all of it — was received in full",
    )
    assert verified_email_evidence([ev], texts)[0]["claim_matches_source"] is True


# LEGACY records (persisted before the mail text moved to metadata): the
# fallback reader still checks them, with the old rules.


def test_a_legacy_mail_read_return_counts_from_the_marker_on():
    content = (
        "- wi_1 [PROCESSED] 2025-01-02 from billing@example.com: Invoice\n"
        "dismissal rationale: the agent wrote this reason itself\n"
        "audit: concur — the auditor wrote this\n"
        f"{LEGACY_MAIL_TEXT_MARKER}\n{SOURCE}"
    )
    texts = read_mail_sources([_returned("mail_read", content)])
    assert texts == [SOURCE]
    ev = [
        Evidence(kind="email", source_ref="wi_1", locator="mail_read", claim=c)
        for c in ("call the billing desk on weekdays", "the agent wrote this reason itself")
    ]
    out = verified_email_evidence(ev, texts)
    assert out[0]["claim_matches_source"] is True
    # Agent-written text printed above the mail is not a source.
    assert out[1]["claim_matches_source"] is False


def test_only_a_recorded_tool_return_counts_never_the_agents_own_words():
    """The agent's tool CALL, its text, and its `source_ref` are its own
    words; only the framework's record of what the tool returned is proof."""
    messages = [
        {
            "kind": "response",
            "parts": [
                {
                    "part_kind": "tool-call",
                    "tool_name": "mail_read",
                    "args": {"ref": f"{LEGACY_MAIL_TEXT_MARKER} {SOURCE}"},
                    "tool_call_id": "c1",
                },
                {"part_kind": "text", "content": f"{LEGACY_MAIL_TEXT_MARKER}\n{SOURCE}"},
            ],
        },
        # A different tool's return, even with the marker in it, is not a mail read.
        _returned("search_knowledge_graph", f"{LEGACY_MAIL_TEXT_MARKER}\n{SOURCE}"),
        # A mail_read return with no marker (an error line) adds nothing.
        _returned("mail_read", "'wi_9' is not in the queue and the mailbox could not return it"),
    ]
    assert read_mail_sources(messages) == []


def test_a_mail_search_snippet_outside_the_queue_counts_and_queue_lines_do_not():
    content = (
        "2 message(s) match 'from:billing@example.com' in the mailbox; 1 of them are in the queue.\n"
        "- wi_1 [DISMISSED] 2025-01-02 from billing@example.com: Invoice — dismissed: “paid already”\n"
        "Not in the queue (1 of 1 shown):\n"
        "- uuid-7 [not in the queue] 2025-01-03 from billing@example.com to doe@example.com: "
        "Re: Invoice — Thanks, the balance was received in full on the 12th."
    )
    texts = read_mail_sources([_returned("mail_search", content)])
    assert texts == ["Thanks, the balance was received in full on the 12th."]


def test_pydantic_ai_message_objects_are_read_the_same_way():
    from pydantic_ai.messages import ModelRequest

    msgs = [
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="mail_read",
                    content=f"head\n{LEGACY_MAIL_TEXT_MARKER}\n{SOURCE}",
                    tool_call_id="c1",
                )
            ]
        )
    ]
    assert read_mail_sources(msgs) == [SOURCE]


def test_the_mail_read_attachment_page_carries_the_page_as_metadata():
    from central_command.runtime.tools import _mail_attachment_page

    row = {
        "id": "wi_1",
        "attachments": [{"name": "terms.txt", "status": "read", "reader": "text", "text": SOURCE}],
    }
    head, page = _mail_attachment_page(row, "terms.txt", 0)
    assert page == SOURCE and SOURCE not in head
    assert read_mail_sources(_recorded("mail_read", tools._mail_return(head, page))) == [SOURCE]


# --- A2: every operator word of the session -----------------------------------


WORDS = [
    {
        "source": "answer",
        "ref": "event:101",
        "text": "Use the second ticket for this one, the first is closed.",
    },
    {"source": "rejection", "ref": "event:102", "text": "Wrong date, make it the 20th."},
]


def test_an_operator_quote_from_an_earlier_answer_verifies_and_points_at_it():
    ev = Evidence(
        kind="operator",
        source_ref="operator:rejection-feedback",
        locator="x",
        claim="Use the second ticket for this one",
    )
    out = verified_operator_evidence([ev], WORDS)[0]
    assert out["claim_matches_source"] is True
    assert out["source_ref"] == "event:101"
    assert "answer" in out["locator"]


def test_an_operator_quote_found_nowhere_is_false_and_keeps_the_agents_pointer():
    ev = Evidence(
        kind="operator",
        source_ref="operator:answer",
        locator="x",
        claim="The operator wants the second ticket used",
    )
    out = verified_operator_evidence([ev], WORDS)[0]
    assert out["claim_matches_source"] is False
    assert out["source_ref"] == "operator:answer"


def test_no_operator_words_leaves_the_quote_not_checked():
    ev = Evidence(kind="operator", source_ref="operator:answer", locator="x", claim="anything")
    assert verified_operator_evidence([ev], [])[0].get("claim_matches_source") is None


# --- A3 / A4 ------------------------------------------------------------------


def test_the_claim_field_asks_for_a_copied_passage_not_an_assertion():
    desc = Evidence.model_json_schema()["properties"]["claim"]["description"].lower()
    assert "copied exactly" in desc
    assert "ellipsis" in desc
    assert "one evidence entry per passage" in desc
    assert "no summary" in desc and "words of your own" in desc
    assert "asserts" not in desc


def test_the_triage_charter_explains_the_same_rule():
    from central_command.runtime.agent import CHARTER, LAYER0

    for text in (CHARTER, LAYER0):
        low = text.lower()
        assert "copied exactly" in low and "ellipsis" in low
        assert "never a summary" in low


def test_tool_built_mail_descriptions_are_not_recorded_as_quotes():
    """`mail.report_spam` (and the other mailbox tools that describe a message
    by its headers) built "from X: subject" under `kind='email'`, which the
    checker judged as a quote and flagged. Every such entry is `mailbox` now —
    the `propose_bulk_dismiss` precedent — and an agent-written `email` entry
    is still checked."""
    import inspect

    from central_command.runtime import tools

    for fn in (tools.propose_report_spam, tools.propose_unsubscribe):
        src = inspect.getsource(fn)
        assert 'kind="mailbox"' in src and 'kind="email"' not in src
    assert 'kind="email"' not in inspect.getsource(tools)
    mailbox = {
        "kind": "mailbox",
        "source_ref": "u",
        "locator": "l",
        "claim": "from billing@example.com: Invoice",
    }
    assert verified_email_evidence([mailbox], [SOURCE])[0].get("claim_matches_source") is None


# --- the seams (database) -------------------------------------------------------


@pytest.fixture
async def demo(monkeypatch):
    # Pin the test model so a real key in .env cannot spend money here.
    monkeypatch.setattr(settings, "test_model", True)
    fake_world(monkeypatch)
    monkeypatch.setattr(settings, "dispatch_approval_limit", 10_000)
    monkeypatch.setattr(settings, "auditor_enabled", False)
    yield


def _raw(body: str, n: int) -> str:
    return (
        "\n".join(
            [
                f"Message-ID: <cc-claimsrc-{n}@example.com>",
                f"From: billing@example-claimsrc{n}.com",
                f"Subject: Claim source check {n}",
                "Date: Wed, 1 Jul 2026 09:00:00 +0000",
            ]
        )
        + f"\n\n{body}\n"
    )


def _reads_then_quotes(ref: str, claim: str) -> FunctionModel:
    """Open `ref` with mail_read, then propose quoting it; then finish."""
    proposal = {**_PROPOSAL, "evidence": [{**_PROPOSAL["evidence"][0], "claim": claim}]}

    def _respond(messages, info: AgentInfo) -> ModelResponse:
        called = [
            p.tool_name
            for m in messages
            for p in getattr(m, "parts", [])
            if isinstance(p, ToolCallPart)
        ]
        if "mail_read" not in called:
            return ModelResponse(parts=[ToolCallPart(tool_name="mail_read", args={"ref": ref})])
        if "propose_action" not in called:
            return ModelResponse(
                parts=[ToolCallPart(tool_name="propose_action", args={"proposal": proposal})]
            )
        return ModelResponse(parts=[TextPart(content="Done.")])

    return FunctionModel(_respond)


@needs_pg
async def test_a_quote_from_a_mail_the_agent_read_verifies_on_the_first_draft(demo):
    from central_command.ingest import ledger
    from central_command.runtime.run import ingest_and_propose

    other = await ledger.enroll_email(_raw(SOURCE, 1))
    run = await ingest_and_propose(
        "Dana: DEMO-1 slipped, due Aug 3.",
        model=_reads_then_quotes(other["item_id"], "call the billing desk on weekdays"),
    )
    ev = (await repo.load_proposal(run["proposal_id"]))["evidence"][0]
    assert ev["claim_matches_source"] is True


@needs_pg
async def test_a_quote_from_a_mail_the_agent_never_read_is_still_flagged(demo):
    from central_command.runtime.run import ingest_and_propose

    run = await ingest_and_propose(
        "Dana: DEMO-1 slipped, due Aug 3.",
        model=_reads_then_quotes("wi_does_not_exist", "call the billing desk on weekdays"),
    )
    ev = (await repo.load_proposal(run["proposal_id"]))["evidence"][0]
    assert ev["claim_matches_source"] is False


@needs_pg
async def test_a_redraft_quoting_an_earlier_answer_verifies_against_that_answer(demo):
    """The redraft path tried only the CURRENT rejection's feedback; the
    operator's earlier answer in the same session is their word too."""
    import uuid

    from central_command.gateway import gateway

    sid = "sess_claimsrc_" + uuid.uuid4().hex[:8]
    answered = await events.emit(
        "orchestration.item_answered",
        ref_id="item_claimsrc",
        payload={
            "kind": "question",
            "session_id": sid,
            "answer": "Use the second ticket for this one, the first is closed.",
        },
        actor="operator",
    )
    redraft = Proposal.model_validate(
        {
            **_PROPOSAL,
            "evidence": [
                {
                    "kind": "operator",
                    "source_ref": "operator:rejection-feedback",
                    "locator": "event log",
                    "claim": "Use the second ticket for this one",
                },
                {
                    "kind": "operator",
                    "source_ref": "operator:rejection-feedback",
                    "locator": "event log",
                    "claim": "make it the 20th",
                },
                {
                    "kind": "operator",
                    "source_ref": "operator:rejection-feedback",
                    "locator": "event log",
                    "claim": "the operator said to close both tickets",
                },
            ],
        }
    )
    out = await gateway._redraft_evidence(
        redraft,
        "rejected",
        "Wrong date, make it the 20th.",
        999_999,
        session_id=sid,
    )
    earlier, current, invented = out
    assert earlier["claim_matches_source"] is True
    assert earlier["source_ref"] == f"event:{answered['id']}"
    assert current["claim_matches_source"] is True
    assert current["source_ref"] == "event:999999"
    assert invented["claim_matches_source"] is False
    assert invented["source_ref"] == "event:999999"


@needs_pg
async def test_a_proposal_parked_after_an_operator_answer_is_checked(demo):
    """The after-question path parked with NO check: 35 proposals carried no
    verdict. It now goes through the same session check as every later leg."""
    from central_command.runtime.questions import resume_with_answer
    from central_command.runtime.run import run_task

    answer = "Use the second ticket for this one, the first is closed."
    proposal = {
        **_PROPOSAL,
        "evidence": [
            {
                "kind": "operator",
                "source_ref": "operator:answer",
                "locator": "inbox item: ask_operator answer",
                "claim": "the first is closed",
            },
            {
                "kind": "operator",
                "source_ref": "operator:answer",
                "locator": "inbox item: ask_operator answer",
                "claim": "the operator prefers the second ticket",
            },
        ],
    }

    def _respond(messages, info: AgentInfo) -> ModelResponse:
        called = {
            p.tool_name
            for m in messages
            for p in getattr(m, "parts", [])
            if isinstance(p, ToolCallPart)
        }
        if "ask_operator" not in called:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="ask_operator",
                        args={"question": "Which ticket?", "searched": "nothing I hold records it"},
                    )
                ]
            )
        if "propose_action" not in called:
            return ModelResponse(
                parts=[ToolCallPart(tool_name="propose_action", args={"proposal": proposal})]
            )
        return ModelResponse(parts=[TextPart(content="Closing out.")])

    model = FunctionModel(_respond)
    out = await run_task("jira-expert", "Update the issue.", model=model)
    await repo.answer_operator_item(out["item_id"], answer, None)
    # What `api.orchestration.answer_item` writes before it resumes the run.
    answered = await events.emit(
        "orchestration.item_answered",
        ref_id=out["item_id"],
        payload={"kind": "question", "session_id": out["session_id"], "answer": answer},
        actor="operator",
    )
    resumed = await resume_with_answer(out["item_id"], answer, model=model)
    ev = (await repo.load_proposal(resumed["proposal_id"]))["evidence"]
    assert ev[0]["claim_matches_source"] is True
    assert ev[0]["source_ref"] == f"event:{answered['id']}"
    assert ev[1]["claim_matches_source"] is False
