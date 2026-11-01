"""Re-run a session under the current charter (epic-cockpit-rerun R1-R3).

The old session closes with its record kept, its inputs run again in a fresh
session, and the two rows name each other. Which sessions qualify is decided by
the record: `KINDS` below is the one table, a row per kind saying where its inputs
live and which fresh-run entry starts it.

The order is the whole job, and each step is on the record before the next:

  1. `repo.claim_rerun`, one transaction: the old row closes naming a pre-minted
     new id, its undecided work is withdrawn, and whatever carries the inputs (a
     task, a work item) goes back to its queue state. Two presses claim once, and
     no decision or resume can land in between.
  2. The withdrawn proposals' folds are released, and the close is announced
     through the one close seam, with reason `rerun`.
  3. `session.rerun` goes on the log (ref the OLD id; ids only), before the run
     it authorises.
  4. The row's starter runs the fresh entry of its kind, which loads the charter
     that is current now. Nothing here awaits it.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from central_command import events
from central_command.db import repo

log = logging.getLogger(__name__)

NO_STORED_INPUTS = "no stored inputs to run it from"


class RerunUnknown(LookupError):
    """No such session."""


class RerunRefused(Exception):
    """The record says this session may not be re-run; nothing has changed. The
    message is the reason, in the operator's words."""


def _not(reason: str) -> dict:
    return {"eligible": False, "reason": reason}


def _opening_message(run_state: dict | None) -> str | None:
    """The operator's opening message: the first user-prompt part of the WHOLE
    transcript, since compaction may have archived it. Attachments were extracted
    to text before the turn existed, so the text carries them."""
    from central_command.runtime.durable import full_messages

    for message in full_messages(run_state or {}):
        for part in message.get("parts") or []:
            if part.get("part_kind") != "user-prompt":
                continue
            content = part.get("content")
            if isinstance(content, list):
                content = "\n".join(c for c in content if isinstance(c, str))
            return content.strip() if isinstance(content, str) and content.strip() else None
    return None


@dataclass
class _Run:
    """One session as `classify` found it, for the row's checks and starter."""

    session: dict
    task: dict | None = None
    item: dict | None = None
    opening: str | None = None


async def _start_task(run: _Run, new_session_id: str, actor: str) -> None:
    from central_command.api import routes
    from central_command.gateway.gateway import _detach

    # The starter reads the owed id from the record (`repo.claim_owed_rerun`), so a
    # restart while this waits on the agent's lane keeps it.
    _detach(routes._run_assigned_task_detached(await repo.get_task(run.task["id"]), actor))


async def _start_conversation(run: _Run, new_session_id: str, actor: str) -> None:
    from central_command.gateway.gateway import _detach
    from central_command.runtime.converse import start_conversation

    session = run.session

    async def opening_turn() -> None:
        try:
            await start_conversation(
                session["agent_id"], run.opening, session_id=new_session_id,
                rerun_of=session["id"], model_override=session.get("model_override"),
                thinking_override=session.get("thinking_override"),
            )
        except Exception:  # noqa: BLE001 — the run's own landing holds the record
            log.exception("re-run of conversation %s failed", session["id"])

    _detach(opening_turn())


async def _start_email(run: _Run, new_session_id: str, actor: str) -> None:
    from central_command.gateway.gateway import _detach
    from central_command.ingest import dispatcher

    async def dispatch() -> None:
        try:
            # Skips the valves, as the operator naming one item does, and honours
            # the thread lock. If the thread is busy the item stays queued for the
            # dispatcher, and the owed id is on the record either way.
            await dispatcher.dispatch_item(run.item["id"])
        except Exception:  # noqa: BLE001 — process_claimed keeps its own record
            log.exception("re-run of work item %s failed", run.item["id"])

    _detach(dispatch())


@dataclass(frozen=True)
class RerunKind:
    """One row: where this kind's inputs live, what starts it or why not, and the
    kind's own checks against the record. `check` returns the reason this run may
    not go again, or None; `waits_for_children` refuses while a task the session
    assigned is not terminal (and makes the claim re-check it in SQL)."""

    name: str
    inputs: str
    start: Callable[[_Run, str, str], Awaitable[None]] | None = None
    refusal: str | None = None
    check: Callable[[_Run], Awaitable[str | None]] | None = None
    waits_for_children: bool = False


async def _check_task(run: _Run) -> str | None:
    from central_command.runtime.roster import taskable_agent_ids

    if run.task.get("agent_id") not in await taskable_agent_ids():
        return f"{run.task.get('agent_id')} is not on the active roster"
    return None


async def _check_conversation(run: _Run) -> str | None:
    from central_command.runtime.roster import conversational_agent_ids

    if run.opening is None:
        return NO_STORED_INPUTS
    if run.session["agent_id"] not in await conversational_agent_ids():
        return f"{run.session['agent_id']} is not on the active roster"
    return None


async def _check_email(run: _Run) -> str | None:
    if run.item.get("state") in ("CLAIMED", "UNPROCESSED"):
        return "its email is already queued or being processed"
    return None


