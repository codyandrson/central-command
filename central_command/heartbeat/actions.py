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
import bisect
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
    # The agent an action hands its work to when that agent is FIXED by the
    # action rather than chosen by a param (task.create / sandbox.run_script
    # read `agent_id` from params). Cockpit display only — a string literal,
    # pinned to the runtime constant by test_heartbeat.py so it cannot drift.
    runs_as: str | None = None


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


async def _source_walk(schedule_id: str, params: dict) -> dict:
    """Walk every ENABLED source, then enroll its catalog backlog at that
    source's own pace (sources-catalog slices 3+4).

    Deterministic throughout: the walk is `stat()` plus text extraction and the
    enrollment is hash/diff comparison — no LLM either side. A source whose
    kind has no watcher (or whose root has gone) records its error against that
    source and the sweep continues: one dead SMB mount must not stop the
    others. The email feed is not a `source` row, so it never appears here.
    """
    from central_command.db import repo
    from central_command.ingest.catalog_enroll import enroll_pending
    from central_command.ingest.watcher import walk_source

    out: dict[str, dict] = {}
    for source in await repo.list_sources():
        if not source["enabled"]:
            continue
        try:
            summary = await walk_source(source)
            summary["enrollment"] = await enroll_pending(source)
        except Exception as e:  # noqa: BLE001 — one bad source must not kill the tick
            summary = {"error": f"{type(e).__name__}: {e}"}
        out[source["id"]] = summary
    return {"sources": out}


async def _wiki_freshness(schedule_id: str, params: dict) -> dict:
    """Sweep the claims ledger and enroll page repairs (sources-catalog slice
    7, Decision 9).

    Deterministic: a recorded evidence version compared against the catalog —
    no LLM decides what is stale. Repair is SCHEDULED work on the ordinary
    ledger, not event-triggered churn, which is why it hangs off a cadence the
    operator sets rather than off the catalog's own writes.
    """
    from central_command.ingest.wiki_freshness import DEFAULT_PAGE_CAP, sweep

    return await sweep(int(params.get("pages") or DEFAULT_PAGE_CAP))


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
    out = await converse.conversation_turn(row["discussion_session_id"], message)
    # The decide hook (FR-4) for a proposal the nudged agent parked, detached:
    # a heartbeat action never awaits agent work (the passthrough lives in
    # api/orchestration because this package may never import the gate).
    from central_command.api import orchestration

    orchestration.decide_landed_detached(out)


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
    """Audit approved graph episodes once their ingest job is done (2026-08-19
    spec; the durable queue since 2026-10-04).

    The body lives in `gateway.graph_auditor` (judging a verification is part
    of the gate), reached through `api.orchestration` because this package may
    never import the gate — same route as `retry.sweep`. A stored
    `missing_after_minutes` from before the queue is ignored: absence is no
    longer inferred from a row's age."""
    from central_command.api import orchestration

    return await orchestration.graph_verify_sweep(
        settle_minutes=int(params.get("settle_minutes", 10)),
    )


