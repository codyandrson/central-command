"""The independent auditor — Phase 4's answer to approval fatigue (D8/D12).

The design rules honoured here:

- **Independent re-derivation.** The auditor judges from the RAW stored email,
  never from the triage agent's summary — the agent's claimed rationale is
  shown for comparison only. Evidence is a reference, not the agent's account.
- **Shadow-first, graduated per action-class, revocable.** `CC_AUDITOR_ENABLED`
  (default false) turns it on in `shadow` mode: verdicts are recorded on the
  log and shown to the operator, but every gate stays exactly as it was.
  `CC_AUDITOR_MODE=active` graduates ONE action class — dismissal
  confirmations, the lowest-risk class there is (a wrong concur costs a missed
  email review, never a world change). Flip the config back and the human gate
  returns; graduation is configuration, never code.
- **The auditor is itself fallible.** Active mode auto-confirms only CONCUR
  verdicts; every CHALLENGE parks for the operator with the auditor's reason
  shown. An auditor error degrades to the human gate — it can never wedge the
  drain, and it can never confirm anything.
"""

from __future__ import annotations

from pydantic import BaseModel
from pydantic_ai import Agent

from central_command import events
from central_command.config import settings
from central_command.contract import is_transient
from central_command.db import repo

# The action classes graduated so far. Anything else the auditor ever covers
# gets its own class name, its own shadow period, and its own revocable flag.
AUDIT_ACTION_CLASS = "dismissal.confirm"
# Bulk dismissals, graduated 2026-08-17 by explicit operator decision (The operator):
# same shape one rung up — triage proposes the pattern, the auditor is the
# secondary check, agreement executes and only DISAGREEMENT reaches the
# operator. Same off switch (`CC_AUDITOR_ENABLED` / `CC_AUDITOR_MODE=active`)
# and the same per-kind flag on the dispatcher's HANDLERS.
BULK_AUDIT_ACTION_CLASS = "work.bulk_dismiss"


class AuditVerdict(BaseModel):
    """concur = the dismissal holds; challenge = the email deserves a human."""

    concur: bool
    rationale: str


# The auditor is a TEAM MEMBER: its built-in charter lives in the runtime tier
# (runtime/auditor_profile.py) so the coaching loop can fall back to it, and
# the CURRENT governed version (M11) wins at audit time — the operator edits
# and coaches the auditor exactly like any other agent.
from central_command.runtime.auditor_profile import AGENT_ID, CHARTER as AUDITOR_CHARTER
from central_command.runtime.templates import render_v0


async def ensure_registered() -> None:
    await repo.upsert_agent(AGENT_ID, "Auditor", "", "audit no-action claims")


def build_auditor(model, charter: str | None = None) -> Agent:
    return Agent(
        model,
        name="auditor",
        output_type=AuditVerdict,
        system_prompt=charter or render_v0(AUDITOR_CHARTER),
    )


async def _resolve_model(model=None):
    if model is not None:
        return model
    if settings.demo_mode:
        from central_command.runtime.spike_model import make_auditor_model

        return make_auditor_model()
    from central_command.runtime.models import resolve_model

    return await resolve_model(agent_id="auditor")


async def audit_dismissal(
    item_id: str,
    subject: str | None,
    email_text: str,
    agent_rationale: str,
    allow_auto_confirm: bool = True,
    model=None,
) -> dict | None:
    """Audit one parked dismissal and apply the outcome for the current mode.

    Returns the audit record, or None when the auditor is disabled or errored —
    in both cases the item simply stays DISMISS_PENDING behind the human gate.
    """
    if not settings.auditor_enabled:
        return None

    prompt = (
        f"EMAIL (subject: {subject or '(none)'}):\n"
        f"---\n{email_text}\n---\n\n"
        f'The triage agent dismissed it, saying: "{agent_rationale}"\n\n'
        "Does 'no action needed' hold? Re-derive from the email itself."
    )
    try:
        await ensure_registered()
        governed = await repo.current_charter(AGENT_ID)
        run = await build_auditor(
            await _resolve_model(model), charter=governed["content"] if governed else None
        ).run(prompt)
        verdict = run.output
    except Exception as e:  # noqa: BLE001 — a down auditor degrades to the human gate
        await events.emit(
            "audit.error", ref_id=item_id,
            payload={"error": str(e), "transient": is_transient(e)},
            actor="auditor",
        )
        return None

    mode = settings.auditor_mode if settings.auditor_mode == "active" else "shadow"
    record = {
        "action_class": AUDIT_ACTION_CLASS,
        "verdict": "concur" if verdict.concur else "challenge",
        "rationale": verdict.rationale,
        "mode": mode,
        "auto_confirmed": False,
        # An item the operator reopened is theirs: the auditor may opine but
        # never close it — a reopen note means "I want eyes on this".
        "operator_hold": not allow_auto_confirm,
    }

    # The verdict goes on the log BEFORE any state change it authorises (the
    # M8 ordering rule) — a confirmation must never appear before its reason.
    # `auto_confirmed` is excluded: at emit time the confirmation hasn't run,
    # so the event cannot know it — the outcome's authoritative record is the
    # `work.dismissal_confirmed` event (actor=auditor) that follows, and the
    # post-flip copy persisted on the work item. (2026-08-18: the field used
    # to ride along, permanently false, and read as "never auto-confirmed".)
    await events.emit(
        "audit.verdict", ref_id=item_id,
        payload={k: v for k, v in record.items() if k != "auto_confirmed"},
        actor="auditor",
    )

    if mode == "active" and verdict.concur and allow_auto_confirm:
        if await repo.confirm_dismissal(item_id):
            record["auto_confirmed"] = True
            await events.emit(
                "work.dismissal_confirmed",
                ref_id=item_id,
                payload={"by": "auditor", "action_class": AUDIT_ACTION_CLASS,
                         "rationale": verdict.rationale},
                actor="auditor",
            )

    await repo.record_audit(item_id, record)
    return record


