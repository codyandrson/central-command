"""The one path a proposal takes into the review queue.

Every proposal is persisted AND announced here — email-driven, operator-tasked,
coached, raised mid-conversation, or a redraft after a rejection. Before this
existed the emit lived at the call sites, and the two paths added later (a coach
run and an operator task) simply forgot it: their proposals reached the
Decisions Inbox with **no `proposal.created` row at all**, so the M16 audit
trail showed a charter edit only at decision time, with no record of when or
from what it was drafted (found 2026-07-25).

This is a legibility guarantee, not an authorisation one — the ordering rule in
CLAUDE.md governs events that authorise something, and a proposal authorises
nothing (its `proposal.decided` does, and the gateway still writes that before
executing). What this buys is that `kind=proposal` is a COMPLETE index of the
queue rather than a partial one.

`tests/test_proposal_created.py` walks the source to keep it the only seam: a
new `repo.save_proposal(...)` call outside this module fails the suite, which is
how the next path avoids repeating the same omission.
"""

from __future__ import annotations

import uuid

from central_command import events
from central_command.db import repo
from central_command.runtime.durable import dump_run_state, proposal_from_call


async def park_proposal(
    *,
    session_id: str,
    agent_id: str,
    result,
    call,
    evidence: list[dict] | None = None,
    actor: str | None = None,
    context: dict | None = None,
    origin: dict | None = None,
) -> dict:
    """Pause the run at its deferred call, persist the proposal, announce it.

    `evidence` overrides the agent's own entries for paths that verify or stamp
    them first (the gateway re-checks a redraft's operator quotes; a coach run
    checks its quotes against the operator's curated signals). `context` adds
    path-specific payload fields — the email path's `item_id`, a redraft's
    `redraft_of` — so the log keeps the detail each path had before.

    `origin` is the same shape persisted ON THE ROW, for provenance the Inbox
    can render long after the event stream has scrolled away (the consult path:
    who asked, and from which session). Pass it alongside `context` so the row
    and the event never disagree.

    `charter.update` gets its evidence checked here REGARDLESS of which door
    the agent came through. The coach's charter and the charter-propose pack
    both promise the quote-and-event-id check "re-checked mechanically against
    the record" — but only `run_coach` performed it. The coach is also
    taskable and conversational, so an operator can raise a charter edit from
    the Task Board or in chat; those paths park with no `evidence=` override,
    which left `claim_matches_source` ABSENT — NOT CHECKED, rendering as
    unremarkable in the Inbox. That is the exact 2026-07-25 failure this
    branch retells in three docstrings. Checking it here, in the one seam
    every proposal takes, means no future door can miss it. A path with no
    curated selection (a task, a chat) has nothing to mark `selected` — every
    citation is honestly `discovered` or `unresolved` against the record.
    """
    from pydantic_ai import DeferredToolRequests

    metadata = None
    if isinstance(result.output, DeferredToolRequests):
        metadata = result.output.metadata.get(call.tool_call_id)
    proposal = proposal_from_call(call, metadata=metadata)
    proposal_id = "prop_" + uuid.uuid4().hex[:12]

    if evidence is None and any(
        a.capability.split("@", 1)[0] == "charter.update" for a in proposal.actions
    ):
        # Local import: `run._verified_evidence` lives in the runtime tier
        # alongside this module, but run.py imports `park_proposal` at module
        # level — a module-level import back here would cycle.
        from central_command.runtime.run import _verified_evidence

        # No curated signals on this path: everything the agent cites is
        # therefore `discovered` or `unresolved`, never `selected` — correct
        # and honest, since nothing was operator-selected for this session.
        evidence = await _verified_evidence(proposal.evidence, signals=[])

    await repo.save_paused_session(
        session_id, agent_id, dump_run_state(result, call.tool_call_id)
    )
    await repo.save_proposal(
        proposal_id=proposal_id,
        session_id=session_id,
        agent_id=agent_id,
        intent=proposal.intent,
        actions=[a.model_dump() for a in proposal.actions],
        evidence=(
            evidence if evidence is not None else [e.model_dump() for e in proposal.evidence]
        ),
        expected_effect=proposal.expected_effect,
        idempotency_key=f"{proposal_id}:0",
        # The agent's own uncertainty, validated by the contract before it is
        # persisted. None means the agent stated none — never "low".
        confidence=(
            proposal.confidence.model_dump(mode="json")
            if proposal.confidence is not None else None
        ),
        origin=origin,
    )
    await events.emit(
        "proposal.created",
        ref_id=proposal_id,
        payload={
            "intent": proposal.intent,
            "session_id": session_id,
            **(context or {}),
        },
        actor=actor or f"agent:{agent_id}",
    )
    return {"proposal_id": proposal_id, "proposal": proposal}
