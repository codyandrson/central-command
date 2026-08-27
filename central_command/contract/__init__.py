"""The contract layer — the shared vocabulary every tier speaks.

This package depends on nothing else in Central Command and is imported by all tiers.
It defines what an agent may *propose* and how that proposal moves through its
lifecycle. See the design docs (contracts artifact) for the full rationale.
"""

from .failures import (
    RETRY_BACKOFF_CAP_SECONDS,
    SEMANTIC,
    TRANSIENT,
    classify_failure,
    classify_failure_text,
    is_transient,
    retry_backoff_seconds,
)
from .enums import (
    ConfidenceLevel,
    ProposalStatus,
    Reversibility,
    RiskTier,
    SessionStatus,
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
)

__all__ = [
    "Reversibility",
    "ConfidenceLevel",
    "Confidence",
    "RiskTier",
    "ProposalStatus",
    "SessionStatus",
    "Evidence",
    "claim_supported",
    "Action",
    "Proposal",
    "Decision",
    "Provenance",
    "ProposalRecord",
    "TRANSIENT",
    "SEMANTIC",
    "classify_failure",
    "classify_failure_text",
    "is_transient",
    "retry_backoff_seconds",
    "RETRY_BACKOFF_CAP_SECONDS",
]