def _spread(rows: list[dict], k: int = 5) -> list[dict]:
    """Up to `k` rows spread evenly across the set (first, middle…, last) —
    deterministic, so the same proposal is always judged on the same sample."""
    if len(rows) <= k:
        return rows
    idx = sorted({round(i * (len(rows) - 1) / (k - 1)) for i in range(k)})
    return [rows[i] for i in idx]


async def audit_bulk_dismissal(
    proposal_id: str,
    allow_auto_confirm: bool = True,
    model=None,
    resume_model=None,
) -> dict | None:
    """Audit a parked bulk dismissal and, on agreement, approve it.

    Independent re-derivation, same rule as the single-email class: the ground
    truth is the rows the proposal actually RESERVED (`folded_into` =
    proposal_id, FOLD_PENDING), hydrated fresh from the mailbox — never the
    samples the agent pinned into its own args.

    Returns the audit record, or None when the auditor is disabled, the
    proposal is not a bulk dismissal, it reserved nothing (a chat-raised one),
    or the auditor errored. In every one of those cases the proposal simply
    stays AWAITING_HUMAN behind the operator's gate.
    """
    if not settings.auditor_enabled:
        return None

    prop = await repo.load_proposal(proposal_id)
    if prop is None or prop["status"] not in ("AWAITING_HUMAN", "PROPOSED"):
        return None
    action = next(
        (a for a in prop["actions"]
         if str(a.get("capability", "")).split("@")[0] == BULK_AUDIT_ACTION_CLASS),
        None,
    )
    if action is None:
        return None
    query = str((action.get("arguments") or {}).get("query") or "")

    covered = [
        r for r in await repo.work_items_folded_into(proposal_id)
        if r["state"] == "FOLD_PENDING"
    ]
    if not covered:
        return None  # nothing reserved, so nothing to sample — human gate.

    try:
        from central_command.ingest.ledger import provider_body, provider_uuid
        from central_command.integrations import email_facade

        sampled = _spread(covered)
        blocks = []
        for r in sampled:
            uuid = provider_uuid(r["message_id"])
            msg = await email_facade.get_message(uuid) if uuid else {}
            blocks.append(
                f"--- from: {msg.get('from') or '(unknown)'} | "
                f"subject: {msg.get('subject') or r.get('subject') or '(none)'}\n"
                f"{provider_body(msg)[:600]}\n---"
            )

        prompt = (
            f"BULK DISMISSAL: the triage agent proposes closing {len(covered)} "
            f"queued email(s) in ONE decision.\n"
            f"Gmail query it pinned: {query}\n"
            f'Its rationale: "{prop["intent"]}"\n\n'
            f"{len(blocks)} of the covered emails, sampled across the set:\n"
            + "\n".join(blocks)
            + "\n\nDoes 'no action needed' hold for this whole set? Re-derive "
            "from the emails themselves: are they genuinely no-action bulk "
            "mail, is the rationale supported by them, and is the query scoped "
            "to that pattern rather than to mail nobody has looked at?"
        )
        await ensure_registered()
        governed = await repo.current_charter(AGENT_ID)
        run = await build_auditor(
            await _resolve_model(model), charter=governed["content"] if governed else None
        ).run(prompt)
        verdict = run.output
    except Exception as e:  # noqa: BLE001 — a down auditor degrades to the human gate
        await events.emit(
            "audit.error", ref_id=proposal_id,
            payload={"error": str(e), "transient": is_transient(e),
                     "action_class": BULK_AUDIT_ACTION_CLASS},
            actor="auditor",
        )
        return None

    mode = settings.auditor_mode if settings.auditor_mode == "active" else "shadow"
    record = {
        "action_class": BULK_AUDIT_ACTION_CLASS,
        "verdict": "concur" if verdict.concur else "challenge",
        "rationale": verdict.rationale,
        "mode": mode,
        "auto_confirmed": False,
        "operator_hold": not allow_auto_confirm,
        "covered": len(covered),
        "sampled_message_ids": [r["message_id"] for r in sampled],
    }
    # Before the approval it authorises (the M8 ordering rule). This is also
    # the only place the verdict is stored: the proposal table has no column
    # for it, and `api.routes.get_proposal` reads it back off the log.
    # `auto_confirmed` is excluded: the approval hasn't run at emit time, so
    # the event cannot know it — whether this proposal auto-approved is
    # recorded authoritatively by proposal.executed's approver ('auditor' vs
    # 'human:lee'). (2026-08-18: the field used to ride along, permanently
    # false, and read as "never auto-confirmed".)
    await events.emit(
        "audit.verdict", ref_id=proposal_id,
        payload={k: v for k, v in record.items() if k != "auto_confirmed"},
        actor="auditor",
    )

    if mode == "active" and verdict.concur and allow_auto_confirm:
        # THE one approval seam — everything downstream (proposal.decided
        # before execute, commit_folds, the drafter's resume) is the normal
        # path, unchanged. `approver` names the mechanism honestly, exactly as
        # the dismissal class's `by: auditor` does.
        from central_command.gateway import gateway

        try:
            await gateway.approve_and_execute(
                prop["session_id"], proposal_id,
                approver="auditor", model=resume_model,
            )
            record["auto_confirmed"] = True
        except Exception as e:  # noqa: BLE001 — a failed approval is the human gate
            await events.emit(
                "audit.error", ref_id=proposal_id,
                payload={"error": str(e), "transient": is_transient(e),
                         "action_class": BULK_AUDIT_ACTION_CLASS,
                         "stage": "auto_approve"},
                actor="auditor",
            )
    return record


