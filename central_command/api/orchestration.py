"""The orchestration driver (D7 phase 2) — the park/resume loop behind the
orchestrator's projects.

The orchestrator is a normal governed agent whose defining tools are DEFERRED
(`submit_plan`, `assign_work`, `ask_operator` — runtime/tools.py): each round
ends either in a plain-text final report or in a `DeferredToolRequests` batch.
This driver validates the batch, creates the child tasks (through THE one
create path, `routes.create_and_run_task`) and operator items, and parks the
run durably — the SAME pause shape M2 built for approval waits, pointed at a
second kind of wait. Children that answer in text resolve ungated and resume
the loop directly; children that propose world changes park in the Decisions
Inbox first, and the loop resumes only after the operator's decision.
Approval attaches to what work DOES, never to the trigger.

Mechanical guards (all tested):
- STAR SHAPE / DEPTH 1: a task carrying a parent_session_id is itself a child
  and is refused orchestration; assign targets must be ACTIVE taskable roster
  agents and never the orchestrator itself.
- PLAN FIRST: assign_work is refused until a submitted plan is APPROVED
  (The operator, 2026-07-24: plan review ON).
- STALL, NOT BUDGETS (The operator, 2026-07-24): consecutive no-progress/looping
  rounds force an operator question instead of another round; generous
  config-only caps (rounds/fanout) exist purely as runaway-token backstops
  and escalate the same way.

Tier note: this module is control-plane (api) — it may import runtime and
gateway-adjacent repo freely; the runtime→gateway import ban is untouched.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

from central_command import events
from central_command.config import settings
from central_command.contract import (
    TRANSIENT,
    classify_failure,
    classify_failure_text,
    retry_backoff_seconds,
)
from central_command.db import repo
from central_command.runtime.deps import TriageDeps

log = logging.getLogger(__name__)

AGENT_ID = "orchestrator"
TASK_TERMINAL = ("DONE", "FAILED", "CANCELLED")
_ORCH_TOOLS = ("submit_plan", "assign_work", "ask_operator")
_MAX_BATCH_RETRIES = 3
_OUTCOME_CHARS = 3000  # child outcome truncation in the result contract


async def _resolve_project_model(model=None):
    if model is not None:
        return model
    if settings.demo_mode:
        from central_command.runtime.spike_model import make_project_model

        return make_project_model()
    from central_command.runtime.models import resolve_model

    return await resolve_model(agent_id=AGENT_ID)


async def _build_agent(model=None):
    from central_command.runtime.orchestrator import build_project_orchestrator
    from central_command.runtime import skills as skills_mod
    from central_command.runtime.packs import granted_packs

    packs = await granted_packs(AGENT_ID)
    if "orchestrate" not in packs:
        raise RuntimeError(
            "the orchestrator's 'orchestrate' pack grant is missing or revoked"
        )
    governed = await repo.current_charter(AGENT_ID)
    caps, catalog = await skills_mod.loadout(AGENT_ID)
    from central_command.runtime.roster import team_section

    return await build_project_orchestrator(
        model=await _resolve_project_model(model),
        charter=governed["content"] if governed else None,
        packs=packs,
        capabilities=caps, skills_catalog=catalog,
        team_section=await team_section(AGENT_ID),
    )


async def _agent_cards() -> str:
    """The roster as generated structured data — role + capability list FROM
    GRANTS (the D25 authority), never hand-written prose. The field's largest
    measured failure class is capability-spec drift; generation is the fix."""
    from central_command.runtime.packs import granted_capability_names, granted_packs
    from central_command.runtime.roster import roster

    lines = []
    for a in await roster():
        if not a.taskable or a.id == AGENT_ID:
            continue
        packs = await granted_packs(a.id)
        caps = sorted(granted_capability_names(packs))
        lines.append(
            f"- {a.id}: {a.role}\n"
            f"  gated capabilities (generated from grants): "
            f"{', '.join(caps) if caps else '(none — answers and reads only)'}"
        )
    return "\n".join(lines) if lines else "(no taskable agents on the roster)"


def _briefing(task: dict, cards: str) -> str:
    return (
        "PROJECT TASK (from the team lead, trusted) — you are ORCHESTRATING "
        "this, not performing it:\n"
        f"{task['instructions']}\n\n"
        "AGENT CARDS — the taskable roster you may assign_work to:\n"
        f"{cards}\n\n"
        "Start by understanding the task, then submit_plan and wait for the "
        "operator's review. Remember: record_progress first, every round."
    )


def _call_args(call) -> dict:
    raw = call.args
    if isinstance(raw, str):
        raw = json.loads(raw)
    return raw if isinstance(raw, dict) else {}


# --- the round processor -------------------------------------------------------


async def run_project(task: dict, model=None) -> dict:
    """Entry point: run the orchestration loop for a project task assigned to
    the orchestrator. Returns when the loop parks (children/operator pending)
    or finishes; the caller's detached wrapper records any exception FAILED."""
    if task.get("parent_session_id"):
        # DEPTH 1, mechanically: an agent-created task is itself a child.
        raise ValueError(
            "depth-1 guard: an agent-created (child) task cannot be orchestrated"
        )

    from central_command.runtime.run import RunWindowExhausted, _run_live

    session_id = "sess_" + uuid.uuid4().hex[:12]
    agent = await _build_agent(model)
    deps = TriageDeps(session_id=session_id, agent_id=AGENT_ID)
    state = {
        "task_id": task["id"], "round": 0, "stalls": 0, "assigns": 0,
        "plan_approved": False, "retries": 0, "pending": {},
    }
    try:
        result = await _run_live(
            agent, _briefing(task, await _agent_cards()), model=None, deps=deps,
            agent_id=AGENT_ID, session_id=session_id, task_id=task["id"],
            continuable=True, after="orchestrator", continue_state=state,
        )
    except RunWindowExhausted as e:
        return _orch_window_parked(e, state)
    return await _process(task, session_id, result, state, deps, model)


def _orch_window_parked(e: RunWindowExhausted, state: dict) -> dict:
    """A ROUND spent its request window. The loop is parked on the operator's
    continue item, exactly like a stall park — and like a stall park the project
    task row is NOT touched: it is IN_PROGRESS and still is, waiting on a human.
    """
    return {"parked": True, "window_exhausted": True, "session_id": e.session_id,
            "task_id": state["task_id"], "item_id": e.item_id}


async def _finish(task_id: str, session_id: str, text: str) -> dict:
    from central_command.runtime.converse import land_session

    await land_session(session_id, agent_id=AGENT_ID)
    await repo.resolve_task(task_id, "DONE", outcome=text)
    await events.emit(
        "task.completed", ref_id=task_id,
        payload={"outcome": text[:500], "orchestration": True},
        actor=f"agent:{AGENT_ID}",
    )
    await events.emit(
        "orchestration.completed", ref_id=session_id,
        payload={"task_id": task_id}, actor=f"agent:{AGENT_ID}",
    )
    return {"parked": False, "done": True, "task_id": task_id,
            "session_id": session_id, "output": text}


async def _fail(task_id: str, session_id: str, reason: str) -> dict:
    from central_command.runtime.converse import land_session

    await land_session(session_id, failed=True, agent_id=AGENT_ID, reason=reason)
    await repo.resolve_task(task_id, "FAILED", outcome=reason)
    await events.emit(
        "task.run_failed", ref_id=task_id,
        payload={"error": reason, "session_id": session_id,
                 # Only the stringified reason survives to here.
                 "transient": classify_failure_text(reason) == "transient"},
        actor=f"agent:{AGENT_ID}",
    )
    return {"parked": False, "done": False, "failed": reason,
            "task_id": task_id, "session_id": session_id}


async def _reject_batch(
    task: dict, session_id: str, result, state: dict, deps, model,
    calls, problems: list[str],
) -> dict:
    """Corrective immediate resume: NOTHING from the batch executes; every call
    gets the same honest rejection so the orchestrator re-decides. Bounded —
    a model that cannot produce a valid batch fails the task honestly."""
    from pydantic_ai import DeferredToolResults

    from central_command.runtime.run import RunWindowExhausted, _run_live

    state["retries"] += 1
    await events.emit(
        "orchestration.batch_rejected", ref_id=session_id,
        payload={"problems": problems, "retry": state["retries"],
                 "task_id": task["id"]},
        actor="system",
    )
    if state["retries"] > _MAX_BATCH_RETRIES:
        return await _fail(
            task["id"], session_id,
            f"orchestration: {_MAX_BATCH_RETRIES} invalid tool batches in a row "
            f"(last problems: {'; '.join(problems)})",
        )
    dtr = DeferredToolResults()
    msg = ("BATCH REJECTED — nothing was executed: " + "; ".join(problems) +
           ". Re-decide this round (the rules are in your charter).")
    for c in calls:
        dtr.calls[c.tool_call_id] = msg
    agent = await _build_agent(model)
    fresh = TriageDeps(session_id=session_id, agent_id=AGENT_ID)
    try:
        result2 = await _run_live(
            agent, None, model=None, deps=fresh, agent_id=AGENT_ID,
            session_id=session_id, message_history=result.all_messages(),
            deferred_tool_results=dtr,
            continuable=True, after="orchestrator", continue_state=state,
        )
    except RunWindowExhausted as e:
        # Caught HERE, not at the callers: `_reject_batch` is reached through
        # `_process`, which neither `run_project` nor `maybe_resume` wraps.
        return _orch_window_parked(e, state)
    return await _process(task, session_id, result2, state, fresh, model)


