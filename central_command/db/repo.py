"""Minimal async persistence for the spine (asyncpg). Enough for the M2 spike;
grows into a proper repository with a connection pool later.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime

import asyncpg

from central_command.config import settings


async def _conn() -> asyncpg.Connection:
    return await asyncpg.connect(settings.database_url)


async def upsert_agent(agent_id: str, name: str, model: str, charter: str) -> None:
    conn = await _conn()
    try:
        await conn.execute(
            """
            insert into agent (id, name, model, charter) values ($1, $2, $3, $4)
            on conflict (id) do nothing
            """,
            agent_id, name, model, charter,
        )
    finally:
        await conn.close()


async def save_paused_session(session_id: str, agent_id: str, run_state: dict) -> None:
    """MERGE, not replace — see `update_session_run_state`."""
    conn = await _conn()
    try:
        await conn.execute(
            """
            insert into session (id, agent_id, status, run_state)
            values ($1, $2, 'AWAITING_HUMAN', $3::jsonb)
            on conflict (id) do update
              set status = 'AWAITING_HUMAN',
                  run_state = coalesce(session.run_state, '{}'::jsonb) || $3::jsonb,
                  updated_at = now()
            """,
            session_id, agent_id, json.dumps(run_state),
        )
    finally:
        await conn.close()


async def load_paused_session(session_id: str) -> dict | None:
    """Run state for a session that is actually paused. Completed sessions keep
    their run_state for the Workspace transcript (M15), but are not resumable —
    hence the status filter, which preserves this function's old semantics."""
    conn = await _conn()
    try:
        row = await conn.fetchrow(
            "select run_state from session where id = $1 and status = 'AWAITING_HUMAN'",
            session_id,
        )
        if row is None or row["run_state"] is None:
            return None
        rs = row["run_state"]
        return json.loads(rs) if isinstance(rs, str) else rs
    finally:
        await conn.close()


async def get_session_run_state(session_id: str) -> dict | None:
    """Run state regardless of status — the Workspace transcript source."""
    conn = await _conn()
    try:
        row = await conn.fetchrow(
            "select run_state from session where id = $1", session_id
        )
        if row is None or row["run_state"] is None:
            return None
        rs = row["run_state"]
        return json.loads(rs) if isinstance(rs, str) else rs
    finally:
        await conn.close()


async def session_status(session_id: str) -> str | None:
    conn = await _conn()
    try:
        row = await conn.fetchrow("select status from session where id = $1", session_id)
        return row["status"] if row else None
    finally:
        await conn.close()


async def mark_session_done(session_id: str) -> None:
    """Complete a session. The run history is KEPT (M15) — it is the episodic
    record the Agent Workspace renders; only the resumability ends."""
    conn = await _conn()
    try:
        await conn.execute(
            "update session set status = 'DONE', updated_at = now() where id = $1",
            session_id,
        )
    finally:
        await conn.close()


async def mark_session_closed(session_id: str, reason: str) -> None:
    """Close a conversation and RECORD WHY ('operator' | 'concluded').

    Same status flip as `mark_session_done` — sessions are never destroyed —
    plus the one column that three consumers read: the lock rule in
    `why_not_sendable`, the cockpit's terminal divider, and the
    `conversation.ended` payload. Written through `converse.close_session`,
    which is the only caller."""
    conn = await _conn()
    try:
        await conn.execute(
            "update session set status = 'DONE', closed_reason = $2, "
            "updated_at = now() where id = $1",
            session_id, reason,
        )
    finally:
        await conn.close()


async def create_running_session(
    session_id: str, agent_id: str, parent_session_id: str | None = None
) -> None:
    """A session row exists from the moment the run STARTS (live transcript):
    the Workspace can watch the transcript grow instead of only reading it
    after the pause. `save_paused_session`'s upsert flips it to AWAITING_HUMAN
    at deferral; `mark_session_done`/`mark_session_failed` close it out.
    `parent_session_id` (D7 phase 2) links a child run to the orchestrator
    session that assigned it — the cockpit's spawnedBy tree."""
    conn = await _conn()
    try:
        await conn.execute(
            """
            insert into session (id, agent_id, status, parent_session_id)
            values ($1, $2, 'RUNNING', $3)
            on conflict (id) do nothing
            """,
            session_id, agent_id, parent_session_id,
        )
    finally:
        await conn.close()


async def update_session_run_state(session_id: str, run_state: dict) -> bool:
    """Persist the transcript-so-far of a live run (status untouched).

    Returns the session's `stop_requested` flag, which is why the UPDATE has a
    `returning` at all: `_run_live` snapshots at every node boundary, so the
    operator's stop is read on a query the run was already paying for rather
    than on a second one per node. False when the row is gone (nothing to stop).

    MERGE (`||`), not replace, like the two park writers below. `run_state` is a
    shared jsonb bag: the writers here mention `messages` (sometimes plus
    `tool_call_id` / `orch`), while `pending_resume`, `pending_continue` and —
    from the context split — `archived` are written by OTHER paths. A
    whole-object write drops whatever the writer happened not to mention, which
    for `archived` would mean silently destroying the archived half of a
    transcript. The merge is at the KEY level, so a writer that means to replace
    `messages` still replaces it wholesale.

    Nothing relied on the old overwrite to CLEAR a key: the pending markers are
    consumed by a status flip (`claim_parked_resume` / `claim_parked_continue`),
    not by their own absence, and `test_the_driver_finishes_a_parked_approved_
    resume` asserts `pending_resume` outlives the retry as the record of the
    outage."""
    conn = await _conn()
    try:
        row = await conn.fetchrow(
            "update session set "
            "run_state = coalesce(run_state, '{}'::jsonb) || $2::jsonb, "
            "updated_at = now() where id = $1 returning stop_requested",
            session_id, json.dumps(run_state),
        )
        return bool(row and row["stop_requested"])
    finally:
        await conn.close()


# --- orchestration parks (D7 phase 2) -----------------------------------------


async def park_session_awaiting_agents(session_id: str, run_state: dict) -> None:
    """Park an orchestrator run that is waiting on child tasks and/or operator
    items — the same durable-pause shape as AWAITING_HUMAN (messages + pending
    deferred calls persisted; a fresh process can resume), distinct status so
    every surface tells the truth about WHAT the session waits for."""
    conn = await _conn()
    try:
        await conn.execute(
            """
            update session set status = 'AWAITING_AGENTS',
                   run_state = coalesce(run_state, '{}'::jsonb) || $2::jsonb,
                   updated_at = now()
             where id = $1
            """,
            session_id, json.dumps(run_state),
        )
    finally:
        await conn.close()


async def claim_parked_orchestration(session_id: str) -> dict | None:
    """Atomically claim a parked orchestration for resume: flips
    AWAITING_AGENTS → RUNNING and returns the run state, or None if the session
    is not parked (someone else already claimed it — two children resolving at
    once must produce ONE resume). The flip is the lock; it lives in SQL like
    every other claim in this codebase."""
    conn = await _conn()
    try:
        row = await conn.fetchrow(
            """
            update session set status = 'RUNNING', updated_at = now()
             where id = $1 and status = 'AWAITING_AGENTS'
            returning run_state
            """,
            session_id,
        )
        if row is None or row["run_state"] is None:
            return None
        rs = row["run_state"]
        return json.loads(rs) if isinstance(rs, str) else rs
    finally:
        await conn.close()


async def sessions_awaiting_agents() -> list[str]:
    """Parked sessions waiting on other agents — the resume sweep's worklist.

    Two shapes share this status and this worklist, distinguished by run_state:
    orchestrations (`run_state->'orch'`) and consult waits
    (`run_state->'consult_wait'`). Both mean the same thing to every surface
    that reads a status, which is exactly why they share one."""
    conn = await _conn()
    try:
        rows = await conn.fetch(
            "select id from session where status = 'AWAITING_AGENTS' order by updated_at"
        )
        return [r["id"] for r in rows]
    finally:
        await conn.close()


async def sessions_waiting_on(specialist_session_id: str) -> list[str]:
    """Askers parked on a consult with THIS specialist session — the wake-up
    hook's worklist.

    Keyed on the specialist's SESSION rather than its proposal because a
    rejection makes the specialist redraft under a new proposal id; the session
    is what stays put until it is genuinely finished."""
    conn = await _conn()
    try:
        rows = await conn.fetch(
            """
            select id from session
             where status = 'AWAITING_AGENTS'
               and run_state -> 'consult_wait' ->> 'specialist_session_id' = $1
             order by updated_at
            """,
            specialist_session_id,
        )
        return [r["id"] for r in rows]
    finally:
        await conn.close()


# --- retryable resume parks (outage equivalence, slice 2) ---------------------
#
# No DDL: `session.status` is unconstrained text and the pending deferred result
# rides inside the existing `run_state` jsonb, next to the messages the resume
# replays. A transient provider failure on a decision resume must cost latency,
# never the agent's final turn.


async def park_session_awaiting_resume(session_id: str, pending: dict) -> None:
    """Park a session whose RESUME leg failed transiently: status
    AWAITING_RESUME plus `run_state.pending_resume` describing the deferred
    result still owed to the run. The messages already in `run_state` are the
    same history the original resume would have replayed, so the merge (`||`)
    matters — a whole-object write would drop the transcript."""
    conn = await _conn()
    try:
        await conn.execute(
            """
            update session
               set status = 'AWAITING_RESUME',
                   run_state = coalesce(run_state, '{}'::jsonb)
                               || jsonb_build_object('pending_resume', $2::jsonb),
                   updated_at = now()
             where id = $1
            """,
            session_id, json.dumps(pending),
        )
    finally:
        await conn.close()


async def claim_parked_resume(session_id: str) -> dict | None:
    """Atomically claim a parked resume: AWAITING_RESUME → RUNNING, returning
    the run state, or None if it is not parked (someone else claimed it). The
    flip IS the lock — the same SQL idiom as `claim_parked_orchestration`, so
    two sweeps racing produce exactly one resume."""
    conn = await _conn()
    try:
        row = await conn.fetchrow(
            """
            update session set status = 'RUNNING', updated_at = now()
             where id = $1 and status = 'AWAITING_RESUME'
            returning run_state
            """,
            session_id,
        )
        if row is None or row["run_state"] is None:
            return None
        # `pending_resume` is deliberately NOT cleared here: it is the record
        # that an outage happened, and `test_the_driver_finishes_a_parked_
        # approved_resume` asserts it survives a successful retry. The status
        # flip is what stops it being re-claimed.
        rs = row["run_state"]
        return json.loads(rs) if isinstance(rs, str) else rs
    finally:
        await conn.close()


# --- continuation parks (resumable request window) ----------------------------
#
# Same no-DDL shape as the retryable resume park above, different meaning: the
# run hit its `run_request_limit` LOOP BREAKER and is waiting on a human to
# grant another window. Deliberately NOT `AWAITING_RESUME`, which the sweep
# auto-retries — auto-driving a limit-parked run is the loop-forever behaviour
# the limit exists to stop. Nothing is owed to the run here; it simply stopped
# between turns, and the persisted history ends with the unsent ModelRequest
# that pydantic-ai will pop and re-send on re-entry.


async def park_session_awaiting_continue(session_id: str, marker: dict) -> None:
    """Park a session that exhausted its request window: status
    AWAITING_CONTINUE plus `run_state.pending_continue`. The merge (`||`)
    matters for the same reason as the resume park — a whole-object write would
    drop the transcript, which here IS the thing being continued."""
    conn = await _conn()
    try:
        await conn.execute(
            """
            update session
               set status = 'AWAITING_CONTINUE',
                   run_state = coalesce(run_state, '{}'::jsonb)
                               || jsonb_build_object('pending_continue', $2::jsonb),
                   updated_at = now()
             where id = $1
            """,
            session_id, json.dumps(marker),
        )
    finally:
        await conn.close()


async def claim_parked_continue(session_id: str) -> dict | None:
    """Atomically claim a window-parked session: AWAITING_CONTINUE → RUNNING,
    returning the run state, or None if it is not parked. The flip IS the lock,
    so a continue racing a decline produces exactly one outcome."""
    conn = await _conn()
    try:
        row = await conn.fetchrow(
            """
            update session set status = 'RUNNING', updated_at = now()
             where id = $1 and status = 'AWAITING_CONTINUE'
            returning run_state
            """,
            session_id,
        )
        if row is None or row["run_state"] is None:
            return None
        # Kept, like `pending_resume` — the record of which windows were granted.
        # Before the `||` merge a later whole-object snapshot happened to erase
        # it; that was a side effect of the write shape, never a decision, and
        # the parallel resume park keeps its marker on purpose. Re-claiming is
        # blocked by the status flip, not by the marker's absence.
        rs = row["run_state"]
        return json.loads(rs) if isinstance(rs, str) else rs
    finally:
        await conn.close()


# --- operator stop (2026-08-02) -----------------------------------------------
#
# Same shape again — a marker in `run_state`, a status flip, a SQL claim — and
# deliberately its OWN status: no sweep looks for STOPPED, because a run the
# operator stopped must stay stopped until the operator resumes it. The one
# difference from a continuation park is that nobody is asked anything: the
# operator initiated this, so there is no operator_item to nag them with.


async def request_session_stop(session_id: str) -> bool:
    """Ask a RUNNING session to stop at its next node boundary.

    RUNNING-only, in SQL: every parked status is cancel-for-good territory, and
    setting the flag on one would arm a trap that fires on its next resume.
    Returns False when the session is missing or not running.

    Deliberately does NOT touch `updated_at`: that column is the liveness
    signal `fail_stale_running_sessions` sweeps on, refreshed by the run's own
    snapshots. An operator clicking stop is not evidence the run is alive —
    on 2026-08-17, 17 stop clicks on an already-dead session each reset the
    staleness clock, hiding the zombie from the very sweep that lands it."""
    conn = await _conn()
    try:
        r = await conn.execute(
            "update session set stop_requested = true "
            "where id = $1 and status = 'RUNNING'",
            session_id,
        )
        return r.endswith("1")
    finally:
        await conn.close()


async def park_session_stopped(session_id: str, marker: dict) -> None:
    """Park a run the operator stopped: status STOPPED, `run_state.pending_stop`
    describing how to re-enter, and the request flag CLEARED (it has been
    honoured — leaving it set would stop the resume at its first boundary). The
    merge (`||`) matters exactly as it does for the other two parks."""
    conn = await _conn()
    try:
        await conn.execute(
            """
            update session
               set status = 'STOPPED',
                   stop_requested = false,
                   run_state = coalesce(run_state, '{}'::jsonb)
                               || jsonb_build_object('pending_stop', $2::jsonb),
                   updated_at = now()
             where id = $1
            """,
            session_id, json.dumps(marker),
        )
    finally:
        await conn.close()


async def claim_stopped_session(session_id: str) -> dict | None:
    """Atomically claim a stopped session for resume: STOPPED → RUNNING,
    returning the run state, or None if it is not stopped (someone else won).
    The flip IS the lock — the same idiom as `claim_parked_continue`, so a
    resume racing a cancel produces exactly one outcome. `pending_stop` is kept
    for the same reason the other markers are: it is the record."""
    conn = await _conn()
    try:
        row = await conn.fetchrow(
            """
            update session set status = 'RUNNING', stop_requested = false,
                   updated_at = now()
             where id = $1 and status = 'STOPPED'
            returning run_state
            """,
            session_id,
        )
        if row is None or row["run_state"] is None:
            return None
        rs = row["run_state"]
        return json.loads(rs) if isinstance(rs, str) else rs
    finally:
        await conn.close()


async def withdraw_operator_items_for_session(session_id: str) -> list[str]:
    """Withdraw every OPEN ask hanging off this session — the work it blocks is
    being cancelled for good, so the question is moot. WITHDRAWN, never deleted
    and never ANSWERED: nobody answered it."""
    conn = await _conn()
    try:
        rows = await conn.fetch(
            "update operator_item set status = 'WITHDRAWN', answered_at = now() "
            "where session_id = $1 and status = 'OPEN' returning id",
            session_id,
        )
        return [r["id"] for r in rows]
    finally:
        await conn.close()


async def latest_proposal_for_session(session_id: str) -> dict | None:
    """The newest proposal a session drafted, or None.

    Newest rather than by-id because a REJECTION makes the drafter redraft: the
    original goes REJECTED and a fresh proposal appears under the same session.
    A consult wait that reported the id it started with would report a stale
    rejection while the accepted redraft sat in the record beside it.

    The jsonb columns are DECODED, like `load_proposal` does — asyncpg hands
    them back as strings otherwise. Its one caller reads
    `prop["provenance"]["result_text"]` to tell an asker what its specialist's
    approved change actually produced, and got `'str' object has no attribute
    'get'` every time (found 2026-08-23 building the chained wait): the resume
    raised, the asker's session landed FAILED, and the one outcome the wait
    exists to deliver — the executed result — was the only one that could
    never arrive."""
    conn = await _conn()
    try:
        row = await conn.fetchrow(
            "select * from proposal where session_id = $1 "
            "order by created_at desc limit 1",
            session_id,
        )
        if row is None:
            return None
        d = dict(row)
        for k in ("actions", "evidence", "provenance", "confidence", "origin",
                  "retry_state"):
            v = d.get(k)
            d[k] = json.loads(v) if isinstance(v, str) else v
        return d
    finally:
        await conn.close()


async def withdraw_proposals_for_session(session_id: str) -> list[str]:
    """Withdraw this session's undecided proposals. AWAITING_HUMAN/PROPOSED
    only: an APPROVED / EXECUTING / RETRY_PENDING proposal is an approval the
    operator already gave, and an approval executes — its landing finds a
    terminal task and no-ops."""
    conn = await _conn()
    try:
        rows = await conn.fetch(
            """
            update proposal set status = 'WITHDRAWN', decided_at = now()
             where session_id = $1 and status in ('AWAITING_HUMAN', 'PROPOSED')
            returning id
            """,
            session_id,
        )
        return [r["id"] for r in rows]
    finally:
        await conn.close()


