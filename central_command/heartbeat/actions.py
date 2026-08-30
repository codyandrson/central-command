"""The heartbeat's action registry — executable truth, like the capability
packs: an action kind that is not in ACTIONS cannot be scheduled or fired.

Every action resolves to a lever the operator already has by hand, and every
lever it may touch is declared on the spec (parity-tested against an explicit
allowlist in tests/test_heartbeat.py — extending the registry means extending
the allowlist in the same commit, reviewable like a pack change).

TRUST BOUNDARY: this package must never import `gateway.gateway` (approve) or
`gateway.executor` (perform) — the heartbeat starts work, it never finishes
it. ONE narrow, guard-tested exemption below: `gateway.auditor`'s
`agreement_report`, a pure read over the event log that happens to live in
the gateway package. The guard test pins that exact import and nothing wider.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

# The thinking levels a schedule may pin. Imported from the model seam so the
# registry and the resolver can never disagree about what is valid.
from central_command.runtime.models import THINKING_LEVELS


@dataclass(frozen=True)
class ActionSpec:
    kind: str
    description: str
    params: dict[str, str]          # param name -> documentation
    required: tuple[str, ...]       # params that must be present
    levers: tuple[str, ...]         # the operator levers this action may touch
    run: Callable[[str, dict], Awaitable[dict]]  # (schedule_id, params) -> summary
    # QUIET-ELIGIBILITY (see `engine.fire`). None = LOUD, the default: every
    # firing writes `heartbeat.fired` BEFORE the action and an outcome after.
    # A predicate marks the action quiet-eligible: a firing whose result is
    # immaterial writes NOTHING to the append-only log (errors always do).
    #
    # An action may only declare one if it is BOTH:
    #   1. non-authorising — it starts no gated work and opens no gate, so its
    #      heartbeat record is provenance, never authorisation; and
    #   2. self-recording — anything it actually does emits its own complete
    #      event (feed.poll -> work.enrolled/feed.polled), so a material run is
    #      never invisible even though the heartbeat row came after it.
    # Which actions qualify is parity-tested in tests/test_heartbeat.py.
    material: Callable[[dict], bool] | None = None
    # ENUM PARAMS: param name -> the only values it may take. Enforced in
    # `validate_action`, so a schedule naming a value the action cannot render
    # is refused at create/patch time rather than failing at 07:30 inside a
    # background loop nobody is watching. Documenting the choices in `params`
    # prose alone would be guidance with no mechanism behind it.
    choices: dict[str, tuple[str, ...]] = field(default_factory=dict)


async def _feed_poll(schedule_id: str, params: dict) -> dict:
    """One idempotent mail sweep — the operator's 'check mail now' button.
    Enrollment only; the dispatcher's valves govern when any of it is worked."""
    from central_command.ingest.feed import poll_once

    return await poll_once()


async def _dispatch_window(schedule_id: str, params: dict) -> dict:
    """Open the drain for a bounded window, then close it. No approval for the
    valve itself; downstream proposals gate as always, and the approval-coupled
    throttle still halts the drain regardless of the window."""
    from central_command import events
    from central_command.ingest.dispatcher import dispatcher

    minutes = float(params.get("minutes") or 10)
    if minutes <= 0:
        raise ValueError("minutes must be positive")
    if dispatcher.running:
        # Continuous drain (CC_DISPATCH_ENABLED) or an overlapping window —
        # nothing to open, and nothing to close later that we didn't open.
        return {"opened": False, "reason": "drain already running"}
    await dispatcher.start()
    if not dispatcher.running:  # lease refused: another process drains this DB
        return {"opened": False, "reason": dispatcher.last_reason}

    async def _close() -> None:
        await asyncio.sleep(minutes * 60)
        if dispatcher.running:
            await dispatcher.stop()
            await events.emit(
                "heartbeat.window_closed",
                ref_id=schedule_id,
                payload={"minutes": minutes},
                actor="heartbeat",
            )

    # If the process dies mid-window the closer dies with it — and so does the
    # dispatcher and its lease, so a restart comes up with the drain OFF
    # (unless CC_DISPATCH_ENABLED says otherwise). Fails toward closed.
    asyncio.create_task(_close())
    return {"opened": True, "minutes": minutes}


async def _task_create(schedule_id: str, params: dict) -> dict:
    """Create an ASSIGNED first-class Task — THE task-create path (one source
    of truth with the Task Board and the "+" dialog). The task runs freely; a
    proposal for a gated write parks in the Decisions Inbox like any other.

    Optional `model` / `thinking` in action_params are the SCHEDULE's override —
    a recurring job can run heavier (or cheaper) than the agent's own setting
    without changing the agent. They sit below a session override and above the
    agent row; `thinking` is validated against THINKING_LEVELS by
    `create_and_run_task`, which raises rather than silently dropping it."""
    # Late import: routes imports this package for its API endpoints.
    from central_command.api.routes import create_and_run_task

    result = await create_and_run_task(
        params["instructions"],
        params.get("title"),
        params["agent_id"],
        actor=f"heartbeat:{schedule_id}",
        model_name=params.get("model") or None,
        thinking=params.get("thinking") or None,
    )
    return {
        k: result.get(k)
        for k in ("task_id", "deferred", "proposal_id", "session_id")
        if result.get(k) is not None
    }


