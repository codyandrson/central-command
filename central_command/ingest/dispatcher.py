"""Backpressure dispatcher (M7) — the thing that drains the Work Ledger.

**PULL, not push.** Nothing shoves work at the agents; the loop asks "may I take
one more?" and only then claims a row. Every valve is a reason to answer no:

- **concurrency** — real in-flight count from the ledger, not a semaphore in
  memory, so a restart can't lose track of what is running (D15: start at 1).
- **approval-coupled throttle** — in the MVP the human *is* the bottleneck. If
  the Decisions Inbox already holds `dispatch_approval_limit` proposals, stop
  producing more. Draining slowly is a correctness requirement (DESIGN.md §4).
- **token budget** — a per-process ceiling, so a bad night can't burn the month.

Pick order (D16) is live-feed-first, then newest-first, and an item related to
one in flight or owing an unexecuted proposal (same thread, normalized subject,
or sender family; CLAIMED / FOLD_PENDING / DISMISS_PENDING / proposal not yet
EXECUTED) is skipped — all enforced in `repo.claim_next_work_item` (widened
2026-08-19). A fourth, fuzzy layer runs here after the claim: the semantic
freeze (`_semantic_freeze_check`) releases a claimed item back with a backoff
when its embedding is too close to anything in flight.
"""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime, timedelta, timezone

from central_command import events
from central_command.config import settings
from central_command.contract import TRANSIENT, classify_failure, retry_backoff_seconds
from central_command.db import repo
from central_command.ingest import ledger
from central_command.runtime.deps import ThreadContext
from central_command.runtime.models import resolve_model
from central_command.runtime.run import ingest_and_propose

log = logging.getLogger(__name__)

# Tokens spent by this process since start — the budget valve's counter.
_tokens_spent = 0


def tokens_spent() -> int:
    return _tokens_spent


async def check_valves() -> tuple[bool, str]:
    """May the dispatcher take one more item? Returns (ok, reason)."""
    in_flight = await repo.count_in_flight()
    if in_flight >= settings.dispatch_concurrency:
        return False, f"concurrency: {in_flight}/{settings.dispatch_concurrency} in flight"

    # Everything awaiting the human counts: proposals AND unconfirmed
    # dismissals (M14) — the drain must never outrun review of either kind.
    awaiting = await repo.count_awaiting_human() + await repo.count_dismiss_pending()
    if awaiting >= settings.dispatch_approval_limit:
        return False, (
            f"approval throttle: {awaiting} items awaiting review "
            f"(limit {settings.dispatch_approval_limit})"
        )

    budget = settings.dispatch_token_budget
    if budget and _tokens_spent >= budget:
        return False, f"token budget: {_tokens_spent}/{budget} spent"

    return True, "ok"


def _bundle_prompt(item: dict, siblings: list[dict], siblings_capped: bool = False) -> str:
    """The claimed email plus its unprocessed siblings, as data for the agent.

    Siblings are shown in full: the agent cannot judge whether a later message
    supersedes an earlier one from subject lines alone.
    """
    count_note = f"There are {len(siblings)} other unprocessed message(s) on the same thread"
    if siblings_capped:
        # `repo.thread_siblings`'s `limit` was hit exactly — a query limit
        # hides whether more exist, so say so honestly rather than implying
        # this is the complete survey (the same rule `_clip` follows).
        count_note += (
            f" (this hit the survey limit — there may be MORE than {len(siblings)}; "
            "treat this as a partial view, not the full thread)"
        )
    parts = [
        "You are handling this email:",
        "",
        item["payload"]["text"],
        "",
        count_note + ", "
        "oldest first. Decide with record_thread_decision whether your proposal "
        "covers them (fold) or whether they should each get their own turn.",
    ]
    for sib in siblings:
        parts += [
            "",
            f"--- sibling message_id: {sib['message_id']} ---",
            sib["payload"]["text"],
        ]
    return "\n".join(parts)


def _with_operator_note(prompt: str, note: str | None) -> str:
    """A reopened item carries the operator's note — trusted input, the same
    standing as rejection feedback. It rides in FRONT of the source text so the
    source itself stays quotable: a citation is checked against the stored
    payload, never against this prompt."""
    if not note:
        return prompt
    return (
        "OPERATOR NOTE (from your team lead — trusted, needs no "
        f"corroboration): {note}\n\n{prompt}"
    )