async def sessions_awaiting_resume() -> list[str]:
    """Parked resumes, oldest first — the resume sweep's second worklist."""
    conn = await _conn()
    try:
        rows = await conn.fetch(
            "select id from session where status = 'AWAITING_RESUME' order by updated_at"
        )
        return [r["id"] for r in rows]
    finally:
        await conn.close()


async def sessions_with_orphaned_resume(age_seconds: int) -> list[dict]:
    """Sessions whose resume died with its process — still AWAITING_HUMAN, but
    holding an armed `pending_resume` describing a turn the agent is OWED.
    Oldest first. Returns `{id, pending}`.

    Three predicates, each load-bearing:

    * **The armed marker** (`resume_park.arm`) is the whole discriminator
      between "the operator decided and we lost the follow-through" and "nothing
      has happened yet". A session AWAITING_HUMAN without one is simply waiting.

    * **`tool_call_id` must still match.** run_state is a key-level MERGE, so a
      run that resumed successfully and then re-parked (a redraft) leaves the
      OLD marker sitting beside a NEW `tool_call_id`. Re-driving that would hand
      a redraft's deferred call the previous decision's outcome. Matching ids
      means the session is still parked on the very call the marker describes —
      i.e. the resume genuinely never landed.

    * **Age.** A live resume does not flip any status while it runs (it reads
      through `load_paused_session` and writes only when it settles), so an
      in-flight resume is indistinguishable from a dead one except by clock.
      The threshold must exceed a real resume's duration.

    Unlike `fail_stale_running_sessions`, which FAILS what a restart interrupted,
    these are re-driven: there the operator never re-issued the work, whereas
    here they answered a question or approved a proposal and the agent's turn is
    owed to them. Losing it would discard the operator's own decision.
    """
    conn = await _conn()
    try:
        rows = await conn.fetch(
            """
            select id, run_state->'pending_resume' as pending from session
             where status = 'AWAITING_HUMAN'
               and run_state->'pending_resume' is not null
               and run_state->'pending_resume'->>'tool_call_id'
                   = run_state->>'tool_call_id'
               and updated_at < now() - ($1 || ' seconds')::interval
             order by updated_at
            """,
            str(int(age_seconds)),
        )
        return [
            {"id": r["id"],
             "pending": json.loads(r["pending"]) if isinstance(r["pending"], str)
             else r["pending"]}
            for r in rows
        ]
    finally:
        await conn.close()


# --- operator items (D7 phase 2): plan reviews + questions --------------------


async def create_operator_item(
    item_id: str, kind: str, session_id: str, agent_id: str,
    body: str, task_id: str | None = None, tool_call_id: str | None = None,
) -> None:
    conn = await _conn()
    try:
        await conn.execute(
            """
            insert into operator_item
                (id, kind, session_id, agent_id, task_id, body, tool_call_id)
            values ($1, $2, $3, $4, $5, $6, $7)
            """,
            item_id, kind, session_id, agent_id, task_id, body, tool_call_id,
        )
    finally:
        await conn.close()


async def get_operator_item(item_id: str) -> dict | None:
    conn = await _conn()
    try:
        row = await conn.fetchrow("select * from operator_item where id = $1", item_id)
        return dict(row) if row else None
    finally:
        await conn.close()


async def answer_operator_item(
    item_id: str, answer: str, verdict: str | None = None
) -> bool:
    """Record the operator's answer. Guarded: an already-answered item is never
    re-answered — the first answer is the one the parked run resumes with."""
    conn = await _conn()
    try:
        r = await conn.execute(
            """
            update operator_item
               set status = 'ANSWERED', answer = $2, verdict = $3, answered_at = now()
             where id = $1 and status = 'OPEN'
            """,
            item_id, answer, verdict,
        )
        return r.endswith("1")
    finally:
        await conn.close()


async def list_operator_items(status: str | None = "OPEN") -> list[dict]:
    conn = await _conn()
    try:
        if status is None:
            rows = await conn.fetch(
                "select * from operator_item order by created_at desc limit 200")
        else:
            rows = await conn.fetch(
                "select * from operator_item where status = $1 order by created_at",
                status,
            )
        return [dict(r) for r in rows]
    finally:
        await conn.close()


# --- conversational sessions (Phase 3) ---------------------------------------


async def create_conversation_session(session_id: str, agent_id: str) -> None:
    """A conversation session starts RUNNING in mode='conversation' — the mode
    is what makes text replies rest in AWAITING_OPERATOR instead of going DONE."""
    conn = await _conn()
    try:
        await conn.execute(
            """
            insert into session (id, agent_id, status, mode)
            values ($1, $2, 'RUNNING', 'conversation')
            on conflict (id) do nothing
            """,
            session_id, agent_id,
        )
    finally:
        await conn.close()


async def mark_session_running(session_id: str) -> None:
    """Flip a resting conversation session back to RUNNING for a fresh turn, so
    the Workspace shows it working while the transcript grows."""
    conn = await _conn()
    try:
        await conn.execute(
            "update session set status = 'RUNNING', updated_at = now() where id = $1",
            session_id,
        )
    finally:
        await conn.close()


async def mark_session_awaiting_operator(session_id: str, run_state: dict | None = None) -> None:
    """The conversation's resting state: the agent has replied and awaits the
    operator's next message. Optionally re-persist the transcript (the gateway
    passes the post-decision history; a plain text turn already persisted it)."""
    conn = await _conn()
    try:
        if run_state is not None:
            await conn.execute(
                "update session set status = 'AWAITING_OPERATOR', "
                "run_state = coalesce(run_state, '{}'::jsonb) || $2::jsonb, "
                "updated_at = now() where id = $1",
                session_id, json.dumps(run_state),
            )
        else:
            await conn.execute(
                "update session set status = 'AWAITING_OPERATOR', updated_at = now() "
                "where id = $1",
                session_id,
            )
    finally:
        await conn.close()


async def session_mode(session_id: str) -> str | None:
    conn = await _conn()
    try:
        row = await conn.fetchrow("select mode from session where id = $1", session_id)
        return row["mode"] if row else None
    finally:
        await conn.close()


async def mark_session_exec_failed_open(
    session_id: str, reason: str, run_state: dict | None = None
) -> None:
    """A FINAL execution failure (no redraft): rest the session OPEN and
    flagged instead of landing it — operator's rule (2026-08-20). The task and
    work items land FAILED at the gateway; the session sits AWAITING_OPERATOR
    with `exec_failure` set until the operator closes it by hand (the one
    hand-closable run, `nerve_gateway._close_conversation_for_key`). Same rest
    shape as a conversation's post-decision return, so a restart treats it
    identically."""
    conn = await _conn()
    try:
        if run_state is not None:
            await conn.execute(
                "update session set status = 'AWAITING_OPERATOR', "
                "exec_failure = $2, "
                "run_state = coalesce(run_state, '{}'::jsonb) || $3::jsonb, "
                "updated_at = now() where id = $1",
                session_id, reason, json.dumps(run_state),
            )
        else:
            await conn.execute(
                "update session set status = 'AWAITING_OPERATOR', "
                "exec_failure = $2, updated_at = now() where id = $1",
                session_id, reason,
            )
    finally:
        await conn.close()


async def get_session_model_override(session_id: str) -> str | None:
    """The cockpit-set model for this session, or None for "agent default"."""
    conn = await _conn()
    try:
        row = await conn.fetchrow(
            "select model_override from session where id = $1", session_id
        )
        return row["model_override"] if row else None
    finally:
        await conn.close()


async def get_session_thinking_override(session_id: str) -> str | None:
    """The cockpit-set thinking level for this session ('off'|'low'|'medium'|
    'xhigh'), or None for the deployment default."""
    conn = await _conn()
    try:
        row = await conn.fetchrow(
            "select thinking_override from session where id = $1", session_id
        )
        return row["thinking_override"] if row else None
    finally:
        await conn.close()


async def set_session_thinking_override(session_id: str, level: str | None) -> bool:
    """Set (or clear, with None) this session's thinking override. False if
    there is no such session."""
    conn = await _conn()
    try:
        result = await conn.execute(
            "update session set thinking_override = $2, updated_at = now() where id = $1",
            session_id, level,
        )
        return result.endswith(" 1")
    finally:
        await conn.close()


async def set_session_model_override(session_id: str, model: str | None) -> bool:
    """Set (or clear, with None) this session's model override. False if there
    is no such session — the caller says so rather than reporting a silent win."""
    conn = await _conn()
    try:
        result = await conn.execute(
            "update session set model_override = $2, updated_at = now() where id = $1",
            session_id, model,
        )
        return result.endswith(" 1")
    finally:
        await conn.close()


async def mark_session_failed(session_id: str) -> None:
    """A run that died before reaching an outcome. The partial transcript is
    KEPT — a failed run's history is exactly what the operator needs to see."""
    conn = await _conn()
    try:
        await conn.execute(
            "update session set status = 'FAILED', updated_at = now() where id = $1",
            session_id,
        )
    finally:
        await conn.close()


async def fail_stale_running_sessions(age_seconds: int) -> tuple[int, list[str]]:
    """Sweep RUNNING sessions whose process died mid-run (runs are in-process,
    so a row still RUNNING long past any real run's duration is an orphan —
    the ledger analogue is `reclaim_stale_work_items`). Returns the number of
    sessions flipped and the ids of the TASKS landed with them.

    Landing the task is half the sweep, not an extra: a task's run dies with the
    same process its session does, and the park that would have recorded it is
    written in an `except` block a dead process never reaches. Flipping only the
    session left the board reading IN_PROGRESS against a FAILED transcript,
    forever — one task sat that way from a restart on 2026-08-01.

    FAILED rather than parked for retry, deliberately: a restart is not a
    dependency outage, and silently re-running whatever was in flight at
    shutdown — a stale morning report, an hours-old ask — would spend tokens on
    work the operator never re-issued. The instructions stay on the record to
    re-run by hand.
    """
    conn = await _conn()
    try:
        async with conn.transaction():
            stale = [
                r["id"] for r in await conn.fetch(
                    """
                    update session set status = 'FAILED', updated_at = now()
                    where status = 'RUNNING'
                      and updated_at < now() - ($1 || ' seconds')::interval
                    returning id
                    """,
                    str(int(age_seconds)),
                )
            ]
            if not stale:
                return 0, []
            # REVIEW/terminal tasks are left alone: their session is parked or
            # done, so they are not what a dead run leaves behind.
            landed = [
                r["id"] for r in await conn.fetch(
                    """
                    update task set status = 'FAILED', terminal_at = now(),
                           outcome = 'run died with its process (swept at startup)'
                    where status = 'IN_PROGRESS' and session_id = any($1::text[])
                    returning id
                    """,
                    stale,
                )
            ]
        return len(stale), landed
    finally:
        await conn.close()


async def list_sessions(
    limit: int = 50,
    status: str | None = None,
    mode: str | None = None,
    agent_id: str | None = None,
    before: datetime | None = None,
    before_id: str | None = None,
    exclude_terminal_oneshot: bool = False,
) -> list[dict]:
    """Recent sessions, newest first, with the Activity browser's facets.

    The filters are all optional so a call passing none of them is unchanged.
    `exclude_terminal_oneshot` is opt-in, for the cockpit sidebar only: at
    3408 oneshot dispatch runs vs 50 closed conversations, the unfiltered
    `limit 1000` pushed conversations out of the window before the client-side
    history toggle ever saw them. RUNNING oneshots still show (the
    paused-task-run-in-panel behavior) — only terminal ones are dropped, so
    the Activity/Runs browser and the spawnedBy child lookup, which need every
    row, must never pass it. Paging is KEYSET on `(created_at, id)`, not offset: sessions
    are created while the operator pages, and an offset would silently skip or
    repeat a row every time one lands. Ties on `created_at` are real here —
    the D7 orchestrator assigns a fan-out in one transaction — so the id is
    part of the cursor, never decoration.

    `token_estimate` is a stored generated column since 2026-08-12 — the
    in-query detoast it replaced grew to ~1.5s at 668 sessions (22-26s under
    the cockpit's concurrent reconnect burst) and took the sidebar down.
    """
    where = ["true"]
    args: list = []

    def _arg(value) -> str:
        args.append(value)
        return f"${len(args)}"

    if status:
        where.append(f"s.status = {_arg(status)}")
    if mode:
        where.append(f"s.mode = {_arg(mode)}")
    if agent_id:
        where.append(f"s.agent_id = {_arg(agent_id)}")
    if before is not None:
        # Strict keyset: everything ordered after the cursor row itself.
        where.append(
            f"(s.created_at, s.id) < ({_arg(before)}, {_arg(before_id or '')})"
        )
    if exclude_terminal_oneshot:
        where.append("not (s.mode = 'oneshot' and s.status in ('DONE','FAILED','CANCELLED'))")
    limit_ph = _arg(limit)
    where_sql = " and ".join(where)

    conn = await _conn()
    try:
        rows = await conn.fetch(
            f"""
            select s.id, s.agent_id, s.status, s.mode, s.created_at, s.updated_at,
                   s.parent_session_id, s.closed_reason, s.model_override,
                   s.thinking_override, s.exec_failure,
                   (select par.agent_id from session par
                     where par.id = s.parent_session_id) as parent_agent_id,
                   (select p.intent from proposal p where p.session_id = s.id
                     order by p.created_at desc limit 1) as latest_intent,
                   (select count(*) from proposal p where p.session_id = s.id)
                     as proposal_count,
                   -- What the run is FOR, so the cockpit sidebar can name a lane
                   -- instead of printing its uuid. A task links its run here.
                   (select t.title from task t where t.session_id = s.id
                     order by t.created_at desc limit 1) as task_title,
                   -- The predicted ceiling arrived (2026-08-12): the detoast
                   -- that used to live here hit ~1.5s at 668 sessions, and the
                   -- cockpit's reconnect burst ran several concurrently — 22-26s
                   -- EACH on the Pi, presenting as socket deaths. The estimate
                   -- is a STORED GENERATED column now (schema.sql): computed by
                   -- the row write, which already carries the full jsonb; this
                   -- read is a plain column.
                   coalesce(s.token_estimate, 0) as token_estimate,
                   -- The other half of "what is this run FOR". 423 of the 601
                   -- sessions on the Pi are ledger drains with no task, so
                   -- without this the Activity browser prints a uuid for 70%
                   -- of its rows. `work_item.session_id` is the existing link.
                   (select w.subject from work_item w where w.session_id = s.id
                     order by w.enrolled_at limit 1) as work_subject
            from session s
            where {where_sql}
            order by s.created_at desc, s.id desc
            limit {limit_ph}
            """,
            *args,
        )
        return [dict(r) for r in rows]
    finally:
        await conn.close()


async def oldest_unprocessed_at() -> datetime | None:
    """When the longest-waiting queued item was enrolled, or None if the queue
    is empty. `list_work_items` orders newest-first, which is the wrong end for
    a backlog-age tile — the question is how long the OLDEST has waited."""
    conn = await _conn()
    try:
        return await conn.fetchval(
            "select min(enrolled_at) from work_item where state = 'UNPROCESSED'"
        )
    finally:
        await conn.close()


async def session_agent_ids() -> list[str]:
    """Distinct agents that have ever held a session — the Activity facet's
    vocabulary. Served with the list so the dropdown needs no second fetch
    (and can never disagree with the rows beside it)."""
    conn = await _conn()
    try:
        rows = await conn.fetch(
            "select distinct agent_id from session order by agent_id"
        )
        return [r["agent_id"] for r in rows]
    finally:
        await conn.close()


async def save_proposal(
    proposal_id: str,
    session_id: str,
    agent_id: str,
    intent: str,
    actions: list,
    evidence: list,
    expected_effect: str,
    idempotency_key: str,
    status: str = "AWAITING_HUMAN",
    confidence: dict | None = None,
    origin: dict | None = None,
) -> None:
    conn = await _conn()
    try:
        await conn.execute(
            """
            insert into proposal
              (id, session_id, agent_id, intent, actions, evidence, expected_effect,
               status, idempotency_key, confidence, origin)
            values ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7, $8, $9, $10::jsonb,
                    $11::jsonb)
            on conflict (id) do update set status = excluded.status
            """,
            proposal_id, session_id, agent_id, intent,
            json.dumps(actions), json.dumps(evidence), expected_effect,
            status, idempotency_key,
            json.dumps(confidence) if confidence is not None else None,
            json.dumps(origin) if origin is not None else None,
        )
    finally:
        await conn.close()


async def set_proposal_status(proposal_id: str, status: str) -> None:
    conn = await _conn()
    try:
        await conn.execute(
            "update proposal set status = $2, decided_at = now() where id = $1",
            proposal_id, status,
        )
    finally:
        await conn.close()


# Was this execution a SIMULATION? Derived from the append-only log, not from
# the CURRENT executor mode — mode changes, and a decision's record must not.
# `executor._execute_action` prefixes every dry-run result with "[dry-run]", and
# `proposal.executed`'s payload carries that text verbatim (2026-08-26: the
# operator approved six proposals believing they had executed). Reading the log
# rather than adding a column also covers rows decided BEFORE this existed.
_SIMULATED_SQL = """
    exists (select 1 from audit_event e
             where e.ref_id = p.id and e.kind = 'proposal.executed'
               and e.payload ->> 'result_text' like '[dry-run]%') as simulated
"""


async def load_proposal(proposal_id: str) -> dict | None:
    conn = await _conn()
    try:
        row = await conn.fetchrow(
            f"""
            select p.id, p.session_id, p.agent_id, p.intent, p.actions,
                   p.evidence, p.expected_effect, p.status, p.provenance,
                   p.created_at, p.decided_at, p.confidence, p.origin,
                   p.retry_state, {_SIMULATED_SQL}
            from proposal p where p.id = $1
            """,
            proposal_id,
        )
        if row is None:
            return None
        d = dict(row)
        for k in ("actions", "evidence", "provenance", "confidence", "origin",
                  "retry_state"):
            v = d.get(k)
            d[k] = json.loads(v) if isinstance(v, str) else v
        return d
    finally:
        await conn.close()


