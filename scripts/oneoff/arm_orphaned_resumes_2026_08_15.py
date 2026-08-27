"""Arm the park record for the 12 resumes SIGKILLed on 2026-08-15.

cc-uvicorn hung on shutdown at 23:26:18 MDT and systemd killed it 15s later,
mid-way through a batch of answered triage questions. Every in-flight resume
died. `resume_park.arm` now writes the marker BEFORE the resume runs, so the
sweep can find the next one — but these twelve predate it and carry no marker
at all, which is exactly why nothing could see them.

This reconstructs the marker each session would have been armed with, from the
durable record (the answered item, or the recorded execution failure), and
PARKS it (AWAITING_RESUME) rather than merely arming it. Arming would restart
each session's `updated_at`, and the sweep's age gate — which exists to avoid
grabbing a resume that is still running — would then skip them for another 15
minutes. There is no such doubt here: the process that owned these runs is
gone. Parking is the same state `resume_park` reaches after a transient
failure, so `resume_sweep` at the next cc-uvicorn start drives them through the
ordinary path; this script drives nothing itself.

    .venv/bin/python scripts/oneoff/arm_orphaned_resumes_2026_08_15.py [--apply]
"""

import asyncio
import sys

from central_command.db import repo
from central_command.runtime import resume_park

# Owed an ANSWER: the operator answered, the resume never ran.
ANSWERED = [
    "sess_4e247f105067", "sess_9e04270eaa94", "sess_0c47e9107893",
    "sess_e31dd88cba17", "sess_b57ccb1a12ce", "sess_fc0251634159",
    "sess_7926d9e2d6c9", "sess_9d238fd8f962", "sess_6e9301c2b9f2",
    "sess_4cff7e43519c", "sess_5ae029cdd8e7",
]
# Owed a FAILURE REPORT: prop_a04c11f03ecf was approved and 400'd on an
# invented project key; the failure resume consulted jira-expert and died
# before it could redraft. Its run_state is still parked on the PROPOSE call,
# so the failure report is what that call is owed.
FAILED_PROPOSAL = ("sess_6193a92a31ff", "prop_a04c11f03ecf")


async def _answered_pending(session_id: str) -> dict | None:
    state = await repo.get_session_run_state(session_id) or {}
    call_id = state.get("tool_call_id")
    items = [
        i for i in await repo.list_operator_items("ANSWERED")
        if i["session_id"] == session_id and i.get("answered_at")
    ]
    if not call_id or not items:
        return None
    item = max(items, key=lambda i: i["answered_at"])
    return resume_park.pending_resume(
        kind="result", text=item["answer"], tool_call_id=call_id,
        after="answered", item_id=item["id"],
    )


async def _failed_pending(session_id: str, proposal_id: str) -> dict | None:
    state = await repo.get_session_run_state(session_id) or {}
    call_id = state.get("tool_call_id")
    events = [e for e in await repo.list_events(ref_id=proposal_id)
              if e["kind"] == "proposal.failed"]
    if not call_id or not events:
        return None
    p = events[-1]["payload"]
    completed = p.get("completed_actions") or []
    # Byte-identical to `gateway._record_execution_failure`'s report.
    report = (
        f"Execution FAILED at {p['failed_capability']}: {p['error']}. "
        f"Actions completed before the failure: "
        f"{'; '.join(completed) if completed else 'none'}."
    )
    return resume_park.pending_resume(
        kind="result", text=report, tool_call_id=call_id, after="failed",
        proposal_id=proposal_id,
        failure_summary=f"{p['failed_capability']}: {p['error']}",
    )


async def main(apply: bool) -> None:
    plan = [(sid, await _answered_pending(sid)) for sid in ANSWERED]
    plan.append((FAILED_PROPOSAL[0], await _failed_pending(*FAILED_PROPOSAL)))

    for session_id, pending in plan:
        session = await repo.get_session(session_id)
        status = session and session["status"]
        if status != "AWAITING_HUMAN":
            print(f"SKIP  {session_id}: status is {status}, not stranded")
            continue
        if pending is None:
            print(f"SKIP  {session_id}: could not reconstruct what it is owed")
            continue
        print(f"PARK  {session_id}  after={pending['after']}  "
              f"text={pending['text'][:70]!r}")
        if apply:
            await resume_park.park_resume(
                session_id, pending, "the resume died with its process"
            )

    if not apply:
        print("\ndry run — pass --apply to write, then restart cc-uvicorn "
              "(its startup resume_sweep drives them)")


if __name__ == "__main__":
    asyncio.run(main("--apply" in sys.argv))