# --- the handler registry (source generalization, 2026-07-29) ------------------
# One branch, in the one path every work item takes. `work_item.kind` says WHAT
# an item is; `source` stays the transport it arrived on. An unknown kind fails
# the item LOUDLY — defaulting to triage would quietly run the wrong agent on
# the wrong shape of work, which is the failure this registry exists to make
# impossible.


async def _with_recurrence_context(prompt: str, item: dict) -> str:
    """Factual recurrence count for triage context (2026-08-17, Myprotein: 152
    individual dismissals of the same sender). Purely a fact for the agent to
    weigh — no directive language, and the ledger row itself is untouched."""
    sender = (item.get("payload") or {}).get("from")
    if not sender:
        return prompt
    n = await repo.count_prior_individual_dismissals(sender)
    if n < 3:
        return prompt
    return prompt + (
        f"\n\n[queue context] {n} earlier emails from this sender were "
        "dismissed individually, one decision each."
    )


async def _with_pending_proposals_context(prompt: str) -> str:
    """The proposals already waiting on the operator, as fact (2026-08-18).

    A parked proposal RELEASES its claim and marks its item PROCESSED, and its
    effect only reaches the world at approval — so the next email runs with the
    first proposal invisible in every direction: not in the graph, not in Jira,
    not in this session. Two unrelated emails both mentioning the same fact
    therefore each propose it (twice: "Xcel Energy is the utility provider").
    The thread lock cannot cover this and never could — the emails share no
    thread; that is exactly what makes them siblings in MEANING only.

    Facts, not directives: the agent is told what is pending and decides.
    Since 2026-08-19 the claim query DOES hard-lock the cheap relatedness
    signals (same thread, same normalized subject — including while a parked
    proposal awaits its verdict), so this context is the remaining soft layer
    for relatedness those keys cannot see: same fact, different thread and
    different subject. That judgement is semantic and stays the agent's.
    """
    pending = await repo.list_proposals("AWAITING_HUMAN")
    if not pending:
        return prompt
    shown = pending[:20]
    more = "" if len(shown) == len(pending) else f" (showing {len(shown)} of {len(pending)})"
    lines = "\n".join(f"- {p['intent']}" for p in shown)
    return prompt + (
        f"\n\n[queue context] These proposals are already awaiting the "
        f"operator's decision and have NOT taken effect yet{more}. If one of "
        "them already covers what this item calls for, do not propose it "
        "again:\n" + lines
    )


async def _handle_email(item, siblings, siblings_capped, note, model) -> dict:
    if siblings:
        prompt = _bundle_prompt(item, siblings, siblings_capped=siblings_capped)
        thread = ThreadContext(
            thread_id=item.get("thread_id"),
            sibling_message_ids=[s["message_id"] for s in siblings],
        )
    else:
        prompt = item["payload"]["text"]
        thread = None
    prompt = await _with_recurrence_context(prompt, item)
    prompt = await _with_pending_proposals_context(prompt)
    return await ingest_and_propose(
        _with_operator_note(prompt, note),
        model=model or await resolve_model(agent_id="inbox-triage"),
        thread=thread,
        # `proposal.created` is emitted by the one park path now; the email
        # path's extra context rides along so the log keeps naming the work
        # item a proposal came from.
        context={"item_id": item["id"]},
    )


async def _handle_document(item, siblings, siblings_capped, note, model) -> dict:
    """Documents set thread_id = message_id, so the sibling set is always empty
    and no bundling applies — the thread lock still serialises re-feeds of the
    same document, which is the whole reason for that choice."""
    from central_command.runtime import steward

    source_text = (item.get("payload") or {}).get("text") or ""
    return await steward.run_document(
        await _with_pending_proposals_context(_with_operator_note(source_text, note)),
        source_text=source_text,
        item_id=item["id"],
        # Deliberately NOT `model or resolve_model(...)`: the steward resolves
        # its OWN model, because the caller's is the triage one.
        model=model,
    )


