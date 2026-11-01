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

Availability FAILS CLOSED. `available: True` means a complete, successful list,
so a day with no events on it is a free day; every other answer is
`available: False` with a reason, and the callers say "unknown", never "free". A
truncated window, a provider that answered without a list and an unconfigured
provider are all a NO. An outage is not: it raises, so `read_calendar` parks
under the outage doctrine and the EA's brief records the error.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from central_command.config import settings
from central_command.contract.failures import is_down
from central_command.integrations import calendar_facade

TRUNCATED_REASON = "truncated: more events than the window shows"


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


def day_name(offset_days: int = 0) -> str:
    """The local day an offset names, for words that must say WHICH day."""
    named = datetime.now(ZoneInfo(settings.ea_timezone)) + timedelta(days=offset_days)
    return named.strftime("%A %Y-%m-%d")


def failure_reason(e: Exception) -> str:
    """A failed read as a closed answer's reason. The provider's words are DATA:
    quoted and bounded."""
    said = str(e).strip()[:300]
    return f"calendar read failed ({type(e).__name__})" + (f': "{said}"' if said else "")


async def day(offset_days: int = 0) -> dict:
    """One local day of the operator's calendar.

    offset 0 = from NOW to the end of local today (what is still ahead of
    them); offset >= 1 = that whole local day, midnight to midnight.

    Raises only for an outage (and for a bug); see the module docstring.
    """
    if not calendar_facade.provider_configured():
        # Not configured is not an error, and it makes NO call: an unconfigured
        # deployment must not spend a request to be told so.
        return {"available": False, "reason": "not configured"}

    tz = ZoneInfo(settings.ea_timezone)
    now = datetime.now(tz)
    midnight = datetime.combine((now + timedelta(days=offset_days)).date(),
                                time.min, tzinfo=tz)
    start = now if offset_days <= 0 else midnight
    end = midnight + timedelta(days=1)

    try:
        out = await calendar_facade.list_events(start.isoformat(), end.isoformat())
    except calendar_facade.CalendarFacadeError as e:
        # The same judge as the tools' retry, cause chain included: a façade
        # timeout's own message can carry no marker at all, and answering one
        # here would turn an outage into a NO.
        if is_down(e):
            raise
        return {"available": False, "reason": failure_reason(e)}
    if out["truncated"]:
        return {"available": False, "reason": TRUNCATED_REASON}
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
        "truncated": False,
        "events": events,
        "conflicts": _conflicts(events),
    }