async def set_proposal_executed(proposal_id: str, provenance: dict) -> None:
    conn = await _conn()
    try:
        await conn.execute(
            """
            update proposal
              set status = 'EXECUTED', provenance = $2::jsonb, decided_at = now()
            where id = $1
            """,
            proposal_id, json.dumps(provenance),
        )
    finally:
        await conn.close()


# --- retryable executions (outage equivalence, slice 3) -----------------------
#
# An approved proposal whose execution hit a TRANSIENT dependency failure is
# never terminal FAILED: it rests RETRY_PENDING carrying the replay cursor, and
# a retry resumes from the first INCOMPLETE action.


async def park_proposal_retry(proposal_id: str, retry_state: dict) -> None:
    """Park an approved-but-unexecuted proposal for replay. `decided_at` is NOT
    touched — the decision happened when the operator made it, and a park is not
    a new decision."""
    conn = await _conn()
    try:
        await conn.execute(
            """
            update proposal
               set status = 'RETRY_PENDING', retry_state = $2::jsonb
             where id = $1
            """,
            proposal_id, json.dumps(retry_state),
        )
    finally:
        await conn.close()


async def claim_proposal_retry(proposal_id: str) -> dict | None:
    """Atomically claim a parked execution: RETRY_PENDING → EXECUTING, returning
    the whole row, or None if it is not parked (someone else claimed it). The
    flip IS the lock — the same SQL idiom as `claim_parked_resume`, so two
    sweeps racing replay the actions exactly once."""
    conn = await _conn()
    try:
        row = await conn.fetchrow(
            """
            update proposal set status = 'EXECUTING'
             where id = $1 and status = 'RETRY_PENDING'
            returning id, session_id, agent_id, intent, actions, evidence,
                      expected_effect, status, provenance, created_at,
                      decided_at, confidence, origin, retry_state
            """,
            proposal_id,
        )
        if row is None:
            return None
        d = dict(row)
        for k in ("actions", "evidence", "provenance", "confidence", "origin",
                  "retry_state"):
            v = d.get(k)
            d[k] = json.loads(v) if isinstance(v, str) else v
        return d
    finally:
        await conn.close()


async def list_proposals(status: str | None = None) -> list[dict]:
    conn = await _conn()
    try:
        if status:
            rows = await conn.fetch(
                """select id, session_id, agent_id, intent, status, created_at,
                          decided_at, origin
                   from proposal where status = $1 order by created_at desc""",
                status,
            )
        else:
            rows = await conn.fetch(
                """select id, session_id, agent_id, intent, status, created_at,
                          decided_at, origin
                   from proposal order by created_at desc"""
            )
        out = []
        for r in rows:
            # `origin` rides the LIST, not just the detail: the Inbox renders
            # "drafted by X · asked by Y" on the row, and a field the gateway
            # never sends is invisible to the cockpit's hand-declared types.
            d = dict(r)
            o = d.get("origin")
            d["origin"] = json.loads(o) if isinstance(o, str) else o
            out.append(d)
        return out
    finally:
        await conn.close()


_DECIDED_STATUSES = (
    "EXECUTED", "REJECTED", "FAILED", "WITHDRAWN", "APPROVED", "EXECUTING",
)


async def list_decided_proposals(limit: int, offset: int) -> tuple[list[dict], bool]:
    """Decisions Inbox history: everything no longer awaiting the operator,
    newest-decided-first. Paginated (fetch limit+1, slice) rather than a
    separate count query — `has_more` is all the cockpit needs to render a
    "load more" affordance."""
    conn = await _conn()
    try:
        rows = await conn.fetch(
            f"""select p.id, p.session_id, p.agent_id, p.intent, p.status,
                       p.created_at, p.decided_at, p.origin, {_SIMULATED_SQL}
                  from proposal p where p.status = any($1::text[])
                 order by coalesce(p.decided_at, p.created_at) desc
                 limit $2 offset $3""",
            list(_DECIDED_STATUSES), limit + 1, offset,
        )
        out = []
        for r in rows:
            d = dict(r)
            o = d.get("origin")
            d["origin"] = json.loads(o) if isinstance(o, str) else o
            out.append(d)
        has_more = len(out) > limit
        return out[:limit], has_more
    finally:
        await conn.close()


async def list_proposals_for(agent_id: str) -> list[dict]:
    """One agent's proposals, newest first — scoped in SQL rather than fetched
    whole and filtered in Python. The unscoped `list_proposals()` used to cap
    at 100 rows system-wide, which silently hid an agent's older history from
    the callers that filter by agent (a coaching corpus that reports "no
    history" when it means "none in the window" is worse than a slow query)."""
    conn = await _conn()
    try:
        rows = await conn.fetch(
            """select id, session_id, agent_id, intent, status, created_at,
                      decided_at, confidence
               from proposal where agent_id = $1 order by created_at desc""",
            agent_id,
        )
        out = []
        for r in rows:
            d = dict(r)
            c = d.get("confidence")
            d["confidence"] = json.loads(c) if isinstance(c, str) else c
            out.append(d)
        return out
    finally:
        await conn.close()


async def get_session(session_id: str) -> dict | None:
    conn = await _conn()
    try:
        row = await conn.fetchrow(
            "select id, agent_id, status, mode, parent_session_id, closed_reason, "
            "model_override, thinking_override, stop_requested, exec_failure, "
            "created_at, updated_at "
            "from session where id = $1",
            session_id,
        )
        return dict(row) if row else None
    finally:
        await conn.close()


# --- Event log (M8) ----------------------------------------------------------
# Append-only. Nothing here updates or deletes; `id` is both the total order and
# the SSE resume cursor.


async def append_event(
    kind: str, ref_id: str | None, payload: dict, actor: str | None = None
) -> dict:
    """Append one event and return it (with its assigned id and timestamp)."""
    conn = await _conn()
    try:
        row = await conn.fetchrow(
            """
            insert into audit_event (kind, ref_id, actor, payload)
            values ($1, $2, $3, $4::jsonb)
            returning id, kind, ref_id, actor, created_at
            """,
            kind, ref_id, actor, json.dumps(payload),
        )
        d = dict(row)
        d["payload"] = payload
        return d
    finally:
        await conn.close()


async def list_events(
    since_id: int = 0,
    kind: str | None = None,
    ref_id: str | None = None,
    limit: int = 200,
    actor: str | None = None,
    before_id: int | None = None,
    newest_first: bool = False,
) -> list[dict]:
    """Events after `since_id`, oldest-first — the replay/cursor query.

    The audit-trail browser reads the other way: `newest_first=True` with a
    `before_id` cursor pages backwards through history (the log is append-only,
    so an id is a stable page boundary forever).
    """
    clauses = ["id > $1"]
    args: list = [since_id]
    if kind:
        # `kind=work` matches the whole `work.*` namespace.
        args.append(kind)
        clauses.append(f"(kind = ${len(args)} or kind like ${len(args)} || '.%')")
    if ref_id:
        args.append(ref_id)
        clauses.append(f"ref_id = ${len(args)}")
    if actor:
        args.append(actor)
        clauses.append(f"actor = ${len(args)}")
    if before_id is not None:
        args.append(before_id)
        clauses.append(f"id < ${len(args)}")
    args.append(limit)

    conn = await _conn()
    try:
        rows = await conn.fetch(
            f"""
            select id, kind, ref_id, actor, payload, created_at
            from audit_event
            where {' and '.join(clauses)}
            order by id {'desc' if newest_first else ''}
            limit ${len(args)}
            """,
            *args,
        )
        out = []
        for r in rows:
            d = dict(r)
            p = d.get("payload")
            d["payload"] = json.loads(p) if isinstance(p, str) else p
            out.append(d)
        return out
    finally:
        await conn.close()


async def count_events_since(kind: str, actor: str, since) -> int:
    """How many `kind` events `actor` wrote at or after `since` — counted in
    SQL, deliberately.

    The attention budget (runtime/attention.py) reads this every time an agent
    tries to open a lane. Doing it as a Python filter over a capped list read
    would make the budget silently wrong the day the cap is reached: an agent
    that had spent its whole budget would look like it had spent none, which
    fails in the direction of MORE unsolicited contact. Counting in the
    database has no cap to be wrong about.
    """
    conn = await _conn()
    try:
        return await conn.fetchval(
            """
            select count(*) from audit_event
             where kind = $1 and actor = $2 and created_at >= $3
            """,
            kind, actor, since,
        )
    finally:
        await conn.close()


async def list_events_of_kinds(kinds: list[str], since_id: int = 0) -> list[dict]:
    """All events whose kind is in the list, ascending — the agreement report's
    read (it pairs verdicts with later operator outcomes, so order matters)."""
    conn = await _conn()
    try:
        rows = await conn.fetch(
            """
            select id, kind, ref_id, actor, payload, created_at
            from audit_event
            where id > $1 and kind = any($2)
            order by id
            """,
            since_id, kinds,
        )
        out = []
        for r in rows:
            d = dict(r)
            d["payload"] = _payload(d.get("payload"))
            out.append(d)
        return out
    finally:
        await conn.close()


def _payload(raw) -> dict:
    """asyncpg may hand back jsonb as str or dict depending on codec setup.
    `list_events_of_kinds` already normalises this way — same rule here so two
    readers of the same column can never disagree about its type."""
    return json.loads(raw) if isinstance(raw, str) else (raw or {})


async def gaps_for_session(session_id: str | None) -> list[dict]:
    """Every gap declared during one session, oldest first."""
    if not session_id:
        return []
    conn = await _conn()
    try:
        rows = await conn.fetch(
            """
            select payload, created_at from audit_event
             where kind = 'gap.declared' and ref_id = $1
             order by id
            """,
            session_id,
        )
        return [{**_payload(r["payload"]), "at": r["created_at"].isoformat()}
                for r in rows]
    finally:
        await conn.close()


async def recent_gaps(limit: int = 100) -> list[dict]:
    """The operator's gap queue — newest first."""
    conn = await _conn()
    try:
        rows = await conn.fetch(
            """
            select ref_id, payload, created_at from audit_event
             where kind = 'gap.declared'
             order by id desc
             limit $1
            """,
            limit,
        )
        return [{**_payload(r["payload"]), "session_id": r["ref_id"],
                 "at": r["created_at"].isoformat()} for r in rows]
    finally:
        await conn.close()


async def work_item_subjects(ids: list[str]) -> dict[str, str]:
    conn = await _conn()
    try:
        rows = await conn.fetch(
            "select id, subject from work_item where id = any($1)", ids
        )
        return {r["id"]: r["subject"] for r in rows}
    finally:
        await conn.close()


async def task_titles(ids: list[str]) -> dict[str, str]:
    conn = await _conn()
    try:
        rows = await conn.fetch("select id, title from task where id = any($1)", ids)
        return {r["id"]: r["title"] for r in rows}
    finally:
        await conn.close()


async def session_statuses(ids: list[str]) -> dict[str, str]:
    """Batched `session_status` — one query for a whole `decisions.list` page
    rather than one per discussion lane."""
    conn = await _conn()
    try:
        rows = await conn.fetch("select id, status from session where id = any($1)", ids)
        return {r["id"]: r["status"] for r in rows}
    finally:
        await conn.close()


async def event_kinds() -> list[dict]:
    """Every event kind the log has ever recorded, with counts — the audit
    trail's filter vocabulary comes from the log itself, never a hardcoded list."""
    conn = await _conn()
    try:
        rows = await conn.fetch(
            """
            select kind, count(*) as count, max(id) as latest_id
            from audit_event
            group by kind
            order by kind
            """
        )
        return [dict(r) for r in rows]
    finally:
        await conn.close()


async def get_event(event_id: int) -> dict | None:
    conn = await _conn()
    try:
        r = await conn.fetchrow(
            "select id, kind, ref_id, actor, payload, created_at from audit_event where id = $1",
            event_id,
        )
        if r is None:
            return None
        d = dict(r)
        p = d.get("payload")
        d["payload"] = json.loads(p) if isinstance(p, str) else p
        return d
    finally:
        await conn.close()


async def latest_event_id() -> int:
    conn = await _conn()
    try:
        return await conn.fetchval("select coalesce(max(id), 0) from audit_event")
    finally:
        await conn.close()


# --- Work Ledger (M6) --------------------------------------------------------
# Invariants: enrollment is idempotent on message_id; the claim is atomic (one
# dispatcher wins a row, never two); a row goes terminal only on a committed
# outcome, so a crash mid-flight leaves it reclaimable rather than lost.


async def enroll_work_item(
    item_id: str,
    message_id: str,
    payload: dict,
    *,
    thread_id: str | None = None,
    feed: str = "backlog",
    source: str = "fixture",
    subject: str | None = None,
    received_at=None,
    kind: str = "email",
) -> bool:
    """Idempotently enroll one work item. Returns True if this call created the row.

    `kind` is WHAT the item is (email | document); `source` stays the TRANSPORT
    it arrived on. The dispatcher routes on the former.
    """
    conn = await _conn()
    try:
        row = await conn.fetchrow(
            """
            insert into work_item
              (id, message_id, thread_id, feed, source, subject, payload,
               received_at, kind)
            values ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9)
            on conflict (message_id) do nothing
            returning id
            """,
            item_id, message_id, thread_id, feed, source, subject,
            json.dumps(payload), received_at, kind,
        )
        return row is not None
    finally:
        await conn.close()


# Proposal statuses that still OWE the world a change — undecided, or decided
# but not yet executed (RETRY_PENDING is an approved execution parked by an
# outage). The relatedness lock holds until none remain: "the next related item
# starts after the actions are ACCOMPLISHED, not after the verdict" (The operator,
# 2026-08-19). Interpolated into the claim queries and the semantic gate's
# comparison set so the three can never drift.
_UNEXECUTED_PROPOSALS = "('AWAITING_HUMAN', 'APPROVED', 'EXECUTING', 'RETRY_PENDING')"

# The relatedness lock's shared predicate: `w` is claimable only if no other
# item sharing a key with it (thread, normalized subject, or sender family) is
# in flight or awaiting an operator verdict — including a PROCESSED item whose
# proposal has not yet EXECUTED.
_RELATED_BUSY = f"""
                  and not exists (
                      select 1 from work_item s
                      where s.id <> w.id
                        and ((s.thread_id is not null and s.thread_id = w.thread_id)
                             or (s.subject_key is not null and s.subject_key = w.subject_key)
                             or (s.sender_key is not null and s.sender_key = w.sender_key))
                        and (s.state in ('CLAIMED', 'FOLD_PENDING', 'DISMISS_PENDING')
                             or (s.state = 'PROCESSED' and exists (
                                     select 1 from proposal p
                                     where p.session_id = s.session_id
                                       and p.status in {_UNEXECUTED_PROPOSALS})))
                  )"""


async def claim_next_work_item(session_id: str | None = None) -> dict | None:
    """Atomically claim the next eligible item, or return None if there is none.

    Pick order (D16, revised 2026-07-20 per the operator): live-feed-first, then
    NEWEST-first — recency correlates with relevance, and stale backlog must
    never starve fresh mail. Refs-only rows carry no received_at, and `nulls
    last` puts them behind EVERY dated row, however old — enrolled_at only
    orders rows WITHIN that date-less group, it is not a per-row fallback. So
    a Date-less hand-fed email waits out the entire dated backlog no matter
    how recently it was enrolled (observed live 2026-08-25: a dispatch/step
    claimed an old dated fixture over a just-fed date-less one). That is the
    accepted reading of newest-first — "no date" sorts as "oldest" — not a
    bug; feed with a Date header if pick order matters. `for update skip
    locked` makes concurrent dispatchers safe once concurrency rises above 1.

    Relatedness lock (D17/M9, widened 2026-08-19 per the operator): an item is skipped
    while ANY other item sharing its thread_id, its normalized subject
    (`subject_key`), or its sender family (`sender_key`) is in flight or
    awaiting an operator verdict — CLAIMED, FOLD_PENDING, DISMISS_PENDING, or
    PROCESSED with its session's proposal not yet EXECUTED (see
    `_UNEXECUTED_PROPOSALS`: the lock holds through approval and any outage
    park, so the world change LANDS before a related item starts). Deliberately
    aggressive: concurrent related runs produce duplicate Jira/graph writes,
    while a frozen item merely waits its turn at ~zero opportunity cost. The
    lock releases by itself at execution or a terminal verdict — no new state
    transitions.

    Outage equivalence (slice 4): a row transiently released while a dependency
    was down carries `not_before`, and is simply not eligible until then. The
    predicate lives HERE, in the same statement as the claim — a Python-side
    filter would race two dispatchers into claiming the same backed-off row.
    """
    conn = await _conn()
    try:
        row = await conn.fetchrow(
            f"""
            with picked as (
                select w.id
                from work_item w
                where w.state = 'UNPROCESSED'
                  and (w.not_before is null or w.not_before <= now())
                  {_RELATED_BUSY}
                order by (w.feed <> 'live'), w.received_at desc nulls last, w.enrolled_at
                for update skip locked
                limit 1
            )
            update work_item
               set state = 'CLAIMED',
                   session_id = coalesce($1, session_id),
                   attempts = attempts + 1,
                   claimed_at = now()
             where id in (select id from picked)
            returning id, message_id, thread_id, feed, source, subject, payload,
                      attempts, kind, transient_releases, embedding
            """,
            session_id,
        )
        if row is None:
            return None
        d = dict(row)
        p = d.get("payload")
        d["payload"] = json.loads(p) if isinstance(p, str) else p
        if "embedding" in d and isinstance(d["embedding"], str):
            d["embedding"] = json.loads(d["embedding"])
        return d
    finally:
        await conn.close()


