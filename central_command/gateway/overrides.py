"""Gate overrides — resolution is override, then default (FR-4, FR-2).

A capability's default gate lives on its registry row (`Capability.default_gate`);
an override is the operator's per-agent row (`agent_gate_override`) relaxing it
to `none`. An override never writes anything itself: a proposal it covers is
still a proposal, decided at the ONE seam (`gateway.approve_and_execute`) with
approver `override:<id>` (`override:<id1>+<id2>` when several different
overrides cover its actions), so `proposal.decided` names the override(s) and
lands before the Executor runs — after a read-only pre-check the Executor
repeats as its backstop (`check_override_approval`) (DL-144's rejection of "auto-approve as system"
stands: the approver names the operator-authored rule, never `system`).

Two rules make a decision here safe to take without the operator watching:

* the override belongs to the DRAFTER (`proposal.agent_id`), never the subject;
* `proposal.created_at > override.granted_at` — a proposal the operator could
  already see before the override existed is never decided by it, whatever
  calls the hook.

The hook runs at every API-tier landing point AFTER the caller's bookkeeping
(`PARK_SITES` below maps each `park_proposal` site to its caller; the startup
`override_sweep` is the guarantee behind them); it is not a `proposal.created`
subscriber, because a subscriber would race fold reservation and task landing.
A NEW override is shadowed (safeguard 3, below): a proposal it covers parks
until the operator has approved N matching proposals; with several
overrides covering one proposal, ALL must be past their shadow period.
An override approval is never operator agreement: `steward_agreement` and the
coaching reader skip a `proposal.decided` whose payload carries `override_id`.
"""

from __future__ import annotations

import asyncio
import json
import weakref
from dataclasses import dataclass
from datetime import datetime, timezone

from central_command import events
from central_command.db import repo
from central_command.contract import ARG_SPECS, validate_action_args
from central_command.gateway.capabilities import (
    REGISTRY, SCOPE_FORMS, effective_shadow_runs, requires_acknowledgement,
    target_args_of,
)

APPROVER_PREFIX = "override:"

# The one hard exclusion (FR-4 as amended 2026-10-25): the auditor never holds
# an override, so a row naming it is never honoured, whoever inserted it.
AUDITOR_ID = "auditor"

_DEFAULT_GATES = {c.name: c.default_gate for c in REGISTRY if c.kind == "write"}


@dataclass(frozen=True)
class Decision:
    """`gate` is what applies to the whole proposal: `none` with the deciding
    `override_ids` (one or several — the operator's ruling of 2026-10-26: a
    proposal 100% covered by overrides, the same or different, is decided like
    a single-override one), or the default (`human`) with none. `action_map`
    is {action index -> override id} for every GATED action (an action whose
    default gate is `none` needs no cover and has no entry). `reason` says why
    an override in play did not decide it; `considered` is False when no
    override of the drafter touched any of its capabilities at all."""

    gate: str
    override_ids: tuple[str, ...] = ()
    reason: str = ""
    considered: bool = False
    # Overrides of the drafter that are SUSPENDED: their capability is outside
    # every live pack grant of the agent (FR-4: an override is carried on a
    # grant). Computed fresh on each evaluation; a re-grant lifts it.
    suspended: tuple[str, ...] = ()
    action_map: dict[int, str] | None = None
    # Every override that covers a gated action of the proposal, with its
    # shadow mark ({override_id, k, n, complete}) — the proposal is decided
    # only when ALL of them are complete (several overrides: the operator's
    # ruling of 2026-10-26). `shadowed` says the park reason IS the shadow
    # period (the record's outcome is then `shadow`, not `parked`).
    shadow: tuple[dict, ...] = ()
    shadowed: bool = False

    @property
    def override_id(self) -> str | None:
        """The deciding override when exactly one decided it."""
        return self.override_ids[0] if len(self.override_ids) == 1 else None


OVERRIDE_JOIN = "+"


def approver_for(override_ids) -> str:
    """`override:<id>` for one override, `override:<id1>+<id2>` (sorted,
    unique) for several."""
    ids = [override_ids] if isinstance(override_ids, str) else list(override_ids)
    return APPROVER_PREFIX + OVERRIDE_JOIN.join(sorted(set(ids)))


def override_ids_of(approver: str | None) -> list[str]:
    """THE parser of an override approver: the overrides it names, sorted, or
    [] for every other decider (an operator, the auditor, `system`). Every
    reader that recognises an override approver goes through this (the
    runtime's coaching reader, which may not import the gateway, uses the
    bare `override:` prefix test — the same rule)."""
    if not approver or not approver.startswith(APPROVER_PREFIX):
        return []
    ids = [i for i in approver[len(APPROVER_PREFIX):].split(OVERRIDE_JOIN) if i]
    return sorted(set(ids))


