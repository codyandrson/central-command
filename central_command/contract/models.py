"""The contract models — proposals as validated Pydantic objects.

This is exactly why the runtime is Pydantic AI: an agent emits a `Proposal` and the
framework validates it, self-correcting a malformed one until it conforms. The
agent authors the `Proposal` core; the control plane wraps it in a
`ProposalRecord` that carries the lifecycle fields.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime

from pydantic import BaseModel, Field, model_serializer

from .enums import ConfidenceLevel, ProposalStatus, Reversibility


class Evidence(BaseModel):
    """A checkable claim. The auditor (or you) re-fetches from `locator` and
    compares the real source against `claim` — never trusting the agent's
    summary. This is the structural defense against hallucinated justifications."""

    kind: str = Field(
        ...,
        description="email | jira_state | calendar | graph_fact | operator | ... "
        "('operator' = a human instruction, e.g. rejection feedback — trusted as "
        "given, needs no re-derivation from an external source)",
    )
    source_ref: str = Field(..., description="pointer to the ACTUAL source, not a paraphrase")
    locator: str = Field(..., description="how to re-fetch it fresh")
    # The model reads this description as part of the tool schema. It used to
    # say "what the agent asserts the source shows", which invited a summary,
    # and a summary is never found in the source (DL-230).
    claim: str = Field(
        ...,
        description=(
            "ONE passage copied exactly from the source, word for word: "
            "contiguous, or with omitted words marked by an ellipsis (...). "
            "One evidence entry per passage. No summary, no paraphrase and no "
            "words of your own — the control plane checks the passage against "
            "the source and flags anything it cannot find."
        ),
    )


def _normalize(text: str) -> str:
    """Case- and punctuation-insensitive, in every script: NFKC, casefold, then
    every run of non-word characters (punctuation, symbols, whitespace) and
    underscores becomes ONE space. Letters and digits of every script are kept
    — a Cyrillic or CJK quote is checked on its own words, not on an ASCII
    residue (2026-11-15). For pure-ASCII text this is byte-for-byte the old
    `[^a-z0-9]+ -> space` after `lower()` (a test pins it)."""
    folded = unicodedata.normalize("NFKC", text).casefold()
    return re.sub(r"[\W_]+", " ", folded).strip()


# Characters a stored NOTE may not contain (DL-217): the notes block renders
# each note as one visible line, so nothing the reviewer cannot see may ride in
# one. Category C* (controls, format, zero-width), the line and paragraph
# separators, and these visible-as-nothing characters outside those classes.
# ONE place: the runtime's build-time collapse removes them and the repo's
# Executor-side check refuses them (both import from here).
NOTE_INVISIBLE_CODEPOINTS = frozenset(
    {
        0x2800,  # braille pattern blank (So)
        0x3164,
        0xFFA0,
        0x115F,
        0x1160,  # Hangul fillers (Lo)
        0x034F,
        0x17B4,
        0x17B5,  # combining grapheme joiner, Khmer vowels (Mn)
        0x180B,
        0x180C,
        0x180D,  # Mongolian variation selectors (Mn)
        *range(0xFE00, 0xFE10),  # variation selectors 1-16
        *range(0xE0100, 0xE01F0),  # variation selectors 17-256
    }
)


def note_forbidden_char(ch: str) -> bool:
    """May a stored note NOT contain `ch`? Every whitespace but the plain
    space, every C* character, the line/paragraph separators, and the
    invisible set above."""
    if ch == " ":
        return False
    return (
        ch.isspace()
        or unicodedata.category(ch)[0] in ("C", "Z")
        or ord(ch) in NOTE_INVISIBLE_CODEPOINTS
    )


def claim_supported(claim: str, source: str) -> bool:
    """Does the agent's quoted claim actually appear in the recorded source?
    Punctuation/case-insensitive, but not fuzzy: a paraphrase fails and gets
    flagged for the reviewer. No agent claim is trusted without verification —
    including its account of what the operator said.

    Lives in `contract/` because BOTH tiers re-check quotes and the runtime may
    never import the gateway: the gateway checks a redraft against the
    rejection feedback, the runtime checks a coached charter against the
    signals the operator curated.
    """
    c = _normalize(claim)
    return bool(c) and c in _normalize(source)


# The shortest piece a quote with omissions may be assembled from (DL-230).
# Four words: a run of four consecutive source words in the claim's order is a
# passage; three scattered words ("the", "invoice", "Friday") are not.
QUOTE_MIN_SEGMENT_WORDS = 4
# A ceiling on the substring searches one check may make. These are short
# strings and the search below is near-linear in practice; the ceiling only
# makes a pathological input fail CLOSED (unsupported, a false flag the
# reviewer sees) instead of running long.
_QUOTE_MAX_SEARCHES = 100_000
# An ellipsis marking an omission: "...", "…" (NFKC folds it to "..."), and
# both inside square brackets — the brackets are punctuation, which the
# normalisation drops, so splitting on the dots is enough. ". . ." counts too.
_ELLIPSIS = re.compile(r"\.(?:\s?\.){2,}")


def quote_supported(claim: str, source: str) -> bool:
    """Is `claim` a passage copied from `source`, whole or with omissions?

    The rule (DL-230), on the same normalisation as `claim_supported`:

    - A claim that is ONE contiguous substring of the source is supported,
      whatever its length — exactly `claim_supported`.
    - Otherwise it is supported only if it splits into SEGMENTS that each
      appear verbatim in the source, in source order and without overlapping,
      every segment at least `QUOTE_MIN_SEGMENT_WORDS` words long. Segments
      are separated in the claim by an ellipsis ("...", "…", "[...]", "[…]"),
      or simply run together with nothing between them (a quote that joined
      two passages and dropped the marker).
    - Every word of the claim belongs to some segment, so framing words, a
      paraphrase, a reordering, or a piece under four words fails.

    How a silent join is segmented: not greedily (the longest match first can
    swallow words a later segment needed and refuse a real quote) but by an
    exact left-to-right search that keeps, for every claim position, the
    EARLIEST source position at which the claim up to there can end. Ending
    earlier never closes off a continuation that ending later allows, so the
    claim is supported exactly when SOME valid segmentation exists, and the
    answer does not depend on which split is tried first. An ellipsis is a hard
    boundary: no segment spans one. A phrase that occurs twice in the source
    may be used twice, never the same occurrence twice.

    Pure and deterministic; it lives in `contract/` beside `claim_supported`
    because both tiers check quotes and `runtime/` may never import
    `gateway/`. Use it for quotes of a SOURCE (mail, the operator's words);
    a note's single-span rule stays on `claim_supported`.
    """
    if claim_supported(claim, source):
        return True
    hay = f" {_normalize(source)} "
    if not hay.strip():
        return False
    pieces = [_normalize(p).split() for p in _ELLIPSIS.split(unicodedata.normalize("NFKC", claim))]
    pieces = [p for p in pieces if p]
    if not pieces:
        return False
    budget = [_QUOTE_MAX_SEARCHES]
    pos = 0  # index of the space the next segment may start at
    for words in pieces:
        pos = _segment_piece(words, hay, pos, budget)
        if pos is None:
            return False
    return True


def _segment_piece(words: list[str], hay: str, start: int, budget: list[int]) -> int | None:
    """The earliest end (the index of the trailing space in `hay`) at which
    `words` can be matched as consecutive segments of at least
    `QUOTE_MIN_SEGMENT_WORDS` words each, in order, the first starting at or
    after `start`; None when no segmentation exists or the budget runs out.

    `best[i]` is the earliest end for `words[:i]`. From each reachable `i`, the
    segment `words[i:j]` is extended one word at a time; once an extension is
    not found after `best[i]`, no longer one can be (it contains the shorter
    one at the same start), so the loop stops — the work per position is the
    length of the longest match there, not the length of the claim."""
    m = len(words)
    if m < QUOTE_MIN_SEGMENT_WORDS:
        return None
    best: list[int | None] = [None] * (m + 1)
    best[0] = start
    for i in range(m):
        e = best[i]
        if e is None:
            continue
        needle = " " + " ".join(words[i : i + QUOTE_MIN_SEGMENT_WORDS - 1])
        k = e
        for j in range(i + QUOTE_MIN_SEGMENT_WORDS, m + 1):
            budget[0] -= 1
            if budget[0] < 0:
                return None
            needle += " " + words[j - 1]
            # The earliest start of a longer segment is never before the
            # shorter one's, so the search resumes where the last one matched.
            k = hay.find(needle + " ", k)
            if k < 0:
                break
            end = k + len(needle)
            if best[j] is None or end < best[j]:
                best[j] = end
    return best[m]


class Action(BaseModel):
    """One concrete mutation."""

    capability: str = Field(..., description="Skill@version, e.g. 'jira.update_issue@v2'")
    arguments: dict
    target_ref: dict = Field(..., description="{system, id}")
    reversibility: Reversibility


class Confidence(BaseModel):
    """The agent's own confidence in its proposal, with the reason for it.

    Its PRESENCE is enforced at propose time for agent-authored proposals
    (`contract.validate_proposal_shape`, called by the runtime for the four
    tools whose `proposal` the model writes): a draft without one is handed
    back in-run. Server-built proposals carry none and are never given one. The
    Executor cannot check it — `execute()` receives the actions, not the
    proposal — and that is recorded, not worked around (GATE-067). Its VALUE
    gates nothing: every proposal still passes the human gate. It exists so the
    confidence-vs-verdict record can be measured (the steward agreement
    report), which is what a later bulk-approval threshold would have to
    graduate on."""

    level: ConfidenceLevel = Field(
        ..., description="high | medium | low — your own assessment of this proposal"
    )
    rationale: str = Field(
        ..., description="one sentence: WHY that level — what makes it certain or not"
    )


class Proposal(BaseModel):
    """The agent-authored core of a proposal — what a `propose_*` tool returns."""

    intent: str = Field(..., description="one sentence: what & why, for the human")
    actions: list[Action] = Field(..., min_length=1)
    evidence: list[Evidence]
    expected_effect: str = Field(..., description="the post-state to verify against")
    # Optional in the MODEL because server-built proposals carry none (and
    # proposals recorded before GATE-067 have none): an ABSENT confidence means
    # nobody stated one, which is not the same as a low one and must never
    # render as one. For agent-authored proposals the runtime enforces its
    # presence at propose time (`contract.validate_proposal_shape`).
    confidence: Confidence | None = None


class Decision(BaseModel):
    """The gateway's verdict on a proposal."""

    verdict: str = Field(..., description="approved | rejected")
    decider: str = Field(..., description="e.g. 'human:lee' | 'auditor'")
    rationale: str | None = None
    at: datetime


class Provenance(BaseModel):
    """Stamped onto the artifact and recorded after execution."""

    approver: str
    source_refs: list[str]
    executed_at: datetime
    # Under a MULTI-override approver (`override:<a>+<b>`): the approver each
    # action was stamped with, by its index in the proposal — the covering
    # `override:<id>`. Composed by the Executor from the stored action map,
    # never supplied by a caller; omitted from the record otherwise.
    action_approvers: dict[str, str] = {}

    @model_serializer(mode="wrap")
    def _omit_empty_action_approvers(self, handler):
        data = handler(self)
        if not data.get("action_approvers"):
            data.pop("action_approvers", None)
        return data


class ProposalRecord(BaseModel):
    """The persisted proposal: the authored core plus its lifecycle."""

    id: str
    session_id: str
    agent_id: str
    proposal: Proposal
    status: ProposalStatus = ProposalStatus.proposed
    idempotency_key: str
    decision: Decision | None = None
    provenance: Provenance | None = None
    created_at: datetime
    decided_at: datetime | None = None
