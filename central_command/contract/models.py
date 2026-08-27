"""The contract models — proposals as validated Pydantic objects.

This is exactly why the runtime is Pydantic AI: an agent emits a `Proposal` and the
framework validates it, self-correcting a malformed one until it conforms. The
agent authors the `Proposal` core; the control plane wraps it in a
`ProposalRecord` that carries the lifecycle fields.
"""

from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, Field

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
    claim: str = Field(..., description="what the agent asserts the source shows")


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


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


class Action(BaseModel):
    """One concrete mutation."""

    capability: str = Field(..., description="Skill@version, e.g. 'jira.update_issue@v2'")
    arguments: dict
    target_ref: dict = Field(
        ..., description="{system, id, read_version} — read_version enables stale-detection"
    )
    reversibility: Reversibility


class Confidence(BaseModel):
    """The agent's own confidence in its proposal, with the reason for it.

    Recorded, never enforced: every proposal still passes the human gate. It
    exists so the confidence-vs-verdict record can be measured (the steward
    agreement report), which is what a later bulk-approval threshold would have
    to graduate on."""

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
    # Optional so every existing path keeps validating unchanged: an ABSENT
    # confidence means the agent did not state one, which is not the same as a
    # low one and must never render as one.
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