async def _sandbox_run_script(schedule_id: str, params: dict) -> dict:
    """Run an already-synced script inside the owning agent's own sandbox
    session, then hand the run to that agent as an evaluation task.

    No per-call profile: this executes under the OWNING AGENT's own grants,
    in the sandbox session it already set up (script already written/copied
    in via `sandbox_write_file`/`sandbox_copy_in` in an earlier turn) — a
    scheduled rerun of an already-reviewed script, never a new exit (decided
    2026-08-03). Captures the standard evaluation contract: exit code,
    `result.json` if the script wrote one, and a capped stdout/stderr tail.
    Failure always creates the evaluation task; on success it only does when
    the schedule asks for it (`success_creates_task`) — the common tick is a
    clean run that costs the owning agent nothing.
    """
    import json

    from central_command.api.routes import create_and_run_task
    from central_command.integrations import sandbox_client

    agent_id = params["agent_id"]
    session_id = params["session_id"]
    command = params["command"]
    success_creates_task = str(params.get("success_creates_task", "")).strip().lower() in (
        "true", "1", "yes",
    )

    sandbox_id = (await sandbox_client.create_session(agent_id, session_id))["sandbox_id"]
    run = await sandbox_client.exec_cmd(sandbox_id, command)
    exit_code = run.get("exit_code")
    success = exit_code == 0

    result_json = None
    try:
        read = await sandbox_client.read_file(sandbox_id, "result.json")
        result_json = json.loads(read["content"])
    except Exception:  # noqa: BLE001 — result.json is optional, not every script writes one
        pass

    out = {
        "exit_code": exit_code, "success": success,
        "stdout": run.get("stdout", ""), "stderr": run.get("stderr", ""),
        "result_json": result_json,
    }
    if not success or success_creates_task:
        title = params.get("title") or f"Evaluate scheduled sandbox run ({schedule_id})"
        instructions = (
            f"A scheduled sandbox script just ran in your own sandbox session "
            f"({session_id!r}) with command `{command}`.\n\n"
            f"exit_code: {exit_code}\n\n"
            f"stdout (tail):\n{run.get('stdout', '')[-2000:]}\n\n"
            f"stderr (tail):\n{run.get('stderr', '')[-2000:]}\n"
            + (f"\nresult.json:\n{json.dumps(result_json, indent=2)}\n"
               if result_json is not None else "")
            + "\nInspect your sandbox workspace (`sandbox_read_file`/`sandbox_exec`) "
              "for the full log or any declared artifacts, then evaluate the run."
        )
        task = await create_and_run_task(
            instructions, title, agent_id, actor=f"heartbeat:{schedule_id}",
        )
        out["task_id"] = task.get("task_id")
    return out


async def _report_audit_agreement(schedule_id: str, params: dict) -> dict:
    """Snapshot the auditor's shadow-vs-operator agreement record onto the
    event log, so the D8 evidence accrues visibly without anyone remembering
    to visit the URL. Read-only."""
    # THE one allowed gateway import (see module docstring): a pure read over
    # the event log. The guard test pins this exact line.
    from central_command.gateway.auditor import agreement_report

    from central_command import events

    report = await agreement_report()
    await events.emit(
        "heartbeat.report",
        ref_id=schedule_id,
        payload={"report_kind": "audit_agreement", "report": report},
        actor="heartbeat",
    )
    return {"report_kind": "audit_agreement", "emitted": True}


def _last_turn_by(run_state: dict | None) -> str:
    """"agent" or "operator" — who authored the newest turn in a discussion.

    Read off the persisted transcript with the Workspace's own reader
    (`durable.simplify_messages`), so this sees exactly what the operator sees.
    Only the operator's turns are `user-prompt`; everything after the last one
    (the model's text, its thinking, its tool calls) is the agent speaking. A
    transcript holding only the seeded opening question has no `user-prompt` at
    all, and the agent spoke last — which is the truth: nobody has replied yet.
    """
    from central_command.runtime import durable

    turns = durable.simplify_messages(run_state or {})
    last_operator = -1
    for i, turn in enumerate(turns):
        if turn.get("kind") == "user-prompt":
            last_operator = i
    if last_operator < 0:
        return "agent"
    return "operator" if last_operator == len(turns) - 1 else "agent"


async def _nudge_discussion(row: dict, task_title: str) -> None:
    """One ordinary turn into the clarification conversation.

    Ordinary on purpose: it lands in the transcript, so a nudge is visible,
    coaching-grade record rather than hidden automation. It authorises nothing
    — the agent it wakes holds exactly the tools it already had, and any gated
    action it proposes still parks in the Decisions Inbox.
    """
    from central_command.runtime import converse

    message = (
        "(automated stall nudge — this discussion has been quiet)\n\n"
        f"You opened this discussion to unblock task {task_title}. The operator "
        "has replied since. If you have what you need, call "
        "`conclude_discussion` with your summary. If you do not, say plainly "
        "what is still missing."
    )
    await converse.conversation_turn(row["discussion_session_id"], message)


async def _discussion_sweep(schedule_id: str, params: dict) -> dict:
    """Find clarification discussions gone quiet and apply the one remedy each
    needs: flag it at the operator (S1) or nudge the agent (S2).

    Nothing watched the tier-3 loop before this, so a discussion nobody
    concluded left its task parked forever. Which remedy a lane needs is
    `runtime.discussions.classify_discussion_stall`'s call — pure, and tested
    without a database; this action does the reading, the emitting and the
    stamping.
    """
    from datetime import datetime, timezone

    from central_command import events
    from central_command.config import settings
    from central_command.db import repo
    from central_command.runtime.discussions import classify_discussion_stall

    now = datetime.now(timezone.utc)
    rows = await repo.list_open_discussions()
    stalled: list[str] = []
    nudged: list[str] = []
    for row in rows:
        session = {
            "id": row["discussion_session_id"],
            "updated_at": row["session_updated_at"],
        }
        verdict = classify_discussion_stall(
            item=row,
            session=session,
            last_turn_by=_last_turn_by(row.get("session_run_state")),
            now=now,
            stall_hours=settings.discussion_stall_hours,
        )
        if verdict is None:
            continue
        waiting_hours = round(
            (now - row["session_updated_at"]).total_seconds() / 3600, 1
        )
        if verdict == "S1":
            # The flag first, then the stamp: a stamp written before its record
            # would silence a lane that was never actually flagged.
            await events.emit(
                "discussion.stalled",
                ref_id=row["id"],
                payload={
                    "agent_id": row["agent_id"],
                    "task_id": row["task_id"],
                    "discussion_session_id": row["discussion_session_id"],
                    "waiting_hours": waiting_hours,
                },
                actor="heartbeat",
            )
            await repo.mark_discussion_stalled(row["id"])
            stalled.append(row["id"])
        else:
            titles = (
                await repo.task_titles([row["task_id"]]) if row.get("task_id") else {}
            )
            await _nudge_discussion(
                row, titles.get(row["task_id"]) or row.get("task_id") or "your task"
            )
            await repo.mark_discussion_nudged(row["id"])
            nudged.append(row["id"])
    return {"checked": len(rows), "stalled": stalled, "nudged": nudged}


