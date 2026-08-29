"""Clear the Decisions Inbox and requeue every email behind it (2026-08-16).

The operator's call after the 2026-08-15 fixes landed (Graphiti invalidation scope,
pinned sampling, reasoning-first dedupe, the qwen3.6-27b swap, the `Person`
ontology entry, and the cockpit's new graph curation): the 27 items sitting in
the inbox were triaged by agents reading a graph that was actively destroying
true facts, so their conclusions are worth less than a fresh pass.

**Clearing is not deleting, and requeueing is the whole point.** Every decision
here is recorded, every session closes through the one close seam with its
transcript kept, and each email goes back to UNPROCESSED so the dispatcher
re-triages it rather than the mail silently disappearing.

The unwind order is `routes.cancel_task`'s, because that route already solved
this exact problem for a task being wound down:

  1. every OPEN operator_item on the session is WITHDRAWN — nobody answered it,
     so it is never marked ANSWERED, and leaving it open asks the operator about work
     that no longer exists;
  2. every undecided proposal is WITHDRAWN through `gateway.dismiss_proposal`,
     which releases its folded siblings through the shared seam (a fold is a
     claim of coverage, never an outcome) and — critically — never resumes the
     drafting agent, which is what makes this different from a rejection;
  3. the session closes through `converse.close_session`;
  4. and only then does the work item move.

Ordering note: the requeue happens LAST, after every session is unwound. The
approval throttle opens the moment the pending count drops below the limit, so
the drain loop starts claiming again mid-script; leaving the flip to the end
means it can never claim an item whose session is still being closed.

Run: .venv/bin/python scripts/oneoff/clear_and_requeue_inbox_2026_08_16.py [--apply]
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from central_command.db import repo
from central_command import events
from central_command.gateway import gateway

REASON = (
    "cleared and requeued by the operator 2026-08-16: triaged against a "
    "knowledge graph that was retiring true facts, re-running under the fixed "
    "extraction stack"
)


async def main(apply: bool) -> int:
    proposals = await repo.list_proposals("AWAITING_HUMAN")
    questions = await repo.list_operator_items("OPEN")
    dismissals = await repo.list_work_items("DISMISS_PENDING", include_payload=False)

    sessions = {p["session_id"] for p in proposals} | {q["session_id"] for q in questions}
    print(f"proposals awaiting review : {len(proposals)}")
    print(f"open questions            : {len(questions)}")
    print(f"dismissal claims          : {len(dismissals)}")
    print(f"sessions to close         : {len(sessions)}")

    if not apply:
        print("\ndry run — pass --apply to perform it")
        return 0

    # 1+2+3: unwind each parked session through the existing seams.
    for q in questions:
        withdrawn = await repo.withdraw_operator_items_for_session(q["session_id"])
        for item_id in withdrawn:
            await events.emit(
                "operator.item_withdrawn",
                ref_id=item_id,
                payload={"reason": REASON, "session_id": q["session_id"]},
                actor="operator",
            )
        print(f"  withdrew question(s) {withdrawn} on {q['session_id']}")

    for p in proposals:
        await gateway.dismiss_proposal(p["session_id"], p["id"], reason=REASON)
        print(f"  dismissed {p['id']} ({p['session_id']})")

    # A question-only session has no proposal to carry it through the close
    # seam, so close it here. Sessions are never destroyed — this is the status
    # flip, transcript kept.
    from central_command.runtime.converse import close_session

    for session_id in sorted(sessions):
        status = await repo.session_status(session_id)
        if status in ("CLOSED", "DONE", "FAILED", None):
            continue
        await close_session(
            session_id, agent_id="inbox-triage", reason="dismissed", actor="operator",
        )
        print(f"  closed {session_id} (was {status})")

    # 4: the requeue. Everything the cleared items were attached to goes back
    # into the queue, attempts reset so the retry machinery treats this as a
    # first run rather than a continuation of one nobody asked for.
    by_session = await repo.work_items_for_sessions(sorted(sessions))
    item_ids = [d["id"] for d in dismissals] + [i["id"] for i in by_session.values()]

    requeued = await repo.requeue_items_for_reprocessing(item_ids)
    for item_id in requeued:
        await events.emit(
            "work.requeued", ref_id=item_id, payload={"reason": REASON}, actor="operator"
        )
    print(f"\nrequeued {len(requeued)} work item(s) to UNPROCESSED")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    sys.exit(asyncio.run(main(ap.parse_args().apply)))