async def _park_stalled(
    task: dict, session_id: str, result, state: dict, calls, reason: str,
    ledger: list[dict],
) -> dict:
    """The stall escalation (The operator: pause when stuck, never silently retry):
    the requested calls are NOT executed; ONE operator item carries the reason
    and the ledger, and every pending call resumes with the operator's answer."""
    from central_command.runtime.durable import dump_run_state

    await events.emit(
        "orchestration.stalled", ref_id=session_id,
        payload={"reason": reason, "task_id": task["id"],
                 "round": state["round"], "ledger": ledger[-3:]},
        actor="system",
    )
    item_id = "oi_" + uuid.uuid4().hex[:12]
    body = (
        f"The orchestration loop was PAUSED by the control plane: {reason}.\n"
        f"Round {state['round']}; recent progress ledger: {json.dumps(ledger[-3:])}\n"
        "The orchestrator's requested actions were NOT executed. Your answer "
        "resumes the loop as trusted guidance."
    )
    await repo.create_operator_item(
        item_id, "stall", session_id, AGENT_ID, body, task_id=task["id"],
    )
    await events.emit(
        "orchestration.question", ref_id=item_id,
        payload={"kind": "stall", "session_id": session_id, "task_id": task["id"],
                 "body": body[:500]},
        actor="system",
    )
    state["pending"] = {
        c.tool_call_id: {"kind": "stall", "item_id": item_id} for c in calls
    }
    await repo.park_session_awaiting_agents(
        session_id,
        {"messages": dump_run_state(result, "")["messages"], "orch": state},
    )
    await events.emit(
        "orchestration.parked", ref_id=session_id,
        payload={"task_id": task["id"], "pending": {"stall": len(calls)}},
        actor="system",
    )
    return {"parked": True, "stalled": True, "session_id": session_id,
            "task_id": task["id"], "item_id": item_id}


async def _process(task, session_id: str, result, state: dict, deps, model) -> dict:
    from pydantic_ai import DeferredToolRequests

    from central_command.runtime.durable import dump_run_state

    output = result.output
    if not isinstance(output, DeferredToolRequests):
        return await _finish(task["id"], session_id, str(output))

    calls = list(output.calls)
    state["round"] += 1

    # Stall accounting from THIS round's ledger (deps are fresh per round).
    ledger = deps.progress_ledger
    stalled_now = bool(ledger) and (
        (not ledger[-1]["made_progress"]) or ledger[-1]["in_a_loop"]
    )
    state["stalls"] = state["stalls"] + 1 if stalled_now else 0

    plans = [c for c in calls if c.tool_name == "submit_plan"]
    asks = [c for c in calls if c.tool_name == "ask_operator"]
    assigns = [c for c in calls if c.tool_name == "assign_work"]
    strangers = [c for c in calls if c.tool_name not in _ORCH_TOOLS]

    # Safety escalations — never another silent round.
    reason = None
    if state["stalls"] >= settings.orch_stall_limit:
        reason = (f"{state['stalls']} consecutive rounds without progress "
                  "(the progress ledger)")
    elif state["round"] > settings.orch_max_rounds:
        reason = f"round backstop hit ({settings.orch_max_rounds})"
    elif state["assigns"] + len(assigns) > settings.orch_fanout_limit:
        reason = f"fan-out backstop hit ({settings.orch_fanout_limit} assignments)"
    if reason is not None:
        return await _park_stalled(task, session_id, result, state, calls,
                                   reason, ledger)

    # Batch validation — all-or-nothing, so a bad target never half-executes.
    problems: list[str] = []
    if strangers:
        problems.append(
            f"unexpected deferred call(s): {[c.tool_name for c in strangers]}")
    if assigns and not state["plan_approved"]:
        problems.append("assign_work before an APPROVED plan — submit_plan and "
                        "wait for the operator's review first")
    from central_command.runtime.roster import taskable_agent_ids

    taskable = [a for a in await taskable_agent_ids() if a != AGENT_ID]
    for c in assigns:
        args = _call_args(c)
        target = args.get("agent_id")
        if target not in taskable:
            problems.append(
                f"assign_work target {target!r} is not an ACTIVE taskable roster "
                f"agent (choose from: {taskable})")
        if not str(args.get("instructions", "")).strip():
            problems.append("assign_work requires non-empty instructions")
    for c in plans:
        if not str(_call_args(c).get("plan", "")).strip():
            problems.append("submit_plan requires a non-empty plan")
    for c in asks:
        if not str(_call_args(c).get("question", "")).strip():
            problems.append("ask_operator requires a non-empty question")
    if problems:
        return await _reject_batch(task, session_id, result, state, deps, model,
                                   calls, problems)
    state["retries"] = 0

    # Execute the batch. The round event goes on the log BEFORE the work it
    # authorises (M8); each child/item then writes its own events.
    await events.emit(
        "orchestration.round", ref_id=session_id,
        payload={"task_id": task["id"], "round": state["round"],
                 "plans": len(plans), "questions": len(asks),
                 "assignments": [
                     {"agent_id": _call_args(c).get("agent_id"),
                      "title": str(_call_args(c).get("title", ""))[:120]}
                     for c in assigns]},
        actor=f"agent:{AGENT_ID}",
    )
    pending: dict[str, dict] = {}
    for c in plans + asks:
        kind = "plan_review" if c.tool_name == "submit_plan" else "question"
        args = _call_args(c)
        body = args.get("plan") or args.get("question") or ""
        item_id = "oi_" + uuid.uuid4().hex[:12]
        await repo.create_operator_item(
            item_id, kind, session_id, AGENT_ID, body, task_id=task["id"],
            tool_call_id=c.tool_call_id,
        )
        await events.emit(
            "orchestration.question" if kind == "question"
            else "orchestration.plan_submitted",
            ref_id=item_id,
            payload={"session_id": session_id, "task_id": task["id"],
                     "body": body[:1000]},
            actor=f"agent:{AGENT_ID}",
        )
        pending[c.tool_call_id] = {"kind": kind, "item_id": item_id}
    for c in assigns:
        from central_command.api.routes import create_and_run_task

        args = _call_args(c)
        r = await create_and_run_task(
            str(args["instructions"]), str(args.get("title") or "")[:120] or None,
            args["agent_id"], actor=f"agent:{AGENT_ID}", background=True,
            parent_session_id=session_id,
        )
        pending[c.tool_call_id] = {"kind": "assign", "task_id": r["task_id"]}
    state["assigns"] += len(assigns)
    state["pending"] = pending

    await repo.park_session_awaiting_agents(
        session_id, {"messages": dump_run_state(result, "")["messages"],
                     "orch": state},
    )
    await events.emit(
        "orchestration.parked", ref_id=session_id,
        payload={"task_id": task["id"],
                 "pending": {k: sum(1 for p in pending.values()
                                    if p["kind"] == k)
                             for k in ("assign", "plan_review", "question")}},
        actor="system",
    )
    return {"parked": True, "session_id": session_id, "task_id": task["id"],
            "pending": len(pending)}


# --- resume --------------------------------------------------------------------


def _child_result(t: dict) -> str:
    return json.dumps({
        "task_id": t["id"], "agent_id": t["agent_id"], "status": t["status"],
        "outcome": (t.get("outcome") or "")[:_OUTCOME_CHARS],
        "session_id": t.get("session_id"),
    })


async def _collect(pending: dict) -> tuple[bool, dict, dict]:
    """Try to resolve every pending deferred call from the current truth
    (task rows / operator items). Returns (all_ready, results_by_call_id,
    flags) — read-only, safe to call repeatedly."""
    results: dict[str, str] = {}
    flags: dict = {"plan_approved": None, "gaps": []}
    for call_id, p in pending.items():
        if p["kind"] == "assign":
            t = await repo.get_task(p["task_id"])
            if t is None or t["status"] not in TASK_TERMINAL:
                return False, {}, flags
            results[call_id] = _child_result(t)
            # Gaps are structured records now, not prose the parent greps for.
            # A child that declared one did so through declare_gap, which wrote a
            # gap.declared event against its own session.
            for gap in await repo.gaps_for_session(t.get("session_id")):
                flags["gaps"].append({
                    **gap,
                    "task_id": t["id"],
                    "agent_id": t["agent_id"],  # the task row is authoritative:
                    # gap["agent_id"] came from ctx.deps at declare_gap time and
                    # can be None — spread first so the task row always wins.
                })
        else:  # plan_review | question | stall
            item = await repo.get_operator_item(p["item_id"])
            if item is None or item["status"] != "ANSWERED":
                return False, {}, flags
            answer = item.get("answer") or ""
            if p["kind"] == "plan_review":
                approved = item.get("verdict") == "approved"
                flags["plan_approved"] = approved
                results[call_id] = (
                    ("PLAN APPROVED by the operator."
                     + (f" Note (trusted): {answer}" if answer else ""))
                    if approved else
                    f"PLAN NOT APPROVED — operator revision note (trusted): {answer}"
                )
            else:
                from central_command.runtime.questions import lane_exchange

                results[call_id] = (
                    f"OPERATOR ANSWER (trusted): {answer}"
                    + await lane_exchange(item)
                )
    return True, results, flags


async def maybe_resume(session_id: str, model=None) -> dict | None:
    """Resume a parked orchestration if everything it waits on has resolved.
    Called by the child-terminal hooks, the answer endpoint, and the sweep.
    The AWAITING_AGENTS→RUNNING flip in SQL is the claim — two children
    resolving at once produce exactly one resume."""
    from pydantic_ai import DeferredToolResults

    from central_command.runtime.durable import load_messages
    from central_command.runtime.run import RunWindowExhausted, _run_live

    if await repo.session_status(session_id) != "AWAITING_AGENTS":
        return None
    rs = await repo.get_session_run_state(session_id)
    if not rs or "orch" not in rs:
        return None
    state = rs["orch"]
    ready, results, flags = await _collect(state.get("pending") or {})
    if not ready:
        return None
    claimed = await repo.claim_parked_orchestration(session_id)
    if claimed is None:
        return None  # a concurrent hook won the claim
    state = claimed["orch"]
    task = await repo.get_task(state["task_id"])
    if task is None:
        return None

    if flags["plan_approved"] is not None:
        state["plan_approved"] = flags["plan_approved"]
    for gap in flags["gaps"]:
        await events.emit(
            "orchestration.capability_gap", ref_id=session_id,
            payload={**gap, "project_task_id": state["task_id"]},
            actor="system",
        )
    await events.emit(
        "orchestration.resumed", ref_id=session_id,
        payload={"task_id": state["task_id"], "resolved": len(results)},
        actor="system",
    )
    dtr = DeferredToolResults()
    for call_id, res in results.items():
        dtr.calls[call_id] = res
    state["pending"] = {}

    try:
        agent = await _build_agent(model)
        deps = TriageDeps(session_id=session_id, agent_id=AGENT_ID)
        result = await _run_live(
            agent, None, model=None, deps=deps, agent_id=AGENT_ID,
            session_id=session_id, message_history=load_messages(claimed),
            deferred_tool_results=dtr,
            continuable=True, after="orchestrator", continue_state=state,
        )
        return await _process(task, session_id, result, state, deps, model)
    except RunWindowExhausted as e:
        # BEFORE the catch-all below, which would turn a park the operator can
        # release into a FAILED project.
        return _orch_window_parked(e, state)
    except Exception as e:  # noqa: BLE001 — a resume error must reach the record
        log.exception("orchestration resume failed for %s", session_id)
        return await _fail(state["task_id"], session_id,
                           f"orchestration resume failed: {e}")