async def _handle_wiki_repair(item, siblings, siblings_capped, note, model) -> dict:
    """A freshness repair brief (Decision 9). Like documents, it sets its own
    thread key — the stale claim's catalog lineage — so the repair queues
    behind that lineage's steward item and no bundling applies."""
    from central_command.runtime import wiki_agent

    return await wiki_agent.run_repair(
        _with_operator_note((item.get("payload") or {}).get("text") or "", note),
        item_id=item["id"],
        # Deliberately NOT the caller's model: the wiki agent resolves its own
        # (the steward precedent).
        model=model,
    )


class _KindHandler:
    """What to run for one work-item kind, and who the record says did it."""

    __slots__ = ("run", "actor", "audits_dismissals", "audits_bulk_dismissals")

    def __init__(
        self, run, actor: str, audits_dismissals: bool,
        audits_bulk_dismissals: bool = False,
    ) -> None:
        self.run = run
        self.actor = actor
        self.audits_dismissals = audits_dismissals
        # Named separately from `audits_dismissals` on purpose: each graduated
        # action class is its own decision, never one inherited by omission.
        self.audits_bulk_dismissals = audits_bulk_dismissals


HANDLERS: dict[str, _KindHandler] = {
    "email": _KindHandler(_handle_email, "agent:inbox-triage", True, True),
    # Document dismissals audit from 2026-08-30 (sources-catalog slice 6) — and
    # this flag is only half of it. The graduation machinery is what makes the
    # flip legitimate: documents book their verdicts under their OWN action
    # class (`dismissal.confirm.document`), so their agreement record never
    # blends with email's, and they answer to their OWN mode knob
    # (`CC_AUDITOR_DOCUMENT_MODE`, default SHADOW) — email may already be
    # active while documents are still accruing evidence. Flipping this to
    # True alone, sharing email's class and knob, is the ungraduated
    # graduation the old comment here refused; per-class is how it stops being
    # one. Auto-confirmation begins only when the operator flips that knob.
    "document": _KindHandler(_handle_document, "agent:knowledge-steward", True),
    # Wiki freshness repairs (sources-catalog slice 7, 2026-08-30). Dismissal
    # auditing stays OFF: this is an UNGRADUATED action class — it has no
    # agreement record of its own yet, and inheriting the document class's
    # flag by omission is exactly the ungraduated graduation the comment above
    # refuses.
    "wiki_repair": _KindHandler(_handle_wiki_repair, "agent:wiki-agent", False),
}


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


async def _semantic_freeze_check(item: dict) -> dict | None:
    """The fuzzy layer of the relatedness lock (2026-08-19): freeze a claimed
    item whose embedding is too close to anything in flight or owing an
    unexecuted proposal. Catches what no exact key can — same topic, different
    thread, subject AND sender (the Kaiser-PT / Amazon-equipment case) — and it
    is the only relatedness signal that survives multi-source ingestion, since
    thread/subject/sender keys are all email-shaped.

    Runs AFTER the atomic claim on purpose: the SQL claim stays race-safe, the
    fuzzy judgement happens out here, and the worst race outcome is one extra
    release/retry — never a duplicate run. Returns a payload describing the
    match (→ release with backoff), or None to proceed. FAIL-OPEN everywhere:
    an unreachable embedder or a check bug must never block dispatch — the
    hard keys and the pending-proposals prompt context still stand behind it.
    """
    if settings.demo_mode or not settings.semantic_freeze_enabled:
        return None
    try:
        from central_command.integrations.neo4j_writer import embed

        vector = item.get("embedding")
        if not vector:
            text = (
                f"{item.get('subject') or ''}\n"
                f"{(((item.get('payload') or {}).get('text')) or '')[:1500]}"
            )
            vector = await embed(text)
            if vector is None:
                return None  # embedder unreachable / empty text — fail open
            await repo.set_work_item_embedding(item["id"], vector)
            item["embedding"] = vector

        best, best_row = 0.0, None
        for row in await repo.in_flight_embeddings(exclude_id=item["id"]):
            score = _cosine(vector, row["embedding"])
            if score > best:
                best, best_row = score, row
        if best_row is not None and best >= settings.semantic_freeze_threshold:
            return {
                "reason": (
                    f"semantically related to in-flight item {best_row['id']} "
                    f"(similarity {best:.2f} >= {settings.semantic_freeze_threshold})"
                ),
                "related_item_id": best_row["id"],
                "related_subject": best_row.get("subject"),
                "score": round(best, 3),
            }
        return None
    except Exception:  # noqa: BLE001 — fail open, the gate is best-effort
        log.exception("semantic freeze check failed — proceeding without it")
        return None