async def _graph_verify_sweep(schedule_id: str, params: dict) -> dict:
    """Audit approved graph episodes after ingestion settles (2026-08-19 spec).

    The body lives in `gateway.graph_auditor` (judging a verification is part
    of the gate), reached through `api.orchestration` because this package may
    never import the gate — same route as `retry.sweep`."""
    from central_command.api import orchestration

    return await orchestration.graph_verify_sweep(
        settle_minutes=int(params.get("settle_minutes", 10)),
        missing_after_minutes=int(params.get("missing_after_minutes", 360)),
    )


async def _retry_sweep(schedule_id: str, params: dict) -> dict:
    """Drive every unit parked by a dependency outage whose dependency is now
    up — the convergence half of outage equivalence (slice 5).

    Three worklists, oldest-first, each through the driver that already owns it:
    sessions parked AWAITING_RESUME (slice 2), approved proposals parked
    RETRY_PENDING (slice 3), and due retry-parked tasks (slice 4). Each is gated
    on a cheap probe of the dependency its retry needs, and **a gated skip
    consumes no attempt** — a long outage costs latency, never a unit's budget.

    **Work ITEMS are deliberately not swept.** A transiently-released email
    converges through the normal dispatch loop (`work_item.not_before` +
    mail-poll/drain cadence) and has no attempt ceiling at all; sweeping it here
    would be a second convergence path racing the dispatcher for the same rows.

    The body lives in `api.orchestration.retry_sweep` because it must reach
    `gateway.retry_execution`, and this package may never import the gate.
    """
    from central_command.api import orchestration

    return await orchestration.retry_sweep()


# --- the EA's attention loop (2026-07-30) -------------------------------------
# Four contact kinds, one action. Each is a brief built from the SAME
# deterministic snapshot with a different framing, so "what do we tell the
# operator" is one templating seam rather than four schedules carrying four
# hand-written mission texts (which is what `task.create` would have forced —
# it passes the operator's instructions through verbatim, by design).
# "digest" KEEPS its kind string for data compat but is framed as the
# END-OF-DAY WRAP-UP since the 2026-08-21 EA charter (v5) daily arc;
# "week_ahead" (same change) is the Sunday look-ahead.

# "onboarding_tour" (2026-08-23) is the odd one out on purpose: it is the ONE
# kind that is not a windowed report, so it takes none of the snapshot/calendar
# machinery below and skips the attention budget (an operator who just ran
# setup asked for this contact — deferring it would be the control plane
# protecting them from a thing they requested). It is a CONTACT kind and not a
# setup script so the tour is giftable, re-runnable, and independent of Claude
# Code. Nothing schedules it: setup creates a DISABLED schedule and fires it
# once through the run-now button.
EA_CONTACT_KINDS: tuple[str, ...] = (
    "digest", "check_in", "morning_report", "week_ahead", "onboarding_tour",
)

# The event whose newest occurrence anchors the "since" window. It exists
# because `engine.fire` stamps `last_fired_at` BEFORE the action runs, so the
# schedule row can never say when the last DELIVERED contact was — only when
# the engine last tried. Anchoring on delivery is also what makes a
# budget-deferred contact lossless: nothing was delivered, so the window stays
# open and the next contact covers the gap.
EA_DELIVERED_EVENT = "ea.contact_delivered"

_EA_FRAMING: dict[str, str] = {
    "digest": (
        "This is the END-OF-DAY WRAP-UP — close what the morning opened. Tell "
        "the operator what got done today, what is still open (and now waiting "
        "on whom), and what comes first tomorrow. Then ask about the gaps the "
        "record cannot close: anything sitting undecided, any question a "
        "teammate raised, anything that looks stuck. The calendar block is "
        "TOMORROW's: flag anything on it the operator should prepare tonight — "
        "an early start, a conflict, a meeting with no agenda — so tomorrow's "
        "morning brief starts warm."
    ),
    "check_in": (
        "This is a CHECK-IN at a natural breakpoint in the operator's day. Keep "
        "it short. Only reach for their attention if something in the window "
        "genuinely needs them now — a decision the queue is stuck behind, an "
        "error, a teammate blocked. If the window is quiet, say so in a line "
        "and ask nothing. Use the calendar block for TIMING — say what is next "
        "and how long they have, and do not ask for attention inside a meeting."
    ),
    "morning_report": (
        "This is the MORNING REPORT — the first contact of the day. Cover what "
        "landed overnight and what is already waiting, then ask what the "
        "operator wants prioritised today. This is the one contact where asking "
        "about priorities is the point rather than an interruption. Frame the "
        "day against the calendar block: what is on it, where the free stretches "
        "are, and any conflict the operator has not resolved."
    ),
    "week_ahead": (
        "This is the WEEK-AHEAD look — audit the coming week before it starts. "
        "The calendar block is tomorrow's; read the rest of the week yourself "
        "with read_calendar (days 2 through 7). Surface what the operator "
        "should fix before Monday: overbooked days, meetings with no prep or "
        "travel buffer around them, conflicts, and anything on the board or in "
        "open follow-ups that has a deadline this week but no time on the "
        "calendar serving it. Propose the calendar changes that would fix what "
        "you find rather than only listing problems."
    ),
}

# Which day each contact kind looks at. The wrap-up fires at 17:00, when
# today's calendar is spent — the useful question then is what tomorrow
# demands. The week-ahead anchors on tomorrow (Monday) and reads onward
# itself.
_CALENDAR_DAY: dict[str, int] = {
    "morning_report": 0, "check_in": 0, "digest": 1, "week_ahead": 1,
}


async def _ea_tour(schedule_id: str) -> dict:
    """The `onboarding_tour` contact: hand the EA its host brief and let it run.

    Same task path as every other contact — `create_and_run_task` — and no
    snapshot, no calendar, no attention budget (see the EA_CONTACT_KINDS note).
    The brief itself is composed in `runtime/tour.py` against the LIVE roster
    and the LIVE grants, so what it promises the operator is what the agents
    can actually do.
    """
    from central_command import events
    from central_command.api.routes import create_and_run_task
    from central_command.runtime import ea, tour

    result = await create_and_run_task(
        await tour.brief(), "Meet the team", ea.AGENT_ID,
        actor=f"heartbeat:{schedule_id}",
    )
    await events.emit(
        EA_DELIVERED_EVENT,
        ref_id=schedule_id,
        payload={
            "kind": "onboarding_tour",
            "task_id": result.get("task_id"),
            "session_id": result.get("session_id"),
            "opened_lane": bool(result.get("discussion_session_id")),
        },
        actor="heartbeat",
    )
    return {
        "delivered": True, "kind": "onboarding_tour",
        **{k: result.get(k) for k in ("task_id", "item_id", "session_id",
                                      "discussion_session_id", "proposal_id")
           if result.get(k) is not None},
    }