async def child_resolved(task: dict) -> None:
    """Fire-and-forget hook body for the task-terminal sites: a task with a
    parent session resolved — try to wake whatever it is waiting on. Errors
    are recorded by the driver itself; this wrapper only guards the
    fire-and-forget seam.

    Three shapes share `AWAITING_AGENTS` and this hook: an orchestrator waiting
    on its assigned children (`orch` in run_state — `maybe_resume`), an asker
    waiting on a consulted specialist's SESSION (`consult_wait` — reached via
    the specialist's own session going terminal, never through this hook), and
    — since Decision 5 (2026-08-22) — a `task.create` proposer waiting on the
    TASK it just created (`task_wait`). Dispatch on the parent's run_state, the
    same way `resume_sweep` does, so a task_wait parent is never silently
    handed to `maybe_resume` (which no-ops for anything without `orch`)."""
    parent = task.get("parent_session_id")
    if not parent:
        return
    try:
        rs = await repo.get_session_run_state(parent)
        if rs and "task_wait" in rs:
            await resume_task_wait(parent)
        else:
            await maybe_resume(parent)
    except Exception:  # noqa: BLE001
        log.exception("orchestration resume hook failed for %s", parent)


def spawn_child_resolved(task: dict) -> None:
    if task.get("parent_session_id"):
        asyncio.create_task(child_resolved(task))


async def park_orphaned_resumes() -> list[str]:
    """Write the park record a KILLED resume never reached, for every session
    old enough to be dead rather than in flight. Returns the session ids.

    Parking is all this does — the AWAITING_RESUME worklist that follows it in
    BOTH sweeps is what drives them, so discovery and recovery still converge in
    one pass. It needs no health gate of its own: a park is a database write,
    and the driver behind it is already gated on the LLM.

    One seam because it has two callers on two cadences, and that difference is
    the 2026-08-19 bug: `resume_sweep` runs at STARTUP, where an orphan is
    younger than `dispatch_stale_claim_seconds` (900s) by construction — the
    restart that orphaned it is the same restart that runs the sweep. Two
    sessions killed 100s before a restart were therefore too young to be seen at
    05:49 and had no second look for as long as the process stayed up, because
    the recurring `retry_sweep` carried every OTHER worklist but this one. An
    orphan is only findable once it is stale, so the sweep that finds it must be
    the RECURRING one.
    """
    from central_command.runtime import resume_park

    orphaned = []
    for row in await repo.sessions_with_orphaned_resume(
        settings.dispatch_stale_claim_seconds
    ):
        await resume_park.park_resume(
            row["id"], row["pending"], "the resume died with its process"
        )
        orphaned.append(row["id"])
    return orphaned


async def resume_sweep() -> dict:
    """Catch-up for parked orchestrations whose wake-up hook was lost (process
    restart between a child resolving and the resume). Run at startup and on
    demand — the hooks are accelerators; this is the guarantee.

    It also drives sessions parked AWAITING_RESUME by a transient provider
    failure (outage equivalence, slice 2): this is already the "after a restart
    or whenever in doubt" lever, so restart recovery covers parked resumes from
    day one. The heartbeat cadence that calls it on its own is slice 5.

    Slice 3 adds the third worklist: proposals parked RETRY_PENDING by a
    transient Executor failure. They are reported separately (`retried` /
    `still_pending`) because the unit is a PROPOSAL, not a session — collapsing
    the two would make a sweep report unreadable the moment both are non-empty.

    Slice 4 adds the fourth: tasks parked ASSIGNED-with-`retry_state` whose run
    died transiently. Only DUE ones are offered (`repo.tasks_awaiting_retry`
    applies the backoff in SQL) — re-running a task the moment it parks would
    burn its whole attempt budget into a dependency that is still down.

    The fifth worklist (2026-08-15) is the one that had no park record at all:
    a resume KILLED mid-flight, whose session still reads AWAITING_HUMAN over an
    already-answered question or already-executed proposal. Nothing owned it —
    the item is no longer OPEN so the Inbox is silent, and every other worklist
    keys off a status a dead process never got to write. It goes first because
    it only WRITES the park record the dead process never reached; the
    AWAITING_RESUME loop below is what drives it, so one sweep both discovers
    and finishes the recovery rather than converging over two.
    """
    resumed, still_parked = [], []
    # FIRST worklist, so an orphan converges in ONE pass: this writes the park
    # record the dead process never reached, and the AWAITING_RESUME loop below
    # is what actually drives it.
    orphaned = await park_orphaned_resumes()
    for sid in await repo.sessions_awaiting_agents():
        # AWAITING_AGENTS carries three shapes — an orchestration waiting on its
        # children, an asker waiting on a specialist it consulted, and (Decision
        # 5, 2026-08-22) a `task.create` proposer waiting on the task it just
        # created. Dispatch on the run state, because `maybe_resume` returns
        # None for anything without `orch` and either wait would otherwise sit
        # in this worklist forever, reported as "still parked" on every sweep.
        rs = await repo.get_session_run_state(sid)
        if rs and "consult_wait" in rs:
            r = await resume_consult_wait(sid)
        elif rs and "task_wait" in rs:
            r = await resume_task_wait(sid)
        else:
            r = await maybe_resume(sid)
        (resumed if r is not None else still_parked).append(sid)
    for sid in await repo.sessions_awaiting_resume():
        r = await resume_parked_session(sid)
        (resumed if r is not None else still_parked).append(sid)

    from central_command.gateway import gateway

    retried, still_pending = [], []
    # Oldest first: a queue that starves its oldest entry is not convergence.
    for prop in reversed(await repo.list_proposals("RETRY_PENDING")):
        r = await gateway.retry_execution(prop["id"])
        (retried if r is not None else still_pending).append(prop["id"])

    tasks_retried, tasks_still_parked = [], []
    for task in await repo.tasks_awaiting_retry(due_only=True):
        r = await retry_parked_task(task["id"])
        (tasks_retried if r is not None else tasks_still_parked).append(task["id"])
    return {"resumed": resumed, "still_parked": still_parked,
            "orphaned": orphaned,
            "retried": retried, "still_pending": still_pending,
            "tasks_retried": tasks_retried,
            "tasks_still_parked": tasks_still_parked}


# --- the gated retry sweep (outage equivalence, slice 5) -----------------------


async def retry_sweep(cache=None) -> dict:
    """Drive every parked unit whose dependency is UP — the cadence half of
    outage equivalence, called by the `retry.sweep` heartbeat action.

    It also carries the orphan worklist (`park_orphaned_resumes`), because a
    resume killed with its process is only FINDABLE once it is stale — later
    than the startup `resume_sweep` can ever see it. Recurring is the only
    cadence on which that worklist converges.

    It adds CADENCE and a HEALTH GATE and nothing else: every unit goes through
    the driver that already owns it (`resume_parked_session`,
    `gateway.retry_execution`, `retry_parked_task`), so a swept retry is
    identical to an operator-triggered one. A second driver here would be a
    second convergence path, and two paths race.

    **A gated skip consumes NO attempt**, by construction: a skip is a `continue`
    before the driver call, so there is no code path on which a down dependency
    can spend a unit's budget. The counterpart matters too — attempts ARE spent
    when the probe said healthy and the call still failed, which is the
    flappiness the budget was written for.

    Oldest-first on every worklist: a queue that starves its oldest entry is not
    convergence.

    **Ledger work items are deliberately NOT swept.** A transiently-released
    email converges through the normal dispatch loop (`work_item.not_before`
    backoff + the mail-poll/drain cadence), and it has no attempt ceiling at
    all. One convergence path per unit kind; sweeping them here would race the
    dispatcher for the same rows.

    This lives in the api tier, not in `heartbeat/`, because it must reach
    `gateway.retry_execution` — the heartbeat package may never import the gate
    (tests/test_heartbeat.py). The action is the trigger; this is the lever, the
    same shape as `task.create` calling `routes.create_and_run_task`.
    """
    from central_command.heartbeat import probes

    cache = cache or probes.ProbeCache()
    resumed: list[str] = []
    retried_executions: list[str] = []
    retried_tasks: list[str] = []
    skipped_gated: list[dict] = []
    errors: list[dict] = []

    async def _drive(unit: str, unit_id: str, dependency, driver, landed: list):
        if not await cache.healthy(dependency):
            skipped_gated.append(
                {"unit": unit, "id": unit_id, "dependency": dependency}
            )
            return
        try:
            out = await driver()
        except Exception as e:  # noqa: BLE001 — one bad unit must not end the sweep
            log.exception("retry sweep: %s %s failed", unit, unit_id)
            errors.append({"unit": unit, "id": unit_id, "error": str(e)[:300],
                           "transient": classify_failure(e) == TRANSIENT})
            return
        if out is not None:  # None = someone else holds the claim, or it moved
            landed.append(unit_id)

    # (a) Sessions parked AWAITING_RESUME — their retry IS an agent run.
    # Orphans are parked FIRST so this same pass drives them: they are only
    # findable once stale, which is later than the startup sweep can ever see.
    orphaned = await park_orphaned_resumes()
    for sid in await repo.sessions_awaiting_resume():
        await _drive("session", sid, probes.LLM,
                     lambda sid=sid: resume_parked_session(sid), resumed)

    # (b) Approved proposals parked RETRY_PENDING — the blocked call is the
    # EXECUTOR's, so the gate is the failed capability's own system. NOT also
    # gated on the LLM: the post-execution resume is agent work, but slice 2
    # parks that leg independently, so double-gating would only delay a write
    # the Executor could land right now.
    from central_command.gateway import gateway

    for prop in reversed(await repo.list_proposals("RETRY_PENDING")):
        row = await repo.load_proposal(prop["id"])
        state = (row or {}).get("retry_state") or {}
        dependency = probes.dependency_for_capability(state.get("failed_capability"))
        await _drive("proposal", prop["id"], dependency,
                     lambda pid=prop["id"]: gateway.retry_execution(pid),
                     retried_executions)

    # (c) Tasks parked for a re-run, DUE ones only (`not_before` is applied in
    # SQL) — a re-run is an agent run, so it gates on the LLM.
    for task in await repo.tasks_awaiting_retry(due_only=True):
        await _drive("task", task["id"], probes.LLM,
                     lambda tid=task["id"]: retry_parked_task(tid), retried_tasks)

    result = {"resumed": resumed, "orphaned": orphaned,
              "retried_executions": retried_executions,
              "retried_tasks": retried_tasks, "skipped_gated": skipped_gated,
              "errors": errors}
    if cache.results:
        result["probes"] = cache.results
    return result