def override_id_of(approver: str | None) -> str | None:
    """The one override an approver names, or None for every other decider
    (and for a combined approver, which names several)."""
    ids = override_ids_of(approver)
    return ids[0] if len(ids) == 1 else None


def decided_by_override(event: dict) -> bool:
    """Was this `proposal.decided` row an override's decision? Read from the
    actor (the approver) and from the payload, so a row carrying either form
    — `override_id` for one, `override_ids` for several — never counts as
    operator agreement."""
    payload = event.get("payload") or {}
    return bool(override_ids_of(event.get("actor")) or payload.get("override_id")
                or payload.get("override_ids"))


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


# --- safeguard 3: the shadow period (story 7) --------------------------------
#
# A new override is shadowed: a proposal it covers parks for the operator until
# the operator has approved N matching proposals. Progress is computed
# FRESH from the proposal rows (`repo.shadow_approval_candidates`) — no counter
# column — over a PINNED window: proposals of the override's agent CREATED after
# its `granted_at`, EXECUTED with a human approver (an override's, the
# auditor's, `system`'s approval never counts), not a redraft or a follow-on,
# carrying an action the override's capability and scope cover (`scope_covers`,
# the matcher the decision uses). A rejected, dismissed or withdrawn proposal
# never reaches EXECUTED, so it advances nothing; a proposal created before the
# grant and approved after does not either. A proposal evaluated while the
# override was SUSPENDED (recorded in its `override_eval.suspended`) counts
# nothing for it. N is the row's `shadow_runs`, copied at authoring, floored at
# 1 for a class that requires acknowledgement.


@dataclass(frozen=True)
class ShadowState:
    override_id: str
    k: int          # matching proposals the operator has approved so far
    n: int          # the override's shadow period

    @property
    def complete(self) -> bool:
        return self.k >= self.n

    def as_mark(self) -> dict:
        return {"override_id": self.override_id, "k": self.k, "n": self.n,
                "complete": self.complete}


async def _schedule_ctx(session_id: str | None) -> dict:
    """The context `scope_covers` reads for a schedule scope: the task the
    proposal's session belongs to."""
    return {"task": await repo.task_for_session(session_id) if session_id else None}


async def _proposal_covered(override: dict, proposal: dict) -> bool:
    """True when one of the proposal's actions is the override's capability and
    inside its scope (the walk `shadow_progress` and `match_stats` share)."""
    ctx = None
    if (override.get("scope") or {}).get("schedule"):
        ctx = await _schedule_ctx(proposal.get("session_id"))
    return any(_capability(a) == override["capability"] and scope_covers(override, a, ctx)
               for a in proposal["actions"] or [])


async def shadow_progress(override: dict) -> int:
    """How many matching proposals the operator has approved inside this
    override's pinned window (see the section comment)."""
    count = 0
    for p in await repo.shadow_approval_candidates(
            override["agent_id"], override["capability"], override["granted_at"]):
        if override["id"] in ((p.get("override_eval") or {}).get("suspended") or []):
            continue
        if await _proposal_covered(override, p):
            count += 1
    return count


async def shadow_state(override: dict) -> ShadowState:
    """THE shadow reading, applied per override by `resolve` and
    `check_override_approval` alike. N is floored at read as well as at
    authoring, so a row that was not authored through the repo cannot skip the
    floor of an acknowledgement class."""
    n = effective_shadow_runs(override["capability"], int(override["shadow_runs"]))
    if n <= 0:
        return ShadowState(override["id"], 0, 0)
    return ShadowState(override["id"], await shadow_progress(override), n)