async def claim_specific_work_item(item_id: str, session_id: str | None = None) -> dict | None:
    """Claim one named item — the operator's "process this now" path.

    Deliberately still honours the relatedness lock: skipping the queue is the
    operator's call to make, but running two sessions on related items is a
    correctness bug regardless of who asked for it.

    It deliberately does NOT honour `not_before`, for the same reason it skips
    the valves: the backoff exists to stop the drain loop hot-looping into a
    dead dependency, and an operator naming one item is not a loop. If the
    dependency is still down the run fails transiently again and re-backs-off.
    """
    conn = await _conn()
    try:
        row = await conn.fetchrow(
            f"""
            with picked as (
                select w.id
                from work_item w
                where w.id = $1
                  and w.state = 'UNPROCESSED'
                  {_RELATED_BUSY}
                for update skip locked
            )
            update work_item
               set state = 'CLAIMED',
                   session_id = coalesce($2, session_id),
                   attempts = attempts + 1,
                   claimed_at = now()
             where id in (select id from picked)
            returning id, message_id, thread_id, feed, source, subject, payload,
                      attempts, kind, transient_releases, embedding
            """,
            item_id, session_id,
        )
        if row is None:
            return None
        d = dict(row)
        p = d.get("payload")
        d["payload"] = json.loads(p) if isinstance(p, str) else p
        if "embedding" in d and isinstance(d["embedding"], str):
            d["embedding"] = json.loads(d["embedding"])
        return d
    finally:
        await conn.close()


async def work_item_related_busy(item_id: str) -> bool:
    """The claim queries' relatedness predicate, re-run for one already-CLAIMED
    item — the post-hydration re-check (2026-08-19). A refs-only backlog stub
    has no subject/sender keys when the claim's SQL lock evaluates it; they
    materialize at hydration, so the dispatcher asks again once they exist.
    Reuses `_RELATED_BUSY` verbatim — the two checks can never drift.

    Two stubs hydrating concurrently can each see the other and BOTH freeze:
    that degrades safe (both wait out a backoff, one wins the retry), never
    unsafe, so it is left simple.
    """
    conn = await _conn()
    try:
        row = await conn.fetchval(
            f"""
            select 1 from work_item w
            where w.id = $1
              {_RELATED_BUSY}
            """,
            item_id,
        )
        # The fragment is a NOT EXISTS filter: a row back means NOT busy.
        return row is None
    finally:
        await conn.close()


async def set_work_item_embedding(item_id: str, vector: list[float]) -> None:
    """Persist the item's semantic-freeze embedding (computed once, at claim)."""
    conn = await _conn()
    try:
        await conn.execute(
            "update work_item set embedding = $2::jsonb where id = $1",
            item_id, json.dumps(vector),
        )
    finally:
        await conn.close()


async def in_flight_embeddings(exclude_id: str) -> list[dict]:
    """Embeddings of every item the relatedness lock counts as busy — the
    semantic gate's comparison set. Small by construction: bounded by
    dispatch_concurrency plus the approval limit, never the queue."""
    conn = await _conn()
    try:
        rows = await conn.fetch(
            f"""
            select id, subject, embedding
            from work_item s
            where s.embedding is not null
              and s.id <> $1
              and (s.state in ('CLAIMED', 'FOLD_PENDING', 'DISMISS_PENDING')
                   or (s.state = 'PROCESSED' and exists (
                           select 1 from proposal p
                           where p.session_id = s.session_id
                             and p.status in {_UNEXECUTED_PROPOSALS})))
            """,
            exclude_id,
        )
        out = []
        for r in rows:
            d = dict(r)
            e = d.get("embedding")
            d["embedding"] = json.loads(e) if isinstance(e, str) else e
            out.append(d)
        return out
    finally:
        await conn.close()


async def finish_work_item(
    item_id: str,
    state: str,
    *,
    session_id: str | None = None,
    error: str | None = None,
) -> None:
    """Move a claimed item to a terminal state (PROCESSED | FOLDED | FAILED)."""
    conn = await _conn()
    try:
        await conn.execute(
            """
            update work_item
               set state = $2,
                   session_id = coalesce($3, session_id),
                   last_error = $4,
                   terminal_at = now()
             where id = $1
            """,
            item_id, state, session_id, error,
        )
    finally:
        await conn.close()


async def release_work_item(
    item_id: str,
    error: str | None = None,
    *,
    count_attempt: bool = True,
    not_before: datetime | None = None,
) -> None:
    """Hand a claimed item back to the queue (crash/transient failure).

    `count_attempt=False` is the outage-equivalence release: the CLAIM already
    incremented `attempts`, so a run that died because the PROVIDER was down
    gives that attempt back — otherwise three poll cycles during one outage
    would park the item FAILED for a reason that was never the item's fault.
    Such a release bumps `transient_releases` (the backoff exponent) instead,
    and `not_before` holds the row out of the queue until the backoff elapses.

    A semantic release keeps any existing `not_before` rather than clearing it:
    the row was claimable, so its `not_before` is already in the past.
    """
    conn = await _conn()
    try:
        await conn.execute(
            """
            update work_item
               set state = 'UNPROCESSED',
                   claimed_at = null,
                   last_error = $2,
                   attempts = case when $3 then attempts
                                   else greatest(attempts - 1, 0) end,
                   transient_releases = case when $3 then transient_releases
                                             else transient_releases + 1 end,
                   not_before = coalesce($4, not_before)
             where id = $1 and state = 'CLAIMED'
            """,
            item_id, error, count_attempt, not_before,
        )
    finally:
        await conn.close()


async def reclaim_stale_work_items(older_than_seconds: int = 900) -> int:
    """Recover items left CLAIMED by a crashed process. Returns rows recovered."""
    conn = await _conn()
    try:
        result = await conn.execute(
            """
            update work_item
               set state = 'UNPROCESSED', claimed_at = null,
                   last_error = 'reclaimed: stale claim'
             where state = 'CLAIMED'
               and claimed_at < now() - make_interval(secs => $1)
            """,
            older_than_seconds,
        )
        return int(result.rsplit(" ", 1)[-1])
    finally:
        await conn.close()


# --- Thread-aware batching (M9) ----------------------------------------------
# The fold lifecycle. A fold is a *claim of coverage*, not an outcome: siblings
# go FOLD_PENDING while the covering proposal awaits review, and only become
# terminal (FOLDED) once it is approved. Rejection returns them to the queue —
# which is what makes "no skips" survive a wrong fold decision.


async def thread_siblings(thread_id: str, exclude_id: str, limit: int = 500) -> list[dict]:
    """Unprocessed siblings of a claimed item — the survey the agent folds over.

    `limit` raised from 20 to 500: sized to the deployment ceiling
    (n_ctx=262144, no output cap). Still a bound (a thread with more than 500
    live unprocessed siblings is a runaway-thread scenario, not a normal
    context need) — `dispatcher._bundle_prompt` reports honestly when the
    result exactly hits this cap, since a query limit hides whether more
    siblings exist.
    """
    if not thread_id:
        return []
    conn = await _conn()
    try:
        rows = await conn.fetch(
            """
            select id, message_id, subject, payload, received_at
            from work_item
            where thread_id = $1 and id <> $2 and state = 'UNPROCESSED'
            order by received_at nulls last, enrolled_at
            limit $3
            """,
            thread_id, exclude_id, limit,
        )
        out = []
        for r in rows:
            d = dict(r)
            p = d.get("payload")
            d["payload"] = json.loads(p) if isinstance(p, str) else p
            out.append(d)
        return out
    finally:
        await conn.close()


async def mark_fold_pending(message_ids: list[str], proposal_id: str) -> int:
    """Reserve siblings as covered by `proposal_id`. Only UNPROCESSED rows move,
    so a race can never steal an item another session already claimed."""
    if not message_ids:
        return 0
    conn = await _conn()
    try:
        result = await conn.execute(
            """
            update work_item
               set state = 'FOLD_PENDING', folded_into = $2
             where message_id = any($1::text[]) and state = 'UNPROCESSED'
            """,
            message_ids, proposal_id,
        )
        return int(result.rsplit(" ", 1)[-1])
    finally:
        await conn.close()


async def commit_folds(proposal_id: str) -> int:
    """The covering proposal was approved — the fold is now a committed outcome."""
    conn = await _conn()
    try:
        result = await conn.execute(
            """
            update work_item
               set state = 'FOLDED', terminal_at = now()
             where folded_into = $1 and state = 'FOLD_PENDING'
            """,
            proposal_id,
        )
        return int(result.rsplit(" ", 1)[-1])
    finally:
        await conn.close()


async def release_folds(proposal_id: str) -> int:
    """The covering proposal was rejected — nothing covers these emails after
    all, so they go back in the queue to be handled on their own."""
    conn = await _conn()
    try:
        result = await conn.execute(
            """
            update work_item
               set state = 'UNPROCESSED', folded_into = null,
                   last_error = 'fold released: covering proposal rejected'
             where folded_into = $1 and state = 'FOLD_PENDING'
            """,
            proposal_id,
        )
        return int(result.rsplit(" ", 1)[-1])
    finally:
        await conn.close()


async def work_items_for_session(session_id: str) -> list[dict]:
    """The email(s) a session worked — so the reviewer can read the source, not
    just the agent's summary of it."""
    conn = await _conn()
    try:
        rows = await conn.fetch(
            """select id, message_id, subject, payload, received_at, state, kind
               from work_item where session_id = $1 order by enrolled_at""",
            session_id,
        )
        out = []
        for r in rows:
            d = dict(r)
            p = d.get("payload")
            d["payload"] = json.loads(p) if isinstance(p, str) else p
            out.append(d)
        return out
    finally:
        await conn.close()


async def work_items_folded_into(proposal_id: str) -> list[dict]:
    """Emails this proposal claims to also resolve. Approving it makes them
    terminal, so the reviewer must be shown them BEFORE deciding."""
    conn = await _conn()
    try:
        rows = await conn.fetch(
            """select id, message_id, subject, payload, state, kind
               from work_item where folded_into = $1 order by enrolled_at""",
            proposal_id,
        )
        out = []
        for r in rows:
            d = dict(r)
            p = d.get("payload")
            d["payload"] = json.loads(p) if isinstance(p, str) else p
            out.append(d)
        return out
    finally:
        await conn.close()


async def ledger_counts() -> dict:
    conn = await _conn()
    try:
        rows = await conn.fetch("select state, count(*) as n from work_item group by state")
        return {r["state"]: r["n"] for r in rows}
    finally:
        await conn.close()


async def list_work_items(
    state: str | None = None, limit: int = 100, include_payload: bool = False
) -> list[dict]:
    conn = await _conn()
    try:
        cols = """id, message_id, thread_id, feed, source, kind, subject, state,
                  session_id, attempts, last_error, enrolled_at, claimed_at,
                  terminal_at"""
        if include_payload:
            cols += ", payload"
        if state:
            rows = await conn.fetch(
                f"""select {cols} from work_item where state = $1
                    order by enrolled_at desc limit $2""",
                state, limit,
            )
        else:
            rows = await conn.fetch(
                f"select {cols} from work_item order by enrolled_at desc limit $1", limit
            )
        out = []
        for r in rows:
            d = dict(r)
            p = d.get("payload")
            if isinstance(p, str):
                d["payload"] = json.loads(p)
            out.append(d)
        return out
    finally:
        await conn.close()


async def work_items_for_sessions(session_ids: list[str]) -> dict[str, dict]:
    """session_id -> the work item (with payload) that session was processing.
    Empty for sessions that were not spawned by a ledger item (tasks, chats)."""
    if not session_ids:
        return {}
    conn = await _conn()
    try:
        rows = await conn.fetch(
            """select id, message_id, thread_id, feed, source, kind, subject,
                      state, session_id, enrolled_at, payload
               from work_item where session_id = any($1::text[])""",
            session_ids,
        )
        out: dict[str, dict] = {}
        for r in rows:
            d = dict(r)
            p = d.get("payload")
            if isinstance(p, str):
                d["payload"] = json.loads(p)
            out[d["session_id"]] = d
        return out
    finally:
        await conn.close()


async def proposal_counts_by_agent() -> list[dict]:
    """Per-agent proposal outcomes — the Coaching screen's approval-rate read."""
    conn = await _conn()
    try:
        rows = await conn.fetch(
            "select agent_id, status, count(*) as count from proposal"
            " group by agent_id, status order by agent_id, status"
        )
        return [dict(r) for r in rows]
    finally:
        await conn.close()


async def open_session_counts_by_agent() -> dict[str, int]:
    """Sessions that have not landed, per agent — the Agents page's "working
    now" bucket, which sorts those agents to the top of the roster.

    Terminal states are the ALLOWLIST, not the open ones: `session.status` is
    free text on purpose (see schema.sql), so a wait state added later must
    count as active by default rather than silently read as finished. An open
    conversation counts as working too — closing one lands it DONE.
    """
    conn = await _conn()
    try:
        rows = await conn.fetch(
            "select agent_id, count(*) as count from session"
            " where status not in ('DONE', 'FAILED')"
            " group by agent_id"
        )
        return {r["agent_id"]: r["count"] for r in rows}
    finally:
        await conn.close()


async def count_awaiting_human() -> int:
    """Proposals sitting in the Decisions Inbox — the approval-coupled throttle."""
    conn = await _conn()
    try:
        return await conn.fetchval(
            "select count(*) from proposal where status = 'AWAITING_HUMAN'"
        )
    finally:
        await conn.close()


async def count_in_flight() -> int:
    conn = await _conn()
    try:
        return await conn.fetchval("select count(*) from work_item where state = 'CLAIMED'")
    finally:
        await conn.close()


async def cleanup_spike() -> None:
    """Remove spike rows so the demo is repeatable."""
    conn = await _conn()
    try:
        await conn.execute("delete from proposal where session_id = 'sess_m2_spike'")
        await conn.execute("delete from session where id = 'sess_m2_spike'")
    finally:
        await conn.close()


# --- governed charters (Phase 3, M11) -----------------------------------------


async def list_agents(include_retired: bool = True, roster_only: bool = True) -> list[dict]:
    """The roster (D24: DB rows ARE the roster), with each agent's current
    charter version (0 = built-in) and lifecycle fields.

    Roster membership = a non-empty `role`, which only the schema seeds and
    `hire_agent` ever set. Plain `upsert_agent` registrations (runtime
    bookkeeping so sessions can FK to something — including piles of
    throwaway test agents) never join the roster."""
    where = ["a.role <> ''"] if roster_only else []
    if not include_retired:
        where.append("a.status = 'ACTIVE'")
    conn = await _conn()
    try:
        rows = await conn.fetch(
            f"""
            select a.id, a.name, a.model, a.thinking, a.created_at,
                   a.role, a.status, a.taskable, a.consultable, a.conversational,
                   a.deviations, a.template, a.hired_by, a.retired_reason, a.retired_at,
                   a.steward_group,
                   coalesce(g.version, 0) as charter_version
            from agent a
            left join guidance_version g
              on g.agent_id = a.id and g.status = 'CURRENT'
            {("where " + " and ".join(where)) if where else ""}
            order by a.created_at
            """
        )
        return [dict(r) for r in rows]
    finally:
        await conn.close()


async def get_agent(agent_id: str) -> dict | None:
    conn = await _conn()
    try:
        row = await conn.fetchrow(
            """
            select id, name, model, thinking, created_at, role, status, taskable,
                   consultable, conversational, deviations, template, hired_by,
                   retired_reason, retired_at, steward_group
            from agent where id = $1
            """,
            agent_id,
        )
        return dict(row) if row else None
    finally:
        await conn.close()


async def set_agent_model(agent_id: str, model: str, thinking: str) -> dict | None:
    """The operator's per-agent model + thinking choice. '' clears either half
    back to "unset" (fall through to CC_MODEL_<AGENT_ID>, then the default) —
    which is why both are written every time: a partial write would leave a
    cleared model still carrying the old thinking level."""
    conn = await _conn()
    try:
        row = await conn.fetchrow(
            "update agent set model = $2, thinking = $3 where id = $1 returning id",
            agent_id, model, thinking,
        )
    finally:
        await conn.close()
    return await get_agent(agent_id) if row else None


async def hire_agent(
    agent_id: str,
    name: str,
    role: str,
    charter: str,
    packs: list[str],
    *,
    template: str,
    taskable: bool = True,
    consultable: bool = False,
    conversational: bool = True,
    deviations: str = "",
    hired_by: str = "operator",
    steward_group: str = "",
) -> dict:
    """Create a roster agent — the D24 uniform-management invariant enforced at
    creation, in ONE transaction: the agent row cannot land without its charter
    v1 and its capability grants. A duplicate id fails loud (retired ids stay
    reserved: history is keyed by agent_id forever)."""
    if not charter.strip():
        raise ValueError("an agent cannot be hired without a charter")
    if not packs:
        raise ValueError("an agent cannot be hired without capability grants")
    if len(role) > 140:
        # `role` rides the generated "YOUR TEAM" section as a one-line routing
        # description (runtime/roster.py:team_section) — a long one bloats
        # every teammate's charter, not just this agent's own.
        raise ValueError(
            f"role must be a one-line routing description (<=140 chars, got "
            f"{len(role)})"
        )
    conn = await _conn()
    try:
        async with conn.transaction():
            await conn.execute(
                """
                insert into agent (id, name, model, charter, role, status,
                                   taskable, consultable, conversational,
                                   deviations, template, hired_by, steward_group)
                values ($1, $2, '', $3, $3, 'ACTIVE', $4, $5, $6, $7, $8, $9, $10)
                """,
                agent_id, name, role, taskable, consultable, conversational,
                deviations, template, hired_by, steward_group,
            )
            await conn.execute(
                """
                insert into guidance_version
                    (agent_id, version, content, status, rationale, created_by)
                values ($1, 1, $2, 'CURRENT', $3, $4)
                """,
                agent_id, charter, f"hired from template {template!r}", hired_by,
            )
            for pack in packs:
                await conn.execute(
                    "insert into agent_grant (agent_id, pack, granted_by)"
                    " values ($1, $2, $3)",
                    agent_id, pack, hired_by,
                )
    except asyncpg.UniqueViolationError:
        raise ValueError(
            f"agent id {agent_id!r} already exists (retired ids stay reserved — "
            "history is keyed by agent_id forever)"
        )
    finally:
        await conn.close()
    return (await get_agent(agent_id)) or {}


