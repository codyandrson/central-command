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

Free time is computed here too (the EA planning design, D2 and D5): the free
stretches inside the working window (`plan_day_start` to `plan_day_end`, local;
today's starts at now) after every timed commitment the conflict check counts
is subtracted. Events the assistant created or adopted (work blocks and
placeholders) are timed events like any other, so they are occupied time. The
ledger that says which events those are is Postgres and is joined by the caller
(`heartbeat/actions._annotate_plan_kinds`); this module stays free of database
access. Only an `available` day carries `free_time`: an unknown day has no free
stretches to report, so the key is absent rather than empty.

Availability FAILS CLOSED. `available: True` means a complete, successful list,
so a day with no events on it is a free day; every other answer is
`available: False` with a reason, and the callers say "unknown", never "free". A
truncated window, a provider that answered without a list and an unconfigured
provider are all a NO. An outage is not: it raises, so `read_calendar` parks
under the outage doctrine and the EA's brief records the error.

`week()` (DL-221 step 5) is the coming week for the weekly plan: one line per
day, each read through `day()` in turn, so the week's arithmetic is the day's
and an unknown day stays unknown.
"""

from __future__ import annotations

import itertools
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from central_command.config import settings
from central_command.contract.failures import is_down
from central_command.integrations import calendar_facade

TRUNCATED_REASON = "truncated: more events than the window shows"


def _dt(value) -> datetime | None:
    """Parse an RFC3339 stamp; all-day dates parse too (naive midnight)."""
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _conflicts(events: list[dict]) -> list[dict]:
    """Overlapping COMMITMENTS, pairwise on the sorted list.

    Back-to-back is not a conflict: the test is `next.start < current.end`,
    strictly. All-day events and declined invitations are skipped entirely.
    """
    real = [
        e
        for e in events
        if not e.get("all_day")
        and not e.get("declined")
        and _dt(e.get("start"))
        and _dt(e.get("end"))
    ]
    real.sort(key=lambda e: _dt(e["start"]))

    out: list[dict] = []
    for current, following in itertools.pairwise(real):
        if _dt(following["start"]) < _dt(current["end"]):
            out.append(
                {
                    "between": [current.get("summary"), following.get("summary")],
                    "overlap_from": following["start"],
                    "until": current["end"],
                }
            )
    return out


def _clock(value: str) -> time:
    """A configured "HH:MM" as a time; refuses anything else loudly."""
    hours, _, minutes = str(value).strip().partition(":")
    try:
        return time(int(hours), int(minutes))
    except ValueError as e:
        raise ValueError(f"working-window time must be HH:MM, got {value!r}") from e


def working_window(local_date, now: datetime | None = None) -> tuple[datetime, datetime]:
    """The working window of one local day, `plan_day_start` to `plan_day_end`
    in `ea_timezone`; when `now` falls on that day after the start, the window
    starts at `now`. A window whose start is not before its end is empty, and
    `free_time` answers no stretch for it."""
    tz = ZoneInfo(settings.ea_timezone)
    start = datetime.combine(local_date, _clock(settings.plan_day_start), tzinfo=tz)
    end = datetime.combine(local_date, _clock(settings.plan_day_end), tzinfo=tz)
    if end <= start:
        raise ValueError(
            f"plan_day_end ({settings.plan_day_end}) must be after plan_day_start "
            f"({settings.plan_day_start})"
        )
    if now is not None and now > start:
        start = now
    return start, end


def _busy(event: dict, tz: ZoneInfo) -> tuple[datetime, datetime] | None:
    """A timed commitment's interval, or None. Exactly the events `_conflicts`
    counts: not all-day, not declined, with a start and an end. A stamp without
    an offset is read as local time rather than dropped, so a malformed event
    can only make the day look busier, never freer."""
    if event.get("all_day") or event.get("declined"):
        return None
    start, end = _dt(event.get("start")), _dt(event.get("end"))
    if start is None or end is None:
        return None
    start = start if start.tzinfo else start.replace(tzinfo=tz)
    end = end if end.tzinfo else end.replace(tzinfo=tz)
    return (start, end) if end > start else None


def free_time(
    events: list[dict], window_from: datetime, window_to: datetime, min_minutes: int
) -> list[dict]:
    """The free stretches of `[window_from, window_to)` after every timed
    commitment in `events` is subtracted, each `{"from", "to", "minutes"}`,
    earliest first. Pure.

    Busy intervals are clipped to the window and merged when they overlap or
    touch (back-to-back meetings leave no gap); a stretch shorter than
    `min_minutes` is dropped."""
    if window_to <= window_from:
        return []
    tz = window_from.tzinfo or ZoneInfo(settings.ea_timezone)
    busy = []
    for e in events:
        interval = _busy(e, tz)
        if interval is None:
            continue
        # Compared as instants, reported in the window's own offset.
        start = max(interval[0].astimezone(tz), window_from)
        end = min(interval[1].astimezone(tz), window_to)
        if end > start:
            busy.append((start, end))
    busy.sort()
    merged: list[list[datetime]] = []
    for start, end in busy:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])

    out: list[dict] = []
    cursor = window_from
    for start, end in [*merged, [window_to, window_to]]:
        if start > cursor:
            minutes = int((start - cursor).total_seconds() // 60)
            if minutes >= min_minutes:
                out.append(
                    {"from": cursor.isoformat(), "to": start.isoformat(), "minutes": minutes}
                )
        cursor = max(cursor, end)
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
    midnight = datetime.combine((now + timedelta(days=offset_days)).date(), time.min, tzinfo=tz)
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
    starts = [s for s in (_dt(e.get("start")) for e in events if not e.get("all_day")) if s]
    work_from, work_to = working_window(midnight.date(), now if offset_days <= 0 else None)
    free = free_time(events, work_from, work_to, int(settings.plan_min_block_minutes))
    return {
        "available": True,
        "timezone": settings.ea_timezone,
        "window": {"from": start.isoformat(), "to": end.isoformat()},
        "event_count": len(events),
        "first_event_at": min(starts).isoformat() if starts else None,
        "truncated": False,
        "events": events,
        "conflicts": _conflicts(events),
        "free_time": free,
        "free_minutes": sum(s["minutes"] for s in free),
    }


def _timed(events: list[dict]) -> list[dict]:
    """The timed events `free_time` subtracts (the `_busy` test)."""
    tz = ZoneInfo(settings.ea_timezone)
    return [e for e in events if _busy(e, tz) is not None]


async def week(start_offset: int = 1, days: int = 7) -> dict:
    """The coming `days` local days from `start_offset`, one summary line per
    day, computed from `day()` so the rules exist once (DL-221 step 5).

    Each day is read through `day()`, ONE AFTER ANOTHER (never in parallel
    against the provider), and fails closed on its own: a day `day()` answers
    `available: false` keeps that answer and its reason and is never counted as
    free. An outage raises, like `day()`, and the caller degrades the key.

    A day line: `date`, `day`, `available` (and `reason` when not), and for an
    available day `timed_events` (the timed, not-declined events free time is
    computed after), `conflicts` (a count), `free_minutes` inside the working
    window with its `free_time` stretches (where a block may go), and `overbooked` (less free time than `plan_overbooked_free_minutes`).
    `busy_event_ids` rides along for the ledger join in the caller, which
    replaces it with the counts of commitments and of the assistant's own
    events."""
    if not calendar_facade.provider_configured():
        return {"available": False, "reason": "not configured"}
    threshold = int(settings.plan_overbooked_free_minutes)
    tz = ZoneInfo(settings.ea_timezone)
    today = datetime.now(tz).date()
    lines: list[dict] = []
    for offset in range(start_offset, start_offset + max(0, int(days))):
        local = today + timedelta(days=offset)
        line = {"date": local.isoformat(), "day": local.strftime("%A")}
        one = await day(offset)  # awaited in turn: sequential by construction
        if not one.get("available"):
            lines.append({**line, "available": False, "reason": one.get("reason") or "unknown"})
            continue
        free = int(one.get("free_minutes") or 0)
        timed = _timed(one.get("events") or [])
        lines.append(
            {
                **line,
                "available": True,
                "timed_events": len(timed),
                # The ids the caller joins to the plan ledger to tell commitments
                # from the assistant's own events (this module holds no database).
                "busy_event_ids": [str(e["event_id"]) for e in timed if e.get("event_id")],
                "conflicts": len(one.get("conflicts") or []),
                "free_minutes": free,
                "free_time": one.get("free_time") or [],
                "overbooked": free < threshold,
            }
        )
    known = [d for d in lines if d["available"]]
    return {
        "available": True,
        "timezone": settings.ea_timezone,
        "working_window": f"{settings.plan_day_start}-{settings.plan_day_end}",
        "overbooked_below_minutes": threshold,
        "days": lines,
        "days_available": len(known),
        "days_unknown": len(lines) - len(known),
        "free_minutes_known_days": sum(d["free_minutes"] for d in known),
        "overbooked_days": [d["date"] for d in known if d["overbooked"]],
        "days_with_conflicts": [d["date"] for d in known if d["conflicts"]],
    }
