"""The operator's calendar for one day — read, conflict-checked, no judgment.

A SIBLING of `ea_report`, deliberately. `ea_report.snapshot()` is pure Postgres:
its contract is "a property of the record", so two callers at the same instant
see the same thing and nothing it reports can fail because a third party is
down. A calendar is the opposite — an outside read over HTTP that can time out.
Keeping them apart means a calendar outage degrades one key of the EA's brief
instead of taking the whole snapshot with it. Do NOT put HTTP in ea_report.

Conflicts are computed HERE, in Python, not asked of a model: overlapping
meetings are arithmetic, and arithmetic an operator acts on should not come out
of a language model. Two events do not conflict if either is all-day (a
day-long marker is not a meeting) or if the operator DECLINED it — a declined
invite is not a commitment.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from central_command.config import settings
from central_command.integrations import calendar_facade


def _dt(value) -> datetime | None:
    """Parse an RFC3339 stamp; all-day dates parse too (naive midnight)."""
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _conflicts(events: list[dict]) -> list[dict]:
    """Overlapping COMMITMENTS, pairwise on the sorted list.

    Back-to-back is not a conflict: the test is `next.start < current.end`,
    strictly. All-day events and declined invitations are skipped entirely.
    """
    real = [
        e for e in events
        if not e.get("all_day") and not e.get("declined") and _dt(e.get("start"))
        and _dt(e.get("end"))
    ]
    real.sort(key=lambda e: _dt(e["start"]))

    out: list[dict] = []
    for current, following in zip(real, real[1:]):
        if _dt(following["start"]) < _dt(current["end"]):
            out.append({
                "between": [current.get("summary"), following.get("summary")],
                "overlap_from": following["start"],
                "until": current["end"],
            })
    return out


async def day(offset_days: int = 0) -> dict:
    """One local day of the operator's calendar.

    offset 0 = from NOW to the end of local today (what is still ahead of
    them); offset >= 1 = that whole local day, midnight to midnight.
    """
    if not settings.calendar_facade_token:
        # Not configured is not an error, and it makes NO call: an unconfigured
        # deployment must not spend a request to be told so.
        return {"available": False, "reason": "not configured"}

    tz = ZoneInfo(settings.ea_timezone)
    now = datetime.now(tz)
    midnight = datetime.combine((now + timedelta(days=offset_days)).date(),
                                time.min, tzinfo=tz)
    start = now if offset_days <= 0 else midnight
    end = midnight + timedelta(days=1)

    out = await calendar_facade.list_events(start.isoformat(), end.isoformat())
    events = out["events"]
    # All-day events are excluded here, not just below: a real all-day start is
    # a bare DATE, which parses NAIVE, and min() over naive + aware raises —
    # one vacation marker beside one flight took out the whole read. "First
    # event" means the first timed commitment anyway.
    starts = [s for s in (_dt(e.get("start")) for e in events
                          if not e.get("all_day")) if s]
    return {
        "available": True,
        "timezone": settings.ea_timezone,
        "window": {"from": start.isoformat(), "to": end.isoformat()},
        "event_count": len(events),
        "first_event_at": min(starts).isoformat() if starts else None,
        "truncated": out["truncated"],
        "events": events,
        "conflicts": _conflicts(events),
    }