async def retire_agent(agent_id: str, reason: str, retired_by: str = "operator") -> bool:
    """Retire = a status flip with a recorded reason. Nothing is deleted:
    sessions, charters, grants and events stay keyed by agent_id."""
    conn = await _conn()
    try:
        result = await conn.execute(
            """
            update agent
            set status = 'RETIRED', retired_reason = $2, retired_at = now()
            where id = $1 and status = 'ACTIVE'
            """,
            agent_id, reason,
        )
        return result.endswith("1")
    finally:
        await conn.close()


async def reactivate_agent(agent_id: str) -> bool:
    conn = await _conn()
    try:
        result = await conn.execute(
            """
            update agent
            set status = 'ACTIVE', retired_reason = null, retired_at = null
            where id = $1 and status = 'RETIRED'
            """,
            agent_id,
        )
        return result.endswith("1")
    finally:
        await conn.close()


# --- capability-pack grants (D25) --------------------------------------------


async def list_grants(agent_id: str) -> list[dict]:
    """Every grant row for one agent, revoked included — callers filter on
    revoked_at. Returning history keeps 'no rows at all' distinguishable from
    'everything revoked' (the DEFAULT_PACKS fallback needs that distinction)."""
    conn = await _conn()
    try:
        rows = await conn.fetch(
            """
            select agent_id, pack, granted_by, granted_at, revoked_at, revoked_reason
            from agent_grant where agent_id = $1
            order by granted_at
            """,
            agent_id,
        )
        return [dict(r) for r in rows]
    finally:
        await conn.close()


async def grant_pack(agent_id: str, pack: str, granted_by: str = "operator") -> None:
    """Grant a pack (or re-grant a revoked one — the row revives with a fresh
    granted_at so the flip is visible in the record).

    Sandbox slice 5: `pack` may also be a DYNAMIC `mcp:<server_id>` grant —
    server access rides the same agent_grant machinery packs use (D25's
    lever, reused rather than duplicated). It is refused when no such
    `mcp_server` row exists, so an operator cannot grant access to a server
    that was never even proposed."""
    if pack.startswith("mcp:"):
        server_id = pack[len("mcp:"):]
        if await get_mcp_server(server_id) is None:
            raise ValueError(f"unknown mcp_server {server_id!r} — cannot grant {pack!r}")
    conn = await _conn()
    try:
        await conn.execute(
            """
            insert into agent_grant (agent_id, pack, granted_by)
            values ($1, $2, $3)
            on conflict (agent_id, pack) do update
                set revoked_at = null, revoked_reason = null,
                    granted_by = excluded.granted_by, granted_at = now()
            """,
            agent_id, pack, granted_by,
        )
    finally:
        await conn.close()


async def revoke_pack(agent_id: str, pack: str, reason: str | None = None) -> bool:
    """Revoke = timestamp the row, never delete it — so idempotent seeds can
    never resurrect a pack the operator took away."""
    conn = await _conn()
    try:
        result = await conn.execute(
            """
            update agent_grant
            set revoked_at = now(), revoked_reason = $3
            where agent_id = $1 and pack = $2 and revoked_at is null
            """,
            agent_id, pack, reason,
        )
        return result.endswith("1")
    finally:
        await conn.close()


async def current_charter(agent_id: str) -> dict | None:
    """The CURRENT guidance version, or None when the agent runs on its
    built-in code charter."""
    conn = await _conn()
    try:
        row = await conn.fetchrow(
            """
            select version, content, rationale, created_by, created_at, proposal_id
            from guidance_version
            where agent_id = $1 and status = 'CURRENT'
            """,
            agent_id,
        )
        return dict(row) if row else None
    finally:
        await conn.close()


async def list_charter_versions(agent_id: str) -> list[dict]:
    conn = await _conn()
    try:
        rows = await conn.fetch(
            """
            select version, content, status, rationale, created_by, created_at, proposal_id
            from guidance_version
            where agent_id = $1
            order by version desc
            """,
            agent_id,
        )
        return [dict(r) for r in rows]
    finally:
        await conn.close()


async def save_charter_version(
    agent_id: str, content: str, rationale: str | None, created_by: str = "operator",
    proposal_id: str | None = None,
) -> dict:
    """Append a new CURRENT version, superseding the previous one — atomically,
    so the one-CURRENT-per-agent index can never be tripped by a race.

    `proposal_id` links back to the coaching proposal that produced this
    version (None for operator-typed saves — never backfilled)."""
    conn = await _conn()
    try:
        async with conn.transaction():
            await conn.execute(
                "update guidance_version set status = 'SUPERSEDED'"
                " where agent_id = $1 and status = 'CURRENT'",
                agent_id,
            )
            row = await conn.fetchrow(
                """
                insert into guidance_version
                    (agent_id, version, content, status, rationale, created_by, proposal_id)
                values (
                    $1,
                    coalesce((select max(version) from guidance_version where agent_id = $1), 0) + 1,
                    $2, 'CURRENT', $3, $4, $5
                )
                returning version, content, rationale, created_by, created_at, proposal_id
                """,
                agent_id, content, rationale, created_by, proposal_id,
            )
        return dict(row)
    finally:
        await conn.close()


async def get_charter_version(agent_id: str, version: int) -> dict | None:
    conn = await _conn()
    try:
        row = await conn.fetchrow(
            "select version, content, rationale, created_by, created_at, proposal_id"
            " from guidance_version where agent_id = $1 and version = $2",
            agent_id, version,
        )
        return dict(row) if row else None
    finally:
        await conn.close()


async def existing_message_ids(message_ids: list[str]) -> set[str]:
    """Which of these ids are already enrolled — the feed poller's cheap
    already-seen check, so known mail is never re-fetched from the provider."""
    if not message_ids:
        return set()
    conn = await _conn()
    try:
        rows = await conn.fetch(
            "select message_id from work_item where message_id = any($1::text[])",
            message_ids,
        )
        return {r["message_id"] for r in rows}
    finally:
        await conn.close()


async def get_feed_state(key: str) -> dict | None:
    conn = await _conn()
    try:
        row = await conn.fetchrow("select value from feed_state where key = $1", key)
        if row is None:
            return None
        v = row["value"]
        return json.loads(v) if isinstance(v, str) else v
    finally:
        await conn.close()


async def set_feed_state(key: str, value: dict) -> None:
    conn = await _conn()
    try:
        await conn.execute(
            """
            insert into feed_state (key, value) values ($1, $2::jsonb)
            on conflict (key) do update set value = $2::jsonb, updated_at = now()
            """,
            key, json.dumps(value),
        )
    finally:
        await conn.close()


# --- lines of effort (2026-08-12) ----------------------------------------------
# The three-layer model is documented in schema.sql: check-ins are append-only
# data, `semantic` is versioned meaning, `presentation` is unversioned display.


def _loe_row(row) -> dict:
    d = dict(row)
    for key in ("semantic", "presentation"):
        v = d.get(key)
        d[key] = json.loads(v) if isinstance(v, str) else v
    return d


async def create_loe(
    loe_id: str, name: str, cadence: str, agent_id: str, semantic: dict,
    presentation: dict | None = None,
) -> dict:
    """Fails loud on a duplicate id — LOE ids are operator-facing names."""
    conn = await _conn()
    try:
        row = await conn.fetchrow(
            """
            insert into line_of_effort
                (id, name, cadence, agent_id, semantic, presentation)
            values ($1, $2, $3, $4, $5::jsonb, $6::jsonb)
            returning *
            """,
            loe_id, name, cadence, agent_id, json.dumps(semantic),
            json.dumps(presentation or {}),
        )
        return _loe_row(row)
    finally:
        await conn.close()


async def get_loe(loe_id: str) -> dict | None:
    conn = await _conn()
    try:
        row = await conn.fetchrow(
            "select * from line_of_effort where id = $1", loe_id
        )
        return _loe_row(row) if row else None
    finally:
        await conn.close()


async def list_loes(include_retired: bool = False) -> list[dict]:
    conn = await _conn()
    try:
        rows = await conn.fetch(
            "select * from line_of_effort"
            + ("" if include_retired else " where status = 'ACTIVE'")
            + " order by id"
        )
        return [_loe_row(r) for r in rows]
    finally:
        await conn.close()


async def update_loe(
    loe_id: str, *, name: str | None = None, cadence: str | None = None,
    semantic: dict | None = None, presentation: dict | None = None,
) -> dict | None:
    """Patch an ACTIVE line of effort. Returns None if it does not exist.

    A CHANGED `semantic` bumps `semantic_version`: past check-ins keep the
    version they were scored under, so a target change never silently rewrites
    what an old green meant. An unchanged semantic (or a presentation-only
    edit) does NOT bump — the version tracks meaning, not writes.
    """
    row = await get_loe(loe_id)
    if row is None:
        return None
    if row["status"] != "ACTIVE":
        raise ValueError(f"line of effort {loe_id!r} is RETIRED")

    sets, args = [], []
    for key, value in (("name", name), ("cadence", cadence)):
        if value is not None:
            args.append(value)
            sets.append(f"{key} = ${len(args)}")
    for key, value in (("semantic", semantic), ("presentation", presentation)):
        if value is not None:
            args.append(json.dumps(value))
            sets.append(f"{key} = ${len(args)}::jsonb")
    bump = semantic is not None and semantic != row["semantic"]
    if bump:
        sets.append("semantic_version = semantic_version + 1")

    args.append(loe_id)
    conn = await _conn()
    try:
        if bump:
            # Archive the outgoing version BEFORE overwriting it — the stamp
            # on past check-ins must always resolve to readable content.
            await conn.execute(
                "insert into loe_semantic (loe_id, version, semantic) "
                "values ($1, $2, $3::jsonb) on conflict do nothing",
                loe_id, row["semantic_version"], json.dumps(row["semantic"]),
            )
        # updated_at is always touched, even for an empty patch — the caller
        # asked for a write and "nothing changed" is still a fact about now.
        out = await conn.fetchrow(
            "update line_of_effort set "
            + "".join(f"{s}, " for s in sets)
            + f"updated_at = now() where id = ${len(args)} returning *",
            *args,
        )
        return _loe_row(out) if out else None
    finally:
        await conn.close()


async def retire_loe(loe_id: str, reason: str) -> bool:
    """Status flip, never a delete — the check-in history stays readable
    (the grant-revocation pattern). Idempotent-ish: retiring an already
    retired row returns False."""
    conn = await _conn()
    try:
        result = await conn.execute(
            "update line_of_effort set status = 'RETIRED', retired_at = now(), "
            "retired_reason = $1, updated_at = now() "
            "where id = $2 and status = 'ACTIVE'",
            reason, loe_id,
        )
        return result.endswith("1")
    finally:
        await conn.close()


async def record_loe_checkin(
    loe_id: str, light: str, confidence: int, summary: str,
    proposal_id: str | None = None,
) -> dict:
    """Append one check-in, stamped with the LOE's CURRENT semantic version.

    The stamp is read here rather than passed in: what a light means is a
    property of the LOE at the moment it was scored, not something the caller
    (ultimately an agent's proposal) may assert about itself.
    """
    row = await get_loe(loe_id)
    if row is None:
        raise ValueError(f"no such line of effort: {loe_id!r}")
    if row["status"] != "ACTIVE":
        raise ValueError(f"line of effort {loe_id!r} is RETIRED")
    conn = await _conn()
    try:
        out = await conn.fetchrow(
            """
            insert into loe_checkin
                (loe_id, semantic_version, light, confidence, summary, proposal_id)
            values ($1, $2, $3, $4, $5, $6)
            returning *
            """,
            loe_id, row["semantic_version"], light, confidence, summary, proposal_id,
        )
        return dict(out)
    finally:
        await conn.close()


async def list_loe_semantic_versions(loe_id: str) -> list[dict]:
    """Superseded semantic versions, newest first. The CURRENT version lives
    on the LOE row itself — callers compose full history as current + these."""
    conn = await _conn()
    try:
        rows = await conn.fetch(
            "select version, semantic, superseded_at from loe_semantic "
            "where loe_id = $1 order by version desc",
            loe_id,
        )
        return [
            {"version": r["version"],
             "semantic": json.loads(r["semantic"]) if isinstance(r["semantic"], str)
                         else r["semantic"],
             "superseded_at": r["superseded_at"]}
            for r in rows
        ]
    finally:
        await conn.close()


async def list_loe_checkins(loe_id: str, limit: int = 12) -> list[dict]:
    conn = await _conn()
    try:
        rows = await conn.fetch(
            "select * from loe_checkin where loe_id = $1 "
            "order by created_at desc, id desc limit $2",
            loe_id, limit,
        )
        return [dict(r) for r in rows]
    finally:
        await conn.close()


# --- heartbeat schedules (Phase 4, D27) ----------------------------------------


def _heartbeat_row(row) -> dict:
    d = dict(row)
    for key in ("schedule", "action_params"):
        v = d.get(key)
        d[key] = json.loads(v) if isinstance(v, str) else v
    return d


async def list_heartbeat_schedules(enabled_only: bool = False) -> list[dict]:
    conn = await _conn()
    try:
        rows = await conn.fetch(
            "select * from heartbeat_schedule"
            + (" where enabled" if enabled_only else "")
            + " order by id"
        )
        return [_heartbeat_row(r) for r in rows]
    finally:
        await conn.close()


async def get_heartbeat_schedule(schedule_id: str) -> dict | None:
    conn = await _conn()
    try:
        row = await conn.fetchrow(
            "select * from heartbeat_schedule where id = $1", schedule_id
        )
        return _heartbeat_row(row) if row else None
    finally:
        await conn.close()


async def create_heartbeat_schedule(
    schedule_id: str, name: str, schedule_kind: str, schedule: dict,
    action_kind: str, action_params: dict, created_by: str = "operator",
) -> None:
    """Fails loud on a duplicate id — schedule ids are operator-facing names."""
    conn = await _conn()
    try:
        await conn.execute(
            """
            insert into heartbeat_schedule
                (id, name, schedule_kind, schedule, action_kind, action_params, created_by)
            values ($1, $2, $3, $4::jsonb, $5, $6::jsonb, $7)
            """,
            schedule_id, name, schedule_kind, json.dumps(schedule),
            action_kind, json.dumps(action_params), created_by,
        )
    finally:
        await conn.close()


async def update_heartbeat_schedule(schedule_id: str, fields: dict) -> bool:
    """Patch name/schedule_kind/schedule/action_kind/action_params. The engine
    watches updated_at to recompute a changed schedule's next due time."""
    allowed = {"name", "schedule_kind", "schedule", "action_kind", "action_params"}
    sets, args = [], []
    for key, value in fields.items():
        if key not in allowed:
            raise ValueError(f"not a patchable field: {key}")
        args.append(json.dumps(value) if key in ("schedule", "action_params") else value)
        cast = "::jsonb" if key in ("schedule", "action_params") else ""
        sets.append(f"{key} = ${len(args)}{cast}")
    if not sets:
        return False
    args.append(schedule_id)
    conn = await _conn()
    try:
        result = await conn.execute(
            f"update heartbeat_schedule set {', '.join(sets)}, updated_at = now() "
            f"where id = ${len(args)}",
            *args,
        )
        return result.endswith("1")
    finally:
        await conn.close()


async def set_heartbeat_enabled(schedule_id: str, enabled: bool) -> bool:
    conn = await _conn()
    try:
        result = await conn.execute(
            "update heartbeat_schedule set enabled = $1, updated_at = now() where id = $2",
            enabled, schedule_id,
        )
        return result.endswith("1")
    finally:
        await conn.close()


async def delete_heartbeat_schedule(schedule_id: str) -> dict | None:
    """Delete a schedule, returning the removed row so the event record can
    carry a full snapshot (the log keeps the history; the table need not)."""
    conn = await _conn()
    try:
        row = await conn.fetchrow(
            "delete from heartbeat_schedule where id = $1 returning *", schedule_id
        )
        return _heartbeat_row(row) if row else None
    finally:
        await conn.close()


async def touch_heartbeat_fired(schedule_id: str) -> None:
    conn = await _conn()
    try:
        await conn.execute(
            "update heartbeat_schedule set last_fired_at = now() where id = $1",
            schedule_id,
        )
    finally:
        await conn.close()


async def hydrate_work_item(
    item_id: str, payload: dict, subject: str | None, received_at
) -> None:
    """Fill in the content of a refs-only row at claim time (M13). Subject and
    received_at only fill blanks — enrollment-time facts are never overwritten."""
    conn = await _conn()
    try:
        await conn.execute(
            """
            update work_item
               set payload = $2::jsonb,
                   subject = coalesce(subject, $3),
                   received_at = coalesce(received_at, $4)
             where id = $1
            """,
            item_id, json.dumps(payload), subject, received_at,
        )
    finally:
        await conn.close()


# --- dismissal review (M14) ----------------------------------------------------
# "No action needed" is a CLAIM, not an outcome — same rule as folds. The agent
# parks the item DISMISS_PENDING with its rationale; only the operator's
# confirmation makes it terminal. Reopening returns it to the queue with the
# operator's note riding the payload into the next run's prompt.


async def mark_dismiss_pending(item_id: str, rationale: str, session_id: str | None) -> None:
    conn = await _conn()
    try:
        await conn.execute(
            """
            update work_item
               set state = 'DISMISS_PENDING',
                   session_id = coalesce($3, session_id),
                   payload = payload || jsonb_build_object('dismissal_rationale', $2::text)
             where id = $1 and state = 'CLAIMED'
            """,
            item_id, rationale, session_id,
        )
    finally:
        await conn.close()


