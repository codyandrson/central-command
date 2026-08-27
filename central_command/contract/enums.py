"""Enumerations for the contract layer."""

from enum import Enum


class Reversibility(str, Enum):
    """How hard an action is to undo — a first-class axis for approval policy."""

    reversible = "reversible"
    hard = "hard"
    irreversible = "irreversible"


class RiskTier(str, Enum):
    """What a class of action requires. The gateway derives this from policy —
    never from the agent's own say-so."""

    auto = "auto"
    ask = "ask"
    never = "never"


class ConfidenceLevel(str, Enum):
    """How sure the AGENT is of its own proposal — its self-assessment, never a
    gate. Uncertainty triage (the knowledge-layer routing record) records it on
    every proposal so bulk approval can later graduate on the accumulated
    confidence-vs-verdict evidence, exactly as the auditor did. A typed enum
    rather than free text because an unvalidated 'pretty sure' is how a mood
    becomes data."""

    high = "high"
    medium = "medium"
    low = "low"


class ProposalStatus(str, Enum):
    """The proposal lifecycle (design: contracts §2). AWAITING_HUMAN is the state
    that must survive a restart."""

    proposed = "PROPOSED"
    awaiting_human = "AWAITING_HUMAN"
    approved = "APPROVED"
    rejected = "REJECTED"
    executing = "EXECUTING"
    executed = "EXECUTED"
    failed = "FAILED"
    # Outage equivalence slice 3: APPROVED, executed partway (or not at all),
    # and queued for replay because a dependency was DOWN. Not terminal and not
    # decidable — the operator decided once; what is pending is the world-change.
    retry_pending = "RETRY_PENDING"


class SessionStatus(str, Enum):
    """The session lifecycle (design: contracts §2)."""

    running = "RUNNING"
    awaiting_human = "AWAITING_HUMAN"
    awaiting_input = "AWAITING_INPUT"
    done = "DONE"
    failed = "FAILED"
    aborted = "ABORTED"
