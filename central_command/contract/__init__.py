"""The contract layer — the shared vocabulary every tier speaks.

This package depends on nothing else in Central Command and is imported by all tiers.
It defines what an agent may *propose* and how that proposal moves through its
lifecycle. See the design docs (contracts artifact) for the full rationale.
"""

from .args import ARG_SPECS, ArgSpec, validate_action_args, validate_proposal_shape
from .enums import (
    ConfidenceLevel,
    ProposalStatus,
    Reversibility,
    SessionStatus,
)
from .failures import (
    RETRY_BACKOFF_BASE_SECONDS,
    SEMANTIC,
    TRANSIENT,
    DependencyDown,
    EmbedderDown,
    EmbedderMismatch,
    classify_failure,
    classify_failure_text,
    is_context_overflow,
    is_transient,
    retry_backoff_seconds,
    retry_not_before,
)
from .models import (
    Action,
    Confidence,
    Decision,
    Evidence,
    Proposal,
    ProposalRecord,
    Provenance,
    claim_supported,
    note_forbidden_char,
    quote_supported,
)

__all__ = [
    "ARG_SPECS",
    "RETRY_BACKOFF_BASE_SECONDS",
    "SEMANTIC",
    "TRANSIENT",
    "Action",
    "ArgSpec",
    "Confidence",
    "ConfidenceLevel",
    "Decision",
    "DependencyDown",
    "EmbedderDown",
    "EmbedderMismatch",
    "Evidence",
    "Proposal",
    "ProposalRecord",
    "ProposalStatus",
    "Provenance",
    "Reversibility",
    "SessionStatus",
    "claim_supported",
    "classify_failure",
    "classify_failure_text",
    "is_context_overflow",
    "is_transient",
    "note_forbidden_char",
    "quote_supported",
    "retry_backoff_seconds",
    "retry_not_before",
    "validate_action_args",
    "validate_proposal_shape",
]
