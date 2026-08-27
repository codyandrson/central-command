"""The EA's deterministic snapshot — the numbers, with nobody's judgment on them.

`snapshot(since)` composes existing `repo` reads into one JSON-serialisable
dict. The EA's brief embeds it as a fenced ```json block, so the digest's
figures cost ZERO tool calls and cannot be a model's arithmetic. The agent's
job is to say what they MEAN; `read_team_activity` exists for the follow-up
question the block cannot answer. Both, with different jobs.

Deliberately NOT `dispatcher.status()`. That reports process-local state (is
THIS process's drain loop running), so a snapshot taken in the API process and
one taken anywhere else would disagree about the same instant. Everything here
comes out of Postgres, so the snapshot is a property of the record rather than
of whoever asked.

`since` is a real window boundary, not a page cursor: events are filtered on
`created_at`, so a snapshot describes an interval of the operator's day. The
count reads beside them (queue depths, ledger states) are CURRENT STATE, not
windowed — the digest needs both, and the block labels which is which.
"""

from __future__ import annotations

from datetime import datetime, timezone

# THE fixed activity vocabulary. Both readers of "what happened" use this one
# tuple — the snapshot below and the agent's `read_team_activity` tool — so a
# follow-up read can never cover a different set of kinds than the digest the
# operator is looking at.
ACTIVITY_KINDS: tuple[str, ...] = (
    "proposal.created",
    "proposal.decided",
    "task.completed",
    "task.blocked",
    "task.review",
    "agent.asked",
    "gap.declared",
    "work.enrolled",
)

# How many individual events of each kind ride the block. The block is a
# summary the operator reads through an agent, not an export: counts are
# complete, examples are a taste, and `truncated` says when there were more.
EXAMPLES_PER_KIND = 5


def _iso(value) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else (value or None)


async def activity_since(since: datetime) -> list[dict]:
    """Every ACTIVITY_KINDS event at or after `since`, oldest first."""
    from central_command.db import repo

    rows = await repo.list_events_of_kinds(list(ACTIVITY_KINDS))
    return [r for r in rows if r["created_at"] >= since]


async def snapshot(since: datetime) -> dict:
    """The whole picture at one instant, over the window `since`..now."""
    from central_command.db import repo

    events = await activity_since(since)

    grouped: dict[str, list[dict]] = {kind: [] for kind in ACTIVITY_KINDS}
    for row in events:
        grouped.setdefault(row["kind"], []).append(row)

    by_kind: dict[str, dict] = {}
    for kind, rows in grouped.items():
        # The MOST RECENT examples, kept in chronological order. Taking the
        # first N instead would show the operator the oldest events in the
        # window and label the newest as "truncated" — the wrong half of a
        # window whose whole purpose is "what just changed".
        shown = rows[-EXAMPLES_PER_KIND:] if rows else []
        by_kind[kind] = {
            "count": len(rows),
            "truncated": max(0, len(rows) - len(shown)),
            "examples": [{
                "event_id": r["id"],
                "ref_id": r["ref_id"],
                "actor": r["actor"],
                "at": _iso(r["created_at"]),
                # One human-facing line per kind, chosen from the payload the
                # emitter actually writes. Never the whole payload: a block
                # that pastes every field is the log browser STATUS rejected.
                "summary": _summarise(kind, r.get("payload") or {}),
            } for r in shown],
        }

    open_items = await repo.list_operator_items("OPEN")
    awaiting = await repo.list_proposals("AWAITING_HUMAN")
    discussions = await repo.list_open_discussions()

    return {
        "window": {"since": _iso(since),
                   "until": _iso(datetime.now(timezone.utc))},
        # CURRENT STATE (not windowed) — the depths that matter right now.
        "queues": {
            "proposals_awaiting_decision": await repo.count_awaiting_human(),
            "work_items_in_flight": await repo.count_in_flight(),
            "dismissals_awaiting_confirmation": await repo.count_dismiss_pending(),
            "operator_items_open": len(open_items),
            "discussions_open": len(discussions),
        },
        "work_ledger_by_state": await repo.ledger_counts(),
        "tasks_by_status": await repo.task_counts(),
        # WINDOWED — what changed since `since`.
        "activity": by_kind,
        "awaiting_decision": [
            {"proposal_id": p["id"], "agent_id": p["agent_id"],
             "intent": (p.get("intent") or "")[:300],
             "created_at": _iso(p["created_at"])}
            for p in awaiting
        ],
        "open_operator_items": [
            {"item_id": i["id"], "kind": i.get("kind"),
             "agent_id": i.get("agent_id"), "task_id": i.get("task_id"),
             "body": (i.get("body") or "")[:300],
             "has_discussion": bool(i.get("discussion_session_id")),
             "created_at": _iso(i.get("created_at"))}
            for i in open_items
        ],
        "open_discussions": [
            {"item_id": d["id"], "agent_id": d.get("agent_id"),
             "task_id": d.get("task_id"),
             "discussion_session_id": d.get("discussion_session_id"),
             "last_moved_at": _iso(d.get("session_updated_at"))}
            for d in discussions
        ],
    }


def _summarise(kind: str, payload: dict) -> str:
    """One line describing an event, from the fields its emitter really writes."""
    if kind == "proposal.decided":
        note = payload.get("feedback")
        return f"{payload.get('verdict', '?')}" + (f" — “{str(note)[:160]}”" if note else "")
    for key in ("intent", "question", "title", "outcome", "need", "subject", "summary"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return f"{key}: {value.strip()[:200]}"
    agent = payload.get("agent_id")
    return f"agent: {agent}" if agent else ""
