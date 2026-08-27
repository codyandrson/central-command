"""The coach path re-checks the agent's account of the operator's words.

The redraft path has one given source (the recorded rejection feedback) and the
gateway stamps the pointer itself. A coach run has neither: the agent supplies
BOTH the quote and the event id, across many curated signals. So the runtime
checks both — against the signals the operator actually fed the session, or
(since stage 5a) against the record itself if the coach read further.

Why it matters more here than anywhere else: a coached charter becomes standing
doctrine that every later run inherits, so a drifted quote outlives the proposal
that carried it. (Found 2026-07-25: `claim_matches_source` was None — meaning
NOT CHECKED — on every coach proposal, while the coach prompt told the agent
quotes were "re-checked mechanically".)

These are pure — no Postgres, no model — so they run in the offline suite
unconditionally. `repo.get_event` is stubbed below so this stays true even
though the checker now consults the record.
"""

from __future__ import annotations

import pytest

from central_command.contract import Evidence, claim_supported
from central_command.db import repo
from central_command.runtime.run import _cited_event_id, _verified_evidence

SIGNALS = [
    {"kind": "rejection", "event_id": 2453,
     "feedback": "There is already a jira task for this, why didn't you notice this already?"},
    {"kind": "reopen", "event_id": 2614,
     "feedback": "Do we already have a jira task for this? If so then dismiss, if not, "
                 "then create a jira task."},
    {"kind": "note", "event_id": 3935, "feedback": "Check for an existing link first."},
]


def _ev(claim: str, source_ref: str, kind: str = "operator") -> Evidence:
    return Evidence(kind=kind, source_ref=source_ref, locator="event log", claim=claim)


# The record the coach may have read past its selection into. Stubbed so this
# suite stays pure — no Postgres, no model, always in the offline run.
RECORD = {
    7788: {"id": 7788, "kind": "proposal.decided", "ref_id": "prop_x",
           "actor": "operator", "created_at": None,
           "payload": {"verdict": "rejected",
                       "feedback": "stop proposing dates you cannot source"}},
    9001: {"id": 9001, "kind": "work.folded", "ref_id": "prop_y",
           "actor": "system", "created_at": None, "payload": {"count": 3}},
    8100: {"id": 8100, "kind": "proposal.created", "ref_id": "prop_z",
           "actor": "agent:coach", "created_at": None,
           "payload": {"intent": "stop proposing dates you cannot source"}},
}


@pytest.fixture(autouse=True)
def stub_record(monkeypatch):
    async def _get_event(event_id: int):
        return RECORD.get(int(event_id))

    monkeypatch.setattr(repo, "get_event", _get_event)


async def test_a_selected_citation_is_marked_selected():
    out = await _verified_evidence([_ev(SIGNALS[0]["feedback"], "event:2453")], SIGNALS)
    assert out[0]["claim_matches_source"] is True
    assert out[0]["citation"] == "selected"
    assert "event:2453" in out[0]["locator"]


async def test_a_real_event_outside_the_selection_verifies_and_is_marked_discovered():
    """Decision 4: the coach has full read access, so citing something the
    operator did not tick is legitimate — and still checked, against the
    record. Verified as REAL, never as representative: the marker is what
    makes the reach visible at approval time."""
    out = await _verified_evidence(
        [_ev("stop proposing dates you cannot source", "event:7788")], SIGNALS
    )
    assert out[0]["claim_matches_source"] is True
    assert out[0]["citation"] == "discovered"
    assert "discovered" in out[0]["locator"]


async def test_an_operator_kind_citation_to_an_agent_authored_event_is_flagged():
    """Gate on WHO wrote the cited event, not just whether the quote matches
    some string in its payload. Event 8100 carries the exact same words as
    the genuine operator event 7788, but it was written by `agent:coach` —
    an agent quoting its own earlier text and labeling it `kind='operator'`
    must not verify, or the Inbox would show a fabricated operator citation."""
    out = await _verified_evidence(
        [_ev("stop proposing dates you cannot source", "event:8100")], SIGNALS
    )
    assert out[0]["claim_matches_source"] is False
    assert out[0]["citation"] == "unresolved"
    assert "agent:coach" in out[0]["locator"]
    assert "event:8100" in out[0]["locator"]


