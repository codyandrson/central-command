"""Gate overrides — resolution is override, then default (FR-4, FR-2).

A capability's default gate lives on its registry row (`Capability.default_gate`);
an override is the operator's per-agent row (`agent_gate_override`) relaxing it
to `none`. An override never writes anything itself: a proposal it covers is
still a proposal, decided at the ONE seam (`gateway.approve_and_execute`) with
approver `override:<id>`, so `proposal.decided` names the override and lands
before the Executor runs (DL-144's rejection of "auto-approve as system"
stands: the approver names the operator-authored rule, never `system`).

Two rules make a decision here safe to take without the operator watching:

* the override belongs to the DRAFTER (`proposal.agent_id`), never the subject;
* `proposal.created_at > override.granted_at` — a proposal the operator could
  already see before the override existed is never decided by it, whatever
  calls the hook.

The hook runs at a landing point AFTER the caller's bookkeeping (the task-run
landing in `api/routes._run_one_assigned_task`); it is not a `proposal.created`
subscriber, because a subscriber would race fold reservation and task landing.
An override approval is never operator agreement: `steward_agreement` and the
coaching reader skip a `proposal.decided` whose payload carries `override_id`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from central_command import events
from central_command.db import repo
from central_command.gateway.capabilities import REGISTRY, requires_acknowledgement

# The capabilities an override may decide at all. A deliberately tiny tracer
# allowlist: until the scope forms, the reviewer-would-have-seen checks and
# the Executor's scope check exist, only one reversible internal write is
# eligible. Deleted when those land.
OVERRIDABLE: frozenset[str] = frozenset({"catalog.tag"})

APPROVER_PREFIX = "override:"

# The one hard exclusion (FR-4 as amended 2026-10-25): the auditor never holds
# an override, so a row naming it is never honoured, whoever inserted it.
AUDITOR_ID = "auditor"

_DEFAULT_GATES = {c.name: c.default_gate for c in REGISTRY if c.kind == "write"}


@dataclass(frozen=True)
class Decision:
    """`gate` is what applies to the whole proposal: `none` with the deciding
    `override_id`, or the default (`human`) with `override_id` None. `reason`
    says why an override in play did not decide it; `considered` is False when
    no override of the drafter touched any of its capabilities at all."""

    gate: str
    override_id: str | None = None
    reason: str = ""
    considered: bool = False


def approver_for(override_id: str) -> str:
    return f"{APPROVER_PREFIX}{override_id}"


def override_id_of(approver: str | None) -> str | None:
    """The override an approver string names, or None for every other decider."""
    if approver and approver.startswith(APPROVER_PREFIX):
        return approver[len(APPROVER_PREFIX):] or None
    return None


def _capability(action: dict) -> str:
    return str(action.get("capability", "")).split("@", 1)[0]


async def resolve(proposal_row: dict) -> Decision:
    """Override, then default. The drafter's live override covering EVERY
    action decides the proposal; anything less leaves the default gate."""
    actions = proposal_row.get("actions") or []
    caps = [_capability(a) for a in actions]
    # `auditor` records that a graduated class MAY be auditor-confirmed; at this
    # seam it gates exactly like `human` (it changes no behaviour).
    default = "human" if any(_DEFAULT_GATES.get(c, "human") != "none" for c in caps) \
        else "none"
    if not caps or proposal_row.get("agent_id") == AUDITOR_ID:
        return Decision(default)

    created_at = proposal_row["created_at"]
    covering: set[str] = set()
    considered = False
    reason = ""
    for cap in caps:
        live = await repo.live_gate_overrides(proposal_row["agent_id"], cap)
        considered = considered or bool(live)
        if cap not in OVERRIDABLE:
            reason = reason or f"{cap} is not overridable"
            continue
        # Only an unscoped, no-shadow relaxation to `none` is honoured until
        # the scope forms and the shadow period exist: a row that asks for
        # either is never treated as wider than it says.
        # A capability whose registry row requires acknowledgement (irreversible
        # or LIVE / highest blast radius) is honoured only with the operator's
        # recorded acknowledgement AND a non-zero shadow period. The mark is the
        # server-held registry field; the agent-written `Action.reversibility`
        # is never read here. Until story 7 counts shadow runs, such a row with
        # both present is the one shadowed row that decides.
        ack = requires_acknowledgement(cap)
        if ack:
            lacking = [o for o in live
                       if not o["acknowledged_irreversible_at"] or o["shadow_runs"] < 1]
            if lacking and len(lacking) == len(live):
                reason = reason or (f"{cap} requires an acknowledgement and a shadow "
                                    "period the override lacks")
                continue
            live = [o for o in live if o not in lacking]
        eligible = [o for o in live
                    if o["gate"] == "none" and not o["scope"]
                    and (ack or o["shadow_runs"] == 0)]
        if live and not eligible:
            reason = reason or "the override is scoped or shadowed"
            continue
        current = [o for o in eligible if created_at > o["granted_at"]]
        if eligible and not current:
            reason = reason or "the proposal predates the override"
            continue
        if not current:
            reason = reason or f"no live override covers {cap}"
            continue
        covering.add(current[0]["id"])

    if not considered:
        return Decision(default)
    if reason:
        return Decision(default, reason=reason, considered=True)
    if len(covering) != 1:
        return Decision(default, reason="the actions are covered by different overrides",
                        considered=True)
    return Decision("none", override_id=covering.pop(), considered=True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def decide_on_park(proposal_id: str, model=None) -> dict | None:
    """The decide hook: evaluate a freshly parked proposal once and, when an
    override covers it, decide it through the one seam.

    Called only after the caller's bookkeeping has landed. Records the
    evaluation on the proposal by compare-and-set (`override_eval`), so a
    second caller does nothing. Returns the record, or None when the proposal
    is not awaiting a decision, was already evaluated, or no override of its
    drafter was in play (nothing is written then: on a database with no
    override rows the hook is a no-op)."""
    row = await repo.load_proposal(proposal_id)
    if row is None or row["status"] != "AWAITING_HUMAN" or row.get("override_eval"):
        return None
    decision = await resolve(row)
    if not decision.considered:
        return None
    record = {
        "override_id": decision.override_id,
        "outcome": "decided" if decision.override_id else "parked",
        "reason": decision.reason or None,
        "at": _now(),
    }
    if not await repo.record_override_eval(proposal_id, record):
        return None
    if decision.override_id is None:
        return record

    from central_command.gateway import gateway

    try:
        await gateway.approve_and_execute(
            row["session_id"], proposal_id,
            approver=approver_for(decision.override_id), model=model,
        )
    except Exception as e:  # noqa: BLE001 — the operator's gate takes it back, loudly
        # The claim lost to an operator click, or the approval crashed before
        # the Executor ran (the gateway handed the claim back): the override
        # did not decide this proposal and the record must say so. A crash
        # AFTER the claim leaves the status moved, and `decided` stays true.
        fresh = await repo.load_proposal(proposal_id)
        if fresh is not None and fresh["status"] == "AWAITING_HUMAN":
            record = {**record, "outcome": "parked",
                      "reason": f"the override decision did not run: {e}"}
            await repo.amend_override_eval(proposal_id, record)
        await events.emit(
            "override.error", ref_id=proposal_id,
            payload={"override_id": decision.override_id, "error": str(e)},
            actor=approver_for(decision.override_id),
        )
    return record
