"""The EA's calendar slice — conflicts computed in Python, and a boundary that
is about WHO may call a write (the Executor, after an approval), not about a
credential's scope: the n8n credential was always full-scope.

NO NETWORK, ever: `calendar_facade.list_events` is monkeypatched in every test
that would otherwise leave the box, and the unconfigured case is asserted with a
fake that RAISES if called — "makes no HTTP call" is a property worth pinning,
not an assumption.

The anchor assertion is the degradation one: a calendar outage must degrade the
EA's contact and never cancel it. `ea.contact_delivered` is what windows the
next contact, so a contact lost to a third party's outage would silently widen
every window after it.
"""

from __future__ import annotations

import json
import pathlib
import re

import httpx
import pytest

from central_command import events
from central_command.config import settings
from central_command.db import repo
from central_command.heartbeat import actions as hb_actions
from central_command.integrations import calendar_facade
from central_command.reports import ea_calendar
from central_command.runtime import tools
from tests.conftest import needs_pg


class _Facade:
    """Records the windows it was asked for; answers a fixed event list."""

    def __init__(self, events_: list[dict] | None = None, truncated: bool = False):
        self.events = events_ or []
        self.truncated = truncated
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, time_min, time_max, calendar_id="primary"):
        self.calls.append((time_min, time_max))
        return {"events": self.events, "truncated": self.truncated}


async def _explode(*a, **kw):
    raise AssertionError("the calendar façade must not be called here")


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    # Pinned demo mode everywhere a model is reachable (a real key in .env
    # would otherwise make the "offline" suite spend money), and a token so the
    # not-configured short-circuit is opted into per-test rather than by
    # whatever the dev box has in .env.
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "executor_mode", "dry_run")
    monkeypatch.setattr(settings, "dispatch_approval_limit", 10_000)
    monkeypatch.setattr(settings, "calendar_facade_token", "test-token")
    # Unlimited (0) contact budget by default — the throttle has its own tests
    # (tests/test_attention.py) and must not silently decide these. A finite
    # number would make them pass or fail on how much unrelated EA traffic the
    # shared test database already saw today. The deferred-contact test below
    # pins budget AND count down again on purpose.
    monkeypatch.setattr(settings, "ea_contact_budget_per_day", 0)
    monkeypatch.setattr(settings, "ea_timezone", "America/Denver")


def _ev(start, end, **kw):
    base = {"start": start, "end": end, "all_day": False, "summary": "Meeting",
            "location": "", "attendee_count": 2, "organizer_is_self": False,
            "has_agenda": False, "declined": False}
    base.update(kw)
    return base


# --- conflicts (arithmetic, not a model's opinion) -----------------------------


async def test_overlapping_events_are_reported_as_a_conflict(monkeypatch):
    monkeypatch.setattr(calendar_facade, "list_events", _Facade([
        _ev("2026-08-01T09:00:00-06:00", "2026-08-01T10:00:00-06:00", summary="A"),
        _ev("2026-08-01T09:30:00-06:00", "2026-08-01T10:30:00-06:00", summary="B"),
    ]))
    out = await ea_calendar.day(0)
    assert out["available"] and out["event_count"] == 2
    assert [c["between"] for c in out["conflicts"]] == [["A", "B"]]
    assert out["first_event_at"].startswith("2026-08-01T09:00")


async def test_back_to_back_is_not_a_conflict(monkeypatch):
    monkeypatch.setattr(calendar_facade, "list_events", _Facade([
        _ev("2026-08-01T09:00:00-06:00", "2026-08-01T10:00:00-06:00", summary="A"),
        _ev("2026-08-01T10:00:00-06:00", "2026-08-01T11:00:00-06:00", summary="B"),
    ]))
    assert (await ea_calendar.day(0))["conflicts"] == []