# --- parked decision resumes (outage equivalence, slice 2) ---------------------


async def _promote_exhausted_resume(
    session_id: str, pending: dict, error: str, attempts: int
) -> None:
    """Attempt exhaustion is a PROMOTION, not a loop: the operator learns the
    outage outlived the system's patience. The terminal behaviour itself is the
    caller's (it is path-specific); this adds the item that makes it visible."""
    item_id = "oi_" + uuid.uuid4().hex[:12]
    subject = pending.get("proposal_id") or pending.get("item_id") or "(none)"
    await repo.create_operator_item(
        item_id, "question", session_id, AGENT_ID,
        (f"Session {session_id} could not be resumed after {attempts} attempts "
         f"following the operator's decision on {subject} "
         f"(after={pending.get('after')}). The dependency kept failing: "
         f"{error}. The decision itself stands and is recorded; what was lost "
         "is the agent's closing turn. Answer to acknowledge, or re-run the "
         "resume once the dependency is healthy."),
    )
    await events.emit(
        "session.resume_exhausted", ref_id=session_id,
        payload={"attempts": attempts, "after": pending.get("after"),
                 "proposal_id": pending.get("proposal_id"),
                 "item_id": pending.get("item_id"),
                 "operator_item_id": item_id, "error": error[:500]},
        actor="system",
    )


async def resume_parked_session(session_id: str, model=None) -> dict | None:
    """Drive a session parked AWAITING_RESUME by a transient failure.

    The claim is the SQL flip (`claim_parked_resume`), so two sweeps racing
    produce exactly one resume. The run is rebuilt from the same persisted
    history the original resume would have replayed, handed the same deferred
    result, and then handed to THE SAME post-resume tail the live decision
    paths use (`gateway.finish_decision_resume`, or `questions._settle` after
    an answered question) — never a second copy of that sequence.
    """
    from pydantic_ai import DeferredToolResults, ToolDenied, UsageLimits

    from central_command.contract import classify_failure
    from central_command.runtime import resume_park
    from central_command.runtime.agent import build_agent_for
    from central_command.runtime.durable import load_messages

    session = await repo.get_session(session_id)
    if session is None or session["status"] != "AWAITING_RESUME":
        return None
    claimed = await repo.claim_parked_resume(session_id)
    if claimed is None:
        return None  # a concurrent sweep won the claim
    pending = claimed.get("pending_resume")
    if not pending:
        return None
    agent_id = session["agent_id"]
    after = pending["after"]
    attempts = int(pending.get("attempts", 0)) + 1

    # Resolve per AGENT, like `resume_with_answer`: a retried run must come
    # back as the same agent, on the same routing, that parked.
    from central_command.runtime.models import resolve_model

    model = await resolve_model(model, agent_id=agent_id)

    await events.emit(
        "session.resume_retried", ref_id=session_id,
        payload={"after": after, "attempt": attempts,
                 "proposal_id": pending.get("proposal_id"),
                 "item_id": pending.get("item_id")},
        actor="system",
    )

    results = DeferredToolResults()
    results.calls[pending["tool_call_id"]] = (
        # Same framing the live reject path uses — `pending["text"]` is the
        # verbatim feedback and stays that way in the record.
        ToolDenied(resume_park.framed_denial(pending["text"]))
        if pending["kind"] == "denial"
        else pending["text"]
    )
    final = None
    agent = None
    exhausted = False
    error_text = None
    try:
        agent = await build_agent_for(agent_id, model=model)
        final = await agent.run(
            message_history=load_messages(claimed),
            deferred_tool_results=results,
            model=model,
            # The chain rides through the transient park too: it is in the same
            # run_state the messages are, and a mid-chain specialist that came
            # back here with `chain=()` would bypass the depth/cycle refusals.
            deps=TriageDeps(
                agent_id=agent_id, session_id=session_id,
                consult_chain=_parked_chain(claimed),
            ),
            # The same loop breaker every fresh run gets. This leg drives
            # `agent.run()` itself, so it had NONE until 2026-08-01 — the hole
            # `_run_live`'s docstring claimed did not exist. Plain fail: the
            # decision is recorded and only the closing turn is at stake.
            usage_limits=UsageLimits(request_limit=settings.run_request_limit),
        )
    except Exception as e:  # noqa: BLE001 — the decision record outranks the resume
        transient = classify_failure(e) == "transient"
        error_text = str(e)
        if transient and attempts < settings.resume_retry_limit:
            await resume_park.park_resume(
                session_id, {**pending, "attempts": attempts}, e
            )
            return {"parked": True, "session_id": session_id, "attempts": attempts}
        if transient:
            exhausted = True
            await _promote_exhausted_resume(session_id, pending, str(e), attempts)
        # Semantic (or exhausted): today's terminal handling for this path.
        await events.emit(
            "session.resume_failed", ref_id=session_id,
            payload={"error": str(e), "proposal_id": pending.get("proposal_id"),
                     "item_id": pending.get("item_id"), "after": after,
                     "transient": transient, "attempts": attempts},
            actor="system",
        )

    if after == "answered":
        return await _finish_answered_resume(
            final, agent=agent, model=model, pending=pending,
            session_id=session_id, agent_id=agent_id, exhausted=exhausted,
            error_text=error_text,
        )

    if after == "consulted":
        # A consult wake-up whose own resume leg hit a transient failure. It
        # has no decision tail — nothing was decided about the ASKER, it was
        # only handed its specialist's outcome — so it settles like a fresh
        # run. Without this branch it would fall through to
        # `finish_decision_resume` with `proposal_id=None` and be recorded as
        # a decision that never happened.
        if final is None:
            from central_command.runtime.converse import land_session

            reason = error_text or pending.get("last_error") or "resume failed"
            await land_session(
                session_id, failed=True, agent_id=agent_id, reason=reason,
            )
            return {"resumed": False, "session_id": session_id,
                    "exhausted": exhausted}
        out = await _settle_consult_resume(
            final, agent=agent, model=model, session_id=session_id,
            agent_id=agent_id, task_id=pending.get("task_id"),
            chain=_parked_chain(claimed),
        )
        return {"resumed": True, "session_id": session_id,
                "after": "consulted", **out}

    from central_command.gateway import gateway

    out = await gateway.finish_decision_resume(
        session_id=session_id, agent_id=agent_id,
        proposal_id=pending.get("proposal_id"), after=after, final=final,
        agent=agent, model=model,
        outcome_text=pending["text"] if after == "approved" else None,
        feedback=pending["text"] if after == "rejected" else None,
        decided_event_id=pending.get("decided_event_id"),
        failure_summary=pending.get("failure_summary"),
        # A fresh park keeps the attempt count, so retries stay bounded even
        # when it is the DRIVE step that keeps failing.
        pending=None if exhausted else {**pending, "attempts": attempts},
    )
    return {"resumed": True, "session_id": session_id, "after": after,
            "attempts": attempts, **out}


_TERMINAL_SESSION = ("DONE", "FAILED")


def _consult_outcome_text(
    target: str, prop: dict | None, closed_reason: str | None,
    answer: str | None = None,
) -> str:
    """What the asker is told when its specialist finishes — its specialist's
    own final ANSWER when it left one, otherwise the verdict on what it drafted.

    `answer` is the mid-chain case (chained waits, 2026-08-23): the specialist
    was itself waiting a level deeper, resumed with that decision, and closed
    out in TEXT. That text is its answer to this asker and outranks a verdict
    the asker's specialist did not draft — `_consult_verdict_text` would
    otherwise read "finished without leaving a proposal ... nothing was
    changed", which is precisely the wrong thing to tell someone whose
    specialist just answered them. It is clipped to the same consult ceiling
    every other answer is."""
    from central_command.runtime.consult import ANSWER_CEILING
    from central_command.runtime.tools import _clip

    if answer and prop is None:
        return _clip(answer, ANSWER_CEILING)
    text = _consult_verdict_text(target, prop, closed_reason)
    if answer:
        # Both: the specialist drafted AND closed out in text. Neither half is
        # droppable — the verdict carries the do-not-re-propose guards.
        text += f"\n\n{target} adds: " + _clip(answer, ANSWER_CEILING)
    return text