async def dispatch_once(model=None) -> dict | None:
    """Claim at most one item from the queue and run it. Returns a result dict,
    or None when a valve is shut or the queue is empty."""
    ok, _reason = await check_valves()
    if not ok:
        return None

    item = await repo.claim_next_work_item()
    if item is None:
        return None
    return await process_claimed(item, model=model, actor="dispatcher")


async def dispatch_item(item_id: str, model=None) -> dict | None:
    """Run one named item now — the operator's hand-fed path.

    This deliberately skips the *valves* (the operator asking for one specific
    email is not agents outrunning review) but not the *ledger*: the item is
    claimed atomically, honours the thread lock, and goes terminal exactly like
    queued work. Returns None if the item was already taken or its thread is busy.
    """
    item = await repo.claim_specific_work_item(item_id)
    if item is None:
        return None
    return await process_claimed(item, model=model, actor="operator")


async def process_claimed(item: dict, model=None, actor: str = "dispatcher") -> dict:
    """Run one already-claimed item to its proposal. The single path every email
    takes, however it arrived — so thread folding, provenance and the event log
    can't be bypassed by feeding work in a different way.

    The ledger row goes terminal only once an outcome is committed: PROCESSED
    when the agent has either paused on a proposal or decided no action is
    needed.

    A failure releases the row back to the queue, and WHICH failure it was
    decides what that costs (outage equivalence, slice 4):

    - **SEMANTIC** — the dependency answered and said no. Today's behaviour,
      byte-for-byte: the attempt the claim consumed stays consumed, and at
      `dispatch_max_attempts` the row parks FAILED for a human to look at.
    - **TRANSIENT** — the dependency was down; nothing about this item was ever
      judged. The attempt is handed back and the row waits out an exponential
      backoff (`work_item.not_before`). **There is deliberately no exhaustion on
      this path.** An email queued during a week-long outage must still process
      afterwards — that is the doctrine's whole point, and a cap here would
      quietly convert "the provider was down" into "this email is bad". The
      outage itself is already loud (every release carries `transient: true`,
      and `heartbeat.error` / `feed.error` shout independently), so nothing is
      hidden by waiting.
    """
    global _tokens_spent

    log.info("dispatch: claimed %s (%s)", item["id"], item.get("subject") or "no subject")
    kind = item.get("kind") or "email"
    await events.emit(
        "work.claimed",
        ref_id=item["id"],
        payload={"subject": item.get("subject"), "feed": item.get("feed"),
                 "thread_id": item.get("thread_id"), "attempt": item["attempts"],
                 "kind": kind},
        actor=actor,
    )

    handler = HANDLERS.get(kind)
    if handler is None:
        # Not transient: retrying an unroutable row would burn every attempt
        # and end in the same place. Park it FAILED now, loudly, with the kind
        # named — a human adds the handler or fixes the row.
        error = (
            f"no handler for work-item kind {kind!r} "
            f"(known: {', '.join(sorted(HANDLERS))})"
        )
        log.error("dispatch: item %s — %s", item["id"], error)
        await repo.finish_work_item(item["id"], "FAILED", error=error)
        await events.emit(
            "work.failed",
            ref_id=item["id"],
            payload={"error": error, "kind": kind, "attempts": item["attempts"]},
            actor="dispatcher",
        )
        return {"item_id": item["id"], "ok": False, "error": error}

    # Survey the thread before running: one session owns a thread (the claim
    # already locked it), so this is a consistent view for the fold decision.
    # Explicit `limit` (matching `repo.thread_siblings`'s own default) so the
    # exact-hit check below stays correct even if the two defaults ever drift.
    _SIBLING_SURVEY_LIMIT = 500
    siblings = await repo.thread_siblings(
        item.get("thread_id"), item["id"], limit=_SIBLING_SURVEY_LIMIT
    )
    siblings_capped = len(siblings) == _SIBLING_SURVEY_LIMIT

    try:
        # Refs-only rows (M13 backlog sweep) carry no content until now —
        # fetch at claim time, so provider reads happen at processing pace.
        # Inside the try: a provider flap gets the same release/retry treatment
        # as any other transient failure.
        await ledger.hydrate_work_item(item)

        # Post-hydration freezes, now that the item has content — BEFORE
        # spending sibling hydrations or a run. First the exact keys re-check:
        # a refs-only stub had no subject/sender keys when the claim's SQL
        # lock ran, so ask again now that hydration materialized them. Then
        # the semantic layer. A freeze is not the item's fault: the attempt is
        # handed back and the row waits out the same backoff a transient
        # release uses (the growing exponent is a feature here — a proposal
        # pending overnight rechecks ever less often instead of churning the
        # claim loop).
        freeze = None
        if await repo.work_item_related_busy(item["id"]):
            freeze = {"reason": "related item in flight (keys appeared at hydration)"}
        if freeze is None:
            freeze = await _semantic_freeze_check(item)
        if freeze is not None:
            releases = int(item.get("transient_releases") or 0)
            backoff = retry_backoff_seconds(releases)
            not_before = datetime.now(timezone.utc) + timedelta(seconds=backoff)
            await repo.release_work_item(
                item["id"], error=freeze["reason"], count_attempt=False,
                not_before=not_before,
            )
            await events.emit(
                "work.frozen",
                ref_id=item["id"],
                payload={**freeze, "subject": item.get("subject"),
                         "backoff_seconds": backoff,
                         "not_before": not_before.isoformat()},
                actor="dispatcher",
            )
            log.info("dispatch: froze %s — %s", item["id"], freeze["reason"])
            return {"item_id": item["id"], "ok": True, "frozen": True, **freeze}

        for s in siblings:
            await ledger.hydrate_work_item(s)

        # A reopened item carries the operator's note — trusted input, same
        # standing as rejection feedback.
        note = (item.get("payload") or {}).get("operator_note")
        result = await handler.run(item, siblings, siblings_capped, note, model)
    except asyncio.CancelledError:
        # A shutdown mid-run (uvicorn cancels tasks that outlive stop()'s 30s
        # grace) is CancelledError — a BaseException the clause below never
        # sees. Without this release the row stays CLAIMED, holding its thread
        # lock, and the startup sweep can't tell it from live work. Same
        # standing as a transient failure: never the item's fault, attempt
        # handed back, no backoff.
        await repo.release_work_item(
            item["id"], error="run cancelled (shutdown)", count_attempt=False
        )
        await events.emit(
            "work.released",
            ref_id=item["id"],
            payload={"error": "run cancelled (shutdown)", "attempt": item["attempts"],
                     "transient": True, "cancelled": True},
            actor="dispatcher",
        )
        raise
    except Exception as e:  # noqa: BLE001 — one bad email must not kill the loop
        log.exception("dispatch: item %s failed", item["id"])
        transient = classify_failure(e) == TRANSIENT
        if transient:
            releases = int(item.get("transient_releases") or 0)
            backoff = retry_backoff_seconds(releases)
            not_before = datetime.now(timezone.utc) + timedelta(seconds=backoff)
            await repo.release_work_item(
                item["id"], error=str(e), count_attempt=False,
                not_before=not_before,
            )
            await events.emit(
                "work.released",
                ref_id=item["id"],
                payload={"error": str(e), "attempt": item["attempts"],
                         "max_attempts": settings.dispatch_max_attempts,
                         "transient": True,
                         "transient_releases": releases + 1,
                         "backoff_seconds": backoff,
                         "not_before": not_before.isoformat()},
                actor="dispatcher",
            )
        elif item["attempts"] >= settings.dispatch_max_attempts:
            await repo.finish_work_item(item["id"], "FAILED", error=str(e))
            await events.emit(
                "work.failed",
                ref_id=item["id"],
                payload={"error": str(e), "attempts": item["attempts"],
                         "transient": False},
                actor="dispatcher",
            )
        else:
            await repo.release_work_item(item["id"], error=str(e))
            await events.emit(
                "work.released",
                ref_id=item["id"],
                payload={"error": str(e), "attempt": item["attempts"],
                         "max_attempts": settings.dispatch_max_attempts,
                         "transient": False},
                actor="dispatcher",
            )
        return {"item_id": item["id"], "ok": False, "error": str(e),
                "transient": transient}

    _tokens_spent += int(result.get("tokens") or 0)

    if not result.get("deferred"):
        # "No action needed" is a CLAIM, not an outcome (M14) — the same rule
        # folds follow. The item parks with the agent's rationale until the
        # operator confirms (→ PROCESSED) or reopens it with a note.
        rationale = str(result.get("output") or "").strip() or "no rationale given"
        await repo.mark_dismiss_pending(item["id"], rationale, result.get("session_id"))
        await events.emit(
            "work.dismiss_pending",
            ref_id=item["id"],
            payload={"subject": item.get("subject"), "rationale": rationale[:300],
                     "kind": kind},
            actor=handler.actor,
        )
        # Phase 4: the independent auditor re-derives the no-action claim from
        # the raw email. Shadow mode records a verdict beside the item; active
        # mode may confirm a concur. Off (default) or erroring, the item just
        # waits for the operator like it always did. The auditor resolves its
        # OWN model — the triage model passed here has the wrong output shape.
        # Per-KIND: only the graduated action classes run it (see HANDLERS),
        # and each runs under its OWN class and mode — `audit_kwargs_for_kind`
        # is that seam, so the email path is unchanged by anything added there.
        if handler.audits_dismissals and settings.auditor_enabled:
            from central_command.gateway import auditor

            await auditor.audit_dismissal(
                item["id"],
                item.get("subject"),
                (item.get("payload") or {}).get("text") or "",
                rationale,
                # A reopened item carries the operator's note — they asked for
                # eyes on it, so the auditor may opine but never close it.
                allow_auto_confirm=not note,
                **auditor.audit_kwargs_for_kind(kind),
            )
        return {"item_id": item["id"], "ok": True, "dismiss_pending": True, **result}

    # PROCESSED means "the agent took its turn and what it produced now lives
    # its own life", NOT "the world changed" — a parked proposal marks the item
    # PROCESSED too, and only its approval changes anything. A consult wait is
    # the same shape: triage is parked until the specialist finishes, and the
    # item is no longer this loop's business either way. (Consequence worth
    # naming: a parked run holds NO claim, so `count_in_flight` — which counts
    # CLAIMED — never sees it. The valve that actually paces review is
    # `dispatch_approval_limit`, not `dispatch_concurrency`.)
    await repo.finish_work_item(
        item["id"], "PROCESSED", session_id=result.get("session_id")
    )

    # ponytail: a turn that ended in a CONSULT WAIT carries no
    # `folded_message_ids`, so its siblings are not reserved here — the
    # resumed run parks its proposal through `_settle_consult_resume`, which
    # never comes back through this loop. That degrades in the SAFE direction
    # the fold doctrine demands: an unreserved sibling stays UNPROCESSED and
    # gets its own turn, so the cost is a duplicate look, never a dropped
    # email. Upgrade path if duplicates get noisy: carry the decision through
    # the wait state and reserve on the resumed park.
    #
    # Reserve any folded siblings against the proposal. FOLD_PENDING is NOT
    # terminal — the gateway commits it on approval or releases it on rejection,
    # so a wrong fold costs a delay rather than a dropped email.
    folded = result.get("folded_message_ids") or []
    if folded and result.get("proposal_id"):
        reserved = await repo.mark_fold_pending(folded, result["proposal_id"])
        await events.emit(
            "work.fold_pending",
            ref_id=result["proposal_id"],
            payload={"message_ids": folded, "reserved": reserved,
                     "rationale": result.get("fold_rationale"),
                     "thread_id": item.get("thread_id")},
            actor=handler.actor,
        )

        # Graduated 2026-08-17: the auditor is the secondary check on a BULK
        # dismissal too — agreement approves it through the normal gateway
        # seam, disagreement (or any error) leaves it AWAITING_HUMAN. Runs
        # here, after the agent's turn fully landed and its folds are
        # reserved, exactly like the dismissal hook: the run is over and the
        # session is parked on disk, so the approval's resume of that same
        # session cannot deadlock this turn. Bulk proposals raised in CHAT
        # reserve no folds and never come through here — nothing to sample,
        # so they stay human-gated. `audit_bulk_dismissal` is a no-op for any
        # other kind of proposal.
        if handler.audits_bulk_dismissals and settings.auditor_enabled:
            from central_command.gateway import auditor

            try:
                await auditor.audit_bulk_dismissal(
                    result["proposal_id"],
                    allow_auto_confirm=not note,
                    resume_model=model,
                )
            except Exception as e:  # noqa: BLE001 — an auditor crash never fails the turn
                await events.emit(
                    "audit.error", ref_id=result["proposal_id"],
                    payload={"error": str(e), "action_class": "work.bulk_dismiss"},
                    actor="auditor",
                )

    await events.emit(
        "work.processed",
        ref_id=item["id"],
        payload={"deferred": bool(result.get("deferred")),
                 "session_id": result.get("session_id"),
                 "tokens": result.get("tokens", 0)},
        actor="dispatcher",
    )
    return {"item_id": item["id"], "ok": True, **result}