async def audit_sweep(model=None) -> dict:
    """Re-audit parked dismissals and bulk proposals that carry no verdict.

    The inline hooks fire exactly once, at park time — a restart cancels them
    with CancelledError (a BaseException their `except Exception` never sees)
    and a transient 5xx degrades to the human gate, and in both cases nothing
    ever retried: the item sat DISMISS_PENDING (or the proposal AWAITING_HUMAN)
    forever, reading as "the auditor never showed up" (found live 2026-08-18,
    8 orphans across 5 restarts). Runs at startup beside `resume_sweep`, same
    doctrine: the inline hook is the accelerator, this is the guarantee.

    A recorded CHALLENGE is never re-rolled — the repo queries exclude anything
    with a verdict. Errors here degrade to the human gate exactly like inline.
    """
    if not settings.auditor_enabled:
        return {"items": 0, "proposals": 0}

    items = 0
    for row in await repo.unaudited_dismiss_pending():
        payload = row.get("payload") or {}
        record = await audit_dismissal(
            row["id"],
            row.get("subject"),
            payload.get("text") or "",
            payload.get("dismissal_rationale") or "no rationale given",
            allow_auto_confirm=not payload.get("operator_note"),
            model=model,
        )
        items += record is not None

    proposals = 0
    for prop in await repo.unaudited_bulk_proposal_ids():
        record = await audit_bulk_dismissal(
            prop["id"],
            allow_auto_confirm=not prop["operator_hold"],
            model=model,
        )
        proposals += record is not None
    return {"items": items, "proposals": proposals}


# --- the agreement record (D8 graduation evidence) ---------------------------
# Shadow mode's whole point: every auditor verdict lands on the log next to
# the operator's eventual confirm/reopen for the same item. Pairing them gives
# the four quadrants that decide graduation:
#   concur    + confirm -> agreement (the auto-confirmable case)
#   challenge + reopen  -> agreement (the challenge was vindicated)
#   challenge + confirm -> auditor over-cautious (noise; costs one extra look)
#   concur    + reopen  -> AUDITOR MISS (the dangerous kind — in active mode
#                          this email would have been buried)