async def _ea_contact(schedule_id: str, params: dict) -> dict:
    """Wake the executive assistant to contact the operator.

    Builds the window, composes the deterministic snapshot, checks the
    attention budget, and — only then — creates an assigned task through THE
    task path. The budget check sits before `create_and_run_task` and after the
    (free) snapshot, so a deferred contact spends no tokens at all.
    """
    import json
    from datetime import datetime, timedelta, timezone

    from central_command import events
    from central_command.db import repo
    from central_command.reports import ea_calendar, ea_followups, ea_report
    from central_command.runtime import attention, ea

    kind = str(params.get("kind") or "").strip()
    if kind not in EA_CONTACT_KINDS:
        # `validate_action` refuses this at create time; this is the belt for a
        # row written before the enum existed (or edited straight in SQL).
        raise ValueError(
            f"ea.contact kind must be one of {list(EA_CONTACT_KINDS)} (got {kind!r})"
        )

    await ea.ensure_registered()

    if kind == "onboarding_tour":
        return await _ea_tour(schedule_id)

    now = datetime.now(timezone.utc)
    delivered = [
        r for r in await repo.list_events_of_kinds([EA_DELIVERED_EVENT])
        if (r.get("payload") or {}).get("kind") == kind
    ]
    # Per KIND: the morning report and the digest cover different halves of the
    # day, and windowing them off each other would make each one's window
    # depend on whether the other fired.
    since = delivered[-1]["created_at"] if delivered else now - timedelta(hours=24)

    snapshot = await ea_report.snapshot(since)

    allowed, used, budget = await attention.may_open_lane(ea.AGENT_ID)
    if not allowed:
        await events.emit(
            "ea.contact_deferred",
            ref_id=schedule_id,
            payload={"kind": kind, "contacts_today": used, "budget": budget,
                     "since": snapshot["window"]["since"]},
            actor="heartbeat",
        )
        # No task, no run, no tokens — and no `ea.contact_delivered`, so the
        # next contact of this kind still windows from the same `since`.
        return {"delivered": False, "reason": "attention budget spent",
                "kind": kind, "contacts_today": used, "budget": budget}

    # AFTER the budget check on purpose: a deferred contact spends nothing,
    # including no call to Google. A calendar outage degrades the contact and
    # never cancels it — the DELIVERED event is what anchors the next window.
    try:
        calendar = await ea_calendar.day(_CALENDAR_DAY[kind])
    except Exception as e:  # noqa: BLE001 — degrade, never lose the contact
        calendar = {"available": False, "error": str(e)}
    if calendar.get("reason") != "not configured":
        snapshot["calendar"] = calendar

    # Same rule as the calendar, same placement: AFTER the budget check, so a
    # deferred contact makes no graph call either. An outage degrades this key
    # and never the contact — `follow_ups` always ends up in the block, unlike
    # `calendar` which is omitted when unconfigured (there is no "not
    # configured" state for the graph: it is always in play).
    try:
        follow_ups = await ea_followups.open_loops()
    except Exception as e:  # noqa: BLE001 — degrade, never lose the contact
        follow_ups = {"available": False, "error": str(e)}
    snapshot["follow_ups"] = follow_ups

    brief = (
        f"{_EA_FRAMING[kind]}\n\n"
        "The block below is the control plane's own snapshot of the record over "
        "the stated window. It is GROUND TRUTH and cost you nothing — narrate "
        "it, do not re-derive it. Counts under `queues` are current state; "
        "everything under `activity` is what happened inside the window. Use "
        "your read tools only for follow-ups, and cite what you actually open.\n"
        + ("`calendar` is the operator's REAL calendar for the day it names, "
           "already conflict-checked for you (declined invitations and all-day "
           "markers are not commitments and are not conflicts). This block is "
           "READ-ONLY: you never change the calendar yourself — if something on "
           "it should change, propose the change (if you hold that pack) or "
           "tell the operator, and never imply anything has already been "
           "created, moved or cancelled.\n" if "calendar" in snapshot else "")
        + ("`follow_ups` is a read of YOUR OWN prior private episodes — the "
           "commitments and open questions you have already distilled and "
           "recorded. It is GROUND TRUTH for what is currently open: narrate "
           "it, never re-derive it. When something in the window resolves one "
           "of these, propose a closing episode naming what resolved it.\n"
           if follow_ups.get("available") else "")
        + "\n"
        "```json\n" + json.dumps(snapshot, indent=2, default=str) + "\n```\n\n"
        "If this is worth the operator's attention AND you have a question only "
        "they can settle, open the conversation with "
        "`ask_operator(needs_discussion=True)` — your report goes in the "
        "question. If you have nothing they need to reply to, answer in plain "
        "text instead; a report nobody must respond to should not cost a "
        "conversation. Your daily contact budget is enforced by the control "
        f"plane ({used} of {budget} used today)."
    )
    title = {
        "digest": "End-of-day wrap-up", "check_in": "Check-in",
        "morning_report": "Morning report", "week_ahead": "Week ahead",
    }[kind]

    # Late import for the same reason `_task_create` has one: routes imports
    # this package for its API endpoints. THE task path — same validation,
    # same events, same gating as the "+" dialog.
    from central_command.api.routes import create_and_run_task

    result = await create_and_run_task(
        brief, title, ea.AGENT_ID, actor=f"heartbeat:{schedule_id}",
    )
    await events.emit(
        EA_DELIVERED_EVENT,
        ref_id=schedule_id,
        payload={
            "kind": kind,
            "since": snapshot["window"]["since"],
            "task_id": result.get("task_id"),
            "session_id": result.get("session_id"),
            "opened_lane": bool(result.get("discussion_session_id")),
            "contacts_today": used,
            "budget": budget,
        },
        actor="heartbeat",
    )
    return {
        "delivered": True, "kind": kind, "since": snapshot["window"]["since"],
        **{k: result.get(k) for k in ("task_id", "item_id", "session_id",
                                      "discussion_session_id", "proposal_id")
           if result.get(k) is not None},
    }