# Advisory-lock key for the dispatcher singleton lease. Found live (2026-07-19):
# a SIGTERMed server draining an open SSE connection kept its dispatcher loop
# polling for hours — two processes split the queue safely (skip locked), but
# the zombie ran OLD config, so its half of the work skipped the auditor. The
# lease makes "one drain loop per database" a database invariant, not a hope.
async def sweep_orphaned_sessions(age_seconds: int) -> int:
    """Fail RUNNING sessions whose process died mid-run, and land their tasks.

    Called with age 0 from app startup (nothing runs in this process yet, so
    any RUNNING row is orphaned by construction — the session analogue of
    `reclaim_stale_work_items(0)`) and with the stale threshold from
    `Dispatcher.start`, which an operator may invoke mid-life alongside
    legitimate live runs. Before the startup call existed, a session whose run
    died young enough stayed RUNNING forever: unstoppable (the cooperative
    stop flag had no reader) and uncloseable (close refuses mid-turn) —
    jira-expert sat that way on 2026-08-17."""
    orphaned, landed = await repo.fail_stale_running_sessions(age_seconds)
    if orphaned:
        log.warning("swept %d orphaned RUNNING session(s)", orphaned)
    # The task's own record, loudly: a board that read IN_PROGRESS against a
    # FAILED transcript is what made three dead runs look alive for days.
    for task_id in landed:
        await events.emit(
            "task.run_failed",
            ref_id=task_id,
            payload={"error": "run died with its process",
                     "transient": False, "swept": True},
            actor="system",
        )
    return orphaned