def _consult_verdict_text(target: str, prop: dict | None, closed_reason: str | None) -> str:
    """What the asker is told when its specialist finishes.

    Three jobs. It reports what ACTUALLY happened (the operator's verdict and,
    on approval, the executed result — the new project key, the issue key), so
    the asker acts on fact instead of on its own guess. It says plainly when
    nothing happened, because "no change was made" is an answer an agent must
    be able to act on rather than read as a hint to try again. And on every
    non-approved verdict it FORBIDS re-proposing: an agent handed a dismissed
    or rejected transcript re-proposes it, which is the loop
    `close_session`'s reflection guard already had to stop (2026-08-13) — the
    same failure by another door.
    """
    if prop is None:
        return (
            f"{target} finished without leaving a proposal on the record"
            + (f" (session closed: {closed_reason})" if closed_reason else "")
            + ". Nothing was changed. Proceed on your own judgment and say "
            "plainly what you could not confirm — do NOT assume the change "
            "was made."
        )
    status = prop.get("status") or "?"
    intent = (prop.get("intent") or "").strip()
    if status == "EXECUTED":
        outcome = ((prop.get("provenance") or {}).get("result_text") or "").strip()
        return (
            f"{target}'s proposal was APPROVED by the operator and EXECUTED: "
            f"{intent}"
            + (f"\n\nResult: {outcome}" if outcome else "")
            + "\n\nThis change is DONE and real — use these values (issue "
            "keys, project keys) directly; do not re-propose it and do not "
            "invent alternatives to them."
        )
    if status in ("WITHDRAWN",):
        return (
            f"{target}'s proposal was DISMISSED by the operator — no action "
            f"needed, and nothing was changed: {intent}\n\nDo NOT propose this "
            "change yourself; the operator has already declined it. Continue "
            "with the rest of your work, and if that work depended on this "
            "change, say so plainly rather than working around it."
        )
    if status == "REJECTED":
        return (
            f"{target}'s proposal was REJECTED and {target} closed out without "
            f"a redraft: {intent}\n\nDo NOT re-propose it yourself. Continue "
            "with the rest of your work and say what you could not do."
        )
    if status == "FAILED":
        return (
            f"{target}'s proposal was approved but its execution FAILED: "
            f"{intent}\n\nThe change did NOT land. Do not assume any of its "
            "values exist, and do not retry it yourself — report it."
        )
    return (
        f"{target} finished; its proposal {prop.get('id')} is {status}: {intent}"
        "\n\nDo not duplicate it. Proceed with what you can do and say what "
        "you could not confirm."
    )


async def resume_consult_wait(session_id: str, model=None) -> dict | None:
    """Resume an asker parked on a consult once its specialist is FINISHED.

    "Finished" is the specialist's SESSION going terminal, not its proposal
    being decided — those differ exactly where it matters. A rejection resumes
    the specialist so it can redraft: its session goes back to AWAITING_HUMAN
    under a NEW proposal id, and the asker must keep waiting. Keying the wait
    on the session (and reading whatever proposal that session ended with) is
    what makes the redraft loop transparent to the asker.

    The claim is the same SQL flip the orchestrator uses, so a hook and a sweep
    racing produce exactly one resume. Returns None when the specialist is not
    finished yet — that is the normal "keep waiting" answer, not an error.
    """
    from pydantic_ai import UsageLimits

    from central_command.runtime import resume_park
    from central_command.runtime.agent import build_agent_for
    from central_command.runtime.durable import load_messages
    from central_command.runtime.models import resolve_model

    session = await repo.get_session(session_id)
    if session is None or session["status"] != "AWAITING_AGENTS":
        return None
    rs = await repo.get_session_run_state(session_id)
    if not rs or "consult_wait" not in rs:
        return None  # an orchestration park — `maybe_resume` owns that shape
    wait = rs["consult_wait"]
    specialist_session = wait.get("specialist_session_id")
    if not specialist_session:
        return None

    spec = await repo.get_session(specialist_session)
    if spec is None or spec["status"] not in _TERMINAL_SESSION:
        return None  # still working (or mid-redraft) — keep waiting

    claimed = await repo.claim_parked_orchestration(session_id)
    if claimed is None:
        return None  # a concurrent hook or sweep won the claim

    agent_id = session["agent_id"]
    target = wait.get("target") or specialist_session
    prop = await repo.latest_proposal_for_session(specialist_session)
    # A specialist that closed out in TEXT left its answer in its own run_state
    # (`_settle_consult_resume` writes it there before landing) — the mid-chain
    # case, where the specialist drafted nothing itself and the verdict alone
    # would read as "nothing happened".
    spec_state = await repo.get_session_run_state(specialist_session) or {}
    outcome_text = _consult_outcome_text(
        target, prop, spec.get("closed_reason"),
        answer=spec_state.get("consult_answer"),
    )
    model = await resolve_model(model, agent_id=agent_id)

    await events.emit(
        "agent.consult_resumed", ref_id=session_id,
        payload={"asker": agent_id, "target": target,
                 "specialist_session_id": specialist_session,
                 "proposal_id": (prop or {}).get("id"),
                 "proposal_status": (prop or {}).get("status")},
        actor="system",
    )

    pending = resume_park.pending_resume(
        kind="result", text=outcome_text,
        tool_call_id=wait["tool_call_id"], after="consulted",
        proposal_id=(prop or {}).get("id"), task_id=wait.get("task_id"),
    )
    try:
        agent = await build_agent_for(agent_id, model=model)
        final = await agent.run(
            message_history=load_messages(claimed),
            deferred_tool_results=_one_result(wait["tool_call_id"], outcome_text),
            model=model,
            deps=TriageDeps(
                agent_id=agent_id, session_id=session_id,
                consult_chain=_parked_chain(claimed),
            ),
            usage_limits=UsageLimits(request_limit=settings.run_request_limit),
        )
    except Exception as e:  # noqa: BLE001
        # Outage equivalence: the specialist's work is already recorded, so a
        # provider that is down costs the asker a DELAY, not its run.
        parked = await resume_park.park_if_transient(session_id, e, pending)
        if parked:
            return {"parked": True, "session_id": session_id}
        from central_command.runtime.converse import land_session

        await land_session(
            session_id, failed=True, agent_id=agent_id,
            reason=f"consult resume failed: {e}",
        )
        return {"resumed": False, "session_id": session_id, "error": str(e)}

    out = await _settle_consult_resume(
        final, agent=agent, model=model, session_id=session_id,
        agent_id=agent_id, task_id=wait.get("task_id"),
        chain=_parked_chain(claimed),
    )
    return {"resumed": True, "session_id": session_id, "target": target, **out}


def _task_wait_outcome_text(t: dict) -> str:
    """What the proposer is told when the task it awaited finishes — the same
    distillation `_child_result` gives an orchestrator, in prose rather than
    JSON since this is a normal tool-result turn, not a batch collection."""
    outcome = (t.get("outcome") or "")[:_OUTCOME_CHARS]
    return (
        f"Task {t['id']} ({t.get('title') or 'untitled'}), assigned to "
        f"{t['agent_id']}, finished with status {t['status']}."
        + (f"\n\nOutcome: {outcome}" if outcome else "")
    )


async def resume_task_wait(session_id: str, model=None) -> dict | None:
    """Resume a `task.create` proposer parked on its own `await_result` task
    (doctrine Decision 5, 2026-08-22) once that task reaches a TERMINAL status.

    Sibling of `resume_consult_wait`: same claim (`claim_parked_orchestration`,
    the AWAITING_AGENTS→RUNNING flip is the lock), same shape of resume (feed
    the distilled outcome in as the deferred call's result and let the run
    continue), same tail (`gateway.finish_decision_resume` with `after=
    "approved"` — the decision was already recorded when the task was
    created; this only supplies the closing turn that decision owed the
    drafter). Returns None when the task has not gone terminal yet — the
    normal "keep waiting" answer, not an error.
    """
    from pydantic_ai import UsageLimits

    from central_command.runtime import resume_park
    from central_command.runtime.agent import build_agent_for
    from central_command.runtime.durable import load_messages
    from central_command.runtime.models import resolve_model

    session = await repo.get_session(session_id)
    if session is None or session["status"] != "AWAITING_AGENTS":
        return None
    rs = await repo.get_session_run_state(session_id)
    if not rs or "task_wait" not in rs:
        return None  # a different AWAITING_AGENTS shape — not this driver's
    wait = rs["task_wait"]
    task = await repo.get_task(wait["task_id"])
    if task is None or task["status"] not in TASK_TERMINAL:
        return None  # still running (or unassigned/queued) — keep waiting

    claimed = await repo.claim_parked_orchestration(session_id)
    if claimed is None:
        return None  # a concurrent hook or sweep won the claim

    agent_id = session["agent_id"]
    outcome_text = _task_wait_outcome_text(task)
    model = await resolve_model(model, agent_id=agent_id)

    await events.emit(
        "agent.task_resumed", ref_id=session_id,
        payload={"agent_id": agent_id, "task_id": task["id"],
                 "task_status": task["status"],
                 "proposal_id": wait.get("proposal_id")},
        actor="system",
    )

    pending = resume_park.pending_resume(
        kind="result", text=outcome_text,
        tool_call_id=wait["tool_call_id"], after="approved",
        proposal_id=wait.get("proposal_id"),
    )
    try:
        agent = await build_agent_for(agent_id, model=model)
        final = await agent.run(
            message_history=load_messages(claimed),
            deferred_tool_results=_one_result(wait["tool_call_id"], outcome_text),
            model=model,
            deps=TriageDeps(agent_id=agent_id, session_id=session_id),
            usage_limits=UsageLimits(request_limit=settings.run_request_limit),
        )
    except Exception as e:  # noqa: BLE001
        # Outage equivalence: the task's own outcome is already recorded, so a
        # provider that is down costs the drafter a DELAY, not its run.
        parked = await resume_park.park_if_transient(session_id, e, pending)
        if parked:
            return {"parked": True, "session_id": session_id}
        from central_command.runtime.converse import land_session

        await land_session(
            session_id, failed=True, agent_id=agent_id,
            reason=f"task-wait resume failed: {e}",
        )
        return {"resumed": False, "session_id": session_id, "error": str(e)}

    from central_command.gateway import gateway

    out = await gateway.finish_decision_resume(
        session_id=session_id, agent_id=agent_id,
        proposal_id=wait.get("proposal_id"), after="approved", final=final,
        agent=agent, model=model, outcome_text=outcome_text, pending=pending,
    )
    return {"resumed": True, "session_id": session_id, "task_id": task["id"], **out}


def _one_result(tool_call_id: str, text: str):
    from pydantic_ai import DeferredToolResults

    results = DeferredToolResults()
    results.calls[tool_call_id] = text
    return results


def _parked_chain(run_state: dict | None) -> tuple[str, ...]:
    """The `consult_chain` a parked run carried, restored for the deps its
    resume rebuilds from scratch.

    Correctness, not bookkeeping: a MID-CHAIN specialist resumed with an empty
    chain passes `refusal_for`'s depth and cycle checks that its real chain
    would have refused, so it could consult back into its own asker or open a
    third level. `park_consult_wait` persists it; this reads it back. Absent on
    every top-level asker (and on every row parked before 2026-08-23), which is
    correctly the empty chain."""
    return tuple((run_state or {}).get("consult_chain") or ())