async def _retry_sweep(schedule_id: str, params: dict) -> dict:
    """Drive every unit parked by a dependency outage whose dependency is now
    up — the convergence half of outage equivalence (slice 5).

    Three worklists, oldest-first, each through the driver that already owns it:
    sessions parked AWAITING_RESUME (slice 2), approved proposals parked
    RETRY_PENDING (slice 3), and due retry-parked tasks (slice 4). Each is gated
    on a cheap probe of the dependency its retry needs, and **a gated skip
    consumes no attempt** — a long outage costs latency, never work: there is
    no attempt limit, and a unit failing despite healthy probes is reported.

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
# END-OF-DAY WRAP-UP since the 2026-08-21 EA charter (v5) daily arc, and as the
# EVENING REVIEW of the stored day plan since 2026-11-19 (DL-221 step 4a);
# "week_ahead" (same change) is the Sunday look-ahead, framed as the WEEKLY
# PLAN since DL-221 step 5, which also added "quarter_ahead", the QUARTERLY
# REVIEW; both carry blocks computed for them (`week`, `deadlines`, `audit`).

# "onboarding_tour" (2026-08-23) is the odd one out on purpose: it is the ONE
# kind that is not a windowed report, so it takes none of the snapshot/calendar
# machinery below and skips the attention budget (an operator who just ran
# setup asked for this contact — deferring it would be the control plane
# protecting them from a thing they requested). It is a CONTACT kind and not a
# setup script so the tour is giftable, re-runnable, and independent of Claude
# Code. Nothing schedules it: schema.sql seeds a DISABLED `team-tour` row
# (v2.51.0 — no installer ever created it before) and the operator fires it
# once through the run-now button.
EA_CONTACT_KINDS: tuple[str, ...] = (
    "digest", "check_in", "morning_report", "week_ahead", "quarter_ahead",
    "onboarding_tour",
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
        "This is the EVENING REVIEW: compare the day with its plan. "
        "`plan_review` is today's STORED plan, the record of what was planned, "
        "read against what the record shows now. For each planned item, set "
        "what was planned (its reason and its planned estimate) against what the "
        "tracker says (`tracker.done`; `null` means the tracker could not say, "
        "and it is NEVER done for you) and against its work blocks (`state: "
        "retired`, or the times the calendar holds now; `moved_off_day` names a "
        "block that now sits on another day). For what slipped, name it and ASK "
        "the operator why; never guess a reason. Ask how long each done item "
        "actually took, and whether any item the tracker still shows open was in "
        "fact finished. `unplanned_done` lists issues the tracker shows "
        "completed today that were not on the plan (`asked` is the search the "
        "control plane ran; `unplanned_done_omitted` says why it could not ask): "
        "ask about those too, because unplanned work is real. Then ask what "
        "should be added to the backlog or dropped from it, and name the "
        "`unscored` issues tomorrow's plan would need scored and what each is "
        "missing. For each of those, propose its value and lateness loss from "
        "the stakes the conversation gives you, on the backlog's `unit`, and "
        "where an issue has no stakes, ASK the operator for them; never invent "
        "stakes or numbers. A due date the operator settles may be set the same "
        "way. Draft TOMORROW in words against the calendar block, which is "
        "TOMORROW's, and its `free_time`: which issues you would plan and "
        "roughly where (never an issue in the `past_hard` tier: name each one "
        "for the morning, which asks the operator to close it or set a new "
        "due date), and anything the operator should prepare tonight (an "
        "early start, a conflict, a meeting with no agenda). Propose no calendar "
        "change tonight: the morning plan blocks the time. When the conversation "
        "has settled the outcomes, record them as ONE `plan.record_review` "
        "proposal (`done` for each issue, and `actual_hours` only where the "
        "operator said), following the conclude-before-you-propose rule: "
        "conclude first, then propose from the resumed run. Record the planning "
        "fields the operator settled as ONE `jira.set_planning_fields` proposal, "
        "one action per issue, separate from the `plan.record_review` proposal "
        "and, like it, after you conclude, in a turn of its own once the "
        "review's proposal has its answer. It changes no other field of an "
        "issue: the reason an item slipped, when the operator gives one worth "
        "keeping, is a comment on that issue (`jira.add_comment` when you hold "
        "it), and any other change to the tracker goes to the agent that owns "
        "the tracker, as a task. For each item the review shows DONE, consider "
        "whether finishing it left a lasting fact about the operator's world "
        "(something delivered, installed, bought, sold, replaced or cancelled, "
        "and when): if it did, propose that outcome as one graph.add_episode "
        "after the review is recorded; most finished work leaves none, and "
        "nothing about the plan, the slip or an issue's state is ever an "
        "episode. Never mark work done "
        "on your own word. If `plan_review` says no plan is stored, say so, and "
        "review the day from the tracker and the calendar alone; if it is "
        "unavailable, say the review could not be read and why. Questions other "
        "agents have put to the operator are theirs: say how many are open in "
        "the Decisions Inbox and whose they are, and never restate or re-ask one."
    ),
    "check_in": (
        "This is the MID-DAY CHECK-IN: a REPLAN only when the day has slipped. "
        "Compare today's work blocks (the calendar events with `plan_kind: "
        "work_block` and today's `plan_date`, and `work_blocks_not_listed`) with "
        "the time now. A block whose time has passed (it is in "
        "`work_blocks_not_listed`, or it ended before now) or which now overlaps "
        "a commitment (see `conflicts`) is a SLIP. `plan` is today's stored "
        "plan, the record of what was planned (or says that none is stored). "
        "Whether the work in a passed block was actually done is recorded only "
        "in the evening review: ASK "
        "the operator, and never assume it was done or that it was missed. Only "
        "when there is a slip, propose the replan as ONE proposal through "
        "`propose_calendar_change`: `calendar.update_event` on your OWN work "
        "blocks (and on a placeholder only when the operator's standing notes "
        "allow it), each moved into a `free_time` stretch and never shorter than "
        "the minimum block. Never propose a change to a commitment unless the "
        "operator asked for it. If nothing slipped, say so in one line and "
        "propose nothing. Something else that genuinely needs the operator now "
        "(a decision the queue is stuck behind, an error, a teammate blocked) "
        "still gets its line. Use the calendar block for TIMING: say what is "
        "next and how long they have, and do not ask for attention inside a "
        "meeting."
    ),
    "morning_report": (
        "This is the MORNING PLAN — the first contact of the day, and you "
        "arrive prepared. FIRST, before the ranked list, name EVERY issue in "
        "the `backlog` block's `past_hard` tier (a hard deadline whose due date "
        "has passed; `counts.past_hard` says how many there are, so say how "
        "many the block does not show). For each, ask the operator the one "
        "question: can it still be done? If yes it needs a new due date; if no "
        "it should be closed. Never put a `past_hard` issue in the day's "
        "ranked list, never block calendar time for it and never propose a "
        "`plan.record_day` item for it: it is decided first. When the operator "
        "answers, propose the new due date with `jira.set_planning_fields` "
        "(`due_date`), one action per issue; closing an issue is not yours, so "
        "say it needs closing and leave that to the operator or the teammate "
        "who owns the tracker. Then propose a ranked short list for today, "
        "three to five "
        "items, taken from the `backlog` block's `must_start` tier first and "
        "then from `standard`, each with the reason it is on the list and its "
        "stakes, value, lateness loss, due date and estimate beside it. Frame "
        "the list against the calendar block's day: what is already on it, "
        "where the free stretches are, and any conflict the operator has not "
        "resolved; a block with `available: false` has no free stretches to "
        "report, so say the day is unknown and why. Say what cannot be ranked "
        "and why (the `unscored` issues and what each is missing). Give the "
        "count of questions open in the Decisions Inbox and whose they are, "
        "and never restate or re-ask one. `plan` says whether a plan is "
        "already stored for today: if one is, say what it holds and propose "
        "another only when the operator asks to replan. Then STORE THE PLAN AND "
        "BLOCK THE TIME as ONE proposal through `propose_calendar_change`: its "
        "FIRST action is `plan.record_day` with today's date and the ranked "
        "list (each issue key with its reason, and no numbers: the control "
        "plane re-reads them from the tracker), followed by "
        "one `calendar.create_event` action per block. Each block lies inside a "
        "`free_time` stretch, is no longer than its issue's estimate and never "
        "shorter than the minimum block, carries `plan: {\"kind\": "
        "\"work_block\", \"issue_key\": \"<KEY>\", \"date\": \"<today, "
        "YYYY-MM-DD>\"}`, and has a title that names the issue key and its "
        "summary. Block only ranked issues (`must_start`, `standard`): an "
        "`unscored` issue gets no block; say it is unestimated and ask for what "
        "it is missing. Leave the rest of the free time free. The proposal "
        "carries the plan to the operator, who corrects it by approving, "
        "rejecting with feedback, or dismissing it: put the ranked list and the "
        "blocks you proposed in its intent, say that nothing is stored or on "
        "the calendar until the operator approves, and do not call ask_operator in the same "
        "turn; never imply that any time has been blocked, moved or reserved "
        "before you are told the change executed. If the day has no "
        "`free_time` or the calendar is unknown, propose no blocks: the "
        "proposal is then the `plan.record_day` action alone. Say why, "
        "and ask the operator to CORRECT the plan, not to supply one. If the "
        "`backlog` block is absent or unavailable, say you could not see the "
        "backlog and why, propose no blocks and no plan, and ask what matters "
        "most today."
    ),
    "week_ahead": (
        "This is the WEEKLY PLAN — plan the coming week before it starts. "
        "FIRST name EVERY issue in the `backlog` block's `past_hard` tier (a "
        "hard deadline whose due date has passed; `counts.past_hard` says how "
        "many there are, so say how many the block does not show). For each, "
        "ask the operator the one question: can it still be done? If yes it "
        "needs a new due date; if no it should be closed. Never make a "
        "`past_hard` issue one of the week's priorities and never block "
        "calendar time for it. When the operator answers, propose the new due "
        "date with `jira.set_planning_fields` (`due_date`), one action per "
        "issue; closing an issue is not yours, so say it needs closing and "
        "leave that to the operator or the teammate who owns the tracker. "
        "Then propose the week's top three to five priorities from the `backlog` "
        "block's ranking (`must_start` first, then `standard`), each with the "
        "reason it is a priority and its stakes, value, lateness loss, due date "
        "and estimate beside it. `deadlines` lists every issue due in the "
        "window against the work-block time already on the calendar for it: "
        "name EVERY one whose `coverage` is `none` or `partly`, and say which "
        "are `unestimated` and what they are missing. Propose blocks for the "
        "uncovered ones on the days of `week` with free time, as ONE proposal "
        "through `propose_calendar_change`: one `calendar.create_event` action "
        "per block, each carrying `plan: {\"kind\": \"work_block\", "
        "\"issue_key\": \"<KEY>\", \"date\": \"<the day the block sits on, "
        "YYYY-MM-DD>\"}` and a title that names the issue key and its summary, "
        "each inside one of that day's `free_time` stretches in `week`, on or "
        "before the issue's due date, no longer than the time the issue still "
        "needs and never shorter than the minimum block. `week` is computed for "
        "you: do not read the week's days yourself. Propose no "
        "`plan.record_day`: a day's plan is stored by that day's own morning. "
        "For each line of effort in `lines_of_effort`, say whether anything in "
        "the week serves it (a priority, a deadline, a block), and say so "
        "plainly when nothing does. Read `audit` back in a few lines: does the "
        "time the record shows match the priorities, what kept slipping "
        "(`slipped`), and what the hours against the estimates say; then ASK "
        "what should be dropped or delegated. If `audit` has `no_record`, say "
        "there is no record of planned time for last week, and never present "
        "that as a clean week. Flag every overbooked day and every day with an "
        "unresolved conflict. A day whose `available` is false is UNKNOWN, "
        "with its reason: availability UNKNOWN is not an open day, so say you "
        "could not see it, never count it as free and propose no block on it. "
        "Where the week has no shape (no placeholders), offer placeholders, or "
        "to adopt events the operator already keeps. The proposal carries the "
        "blocks to the operator: put the priorities and the blocks in its "
        "intent, say nothing is on the calendar until the operator approves, "
        "and do not call ask_operator in the same turn. Never propose a change "
        "to a commitment unless the operator asked for it. Read `delegated`, "
        "when it is present, back as what the operator is waiting on from "
        "other people: what is overdue, and what has gone quiet. When the plan "
        "involves work for other people (a `delegated` block is present, or the "
        "operator raises who should take something), ask the team's staffing "
        "agent (`staffing_agent`) through `consult_agent` who should take it "
        "and whether they have room, and carry its recommendation, with its "
        "evidence, into the conversation. Never recommend an assignee from your "
        "own judgment. When the operator has decided who takes what, consult the "
        "staffing agent again with that decision, and it proposes the "
        "assignments; you propose none. If the brief carries no "
        "`staffing_agent`, say there is no staffing agent on the roster and "
        "leave the assignment to the operator. Questions other "
        "agents have put to the operator are theirs: say how many are open in "
        "the Decisions Inbox and whose they are, and never restate or re-ask "
        "one."
    ),
    "quarter_ahead": (
        "This is the QUARTERLY REVIEW — the conversation that resets the "
        "operator's priorities. Read `audit`, the record of the last quarter, "
        "back plainly, including where it is unflattering: where the planned "
        "time went against what was planned (`items`, `by_tier`, `hours`), what "
        "was finished, and what kept slipping (`slipped`); for each issue that "
        "slipped often, ask whether it should be dropped, delegated or "
        "re-scoped. Say what the ratio of actual to estimated hours says about "
        "the estimates. If `audit` has `no_record`, say there is no record of "
        "planned time for the quarter, and never present that as a clean one. "
        "Then ASK the operator for the priorities of the coming quarter, "
        "showing what is already dated in it (`deadlines`, with the time "
        "already blocked for each) and the top of the `backlog` ranking. Name "
        "every issue in its `past_hard` tier first and ask, for each, whether "
        "it can still be done (it then needs a new due date) or should be "
        "closed. Give "
        "each line of effort in `lines_of_effort` its standing (its last "
        "check-in and whether the record shows time serving it) and ask "
        "whether it continues. Ask whether any of the operator's standing "
        "rules (the standing notes) should change. Record what the operator "
        "decides the way your charter says, each thing in its one home and "
        "all of it after you conclude: the re-scored and re-dated issues as "
        "ONE `jira.set_planning_fields` proposal; the reason behind a "
        "decision about an issue (deferred and until when, dropped, "
        "re-scoped) as a comment on that issue, ONE `jira.add_comment` "
        "proposal with one action per issue when you hold that capability, "
        "otherwise a task for the teammate who owns the tracker; an issue to "
        "close as a task for that teammate. A decision about work is never a "
        "graph.add_episode. Propose an episode only when the conversation "
        "taught a lasting fact about the operator's world, and none when it "
        "did not. "
        "Propose no calendar change beyond what the operator asks for, and "
        "never a change to a commitment unless the operator asked. Questions "
        "other agents have put to the operator are theirs: say how many are "
        "open in the Decisions Inbox and whose they are, and never restate or "
        "re-ask one."
    ),
}

# Which day each contact kind looks at. The wrap-up fires at 17:00, when
# today's calendar is spent — the useful question then is what tomorrow
# demands. The weekly plan anchors on tomorrow (Monday) and carries the rest of
# the week as the computed `week` block; the quarterly review carries tomorrow
# only (no ninety-day calendar read).
_CALENDAR_DAY: dict[str, int] = {
    "morning_report": 0, "check_in": 0, "digest": 1, "week_ahead": 1,
    "quarter_ahead": 1,
}


async def _annotate_plan_kinds(calendar: dict) -> dict:
    """Mark each event of an available calendar block with what the plan
    ledger says it is (the EA planning design, D5): `work_block` (with its
    `issue_key` and `plan_date`) or `placeholder` when the assistant created or
    adopted it through an approved proposal, else `commitment`. A Postgres
    read joined here, in the control plane, so `ea_calendar.day()` stays free
    of database access. `work_blocks_not_listed` is the ledger's work blocks
    for the block's day that the listed window does not show: on today's
    block, a block that already ended (the window starts at now), or one moved
    off the day or deleted outside the control plane."""
    from central_command.db import repo

    if not calendar.get("available"):
        return calendar
    events = calendar.get("events") or []
    ids = [str(e["event_id"]) for e in events if e.get("event_id")]
    day = str((calendar.get("window") or {}).get("from") or "")[:10] or None
    rows = await repo.list_calendar_plan_events(event_ids=ids, plan_date=day)
    by_id = {r["event_id"]: r for r in rows}
    for e in events:
        row = by_id.get(str(e.get("event_id") or ""))
        if row is None:
            e["plan_kind"] = "commitment"
            continue
        e["plan_kind"] = row["kind"]
        if row["kind"] == "work_block":
            e["issue_key"] = row["issue_key"]
            e["plan_date"] = str(row["plan_date"])
    listed = set(ids)
    calendar["work_blocks_not_listed"] = [
        {"event_id": r["event_id"], "issue_key": r["issue_key"],
         "plan_date": str(r["plan_date"])}
        for r in rows
        if r["kind"] == "work_block" and str(r["plan_date"]) == day
        and r["event_id"] not in listed
    ]
    return calendar


async def _annotate_week(week: dict) -> dict:
    """Split each available day's timed events of a `week` block into
    `commitments` and the assistant's own `work_blocks` and `placeholders`
    from the plan ledger (the same join `_annotate_plan_kinds` makes for one
    day), and drop the event ids the report carried for it."""
    from central_command.db import repo

    if not week.get("available"):
        return week
    ids = [i for d in week.get("days") or [] for i in d.get("busy_event_ids") or []]
    rows = await repo.list_calendar_plan_events(event_ids=ids) if ids else []
    kind_of = {r["event_id"]: r["kind"] for r in rows}
    for d in week.get("days") or []:
        busy = d.pop("busy_event_ids", None)
        if busy is None:
            continue
        kinds = [kind_of.get(i) for i in busy]
        d["work_blocks"] = sum(1 for k in kinds if k == "work_block")
        d["placeholders"] = sum(1 for k in kinds if k == "placeholder")
        d["commitments"] = d.get("timed_events", 0) - d["work_blocks"] - d["placeholders"]
    return week


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
    from central_command.config import settings
    from central_command.db import repo
    from central_command.reports import (
        ea_backlog,
        ea_calendar,
        ea_followups,
        ea_plan,
        ea_report,
        ea_team,
    )
    from central_command.runtime import attention, ea, staffing_profile

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
        snapshot["calendar"] = await _annotate_plan_kinds(calendar)

    # Same rule as the calendar, same placement: AFTER the budget check, so a
    # deferred contact makes no tracker call either. An outage degrades this
    # key and never the contact; like `calendar`, it is omitted when the
    # tracker is not configured.
    try:
        tracked = await ea_followups.tracked()
    except Exception as e:  # noqa: BLE001 — degrade, never lose the contact
        tracked = {"available": False, "error": str(e)}
    if tracked.get("reason") != "not configured":
        snapshot["tracked_follow_ups"] = tracked

    # The operator's own backlog, ranked in code (the EA planning design, D2
    # and D4). Same rule and placement as the follow-ups: after the budget
    # check, degraded on failure, omitted when the tracker is not configured.
    try:
        backlog = await ea_backlog.backlog()
    except Exception as e:  # noqa: BLE001 — degrade, never lose the contact
        backlog = {"available": False, "error": str(e)}
    if backlog.get("reason") != "not configured":
        snapshot["backlog"] = backlog

    # What the operator is waiting on from other people (the EA planning
    # design, D2's `delegated`; DL-221 step 6). Every kind that carries the
    # backlog carries it, degraded on failure like the blocks above; omitted
    # when the tracker is not configured, the setting is empty, or nothing is
    # delegated, so a deployment with no team sees no block and no mention.
    try:
        delegated = await ea_team.delegated()
    except Exception as e:  # noqa: BLE001 — degrade, never lose the contact
        delegated = {"available": False, "error": str(e)}
    if delegated.get("error") or (delegated.get("available") and delegated.get("count")):
        snapshot["delegated"] = delegated

    # Who the team's staffing agent is (D10), by the template it was hired
    # from: the weekly plan's assignment questions go to it through a consult.
    # Present only when one is on the roster; the framing says what to do
    # when it is absent.
    if kind == "week_ahead":
        staffing = [a for a in await repo.list_agents(include_retired=False)
                    if a.get("template") == staffing_profile.TEMPLATE_ID]
        if staffing:
            snapshot["staffing_agent"] = {"id": staffing[0]["id"],
                                          "name": staffing[0]["name"]}

    # The stored day plan (the EA planning design, D6; DL-221 step 4a). The
    # evening contact carries the REVIEW of today's plan (an outside read of the
    # tracker and the calendar: degraded on an outage like the blocks above);
    # the morning and mid-day contacts carry today's plan in brief form, or its
    # absence, so the assistant knows whether one exists before proposing.
    plan_today = ea_backlog._today()
    if kind == "digest":
        try:
            snapshot["plan_review"] = await ea_plan.review(plan_today)
        except Exception as e:  # noqa: BLE001 — degrade, never lose the contact
            snapshot["plan_review"] = {"available": False, "error": str(e)}
    elif kind in ("morning_report", "check_in"):
        try:
            snapshot["plan"] = await ea_plan.brief(plan_today)
        except Exception as e:  # noqa: BLE001 — degrade, never lose the contact
            snapshot["plan"] = {"available": False, "error": str(e)}

    # The weekly plan and the quarterly review (DL-221 step 5): their blocks are
    # computed for them, each degraded on failure like the blocks above. The
    # weekly plan carries the coming week (`week`, read day by day), the
    # deadlines of the next seven days and the audit of the last seven; the
    # quarterly review carries the audit of the last `plan_quarter_days` and the
    # deadlines of the coming ones, and no week (its calendar block is
    # tomorrow's only). `week` and `deadlines` are omitted when their
    # integration is not configured; `audit` is Postgres and always present.
    if kind in ("week_ahead", "quarter_ahead"):
        reach = 7 if kind == "week_ahead" else max(1, int(settings.plan_quarter_days))
        if kind == "week_ahead":
            try:
                week = await _annotate_week(await ea_calendar.week(1, 7))
            except Exception as e:  # noqa: BLE001 — degrade, never lose the contact
                week = {"available": False, "error": str(e)}
            if week.get("reason") != "not configured":
                snapshot["week"] = week
        try:
            deadlines = await ea_plan.deadlines(reach)
        except Exception as e:  # noqa: BLE001 — degrade, never lose the contact
            deadlines = {"available": False, "error": str(e)}
        if deadlines.get("reason") != "not configured":
            snapshot["deadlines"] = deadlines
        try:
            snapshot["audit"] = await ea_plan.audit(reach, plan_today)
        except Exception as e:  # noqa: BLE001 — degrade, never lose the contact
            snapshot["audit"] = {"available": False, "error": str(e)}

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
           "created, moved or cancelled. `available: false` means the day is "
           "UNKNOWN (`reason` or `error` says why): say so, and never report it "
           "as free time.\n"
           "Every event carries `plan_kind`: `work_block` (time you blocked for "
           "one issue, with its `issue_key` and `plan_date`), `placeholder` (a "
           "shape of the operator's week you created or adopted) or `commitment` "
           "(everything else). The kinds come from the plan ledger, the control "
           "plane's record of which events you created or adopted through "
           "approved proposals; never infer a kind from an event's title. "
           "`work_blocks_not_listed` names the ledger's work blocks for this day "
           "that the window does not show (already ended, or moved off the day "
           "or deleted outside the control plane). `free_time` is computed for "
           "you: the free stretches inside the operator's working window "
           f"({settings.plan_day_start}-{settings.plan_day_end} local; today's "
           "starts now), after every timed event (your own work blocks and "
           "placeholders included) is subtracted, with stretches shorter than "
           f"the minimum block of {settings.plan_min_block_minutes} minutes "
           "dropped; `free_minutes` is their total. It is the ONLY place a work "
           "block may be proposed. A day that is not available has no "
           "`free_time`.\n" if "calendar" in snapshot else "")
        + "`open_tasks` is every task not yet finished, and "
        "`open_operator_items` every question still waiting on the operator: "
        "together they are what the team currently owes and is owed inside "
        "the control plane. An `agent.asked` entry under `activity` is a "
        "question a teammate put to the operator in the window; its `state` "
        "says whether it is still open, and `open_operator_items` is the only "
        "list of what is waiting on the operator.\n"
        + ("`tracked_follow_ups` is the issue tracker's open issues labelled "
           "as follow-ups, soonest due first — the dated commitments. It is "
           "GROUND TRUTH for what is currently open: narrate it, never "
           "re-derive it, and say which are due or overdue. You do not close "
           "these yourself; when something in the window resolves one, say so "
           "and name the issue.\n"
           if tracked.get("available") else "")
        + "`lines_of_effort` is the operator's active lines of effort, each with "
        "its cadence and the time and gist of its last check-in (null when it "
        "has none yet); `loe_list` reads their questions and history.\n"
        + ("`backlog` is the operator's OWN open work, read from the issue "
           "tracker and RANKED BY THE CONTROL PLANE, not by you. Every issue "
           "is in one tier, and the tiers come in this order. `past_hard`: its due "
           "date is before today and its deadline type is hard. It is NOT "
           "ranked and has no score, because a missed hard deadline is a "
           "decision, not work: either it can no longer be done and the issue "
           "should be closed, or it still can and needs a new due date. Each "
           "row says so in `needs_decision`; oldest due date first. "
           "`must_start`: its latest start (the due date less "
           "the estimate in working days at `hours_per_day`) is today or past, "
           "ordered by lateness loss per estimated hour; an issue past a soft "
           "deadline stays here. `standard`: ordered by value per estimated "
           "hour. "
           "`unscored`: no value or no estimate, so it cannot be ranked; never "
           "guess it into the order, and say what each is missing (`missing`). "
           "`score` is the number the tier is ordered by and `unit` labels "
           "value and lateness loss. The order is GROUND TRUTH: you may argue "
           "for a different one and must say why, but never change it "
           "silently. Every line you present shows stakes, value, lateness "
           "loss, due date and estimate together, so urgency never appears "
           "without value. `planning_fields.missing` names planning fields the "
           "tracker does not have AT ALL: that is a setup gap, so tell the "
           "operator once, plainly, which fields are missing. `truncated` "
           "means the read hit its limit; `items_omitted` counts ranked rows "
           "left out of this block. `available: false` means the backlog is "
           "UNKNOWN (`error` says why): say so, and never present a plan as if "
           "you had read it. `padding` is the ratio of actual to estimated "
           "hours from the recorded evening reviews (`padding_samples` of them; "
           "1.0 until there are enough), by which every estimate is multiplied "
           "when its latest start is worked out.\n"
           if "backlog" in snapshot else "")
        + ("`delegated` is the issues the operator is WAITING ON from other "
           "people, read from the tracker: each with its `assignee`, `status`, "
           "`due_date`, `overdue` (and `days_overdue`) and `days_since_update`, "
           "overdue first, then soonest due, then those with no due date; "
           "`count` and `overdue` are the totals, `items_omitted` the rows left "
           "out of this block and `truncated` that the read hit its limit. Read "
           "it back as what the operator is waiting on: what is overdue, and "
           "what has gone quiet (the longest without an update). It says what "
           "the tracker shows, not why: ask, never guess. `available: false` "
           "means it is UNKNOWN (`error` says why).\n"
           if "delegated" in snapshot else "")
        + ("`staffing_agent` is the team's staffing agent on the roster (its "
           "`id` is the one to consult).\n"
           if "staffing_agent" in snapshot else "")
        + ("`plan` is today's STORED plan, the record the operator approved "
           "(its ranked items with their reasons, the estimate and tier as the "
           "tracker held them when it was stored), or `available: false` with "
           "`reason: no plan stored`. A stored plan exists only once its "
           "proposal executed; never describe a plan as stored otherwise.\n"
           if "plan" in snapshot else "")
        + ("`plan_review` is today's stored plan read against what the record "
           "shows now: each planned item with its `planned` numbers, its "
           "`tracker` status (`done` from the status category; `null` when the "
           "tracker could not say, which is never done), its work `blocks` "
           "from the plan ledger (`state` active with the times the calendar "
           "holds now, or retired) and any `outcome` already recorded; "
           "`blocks_not_on_plan`, `unplanned_outcomes`, and `unplanned_done` "
           "(issues completed today that were not planned, with the search "
           "that found them) or `unplanned_done_omitted` saying why it was "
           "not asked. `available: false` with `reason: no plan stored` means "
           "no plan was stored for today; with `error`, the review could not "
           "be read.\n"
           if "plan_review" in snapshot else "")
        + ("`week` is the coming week computed for you, one line per day read in "
           "turn from the calendar: `available` (false with `reason` or `error` "
           "means that day is UNKNOWN, never free), and for a known day its "
           "`commitments`, your own `work_blocks` and `placeholders` (from the "
           "plan ledger), `conflicts` (a count), `free_minutes` and the "
           "`free_time` stretches inside the working window, and `overbooked` "
           "(less free time than `overbooked_below_minutes`). `overbooked_days` "
           "and `days_with_conflicts` list them.\n"
           if "week" in snapshot else "")
        + ("`deadlines` is every backlog issue due from today through the end "
           "of its `window`, soonest first, against the work-block time the plan "
           "ledger holds for it from today through its due date: `block_minutes` "
           "against `needed_minutes` (its estimate times `padding`), and "
           "`coverage` `covered`, `partly`, `none`, or `unestimated` (no "
           "estimate to measure, never covered). A block the calendar could not "
           "read counts as no time and is named in `blocks_unread`. "
           "`available: false` means the deadlines are UNKNOWN (`error` says "
           "why).\n"
           if "deadlines" in snapshot else "")
        + ("`audit` is the record's account of where planned time went over its "
           "`window` (the days ending yesterday), built from the stored plans, "
           "the outcomes the operator recorded and the plan ledger, never from a "
           "listing of the past calendar. Each part says in `what` what it "
           "counts: `days_with_plan`; `items` planned, done, not done and "
           "unknown (no outcome recorded); `hours`, the estimates against the "
           "actual hours reported and their ratio; `by_tier`; `slipped`, the "
           "issues planned most often without a done outcome; `blocks` created, "
           "moved off their day and retired; and `unplanned_outcomes`. "
           "`no_record` means no plan was stored in the window: there is "
           "nothing to audit, which is not a clean record. Read it back "
           "plainly, including when it is unflattering.\n"
           if "audit" in snapshot else "")
        + "\n"
        "```json\n" + json.dumps(snapshot, indent=2, default=str) + "\n```\n\n"
        "If this is worth the operator's attention AND you have a question only "
        "they can settle, open the conversation with "
        "`ask_operator(needs_discussion=True)` — your report goes in the "
        "question. If you have nothing they need to reply to, answer in plain "
        "text instead; a report nobody must respond to should not cost a "
        "conversation."
    )
    title = {
        "digest": "Evening review", "check_in": "Check-in",
        "morning_report": "Morning report", "week_ahead": "Week ahead",
        "quarter_ahead": "Quarterly review",
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


# The id-matching rule lives with the catalog helpers (the Executor prices a
# deployment from the same match); kept under this name for the diff.
from central_command.integrations.litellm import candidate_ids as _candidate_ids  # noqa: E402


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
    # And a deployment tied to THIS credential is this provider's by
    # definition, whatever its provider fields say (2026-09-14): a gateway
    # credential's provider is 'openai_compatible' while its deployments say
    # 'openai', so the name-based match alone found nothing — every catalog
    # id read as NEW, including the registered one, and the managed set was
    # empty, so stale/unhealthy could never fire for a gateway.
    this_provider = [
        d for d in deployments
        if d.get("credential_name") == credential_name
        or d.get("provider") == provider
        or str(d.get("provider_model") or "").startswith(f"{provider}/")
    ]
    configured_ids: set[str] = set()
    for d in this_provider:
        if d.get("provider_model"):
            configured_ids |= _candidate_ids(d["provider_model"])
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
        and not (_candidate_ids(d["provider_model"]) & set(catalog_by_id))
    ]
    managed_names = {d.get("model_name") for d in managed}
    unhealthy_managed = [u for u in unhealthy if u.get("model") in managed_names]

    return {"missing": missing, "stale": stale, "unhealthy": unhealthy_managed}


# The catalog fields whose change re-opens a decided model. Explicit and
# closed on purpose: a provider's /v1/models entry is nearly immutable (id,
# created, owned_by), so "any change" must name the fields that CAN move —
# OpenAI's `shutdown_date`, Anthropic's `display_name`, a gateway's
# `pricing` — or it silently means "new or removed" while everyone believes it
# means more. Context and description are NOT in the catalog (they live in
# LiteLLM's cost map), so no fingerprint can see them. `pricing` IS, for
# OpenRouter-shaped gateways (2026-09-14): the Executor copies it onto the
# deployment at add time, and a price the provider moves must re-open the
# model so the copy is refreshed — otherwise it is right on the day of the
# add and stale forever after.
FINGERPRINT_FIELDS = ("id", "shutdown_date", "display_name", "pricing")

SNAPSHOT_SETTING = "autodiscovery_snapshot"
HEALTH_CURSOR_SETTING = "autodiscovery_health_cursor"


def catalog_fingerprint(entry: dict) -> str:
    import json

    return json.dumps({f: entry.get(f) for f in FINGERPRINT_FIELDS if f in entry},
                      sort_keys=True, default=str)


def reconcile_snapshot(
    previous: dict,
    catalog: list[dict],
    missing_ids: set[str],
    configured_ids: set[str],
    task_statuses: dict[str, str | None],
) -> dict:
    """Autodiscovery's MEMORY (2026-09-11): decide, per catalog id, whether
    the agent needs to look at it THIS pass. Pure — the caller reads the
    snapshot from `app_setting` and writes the returned one back.

    `previous` is the credential's stored snapshot `{id: {fp, disposition,
    task_id?}}`; disposition is `registered` (on the proxy), `skipped` (a
    task examined it and proposed nothing) or `pending` (a task is open).
    `task_statuses` maps every task_id the snapshot names to its current
    status (None = row gone).

    The rule: an id reaches the agent when it is NEW, when its fingerprint
    CHANGED since it was decided (even if skipped or registered), when it was
    REGISTERED and has since left the proxy (`removed`), or when the task
    that was examining it FAILED. A skipped id with the same fingerprint
    is never shown again — that is the whole fix for the daily re-examination
    of 60 dated snapshots. A pending id whose task finished DONE without
    registering it becomes skipped; nothing else is inferred.

    REVIEW (2026-09-13): a new id is first offered to a REVIEW task (entry
    carries `review_task_id`), where the operator settles add-vs-skip. Skips
    land on the operator's skip list and leave `missing_ids`; what remains
    when that review is DONE comes back with reason `reviewed` — the
    caller's cue to task the ADD. `review_task_id` rides along on the add
    entry so a FAILED add re-offers the id as `reviewed`, never back to
    review. `to_task` therefore carries both kinds; the caller partitions
    on the entry's reason.

    Returns `{"snapshot", "to_task": [catalog entries], "changed": [...]}`
    where `changed` lists REGISTERED ids whose fingerprint moved (a
    maintenance finding — e.g. a shutdown date announced), and `snapshot` is
    the new state with every to-task id already marked pending (the caller
    stamps the task_id once it exists).
    """
    snapshot: dict = {}
    to_task: list[dict] = []
    changed: list[dict] = []
    for entry in catalog:
        cid = str(entry.get("id") or "")
        if not cid:
            continue
        fp = catalog_fingerprint(entry)
        prev = previous.get(cid) or {}
        if cid in configured_ids:
            if prev.get("disposition") == "registered" and prev.get("fp") not in (None, fp):
                changed.append({"id": cid, "before": prev["fp"], "after": fp})
            snapshot[cid] = {"fp": fp, "disposition": "registered"}
            continue
        if cid not in missing_ids:
            continue  # the operator's own skip list: not ours to track
        if not prev:
            reason = "new"
        elif prev.get("disposition") == "registered":
            # It was on the proxy and is not any more (2026-09-14): someone
            # removed it outside a decision this memory recorded. Review it
            # again — the operator says skip there if that is what the
            # removal meant — rather than carrying `registered` forever or
            # inferring a skip nobody made.
            reason = "removed"
        elif prev.get("fp") not in (None, fp):
            reason = "changed"
        elif prev.get("disposition") == "pending" and prev.get("task_id"):
            status = task_statuses.get(prev["task_id"])
            if status == "DONE":
                snapshot[cid] = {"fp": fp, "disposition": "skipped"}
                continue
            if status in ("FAILED", "CANCELLED", None):
                reason = "reviewed" if prev.get("review_task_id") else "retask"
            else:
                snapshot[cid] = {**prev, "fp": fp}  # still being examined
                continue
        elif prev.get("disposition") == "pending" and prev.get("review_task_id"):
            status = task_statuses.get(prev["review_task_id"])
            if status == "DONE":
                reason = "reviewed"  # still missing after the review: an agreed add
            elif status in ("FAILED", "CANCELLED", None):
                reason = "retask"  # review never happened — review again
            else:
                snapshot[cid] = {**prev, "fp": fp}  # under review
                continue
        elif prev.get("disposition") == "pending":
            reason = "retask"  # pending under no task at all
        else:
            snapshot[cid] = {**prev, "fp": fp}  # skipped, unchanged: never shown again
            continue
        snapshot[cid] = {"fp": fp, "disposition": "pending", "reason": reason}
        if reason == "reviewed":
            snapshot[cid]["review_task_id"] = prev.get("review_task_id")
        to_task.append(entry)
    return {"snapshot": snapshot, "to_task": to_task, "changed": changed}


def brief_model_ids(instructions: str) -> list[str]:
    """The catalog ids an add-task brief carries (its trailing JSON block) —
    how a snapshot is bootstrapped from tasks that predate it."""
    import json

    try:
        block = instructions.rsplit("```json\n", 1)[1].rstrip("`\n")
        return [str(m["id"]) for m in json.loads(block) if isinstance(m, dict) and m.get("id")]
    except (IndexError, ValueError, TypeError):
        return []


def bootstrap_snapshot(tasks: list[dict]) -> dict:
    """A snapshot for a credential that has none yet, from the add-tasks of
    earlier passes: a DONE task's ids are `skipped` (it proposed nothing that
    stuck, or the id would be configured), an open task's are `pending` under
    that task, FAILED/CANCELLED ones are forgotten. The fingerprint is unknown
    (""), so the first pass with a snapshot re-fingerprints without re-tasking."""
    snap: dict = {}
    for t in tasks:
        status = t.get("status")
        if status in ("FAILED", "CANCELLED"):
            continue
        for cid in brief_model_ids(t.get("instructions") or ""):
            if status == "DONE":
                snap.setdefault(cid, {"fp": None, "disposition": "skipped"})
            else:
                snap[cid] = {"fp": None, "disposition": "pending", "task_id": t.get("id")}
    return snap


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
    "ambiguous, `ask_operator`; a 'never add this one' answer is recorded "
    "by an `autodiscovery.skip` proposal (gated, one per credential) — "
    "normally from the REVIEW task where the operator settles it.\n\n"
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
        "counterpart if one exists, for cost-map defaults). The provider "
        "model is `openai/` + the catalog `id` EXACTLY as listed below, "
        "vendor segment included — `openai/openai/gpt-5.6-sol`, "
        "`openai/anthropic/claude-opus-5`; a doubled `openai/openai/` is "
        "correct and a stripped vendor is a 404 or a silent alias. NEVER set a "
        "price (input_cost_per_token and its siblings): the Executor copies "
        "per-token prices from the credential's own catalog and drops any "
        "you wrote — a card's per-million price typed as a per-token price "
        "booked $80,589 of spend on 2026-09-13. On approval the "
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
        "NON-CHAT MODELS (transcription, TTS, image generation, embedding, "
        "rerank, moderation, legacy completion) are registered the same way: "
        "declare their catalog `mode` (audio_transcription / audio_speech / "
        "image_generation / embedding / rerank / moderation / completion) and "
        "the probe runs a mode-matched one-request battery on the model's own "
        "endpoint instead of the chat checks. A mode with no battery (e.g. "
        "realtime) still registers — the probe reports inconclusive and the "
        "capabilities are declared from the model card. Don't skip a model "
        "just because it isn't chat.\n\n"
        "MEMORY: a model you do NOT propose is recorded as skipped and will "
        "not be shown to you again unless its catalog entry changes, so a "
        "skip needs no bookkeeping proposal — just don't propose it.\n\n"
        "SKIP CANDIDATES: the operator already agreed to add this slice in "
        "the review discussion. If registering one still looks wrong (a dated "
        "snapshot of an alias already covered elsewhere, an imminent "
        "shutdown), don't propose it and say why in your result — it is "
        "recorded as skipped.\n\n"
        "```json\n" + json.dumps(models, indent=2, default=str) + "\n```"
    )


NO_VENDOR = "(no vendor)"


def vendor_of(catalog_id: str) -> str:
    """The vendor prefix a gateway catalog id carries ('anthropic/claude-x'
    -> 'anthropic'); a bare id has none. The unit the operator answers a
    review in, and the unit `autodiscovery.skip` expands by."""
    return catalog_id.split("/", 1)[0] if "/" in catalog_id else NO_VENDOR


def group_by_vendor(ids: list[str]) -> dict[str, list[str]]:
    """Catalog ids grouped by vendor prefix, in first-seen order — plain
    code, so the agent never has to derive or resolve the grouping."""
    groups: dict[str, list[str]] = {}
    for cid in ids:
        groups.setdefault(vendor_of(cid), []).append(cid)
    return groups


def _review_brief(credential_name: str, models: list[dict],
                  removed_ids: set[str] = frozenset()) -> str:
    """One credential's NEW catalog ids, for the operator to settle add-vs-
    skip with the agent in a discussion (2026-09-13). Compact on purpose: a
    gateway credential can surface hundreds of ids at once, and this brief
    registers nothing — the agreed adds become one add-task per model on the
    next pass, once this task is DONE.

    Pre-grouped by vendor (2026-09-14): a 314-id list handed to one model
    turn with 'group it and resolve the operator's patterns to exact ids
    yourself' never finished. The grouping is code, the operator answers by
    group, and the skip proposal names GROUPS (`vendors`) — the Executor
    expands them to exact ids from the snapshot. The model does no set
    arithmetic."""
    labels: dict[str, str] = {}
    for m in models:
        cid = str(m.get("id"))
        label = cid
        if m.get("display_name"):
            label += f" — {m['display_name']}"
        if m.get("shutdown_date"):
            label += f" (shutdown {m['shutdown_date']})"
        if cid in removed_ids:
            label += " [was registered; since removed from the proxy]"
        labels[cid] = label
    groups = group_by_vendor(list(labels))
    lines = [f"{vendor} ({len(ids)}): " + ", ".join(labels[c] for c in ids)
             for vendor, ids in groups.items()]
    return (
        _DISCOVERY_INTRO
        + _DISCOVERY_NAMING
        + f"NEW under credential {credential_name!r}: {len(models)} catalog "
        f"model(s) in {len(groups)} vendor group(s) the proxy does not have "
        "and nobody has decided on yet. This task REGISTERS NOTHING. Its job "
        "is the decision, and the list below is ALREADY GROUPED by vendor "
        "prefix — do not regroup it, and never retype the ids:\n\n"
        "1. Form a recommendation per GROUP (add, skip, or ask) — dated "
        "snapshots of an undated alias, imminent shutdowns, and non-chat "
        "modes the operator has no use for are the usual skips.\n"
        "2. Open a discussion with `ask_operator(needs_discussion=True)`: put "
        "the group summary (vendor, count, your recommendation) in the "
        "question — not every id — and settle with the operator which GROUPS "
        "to ADD and which to SKIP; they may also name individual ids either "
        "way.\n"
        "3. Propose ONE `autodiscovery.skip` (via propose_litellm_change, "
        "credential_name = this credential, `vendors` = exactly the agreed "
        "skip groups as written in the list below, `model_ids` = only the "
        "individual ids the operator named, reason = the operator's words). "
        "Plain code expands each vendor to its exact ids on execution; you "
        "never enumerate them. It is gated: they approve it in the Inbox "
        "like any change.\n"
        "4. `conclude_discussion`, then end with a result naming the agreed "
        "ADD groups. Do NOT register them here: once this task is DONE the "
        "next discovery pass hands you one add-task per agreed model, each "
        "with the full registration procedure.\n\n"
        "If the operator wants none of them, propose the skip with every "
        "vendor. If they do not settle a group, leave it OUT of the skip — "
        "an undecided id is reviewed again next pass, a skipped one never "
        "is. An entry marked [was registered; since removed from the proxy] "
        "was on the proxy once and is gone: ask whether the removal meant "
        "skip.\n\n"
        + "\n".join(lines) + "\n"
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
        "Verification of any fix is a LATER discovery pass observing health "
        "clean (the health walk is capped per credential per pass and "
        "rotates); you don't need to re-check it now.\n\n"
        "CHANGED (a REGISTERED model whose provider catalog entry moved since "
        "it was last seen — `before`/`after` are the fingerprinted fields, "
        "typically a shutdown date being announced): read the diff and "
        "propose litellm.update_model or litellm.delete_model only if it "
        "warrants one; otherwise say so in your result.\n\n"
        "UNDECLARED (managed deployments whose measured capabilities are not "
        "declared): propose ONE litellm.update_model per alias carrying "
        "exactly its `suggested_model_info` as `model_info` — measured, "
        "never guessed. This converges: once declared, an alias never "
        "re-qualifies.\n\n"
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
    unhealthy, all credentials) plus, per credential, ONE REVIEW task for
    every id nobody has decided on (2026-09-13: the agent opens a discussion
    and the operator settles add-vs-skip; skips become a gated
    `autodiscovery.skip`), and one ADD task per `batch_size`-sized chunk of
    the ids a DONE review left standing. Every chunk becomes a task in the
    SAME pass (2026-08-21); the operator's review is what bounds the count.

    GENERATION GUARD: before tasking a credential's missing list, this checks
    for still-open ("LiteLLM autodiscovery: <credential> batch …" / "… review")
    tasks from an earlier pass. If any are still open, nothing new of that
    kind is created for that credential this pass — a daily tick mid-drain must not re-task models
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
    skip_all = list(decisions.get("skip") or [])
    skip_by_cred = decisions.get("skip_by_credential") or {}
    snapshots = dict(await repo.get_app_setting(SNAPSHOT_SETTING, {}))

    deployments = (await litellm_client.list_models())["models"]
    cred_names = {c["credential_name"] for c in credentials}
    # Health is probed ONLY for managed deployments, one scoped call per
    # alias (each is a REAL provider call). Whole-fleet /health is the wrong
    # tool here: it walks the hand-tuned local fleet too — swapping GPU
    # models on the model host and blowing the 30s client timeout (measured
    # 2026-08-20) — and no managed deployments means no health call at all.
    # A health call that RAISES is not a finding about the model — the proxy
    # answers 200 with the failing endpoint listed when a model is down. An
    # exception is this mechanism failing (our client refusing the alias,
    # the proxy unreachable), so it lands in the tick's own `errors`, which
    # the engine records as material, instead of being handed to the agent
    # to diagnose and ask the operator about (2026-09-14).
    #
    # And the walk is CAPPED per credential: one probe per deployment is one
    # request at the provider, and 282 of them in ten minutes got the egress
    # IP banned by a gateway's edge for ~2h — taking that credential's catalog
    # fetch AND the manager's own model down with it, twice in one night
    # (2026-09-19). At most `health_batch` deployments per credential per
    # tick, resuming after the alias the last tick stopped at
    # (`autodiscovery_health_cursor`), so the fleet still converges over a
    # few nights. The default is sized to the one published budget we know
    # of — Kilo.ai: 200 requests per hour per IP, shared by EVERY Kilo call
    # this host makes (the manager's own turns, the capability probes, and
    # a failing probe's retries) — 40 tripped it when a double press ran two
    # sweeps at once. A LiteLLM-side rpm limit is not a substitute: it is per
    # DEPLOYMENT (282 deployments × 1 call each never trips it), the proxy's
    # /health calls the provider directly past the router anyway, and a limit
    # refuses with 429 rather than pacing.
    health_batch = _parse_positive_int(params, "health_batch", 15)
    cursors = dict(await repo.get_app_setting(HEALTH_CURSOR_SETTING, {}))
    by_cred: dict[str, set[str]] = {}
    for d in deployments:
        if d.get("credential_name") in cred_names and d.get("model_name"):
            by_cred.setdefault(d["credential_name"], set()).add(d["model_name"])
    unhealthy: list[dict] = []
    errors: dict[str, str] = {}
    for cred, names in by_cred.items():
        aliases = sorted(names)
        start = bisect.bisect_right(aliases, cursors.get(cred, ""))
        window = (aliases[start:] + aliases[:start])[:health_batch]
        for alias in window:
            try:
                res = await litellm_client.check_model_health(alias)
                unhealthy.extend(res.get("unhealthy") or [])
            except Exception as e:  # noqa: BLE001 — one failed call must not kill the tick
                errors[f"health:{alias}"] = f"{type(e).__name__}: {e}"
        cursors[cred] = window[-1]
    await repo.set_app_setting(HEALTH_CURSOR_SETTING, cursors)

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
            # Responses-bridge alias — the chat battery can't probe it. Not
            # graphiti-llm any more by decision (2026-09-21: its caller uses
            # the stock chat-completions client and a bridged registration
            # 404s there); this skip is for any other alias still bridging.
            continue

        alias = d.get("model_name")
        if not alias:
            continue
        probed += 1
        try:
            probe = await litellm_client.probe_model(alias)
        except Exception as e:  # noqa: BLE001 — one bad probe must not kill the tick
            errors[f"probe:{alias}"] = f"{type(e).__name__}: {e}"
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
        skip = skip_all + list(skip_by_cred.get(name) or [])
        drift = compute_discovery_drift(name, cred.get("provider"), catalog, deployments, unhealthy, skip)
        # MEMORY (2026-09-11): only what is new, changed, or re-examinable
        # reaches the agent. `missing` is the raw diff; the snapshot decides
        # which of it is actually work this pass. Configured ids are the
        # catalog minus missing minus the operator's skip list.
        missing_ids = {str(m["id"]) for m in drift["missing"]}
        configured_ids = {
            str(m["id"]) for m in catalog
            if isinstance(m, dict) and m.get("id")
            and str(m["id"]) not in missing_ids and str(m["id"]) not in set(skip)
        }
        previous = snapshots.get(name) or {}
        if not previous:
            previous = bootstrap_snapshot(await repo.tasks_with_title_prefix(
                f"LiteLLM autodiscovery: {name} batch"))
        task_ids_named = {
            tid for e in previous.values()
            for tid in (e.get("task_id"), e.get("review_task_id")) if tid
        }
        statuses: dict[str, str | None] = {}
        for tid in task_ids_named:
            row = await repo.get_task(tid)
            statuses[tid] = row.get("status") if row else None
        rec = reconcile_snapshot(previous, catalog, missing_ids, configured_ids, statuses)
        snapshots[name] = rec["snapshot"]
        drift["missing"] = rec["to_task"]
        if rec["changed"]:
            drift["changed"] = rec["changed"]
        if drift["missing"] or drift["stale"] or drift["unhealthy"] or drift.get("changed"):
            findings[name] = drift

    await repo.set_app_setting(SNAPSHOT_SETTING, snapshots)
    if not findings and not undeclared:
        return {"drift": False, "errors": errors} if errors else {"drift": False}

    batch_size = _parse_positive_int(params, "batch_size", 1)

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
        if d.get("error") or d.get("stale") or d.get("unhealthy") or d.get("changed")
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
        entries = snapshots.get(name, {})
        to_review = [m for m in (d.get("missing") or [])
                     if entries.get(str(m.get("id")), {}).get("reason") != "reviewed"]
        missing = [m for m in (d.get("missing") or [])
                   if entries.get(str(m.get("id")), {}).get("reason") == "reviewed"]
        # Generation guards, one per task kind: a still-open review or batch
        # task from an earlier pass means this credential is already queued
        # or in flight for that kind — task nothing new of it this pass and
        # report what's outstanding instead. An entry not tasked this pass
        # keeps its snapshot row, which the reconciler re-offers next pass.
        if to_review:
            if await repo.count_nonterminal_tasks_with_title_prefix(
                f"LiteLLM autodiscovery: {name} review"
            ):
                backlog[name] = backlog.get(name, 0) + len(to_review)
            else:
                removed = {cid for cid, e in entries.items() if e.get("reason") == "removed"}
                result = await create_and_run_task(
                    _review_brief(name, to_review, removed), f"LiteLLM autodiscovery: {name} review",
                    litellm_manager.AGENT_ID, actor=f"heartbeat:{schedule_id}", background=True,
                )
                if result.get("task_id"):
                    task_ids.append(result["task_id"])
                    for m in to_review:
                        entry = entries.get(str(m.get("id")))
                        if entry is not None:
                            entry["review_task_id"] = result["task_id"]
        if not missing:
            continue
        if await repo.count_nonterminal_tasks_with_title_prefix(
            f"LiteLLM autodiscovery: {name} batch"
        ):
            backlog[name] = backlog.get(name, 0) + len(missing)
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
                for m in chunk:
                    entry = snapshots.get(name, {}).get(str(m.get("id")))
                    if entry is not None:
                        entry["task_id"] = result["task_id"]
    await repo.set_app_setting(SNAPSHOT_SETTING, snapshots)

    out = {
        "drift": True,
        "task_ids": task_ids,
        "credentials": sorted(findings),
        "queued": len(task_ids),
    }
    if errors:
        out["errors"] = errors
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
            kind="source.walk",
            description=(
                "Walk every enabled document source and enroll its catalog "
                "backlog at that source's pace (deterministic; no LLM)."
            ),
            params={},
            required=(),
            levers=(
                "ingest.watcher.walk_source",
                "ingest.catalog_enroll.enroll_pending",
            ),
            run=_source_walk,
            # QUIET-ELIGIBLE. NON-AUTHORISING: it writes inventory rows and
            # enrolls ledger items; nothing is worked until the dispatcher's
            # valves say so, and every proposal that follows still gates.
            # SELF-RECORDING: the walk emits catalog.walk.completed, the sweep
            # catalog.enrolled / catalog.version.autofolded. The common tick —
            # a tree nobody touched since the last walk — must cost no history.
            material=lambda r: bool(any(
                s.get("error") or s.get("new_lineages") or s.get("new_versions")
                or s.get("marked_missing")
                or (s.get("enrollment") or {}).get("enrolled")
                for s in (r.get("sources") or {}).values()
            )),
        ),
        ActionSpec(
            kind="wiki.freshness",
            description=(
                "Sweep the wiki claims ledger, flag pages whose evidence moved "
                "on, and enroll one repair item per page (deterministic; no LLM)."
            ),
            params={"pages": "how many stale pages to act on per tick (default 5)"},
            required=(),
            levers=("ingest.wiki_freshness.sweep",),
            run=_wiki_freshness,
            # QUIET-ELIGIBLE. NON-AUTHORISING: it enrolls ledger items — the
            # dispatcher's valves decide when any of it is worked, and the
            # repair proposal that follows still gates. (Annotation in 'auto'
            # mode is the operator's own graduated action class, and records
            # itself.) SELF-RECORDING: every changed stale set emits
            # wiki.claims.stale, every enrollment work.enrolled, every panel
            # write wiki.page.annotated. A wiki whose sources have not moved —
            # the common tick — must cost no history.
            material=lambda r: bool(
                r.get("emitted") or r.get("enrolled") or r.get("annotated")
            ),
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
            # A unit newly reported failing despite a healthy probe is material
            # too: the operator should see the tick that noticed it.
            material=lambda r: bool(
                r.get("resumed") or r.get("retried_executions")
                or r.get("retried_tasks") or r.get("errors")
                or r.get("failing_despite_probe")
            ),
        ),
        ActionSpec(
            kind="graph.verify_sweep",
            description=(
                "Audit approved graph episodes once their ingest job is done: "
                "mechanical read-back checks (landed, right group, non-empty, "
                "embedded) plus the graph-auditor's alignment judgment; "
                "findings park for the operator. A row whose extraction is "
                "still queued waits; one whose extraction failed was already "
                "parked by the ingest worker."
            ),
            params={
                "settle_minutes": (
                    "minutes after execute before a row is first checked "
                    "(default 10)"
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
                or r.get("missing") or r.get("failed") or r.get("errors")
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
                "digest, a check-in, the morning report, the weekly plan, the "
                "quarterly review, or the onboarding tour of the team."
            ),
            params={"kind": "digest | check_in | morning_report | week_ahead "
                            "| quarter_ahead | onboarding_tour"},
            required=("kind",),
            runs_as="ea",
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
                "LiteLLM proxy's configured deployments; on real drift hand "
                "the litellm-manager a review task per credential (the "
                "operator settles add-vs-skip in a discussion) and one small "
                "add-task per agreed model."
            ),
            params={
                "batch_size": "models per add-task, default 1",
                "probe_batch": (
                    "managed deployments with undeclared capabilities to "
                    "probe per tick, default 3"
                ),
                "health_batch": (
                    "managed deployments health-checked per credential per "
                    "tick (rotating), default 15"
                ),
            },
            required=(),
            runs_as="litellm-manager",
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
            # Truthy on errors too (2026-09-14): a probe or health call the
            # mechanism itself could not make is a failure of THIS action,
            # recorded here, never a question routed to the operator via
            # the agent.
            material=lambda r: bool(r.get("task_ids") or r.get("backlog") or r.get("errors")),
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