async def test_all_day_markers_and_declined_invitations_are_not_commitments(
    monkeypatch
):
    """A day-long marker overlaps everything and commits to nothing; a declined
    invitation is not on the operator's plate at all. Either counted as a clash
    would make the conflict list noise the operator learns to ignore."""
    monkeypatch.setattr(calendar_facade, "list_events", _Facade([
        _ev("2026-08-01T00:00:00-06:00", "2026-08-02T00:00:00-06:00",
            all_day=True, summary="Vacation"),
        _ev("2026-08-01T09:00:00-06:00", "2026-08-01T10:00:00-06:00", summary="A"),
        _ev("2026-08-01T09:15:00-06:00", "2026-08-01T09:45:00-06:00",
            declined=True, summary="Declined"),
    ]))
    out = await ea_calendar.day(0)
    assert out["conflicts"] == []
    # Skipped for CONFLICTS, still reported: the operator sees their own day.
    assert out["event_count"] == 3


async def test_truncation_is_carried_through_honestly(monkeypatch):
    monkeypatch.setattr(calendar_facade, "list_events", _Facade([], truncated=True))
    out = await ea_calendar.day(0)
    assert out["truncated"] is True and out["first_event_at"] is None


async def test_an_unconfigured_token_makes_no_call_at_all(monkeypatch):
    monkeypatch.setattr(settings, "calendar_facade_token", "")
    monkeypatch.setattr(calendar_facade, "list_events", _explode)
    assert await ea_calendar.day(0) == {"available": False,
                                        "reason": "not configured"}


async def test_tomorrow_is_a_full_local_day_and_today_starts_now(monkeypatch):
    facade = _Facade()
    monkeypatch.setattr(calendar_facade, "list_events", facade)
    await ea_calendar.day(1)
    time_min, time_max = facade.calls[0]
    assert time_min.endswith("T00:00:00-06:00") or time_min.endswith("T00:00:00-07:00")
    assert time_min[:10] != time_max[:10], "a day window must cross midnight"


# --- the sibling boundary ------------------------------------------------------


CALENDAR_WRITE_HELPERS = ("create_event", "update_event", "delete_event")

# The two files that may name a calendar write helper: the module that defines
# them, and the credentialed Executor that is allowed to call them. Anything
# else — a route, a report, and above all anything under runtime/ — would be a
# calendar write outside the approval gate.
CALENDAR_WRITE_CALLERS = {
    "integrations/calendar_facade.py",
    "gateway/executor.py",
}


def test_only_the_executor_calls_the_calendar_write_helpers():
    """THE GUARD the revised calendar policy names (EA widening slice A,
    2026-08-06). The old guard asserted no write helper EXISTS; three of them do
    now, so the boundary moved from absence to CALLER. Source walk over the
    whole package, in the idiom of test_mcp_wiring / test_proposal_created:
    naming a helper anywhere else fails the suite in the commit that adds it,
    not at the first live approval."""
    pkg = pathlib.Path(calendar_facade.__file__).parents[1]
    offenders: dict[str, list[str]] = {}
    for path in sorted(pkg.rglob("*.py")):
        rel = path.relative_to(pkg).as_posix()
        if rel in CALENDAR_WRITE_CALLERS:
            continue
        src = path.read_text(encoding="utf-8")
        # CALL form only (`create_event(`, no space — PEP8 never puts one).
        # The bare names also appear as registry/pack capability strings
        # ('calendar.create_event') and in prose about the modes: those are
        # governance DATA an agent is meant to read. It is the callable that
        # is Executor-only.
        hits = [h for h in CALENDAR_WRITE_HELPERS
                if re.search(rf"(?<!calendar\.)\b{h}\(", src)]
        if hits:
            offenders[rel] = hits
    assert not offenders, (
        f"calendar write helpers named outside the Executor: {offenders}. "
        "create_event/update_event/delete_event are Executor-only — they run "
        "after a human approved the proposal that named them, and runtime/ may "
        "not reach them at all."
    )