def _raw_provider_id(provider_model: str) -> str:
    """Strip a leading 'provider/' so a catalog id and a configured
    deployment's provider_model compare on the same string regardless of
    which side carries the prefix ('anthropic/claude-sonnet-4-5-20250929'
    and 'claude-sonnet-4-5-20250929' are the same raw id)."""
    return provider_model.split("/", 1)[1] if "/" in provider_model else provider_model


def compute_discovery_drift(
    credential_name: str,
    provider: str,
    catalog: list[dict],
    deployments: list[dict],
    unhealthy: list[dict],
    skip: list[str],
) -> dict:
    """Pure diff of ONE stored credential's live catalog against the proxy's
    configured deployments — plain deterministic set math, no agent and no
    LLM. Called once per stored credential (two credentials may share a
    provider, e.g. two Anthropic keys).

    `deployments` is `litellm.list_models()`'s `models` list (any deployment,
    managed or hand-configured — never propose adding something that already
    exists under another alias). `catalog` is `litellm.provider_catalog`'s raw
    entries (each carrying `id`). `unhealthy` is
    `litellm.check_model_health()`'s `unhealthy` list. `skip` is the
    operator's own never-propose list of raw provider ids. Ownership
    (`managed`) is credential-tied: a deployment is this credential's iff its
    `litellm_params.litellm_credential_name` equals `credential_name` — the
    `cc_managed` marker is gone.
    """
    catalog_by_id = {
        str(m["id"]): m for m in catalog if isinstance(m, dict) and m.get("id")
    }
    # A deployment's explicit `provider` (custom_llm_provider) is usually
    # unset — the provider normally rides only as the model-string prefix
    # ("anthropic/claude-…"), verified live 2026-08-20 — so match on either.
    this_provider = [
        d for d in deployments
        if d.get("provider") == provider
        or str(d.get("provider_model") or "").startswith(f"{provider}/")
    ]
    configured_ids = {
        _raw_provider_id(d["provider_model"])
        for d in this_provider if d.get("provider_model")
    }
    skip_set = set(skip or [])

    missing = [
        catalog_by_id[cid] for cid in catalog_by_id
        if cid not in configured_ids and cid not in skip_set
    ]

    # Managed = OURS to touch. Everything else on this provider is hand-tuned
    # and read-only to autodiscovery, whether it is stale or unhealthy.
    managed = [d for d in this_provider if d.get("credential_name") == credential_name]
    stale = [
        d for d in managed
        if d.get("provider_model")
        and _raw_provider_id(d["provider_model"]) not in catalog_by_id
    ]
    managed_names = {d.get("model_name") for d in managed}
    unhealthy_managed = [u for u in unhealthy if u.get("model") in managed_names]

    return {"missing": missing, "stale": stale, "unhealthy": unhealthy_managed}


_DISCOVERY_INTRO = (
    "This is a scheduled model-autodiscovery pass (see AUTODISCOVERY in "
    "your charter). The block below is GROUND TRUTH computed by plain code "
    "— each STORED CREDENTIAL's live model catalog diffed against what the "
    "proxy already has configured. It cost you nothing; narrate it, do not "
    "re-derive it.\n\n"
)

_DISCOVERY_NAMING = (
    "CANONICAL NAMING: a model_name is lowercase-kebab, family + version, "
    "with the provider prefix and any date suffix stripped "
    "(`claude-sonnet-4-5-20250929` -> `claude-sonnet-4-5`). When two "
    "providers offer the same underlying model, they share ONE canonical "
    "model_name — that sharing IS the routing group. If a mapping is "
    "ambiguous, `ask_operator`; a 'never add this one' answer belongs in "
    "the operator's own skip list (the `autodiscovery_decisions` app "
    "setting) — note it for them to record, you don't write it yourself.\n\n"
)


def _add_brief(credential_name: str, models: list[dict]) -> str:
    """One task's worth of MISSING models — a bounded slice of one
    credential's catalog, never the whole drift."""
    import json

    return (
        _DISCOVERY_INTRO
        + _DISCOVERY_NAMING
        + f"MISSING under credential {credential_name!r} (catalog models not "
        "registered anywhere, under any alias) — register each, then MEASURE "
        "it: a catalog entry carries an id and little else, and a private "
        "model's real limits and capabilities differ from its public card. "
        "Make ONE batched proposal for EXACTLY these models via "
        "propose_litellm_change, one litellm.add_model action per model, "
        "carrying only what you know for certain (the format prefix, the "
        "credential, `mode`, and `model_info.base_model` = the public "
        "counterpart if one exists, for cost-map defaults). On approval the "
        "Executor runs the full capability probe and the execution result "
        "carries the declared→observed diff — read it and propose "
        "litellm.update_model with that `suggested_model_info` (re-run "
        "litellm_probe_model yourself if you need more detail). That is "
        "where supports_* and the token limits come from, never from a "
        "guess. Every add MUST set "
        f"litellm_params.litellm_credential_name to {credential_name!r} — "
        "NEVER embed a key. That reference is both auth AND the ownership "
        "marker: deployments tied to a stored credential are yours; anything "
        "else (env-keyed, hand-tuned, local fleet) is read-only. Put "
        "capability facts in LiteLLM's NATIVE model_info fields — "
        "supports_vision, supports_function_calling, supports_pdf_input, "
        "max_input_tokens, max_output_tokens, mode — NEVER an invented dict; "
        "Central Command's own attachment gate reads native supports_vision, so "
        "an invented field is invisible to it. Also set `description`.\n\n"
        "SKIP CANDIDATES: if any model in this slice looks like a dated "
        "snapshot of an undated alias already covered elsewhere, or carries "
        "an imminent shutdown/deprecation date, don't propose it — recommend "
        "it for the operator's skip list (`autodiscovery_decisions`) via "
        "ask_operator or in your task result instead.\n\n"
        "```json\n" + json.dumps(models, indent=2, default=str) + "\n```"
    )