async def _settle_consult_resume(
    final, *, agent, model, session_id: str, agent_id: str, task_id: str | None,
    chain: tuple[str, ...] = (),
) -> dict:
    """What the woken asker ended on. Every outcome a fresh run can reach, the
    resumed leg can reach too — including consulting AGAIN, which parks another
    wait rather than dead-ending (a second question is not a failure)."""
    from central_command.runtime.consult import ANSWER_CEILING, park_consult_wait
    from central_command.runtime.converse import land_session
    from central_command.runtime.durable import classify_deferred, extract_deferred
    from central_command.runtime.proposals import park_proposal
    from central_command.runtime.questions import park_question
    from central_command.runtime.tools import _clip

    call = extract_deferred(final)
    if call is None:
        # The final text is PERSISTED BEFORE the landing, because landing is
        # what wakes whoever is waiting on this session (chained waits,
        # 2026-08-23) — and for a mid-chain specialist this text IS the answer
        # they are owed. Written even when nobody is waiting: it costs one merge
        # and the alternative is a wake that reads an empty record.
        text = str(final.output)
        await repo.update_session_run_state(
            session_id, {"consult_answer": _clip(text, ANSWER_CEILING)}
        )
        await land_session(session_id, agent_id=agent_id)
        return {"deferred": False, "output": text}

    kind = classify_deferred(call)
    if kind == "proposal":
        parked = await park_proposal(
            session_id=session_id, agent_id=agent_id, result=final, call=call,
        )
        return {"deferred": True, "proposal_id": parked["proposal_id"],
                "intent": parked["proposal"].intent}
    if kind == "ask":
        return await park_question(
            session_id=session_id, agent_id=agent_id, result=final, call=call,
            task_id=task_id,
        )
    if kind == "consult":
        return await park_consult_wait(
            session_id=session_id, agent_id=agent_id, result=final, call=call,
            task_id=task_id, chain=chain,
        )
    await land_session(session_id, agent_id=agent_id)
    return {
        "deferred": False,
        "output": (
            f"(reached for `{getattr(call, 'tool_name', '?')}`, which a "
            "consult resume cannot drive)"
        ),
    }


async def _finish_answered_resume(
    final, *, agent, model, pending: dict, session_id: str, agent_id: str,
    exhausted: bool, error_text: str | None = None,
) -> dict:
    """The `after=answered` tail: `questions._settle` (the one the live path
    uses) plus the task bookkeeping `_resume_task_question` does."""
    from central_command.runtime import questions

    item_id = pending.get("item_id")
    item = await repo.get_operator_item(item_id) if item_id else None
    if final is None or item is None:
        # The retry itself failed terminally — record it against the task the
        # same way the live path does.
        task_id = (item or {}).get("task_id")
        reason = error_text or pending.get("last_error") or "resume failed"
        if task_id:
            await repo.resolve_task(task_id, "FAILED", outcome=reason)
            # The same event the live path writes when a question resume fails,
            # so a retried failure reads identically to a first-try one.
            await events.emit(
                "task.run_failed", ref_id=task_id,
                payload={"error": reason, "item_id": item_id,
                         "transient": classify_failure_text(reason) == "transient",
                         "exhausted": exhausted},
                actor="system",
            )
        from central_command.runtime.converse import land_session

        await land_session(session_id, failed=True, agent_id=agent_id, reason=reason)
        return {"resumed": False, "session_id": session_id,
                "exhausted": exhausted}
    out = await questions._settle(
        final, agent=agent, model=model, item=item, session_id=session_id,
        agent_id=agent_id, may_drive=True,
    )
    await _land_task_question_outcome(item.get("task_id"), out)
    return {"resumed": True, "session_id": session_id, "after": "answered", **out}


# --- retryable task runs (outage equivalence, slice 4) -------------------------
#
# A task whose RUN died because a dependency was DOWN returns to ASSIGNED — its
# pre-run status, same agent, same instructions — instead of going terminal
# FAILED with the operator's ask spent. A fresh run IS the unit-level replay the
# doctrine promises; the dead run's session stays FAILED with its partial
# transcript, because sessions are never rewritten.
#
# The park is ONE seam. Two call sites each writing their own status flip + event
# is exactly how `proposal.created` went missing from two paths (2026-07-25), and
# it is also the only place that decides transient-vs-semantic, so no caller can
# drift into retrying an error the dependency actually answered.
#
# **Orchestrator children and `_fail`.** `_fail` — the orchestrator's own
# child-failure reporting, inside the project loop — is deliberately untouched:
# that loop has a failure protocol the parent reasons about, and retrying
# underneath it would race that protocol. The park at the task-RUN wrapper does
# cover a child's own run (it is the same entry a plain task uses), and that is
# safe for the opposite reason: a parked child simply has not resolved yet, so
# the parent is not told anything at all until it converges — and exhaustion
# still resolves it FAILED and wakes the parent, so nothing waits forever.

RETRY_HINT = "queued for retry (provider outage)"


async def park_task_retry(
    task_id: str,
    exc: BaseException,
    *,
    kind: str = "run",
    attempts: int = 1,
    item_id: str | None = None,
) -> dict | None:
    """Park `task_id` for a re-run if `exc` is TRANSIENT; None means it was not
    parked (a semantic failure, or an already-terminal task) and the caller
    should run its unchanged failure handling.

    `kind` names the entry the ORIGINAL run used, so the driver re-enters
    through that one: `"run"` for a plain assigned run, `"answer"` for the
    resume of a question the operator already answered — re-running the latter
    as a fresh task would throw away the operator's answer.
    """
    if classify_failure(exc) != TRANSIENT:
        return None
    backoff = retry_backoff_seconds(attempts - 1)
    now = datetime.now(timezone.utc)
    state = {
        "kind": kind,
        "attempts": attempts,
        "last_error": str(exc)[:500],
        "parked_at": now.isoformat(),
        "not_before": (now + timedelta(seconds=backoff)).isoformat(),
    }
    if item_id:
        state["item_id"] = item_id
    if not await repo.park_task_retry(
        task_id, state, hint=f"{RETRY_HINT}: {state['last_error']}"
    ):
        return None
    await events.emit(
        "task.run_parked", ref_id=task_id,
        payload={"transient": True, "attempts": attempts, "kind": kind,
                 "error": state["last_error"], "item_id": item_id,
                 "backoff_seconds": backoff,
                 "not_before": state["not_before"]},
        actor="system",
    )
    return state


async def _promote_exhausted_task(task: dict, error: str, attempts: int) -> None:
    """Attempt exhaustion is a PROMOTION, not a loop: after `task_retry_limit`
    the task converts to today's terminal FAILED and the operator learns the
    outage outlived the system's patience, with the instructions still on the
    record to re-issue.

    The operator item needs a session to hang on (`operator_item.session_id` is
    a NOT NULL foreign key). `_run_live` links the run's session to the task at
    run START, so even a run that died on its first turn leaves one — but the
    `task` dict here was read at CLAIM time, before that run, so re-read the row
    for it. When there genuinely is no session (a failure before the session row
    existed) the promotion is still made and still loud (terminal FAILED + this
    event), it simply carries `operator_item_id: null`.
    """
    item_id = None
    session_id = (await repo.get_task(task["id"]) or task).get("session_id")
    if session_id:
        item_id = "oi_" + uuid.uuid4().hex[:12]
        await repo.create_operator_item(
            item_id, "question", session_id, task.get("agent_id") or AGENT_ID,
            (f"Task {task['id']} ({task.get('title') or 'untitled'}) could not "
             f"be run after {attempts} attempts — the dependency kept failing: "
             f"{error}. The instructions are unchanged and still on the task; "
             "re-run it once the dependency is healthy, or answer here to "
             "acknowledge."),
            task_id=task["id"],
        )
    await events.emit(
        "task.run_exhausted", ref_id=task["id"],
        payload={"attempts": attempts, "error": error[:500],
                 "operator_item_id": item_id,
                 "agent_id": task.get("agent_id")},
        actor="system",
    )


async def _retry_answered_question(state: dict) -> dict:
    """Re-enter the entry an ANSWER-parked task used: the question resume,
    carrying the operator's recorded answer (the item row is its one durable
    copy — re-reading it beats a second copy in `retry_state` going stale)."""
    from central_command.runtime.questions import resume_with_answer

    item_id = state.get("item_id")
    item = await repo.get_operator_item(item_id) if item_id else None
    if item is None:
        raise ValueError(f"unknown operator item {item_id!r}")
    out = await resume_with_answer(item_id, item.get("answer") or "")
    if out.get("resume_parked"):
        # The session parked itself AWAITING_RESUME (slice 2) — the sweep owns
        # it from here, and failing the task would turn a delay into a loss.
        return out
    await _land_task_question_outcome(item.get("task_id"), out)
    return out


async def retry_parked_task(task_id: str, model=None) -> dict | None:
    """Re-run a retry-parked task — the convergence half of outage equivalence.

    The claim is the SQL flip (`repo.claim_task_retry`), so two sweeps racing
    run it exactly once; None means someone else holds it (or it is not parked).
    Deliberately does NOT check the backoff: `not_before` is the SWEEP's filter,
    and a caller naming one task is a deliberate act, not a poll loop.
    """
    task = await repo.claim_task_retry(task_id)
    if task is None:
        return None
    state = task.get("retry_state") or {}
    attempts = int(state.get("attempts") or 1)
    kind = state.get("kind") or "run"

    await events.emit(
        "task.run_retried", ref_id=task_id,
        payload={"attempt": attempts, "kind": kind,
                 "agent_id": task.get("agent_id")},
        actor="system",
    )

    try:
        if kind == "answer":
            out = await _retry_answered_question(state)
        else:
            # THE entry the original used — the same wrapper that lands
            # task.blocked / task.review / task.completed and resolves a
            # waiting orchestrator. Re-implementing any of that here is how the
            # retried run would stop being equivalent to a first one.
            #
            # The _inner (unguarded) body, deliberately: the failure landing
            # this `except` performs is attempt-AWARE, and the fresh-run guard
            # in `_run_assigned_task` is not. Letting both run would park with
            # attempts=1 underneath us every round — a retry loop that never
            # reaches the exhaustion promotion.
            from central_command.api.routes import _run_assigned_task_inner

            out = await _run_assigned_task_inner(task, actor="system")
    except Exception as e:  # noqa: BLE001 — a retry failure must reach the record
        parked = await park_task_retry(
            task_id, e, kind=kind, attempts=attempts + 1,
            item_id=state.get("item_id"),
        ) if attempts < settings.task_retry_limit else None
        if parked is not None:
            return {"parked": True, "task_id": task_id,
                    "attempts": attempts + 1}
        transient = classify_failure(e) == TRANSIENT
        if transient:
            await _promote_exhausted_task(task, str(e), attempts)
        await repo.resolve_task(task_id, "FAILED", outcome=f"run failed: {e}")
        await events.emit(
            "task.run_failed", ref_id=task_id,
            payload={"error": str(e), "transient": transient,
                     "exhausted": transient, "attempts": attempts},
            actor=f"agent:{task.get('agent_id')}",
        )
        # A FAILED child still resumes its waiting orchestrator — the failure IS
        # the result the loop needs to decide on (D7 phase 2).
        spawn_child_resolved(task)
        return {"task_id": task_id, "ok": False, "error": str(e),
                "exhausted": transient, "attempts": attempts}

    return {"task_id": task_id, "retried": True, "attempts": attempts, **out}