def shadow_text(states) -> str:
    return "shadow period: " + "; ".join(
        f"override {s.override_id} {s.k} of {s.n}" for s in states)


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

    granted: set[str] | None = None
    suspended: list[str] = []
    created_at = proposal_row["created_at"]
    action_map: dict[int, str] = {}
    considered = False
    shadow_open = False
    reason = ""
    live_by_cap: dict[str, list[dict]] = {}
    marks: dict[str, ShadowState] = {}
    ctx: dict | None = None
    for index, (action, cap) in enumerate(zip(actions, caps)):
        if _DEFAULT_GATES.get(cap, "human") == "none" and cap in _DEFAULT_GATES:
            continue            # nothing gates this action; there is nothing to cover
        if cap not in live_by_cap:
            live_by_cap[cap] = await repo.live_gate_overrides(proposal_row["agent_id"], cap)
        live = live_by_cap[cap]
        considered = considered or bool(live)
        if cap not in _DEFAULT_GATES:
            reason = reason or f"{cap} is not overridable"
            continue
        if live and granted is None:
            granted = await _granted_capabilities(proposal_row["agent_id"])
        if live and cap not in granted:
            suspended += [o["id"] for o in live if o["id"] not in suspended]
            reason = reason or (f"the override is suspended: {cap} is outside the "
                                "agent's live pack grants")
            continue
        # A capability whose registry row requires acknowledgement (irreversible
        # or LIVE / highest blast radius) is honoured only with the operator's
        # recorded acknowledgement, and then waits its shadow period like any
        # other (floored at 1: `shadow_state`). The mark is the server-held
        # registry field; the agent-written `Action.reversibility` is never
        # read here.
        ack = requires_acknowledgement(cap)
        if ack:
            lacking = [o for o in live if not o["acknowledged_irreversible_at"]]
            if lacking and len(lacking) == len(live):
                reason = reason or f"{cap} requires an acknowledgement the override lacks"
                continue
            live = [o for o in live if o not in lacking]
        eligible = [o for o in live if o["gate"] == "none"]
        if live and not eligible:
            reason = reason or "the override's gate is not none"
            continue
        if eligible and ctx is None and any((o["scope"] or {}).get("schedule") for o in eligible):
            ctx = await _schedule_ctx(proposal_row.get("session_id"))
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
        # Every override covering this action is involved: it is decided only
        # when ALL of them are past their shadow period.
        for o in current:
            if o["id"] not in marks:
                marks[o["id"]] = await shadow_state(o)
        if not all(marks[o["id"]].complete for o in current):
            shadow_open = True
            continue
        action_map[index] = current[0]["id"]

    if not considered:
        return Decision(default)
    # What the reviewer would have seen flagged parks it, whatever covers it:
    # an override relaxes WHO decides, never what the decider is shown.
    # A suspension names itself first: the grant flag says the same thing less
    # usefully.
    flagged = await review_flags(proposal_row, granted)
    reason = (reason if suspended else "") or flagged or reason
    # The operator reopened the work item with a note — "I want eyes on this"
    # (the auditor reads the same note and never auto-confirms under it).
    reason = reason or await operator_hold_reason(proposal_row)
    shadow = tuple(marks[i] for i in sorted(marks))
    open_marks = [m for m in shadow if not m.complete]
    # Nothing else stops it, so the shadow period is the reason it parks.
    shadowed = bool(shadow_open and not reason)
    if shadowed:
        reason = shadow_text(open_marks)
    if not reason and not action_map:
        reason = "no gated action is covered"
    if reason:
        return Decision(default, reason=reason, considered=True,
                        suspended=tuple(suspended), shadow=shadow,
                        shadowed=shadowed)
    return Decision("none", override_ids=tuple(sorted(set(action_map.values()))),
                    considered=True, action_map=action_map, shadow=shadow)


async def _granted_capabilities(agent_id: str) -> set[str]:
    """The gated capabilities the agent's LIVE pack grants cover — the same
    computation the reviewer's within-granted-capabilities flag uses
    (`api/routes.get_proposal`). An unknown pack name covers nothing."""
    # gateway → runtime is the allowed import direction.
    from central_command.runtime.packs import granted_capability_names, granted_packs

    try:
        return granted_capability_names(await granted_packs(agent_id))
    except ValueError:
        return set()


async def review_flags(proposal_row: dict, granted: set[str] | None = None) -> str:
    """Why a reviewer would have seen this proposal flagged, as the park
    reason, or "" when it is clean. Calls the SAME checks the Inbox shows
    (`policy.check_actions`, `check_grants`, `check_charter_content`), the
    contract's shape check, and the evidence's RECORDED quote verdict:
    `claim_matches_source` False parks, None (not checked) does not."""
    from central_command.gateway import policy

    actions = proposal_row.get("actions") or []
    if granted is None:
        granted = await _granted_capabilities(proposal_row["agent_id"])
    flags = policy.check_actions(actions) + policy.check_grants(actions, granted)
    for a in actions:
        if _capability(a) == "charter.update":
            flags += policy.check_charter_content(
                str((a.get("arguments") or {}).get("content", "")))
    if flags:
        f = flags[0]
        return f"policy flag {f['policy']}: {f['problem']}"
    for a in actions:
        problems = validate_action_args(str(a.get("capability", "")), a.get("arguments"))
        if problems:
            return f"malformed draft: {problems[0]}"
    for e in proposal_row.get("evidence") or []:
        if isinstance(e, dict) and e.get("claim_matches_source") is False:
            return "an evidence claim does not match its recorded source"
    return ""