DISPATCH_LEASE_KEY = 0x67760001


class Dispatcher:
    """The background drain loop. One per DATABASE — enforced by a Postgres
    advisory lock held on a dedicated connection for the life of the loop, so
    a dying process releases it automatically."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._tasks: set[asyncio.Task] = set()
        self._stopping = asyncio.Event()
        self._lease_conn = None
        self.last_reason = "not started"

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def _acquire_lease(self) -> bool:
        if self._lease_conn is not None:  # ours already (e.g. stop/start cycle)
            return True
        conn = await repo._conn()
        try:
            got = await conn.fetchval("select pg_try_advisory_lock($1)", DISPATCH_LEASE_KEY)
        except Exception:
            await conn.close()
            raise
        if not got:
            await conn.close()
            return False
        self._lease_conn = conn
        return True

    async def _release_lease(self) -> None:
        if self._lease_conn is None:
            return
        try:
            await self._lease_conn.execute(
                "select pg_advisory_unlock($1)", DISPATCH_LEASE_KEY
            )
        finally:
            await self._lease_conn.close()
            self._lease_conn = None

    async def start(self) -> None:
        if self.running:
            return
        if not await self._acquire_lease():
            # Another process is already draining this database. Refusing is
            # the safety property: a second loop would run under ITS config —
            # different gates for the same queue.
            self.last_reason = "refused: another dispatcher holds the lease"
            log.warning("dispatch: %s", self.last_reason)
            await events.emit(
                "dispatch.lease_refused",
                payload={"lease_key": DISPATCH_LEASE_KEY},
                actor="dispatcher",
            )
            return
        # A previous process may have died holding claims; take them back first.
        # Age 0, not the stale threshold: with the lease held and nothing yet
        # in flight in THIS process, any CLAIMED row is orphaned by
        # construction — and under systemd the restart lands in seconds, so a
        # dead process's row is always far younger than any threshold that
        # must also exceed a legitimate run's duration.
        recovered = await repo.reclaim_stale_work_items(0)
        if recovered:
            log.warning("dispatch: reclaimed %d stale claim(s)", recovered)
        # Same sweep for live-transcript sessions: runs are in-process, so a
        # session still RUNNING long past any real run's duration died with its
        # process. The partial transcript is kept (honest record). The stale
        # threshold (not 0) because an operator may start dispatch mid-life,
        # with legitimate runs live in this same process.
        await sweep_orphaned_sessions(settings.dispatch_stale_claim_seconds)
        self._stopping.clear()
        self._task = asyncio.create_task(self._loop())
        await events.emit(
            "dispatch.started",
            payload={"reclaimed_stale": recovered,
                     "concurrency": settings.dispatch_concurrency,
                     "approval_limit": settings.dispatch_approval_limit},
            actor="dispatcher",
        )

    async def stop(self) -> None:
        was_running = self.running
        self._stopping.set()
        if self._task is not None:
            await asyncio.wait([self._task], timeout=30)
            self._task = None
        # In-flight runs keep their claims; let them land rather than cancel —
        # a hard shutdown cancels them anyway and process_claimed releases the
        # row on CancelledError.
        if self._tasks:
            await asyncio.wait(self._tasks, timeout=30)
        await self._release_lease()
        if was_running:
            await events.emit("dispatch.stopped", actor="dispatcher")

    async def _run_claimed(self, item: dict) -> None:
        """process_claimed handles its own failures; this catches anything that
        escapes it (e.g. an emit before its try) so a spawned task never dies
        with an unretrieved exception."""
        try:
            await process_claimed(item, actor="dispatcher")
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — one bad item must not go unlogged
            log.exception("dispatch: item %s failed outside its handler", item["id"])

    async def _loop(self) -> None:
        log.info("dispatch: loop started (concurrency=%d, approval limit=%d)",
                 settings.dispatch_concurrency, settings.dispatch_approval_limit)
        stalled_on: str | None = None
        while not self._stopping.is_set():
            try:
                ok, reason = await check_valves()
                self.last_reason = reason

                # Log valve *transitions* only — a poll loop that emitted every
                # iteration would bury the log in noise within minutes.
                if not ok and reason != stalled_on:
                    stalled_on = reason
                    await events.emit(
                        "dispatch.stalled", payload={"reason": reason}, actor="dispatcher"
                    )
                elif ok and stalled_on is not None:
                    await events.emit(
                        "dispatch.resumed", payload={"was": stalled_on}, actor="dispatcher"
                    )
                    stalled_on = None

                if ok:
                    # Claim HERE, serially, so the row is CLAIMED before the
                    # next valve check — `count_in_flight` is ledger-backed and
                    # sees it immediately, which is what makes the concurrency
                    # valve hold under parallel runs. Only the RUN is a task.
                    item = await repo.claim_next_work_item()
                    if item is not None:
                        task = asyncio.create_task(self._run_claimed(item))
                        self._tasks.add(task)
                        task.add_done_callback(self._tasks.discard)
                        continue  # work available — go straight round again
                    self.last_reason = "queue empty"
            except Exception:  # noqa: BLE001 — the loop outlives any single error
                log.exception("dispatch: loop iteration failed")
                self.last_reason = "error (see logs)"
            try:
                await asyncio.wait_for(
                    self._stopping.wait(), timeout=settings.dispatch_poll_seconds
                )
            except asyncio.TimeoutError:
                pass
        log.info("dispatch: loop stopped")

    async def status(self) -> dict:
        ok, reason = await check_valves()
        return {
            "running": self.running,
            "may_dispatch": ok,
            "reason": reason if not ok else self.last_reason,
            "in_flight": await repo.count_in_flight(),
            "awaiting_human": await repo.count_awaiting_human(),
            "tokens_spent": _tokens_spent,
            "valves": {
                "concurrency": settings.dispatch_concurrency,
                "approval_limit": settings.dispatch_approval_limit,
                "token_budget": settings.dispatch_token_budget,
            },
            "ledger": await repo.ledger_counts(),
        }


dispatcher = Dispatcher()