# --- continuation windows (resumable request limit, slice 1) -------------------
#
# `run_request_limit` is a LOOP BREAKER, and a run that hits it has usually done
# real work — throwing it away is the wrong end of the trade. So a CONTINUABLE
# run parks AWAITING_CONTINUE and asks: continue (another window of the same
# size) or decline (the run ends here, today's behaviour).
#
# Two rules from the field (Cline's two well-known bugs with the same feature):
# read the limit from `settings` at CONTINUATION time, never from the park
# marker, or changing it mid-task does nothing; and always leave a decline path,
# or a hit limit can lock the work out with no way forward.
#
# Slice 1 covered the TASK path (`run.run_task`); slice 2 adds the two remaining
# high-value ones — a conversation turn (`converse._turn`) and an orchestrator
# round (`run_project` / `_reject_batch` / `maybe_resume`). Everything else
# (ingest, coach, routing, steward) still fails plainly, on purpose: those runs
# are unattended and re-runnable, and a window is worth asking about only when
# the work behind it is.


async def _continue_agent(after: str, agent_id: str, model):
    """The agent a continuation re-enters under, and the model to run it with.

    Each surface builds its agent differently and the difference is load-bearing:
    a conversation must NOT get `build_agent_for`'s raw grants, because
    `converse._conversational_packs` is what stops the orchestrator being handed
    chat tools nothing can drive; the orchestrator project loop has its own
    builder that asserts the `orchestrate` grant. Getting this wrong is silent —
    the run continues with the wrong tool surface.
    """
    from central_command.runtime import converse as converse_mod
    from central_command.runtime.agent import build_agent_for, load_charter
    from central_command.runtime.models import resolve_model

    if after == "orchestrator":
        # `_build_agent` resolves the project model itself, so the run passes
        # model=None — exactly as `run_project` and `_reject_batch` do.
        return await _build_agent(model), None
    resolved = await resolve_model(model, agent_id=agent_id)
    if after == "converse":
        agent = await converse_mod._build(
            agent_id, resolved, await load_charter(agent_id)
        )
        return agent, resolved
    return await build_agent_for(agent_id, model=resolved), resolved


async def continue_session(item_id: str, model=None) -> dict | None:
    """Grant a window-parked run another request window, and land it normally.

    The claim is the SQL flip (`repo.claim_parked_continue`), so a continue
    racing a decline produces exactly one outcome. Re-entry is
    `agent.iter(None, message_history=…)`: the persisted history ends with the
    unsent `ModelRequest` pydantic-ai appended before the limit tripped, and
    `UserPromptNode` pops it and re-sends its parts — no duplicated tool
    returns, no synthesised prompt. The usage counter starts at zero on every
    run, so the fresh window costs no extra code.

    The marker's `after` picks the LANDING TAIL — `run._land`, `converse.
    _land_turn` or `_process` — so a continued run lands exactly as an
    uninterrupted one, through the same code. Never a second copy of a landing
    sequence; that is why each of those three exists as a function at all.
    """
    from central_command.runtime import converse as converse_mod
    from central_command.runtime import run as run_mod
    from central_command.runtime.durable import load_messages

    item = await repo.get_operator_item(item_id)
    if item is None:
        return None
    session_id = item["session_id"]
    claimed = await repo.claim_parked_continue(session_id)
    if claimed is None:
        return None  # not parked, or someone else won the claim
    marker = claimed.get("pending_continue") or {}
    after = marker.get("after") or "task"
    state = marker.get("state") or {}
    agent_id = item["agent_id"]
    task_id = marker.get("task_id")
    window = int(marker.get("windows") or 1) + 1

    await events.emit(
        "session.continued", ref_id=session_id,
        payload={"item_id": item_id, "agent_id": agent_id, "window": window,
                 "requests": settings.run_request_limit, "task_id": task_id,
                 "after": after},
        actor="operator",
    )

    agent, run_model = await _continue_agent(after, agent_id, model)
    deps = TriageDeps(agent_id=agent_id, session_id=session_id)
    try:
        result = await run_mod._run_live(
            agent, None, model=run_model, deps=deps,
            agent_id=agent_id, session_id=session_id,
            message_history=load_messages(claimed),
            # The orchestrator's task row is already IN_PROGRESS from round 1
            # and its id travels in the loop state — re-linking it here would
            # differ from what `maybe_resume` does on the same session.
            task_id=None if after == "orchestrator" else task_id,
            continuable=True, window=window, after=after, continue_state=state,
        )
    except run_mod.RunStopped:
        # The operator stopped the continued run. Already parked STOPPED by
        # `_run_live`; nothing here should re-park or fail it.
        return {"window": window, "stopped": True, "session_id": session_id}
    except run_mod.RunWindowExhausted as e:
        # It spent this window too. Same park, one window higher — repeatable
        # by design, because every window costs an explicit human click.
        if after == "orchestrator":
            return {"window": window, **_orch_window_parked(e, state)}
        out = run_mod.window_outcome(e)
        if after == "task" and task_id:
            # Back to REVIEW: `_run_live` put the task IN_PROGRESS when it
            # re-entered, and it is waiting on the operator again.
            await repo.park_task_review(task_id, session_id)
            await _land_task_question_outcome(task_id, out)
        return {"window": window, **out}

    if after == "orchestrator":
        task = await repo.get_task(state["task_id"])
        if task is None:  # the project was deleted under the park
            return await _fail(state["task_id"], session_id,
                               "the project task vanished while parked")
        out = await _process(task, session_id, result, state, deps, model)
        return {"continued": True, "window": window, **out}
    if after == "converse":
        out = await converse_mod._land_turn(session_id, agent_id, result)
        return {"continued": True, "window": window, **out}

    out = await run_mod._land(
        result, session_id=session_id, agent_id=agent_id, task_id=task_id,
        tokens=run_mod._total_tokens(result),
    )
    # The shared task-row landing, so a continued run lands exactly as an
    # uninterrupted one would.
    await _land_task_question_outcome(task_id, out)
    return {"continued": True, "session_id": session_id, "window": window, **out}


# --- operator stop / resume ----------------------------------------------------
#
# The stop is COOPERATIVE and cheap: a flag the running loop reads at each node
# boundary (`repo.request_session_stop` → `_run_live` → `resume_park.park_stopped`).
# The resume below is `continue_session` with the window language removed — same
# claim-in-SQL, same re-entry on the persisted history, same `after` dispatch
# into the same three landing tails, because a stopped run has to land exactly
# as an uninterrupted one would. What it is NOT is a sweep: no background driver
# looks for STOPPED sessions, ever. Stopped stays stopped until a human says
# otherwise — that is the whole point of the button.


async def request_stop(session_id: str) -> bool:
    """Ask a RUNNING session to stop at its next node boundary.

    Returns immediately — the turn is still finishing the node it is on, and
    the park's own `session.stopped` event is what closes the loop for the
    cockpit. False means there was nothing running to stop.
    """
    if not await repo.request_session_stop(session_id):
        return False
    await events.emit(
        "session.stop_requested", ref_id=session_id, payload={}, actor="operator",
    )
    return True


async def resume_stopped_session(session_id: str, model=None) -> dict | None:
    """Resume a session the operator stopped, and land it normally.

    Mirrors `continue_session`: the claim is the SQL flip
    (`repo.claim_stopped_session`), so a resume racing a cancel produces exactly
    one outcome, and re-entry is `agent.iter(None, message_history=…)` over the
    persisted history — whose tail is the unsent `ModelRequest` carrying the
    last batch's tool returns, so nothing is re-executed. None means the session
    is not stopped (or someone else won the claim).
    """
    from central_command.runtime import converse as converse_mod
    from central_command.runtime import run as run_mod
    from central_command.runtime.durable import load_messages

    session = await repo.get_session(session_id)
    if session is None:
        return None
    claimed = await repo.claim_stopped_session(session_id)
    if claimed is None:
        return None
    marker = claimed.get("pending_stop") or {}
    after = marker.get("after") or "task"
    state = marker.get("state") or {}
    agent_id = session["agent_id"]
    task_id = marker.get("task_id")

    await events.emit(
        "session.resumed", ref_id=session_id,
        payload={"agent_id": agent_id, "after": after, "task_id": task_id},
        actor="operator",
    )

    agent, run_model = await _continue_agent(after, agent_id, model)
    deps = TriageDeps(agent_id=agent_id, session_id=session_id)
    try:
        result = await run_mod._run_live(
            agent, None, model=run_model, deps=deps,
            agent_id=agent_id, session_id=session_id,
            message_history=load_messages(claimed),
            task_id=None if after == "orchestrator" else task_id,
            continuable=True, window=int(marker.get("windows") or 1),
            after=after, continue_state=state,
        )
    except run_mod.RunStopped:
        # Stopped again at the first boundary — the accepted race documented on
        # `session.stop_requested`, or simply a second press. Parked already.
        return {"resumed": True, "stopped": True, "session_id": session_id}
    except run_mod.RunWindowExhausted as e:
        if after == "orchestrator":
            return _orch_window_parked(e, state)
        out = run_mod.window_outcome(e)
        if after == "task" and task_id:
            await repo.park_task_review(task_id, session_id)
            await _land_task_question_outcome(task_id, out)
        return out

    if after == "orchestrator":
        task = await repo.get_task(state["task_id"])
        if task is None:
            return await _fail(state["task_id"], session_id,
                               "the project task vanished while stopped")
        return {"resumed": True, **await _process(task, session_id, result, state,
                                                  deps, model)}
    if after == "converse":
        return {"resumed": True,
                **await converse_mod._land_turn(session_id, agent_id, result)}

    out = await run_mod._land(
        result, session_id=session_id, agent_id=agent_id, task_id=task_id,
        tokens=run_mod._total_tokens(result),
    )
    await _land_task_question_outcome(task_id, out)
    return {"resumed": True, "session_id": session_id, **out}