KINDS: dict[str, RerunKind] = {row.name: row for row in (
    RerunKind("task", "task.instructions", _start_task, check=_check_task),
    # Refused too while a child is not terminal, or the fresh run assigns the work
    # twice. The roster cards are re-read live by design.
    RerunKind("orchestrator", "task.instructions, through run_project", _start_task,
              waits_for_children=True),
    # The opening message only (A2): the agent's answers shaped the later turns,
    # so replaying them would not be the same conversation.
    RerunKind("conversation", "the first user-prompt part of the transcript",
              _start_conversation, check=_check_conversation),
    # Recurrence and pending-proposal context are recomputed per run by design.
    RerunKind("email", "work_item.payload", _start_email, check=_check_email),
    # A lane an agent opened to unblock its task: the agent wrote its first words.
    RerunKind("discussion", "none", refusal=NO_STORED_INPUTS),
    RerunKind("document", "none", refusal=NO_STORED_INPUTS),
    RerunKind("wiki-repair", "none", refusal=NO_STORED_INPUTS),
    # Coach, heartbeat, steward and consult sessions have no task and no item.
    RerunKind("agent-run", "none", refusal=NO_STORED_INPUTS),
)}

_ITEM_KINDS = {"email": "email", "document": "document", "wiki_repair": "wiki-repair"}
UNLISTED = RerunKind("unlisted", "none", refusal="this kind of run cannot be re-run")


async def classify(session: dict) -> tuple[RerunKind, _Run]:
    """Which row this session is, read off the record: the same facts the read-only
    notice's kind comes from (`nerve_gateway._run_session_context`)."""
    run = _Run(session)
    if session.get("mode") == "conversation":
        if await repo.get_operator_item_by_discussion(session["id"]):
            return KINDS["discussion"], run
        run.opening = _opening_message(await repo.get_session_run_state(session["id"]))
        return KINDS["conversation"], run
    run.task = await repo.task_for_session(session["id"])
    if run.task is not None:
        return KINDS["orchestrator" if run.task.get("agent_id") == "orchestrator" else "task"], run
    items = await repo.work_items_for_session(session["id"])
    if not items:
        return KINDS["agent-run"], run
    run.item = items[0]
    return KINDS.get(_ITEM_KINDS.get(run.item.get("kind") or "email", ""), UNLISTED), run


async def _refusal(kind: RerunKind, run: _Run) -> str | None:
    """The record's reason this run may not go again, from the kind's own row."""
    if kind.refusal:
        return kind.refusal
    session_id = run.session["id"]
    if await repo.session_has_landing_proposal(session_id):
        return "decided work is still landing; wait for it"
    if kind.waits_for_children and await repo.session_has_live_children(session_id):
        return "children still running; wait for them or cancel them"
    return await kind.check(run) if kind.check else None


async def eligibility(session: dict) -> dict:
    """`{"eligible", "reason"}` for this session: what the composer shows and what
    `rerun_session` enforces, so the button and the action cannot disagree."""
    from central_command.runtime import hold

    if hold.active:
        return _not("an update is waiting for the agents to finish")
    if session.get("rerun_by"):
        return _not(f"already re-run as {session['rerun_by']}")
    if session.get("status") == "RUNNING":
        return _not("it is running; stop it first")
    reason = await _refusal(*await classify(session))
    return _not(reason) if reason else {"eligible": True, "reason": None}


async def rerun_session(session_id: str, actor: str = "operator") -> dict:
    """Close this session and run its inputs again in a fresh one. Returns the new
    session's id at once; raises `RerunUnknown` or `RerunRefused`."""
    from central_command.gateway.gateway import release_folds_for
    from central_command.runtime.converse import close_session

    session = await repo.get_session(session_id)
    if session is None:
        raise RerunUnknown(f"no session {session_id}")
    state = await eligibility(session)
    if not state["eligible"]:
        raise RerunRefused(f"session {session_id} cannot be re-run: {state['reason']}")
    kind, run = await classify(session)
    agent_id = session["agent_id"]
    new_session_id = "sess_" + uuid.uuid4().hex[:12]
    task_id = run.task["id"] if run.task else None
    item_id = run.item["id"] if run.item else None

    claimed = await repo.claim_rerun(
        session_id, new_session_id, task_id, work_item_id=item_id,
        refuse_live_children=kind.waits_for_children,
    )
    if claimed is None:
        raise RerunRefused(
            f"session {session_id} cannot be re-run: it changed while the re-run "
            "was being claimed")
    for proposal_id in claimed["proposals"]:
        await release_folds_for(proposal_id, "covering proposal withdrawn")
    await close_session(session_id, agent_id=agent_id, reason="rerun", actor=actor)

    charter = await repo.current_charter(agent_id)
    links = {"task_id": task_id} if task_id else {"work_item_id": item_id} if item_id else {}
    await events.emit(
        "session.rerun",
        ref_id=session_id,
        payload={"new_session_id": new_session_id, **links,
                 "charter_version": charter["version"] if charter else 0},
        actor=actor,
    )
    await kind.start(run, new_session_id, actor)
    return {"sessionId": new_session_id, "agentId": agent_id, "rerunOf": session_id,
            "taskId": task_id, "workItemId": item_id}