async def test_a_genuine_operator_authored_event_still_verifies_when_discovered():
    """The counterpart to the actor-gate test above: a REAL operator-authored
    event outside the curated selection still verifies True and is marked
    discovered — the gate must not become a blanket rejection of anything
    outside the signal set."""
    out = await _verified_evidence(
        [_ev("stop proposing dates you cannot source", "event:7788")], SIGNALS
    )
    assert out[0]["claim_matches_source"] is True
    assert out[0]["citation"] == "discovered"


async def test_a_paraphrase_of_a_discovered_event_still_flags():
    out = await _verified_evidence(
        [_ev("the operator dislikes unsourced dates", "event:7788")], SIGNALS
    )
    assert out[0]["claim_matches_source"] is False
    assert out[0]["citation"] == "discovered"


async def test_a_quote_cannot_verify_against_json_key_names():
    """`claim_supported` normalizes punctuation away, so serializing the payload
    once made key names matchable: "feedback stop proposing dates" verified
    against {"feedback": "stop proposing dates..."} though nobody wrote it.
    Each value is now checked on its own."""
    out = await _verified_evidence(
        [_ev("feedback stop proposing dates", "event:7788")], SIGNALS
    )
    assert out[0]["claim_matches_source"] is False
    assert out[0]["citation"] == "discovered"


async def test_a_quote_cannot_span_two_unrelated_payload_fields():
    out = await _verified_evidence(
        [_ev("rejected stop proposing dates", "event:7788")], SIGNALS
    )
    assert out[0]["claim_matches_source"] is False


async def test_an_event_with_no_text_payload_verifies_nothing():
    out = await _verified_evidence([_ev("anything at all", "event:9001")], SIGNALS)
    assert out[0]["claim_matches_source"] is False


async def test_a_citation_that_resolves_to_no_event_is_flagged_false():
    """The hallucination case: an event id that exists nowhere. A check RAN
    and the pointer resolved to nothing, so this is False — never None, which
    means NOT CHECKED and would render as unremarkable in the Inbox."""
    out = await _verified_evidence([_ev("Some feedback entirely", "event:1317")], SIGNALS)
    assert out[0]["claim_matches_source"] is False
    assert out[0]["citation"] == "unresolved"
    assert "does not exist" in out[0]["locator"]
    assert "event:1317" in out[0]["locator"]


async def test_an_unparseable_pointer_is_flagged_never_guessed():
    for ref in ("the event log", "", "event:abc"):
        out = await _verified_evidence([_ev("There is already a jira task", ref)], SIGNALS)
        assert out[0]["claim_matches_source"] is False, ref
        assert out[0]["citation"] == "unresolved", ref
        assert "UNVERIFIABLE" in out[0]["locator"], ref


async def test_quote_survives_punctuation_and_case_drift_but_not_paraphrase():
    # The check is normalized, not fuzzy — quoting loosely is fine, inventing is not.
    loose = await _verified_evidence(
        [_ev("THERE IS ALREADY A JIRA TASK FOR THIS", "event:2453")], SIGNALS
    )
    assert loose[0]["claim_matches_source"] is True

    paraphrase = await _verified_evidence(
        [_ev("The operator said we should always check Jira twice", "event:2453")], SIGNALS
    )
    assert paraphrase[0]["claim_matches_source"] is False


async def test_the_operators_own_note_is_checkable_like_any_other_signal():
    # The note is logged as agent.coach_requested BEFORE the run precisely so
    # the coached charter can cite it — so it must be checkable too.
    out = await _verified_evidence([_ev("Check for an existing link first.", "event:3935")], SIGNALS)
    assert out[0]["claim_matches_source"] is True


