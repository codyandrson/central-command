"""THE park path for a resume that failed because the provider was down.

Outage equivalence (docs/superpowers/specs/2026-07-31-outage-equivalence-design.md):
a transient failure on a decision-or-answer resume leg may delay the agent's
final turn, never lose it. The decision itself is already recorded and outranks
everything here — parking affects only the turn the agent still owes.

Why a module of its own rather than a gateway helper: the fourth park site is
`runtime/questions.resume_with_answer`, and **`runtime/` may never import
`gateway/`**. Four call sites each writing their own status flip + event is
exactly how `proposal.created` went missing from two paths (2026-07-25), so
there is one seam, in the tier both callers may import.

It now owns a SECOND park for the same structural reason: `park_continuation`,
for a run that exhausted its request window and needs the operator to grant
another. Different wait, same discipline — one flip, one event, one item.

And a THIRD, `park_stopped`: the operator stopped a live run at a node
boundary. Same discipline minus the item — see its docstring for why an
operator-initiated pause must not appear in the Decisions Inbox.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from central_command import events
from central_command.contract import classify_failure, retry_backoff_seconds
from central_command.contract.dependencies import LLM, stamped_dependencies
from central_command.db import repo


def pending_resume(
    *,
    kind: str,
    text: str | None,
    tool_call_id: str,
    after: str,
    proposal_id: str | None = None,
    item_id: str | None = None,
    attempts: int = 0,
    **extra,
) -> dict:
    """The stored description of the deferred result the run is still owed.

    `kind` is `"result"` (a ToolReturn: an Executor outcome, a failure report,
    an operator's answer), `"denial"` (a ToolDenied: rejection feedback) or
    `"read"` (a live read whose dependency was DOWN — no result exists yet, so
    `text` is None and the driver re-executes the read; see `read_pending`).
    `after` names the decision that produced it — approved | failed | rejected |
    answered — or, for a read, the landing tail to re-enter (task | converse |
    orchestrator), and is what the driver dispatches on.

    `extra` carries the few facts the ORIGINAL path had in scope and the retry
    would otherwise have to re-derive: the rejected path's authoritative
    `decided_event_id` (operator evidence must still be re-pointed at it on a
    retried redraft) and the failed path's `failure_summary`.
    """
    out = {
        "kind": kind,
        **({"text": text} if text is not None else {}),
        "tool_call_id": tool_call_id,
        "after": after,
        "attempts": attempts,
    }
    if proposal_id is not None:
        out["proposal_id"] = proposal_id
    if item_id is not None:
        out["item_id"] = item_id
    out.update({k: v for k, v in extra.items() if v is not None})
    return out


def read_pending(*, call, metadata: dict, after: str,
                 task_id: str | None = None) -> dict:
    """The pending record of a PARKED READ (FR-40): everything the resume
    driver needs to re-execute it — the tool, its arguments as the model sent
    them, the dependencies it waits on — and nothing of the turn's text (the
    history already holds the turn; the read's result does not exist yet).
    `not_before` is filled by `park_resume`, like every other kind's."""
    return pending_resume(
        kind="read", text=None, tool_call_id=call.tool_call_id, after=after,
        tool=metadata.get("tool") or call.tool_name,
        args=metadata.get("args") or {},
        dependencies=list(metadata.get("dependencies") or []),
        task_id=task_id,
    )


async def park_read(
    *, session_id: str, result, call, metadata: dict, after: str = "task",
    task_id: str | None = None, state: dict | None = None,
) -> dict:
    """Park a run whose read found its dependencies DOWN: AWAITING_RESUME with
    the pending read, announced by the same `session.resume_parked` every
    resume park emits. Returns the stored record.

    Order is the arm discipline: the history (up to the model's tool-call
    request, the siblings' results included) is written FIRST, then the status
    and the pending record land in ONE update — so there is no instant at
    which the session reads parked without the record that drives it, nor a
    record pointing at a history that was never persisted.
    """
    from central_command.runtime.durable import dump_run_state

    await repo.update_session_run_state(
        session_id, dump_run_state(result, call.tool_call_id)
    )
    pending = read_pending(call=call, metadata=metadata, after=after, task_id=task_id)
    if state:
        # The orchestrator round's loop counters, which its tail cannot
        # rebuild from the record (the same `state` a turn park carries).
        pending["state"] = state
    return await park_resume(
        session_id, pending, metadata.get("error") or "dependency down"
    )


async def park_turn(
    *, session_id: str, after: str, error: BaseException, task_id: str | None,
    state: dict | None, window: int, attempts: int, dependencies: list[str],
) -> dict:
    """Park a run whose TURN met a DOWN dependency (epic-outage-doctrine story
    6): AWAITING_RESUME with a `pending_resume` of kind `turn`. Called by
    `run._run_live` only after its record is persisted ending in the unsent
    request, so there is no call to answer and no text to keep — the resume
    re-sends that request (`agent.iter(None, message_history=…)`). `after` is
    the landing tail it re-enters (task | converse | orchestrator); `state`
    is what that tail cannot rebuild from the record (the orchestrator's loop
    counters); `window` keeps the continuation window it was on.

    Goes through `park_resume`, so the record gets `not_before` on the one
    backoff curve and the same `session.resume_parked` announcement every
    other resume park writes."""
    pending = {
        "kind": "turn",
        "after": after,
        "attempts": attempts,
        "dependencies": list(dependencies),
        "window": window,
    }
    if task_id:
        pending["task_id"] = task_id
    if state:
        pending["state"] = state
    return await park_resume(session_id, pending, error)


def framed_denial(feedback: str) -> str:
    """The ToolDenied text a REJECTED proposal comes back as.

    The raw feedback alone left the agent to guess what a rejection means, and
    a drafting prompt that says "exactly as given" (reflection's episode
    prompt) outranked it — found 2026-08-26: "scope it for the EA only" got a
    text close-out instead of a redraft. Frame ONLY here: the park record's
    `text` and the `proposal.decided` payload must stay the verbatim words,
    because `_redraft_evidence` re-checks the agent's quote against them.
    """
    return (
        "The operator REJECTED this proposal. Their feedback, verbatim: "
        f"{feedback}\n\n"
        "Read it as one of two things. (1) It asks for CHANGES — redraft by "
        "calling propose_action again with the feedback incorporated; operator "
        "feedback OVERRIDES any earlier instruction to keep arguments or scope "
        "exact. (2) It says the action is NOT NEEDED — close out briefly in "
        "plain text and do not re-propose."
    )


async def arm(session_id: str, pending: dict | None) -> None:
    """Record what the run is OWED before driving the resume, so a resume that
    dies with its process is recoverable.

    Every decision path already builds `pending` before it resumes — for the
    transient-failure park below — and then holds it in a local variable. A
    SIGKILL takes that variable with it, and what is left is a session sitting
    AWAITING_HUMAN whose question is already ANSWERED (or whose proposal is
    already EXECUTED): no sweep's worklist, no OPEN Inbox item, nothing that
    will ever drive it. Twelve triage sessions ended that way on 2026-08-15
    when a restart hung and systemd SIGKILLed uvicorn 15s later, mid-batch.

    Deliberately NOT a status flip: the session must stay AWAITING_HUMAN so the
    in-flight resume's own `load_paused_session` still finds it. This writes one
    key and nothing else; `sessions_with_orphaned_resume` reads it back.

    The marker is consumed by the session going terminal, not by being cleared —
    the same discipline as `pending_resume` after a retry. A run that re-parks
    (a redraft) leaves a STALE marker beside a NEW `tool_call_id`, which is
    exactly how the sweep tells the two apart; see the repo query.
    """
    if not pending:
        return
    await repo.update_session_run_state(session_id, {"pending_resume": pending})


async def park_resume(
    session_id: str, pending: dict, error: BaseException | str, *,
    due_now: bool = False,
) -> dict:
    """Park the session AWAITING_RESUME and announce it. Returns the stored
    pending record (with `last_error`/`parked_at`/`not_before` filled in).

    `not_before` is the ONE backoff curve (`contract.retry_backoff_seconds`)
    over the attempts this park has already had: the sweep does not offer the
    session again before it. There is no limit — a resume parked on a DOWN
    dependency waits as long as the outage lasts.

    `due_now` is for a park that is NOT an outage: an orphaned resume (its
    process died) has nothing to wait out, and the recurring sweep that found
    it must drive it in the same pass."""
    # What the park waits on: a read's own tuple, else the typed error's, else
    # the model — a resume is a model turn, and a model DOWN records ['llm'].
    dependencies = list(
        pending.get("dependencies")
        or stamped_dependencies(error if isinstance(error, BaseException) else None)
        or (LLM,)
    )
    now = datetime.now(timezone.utc)
    backoff = 0 if due_now else retry_backoff_seconds(int(pending.get("attempts") or 0))
    stored = {
        **pending,
        "dependencies": dependencies,
        "last_error": str(error)[:500],
        "parked_at": now.isoformat(),
        "not_before": (now + timedelta(seconds=backoff)).isoformat(),
    }
    await repo.park_session_awaiting_resume(session_id, stored)
    await events.emit(
        "session.resume_parked",
        ref_id=session_id,
        payload={
            "after": stored["after"],
            "proposal_id": stored.get("proposal_id"),
            "item_id": stored.get("item_id"),
            "error": stored["last_error"],
            "transient": True,
            "attempts": stored["attempts"],
            # What the park waits on (always recorded since story 3).
            "dependencies": stored["dependencies"],
            "backoff_seconds": backoff,
            "not_before": stored["not_before"],
        },
        actor="system",
    )
    return stored


def continue_body(agent_id: str, requests: int, windows: int) -> str:
    """What the operator reads in the Inbox. One copy: the same sentence is the
    item body and the `question` the task's `task.blocked` event carries."""
    nth = f" (window {windows})" if windows > 1 else ""
    return (
        f"{agent_id} used its full request window{nth} — {requests} model "
        "requests — without finishing, and stopped rather than looping. The run "
        "is paused with its work intact. Continue to give it another window of "
        "the same size, or decline to end the run here."
    )


async def park_continuation(
    *,
    session_id: str,
    agent_id: str,
    requests: int,
    windows: int = 1,
    after: str = "task",
    task_id: str | None = None,
    state: dict | None = None,
) -> dict:
    """Park a run that hit its request window and ASK the operator to continue.

    Not a proposal: a window grants more agent thinking, it changes nothing in
    the world (every write the continued run reaches for still parks its own
    proposal), and gates belong on gated capabilities, never on triggers. So the
    approval surface is an `operator_item` of kind `"continue"`.

    `after` names the landing tail the continuation must re-enter — the same
    dispatch idiom `pending_resume` uses, and since slice 2 a real choice:
    `task` | `converse` | `orchestrator`.

    `state` is whatever the landing tail cannot rebuild from the transcript. Only
    the orchestrator has any: its round/stall/plan_approved/assign counters live
    in memory across `_process`, and a continuation that lost them would restart
    the round count and re-arm every backstop. The transcript alone carries the
    other two tails.

    The event goes before the item that authorises the continuation. `windows`
    is carried for honesty in the body only — there is no cumulative ceiling,
    because every window already costs an explicit human click.
    """
    marker = {
        "after": after,
        "windows": windows,
        "requests": requests,
        "task_id": task_id,
        "state": state,
        "parked_at": datetime.now(timezone.utc).isoformat(),
    }
    await repo.park_session_awaiting_continue(session_id, marker)
    body = continue_body(agent_id, requests, windows)
    await events.emit(
        "session.window_exhausted",
        ref_id=session_id,
        payload={"agent_id": agent_id, "requests": requests, "windows": windows,
                 "after": after, "task_id": task_id},
        actor="system",
    )
    item_id = "item_" + uuid.uuid4().hex[:12]
    await repo.create_operator_item(
        item_id=item_id, kind="continue", session_id=session_id,
        agent_id=agent_id, body=body, task_id=task_id,
    )
    return {"item_id": item_id, "body": body, "marker": marker}


async def park_stopped(
    *,
    session_id: str,
    agent_id: str,
    after: str = "task",
    task_id: str | None = None,
    state: dict | None = None,
    reason: str = "operator",
) -> dict:
    """Park a run the OPERATOR stopped, at the node boundary where it noticed.

    `reason` is "operator" (the button) or `hold.REASON` (an update waiting for
    the agents to finish) — the latter is the one kind of STOPPED session a
    background driver may resume (`api.hold.resume_held_sessions`), because the
    operator never pressed stop on it.

    A sibling of `park_continuation` — same marker shape (`after`, `task_id`,
    `state`), because the resume driver re-enters through the same three landing
    tails — and deliberately NOT the same wait. Two differences, both
    load-bearing:

    * The language is stop, not window. Nothing ran out; a human pressed stop.
    * There is NO operator_item. A continuation park asks the operator for
      something; this one is the operator's own act, and putting it in the
      Decisions Inbox would nag them about a decision they just made.

    The status flip clears `stop_requested` (see `repo.park_session_stopped`),
    and the event follows the park because it authorises nothing — it announces
    a stop that has already happened.
    """
    marker = {
        "after": after,
        "task_id": task_id,
        "state": state,
        "reason": reason,
        "stopped_at": datetime.now(timezone.utc).isoformat(),
    }
    await repo.park_session_stopped(session_id, marker)
    await events.emit(
        "session.stopped",
        ref_id=session_id,
        payload={"agent_id": agent_id, "after": after, "task_id": task_id,
                 "reason": reason},
        actor="operator" if reason == "operator" else "system",
    )
    return {"marker": marker}


async def park_if_transient(
    session_id: str, exc: BaseException, pending: dict
) -> dict | None:
    """Park when `exc` is TRANSIENT, else return None so the caller runs its
    unchanged semantic handling. One place decides, so no path can drift into
    retrying a real error (or terminating a survivable one)."""
    if classify_failure(exc) != "transient":
        return None
    return await park_resume(session_id, pending, exc)