@needs_pg
async def test_the_postgres_snapshot_stays_pure_postgres():
    """`ea_report.snapshot()` must not grow an HTTP dependency: its contract is
    'a property of the record'. The calendar rides beside it, added by the
    heartbeat, so a Google outage degrades one key instead of the whole block."""
    from datetime import datetime, timedelta, timezone

    from central_command.reports import ea_report

    snap = await ea_report.snapshot(datetime.now(timezone.utc) - timedelta(hours=1))
    assert "calendar" not in snap


# --- the EA's contact ----------------------------------------------------------


@pytest.fixture()
def stub_task(monkeypatch):
    """Capture the brief instead of running the EA — this is a test of what the
    contact CARRIES, and a real run would prove nothing extra about it."""
    from central_command.api import routes

    seen: list[str] = []

    async def fake(brief, title, agent_id, actor=None):
        seen.append(brief)
        return {"task_id": "task_cal_test", "session_id": "sess_cal_test"}

    monkeypatch.setattr(routes, "create_and_run_task", fake)
    return seen


@needs_pg
async def test_the_digest_asks_for_tomorrow_and_the_check_in_for_today(
    monkeypatch, stub_task
):
    facade = _Facade()
    monkeypatch.setattr(calendar_facade, "list_events", facade)

    await hb_actions._ea_contact("sched_cal_digest", {"kind": "digest"})
    await hb_actions._ea_contact("sched_cal_checkin", {"kind": "check_in"})

    assert hb_actions._CALENDAR_DAY == {"morning_report": 0, "check_in": 0,
                                        "digest": 1, "week_ahead": 1}
    digest_min, check_in_min = facade.calls[0][0], facade.calls[1][0]
    assert digest_min[:10] > check_in_min[:10], (
        "the 17:00 digest must look at TOMORROW; today's calendar is spent"
    )


@needs_pg
async def test_the_brief_carries_the_calendar_and_says_it_is_read_only(
    monkeypatch, stub_task
):
    monkeypatch.setattr(calendar_facade, "list_events", _Facade([
        _ev("2026-08-01T09:00:00-06:00", "2026-08-01T10:00:00-06:00", summary="A"),
    ]))
    await hb_actions._ea_contact("sched_cal_brief", {"kind": "morning_report"})

    brief = stub_task[0]
    assert "`calendar` is the operator's REAL calendar" in brief
    assert "READ-ONLY" in brief
    block = json.loads(brief.split("```json\n")[1].split("\n```")[0])
    assert block["calendar"]["available"] is True
    assert block["calendar"]["event_count"] == 1


@needs_pg
async def test_an_unconfigured_calendar_leaves_the_brief_alone(
    monkeypatch, stub_task
):
    monkeypatch.setattr(settings, "calendar_facade_token", "")
    monkeypatch.setattr(calendar_facade, "list_events", _explode)

    await hb_actions._ea_contact("sched_cal_off", {"kind": "check_in"})

    brief = stub_task[0]
    block = json.loads(brief.split("```json\n")[1].split("\n```")[0])
    assert "calendar" not in block
    assert "REAL calendar" not in brief


@needs_pg
async def test_a_calendar_outage_degrades_the_contact_but_never_cancels_it(
    monkeypatch, stub_task
):
    """THE anchor assertion. `ea.contact_delivered` is what windows the next
    contact of this kind — a contact lost to Google being down would silently
    widen every window after it."""
    async def down(*a, **kw):
        raise httpx.ConnectError("[Errno 111] Connection refused")

    monkeypatch.setattr(calendar_facade, "list_events", down)

    emitted: list[str] = []
    real_emit = events.emit

    async def spy(kind, ref_id=None, payload=None, actor=None):
        emitted.append(kind)
        return await real_emit(kind, ref_id=ref_id, payload=payload, actor=actor)

    monkeypatch.setattr(events, "emit", spy)

    out = await hb_actions._ea_contact("sched_cal_down", {"kind": "check_in"})

    assert out["delivered"] is True
    assert hb_actions.EA_DELIVERED_EVENT in emitted
    block = json.loads(stub_task[0].split("```json\n")[1].split("\n```")[0])
    assert block["calendar"]["available"] is False
    assert block["calendar"]["error"]