async def test_non_operator_evidence_is_left_alone():
    out = await _verified_evidence(
        [_ev("TASKS-17 relates to TASKS-4", "TASKS-17", kind="jira_state")], SIGNALS
    )
    assert "claim_matches_source" not in out[0]
    assert "citation" not in out[0]
    assert out[0]["locator"] == "event log"  # untouched


async def test_every_operator_entry_is_checked_not_just_the_first():
    out = await _verified_evidence(
        [
            _ev(SIGNALS[0]["feedback"], "event:2453"),
            _ev("a claim nobody made", "event:2614"),
            _ev(SIGNALS[2]["feedback"], "event:3935"),
        ],
        SIGNALS,
    )
    assert [e["claim_matches_source"] for e in out] == [True, False, True]


def test_cited_event_id_parsing():
    assert _cited_event_id("event:2453") == 2453
    assert _cited_event_id("event: 2453") == 2453
    assert _cited_event_id("event:2453, payload.feedback") == 2453
    assert _cited_event_id("2453") == 2453
    assert _cited_event_id("TASKS-17") is None
    assert _cited_event_id(None) is None


async def test_an_empty_claim_never_passes():
    # Normalizes to "" — which is a substring of everything. Guarded at source.
    assert claim_supported("", "anything at all") is False
    out = await _verified_evidence([_ev("", "event:2453")], SIGNALS)
    assert out[0]["claim_matches_source"] is False


async def test_none_is_never_written_by_this_checker():
    """`claim_matches_source: None` means NOT CHECKED — it is what the Inbox
    reads as 'nothing was verified'. Every operator entry this function
    touches must come out True or False."""
    out = await _verified_evidence(
        [_ev(SIGNALS[0]["feedback"], "event:2453"),
         _ev("invented", "event:99999"),
         _ev("stop proposing dates you cannot source", "event:7788")],
        SIGNALS,
    )
    assert [e["claim_matches_source"] for e in out] == [True, False, True]
    assert all(e["claim_matches_source"] is not None for e in out)


def test_both_tiers_share_one_checker():
    """The gateway and the runtime must never fork this logic — and the runtime
    may not import the gateway (the trust boundary), which is why it lives in
    `contract/`. If someone re-forks it, this fails."""
    from central_command.gateway import gateway as gw

    assert gw.claim_supported is claim_supported
    assert not hasattr(gw, "_claim_supported")


# --- what the prompt SHOWS must be what the checker ACCEPTS -------------------
# The first live coach run (2026-07-28) returned four citations, every one of
# them flagged unverified. The agent had not misbehaved: `_signal_line` rendered
# the context and the operator's words on one line in the same curly quotes, the
# prompt said "quote verbatim", and the agent quoted the line it was shown —
# while `_verified_evidence` only accepts a span contained in `feedback`.
#
# These tests bind the two halves together. If someone edits the rendering so a
# faithful quote of the labelled line no longer verifies, this fails here rather
# than silently turning the Inbox's amber badge into noise.

REJECTION = {
    "kind": "rejection", "event_id": 2736,
    "intent": "Re-register the three Claude aliases with added metadata fields",
    "feedback": ("You should add the new updated models first, only after they are "
                 "added successfully should you remove the old models."),
}


def _quotable_span(rendered: str) -> str:
    """The one span the prompt marks as quotable — the curly-quoted text on the
    line after the label. This mirrors what a compliant agent copies."""
    import re as _re
    tail = rendered.split("THE OPERATOR'S EXACT WORDS", 1)[1]
    return _re.search(r"“([^”]*)”", tail).group(1)


async def test_quoting_the_labelled_line_verifies():
    from central_command.runtime.run import _signal_line

    quoted = _quotable_span(_signal_line(0, REJECTION))
    assert quoted == REJECTION["feedback"], "the labelled span is not the feedback"

    out = await _verified_evidence([_ev(quoted, "event:2736")], [REJECTION])
    assert out[0]["claim_matches_source"] is True, (
        "an agent that quoted exactly what the prompt labelled as quotable was "
        "still flagged — the rendering and the checker have drifted apart"
    )
    assert out[0]["citation"] == "selected"