def _maintenance_brief(findings: dict[str, dict]) -> str:
    """One task's worth of ERRORS + STALE + UNHEALTHY across every
    credential — no `missing` content, ever."""
    import json

    return (
        _DISCOVERY_INTRO
        + _DISCOVERY_NAMING
        + "STALE (your own managed deployments the catalog no longer lists): "
        "propose litellm.delete_model for each.\n\n"
        "UNHEALTHY (your own managed deployments failing health): diagnose "
        "from the health error and your read tools, then propose "
        "litellm.update_model config fixes. NEVER propose anything against a "
        "deployment whose litellm_credential_name isn't the discovering "
        "credential — those are hand-tuned or another credential's, and "
        "read-only to you.\n\n"
        "Verification of any fix is the NEXT discovery pass observing health "
        "clean; you don't need to re-check it now.\n\n"
        "UNDECLARED (managed deployments whose measured capabilities are not "
        "declared): propose ONE litellm.update_model per alias carrying "
        "exactly its `suggested_model_info` as `model_info` — measured, "
        "never guessed. This converges: once declared, an alias never "
        "re-qualifies. A probe `error` entry here is a mechanism failure "
        "like any other — surface it via ask_operator, same as the ERRORS "
        "paragraph below.\n\n"
        "ERRORS: a findings entry carrying `error` instead of a diff means the "
        "DISCOVERY MECHANISM itself failed for that credential — the pass was "
        "blind there, and blind is never 'nothing to do'. Diagnose what you "
        "can with your read tools, then SURFACE IT with ask_operator before "
        "finishing, so the task parks for the operator instead of reading as "
        "quietly completed. A capability gap (no fetcher for a provider, a "
        "credential the mechanism can't use) is first-class: name the gap and "
        "the remedy you'd request.\n\n"
        "```json\n" + json.dumps(findings, indent=2, default=str) + "\n```"
    )


def _parse_positive_int(params: dict, name: str, default: int) -> int:
    raw = params.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        value = int(str(raw))
    except (TypeError, ValueError):
        raise ValueError(f"litellm.discovery param {name!r} must be a positive integer (got {raw!r})")
    if value <= 0:
        raise ValueError(f"litellm.discovery param {name!r} must be a positive integer (got {raw!r})")
    return value


async def _litellm_discovery(schedule_id: str, params: dict) -> dict:
    """Diff every STORED CREDENTIAL's live model catalog against the proxy's
    configured deployments, and — only on real drift — hand the litellm-
    manager one or more small tasks with the structured findings. The diff
    itself is plain code (`compute_discovery_drift`); no agent runs, and no
    provider or proxy call is even made, when no credential is stored.
    Credential-driven per the 2026-08-20 redesign: the operator's only
    enrollment step is adding a credential in the LiteLLM UI — no
    Central Command-side provider setting.

    A large drift is split (2026-08-20b) so one pass never hands the agent a
    single sprawling task: an optional MAINTENANCE task (errors + stale +
    unhealthy, all credentials) plus one ADD task per `batch_size`-sized
    chunk of one credential's `missing` list. Every chunk becomes a task in
    the SAME pass now (2026-08-21) — there is no per-pass cap any more,
    because the per-agent task queue (`api/routes._agent_task_lock`) drains
    them one at a time regardless of how many land on the board at once.

    GENERATION GUARD: before tasking a credential's missing list, this checks
    for still-open ("LiteLLM autodiscovery: <credential> batch …") tasks from
    an earlier pass. If any are still open, nothing new is created for that
    credential this pass — a daily tick mid-drain must not re-task models
    that are already queued or in flight — and the credential's outstanding
    count is reported under `backlog` instead."""
    from central_command.db import repo
    from central_command.integrations import litellm as litellm_client
    from central_command.integrations import litellm_credstore
    from central_command.runtime import attention, litellm_manager

    try:
        credentials = await litellm_credstore.list_provider_credentials()
    except litellm_credstore.CredStoreError as e:
        # UNCONFIGURED (env vars unset) is a valid quiet state — a dev box
        # without the credstore wired should not error every tick. Anything
        # else (wrong salt key, unreachable DB) must RAISE: this action is
        # quiet-eligible, so a swallowed failure would read as "nothing to
        # do" silently forever, and a heartbeat error is always material.
        if "not set" in str(e):
            return {"skipped": str(e)}
        raise
    if not credentials:
        return {"skipped": "no credentials stored"}

    decisions = await repo.get_app_setting("autodiscovery_decisions", {"skip": []})
    skip = decisions.get("skip") or []

    deployments = (await litellm_client.list_models())["models"]
    cred_names = {c["credential_name"] for c in credentials}
    # Health is probed ONLY for managed deployments, one scoped call per
    # alias (each is a REAL provider call). Whole-fleet /health is the wrong
    # tool here: it walks the hand-tuned local fleet too — swapping GPU
    # models on the workstation and blowing the 30s client timeout (measured
    # 2026-08-20) — and no managed deployments means no health call at all.
    unhealthy: list[dict] = []
    for d in deployments:
        if d.get("credential_name") not in cred_names:
            continue
        alias = d.get("model_name")
        if not alias:
            continue
        try:
            res = await litellm_client.check_model_health(alias)
            unhealthy.extend(res.get("unhealthy") or [])
        except Exception as e:  # noqa: BLE001 — a dead model must read as unhealthy, not kill the tick
            unhealthy.append({"model": alias, "error": f"{type(e).__name__}: {e}"})

    # UNDECLARED-CAPABILITY reconciliation: "declare the undeclared", not
    # continuous re-measurement. A probe costs ~10 real requests, so this
    # caps itself at `probe_batch` per tick — a fleet of newly-added models
    # converges over a few nightly passes rather than one expensive burst,
    # and once an alias is declared its capabilities are no longer None here
    # so it never re-qualifies. Re-measuring an already-declared fleet is an
    # operator-triggered task (`litellm_probe_model`), never a heartbeat one.
    probe_batch = _parse_positive_int(params, "probe_batch", 3)
    undeclared: dict[str, dict] = {}
    probed = 0
    for d in deployments:
        if probed >= probe_batch:
            break
        if d.get("credential_name") not in cred_names:
            continue
        caps = d.get("capabilities") or {}
        if not any(
            caps.get(f) is None
            for f in ("mode", "supports_function_calling",
                      "supports_response_schema", "supports_vision")
        ):
            continue
        if "/chat_completions/" in str(d.get("provider_model") or ""):
            continue  # Responses-bridge alias — the chat battery can't probe it
        alias = d.get("model_name")
        if not alias:
            continue
        probed += 1
        try:
            probe = await litellm_client.probe_model(alias)
        except Exception as e:  # noqa: BLE001 — one bad probe must not kill the tick
            undeclared[alias] = {"error": f"{type(e).__name__}: {e}"}
            continue
        suggested = probe.get("suggested_model_info") or {}
        if suggested:
            undeclared[alias] = {
                "model_id": d.get("model_id"),
                "suggested_model_info": suggested,
                "notes": probe.get("notes"),
            }

    findings: dict[str, dict] = {}
    for cred in credentials:
        name = cred["credential_name"]
        try:
            values = cred["values"]
            catalog = await litellm_client.provider_catalog(
                cred.get("provider"), values.get("api_key"), values.get("api_base")
            )
        except Exception as e:  # noqa: BLE001 — one bad credential must not block the rest
            findings[name] = {"error": f"{type(e).__name__}: {e}"}
            continue
        drift = compute_discovery_drift(name, cred.get("provider"), catalog, deployments, unhealthy, skip)
        if drift["missing"] or drift["stale"] or drift["unhealthy"]:
            findings[name] = drift

    if not findings and not undeclared:
        return {"drift": False}

    batch_size = _parse_positive_int(params, "batch_size", 5)

    # Same posture as ea.contact: check the attention budget before spending
    # any tokens. litellm-manager carries no budget today (unlimited, like
    # every non-EA agent) — this is the seam ready for the day it does.
    allowed, used, budget = await attention.may_open_lane(litellm_manager.AGENT_ID)
    if not allowed:
        return {"drift": True, "deferred": "attention budget spent",
                "contacts_today": used, "budget": budget}

    from central_command.api.routes import create_and_run_task

    task_ids: list[str] = []

    # Maintenance findings (errors, plus stale/unhealthy without missing) —
    # at most one task, created first so it never gets crowded out visually
    # by the add-chunk tasks queued behind it.
    maintenance = {
        name: {k: v for k, v in d.items() if k != "missing"}
        for name, d in findings.items()
        if d.get("error") or d.get("stale") or d.get("unhealthy")
    }
    if undeclared:
        maintenance["undeclared"] = undeclared
    if maintenance:
        result = await create_and_run_task(
            _maintenance_brief(maintenance), "LiteLLM autodiscovery: maintenance",
            litellm_manager.AGENT_ID, actor=f"heartbeat:{schedule_id}", background=True,
        )
        if result.get("task_id"):
            task_ids.append(result["task_id"])

    backlog: dict[str, int] = {}
    for name, d in findings.items():
        missing = d.get("missing") or []
        if not missing:
            continue
        # Generation guard: a still-open batch task from an earlier pass
        # means this credential is already queued or in flight — task
        # nothing new for it this pass, report what's outstanding instead.
        open_count = await repo.count_nonterminal_tasks_with_title_prefix(
            f"LiteLLM autodiscovery: {name} batch"
        )
        if open_count:
            backlog[name] = len(missing)
            continue
        chunks = [missing[i:i + batch_size] for i in range(0, len(missing), batch_size)]
        n = len(chunks)
        for i, chunk in enumerate(chunks, start=1):
            result = await create_and_run_task(
                _add_brief(name, chunk), f"LiteLLM autodiscovery: {name} batch {i}/{n}",
                litellm_manager.AGENT_ID, actor=f"heartbeat:{schedule_id}", background=True,
            )
            if result.get("task_id"):
                task_ids.append(result["task_id"])

    out = {
        "drift": True,
        "task_ids": task_ids,
        "credentials": sorted(findings),
        "queued": len(task_ids),
    }
    if backlog:
        out["backlog"] = backlog
    return out