@needs_pg
async def test_a_budget_deferred_contact_makes_zero_calendar_calls(monkeypatch):
    """A deferred contact spends NOTHING — no tokens, and no call to Google
    either. The calendar read sits after the budget check for exactly this."""
    monkeypatch.setattr(settings, "ea_contact_budget_per_day", 3)
    monkeypatch.setattr(calendar_facade, "list_events", _explode)

    async def spent(kind, actor, since):
        return 3

    monkeypatch.setattr(repo, "count_events_since", spent)

    out = await hb_actions._ea_contact("sched_cal_broke", {"kind": "digest"})
    assert out["delivered"] is False


# --- the agent-facing tool -----------------------------------------------------


@pytest.fixture
def no_wait(monkeypatch):
    """The retry's wait, patched — a bounded retry must cost the suite no wall
    clock (patching asyncio.sleep itself would break the loop under the test)."""
    waits: list[float] = []

    async def _fake(delay):
        waits.append(delay)

    monkeypatch.setattr(tools, "_sleep", _fake)
    return waits


async def test_read_calendar_retries_once_on_a_transient_failure(
    monkeypatch, no_wait
):
    calls = {"n": 0}

    async def flaky(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("[Errno 111] Connection refused")
        return {"events": [_ev("2026-08-01T09:00:00-06:00",
                               "2026-08-01T10:00:00-06:00", summary="Standup")],
                "truncated": False}

    monkeypatch.setattr(calendar_facade, "list_events", flaky)

    out = await tools.read_calendar(None, 1)
    assert calls["n"] == 2 and no_wait == [0.5]
    assert "Standup" in out and "failed" not in out


async def test_read_calendar_degrades_without_retrying_a_semantic_failure(
    monkeypatch, no_wait
):
    calls = {"n": 0}

    async def refused(*a, **kw):
        calls["n"] += 1
        raise calendar_facade.CalendarFacadeError("calendar façade list refused")

    monkeypatch.setattr(calendar_facade, "list_events", refused)

    out = await tools.read_calendar(None, 0)
    assert calls["n"] == 1 and no_wait == []
    assert out.startswith("calendar read failed (CalendarFacadeError)")


async def test_read_calendar_clamps_the_day_offset(monkeypatch):
    facade = _Facade()
    monkeypatch.setattr(calendar_facade, "list_events", facade)
    await tools.read_calendar(None, 99)
    await tools.read_calendar(None, -5)
    seven, today = facade.calls[0][0][:10], facade.calls[1][0][:10]
    assert seven > today, "99 must clamp to +7 and -5 to today"


async def test_read_calendar_says_so_when_the_calendar_is_not_configured(
    monkeypatch
):
    monkeypatch.setattr(settings, "calendar_facade_token", "")
    monkeypatch.setattr(calendar_facade, "list_events", _explode)
    assert "not configured" in await tools.read_calendar(None, 0)


# --- slice A: the gated write path (2026-08-06) --------------------------------
# Handlers against a fake façade — the boundary these tests care about is
# "the Executor calls the right mode with the right args and reports honestly",
# not Google's wire format (which lives in the n8n lib).


class _WriteFacade:
    """Captures the payloads the Executor sends; answers a fixed event."""

    def __init__(self):
        self.calls: list[dict] = []

    async def _call(self, payload: dict, timeout: float = 60.0) -> dict:
        self.calls.append(payload)
        if payload["mode"] == "delete_event":
            return {"ok": True, "deleted": payload["event_id"]}
        return {"ok": True, "event": {"event_id": "evt_123"}}


@pytest.fixture()
def write_facade(monkeypatch):
    from central_command.gateway import executor

    fake = _WriteFacade()
    monkeypatch.setattr(calendar_facade, "_call", fake._call)
    monkeypatch.setattr(settings, "executor_mode", "live")
    return fake, executor


async def test_the_executor_creates_an_event_through_the_facade(write_facade):
    fake, executor = write_facade
    out = await executor._calendar_create_event(
        {"title": "1:1", "start": "2026-08-07T09:00:00-06:00",
         "end": "2026-08-07T09:30:00-06:00", "attendees": ["a@example.com"]},
        "lee", "ea",
    )
    assert fake.calls[0]["mode"] == "create_event"
    assert fake.calls[0]["attendees"] == ["a@example.com"]
    assert fake.calls[0]["calendar_id"] == "primary"
    assert "evt_123" in out, "the result must carry a real id for a later update"


async def test_an_update_sends_only_the_changed_fields(write_facade):
    fake, executor = write_facade
    out = await executor._calendar_update_event(
        {"event_id": "evt_123", "start": "2026-08-07T10:00:00-06:00",
         "end": "2026-08-07T10:30:00-06:00", "title": None},
        "lee", "ea",
    )
    payload = fake.calls[0]
    assert payload["mode"] == "update_event" and payload["event_id"] == "evt_123"
    assert "title" not in payload, "a PATCH must not clear fields the agent left out"
    assert "start" in out or "updated" in out


async def test_an_update_with_nothing_to_change_fails_loudly(write_facade):
    fake, executor = write_facade
    with pytest.raises(executor.ExecutorError):
        await executor._calendar_update_event({"event_id": "evt_123"}, "lee", "ea")
    assert not fake.calls, "a no-op must never reach the calendar"


async def test_a_delete_requires_a_reason_and_records_it(write_facade):
    """The reason never reaches Google — it is the review artifact for the one
    calendar action that cannot be taken back, so it must survive into the
    result the record keeps."""
    fake, executor = write_facade
    with pytest.raises(executor.ExecutorError):
        await executor._calendar_delete_event(
            {"event_id": "evt_123", "reason": "  "}, "lee", "ea")
    assert not fake.calls

    out = await executor._calendar_delete_event(
        {"event_id": "evt_123", "reason": "moved to Thursday"}, "lee", "ea")
    assert fake.calls[0]["mode"] == "delete_event"
    assert "moved to Thursday" in out


async def test_the_propose_tool_defers_instead_of_writing():
    """The runtime side of the boundary: the agent's only calendar-write tool
    ends the run at a deferral for the operator, and touches nothing."""
    from pydantic_ai import CallDeferred

    from central_command.contract import Action, Proposal, Reversibility

    proposal = Proposal(
        intent="create the event", expected_effect="event exists", evidence=[],
        actions=[Action(capability="calendar.create_event", arguments={},
                        target_ref={"system": "calendar", "id": "new"},
                        reversibility=Reversibility.reversible)],
    )
    with pytest.raises(CallDeferred) as exc:
        await tools.propose_calendar_change(None, proposal)
    assert exc.value.metadata == {"kind": "calendar_change"}

    from central_command.runtime.durable import classify_deferred

    class _Call:
        tool_name = "propose_calendar_change"

    assert classify_deferred(_Call()) == "proposal", (
        "an unclassified propose tool parks nowhere"
    )


def test_the_calendar_propose_pack_reaches_the_charter():
    from central_command.runtime import packs

    section = packs.charter_section(("calendar-read", "calendar-propose"))
    for cap in ("calendar.create_event", "calendar.update_event",
                "calendar.delete_event"):
        assert cap in section
    assert "READ BEFORE YOU PROPOSE" in section
    # The read pack no longer claims the calendar cannot be changed.
    assert "NO way" not in section
    toolset = packs.toolset_for(("calendar-read", "calendar-propose"))
    assert "propose_calendar_change" in toolset.tools


def test_the_ea_template_grants_calendar_propose():
    from central_command.runtime.templates import EA_TEMPLATE

    assert "calendar-propose" in EA_TEMPLATE.default_packs