async def test_the_context_is_not_dressed_as_quotable():
    """The intent must not wear the same quote marks as the operator's words —
    that ambiguity is what produced the bad citations."""
    from central_command.runtime.run import _signal_line

    rendered = _signal_line(0, REJECTION)
    head = rendered.split("THE OPERATOR'S EXACT WORDS", 1)[0]
    assert REJECTION["intent"] in head, "context should still be shown"
    assert "“" not in head and "”" not in head, (
        "context is wrapped in the same curly quotes as the quotable span"
    )


async def test_quoting_the_whole_rendered_line_still_fails():
    """The old failure, kept as a guard: swallowing the context with the quote
    is not a faithful citation and must stay flagged."""
    from central_command.runtime.run import _signal_line

    out = await _verified_evidence(
        [_ev(_signal_line(0, REJECTION), "event:2736")], [REJECTION]
    )
    assert out[0]["claim_matches_source"] is False


# --- agent-authored signals (2026-08-01) --------------------------------------
# The picker offers more than rejections now: a task outcome, a declared gap, a
# deferred contact. They are the AGENT's words, so they are checkable material
# (`kind='record'`) but NOT operator instruction — citing one as `operator`
# must fail exactly like citing an agent-authored event off the record does.

TASK_SIGNAL = {
    "kind": "task-outcome", "event_id": 4242, "author": "agent",
    "intent": "task_9885f870fa04 — register the alias",
    "feedback": "Closing as done; still unclear which region the alias belongs to.",
}


async def test_a_record_signal_is_checked_against_its_source():
    out = await _verified_evidence(
        [_ev(TASK_SIGNAL["feedback"], "event:4242", kind="record")], [TASK_SIGNAL]
    )
    assert out[0]["claim_matches_source"] is True
    assert out[0]["citation"] == "selected"
    assert "task-outcome" in out[0]["locator"]


async def test_a_paraphrased_record_signal_still_flags():
    out = await _verified_evidence(
        [_ev("the agent was confused about regions", "event:4242", kind="record")],
        [TASK_SIGNAL],
    )
    assert out[0]["claim_matches_source"] is False


async def test_an_agent_authored_signal_cannot_be_cited_as_operator_input():
    """The selection is no longer all operator words, so the provenance gate the
    discovered path applies must apply to the selection too — otherwise the
    agent's own task outcome renders in the Inbox as an operator instruction."""
    out = await _verified_evidence(
        [_ev(TASK_SIGNAL["feedback"], "event:4242", kind="operator")], [TASK_SIGNAL]
    )
    assert out[0]["claim_matches_source"] is False
    assert out[0]["citation"] == "unresolved"
    assert "not operator input" in out[0]["locator"]


async def test_agent_authored_signals_render_a_quotable_span():
    from central_command.runtime.run import _signal_line

    for kind in ("task-outcome", "gap", "contact-deferred"):
        s = {**TASK_SIGNAL, "kind": kind}
        rendered = _signal_line(0, s)
        assert "THE OPERATOR'S EXACT WORDS" not in rendered, kind
        import re as _re
        quoted = _re.search(r"EXACT WORDS[^\n]*\n\s*“([^”]*)”", rendered).group(1)
        assert quoted == s["feedback"], kind
        out = await _verified_evidence([_ev(quoted, "event:4242", kind="record")], [s])
        assert out[0]["claim_matches_source"] is True, kind


async def test_every_signal_kind_renders_a_quotable_span():
    """Rejections are not the only coached signal — a reopen note, the
    operator's own note, the auditor's and the orchestrator's signals all reach
    the same checker."""
    from central_command.runtime.run import _signal_line

    for kind in ("rejection", "reopen", "note", "audit-overcautious",
                 "audit-miss", "route-override"):
        s = {**REJECTION, "kind": kind}
        quoted = _quotable_span(_signal_line(0, s))
        assert quoted == s["feedback"], f"{kind}: labelled span is not the feedback"
        out = await _verified_evidence([_ev(quoted, "event:2736")], [s])
        assert out[0]["claim_matches_source"] is True, f"{kind} failed to verify"
