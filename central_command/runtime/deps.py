"""Per-run dependencies handed to the agent (Pydantic AI `deps`).

Phase 1 agents needed no context beyond the prompt. M9 adds one thing: when an
email arrives on a thread that has other unprocessed messages, the agent is told
about the siblings and must say whether it is folding them into one proposal or
handling them separately. That answer comes back here.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ThreadContext:
    """What the agent is allowed to fold — the surveyed, still-unprocessed
    siblings of the item being worked. Empty for a single-email run."""

    thread_id: str | None = None
    sibling_message_ids: list[str] = field(default_factory=list)


@dataclass
class ThreadDecision:
    fold: bool
    covered_message_ids: list[str]
    rationale: str


@dataclass
class TriageDeps:
    thread: ThreadContext = field(default_factory=ThreadContext)
    decision: ThreadDecision | None = None
    # D7 phase 2 (orchestration): the running session's id (so tools can emit
    # events about the right subject) and the round's progress-ledger entries
    # (`record_progress` appends; the driver reads them for stall detection).
    session_id: str | None = None
    progress_ledger: list[dict] = field(default_factory=list)
    agent_id: str | None = None    # who is running — declare_gap records it
    # Consult depth 2 (2026-08-22): the ids of agents already stacked in this
    # consultation chain BEFORE this run — empty for a top-level run. Read by
    # consult_agent/refusal_for to refuse mechanically past depth 2 or back
    # into an agent already in the chain (runtime/consult.py).
    consult_chain: tuple[str, ...] = ()