ACTIONS: dict[str, ActionSpec] = {
    spec.kind: spec
    for spec in (
        ActionSpec(
            kind="feed.poll",
            description="Poll the mail façade once and enroll new mail (no approval — queue only).",
            params={},
            required=(),
            levers=("ingest.feed.poll_once",),
            run=_feed_poll,
            # A sweep that enrolled nothing changed nothing. New mail records
            # itself (work.enrolled + feed.polled), so quiet costs no history.
            material=lambda r: bool(r.get("new") or r.get("enrolled")),
        ),
        ActionSpec(
            kind="dispatch.window",
            description="Open the dispatcher drain for a bounded window, then close it.",
            params={"minutes": "window length in minutes (default 10)"},
            required=(),
            levers=("ingest.dispatcher.start", "ingest.dispatcher.stop"),
            run=_dispatch_window,
            # A window that did NOT open (continuous drain already running, or
            # the lease refused) touched nothing. An opened window is material
            # and logged — as is the heartbeat.window_closed that ends it.
            material=lambda r: bool(r.get("opened")),
        ),
        ActionSpec(
            kind="task.create",
            description="Create an assigned first-class Task; the agent runs it now.",
            params={
                "agent_id": "a taskable ACTIVE roster agent",
                "instructions": "the task mission, verbatim",
                "title": "card title (optional; defaults from instructions)",
                "model": "LiteLLM model id to run this schedule on (optional; "
                         "defaults to the agent's own setting)",
                "thinking": "thinking level for this schedule (optional)",
            },
            required=("agent_id", "instructions"),
            choices={"thinking": THINKING_LEVELS},
            levers=("api.routes.create_and_run_task",),
            run=_task_create,
        ),
        ActionSpec(
            kind="report.audit_agreement",
            description="Snapshot the auditor agreement report onto the event log (read-only).",
            params={},
            required=(),
            levers=("gateway.auditor.agreement_report",),
            run=_report_audit_agreement,
        ),
        ActionSpec(
            kind="discussion.sweep",
            description="Flag clarification discussions gone quiet; nudge the agent when it owes a conclusion.",
            params={},
            required=(),
            levers=("runtime.converse.conversation_turn",),
            run=_discussion_sweep,
            # Quiet ONLY when it found nothing: a sweep with no stalled lane
            # changed nothing and each flag emits its own discussion.stalled.
            # A nudge starts an agent turn, so it is always material.
            material=lambda r: bool(r.get("stalled") or r.get("nudged")),
        ),
        ActionSpec(
            kind="retry.sweep",
            description=(
                "Re-drive units parked by a dependency outage (sessions, "
                "approved executions, tasks), health-gated per dependency."
            ),
            params={},
            required=(),
            levers=("api.orchestration.retry_sweep",),
            run=_retry_sweep,
            # QUIET-ELIGIBLE. Non-authorising: every unit it touches was
            # authorised BEFORE it parked — an approved proposal's decision, an
            # operator's task, a session already mid-resume — and the sweep
            # opens no gate and creates no work. Self-recording: each driver
            # emits its own session.resume_retried /
            # proposal.execution_retried / task.run_retried, so a material
            # sweep is never invisible. The overwhelmingly common tick finds
            # nothing parked and must write nothing (2026-07-25).
            #
            # A tick that only SKIPPED gated units is immaterial too: nothing
            # happened, the attempts are untouched, and the outage itself is
            # already on the log from the failures that parked the units. The
            # skip list rides the result for the ticks that ARE material.
            material=lambda r: bool(
                r.get("resumed") or r.get("retried_executions")
                or r.get("retried_tasks") or r.get("errors")
            ),
        ),
        ActionSpec(
            kind="graph.verify_sweep",
            description=(
                "Audit approved graph episodes once ingestion has settled: "
                "mechanical read-back checks (landed, right group, non-empty, "
                "embedded) plus the graph-auditor's alignment judgment; "
                "findings park for the operator."
            ),
            params={
                "settle_minutes": (
                    "minutes after execute before a row is first checked "
                    "(default 10)"
                ),
                "missing_after_minutes": (
                    "age past which a still-absent episode parks as MISSING — "
                    "ingestion is a serial queue (~2-3 min/episode), so a batch "
                    "of approvals lands an hour late; inside this deadline the "
                    "row just waits for the next tick (default 360)"
                ),
            },
            required=(),
            levers=("api.orchestration.graph_verify_sweep",),
            run=_graph_verify_sweep,
            # QUIET-ELIGIBLE. Non-authorising: it opens no gate — it reads the
            # graph, records verdicts, and parks rows for the operator; the
            # only close it can perform (active mode, aligned, addition-only)
            # is a verification bookkeeping flip, never a world change.
            # Self-recording: every parked row emits graph.verification.parked,
            # every verdict graph.audit.verdict, every auto-close
            # graph.verification.confirmed. The common tick finds no due rows
            # and must write nothing.
            material=lambda r: bool(
                r.get("auto_verified") or r.get("awaiting")
                or r.get("missing") or r.get("errors")
            ),
        ),
        ActionSpec(
            kind="sandbox.run_script",
            description=(
                "Run an already-synced script in the owning agent's sandbox "
                "session; hand the run to that agent as an evaluation task."
            ),
            params={
                "agent_id": "the owning agent — its own grants and sandbox session",
                "session_id": "the sandbox session the script was already synced into",
                "command": "shell command that runs the already-synced script",
                "success_creates_task": (
                    "'true' to create the evaluation task on success too "
                    "(default false; failure always creates one)"
                ),
                "title": "evaluation task title (optional)",
            },
            required=("agent_id", "session_id", "command"),
            levers=(
                "integrations.sandbox_client.exec_cmd",
                "api.routes.create_and_run_task",
            ),
            run=_sandbox_run_script,
            # QUIET-ELIGIBLE. Non-authorising: the script runs ungated, under
            # grants the agent already holds, inside its own sandbox — same
            # posture as the sandbox pack's tools, no new gate opened. Only
            # material when it actually created the evaluation task; that
            # creation is self-recording (create_and_run_task emits
            # task.created itself). The common tick — a clean run nobody asked
            # to be told about — costs no history.
            material=lambda r: bool(r.get("task_id")),
        ),
        ActionSpec(
            kind="ea.contact",
            description=(
                "Wake the executive assistant to contact the operator with a "
                "digest, a check-in, the morning report, the week-ahead look, "
                "or the onboarding tour of the team."
            ),
            params={"kind": "digest | check_in | morning_report | week_ahead "
                            "| onboarding_tour"},
            required=("kind",),
            levers=("api.routes.create_and_run_task",),
            run=_ea_contact,
            choices={"kind": EA_CONTACT_KINDS},
            # LOUD (material=None) on purpose: this AUTHORISES an agent run —
            # tokens are spent and a conversation may be opened with the
            # operator. Both quiet-eligibility conditions fail, and a contact
            # DEFERRED for budget is exactly the firing the operator most needs
            # to see on the log when they wonder why the digest never came.
        ),
        ActionSpec(
            kind="litellm.discovery",
            description=(
                "Diff each STORED CREDENTIAL's live model catalog against the "
                "LiteLLM proxy's configured deployments; hand the litellm-"
                "manager small batched tasks only when something actually "
                "drifted."
            ),
            params={
                "batch_size": "models per add-task, default 5",
                "probe_batch": (
                    "managed deployments with undeclared capabilities to "
                    "probe per tick, default 3"
                ),
            },
            required=(),
            levers=(
                "integrations.litellm_credstore.list_provider_credentials",
                "integrations.litellm.provider_catalog",
                "integrations.litellm.list_models",
                "integrations.litellm.check_model_health",
                "integrations.litellm.probe_model",
                "api.routes.create_and_run_task",
            ),
            run=_litellm_discovery,
            # QUIET-ELIGIBLE, same posture as sandbox.run_script. NON-
            # AUTHORISING at the heartbeat layer: the diff itself is plain
            # code that opens no gate; the task it may create runs the
            # litellm-manager under its OWN existing capability grants (no
            # new gate opened by this action) and every proposal it drafts
            # still goes through the ordinary approval gate. SELF-RECORDING:
            # creating the task emits task.created itself. The common tick —
            # no providers enrolled, or nothing drifted — must cost no
            # history.
            # Truthy on task_ids OR backlog: a pass that created nothing new
            # because everything is already queued from a prior pass is not
            # "nothing happened" — it's drift the operator should still be
            # able to see accounted for, without re-tasking it.
            material=lambda r: bool(r.get("task_ids") or r.get("backlog")),
        ),
    )
}


def validate_action(action_kind: str, params: dict) -> None:
    """Raise ValueError when the action kind is unknown or params are missing/
    unknown — create/patch-time validation so a schedule can't be born broken."""
    spec = ACTIONS.get(action_kind)
    if spec is None:
        raise ValueError(
            f"unknown action kind {action_kind!r} (known: {sorted(ACTIONS)})"
        )
    for name in spec.required:
        if not str(params.get(name) or "").strip():
            raise ValueError(f"{action_kind} requires param {name!r}")
    for name in params:
        if name not in spec.params:
            raise ValueError(f"{action_kind} has no param {name!r}")
    for name, allowed in spec.choices.items():
        value = params.get(name)
        if value is None:
            continue
        if str(value) not in allowed:
            raise ValueError(
                f"{action_kind} param {name!r} must be one of {list(allowed)} "
                f"(got {value!r})"
            )
