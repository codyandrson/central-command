"""Task backstops — the provenance and the loop breakers of agent-created tasks.

`routes.create_and_run_task` is the one task-create path; this module holds what
it checks and records there, so no caller carries its own copy. It lives under
`api/`, never `runtime/`: a refusal is control-plane policy, and the trust
boundary keeps `runtime/` out of the tier that decides it.

Provenance is SERVER-DERIVED. The creator is the agent of the creating session's
row, the parent task is the task that session is running (`run.py` links the
session to its task at run start, so `repo.task_for_session` resolves it), and
the chain is the parent task's stored chain plus the creator. Nothing comes from
tool arguments: a value an agent can write is an agent claim.

A refusal is LOUD and writes no task row: `task.refused` is emitted through
`events.emit()` before anything runs, and the caller turns the returned
`Refusal` into a 422 whose text names the reason.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from central_command import events
from central_command.config import settings
from central_command.db import repo

OPERATOR = "operator"
AGENT_PREFIX = "agent:"
HEARTBEAT_PREFIX = "heartbeat:"

DEPTH = "depth"
CYCLE = "cycle"
SELF = "self"
FAN_OUT = "fan-out"
NO_ASSIGNEE = "no-assignee"


@dataclass(frozen=True)
class Provenance:
    """Who made a task and where it sits in a chain of agent-created tasks."""

    created_by: str
    parent_task_id: str | None = None
    # Agent ids, root first, including the immediate creator; empty for an
    # operator or heartbeat creation.
    chain: tuple[str, ...] = ()
    # The row a refusal hangs on, and the key the fan-out count is read by: the
    # parent task (the creator's own, or the ancestor an inherited chain runs
    # under), else the creating session.
    ref_id: str | None = None

    @property
    def creator_agent(self) -> str | None:
        if self.created_by.startswith(AGENT_PREFIX):
            return self.created_by[len(AGENT_PREFIX):]
        return None

    @property
    def agent_created(self) -> bool:
        return self.creator_agent is not None


@dataclass(frozen=True)
class Refusal:
    reason: str
    message: str
    payload: dict = field(default_factory=dict)


async def derive_provenance(actor: str, parent_session_id: str | None) -> Provenance:
    """Creator, parent task and chain from server-side facts only.

    A creating session means an agent made this task (the Executor's proposing
    session, the orchestration driver's session): the creator is that session's
    agent, whatever `actor` says — while the gate is on `actor` is the approver
    (GATE-033). With no session the caller is the operator or a heartbeat and
    `actor` names which; either starts a chain at the root.
    """
    if not parent_session_id:
        return Provenance(created_by=actor)
    session = await repo.get_session(parent_session_id)
    if not session or not session.get("agent_id"):
        # An agent-created task whose creator cannot be named has no provenance;
        # refuse rather than record an unattributed creation.
        raise LookupError(f"creating session {parent_session_id!r} has no agent")
    creator = session["agent_id"]
    parent = await repo.task_for_session(parent_session_id)
    if parent:
        chain = tuple(parent.get("creator_chain") or ()) + (creator,)
        return Provenance(f"{AGENT_PREFIX}{creator}", parent["id"], chain, parent["id"])
    # A task-less session (chat, triage, a consult specialist) inherits the chain
    # of the nearest ancestor session that has a task, so a consult inside a
    # task chain cannot reset the depth or cycle count. No such ancestor: it
    # starts a chain of its own.
    # The ancestor task is also the parent: the specialist's creations count
    # toward that task's fan-out and a refusal hangs on it.
    inherited, ancestor_task = await _inherited_chain(parent_session_id)
    return Provenance(f"{AGENT_PREFIX}{creator}", ancestor_task,
                      inherited + (creator,), ancestor_task or parent_session_id)


_ANCESTOR_HOPS = 16


async def _inherited_chain(session_id: str) -> tuple[tuple[str, ...], str | None]:
    """The stored chain and id of the nearest ancestor session's task, or
    `((), None)` when no ancestor runs one.

    Ancestry is `session.parent_session_id` (an orchestrated child) and, for a
    consult specialist's session, the asking session stamped on its proposal's
    `origin`. Bounded and cycle-safe: the walk is over server-side rows only.
    """
    seen = {session_id}
    current = session_id
    for _ in range(_ANCESTOR_HOPS):
        row = await repo.get_session(current)
        nxt = (row or {}).get("parent_session_id") or (
            await repo.consult_caller_session(current))
        if not nxt or nxt in seen:
            return (), None
        seen.add(nxt)
        task = await repo.task_for_session(nxt)
        if task:
            return tuple(task.get("creator_chain") or ()), task["id"]
        current = nxt
    return (), None


async def check(prov: Provenance, agent_id: str | None) -> Refusal | None:
    """The first backstop the creation trips, or None. Operator and heartbeat
    creations are roots and are never refused here.

    Every refusal that has an alternative names it: the taskable teammates the
    creator may use instead, from the same roster source the seam's taskable
    check reads, minus the creator and everyone already in the chain."""
    if not prov.agent_created:
        return None
    if agent_id is None:
        return Refusal(
            NO_ASSIGNEE,
            "an agent-created task must name an assignee; an unassigned task "
            "is the operator's to route",
        )
    creator = prov.creator_agent
    if agent_id == creator:
        return Refusal(
            SELF,
            f"you ARE {agent_id} — a task assigned to yourself adds nothing. "
            + await _alternatives(prov),
        )
    if agent_id in prov.chain:
        return Refusal(
            CYCLE,
            f"{agent_id!r} is already in this task chain "
            f"({' -> '.join(prov.chain)}) — assigning back to it would cycle. "
            + await _alternatives(prov),
        )
    cap = settings.task_max_depth
    if len(prov.chain) > cap:
        return Refusal(
            DEPTH,
            f"this task chain would be {len(prov.chain)} agent-created tasks "
            f"deep ({' -> '.join(prov.chain)}) — depth is capped at {cap}. "
            f"Do the work yourself or report back; the operator can create "
            f"a fresh task.",
        )
    limit = settings.task_fanout_limit
    # Counted by the creating TASK, so a retry-parked re-run in a new session
    # continues the count; a creator with no task (chat, triage) is counted by
    # its session. Every row counts, whatever its status: a runaway's children
    # that already failed are still children.
    created = await repo.count_child_tasks(
        parent_task_id=prov.parent_task_id,
        parent_session_id=None if prov.parent_task_id else prov.ref_id,
    )
    if created >= limit:
        scope = "task" if prov.parent_task_id else "conversation"
        return Refusal(
            FAN_OUT,
            f"this {scope} has already created {created} tasks — fan-out is "
            f"capped at {limit}. Do the rest yourself or report back; the "
            f"operator can create a fresh task.",
            {"limit": limit, "created": created},
        )
    return None


async def _alternatives(prov: Provenance) -> str:
    from central_command.runtime.roster import taskable_agent_ids

    options = [a for a in await taskable_agent_ids() if a not in prov.chain]
    if not options:
        return ("No other taskable teammate is outside this chain: do the "
                "work yourself or report back.")
    return "Taskable teammates you may assign instead: " + ", ".join(options) + "."


async def refuse(prov: Provenance, agent_id: str | None, refusal: Refusal,
                 actor: str) -> None:
    """Record the refusal on the event log, before anything it concerns runs."""
    await events.emit(
        "task.refused",
        ref_id=prov.ref_id,
        payload={
            "reason": refusal.reason,
            "chain": list(prov.chain),
            "creator": prov.created_by,
            "agent_id": agent_id,
            **refusal.payload,
        },
        actor=actor,
    )