def pair_verdicts(rows: list[dict]) -> dict:
    """Pure pairing over event rows (any kinds; ordered by id ascending).

    A verdict pairs with the NEXT operator outcome for its item, so an item
    that cycles (dismiss → reopen → dismiss again) contributes one pair per
    round. Auditor auto-confirms (active mode) carry no operator signal and
    are tallied separately, as are verdicts still awaiting an outcome.
    """
    pending: dict[str, dict] = {}
    pairs: list[dict] = []
    auto_confirmed = 0

    def close(item_id: str, outcome: str, outcome_event: int, note: str | None = None) -> None:
        verdict = pending.pop(item_id, None)
        if verdict is not None:
            pairs.append({"item_id": item_id, "outcome": outcome,
                          "outcome_event": outcome_event, "note": note, **verdict})

    for e in rows:
        kind, ref, payload = e["kind"], e["ref_id"], e.get("payload") or {}
        if kind == "audit.verdict":
            # One class per record: a bulk verdict is keyed on a PROPOSAL and
            # pairs with an approval, not with a confirm/reopen — counting it
            # here would sit in `awaiting_outcome` forever and skew the rate.
            if (payload.get("action_class") or AUDIT_ACTION_CLASS) != AUDIT_ACTION_CLASS:
                continue
            pending[ref] = {
                "verdict": payload.get("verdict"),
                "rationale": payload.get("rationale", ""),
                "mode": payload.get("mode"),
                "verdict_event": e["id"],
            }
        elif kind == "work.dismissal_confirmed":
            if e.get("actor") == "auditor":
                auto_confirmed += 1
                pending.pop(ref, None)
            else:  # operator: single confirm carries ref_id, Confirm-all a list
                for item_id in [ref] if ref else payload.get("item_ids", []):
                    close(item_id, "confirmed", e["id"])
        elif kind == "work.reopened":
            close(ref, "reopened", e["id"], note=payload.get("note"))

    q = {"concur_confirmed": 0, "challenge_reopened": 0,
         "challenge_confirmed": 0, "concur_reopened": 0}
    for p in pairs:
        q[f"{p['verdict']}_{p['outcome']}"] += 1

    agreed = q["concur_confirmed"] + q["challenge_reopened"]
    return {
        "pairs": pairs,
        "quadrants": q,
        "agreed": agreed,
        "total_paired": len(pairs),
        "agreement_rate": round(agreed / len(pairs), 3) if pairs else None,
        "auditor_misses": [p for p in pairs
                           if p["verdict"] == "concur" and p["outcome"] == "reopened"],
        "over_cautious": [p for p in pairs
                          if p["verdict"] == "challenge" and p["outcome"] == "confirmed"],
        "auto_confirmed": auto_confirmed,
        "awaiting_outcome": len(pending),
    }


def signals_from_report(report: dict, subjects: dict[str, str] | None = None) -> list[dict]:
    """The auditor's coaching signals ARE the disagreement quadrants: every
    over-cautious challenge and every miss is the operator's recorded verdict
    on the auditor's judgment, citable by its verdict event. Pure, so the
    derivation is testable without a database."""
    subjects = subjects or {}
    signals: list[dict] = []
    for p in report.get("over_cautious", []):
        subject = subjects.get(p["item_id"]) or p.get("subject") or p["item_id"]
        signals.append({
            "kind": "audit-overcautious",
            "event_id": p["verdict_event"],
            "feedback": (
                f"on “{subject}” you challenged with “{p['rationale']}” — "
                "the team lead confirmed the dismissal anyway: no action was needed"
            ),
        })
    for p in report.get("auditor_misses", []):
        subject = subjects.get(p["item_id"]) or p.get("subject") or p["item_id"]
        note = f" — their note: “{p['note']}”" if p.get("note") else ""
        signals.append({
            "kind": "audit-miss",
            "event_id": p["verdict_event"],
            "feedback": (
                f"on “{subject}” you concurred with “{p['rationale']}” — "
                f"the team lead REOPENED it: action WAS needed{note}"
            ),
        })
    return signals


async def auditor_signals() -> list[dict]:
    """Candidate coaching inputs for the auditor, fresh from the log."""
    return signals_from_report(await agreement_report())


async def agreement_report() -> dict:
    """The shadow-vs-operator record, computed fresh from the event log every
    time — the log is the truth; nothing here is cached or re-stated."""
    rows = await repo.list_events_of_kinds(
        ["audit.verdict", "work.dismissal_confirmed", "work.reopened"]
    )
    report = pair_verdicts(rows)
    # Subjects for the disagreement lists — that's what the operator reviews
    # when deciding graduation.
    ids = {p["item_id"] for p in report["auditor_misses"] + report["over_cautious"]}
    subjects = await repo.work_item_subjects(sorted(ids)) if ids else {}
    for p in report["auditor_misses"] + report["over_cautious"]:
        p["subject"] = subjects.get(p["item_id"])
    return report