async def record_audit(item_id: str, audit: dict) -> None:
    """Attach the auditor's verdict to the work item's payload, where it rides
    to the operator's dismissal list alongside the agent's rationale."""
    conn = await _conn()
    try:
        await conn.execute(
            """
            update work_item
               set payload = payload || jsonb_build_object('audit', $2::jsonb)
             where id = $1
            """,
            item_id, json.dumps(audit),
        )
    finally:
        await conn.close()


async def unaudited_dismiss_pending() -> list[dict]:
    """Parked dismissals whose inline audit never landed — no verdict in the
    payload means it was either never run, cancelled by a restart, or errored
    (errors are deliberately not recorded on the item). A recorded CHALLENGE
    stays put: the operator gate it parked behind is the verdict."""
    conn = await _conn()
    try:
        rows = await conn.fetch(
            """
            select id, subject, payload from work_item
             where state = 'DISMISS_PENDING' and not payload ? 'audit'
             order by claimed_at
            """
        )
        out = []
        for r in rows:
            d = dict(r)
            d["payload"] = json.loads(d["payload"]) if d.get("payload") else {}
            out.append(d)
        return out
    finally:
        await conn.close()


async def unaudited_bulk_proposal_ids() -> list[dict]:
    """AWAITING_HUMAN proposals with no audit.verdict on the log (the log is
    the only place a bulk verdict lives). `operator_hold` mirrors the inline
    hook's `allow_auto_confirm=not note`: the run's work item still carries
    the reopen note that meant "I want eyes on this"."""
    conn = await _conn()
    try:
        rows = await conn.fetch(
            """
            select p.id,
                   exists (select 1 from work_item w
                            where w.session_id = p.session_id
                              and w.payload ? 'operator_note') as operator_hold
              from proposal p
             where p.status = 'AWAITING_HUMAN'
               and not exists (select 1 from audit_event e
                                where e.ref_id = p.id and e.kind = 'audit.verdict')
             order by p.created_at
            """
        )
        return [dict(r) for r in rows]
    finally:
        await conn.close()


async def confirm_dismissal(item_id: str) -> bool:
    conn = await _conn()
    try:
        r = await conn.execute(
            """
            update work_item set state = 'PROCESSED', terminal_at = now()
             where id = $1 and state = 'DISMISS_PENDING'
            """,
            item_id,
        )
        return r.endswith("1")
    finally:
        await conn.close()


async def unprocessed_items_for_message_ids(message_ids: list[str]) -> list[dict]:
    """Which of these ledger identities are still UNPROCESSED — the bulk-dismiss
    resolution seam (2026-08-17 design): candidates are pinned to concrete rows
    at resolve time, never a standing query."""
    if not message_ids:
        return []
    conn = await _conn()
    try:
        rows = await conn.fetch(
            """select id, message_id from work_item
                where message_id = any($1::text[]) and state = 'UNPROCESSED'""",
            message_ids,
        )
        return [dict(r) for r in rows]
    finally:
        await conn.close()


async def bulk_dismiss_unprocessed(message_ids: list[str], rationale: str) -> int:
    """One UPDATE, scoped to UNPROCESSED exactly like `mark_fold_pending` — a
    race can never touch a row another session already claimed. Returns the
    count actually flipped."""
    if not message_ids:
        return 0
    conn = await _conn()
    try:
        result = await conn.execute(
            """
            update work_item
               set state = 'PROCESSED',
                   terminal_at = now(),
                   payload = coalesce(payload, '{}'::jsonb)
                              || jsonb_build_object('dismissal_rationale', $2::text)
             where message_id = any($1::text[]) and state = 'UNPROCESSED'
            """,
            message_ids, rationale,
        )
        return int(result.rsplit(" ", 1)[-1])
    finally:
        await conn.close()


async def confirm_all_dismissals() -> list[str]:
    """Bulk confirm — returns the confirmed item ids (for the audit event)."""
    conn = await _conn()
    try:
        rows = await conn.fetch(
            """
            update work_item set state = 'PROCESSED', terminal_at = now()
             where state = 'DISMISS_PENDING'
            returning id
            """
        )
        return [r["id"] for r in rows]
    finally:
        await conn.close()


async def reopen_work_item(item_id: str, note: str | None = None) -> bool:
    """Back to the queue for another pass; the operator's note (trusted input)
    rides the payload into the next run's prompt."""
    conn = await _conn()
    try:
        r = await conn.execute(
            """
            update work_item
               set state = 'UNPROCESSED', claimed_at = null, session_id = null,
                   payload = case when $2::text is null then payload
                                  else payload || jsonb_build_object('operator_note', $2::text) end
             where id = $1 and state = 'DISMISS_PENDING'
            """,
            item_id, note,
        )
        return r.endswith("1")
    finally:
        await conn.close()


async def count_dismiss_pending() -> int:
    conn = await _conn()
    try:
        return await conn.fetchval(
            "select count(*) from work_item where state = 'DISMISS_PENDING'"
        )
    finally:
        await conn.close()


async def count_prior_individual_dismissals(sender: str) -> int:
    """Recurrence fact for triage context (2026-08-17): how many prior emails
    from this sender the agent dismissed one at a time. Excludes rows stamped
    by an operator bulk dismissal — those are not individual decisions."""
    conn = await _conn()
    try:
        return await conn.fetchval(
            """
            select count(*) from work_item
             where payload->>'from' = $1
               and state = 'PROCESSED'
               and payload ? 'dismissal_rationale'
               and payload->>'dismissal_rationale' not like 'operator bulk dismissal:%'
            """,
            sender,
        )
    finally:
        await conn.close()


async def fail_work_items_for_session(session_id: str, error: str) -> list[str]:
    """Ledger honesty for execution failures: an email goes PROCESSED when its
    proposal parks, so when that proposal later FAILS to execute (and the
    session ends without a successful redraft) the row is a lie — it looks
    handled while nothing happened in the world. Flip it to FAILED with the
    error, so the false negative is visible instead of silent."""
    conn = await _conn()
    try:
        rows = await conn.fetch(
            """
            update work_item
               set state = 'FAILED', last_error = $2, terminal_at = now()
             where session_id = $1 and state = 'PROCESSED'
            returning id
            """,
            session_id, error[:500],
        )
        return [r["id"] for r in rows]
    finally:
        await conn.close()


async def get_work_item(item_id: str) -> dict | None:
    conn = await _conn()
    try:
        row = await conn.fetchrow("select * from work_item where id = $1", item_id)
        if row is None:
            return None
        d = dict(row)
        d["payload"] = json.loads(d["payload"]) if d.get("payload") else {}
        return d
    finally:
        await conn.close()


async def requeue_failed_work_item(item_id: str, payload: dict | None = None) -> bool:
    """The operator's redo: a FAILED email goes back to UNPROCESSED for a fresh
    run (e.g. after the missing capability it needed gets built). `payload`
    optionally replaces the stored one — the requeue route re-parses hand-fed
    raw text so a payload stored under an old parser bug is healed instead of
    replayed (the wi_68bb23613065 incident: text '\\n' 400s Claude forever)."""
    conn = await _conn()
    try:
        r = await conn.execute(
            """
            update work_item
               set state = 'UNPROCESSED', claimed_at = null, session_id = null,
                   terminal_at = null, last_error = null, attempts = 0,
                   -- a requeue withdraws any earlier acknowledgment: if this
                   -- run fails again, the failure must ask for action again
                   payload = coalesce($2::jsonb, payload) - 'failure_ack'
             where id = $1 and state = 'FAILED'
            """,
            item_id, json.dumps(payload) if payload is not None else None,
        )
        return r.endswith("1")
    finally:
        await conn.close()


async def requeue_items_for_reprocessing(item_ids: list[str]) -> list[str]:
    """Send already-landed items back through triage — the operator clearing the
    Decisions Inbox without throwing the mail away.

    Distinct from `requeue_failed_work_item`, which is the redo for ONE item
    that FAILED. These have not failed: they were triaged, produced a proposal,
    a question or a dismissal claim, and the operator has decided that whole
    conclusion should be drawn again (2026-08-16: everything triaged while the
    graph was retiring true facts). So the eligible states are the LANDED ones,
    and CLAIMED is deliberately not among them — an item a live session is
    holding must be unwound through that session, never yanked underneath it.

    `attempts = 0` because this is a first run of new work, not a continuation:
    leaving it would walk the retry machinery toward exhaustion for reasons
    that have nothing to do with the mail. Returns the ids actually moved, so
    the caller records what happened rather than what it asked for."""
    if not item_ids:
        return []
    conn = await _conn()
    try:
        rows = await conn.fetch(
            """
            update work_item
               set state = 'UNPROCESSED', claimed_at = null, session_id = null,
                   terminal_at = null, last_error = null, attempts = 0,
                   not_before = null, payload = payload - 'failure_ack'
             where id = any($1::text[])
               and state in ('PROCESSED', 'DISMISS_PENDING', 'FOLD_PENDING', 'FOLDED')
            returning id
            """,
            item_ids,
        )
        return [r["id"] for r in rows]
    finally:
        await conn.close()


async def acknowledge_failed_work_item(item_id: str) -> bool:
    """The operator closes the loop on a failure not worth retrying: the row
    STAYS FAILED (the record is the truth) but carries the acknowledgment, so
    the Failures strip stops asking for action. An acknowledgment is an
    operator decision and is logged by the caller."""
    conn = await _conn()
    try:
        r = await conn.execute(
            """
            update work_item
               set payload = payload || '{"failure_ack": true}'::jsonb
             where id = $1 and state = 'FAILED'
               and coalesce((payload ->> 'failure_ack')::boolean, false) = false
            """,
            item_id,
        )
        return r.endswith("1")
    finally:
        await conn.close()


# --- first-class Tasks (Phase 4, SC-2 groundwork) ------------------------------

TASK_TERMINAL = ("DONE", "FAILED", "CANCELLED")


async def create_task(
    task_id: str, title: str, instructions: str, agent_id: str | None,
    parent_session_id: str | None = None,
) -> None:
    conn = await _conn()
    try:
        await conn.execute(
            """
            insert into task (id, title, instructions, agent_id, status,
                              parent_session_id)
            values ($1, $2, $3, $4, $5, $6)
            """,
            task_id, title, instructions, agent_id,
            "ASSIGNED" if agent_id else "NEW",
            parent_session_id,
        )
    finally:
        await conn.close()


async def assign_task(task_id: str, agent_id: str) -> bool:
    """Assign an unassigned task. Only NEW tasks are assignable — once a run
    has started, the assignment is history, not a mutable field."""
    conn = await _conn()
    try:
        r = await conn.execute(
            "update task set agent_id = $2, status = 'ASSIGNED' "
            "where id = $1 and status = 'NEW'",
            task_id, agent_id,
        )
        return r.endswith("1")
    finally:
        await conn.close()


async def start_task(task_id: str, session_id: str | None) -> None:
    conn = await _conn()
    try:
        await conn.execute(
            "update task set status = 'IN_PROGRESS', session_id = $2, "
            "started_at = coalesce(started_at, now()) where id = $1",
            task_id, session_id,
        )
    finally:
        await conn.close()


async def park_task_review(task_id: str, session_id: str) -> None:
    """A proposal from this task is awaiting the operator; the task holds in
    REVIEW until the session resolves (the gateway resolves it)."""
    conn = await _conn()
    try:
        await conn.execute(
            "update task set status = 'REVIEW', session_id = $2, "
            "started_at = coalesce(started_at, now()) where id = $1",
            task_id, session_id,
        )
    finally:
        await conn.close()


async def resolve_task(
    task_id: str, status: str, outcome: str | None = None,
    only_from: str | None = None,
) -> bool:
    """Move a task to a terminal state. Guarded: an already-terminal task is
    never re-resolved, and `only_from` narrows further (e.g. cancel is
    NEW-only — a running task can't be cancelled out from under its session)."""
    if status not in TASK_TERMINAL:
        raise ValueError(f"{status!r} is not a terminal task status")
    conn = await _conn()
    try:
        r = await conn.execute(
            """
            update task
               set status = $2, outcome = coalesce($3, outcome), terminal_at = now()
             where id = $1
               and status != all($4::text[])
               and ($5::text is null or status = $5)
            """,
            task_id, status, outcome, list(TASK_TERMINAL), only_from,
        )
        return r.endswith("1")
    finally:
        await conn.close()


# --- retryable task runs (outage equivalence, slice 4) ------------------------
#
# A task whose run died on a TRANSIENT dependency failure is never terminal
# FAILED: it returns to ASSIGNED — its pre-run status, same agent, same
# instructions — carrying the retry bookkeeping, and a fresh run IS the
# unit-level replay the doctrine promises.


async def park_task_retry(
    task_id: str, retry_state: dict, hint: str | None = None
) -> bool:
    """Return a task to ASSIGNED carrying its retry bookkeeping.

    Guarded against a terminal task: a run that raised after the task was
    already resolved (a late failure on a DONE task) must not resurrect it.
    `outcome` carries the operator-visible hint — the Task Board renders it
    today as the task's result, so a queued-for-retry task explains itself with
    no new wire field. It is cleared again by `claim_task_retry`.
    """
    conn = await _conn()
    try:
        r = await conn.execute(
            """
            update task
               set status = 'ASSIGNED',
                   retry_state = $2::jsonb,
                   outcome = $3
             where id = $1
               and status != all($4::text[])
            """,
            task_id, json.dumps(retry_state), hint, list(TASK_TERMINAL),
        )
        return r.endswith("1")
    finally:
        await conn.close()


async def claim_task_retry(task_id: str) -> dict | None:
    """Atomically claim a retry-parked task: ASSIGNED-with-retry_state →
    IN_PROGRESS, returning the whole row, or None if it is not parked (someone
    else claimed it). The flip IS the lock — the same SQL idiom as
    `claim_parked_resume` / `claim_proposal_retry`, so two sweeps racing re-run
    the task exactly once.

    The returned row carries the retry_state that was claimed; the ROW in the
    database no longer does, because the retry it describes is now in flight.
    That is why the state comes from a CTE rather than from `returning`:
    `returning` yields the NEW tuple, so it would hand every caller the null it
    just wrote and the attempt count would reset to zero on every claim —
    retrying forever, which is the one thing the limit exists to prevent. The
    CTE reads the statement's snapshot (the OLD value) while the UPDATE's own
    `retry_state is not null` predicate, re-checked under the row lock, is
    still what makes the flip the lock.
    """
    conn = await _conn()
    try:
        row = await conn.fetchrow(
            """
            with prev as (
                select id, retry_state from task where id = $1
            )
            update task
               set status = 'IN_PROGRESS',
                   retry_state = null,
                   outcome = null,
                   started_at = coalesce(started_at, now())
              from prev
             where task.id = prev.id
               and task.status = 'ASSIGNED'
               and task.retry_state is not null
            returning task.id, task.title, task.instructions, task.agent_id,
                      task.status, task.session_id, task.parent_session_id,
                      task.outcome, task.recommendation, task.created_at,
                      task.started_at, task.terminal_at,
                      prev.retry_state as retry_state
            """,
            task_id,
        )
        return _task_row(row) if row else None
    finally:
        await conn.close()


async def tasks_awaiting_retry(due_only: bool = True) -> list[dict]:
    """Retry-parked tasks, oldest park first — the resume sweep's worklist.

    `due_only` applies the backoff: a task whose `retry_state.not_before` has
    not elapsed is not offered yet. Like the ledger's `not_before` predicate
    this is SQL, not a Python filter, so the sweep can't hand a racing caller a
    task it is about to skip.
    """
    conn = await _conn()
    try:
        rows = await conn.fetch(
            """
            select * from task
             where status = 'ASSIGNED' and retry_state is not null
               and (not $1 or coalesce(retry_state ->> 'not_before', '')  = ''
                    or (retry_state ->> 'not_before')::timestamptz <= now())
             order by coalesce((retry_state ->> 'parked_at')::timestamptz,
                               created_at)
            """,
            due_only,
        )
        return [_task_row(r) for r in rows]
    finally:
        await conn.close()


def _task_row(row) -> dict:
    """jsonb comes back from asyncpg as a string — decode the recommendation
    (and the retry state) so every caller sees a dict, the same treatment
    run_state gets.

    `stopped` rides along wherever the query joined the session: the cockpit
    hand-declares its interfaces over these payloads, so a board that had to
    fetch each session separately to learn a task was stopped would simply
    never render the badge. Derived, never stored — the session's status is the
    truth and this cannot drift from it.
    """
    d = dict(row)
    for k in ("recommendation", "retry_state"):
        v = d.get(k)
        if isinstance(v, str):
            d[k] = json.loads(v)
    if "session_status" in d:
        d["stopped"] = d["session_status"] == "STOPPED"
    return d


async def task_for_session(session_id: str) -> dict | None:
    conn = await _conn()
    try:
        row = await conn.fetchrow("select * from task where session_id = $1", session_id)
        return _task_row(row) if row else None
    finally:
        await conn.close()


async def get_task(task_id: str) -> dict | None:
    conn = await _conn()
    try:
        row = await conn.fetchrow(
            "select t.*, s.status as session_status from task t "
            "left join session s on s.id = t.session_id where t.id = $1",
            task_id,
        )
        return _task_row(row) if row else None
    finally:
        await conn.close()


async def list_tasks(
    status: str | None = None, limit: int = 100, agent_id: str | None = None
) -> list[dict]:
    conn = await _conn()
    try:
        rows = await conn.fetch(
            """
            select t.*, s.status as session_status
              from task t left join session s on s.id = t.session_id
            where ($1::text is null or t.status = $1)
              and ($3::text is null or t.agent_id = $3)
            order by t.created_at desc
            limit $2
            """,
            status, limit, agent_id,
        )
        return [_task_row(r) for r in rows]
    finally:
        await conn.close()


