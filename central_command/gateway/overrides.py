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

import json
from dataclasses import dataclass
from datetime import datetime, timezone

from central_command import events
from central_command.db import repo
from central_command.contract import ARG_SPECS
from central_command.gateway.capabilities import (
    REGISTRY, requires_acknowledgement, target_args_of,
)

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

# --- scope ------------------------------------------------------------------
#
# An override row's `scope` is `{}` (every use) or any of three forms, ANDed:
#
#   {"argument": {"name": "link_type", "values": ["Relates"]},   # exact values
#    "target": ["DOE"],                                          # registry target args
#    "schedule": "mail-poll"}                                    # heartbeat schedule id
#
# No regex and no time windows. Every form is matched against SERVER-HELD
# facts only: the action arguments the Executor will use, `proposal.agent_id`
# and the drafting session's task row. The agent-written `target_ref` and
# `Action.reversibility` are never read. Anything the matcher cannot verify
# (an unknown key, a missing required argument, a capability with no target
# declaration) is NOT covered: a scope can only narrow.

SCOPE_FORMS = ("argument", "target", "schedule")
SCHEDULE_CREATOR_PREFIX = "heartbeat:"


def _scalar(v) -> str | None:
    if isinstance(v, bool) or isinstance(v, (int, float)):
        return json.dumps(v)
    return v if isinstance(v, str) else None


def _flat(v) -> list | None:
    """An argument's values as scalars: a list contributes every element, an
    empty one or a nested structure is None (nothing exact to compare)."""
    items = v if isinstance(v, list) else [v]
    if not items:
        return None
    out = [_scalar(i) for i in items]
    return None if any(i is None for i in out) else out


def _target_key(capability: str, value: str) -> str:
    """What a target value is compared as: a Jira issue key by its project
    prefix (`DOE-1` -> `DOE`; a bare project key is itself), everything else
    exactly."""
    return value.split("-", 1)[0] if capability.startswith("jira.") else value


def _arg_names(capability: str) -> set[str]:
    spec = ARG_SPECS.get(capability)
    if spec is None:
        return set()
    names = set(spec.required) | set(spec.enums)
    for group in (*spec.any_of, *spec.any_present):
        names |= set(group)
    return names


def scope_covers(override: dict, action: dict, ctx: dict | None = None) -> bool:
    """Does the override's scope cover this action? Pure: `ctx["task"]` is the
    drafting session's task row (None for a ledger-sourced proposal, which
    therefore never matches a schedule scope)."""
    scope = override.get("scope") or {}
    if isinstance(scope, str):
        scope = json.loads(scope)
    capability = _capability(action)
    if override.get("capability") not in (None, capability):
        return False
    if not scope:
        return True
    if not isinstance(scope, dict) or set(scope) - set(SCOPE_FORMS):
        return False
    args = action.get("arguments") or {}
    spec = ARG_SPECS.get(capability)

    if "argument" in scope:
        form = scope["argument"]
        if spec is None or not isinstance(form, dict):
            return False
        have = _flat(args.get(form.get("name")))
        allowed = [_scalar(v) for v in form.get("values") or []]
        if have is None or not allowed or any(h not in allowed for h in have):
            return False

    if "target" in scope:
        declared = target_args_of(capability)
        allowed = scope["target"]
        if spec is None or not declared or not isinstance(allowed, list) or not allowed:
            return False
        seen = 0
        for name in declared:
            value = args.get(name)
            if value in (None, "", []):
                if name in spec.required:
                    return False
                continue            # an optional target not named is not a target
            vals = _flat(value)
            if vals is None:
                return False
            if any(_target_key(capability, v) not in allowed for v in vals):
                return False
            seen += 1
        if not seen:
            return False

    if "schedule" in scope:
        task = (ctx or {}).get("task")
        want = scope["schedule"]
        if not task or not isinstance(want, str) or not want:
            return False
        if task.get("created_by") != f"{SCHEDULE_CREATOR_PREFIX}{want}":
            return False
    return True


