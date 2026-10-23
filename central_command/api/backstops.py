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
NO_ASSIGNEE = "no-assignee"


@dataclass(frozen=True)
class Provenance:
    """Who made a task and where it sits in a chain of agent-created tasks."""

    created_by: str
    parent_task_id: str | None = None
    # Agent ids, root first, including the immediate creator; empty for an
    # operator or heartbeat creation.
    chain: tuple[str, ...] = ()
    # The row a refusal hangs on: the parent task, else the creating session.
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
    return Provenance(f"{AGENT_PREFIX}{creator}", None, (creator,), parent_session_id)


def check(prov: Provenance, agent_id: str | None) -> Refusal | None:
    """The first backstop the creation trips, or None. Operator and heartbeat
    creations are roots and are never refused here."""
    if not prov.agent_created:
        return None
    if agent_id is None:
        return Refusal(
            NO_ASSIGNEE,
            "an agent-created task must name an assignee; an unassigned task "
            "is the operator's to route",
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
    return None


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