async def tasks_orphaned_at_startup() -> list[dict]:
    """Plain-ASSIGNED tasks (no retry_state) with an agent — a run that was
    queued but never got to `task.started` before the process died, oldest
    first. Startup-sweep worklist ONLY (`api/app.py` lifespan): the in-memory
    task queue is empty by definition right after a restart, so any row still
    sitting here is genuinely orphaned, not merely "queued and waiting its
    turn" (a periodic sweep could not tell those apart).

    Deliberately excludes ASSIGNED-with-retry_state (owned by
    `orchestration.retry_parked_task`'s own atomic claim, `claim_task_retry`)
    and NEW/unassigned tasks (never started, not this sweep's job)."""
    conn = await _conn()
    try:
        rows = await conn.fetch(
            "select * from task where status = 'ASSIGNED' and retry_state is null "
            "and agent_id is not null order by created_at"
        )
        return [_task_row(r) for r in rows]
    finally:
        await conn.close()


async def fail_sessionless_in_progress_tasks() -> list[dict]:
    """Land tasks the process died on BETWEEN `start_task(id, None)` and the
    session actually being created — IN_PROGRESS with `session_id is null`.

    Startup-only, same by-construction argument as `tasks_orphaned_at_startup`:
    nothing in a fresh process can be mid-run yet, so a sessionless IN_PROGRESS
    row is genuinely orphaned. It is invisible to every OTHER landing: the
    session-orphan sweep matches on session_id (null never matches), the
    ASSIGNED sweep excludes IN_PROGRESS, and `_land_run_failure` only fires on
    an in-process exception a hard kill never raises. Found live 2026-08-25 —
    a restart in that window left a task stuck on the board forever.

    FAILED, never re-run — an interrupted RUN follows the orphan-session rule
    (the operator didn't re-issue it), unlike the ASSIGNED sweep's relaunch."""
    conn = await _conn()
    try:
        rows = await conn.fetch(
            """
            update task
               set status = 'FAILED', terminal_at = now(),
                   outcome = 'run died with its process before its session existed (swept at startup)'
             where status = 'IN_PROGRESS' and session_id is null
            returning *
            """
        )
        return [_task_row(r) for r in rows]
    finally:
        await conn.close()


async def count_nonterminal_tasks_with_title_prefix(prefix: str) -> int:
    """Count of non-terminal (ASSIGNED/IN_PROGRESS/REVIEW) tasks whose title
    starts with `prefix` — the litellm.discovery generation guard's read: a
    still-open task from an earlier autodiscovery pass means the current
    pass must task nothing new for that slice (see `heartbeat.actions.
    _litellm_discovery`)."""
    conn = await _conn()
    try:
        return await conn.fetchval(
            "select count(*) from task "
            "where status != all($2::text[]) and title like $1 || '%'",
            prefix, list(TASK_TERMINAL),
        )
    finally:
        await conn.close()


async def set_task_recommendation(task_id: str, recommendation: dict) -> None:
    """Store the orchestrator's routing recommendation (D7, advisory only)."""
    conn = await _conn()
    try:
        await conn.execute(
            "update task set recommendation = $2::jsonb where id = $1",
            task_id, json.dumps(recommendation),
        )
    finally:
        await conn.close()


async def task_counts() -> dict:
    conn = await _conn()
    try:
        rows = await conn.fetch("select status, count(*) as n from task group by status")
        return {r["status"]: r["n"] for r in rows}
    finally:
        await conn.close()


async def link_operator_item_discussion(item_id: str, session_id: str) -> None:
    """Tier 3: record which clarification conversation an item opened."""
    conn = await _conn()
    try:
        await conn.execute(
            "update operator_item set discussion_session_id = $2 where id = $1",
            item_id, session_id,
        )
    finally:
        await conn.close()


async def get_operator_item_by_discussion(session_id: str) -> dict | None:
    """The item a clarification conversation belongs to — how a concluding
    discussion finds the task it is unblocking."""
    conn = await _conn()
    try:
        row = await conn.fetchrow(
            "select * from operator_item where discussion_session_id = $1", session_id
        )
        return dict(row) if row else None
    finally:
        await conn.close()


async def list_open_discussions() -> list[dict]:
    """Every OPEN item that opened a clarification conversation, carrying the
    discussion session's current state beside it — what `discussion.sweep`
    reads.

    One query, not two, because the classifier needs the item's stall stamps AND
    when the lane last moved AND its transcript (to read who spoke last), and a
    per-item follow-up read would be a query per open discussion every tick.

    Deliberately unfiltered by age: "too quiet" is the classifier's judgement
    (`runtime/discussions.py`), which is pure and unit-tested without a
    database. Pushing the hours into SQL here would put the rule in two places.
    """
    conn = await _conn()
    try:
        rows = await conn.fetch(
            """
            select i.*,
                   s.updated_at as session_updated_at,
                   s.status     as session_status,
                   s.run_state  as session_run_state
              from operator_item i
              join session s on s.id = i.discussion_session_id
             where i.status = 'OPEN'
               and i.discussion_session_id is not null
             order by s.updated_at
            """
        )
        out = []
        for r in rows:
            row = dict(r)
            rs = row.get("session_run_state")
            row["session_run_state"] = json.loads(rs) if isinstance(rs, str) else rs
            out.append(row)
        return out
    finally:
        await conn.close()


async def mark_discussion_stalled(item_id: str) -> None:
    """S1 flagged. Current state, not history: "this lane was already flagged
    during THIS quiet window" — the classifier compares it against the session's
    updated_at, so a lane that moves and goes quiet again flags again."""
    conn = await _conn()
    try:
        await conn.execute(
            "update operator_item set stalled_at = now() where id = $1", item_id
        )
    finally:
        await conn.close()


async def mark_discussion_nudged(item_id: str) -> None:
    """S2 nudged. Counted separately from `stalled_at` on purpose: a lane nudged
    as S2 that then flips to S1 must still be flagged."""
    conn = await _conn()
    try:
        await conn.execute(
            "update operator_item set nudged_at = now() where id = $1", item_id
        )
    finally:
        await conn.close()


# --- Skills Library ----------------------------------------------------------
# Curated subject knowledge as governed data. Versioning mirrors `charter`: one
# CURRENT row per (skill, doc_key) enforced by a partial unique index, history
# immutable, a revert is a NEW version carrying old content.


async def create_skill(skill_id: str, title: str, summary: str) -> None:
    conn = await _conn()
    try:
        await conn.execute(
            """
            insert into skill (id, title, summary) values ($1, $2, $3)
            on conflict (id) do update set title = $2, summary = $3
            """,
            skill_id, title, summary,
        )
    finally:
        await conn.close()


async def list_skills(include_retired: bool = False) -> list[dict]:
    conn = await _conn()
    try:
        rows = await conn.fetch(
            """
            select s.*,
                   (select count(*) from skill_doc d
                     where d.skill_id = s.id and d.is_current and d.kind = 'reference')
                       as reference_count,
                   -- Mirrors `get_skill_route`'s holder set EXACTLY, because the
                   -- catalog row and the detail pane show the operator the same
                   -- number and must never disagree about it. Three predicates,
                   -- each load-bearing: `a.role <> ''` is roster membership
                   -- (D24 — plain `upsert_agent` registrations, including piles
                   -- of throwaway test agents, are NOT the roster, and
                   -- `list_agents` excludes them); `g.revoked_at is null` is an
                   -- active grant (revocation is a timestamp, never a delete);
                   -- and `s.status = 'ACTIVE'` matches `list_agent_skills`,
                   -- which drops grants on a retired skill, so a retired skill
                   -- reports 0 holders here just as it does there.
                   (select count(*) from agent_skill_grant g
                     join agent a on a.id = g.agent_id
                    where g.skill_id = s.id
                      and g.revoked_at is null
                      and a.role <> ''
                      and s.status = 'ACTIVE') as holder_count
              from skill s
             where ($1 or s.status = 'ACTIVE')
             order by s.id
            """,
            include_retired,
        )
        return [dict(r) for r in rows]
    finally:
        await conn.close()


async def get_skill(skill_id: str) -> dict | None:
    conn = await _conn()
    try:
        row = await conn.fetchrow("select * from skill where id = $1", skill_id)
        return dict(row) if row else None
    finally:
        await conn.close()


async def add_skill_doc(
    skill_id: str,
    doc_key: str,
    kind: str,
    title: str,
    content: str,
    *,
    source_url: str | None = None,
    describes: str | None = None,
    captured_at=None,
    added_by: str = "operator",
) -> dict:
    """Add a NEW version of one document, superseding the prior current one.

    Supersede-then-insert runs in a transaction: the partial unique index makes a
    half-applied version impossible rather than merely unlikely. Chunks are
    rebuilt here too, so `skill_chunk` can never describe a version that is no
    longer current.
    """
    from central_command.skills.chunking import split_markdown

    conn = await _conn()
    try:
        async with conn.transaction():
            prior = await conn.fetchval(
                """
                select max(version) from skill_doc
                 where skill_id = $1 and doc_key = $2
                """,
                skill_id, doc_key,
            )
            version = (prior or 0) + 1
            await conn.execute(
                """
                update skill_doc set is_current = false
                 where skill_id = $1 and doc_key = $2 and is_current
                """,
                skill_id, doc_key,
            )
            doc_id = f"doc_{uuid.uuid4().hex[:12]}"
            row = await conn.fetchrow(
                """
                insert into skill_doc
                    (id, skill_id, doc_key, kind, title, content, version,
                     source_url, describes, captured_at, added_by)
                values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                returning *
                """,
                doc_id, skill_id, doc_key, kind, title, content, version,
                source_url, describes, captured_at, added_by,
            )
            # Only reference docs are searchable — guidance arrives in context
            # whole when the capability loads, so indexing it would return the
            # agent text it is already reading.
            if kind == "reference":
                for ord_, chunk in enumerate(split_markdown(content)):
                    await conn.execute(
                        "insert into skill_chunk (doc_id, ord, content) values ($1, $2, $3)",
                        doc_id, ord_, chunk,
                    )
        return dict(row)
    finally:
        await conn.close()


async def list_skill_docs(
    skill_id: str, current_only: bool = True, include_retired: bool = False,
) -> list[dict]:
    """`include_retired` adds, for each doc_key with NO current version, its
    newest row (which carries `retired_at`) — the operator's click-target for
    a retired doc's history. The runtime never passes it: delivery and search
    stay current-only, which is what makes a retire invisible to new sessions.
    """
    conn = await _conn()
    try:
        if include_retired:
            rows = await conn.fetch(
                """
                select distinct on (doc_key) * from skill_doc
                 where skill_id = $1
                 order by doc_key, is_current desc, version desc
                """,
                skill_id,
            )
            return sorted(
                (dict(r) for r in rows), key=lambda d: (d["kind"], d["doc_key"]),
            )
        rows = await conn.fetch(
            """
            select * from skill_doc
             where skill_id = $1 and ($2 = false or is_current)
             order by kind, doc_key, version desc
            """,
            skill_id, current_only,
        )
        return [dict(r) for r in rows]
    finally:
        await conn.close()


async def retire_skill_doc(skill_id: str, doc_key: str, reason: str) -> dict | None:
    """End a doc_key: stamp the current version retired and drop its search
    chunks, so new sessions can neither load nor find it. Every version row
    stays — in-flight sessions already hold the text in their message history,
    and the operator's history view keeps working. Returns the retired row,
    or None when the key has no current version (unknown or already retired).

    Re-importing the same doc_key later simply creates version N+1 as current
    again — "retire then refresh" and "supersede" converge on purpose.
    """
    conn = await _conn()
    try:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                update skill_doc
                   set is_current = false, retired_at = now(), retired_reason = $3
                 where skill_id = $1 and doc_key = $2 and is_current
                returning *
                """,
                skill_id, doc_key, reason,
            )
            if row is None:
                return None
            await conn.execute("delete from skill_chunk where doc_id = $1", row["id"])
        return dict(row)
    finally:
        await conn.close()


async def get_skill_doc(doc_id: str) -> dict | None:
    conn = await _conn()
    try:
        row = await conn.fetchrow("select * from skill_doc where id = $1", doc_id)
        return dict(row) if row else None
    finally:
        await conn.close()


async def skill_doc_history(skill_id: str, doc_key: str) -> list[dict]:
    conn = await _conn()
    try:
        rows = await conn.fetch(
            """
            select * from skill_doc
             where skill_id = $1 and doc_key = $2
             order by version desc
            """,
            skill_id, doc_key,
        )
        return [dict(r) for r in rows]
    finally:
        await conn.close()


async def retire_skill(skill_id: str, reason: str) -> bool:
    conn = await _conn()
    try:
        result = await conn.execute(
            """
            update skill set status = 'RETIRED', retired_reason = $2, retired_at = now()
             where id = $1 and status = 'ACTIVE'
            """,
            skill_id, reason,
        )
        return result.endswith(" 1")
    finally:
        await conn.close()


async def reactivate_skill(skill_id: str) -> bool:
    """Bring a retired skill back, mirroring `reactivate_agent`.

    Grants are NOT touched here, because retirement never touched them: the
    `agent_skill_grant` rows survive a retirement with `revoked_at` still null,
    and what hid them was the `s.status = 'ACTIVE'` predicate in
    `list_agent_skills` (and the matching one in `list_skills`'s `holder_count`
    subselect). Flipping the status back therefore restores every previous
    holder — nothing needs re-granting.

    `and status = 'RETIRED'` makes the False return mean exactly one thing:
    nothing moved. An unknown id and an already-active skill both land there,
    which is what the route turns into its 409.
    """
    conn = await _conn()
    try:
        result = await conn.execute(
            """
            update skill
               set status = 'ACTIVE', retired_reason = null, retired_at = null
             where id = $1 and status = 'RETIRED'
            """,
            skill_id,
        )
        return result.endswith(" 1")
    finally:
        await conn.close()


async def grant_skill(agent_id: str, skill_id: str, granted_by: str = "operator") -> None:
    conn = await _conn()
    try:
        await conn.execute(
            """
            insert into agent_skill_grant (agent_id, skill_id, granted_by)
            values ($1, $2, $3)
            on conflict (agent_id, skill_id) do update
              set revoked_at = null, revoked_reason = null,
                  granted_by = $3, granted_at = now()
            """,
            agent_id, skill_id, granted_by,
        )
    finally:
        await conn.close()


async def revoke_skill(agent_id: str, skill_id: str, reason: str) -> bool:
    conn = await _conn()
    try:
        result = await conn.execute(
            """
            update agent_skill_grant set revoked_at = now(), revoked_reason = $3
             where agent_id = $1 and skill_id = $2 and revoked_at is null
            """,
            agent_id, skill_id, reason,
        )
        return result.endswith(" 1")
    finally:
        await conn.close()


async def list_agent_skills(agent_id: str, include_revoked: bool = False) -> list[dict]:
    conn = await _conn()
    try:
        rows = await conn.fetch(
            """
            select g.*, s.title, s.summary, s.status,
                   -- Mirrors `runtime.skills.capabilities_for`'s skip condition
                   -- EXACTLY, because it is the same claim computed twice and
                   -- the Agents page's Skills panel renders it as "delivered"
                   -- or "not delivered". That function reads
                   -- `list_skill_docs(skill_id)` — whose `current_only` default
                   -- is `is_current` and nothing else — then takes the first
                   -- doc with `kind == 'guidance'`, and offers the agent NO
                   -- capability at all when there is none. So: two predicates,
                   -- `is_current` and `kind = 'guidance'`, and deliberately no
                   -- third. Not `doc_key = 'guidance'` (the key is a slug, the
                   -- kind is the contract, and `add_skill_doc` lets a new
                   -- version change the kind under the same key); not
                   -- `s.status = 'ACTIVE'` (retirement is a separate reason for
                   -- non-delivery, shown as its own state — folding it in here
                   -- would report a documented skill as undocumented).
                   -- `tests/test_agent_skills_panel.py` holds the two sides
                   -- together against the live runtime.
                   exists (select 1 from skill_doc d
                            where d.skill_id = s.id
                              and d.is_current
                              and d.kind = 'guidance') as has_guidance,
                   -- Same shape as `list_skills`'s count, so the number the
                   -- panel shows is the number the catalog shows.
                   (select count(*) from skill_doc d
                     where d.skill_id = s.id and d.is_current and d.kind = 'reference')
                       as reference_count
              from agent_skill_grant g
              join skill s on s.id = g.skill_id
             where g.agent_id = $1
               and ($2 or (g.revoked_at is null and s.status = 'ACTIVE'))
             order by g.skill_id
            """,
            agent_id, include_revoked,
        )
        return [dict(r) for r in rows]
    finally:
        await conn.close()


_SKILL_CHUNK_SEARCH = """
    select c.content, c.ord,
           d.doc_key, d.title, d.source_url, d.describes, d.captured_at,
           ts_rank(c.tsv, {q}) as rank
      from skill_chunk c
      join skill_doc d on d.id = c.doc_id
     where d.skill_id = $1
       and d.is_current
       and d.kind = 'reference'
       and c.tsv @@ {q}
     order by rank desc, d.doc_key, c.ord
     limit $3
"""

# `websearch_to_tsquery` joins bare terms with `&`, so a natural-language query
# needs EVERY lexeme present in one ~2000-char chunk. Measured on the live
# index: 'defer_loading' returns 14 hits; the same term plus four ordinary
# English words returns 0. That is what made 3 of 5 conceptual searches come
# back "no matching passage" on the library's first live outing — not FTS being
# unsuited to conceptual queries, but AND over a long query.
#
# The `::text` round-trip is how a tsquery's operators are rewritten without
# re-parsing the user's words: the text form is `'a' & 'b'`, and only the
# top-level conjunctions become `|`. Phrase operators (`<->`, from quoted
# input) render as their own token and survive untouched.
#
# NEGATION IS THE EXCEPTION, so it is excluded rather than glossed over:
# `-unwanted` renders as `!'unwant'`, and OR-ing that in would match every
# chunk that merely lacks the word — the fallback would return noise ranked
# above nothing. A query carrying `!` therefore keeps its AND form, i.e. gets
# no fallback at all and still returns the honest empty result.
_ANY_TERM = """
    case when position('!' in websearch_to_tsquery('english', $2)::text) > 0
         then websearch_to_tsquery('english', $2)
         else replace(websearch_to_tsquery('english', $2)::text, ' & ', ' | ')::tsquery
    end