def validate_scope(capability: str, scope: dict | None) -> list[str]:
    """Every reason a scope may not be authored for this capability, one
    message each; empty means it is well-formed. The authoring path calls this
    before it inserts a row."""
    capability = capability.split("@", 1)[0]
    problems: list[str] = []
    if _DEFAULT_GATES.get(capability, "none") == "none":
        problems.append(f"{capability} has no gate to relax, so it takes no override")
    scope = scope or {}
    if not isinstance(scope, dict):
        return problems + ["scope must be an object"]
    for key in sorted(set(scope) - set(SCOPE_FORMS)):
        problems.append(f"unknown scope form {key!r} (forms: {', '.join(SCOPE_FORMS)})")
    if "argument" in scope:
        form = scope["argument"]
        if capability not in ARG_SPECS:
            problems.append(f"{capability} has no argument shape, so it takes no argument scope")
        elif not isinstance(form, dict) or not isinstance(form.get("name"), str):
            problems.append("an argument scope is {name, values}")
        elif form["name"] not in _arg_names(capability):
            problems.append(f"{capability} has no argument {form['name']!r} "
                            f"(known: {', '.join(sorted(_arg_names(capability)))})")
        if isinstance(form, dict) and not (
                isinstance(form.get("values"), list) and form["values"]
                and all(_scalar(v) is not None for v in form["values"])):
            problems.append("an argument scope needs a non-empty list of exact values")
    if "target" in scope:
        values = scope["target"]
        if not target_args_of(capability):
            problems.append(f"{capability} declares no target argument, so it takes no target scope")
        if not (isinstance(values, list) and values
                and all(isinstance(v, str) and v for v in values)):
            problems.append("a target scope needs a non-empty list of target values")
    if "schedule" in scope and not (isinstance(scope["schedule"], str) and scope["schedule"]):
        problems.append("a schedule scope names one heartbeat schedule id")
    return problems


def scope_text(override: dict) -> str:
    """The one plain-words rendering of an override's scope; every screen shows
    this string, none composes its own."""
    scope = override.get("scope") or {}
    if isinstance(scope, str):
        scope = json.loads(scope)
    capability = override.get("capability", "")
    if not scope:
        return "every use"
    parts: list[str] = []
    if "argument" in scope:
        f = scope["argument"]
        vals = " or ".join(str(v) for v in f.get("values", []))
        parts.append(f"only where {f.get('name')} is {vals}")
    if "target" in scope:
        declared = target_args_of(capability)
        names = " and ".join(declared) or "the target"
        where = "Jira project" if capability.startswith("jira.") else "the target"
        vals = " or ".join(str(v) for v in scope["target"])
        verb = "are all" if len(declared) > 1 else "is"
        parts.append(f"only where {names} {verb} in {where} {vals}" if where != "the target"
                     else f"only where {names} {verb} {vals}")
    if "schedule" in scope:
        parts.append(f"only in work started by the {scope['schedule']} schedule")
    return "; and ".join(parts)


async def resolve(proposal_row: dict) -> Decision:
    """Override, then default. The drafter's live override covering EVERY
    gated action decides the proposal; anything less leaves the default gate.
    An override's scope is evaluated per action (`scope_covers`)."""
    actions = proposal_row.get("actions") or []
    caps = [_capability(a) for a in actions]
    # `auditor` records that a graduated class MAY be auditor-confirmed; at this
    # seam it gates exactly like `human` (it changes no behaviour).
    default = "human" if any(_DEFAULT_GATES.get(c, "human") != "none" for c in caps) \
        else "none"
    if default == "none" or proposal_row.get("agent_id") == AUDITOR_ID:
        return Decision(default)

    created_at = proposal_row["created_at"]
    covering: set[str] = set()
    considered = False
    reason = ""
    live_by_cap: dict[str, list[dict]] = {}
    ctx: dict | None = None
    for action, cap in zip(actions, caps):
        if _DEFAULT_GATES.get(cap, "human") == "none" and cap in _DEFAULT_GATES:
            continue            # nothing gates this action; there is nothing to cover
        if cap not in live_by_cap:
            live_by_cap[cap] = await repo.live_gate_overrides(proposal_row["agent_id"], cap)
        live = live_by_cap[cap]
        considered = considered or bool(live)
        if cap not in _DEFAULT_GATES:
            reason = reason or f"{cap} is not overridable"
            continue
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
                    if o["gate"] == "none" and (ack or o["shadow_runs"] == 0)]
        if live and not eligible:
            reason = reason or "the override is shadowed"
            continue
        if eligible and ctx is None and any((o["scope"] or {}).get("schedule") for o in eligible):
            sid = proposal_row.get("session_id")
            ctx = {"task": await repo.task_for_session(sid) if sid else None}
        scoped = [o for o in eligible if scope_covers(o, action, ctx)]
        if eligible and not scoped:
            reason = reason or f"{cap} is outside the override's scope"
            continue
        current = [o for o in scoped if created_at > o["granted_at"]]
        if scoped and not current:
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
