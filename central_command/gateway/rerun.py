"""Re-run a session under the current charter (epic-cockpit-rerun R1-R3).

The old session closes with its record kept, its inputs run again in a fresh
session, and the two rows name each other. Only a task run qualifies so far; the
other kinds refuse with "not yet".

The order is the whole job, and each step is on the record before the next:

  1. `repo.claim_rerun`: the old row names a pre-minted new id and the task goes
     back to ASSIGNED, in one transaction. Two presses claim once.
  2. The old session's undecided work is withdrawn and it closes through the one
     close seam, with reason `rerun`.
  3. `session.rerun` goes on the log (ref the OLD id; ids only), before the run
     it authorises.
  4. The normal detached starter runs the task. It is a fresh run, so it loads
     the charter that is current now. Nothing here awaits it.
"""

from __future__ import annotations

import uuid

from central_command import events
from central_command.db import repo


class RerunUnknown(LookupError):
    """No such session."""


class RerunRefused(Exception):
    """The record says this session may not be re-run; nothing has changed. The
    message is the reason, in the operator's words."""


def _not(reason: str) -> dict:
    return {"eligible": False, "reason": reason}


async def eligibility(session: dict) -> dict:
    """`{"eligible", "reason"}` for this session: what the composer shows and what
    `rerun_session` enforces, so the button and the action cannot disagree."""
    from central_command.runtime.roster import taskable_agent_ids

    if session.get("rerun_by"):
        return _not(f"already re-run as {session['rerun_by']}")
    if session.get("status") == "RUNNING":
        return _not("it is running; stop it first")
    if session.get("mode") == "conversation":
        return _not("re-running a conversation is not built yet")
    task = await repo.task_for_session(session["id"])
    if task is None:
        return _not("only a task run can be re-run yet")
    if task.get("agent_id") == "orchestrator":
        return _not("re-running an orchestrator project is not built yet")
    if await repo.session_has_landing_proposal(session["id"]):
        return _not("decided work is still landing; wait for it")
    if task.get("agent_id") not in await taskable_agent_ids():
        return _not(f"{task.get('agent_id')} is not on the active roster")
    return {"eligible": True, "reason": None}


async def rerun_session(session_id: str, actor: str = "operator") -> dict:
    """Close this session and run its task again in a fresh one. Returns the new
    session's id at once; raises `RerunUnknown` or `RerunRefused`."""
    from central_command.api import routes
    from central_command.gateway.gateway import _detach, withdraw_session_work
    from central_command.runtime.converse import close_session

    session = await repo.get_session(session_id)
    if session is None:
        raise RerunUnknown(f"no session {session_id}")
    state = await eligibility(session)
    if not state["eligible"]:
        raise RerunRefused(f"session {session_id} cannot be re-run: {state['reason']}")
    task = await repo.task_for_session(session_id)
    agent_id = session["agent_id"]
    new_session_id = "sess_" + uuid.uuid4().hex[:12]

    if not await repo.claim_rerun(session_id, new_session_id, task["id"]):
        raise RerunRefused(
            f"session {session_id} cannot be re-run: it changed while the re-run "
            "was being claimed")
    await withdraw_session_work(session_id)
    await close_session(session_id, agent_id=agent_id, reason="rerun", actor=actor)

    charter = await repo.current_charter(agent_id)
    await events.emit(
        "session.rerun",
        ref_id=session_id,
        payload={"new_session_id": new_session_id, "task_id": task["id"],
                 "charter_version": charter["version"] if charter else 0},
        actor=actor,
    )
    _detach(routes._run_assigned_task_detached(await repo.get_task(task["id"]), actor))
    return {"sessionId": new_session_id, "agentId": agent_id, "rerunOf": session_id,
            "taskId": task["id"]}