OPERATOR_HOLD_REASON = ("the operator reopened this work item with a note "
                        "(eyes on it), so no override decides it")


async def operator_hold_reason(proposal_row: dict) -> str:
    """The reopen-note hold, read the way the auditor reads it: a work item of
    the proposal's session whose payload carries `operator_note`."""
    sid = proposal_row.get("session_id")
    if sid and await repo.session_has_operator_note(sid):
        return OPERATOR_HOLD_REASON
    return ""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# The parks that are never evaluated (the operator's ruling, 2026-10-25): a REDRAFT — after a
# Reject or a failed execution, and a redraft of a redraft — never auto-executes
# under an override; a FOLLOW-ON parked on the approved leg goes to the operator
# by default. Each is recorded at the park itself, so no later hook or sweep
# evaluates it (`override_eval` is compare-and-set where null).
UNEVALUATED_PARKS = ("redraft", "follow_on")


async def record_unevaluated_park(proposal_id: str, kind: str) -> bool:
    if kind not in UNEVALUATED_PARKS:
        raise ValueError(f"{kind!r} is not an unevaluated park")
    return await repo.record_override_eval(proposal_id, {
        "override_id": None, "outcome": "parked", "reason": kind, "at": _now(),
    })


_live_locks: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock]" = (
    weakref.WeakKeyDictionary())


async def _announce_live(decision: Decision, proposal_id: str) -> None:
    """`override.live`: an override past its shadow period is about to make its
    first decision. Emitted ONCE per override (the log is the memory), BEFORE
    the `proposal.decided` that decision writes — write order is read order.
    The lock is per event loop (a module-level lock binds to the first loop
    that contends on it) and makes check-then-emit atomic in this process."""
    lock = _live_locks.setdefault(asyncio.get_running_loop(), asyncio.Lock())
    async with lock:
        for mark in decision.shadow:
            if mark.override_id not in decision.override_ids:
                continue
            if await repo.count_events("override.live", mark.override_id):
                continue
            await events.emit(
                "override.live", ref_id=mark.override_id,
                payload={"override_id": mark.override_id, "shadow_runs": mark.n,
                         "progress": mark.k, "proposal_id": proposal_id},
                actor="gateway",
            )


