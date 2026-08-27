"""Client for the n8n calendar tool façade (cc-calendar-facade).

THE BOUNDARY, honestly (revised 2026-08-06, EA widening slice A): the Google
credential inside n8n is FULL-SCOPE by operator decision — it always COULD
write, and now there are write helpers here that do. The boundary is no longer
"no write path exists"; it is **who may call one**: `list_events` is an ungated
read anyone may make, while `create_event`/`update_event`/`delete_event` are
called ONLY from `gateway/executor.py`, i.e. only after a human approved the
proposal that named them. `tests/test_ea_calendar.py::
test_only_the_executor_calls_the_calendar_write_helpers` walks the sources and
fails if any other module so much as mentions them — the runtime tier (where
the agents live) must never reach these.

Calendar content passes through here as data; the callers (the EA's brief, the
`read_calendar` tool) own the data-not-commands discipline.
"""

from __future__ import annotations

import httpx

from central_command.config import settings


class CalendarFacadeError(Exception):
    pass


async def _call(payload: dict, timeout: float = 60.0) -> dict:
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            settings.calendar_facade_url,
            headers={"x-cc-token": settings.calendar_facade_token},
            json=payload,
        )
    if resp.status_code != 200:
        raise CalendarFacadeError(
            f"calendar façade {payload.get('mode')} failed: HTTP "
            f"{resp.status_code} {resp.text[:200]}"
        )
    out = resp.json()
    if not out.get("ok"):
        raise CalendarFacadeError(
            f"calendar façade {payload.get('mode')} refused: {str(out)[:200]}"
        )
    return out


async def list_events(
    time_min: str, time_max: str, calendar_id: str = "primary"
) -> dict:
    """Events in an RFC3339 window: {"events": [...], "truncated": bool}.

    Returns the envelope rather than a bare list ONLY because `truncated` is a
    fact about the read that the caller must be able to state honestly — a
    brief that silently drops events is worse than one that says it was cut.
    Each event is already normalised by the n8n lib (start, end, all_day,
    summary, location, attendee_count, organizer_is_self, has_agenda, declined).
    """
    out = await _call({
        "mode": "list",
        "time_min": time_min,
        "time_max": time_max,
        "calendar_id": calendar_id,
    })
    return {"events": out.get("events", []), "truncated": bool(out.get("truncated"))}


# --- writes: EXECUTOR-ONLY (see the module docstring) --------------------------
# The façade returns the created/updated event so the Executor can state a real
# event_id in the result the agent and the record see — "it worked" without an
# id is not something a later update or delete can act on.


async def create_event(
    title: str,
    start: str,
    end: str,
    description: str | None = None,
    attendees: list[str] | None = None,
    calendar_id: str = "primary",
) -> dict:
    """Create an event. `start`/`end` are RFC3339 with an explicit offset or Z."""
    out = await _call({
        "mode": "create_event",
        "title": title,
        "start": start,
        "end": end,
        "description": description or "",
        "attendees": attendees or [],
        "calendar_id": calendar_id,
    })
    return out.get("event") or {}


async def update_event(
    event_id: str,
    title: str | None = None,
    start: str | None = None,
    end: str | None = None,
    description: str | None = None,
    attendees: list[str] | None = None,
    calendar_id: str = "primary",
) -> dict:
    """Patch an existing event. Only the fields supplied are sent, and the façade
    PATCHes — an omitted field keeps its current value rather than being cleared."""
    payload = {"mode": "update_event", "event_id": event_id, "calendar_id": calendar_id}
    for key, value in (("title", title), ("start", start), ("end", end),
                       ("description", description), ("attendees", attendees)):
        if value is not None:
            payload[key] = value
    out = await _call(payload)
    return out.get("event") or {}


async def delete_event(event_id: str, calendar_id: str = "primary") -> str:
    """Cancel/delete an event. Google notifies the attendees; there is no undo
    that restores their acceptances, which is why the registry calls this
    irreversible and the proposal must carry a reason."""
    out = await _call({
        "mode": "delete_event",
        "event_id": event_id,
        "calendar_id": calendar_id,
    })
    return str(out.get("deleted") or event_id)