"""


async def search_skill_chunks(skill_id: str, query: str, limit: int = 50) -> list[dict]:
    """Full-text search over ONE skill's current reference documents.

    `websearch_to_tsquery` is used rather than `plainto_tsquery` because it
    tolerates the way a model actually phrases a lookup (quotes, `or`, `-term`)
    instead of erroring on it.

    Two passes: ALL terms first, and if that finds nothing, ANY term ranked by
    `ts_rank`. The strict pass keeps precision where precision is available;
    the fallback exists because the alternative is telling the agent a subject
    is undocumented when the library documents it — three best partial matches
    beat "nothing here", and the agent can see the passages and judge.

    Postgres FTS rather than embeddings, deliberately: the work deployment target
    is air-gapped, and Graphiti's OpenAI embedding call is already one of the few
    off-box dependencies. If retrieval quality disappoints, an embedding index
    goes behind THIS function without touching a caller.
    """
    # Clamp raised from 10 to 500: sized to the deployment ceiling
    # (n_ctx=262144, no output cap), not a small-context guess. Still clamped
    # (not removed) because this backs a full-text index scan, not a plain
    # bound on text a model reads — an unbounded caller-supplied limit here
    # is a query-cost knob, not a context-budget one.
    limit = max(1, min(int(limit), 500))
    conn = await _conn()
    try:
        rows = await conn.fetch(
            _SKILL_CHUNK_SEARCH.format(q="websearch_to_tsquery('english', $2)"),
            skill_id, query, limit,
        )
        if not rows:
            rows = await conn.fetch(
                _SKILL_CHUNK_SEARCH.format(q=_ANY_TERM), skill_id, query, limit,
            )
        return [dict(r) for r in rows]
    finally:
        await conn.close()


# --- sandbox copy-in grants (sandbox slice 1, D-sandbox) ---------------------
# Same shape as agent_grant on purpose (revoke-as-timestamp, operator-
# attributed): this is what makes copy-in "operator-granted", not "agent-
# chosen path". The sandbox-runner never reads this table — it has no DB
# access at all, by design; runtime/tools.py checks it before calling
# sandbox_copy_in.


async def list_sandbox_sources(agent_id: str) -> list[dict]:
    """Every `agent_sandbox_source` row for one agent, revoked included —
    callers filter on `revoked_at`, matching the `list_grants` convention."""
    conn = await _conn()
    try:
        rows = await conn.fetch(
            """
            select agent_id, source_path, granted_by, granted_at, revoked_at
            from agent_sandbox_source where agent_id = $1
            order by granted_at
            """,
            agent_id,
        )
        return [dict(r) for r in rows]
    finally:
        await conn.close()


async def grant_sandbox_source(
    agent_id: str, source_path: str, granted_by: str = "operator"
) -> None:
    """Grant (or re-grant a revoked) copy-in source, mirroring `grant_pack`."""
    conn = await _conn()
    try:
        await conn.execute(
            """
            insert into agent_sandbox_source (agent_id, source_path, granted_by)
            values ($1, $2, $3)
            on conflict (agent_id, source_path) do update
                set revoked_at = null, granted_by = excluded.granted_by,
                    granted_at = now()
            """,
            agent_id, source_path, granted_by,
        )
    finally:
        await conn.close()


async def revoke_sandbox_source(agent_id: str, source_path: str) -> bool:
    """Revoke = timestamp the row, never delete it, mirroring `revoke_pack`."""
    conn = await _conn()
    try:
        result = await conn.execute(
            """
            update agent_sandbox_source
            set revoked_at = now()
            where agent_id = $1 and source_path = $2 and revoked_at is null
            """,
            agent_id, source_path,
        )
        return result.endswith("1")
    finally:
        await conn.close()


async def upsert_mcp_server_synced(server_id: str, created_by: str) -> None:
    """Sandbox slice 2: the ONLY writer of `mcp_server` rows so far — called by
    the mcp.sync_source Executor handler after a successful commit. Insert as
    'synced' the first time; a later sync of the same server just moves an
    already-synced row's status back to 'synced' (idempotent, no history
    kept per-status — the git log is the history)."""
    conn = await _conn()
    try:
        await conn.execute(
            """
            insert into mcp_server (id, status, created_by)
            values ($1, 'synced', $2)
            on conflict (id) do update set status = 'synced'
            """,
            server_id, created_by,
        )
    finally:
        await conn.close()


async def get_mcp_server(server_id: str) -> dict | None:
    conn = await _conn()
    try:
        row = await conn.fetchrow(
            "select * from mcp_server where id = $1", server_id
        )
        return dict(row) if row else None
    finally:
        await conn.close()


async def list_mcp_servers() -> list[dict]:
    """Every mcp_server row (sandbox slice 5's operator view). No prior slice
    needed a list-all — only get_mcp_server(id) by the state machine."""
    conn = await _conn()
    try:
        rows = await conn.fetch("select * from mcp_server order by created_at")
        return [dict(r) for r in rows]
    finally:
        await conn.close()


async def mark_mcp_server_built(server_id: str, image_ref: str, digest: str) -> None:
    """Sandbox slice 3: mcp.build_image's write, after the image is built,
    imported into containerd, and its presence verified. Unconditional set
    (not `on conflict`) — the row already exists (build's precondition is a
    prior 'synced'-or-later row), so this is a plain state-machine advance."""
    conn = await _conn()
    try:
        await conn.execute(
            "update mcp_server set status = 'built', image_ref = $2, digest = $3"
            " where id = $1",
            server_id, image_ref, digest,
        )
    finally:
        await conn.close()


async def mark_mcp_server_deployed(server_id: str, namespace: str) -> None:
    """Sandbox slice 3: mcp.server_deploy's write, after `kubectl apply` and a
    successful rollout. Same idiom as mark_mcp_server_built."""
    conn = await _conn()
    try:
        await conn.execute(
            "update mcp_server set status = 'deployed', namespace = $2 where id = $1",
            server_id, namespace,
        )
    finally:
        await conn.close()


async def mark_mcp_server_retired(server_id: str) -> None:
    """mcp.server_remove's write, after k8s teardown and litellm deregistration
    both complete (or were skipped idempotently). Same idiom as
    mark_mcp_server_built/deployed/registered (unconditional set)."""
    conn = await _conn()
    try:
        await conn.execute(
            "update mcp_server set status = 'retired', retired_at = now() where id = $1",
            server_id,
        )
    finally:
        await conn.close()


async def mark_mcp_server_registered(server_id: str, litellm_id: str) -> None:
    """Sandbox slice 4: litellm.register_mcp_server's write, after the proxy
    accepts the registration. `litellm_alias` stores LiteLLM's own generated
    server_id (a uuid) — the LiteLLM-SIDE HANDLE deregistration will need,
    despite the column's name: the naming convention on OUR side is that
    `server_name`/`alias` sent to the proxy are both `server_id`, so this
    column is the only place the proxy's own id is recorded. Same idiom as
    mark_mcp_server_built/deployed (unconditional set; precondition is a
    prior 'deployed'-or-later row)."""
    conn = await _conn()
    try:
        await conn.execute(
            "update mcp_server set status = 'registered', litellm_alias = $2 where id = $1",
            server_id, litellm_id,
        )
    finally:
        await conn.close()


# --- MCP tool grants (sandbox slice 5) ---------------------------------------
# mcp_tool carries the DEFAULT gate per (server_id, tool_name), discovered at
# registration time; agent_mcp_gate_override is the operator's per-agent
# widen/narrow, written ONLY by the operator API routes (never a propose
# path) — its existence IS the explicit buy-in the governance doctrine
# requires. Both mirror agent_grant's revoke-as-timestamp shape.


async def upsert_mcp_tools(server_id: str, tools: list[dict]) -> None:
    """Insert/update the tools a server exposes (name + description only).
    `default_gate` is deliberately OMITTED from the update clause: a
    re-discovery (the server's tool catalog changed) must never silently
    re-gate a tool the operator already ungated — only a first INSERT sets
    the conservative default. `tools` items are {'name': ..., 'description':
    ...}; missing/empty description is stored as ''."""
    if not tools:
        return
    conn = await _conn()
    try:
        async with conn.transaction():
            for t in tools:
                name = t.get("name")
                if not name:
                    continue
                await conn.execute(
                    """
                    insert into mcp_tool (server_id, tool_name, description)
                    values ($1, $2, $3)
                    on conflict (server_id, tool_name) do update
                        set description = excluded.description
                    """,
                    server_id, name, t.get("description") or "",
                )
    finally:
        await conn.close()


async def list_mcp_tools(server_id: str | None = None) -> list[dict]:
    """Every `mcp_tool` row, optionally scoped to one server."""
    conn = await _conn()
    try:
        if server_id is None:
            rows = await conn.fetch(
                "select server_id, tool_name, default_gate, description "
                "from mcp_tool order by server_id, tool_name"
            )
        else:
            rows = await conn.fetch(
                "select server_id, tool_name, default_gate, description "
                "from mcp_tool where server_id = $1 order by tool_name",
                server_id,
            )
        return [dict(r) for r in rows]
    finally:
        await conn.close()


async def set_mcp_tool_default_gate(server_id: str, tool_name: str, gate: str) -> bool:
    conn = await _conn()
    try:
        result = await conn.execute(
            "update mcp_tool set default_gate = $3 where server_id = $1 and tool_name = $2",
            server_id, tool_name, gate,
        )
        return result.endswith("1")
    finally:
        await conn.close()


async def set_agent_mcp_gate_override(
    agent_id: str, server_id: str, tool_name: str, gate: str, granted_by: str = "operator",
) -> None:
    """Upsert an operator override — re-granting after a revoke REACTIVATES the
    row (fresh granted_at, revoked_at cleared), mirroring `grant_pack`."""
    conn = await _conn()
    try:
        await conn.execute(
            """
            insert into agent_mcp_gate_override
                (agent_id, server_id, tool_name, gate, granted_by)
            values ($1, $2, $3, $4, $5)
            on conflict (agent_id, server_id, tool_name) do update
                set gate = excluded.gate, granted_by = excluded.granted_by,
                    granted_at = now(), revoked_at = null
            """,
            agent_id, server_id, tool_name, gate, granted_by,
        )
    finally:
        await conn.close()


async def revoke_agent_mcp_gate_override(agent_id: str, server_id: str, tool_name: str) -> bool:
    """Revoke = timestamp the row, never delete — the default_gate takes back
    over once revoked_at is set (see `effective_mcp_gates`)."""
    conn = await _conn()
    try:
        result = await conn.execute(
            """
            update agent_mcp_gate_override set revoked_at = now()
            where agent_id = $1 and server_id = $2 and tool_name = $3
              and revoked_at is null
            """,
            agent_id, server_id, tool_name,
        )
        return result.endswith("1")
    finally:
        await conn.close()


async def list_agent_mcp_overrides(agent_id: str) -> list[dict]:
    """Every override row for one agent, revoked included — same convention as
    `list_grants`/`list_sandbox_sources`."""
    conn = await _conn()
    try:
        rows = await conn.fetch(
            """
            select agent_id, server_id, tool_name, gate, granted_by, granted_at, revoked_at
            from agent_mcp_gate_override where agent_id = $1
            order by granted_at
            """,
            agent_id,
        )
        return [dict(r) for r in rows]
    finally:
        await conn.close()


async def get_app_setting(key: str, default: dict | list) -> dict | list:
    """Small key/value settings store (see schema.sql's app_setting). Absent
    row = default — settings persist only once an operator actually sets one."""
    conn = await _conn()
    try:
        row = await conn.fetchrow(
            "select value from app_setting where key = $1", key,
        )
        return json.loads(row["value"]) if row else default
    finally:
        await conn.close()


async def set_app_setting(key: str, value: dict) -> None:
    conn = await _conn()
    try:
        await conn.execute(
            """
            insert into app_setting (key, value, updated_at)
            values ($1, $2::jsonb, now())
            on conflict (key) do update
                set value = $2::jsonb, updated_at = now()
            """,
            key, json.dumps(value),
        )
    finally:
        await conn.close()


async def effective_mcp_gates(agent_id: str) -> dict[tuple[str, str], str]:
    """The gate that actually applies, per (server_id, tool_name), for this
    agent: an unrevoked operator override wins over `mcp_tool.default_gate`.
    Every server's tools appear (even with no override), so callers never need
    to fall back to a second lookup."""
    conn = await _conn()
    try:
        rows = await conn.fetch(
            """
            select t.server_id, t.tool_name,
                   coalesce(o.gate, t.default_gate) as gate
            from mcp_tool t
            left join agent_mcp_gate_override o
                on o.server_id = t.server_id and o.tool_name = t.tool_name
               and o.agent_id = $1 and o.revoked_at is null
            """,
            agent_id,
        )
        return {(r["server_id"], r["tool_name"]): r["gate"] for r in rows}
    finally:
        await conn.close()


# --- graph verification worklist (2026-08-19 spec) ------------------------------
# One row per approved graph.add_episode, written by the Executor at execute
# time. The graph-verify-sweep is the only PENDING consumer; the operator
# routes decide AWAITING_OPERATOR rows. Statuses are documented on the table
# in schema.sql.


def _graph_verification_row(row) -> dict:
    d = dict(row)
    for key in ("mechanical", "delta"):
        v = d.get(key)
        if isinstance(v, str):
            d[key] = json.loads(v)
    return d


async def create_graph_verification(
    *, proposal_id: str, episode_name: str, group_id: str, scope: str, marker: str,
    remediation_of: str | None = None,
) -> dict:
    conn = await _conn()
    try:
        row = await conn.fetchrow(
            """
            insert into graph_verification
                (id, proposal_id, episode_name, group_id, scope, marker,
                 remediation_of)
            values ($1, $2, $3, $4, $5, $6, $7)
            returning *
            """,
            str(uuid.uuid4()), proposal_id, episode_name, group_id, scope, marker,
            remediation_of,
        )
        return _graph_verification_row(row)
    finally:
        await conn.close()


async def graph_verification_for_proposal(proposal_id: str) -> dict | None:
    """The verification row a proposal already produced — the once-per-proposal
    guard for the curation re-check (one curation proposal may carry several
    actions; the first to execute creates the re-check, the rest find it)."""
    conn = await _conn()
    try:
        row = await conn.fetchrow(
            "select * from graph_verification where proposal_id = $1 limit 1",
            proposal_id,
        )
        return _graph_verification_row(row) if row else None
    finally:
        await conn.close()


async def due_graph_verifications(settle_minutes: int, limit: int = 20) -> list[dict]:
    """PENDING rows past the settle delay — old enough that Graphiti's queued
    extraction should have landed, so a missing episode is a finding, not a
    race."""
    conn = await _conn()
    try:
        rows = await conn.fetch(
            """
            select * from graph_verification
             where status = 'PENDING'
               and created_at < now() - make_interval(mins => $1)
             order by created_at
             limit $2
            """,
            settle_minutes, limit,
        )
        return [_graph_verification_row(r) for r in rows]
    finally:
        await conn.close()


async def finish_graph_verification(
    verification_id: str,
    *,
    status: str,
    episode_uuid: str | None = None,
    mechanical: dict | None = None,
    delta: dict | None = None,
    verdict: str | None = None,
    verdict_rationale: str | None = None,
    has_invalidations: bool = False,
    closed_by: str | None = None,
) -> bool:
    """The sweep's one writer: audit results + the transition out of PENDING.
    `closed_by` set means a terminal close (active-mode auto-verify) and stamps
    `closed_at`; otherwise the row parks AWAITING_OPERATOR."""
    conn = await _conn()
    try:
        r = await conn.execute(
            """
            update graph_verification
               set status = $2, episode_uuid = $3,
                   mechanical = $4::jsonb, delta = $5::jsonb,
                   verdict = $6, verdict_rationale = $7,
                   has_invalidations = $8,
                   closed_by = $9,
                   closed_at = case when $9::text is null then null else now() end
             where id = $1 and status = 'PENDING'
            """,
            verification_id, status, episode_uuid,
            json.dumps(mechanical) if mechanical is not None else None,
            json.dumps(delta) if delta is not None else None,
            verdict, verdict_rationale, has_invalidations, closed_by,
        )
        return r.endswith("1")
    finally:
        await conn.close()


async def close_graph_verification(
    verification_id: str, *, status: str, closed_by: str, problem_note: str | None = None
) -> bool:
    """The operator's decision on a parked row. Status-guarded like
    `confirm_dismissal`: only AWAITING_OPERATOR rows close this way."""
    conn = await _conn()
    try:
        r = await conn.execute(
            """
            update graph_verification
               set status = $2, closed_by = $3, problem_note = $4, closed_at = now()
             where id = $1 and status = 'AWAITING_OPERATOR'
            """,
            verification_id, status, closed_by, problem_note,
        )
        return r.endswith("1")
    finally:
        await conn.close()


async def get_graph_verification(verification_id: str) -> dict | None:
    conn = await _conn()
    try:
        row = await conn.fetchrow(
            "select * from graph_verification where id = $1", verification_id
        )
        return _graph_verification_row(row) if row else None
    finally:
        await conn.close()


async def list_graph_verifications(status: str | None = None, limit: int = 100) -> list[dict]:
    conn = await _conn()
    try:
        if status:
            rows = await conn.fetch(
                """select * from graph_verification where status = $1
                   order by created_at desc limit $2""",
                status, limit,
            )
        else:
            rows = await conn.fetch(
                "select * from graph_verification order by created_at desc limit $1",
                limit,
            )
        return [_graph_verification_row(r) for r in rows]
    finally:
        await conn.close()
