"""Who is blocking a quiet clarification discussion — the tier-3 stall test.

A tier-3 question opens a parallel conversation and parks the task until the
agent calls `conclude_discussion`. Nothing watched that loop, so a discussion
nobody concluded left its task blocked forever. This is the classifier the
sweep uses to decide which of the two remedies a quiet lane needs: flag it at
the operator (S1) or nudge the agent (S2).

**The test is WHO SPOKE LAST, not "has the operator ever replied."** They look
equivalent on a fresh discussion and diverge exactly where it matters: after a
nudge, an agent may legitimately answer that it still needs something. The
operator HAS replied at that point, so the weaker test would keep calling it
S2 and nudge — forever — an agent that is correctly waiting on a human. Read
by who spoke last, that same reply flips the lane to S1 and points back at the
operator, which is the truth. (The reverse is why S2 exists at all: the
operator answered, and from their side it looks handled while the task stays
parked.)

The two idempotence stamps are current state, not history, like the
heartbeat's own `last_fired_at`: "this lane was already acted on during THIS
quiet window". Comparing them against the session's `updated_at` rather than
against `now` is what lets a lane that goes quiet, moves, then goes quiet
again be flagged a second time instead of being silently ignored forever.

Pure on purpose — plain dicts and a datetime, no database, no I/O. The sweep
does the reading and the writing; this decides, and is unit-tested without a
Postgres.
"""

from __future__ import annotations

from datetime import datetime, timedelta

# Which stamp proves the remedy was already applied in this quiet window. S1's
# remedy is a flag, S2's is a nudge, and they are counted separately: a lane
# nudged as S2 that then flips to S1 must still be flagged.
_STAMP = {"S1": "stalled_at", "S2": "nudged_at"}


def classify_discussion_stall(
    *,
    item: dict,
    session: dict,
    last_turn_by: str,
    now: datetime,
    stall_hours: int,
) -> str | None:
    """Return "S1" (waiting on the operator), "S2" (waiting on the agent), or
    None when this discussion needs nothing right now.

    `item` is an `operator_item` row (status + the two stamps), `session` the
    discussion session row (its `updated_at` is when the lane last moved), and
    `last_turn_by` is "agent" or "operator" — the author of the newest turn in
    the transcript, including the seeded opening question.
    """
    # An answered item is not blocking anything; the task has already resumed.
    if item.get("status") != "OPEN":
        return None

    moved = session.get("updated_at")
    if moved is None or now - moved < timedelta(hours=stall_hours):
        return None

    stall = "S1" if last_turn_by == "agent" else "S2"

    stamp = item.get(_STAMP[stall])
    if stamp is not None and stamp >= moved:
        # Already acted on since the lane last moved — acting again would be
        # the nag this whole mechanism exists to avoid.
        return None
    return stall