async def _decline_continue(item_id: str) -> dict | None:
    """The operator declined another window: the run ends here — the terminal
    outcome a non-continuable path would have produced at the same moment. The
    transcript is kept, as with every FAILED session."""
    item = await repo.get_operator_item(item_id)
    if item is None:
        return None
    session_id = item["session_id"]
    claimed = await repo.claim_parked_continue(session_id)
    if claimed is None:
        return None
    marker = claimed.get("pending_continue") or {}
    task_id = marker.get("task_id")
    from central_command.runtime.converse import land_session

    await land_session(session_id, failed=True, agent_id=item["agent_id"],
                       reason="the operator declined another continue window")
    await events.emit(
        "session.window_declined", ref_id=session_id,
        payload={"item_id": item_id, "agent_id": item["agent_id"],
                 "windows": marker.get("windows"), "task_id": task_id},
        actor="operator",
    )
    reason = "the operator declined another request window"
    if task_id:
        await repo.resolve_task(task_id, "FAILED", outcome=reason)
        await events.emit(
            "task.run_failed", ref_id=task_id,
            payload={"error": reason, "item_id": item_id, "transient": False},
            actor="operator",
        )
    return {"declined": True, "session_id": session_id}


# --- operator items API core ---------------------------------------------------


async def answer_item(item_id: str, answer: str, verdict: str | None = None) -> dict:
    """Record the operator's answer to a plan review / question / stall and
    resume the parked loop in the background. The answer is trusted operator
    input; it goes on the event log BEFORE the resume it authorises, so the
    resumed run's guidance is citable (event:<id>)."""
    item = await repo.get_operator_item(item_id)
    if item is None:
        raise LookupError(f"operator item {item_id!r} not found")
    if item["status"] != "OPEN":
        raise ValueError(f"operator item {item_id!r} is already answered")
    if item["kind"] == "plan_review":
        if verdict not in ("approved", "revise"):
            raise ValueError("plan review requires verdict 'approved' or 'revise'")
        if verdict == "revise" and not answer.strip():
            raise ValueError("a revision requires a note — the orchestrator "
                             "replans from it")
    elif item["kind"] == "continue":
        # A window grant is a yes/no, not an answer: the run has no question to
        # answer, it ran out of rope. A note is welcome and optional.
        if verdict not in ("approved", "declined"):
            raise ValueError(
                "a continuation requires verdict 'approved' or 'declined'"
            )
    else:
        verdict = None
        if not answer.strip():
            raise ValueError("an answer is required")
    ok = await repo.answer_operator_item(item_id, answer.strip(), verdict)
    if not ok:
        raise ValueError(f"operator item {item_id!r} is already answered")
    event = await events.emit(
        "orchestration.item_answered", ref_id=item_id,
        payload={"kind": item["kind"], "session_id": item["session_id"],
                 "task_id": item.get("task_id"), "verdict": verdict,
                 "answer": answer.strip()[:2000]},
        actor="operator",
    )
    # A question from a PLAIN assigned task resumes that task's own run, not an
    # orchestration loop — `child_resolved` would find no project to advance.
    # Both are "the operator answered, carry on"; which loop carries on depends
    # on which kind of run parked the item.
    # A tier-3 item carries a clarification LANE, and it was just answered
    # somewhere else — leaving the lane AWAITING_OPERATOR is an undead row whose
    # question already has an answer. Contained: the answer is recorded and the
    # resume must still be scheduled even if the lane close fails.
    if item.get("discussion_session_id"):
        try:
            await _close_answered_discussion(item, answer.strip())
        except Exception:  # noqa: BLE001 — the answer record outranks the close
            log.exception(
                "closing the discussion lane of %s failed", item_id
            )
    if item["kind"] == "continue":
        # The verdict is already recorded above — emit before the run it
        # authorises, like every other decision here.
        asyncio.create_task(
            continue_session(item_id) if verdict == "approved"
            else _decline_continue(item_id)
        )
    elif item["kind"] == "question" and not await _belongs_to_a_project(item):
        asyncio.create_task(_resume_task_question(item_id, answer.strip()))
    else:
        asyncio.create_task(child_resolved({"parent_session_id": item["session_id"]}))
    return {"item_id": item_id, "kind": item["kind"], "verdict": verdict,
            "event_id": event["id"]}


async def _close_answered_discussion(item: dict, answer: str) -> None:
    """The operator answered a tier-3 item from the Inbox: close its lane.

    The answer lands in the transcript first — the mirror of `_open_discussion`
    seeding the question — because a lane that goes terminal without showing WHY
    reads as truncated. It is trusted operator input, so it lands verbatim as an
    operator turn.

    Closed through `converse.close_session`, the seam both close paths share, so
    the cockpit gets the `conversation.ended` frame its session panel refreshes
    on (never `mark_session_done`, which is the 2026-07-23 stale-row bug).
    `end_conversation` is the wrong door: its guards exist for the operator
    racing a live turn, and refuse a lane holding a pending proposal — this
    close is the control plane retiring a lane whose question is answered.

    `reason='concluded'`, not a new value: the lane's question is answered and
    its task resumes, which is exactly what the cockpit renders for 'concluded'
    (divider text plus the `discussion_concluded` read-only lock). 'operator'
    would leave the lane WRITABLE, i.e. reopenable — the undead row again.
    """
    from pydantic_ai.messages import (
        ModelMessagesTypeAdapter, ModelRequest, UserPromptPart,
    )

    from central_command.runtime import converse

    session_id = item["discussion_session_id"]
    session = await repo.get_session(session_id)
    if session is None or session["status"] != "AWAITING_OPERATOR":
        # RUNNING (a turn owns the session and will write its own status) or
        # already terminal. Skipping is the safe half of both cases; never race
        # a live turn, never re-close.
        return

    run_state = await repo.get_session_run_state(session_id) or {}
    messages = list(run_state.get("messages") or [])
    messages += ModelMessagesTypeAdapter.dump_python(
        [ModelRequest(parts=[UserPromptPart(content=answer)])], mode="json"
    )
    await repo.update_session_run_state(session_id, {**run_state, "messages": messages})
    await converse.close_session(
        session_id, agent_id=session["agent_id"], reason="concluded",
        actor="operator",
    )


async def _belongs_to_a_project(item: dict) -> bool:
    """True when the parked session is an orchestrator's project loop."""
    session = await repo.get_session(item["session_id"])
    return bool(session and session["agent_id"] == AGENT_ID)


async def _resume_task_question(item_id: str, answer: str) -> None:
    """Resume an assigned task that was blocked on a question, and land its
    outcome on the task row the same way a first run would."""
    from central_command.runtime.questions import resume_with_answer

    from central_command.contract import is_transient

    item = await repo.get_operator_item(item_id)
    task_id = (item or {}).get("task_id")
    try:
        out = await resume_with_answer(item_id, answer)
    except Exception as e:  # noqa: BLE001 — a resume failure must be visible
        # The RESUME's own transient failure is parked inside
        # `resume_with_answer` (slice 2). What reaches here is everything
        # AROUND it — building the agent, the settle turn, the bookkeeping —
        # and a transient one of those is still an outage, not the task's
        # fault: the task queues for a re-entry through this same path, with
        # the operator's answer re-read from the item row.
        if task_id and await park_task_retry(
            task_id, e, kind="answer", item_id=item_id
        ):
            return
        await events.emit(
            "task.run_failed", ref_id=task_id or item_id,
            payload={"error": str(e), "item_id": item_id,
                     "transient": is_transient(e)},
            actor="system",
        )
        if task_id:
            await repo.resolve_task(task_id, "FAILED", outcome=str(e))
        return
    if out.get("resume_parked"):
        # The provider was down: `resume_with_answer` parked the session
        # AWAITING_RESUME and the sweep will drive it. The task keeps its
        # current status — failing it here would turn a delay into a loss.
        return
    await _land_task_question_outcome(task_id, out)


async def _land_task_question_outcome(task_id: str | None, out: dict) -> None:
    """What a resumed question-run's outcome means for its task row — shared by
    the live path and the parked-resume driver, so a retried run lands its
    result exactly as a no-outage run would have."""
    if not task_id:
        return
    if out.get("asked"):
        await events.emit(
            "task.blocked", ref_id=task_id,
            payload={"item_id": out["item_id"], "session_id": out["session_id"],
                     "question": out["question"][:500]},
            actor="system",
        )
        return
    if out.get("deferred"):
        await repo.park_task_review(task_id, out["session_id"])
        await events.emit(
            "task.review", ref_id=task_id,
            payload={"proposal_id": out["proposal_id"],
                     "session_id": out["session_id"]},
            actor="system",
        )
        return
    await repo.resolve_task(task_id, "DONE", outcome=str(out.get("output", "")))
    await events.emit(
        "task.completed", ref_id=task_id,
        payload={"outcome": str(out.get("output", ""))[:500]}, actor="system",
    )


async def graph_verify_sweep(settle_minutes: int = 10, missing_after_minutes: int = 360) -> dict:
    """Audit approved graph episodes once ingestion settles — called by the
    `graph.verify_sweep` heartbeat action. A passthrough for the same reason
    `retry_sweep` lives here: the body needs the gateway tier (judging a
    verification is part of the gate) and the heartbeat package may never
    import the gate."""
    from central_command.gateway import graph_auditor

    return await graph_auditor.verify_sweep(
        settle_minutes=settle_minutes, missing_after_minutes=missing_after_minutes
    )