async def decide_on_park(proposal_id: str, model=None) -> dict | None:
    """The decide hook: evaluate a freshly parked proposal once and, when
    overrides cover it, decide it through the one seam.

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
        "outcome": ("decided" if decision.override_ids
                    else "shadow" if decision.shadowed else "parked"),
        "reason": decision.reason or None,
        "at": _now(),
    }
    open_marks = [m for m in decision.shadow if not m.complete]
    if open_marks:
        # Every involved override's k of N, live ones too (several overrides).
        record["shadow"] = [m.as_mark() for m in decision.shadow]
    if decision.override_ids:
        record["override_ids"] = list(decision.override_ids)
        record["action_map"] = {str(i): o for i, o in sorted(decision.action_map.items())}
    if decision.suspended:
        record["suspended"] = list(decision.suspended)
    if not await repo.record_override_eval(proposal_id, record):
        return None
    if not decision.override_ids:
        # The mark is on the proposal before anything but the log hears of it.
        if decision.shadowed:
            for m in open_marks:
                await events.emit(
                    "override.shadowed", ref_id=proposal_id,
                    payload={"override_id": m.override_id, "k": m.k, "n": m.n,
                             "proposal_id": proposal_id,
                             "overrides": [x.as_mark() for x in decision.shadow]},
                    actor="gateway",
                )
        return record
    await _announce_live(decision, proposal_id)

    from central_command.gateway import gateway

    approver = approver_for(decision.override_ids)
    try:
        await gateway.approve_and_execute(
            row["session_id"], proposal_id, approver=approver, model=model,
        )
    except OverrideRefused as e:
        # The pre-check (or the Executor's backstop) refused and handed the
        # proposal back; `override.refused` is already on the log.
        record = {**record, "outcome": "parked", "reason": f"refused: {e.reason}"}
        await repo.amend_override_eval(proposal_id, record)
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
            payload={"override_id": decision.override_id,
                     "override_ids": list(decision.override_ids), "error": str(e)},
            actor=approver,
        )
    return record


# --- safeguard 1: the pre-check and the Executor's backstop (story 5) ---------


class OverrideRefused(Exception):
    """An override approval failed its check at decision or execution time.
    Not an `ExecutionFailed`: nothing ran, nothing was written, and the
    operator's gate takes the proposal back (or, on a retry with a completed
    prefix, the failure path records it — see `gateway.retry_execution`)."""

    def __init__(self, override_id: str | None, reason: str):
        super().__init__(f"override {override_id or '?'} refused: {reason}")
        self.override_id = override_id
        self.reason = reason


def _stored_map(proposal_row: dict | None) -> dict[int, str]:
    record = (proposal_row or {}).get("override_eval") or {}
    return {int(k): v for k, v in (record.get("action_map") or {}).items()}


async def check_override_approval(
    approver: str, actions: list[dict], *, proposer: str | None,
    proposal_row: dict | None = None, offset: int = 0,
) -> None:
    """Re-check an override approval against FRESH server-held state; raise
    `OverrideRefused` naming the failing override, or return. Read-only. The
    gateway runs it before `proposal.decided`; the Executor runs it again as
    the backstop before the first action. `actions` are the ones about to run
    (on a retry, the REMAINING ones); `offset` is the index of the first of
    them in the proposal, so each is checked against ITS mapped override.

    Each named override: unrevoked, its agent the drafter (and not the
    auditor), its shadow period complete (`shadow_state`, the one reading
    `resolve` also makes), not suspended (its capability inside the
    drafter's live pack grants). Each gated action: its mapped override's
    capability is the action's, `scope_covers` holds, an acknowledgement class
    has `acknowledged_irreversible_at`. With the row:
    the proposal postdates every override, nothing a reviewer would see
    flagged (`review_flags`, grants fresh) and no operator reopen note."""
    ids = override_ids_of(approver)
    if not ids:
        return
    rows = {i: await repo.get_gate_override(i) for i in ids}
    stored = _stored_map(proposal_row)
    granted = await _granted_capabilities(proposer) if proposer else set()
    for oid, o in rows.items():
        if o is None:
            raise OverrideRefused(oid, "the override does not exist")
        if o["revoked_at"] is not None:
            raise OverrideRefused(oid, "the override was revoked")
        if o["agent_id"] != proposer or o["agent_id"] == AUDITOR_ID:
            raise OverrideRefused(oid, f"the override belongs to {o['agent_id']}, "
                                       f"not the drafter {proposer}")
        if o["gate"] != "none":
            raise OverrideRefused(oid, f"the override's gate is {o['gate']}")
        state = await shadow_state(o)
        if not state.complete:
            raise OverrideRefused(oid, "the override is still in its shadow period "
                                       f"({state.k} of {state.n})")
        if o["capability"] not in granted:
            raise OverrideRefused(oid, f"the override is suspended: {o['capability']} "
                                       "is outside the agent's live pack grants")
        if proposal_row is not None and not proposal_row["created_at"] > o["granted_at"]:
            raise OverrideRefused(oid, "the proposal predates the override")

    ctx: dict | None = None
    for i, action in enumerate(actions):
        index = offset + i
        cap = _capability(action)
        if _DEFAULT_GATES.get(cap, "human") == "none" and cap in _DEFAULT_GATES:
            continue
        oid = stored.get(index) or (ids[0] if len(ids) == 1 else None)
        if oid not in rows:
            raise OverrideRefused(oid, f"action {index} ({cap}) has no override "
                                       "of this approval mapped to it")
        o = rows[oid]
        if o["capability"] != cap:
            raise OverrideRefused(oid, f"action {index} is {cap}, the override "
                                       f"covers {o['capability']}")
        if requires_acknowledgement(cap) and not o["acknowledged_irreversible_at"]:
            raise OverrideRefused(oid, f"{cap} requires an acknowledgement the "
                                       "override lacks")
        if ctx is None and (o["scope"] or {}).get("schedule"):
            ctx = await _schedule_ctx((proposal_row or {}).get("session_id"))
        if not scope_covers(o, action, ctx):
            raise OverrideRefused(oid, f"action {index} ({cap}) is outside the "
                                       "override's scope")

    if proposal_row is not None:
        flagged = await review_flags({**proposal_row, "actions": actions}, granted)
        if flagged:
            raise OverrideRefused(ids[0] if len(ids) == 1 else None, flagged)
        hold = await operator_hold_reason(proposal_row)
        if hold:
            raise OverrideRefused(ids[0] if len(ids) == 1 else None, hold)


async def emit_refused(proposal_id: str, approver: str, refused: OverrideRefused,
                       stage: str) -> None:
    """`override.refused`: which override, why, for which proposal — written
    BEFORE the proposal is handed back, so the log reads in the right order."""
    await events.emit(
        "override.refused", ref_id=proposal_id,
        payload={"override_id": refused.override_id,
                 "override_ids": override_ids_of(approver),
                 "reason": refused.reason, "proposal_id": proposal_id,
                 "stage": stage},
        actor=approver,
    )


async def decide_landed(out: dict | None, model=None) -> dict | None:
    """The hook as a landing point calls it: decide the proposal a landing
    result names (`out["proposal_id"]` — the drafter's own, or a consulted
    specialist's when the run parked on a consult wait), once the caller's
    bookkeeping is done. A result with no proposal is a no-op.

    A hook error never breaks the caller and is never silent: an approval
    that fails is handled inside `decide_on_park` (eval amended to parked,
    `override.error`); anything else that raises here — loading the row,
    resolving the override — leaves the proposal AWAITING_HUMAN for the
    operator and writes `override.error` with `stage: "evaluate"`."""
    proposal_id = (out or {}).get("proposal_id")
    if not proposal_id:
        return None
    try:
        return await decide_on_park(proposal_id, model=model)
    except Exception as e:  # noqa: BLE001 — the operator's gate takes it back, loudly
        await events.emit(
            "override.error", ref_id=proposal_id,
            payload={"override_id": None, "error": str(e), "stage": "evaluate"},
            actor="gateway",
        )
        return None


async def override_sweep(model=None) -> dict:
    """The startup guarantee behind the landing hooks: a hook that a restart
    killed (a `CancelledError` sails past every `except Exception`) left a
    proposal no override ever evaluated. Runs at startup AFTER `audit_sweep`,
    so a bulk dismissal the auditor re-audits is decided by an override only
    once the auditor has had its turn — the same order the dispatcher keeps.

    Evaluates only proposals that are unresolved (`override_eval` null,
    AWAITING_HUMAN), created after the granted_at of a live override of their
    drafter, in a session that is no longer RUNNING (`repo.
    override_sweep_candidates`). The filter is applied here, not trusted to
    `resolve()`, because the hook records nothing when no override is in play:
    a null `override_eval` does not mean "never looked at"."""
    decided = parked = 0
    for proposal_id in await repo.override_sweep_candidates():
        record = await decide_landed({"proposal_id": proposal_id}, model=model)
        if record is None:
            continue
        if record["outcome"] == "decided":
            decided += 1
        else:
            parked += 1
    return {"decided": decided, "parked": parked}


# --- authoring and revocation: the operator only (story 8) -------------------
#
# The one way an override row comes to exist. `author` and `revoke` are called
# from exactly two places, both the operator's own direct actions: the REST
# routes (`api/routes.py`, PUT/DELETE /agents/{id}/gate-overrides) and the
# cockpit RPC (`api/nerve_gateway.py`, overrides.author / overrides.revoke). No
# agent tool, pack, capability, propose_* path, heartbeat action or chat lane
# reaches them (a source walk in tests/test_gate_overrides.py pins the callers),
# and nothing tells an agent an override exists. `override.created` and
# `override.revoked` are bookkeeping: each follows its write.


class AuthoringRefused(Exception):
    """An override the operator asked for that may not be authored. `status`
    is the HTTP code the route answers with (404 unknown, 409 duplicate or
    already revoked, 422 otherwise); `problems` says every reason."""

    def __init__(self, status: int, problems: list[str]):
        super().__init__("; ".join(problems))
        self.status = status
        self.problems = problems


_WRITE_ROWS = {c.name: c for c in REGISTRY if c.kind == "write"}
_ALL_ROWS = {c.name: c for c in REGISTRY}

_author_locks: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock]" = (
    weakref.WeakKeyDictionary())


def _same_scope(a, b) -> bool:
    return json.dumps(a or {}, sort_keys=True) == json.dumps(b or {}, sort_keys=True)


async def author(agent_id: str, capability: str, scope: dict | None = None,
                 acknowledge_irreversible: bool = False, by: str = "operator") -> dict:
    """Write a new override (always a new id; the gate relaxes to `none`
    only) and return `{"override": <listing row>, "warnings": [...]}`.

    Refused: an unknown agent or capability; the auditor as the override's
    agent (the one hard exclusion); a capability whose default gate is none;
    a scope `validate_scope` rejects; an acknowledgement class (irreversible,
    or LIVE / highest blast radius: `requires_acknowledgement`) without
    `acknowledge_irreversible`; a live override already on the same agent,
    capability and scope. The shadow period is the setting, floored for an
    acknowledgement class (`repo.create_gate_override(shadow_runs=None)`).
    An agent holding no pack that covers the capability gets the override
    anyway, born suspended, with a warning saying so."""
    capability = str(capability or "").split("@", 1)[0]
    scope = scope or {}
    if not agent_id or await repo.get_agent(agent_id) is None:
        raise AuthoringRefused(404, [f"unknown agent {agent_id!r}"])
    if capability not in _ALL_ROWS:
        raise AuthoringRefused(404, [f"unknown capability {capability!r}"])
    if agent_id == AUDITOR_ID:
        raise AuthoringRefused(422, ["the auditor never holds an override"])
    problems = validate_scope(capability, scope)
    ack_required = requires_acknowledgement(capability)
    if ack_required and not acknowledge_irreversible:
        row = _WRITE_ROWS[capability]
        problems.append(
            f"{capability} is {row.reversibility}"
            + ("" if row.reversibility == "irreversible" else " but LIVE or of the highest blast radius")
            + ": an override on it needs the operator's explicit acknowledgement")
    if problems:
        raise AuthoringRefused(422, problems)

    lock = _author_locks.setdefault(asyncio.get_running_loop(), asyncio.Lock())
    async with lock:
        if any(_same_scope(o["scope"], scope)
               for o in await repo.live_gate_overrides(agent_id, capability)):
            raise AuthoringRefused(409, [
                f"{agent_id} already holds a live override on {capability} with this "
                "scope; revoke it first (a scope change is a revoke plus a new override)"])
        row = await repo.create_gate_override(
            agent_id, capability, scope=scope, granted_by=by, shadow_runs=None,
            acknowledged_irreversible=bool(ack_required and acknowledge_irreversible),
        )
    await events.emit(
        "override.created", ref_id=row["id"],
        payload={"override_id": row["id"], "agent_id": agent_id,
                 "capability": capability, "scope": scope,
                 "scope_text": scope_text(row), "shadow_runs": row["shadow_runs"],
                 "acknowledged_irreversible": row["acknowledged_irreversible_at"] is not None},
        actor=by,
    )
    warnings = []
    if capability not in await _granted_capabilities(agent_id):
        warnings.append(
            f"{agent_id} holds no live pack that covers {capability}: the override is "
            "suspended until a pack grant covers it")
    return {"override": await describe(row), "warnings": warnings}


async def revoke(override_id: str, reason: str | None = None, by: str = "operator") -> dict:
    """Revoke = a timestamp on the row, never a delete or an edit; the next
    matching proposal parks for the operator. Returns the listing row."""
    if await repo.get_gate_override(override_id) is None:
        raise AuthoringRefused(404, [f"no override {override_id!r}"])
    reason = (reason or "").strip() or None
    if not await repo.revoke_gate_override(override_id, reason):
        raise AuthoringRefused(409, [f"override {override_id!r} is already revoked"])
    row = await repo.get_gate_override(override_id)
    await events.emit(
        "override.revoked", ref_id=override_id,
        payload={"override_id": override_id, "agent_id": row["agent_id"],
                 "capability": row["capability"], "reason": reason},
        actor=by,
    )
    return await describe(row)


async def match_stats(override: dict) -> tuple[int, datetime | None]:
    """How many proposals this override's capability and scope have covered
    while it was unrevoked (whatever decided them: the override, the operator
    in its shadow period, or a park), and when the last was created."""
    count, last = 0, None
    for p in await repo.override_match_candidates(
            override["agent_id"], override["capability"], override["granted_at"],
            override.get("revoked_at")):
        if await _proposal_covered(override, p):
            count += 1
            last = p["created_at"]
    return count, last


OVERRIDE_STATES = ("shadow", "live", "suspended", "revoked")


async def describe(override: dict, granted: set[str] | None = None) -> dict:
    """The one listing row both entry points serve (pinned field by field by
    tests/test_overrides_wire.py). `state` is revoked, else suspended (no live
    pack grant of the agent covers the capability), else shadow (k of N not
    reached), else live."""
    row = _WRITE_ROWS.get(override["capability"])
    shadow = await shadow_state(override)
    if override["revoked_at"] is not None:
        state = "revoked"
    else:
        if granted is None:
            granted = await _granted_capabilities(override["agent_id"])
        state = ("suspended" if override["capability"] not in granted
                 else "shadow" if not shadow.complete else "live")
    matches, last_match = await match_stats(override)
    return {
        "id": override["id"],
        "agent_id": override["agent_id"],
        "capability": override["capability"],
        "scope": override["scope"] or {},
        "scope_text": scope_text(override),
        "state": state,
        "shadow": {"k": shadow.k, "n": shadow.n},
        "matches": matches,
        "last_match": last_match,
        "granted_by": override["granted_by"],
        "granted_at": override["granted_at"],
        "revoked_at": override["revoked_at"],
        "reversibility": row.reversibility if row else "",
        "requires_acknowledgement": requires_acknowledgement(override["capability"]),
    }


async def list_for(agent_id: str | None = None, capability: str | None = None) -> list[dict]:
    """Every override of one agent, or on one capability, revoked included."""
    rows = await repo.list_gate_overrides(agent_id=agent_id, capability=capability)
    granted: dict[str, set[str]] = {}
    out = []
    for o in rows:
        if o["revoked_at"] is None and o["agent_id"] not in granted:
            granted[o["agent_id"]] = await _granted_capabilities(o["agent_id"])
        out.append(await describe(o, granted.get(o["agent_id"])))
    return out


# --- where the hook runs (story 17) -------------------------------------------
#
# Every `park_proposal(...)` call site, keyed `module:function`, mapped to the
# API-tier caller that runs the decide hook after its own bookkeeping — or to
# the reason no hook runs there. A source walk can find the call sites but not
# the API caller of a `runtime/` site (runtime may never import the gateway,
# so its hook lives a tier up); `tests/test_gate_overrides.py` checks this
# table against the walk, so a new site fails the suite until it is mapped.
# `docs/ARCHITECTURE.md` carries the same table in prose.

HOOK = "hook"
EXEMPT = "exempt"
SWEEP_ONLY = "sweep only"

PARK_SITES: dict[str, tuple[str, str]] = {
    "runtime/run.py:ingest_and_propose": (HOOK,
        "ingest/dispatcher.process_claimed, at function scope after the fold "
        "reservation and the auditor's bulk hook, before work.processed"),
    "runtime/steward.py:run_document": (HOOK,
        "ingest/dispatcher.process_claimed (the document handler), same place"),
    "runtime/wiki_agent.py:run_repair": (HOOK,
        "ingest/dispatcher.process_claimed (the wiki-repair handler), same place"),
    "runtime/run.py:_land": (HOOK,
        "api/routes._run_one_assigned_task (run_task, after task.review); "
        "api/orchestration._land_task_question_outcome for continue_session, "
        "resume_stopped_session and resume_parked_read (a parked propose read "
        "that drafted on re-execution, through _land_tail)"),
    "runtime/run.py:run_coach": (HOOK,
        "api/routes.coach_agent (synchronous) and routes._run_coach_detached "
        "(after task.review)"),
    "runtime/converse.py:_land_turn": (HOOK,
        "the chat lane api/nerve_gateway._chat_send; the heartbeat discussion "
        "nudge heartbeat/actions._nudge_discussion; the converse branches of "
        "api/orchestration.continue_session and resume_stopped_session; "
        "api/orchestration._land_tail for a resumed read or turn"),
    "runtime/questions.py:_settle": (HOOK,
        "api/orchestration._land_task_question_outcome (every answered-question "
        "resume: _resume_task_question, _retry_answered_question, "
        "_finish_answered_resume)"),
    "runtime/consult.py:consult": (HOOK,
        "the asker's own landing: the consult wait result carries the "
        "specialist's proposal_id, so whichever caller lands the asker's run "
        "(rows above) decides it once the asker is parked AWAITING_AGENTS"),
    "api/orchestration.py:_settle_consult_resume": (HOOK,
        "api/orchestration.resume_consult_wait and resume_parked_session "
        "(after=consulted), after the settle returns"),
    "runtime/reflection.py:_propose_episode": (SWEEP_ONLY,
        "a reflection runs as a background task off runtime's close_session "
        "with no API-tier landing; its proposal is evaluated by the startup "
        "override_sweep"),
    "gateway/gateway.py:_park_redraft": (EXEMPT,
        "a redraft is recorded unevaluated at the park (story 16)"),
    "gateway/gateway.py:finish_decision_resume": (EXEMPT,
        "a follow-on on the approved leg is recorded unevaluated at the park "
        "(story 16)"),
}
