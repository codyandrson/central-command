"""The weekly plan and the quarterly review (the EA planning design, D9; DL-221
step 5): the week computed in code, deadlines with no time serving them, the
time audit from the record, the two contact kinds' briefs, the quarterly seed,
the framings and the charter.

NO NETWORK: the calendar façade and the tracker are faked in every test that
would reach them; the conftest guard holds `ea_plan.deadlines` unconfigured,
and the tests that need the real one put it back (`_REAL_DEADLINES`).
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
import pytest

from central_command.config import settings
from central_command.db import repo
from central_command.heartbeat import actions as hb_actions
from central_command.integrations import calendar_facade, jira
from central_command.reports import ea_backlog, ea_calendar, ea_plan
from central_command.runtime import ea_profile
from tests.conftest import needs_pg

TZ = "America/Denver"
TODAY = date(2026, 11, 22)
# Captured at import, before the conftest guard swaps in its stand-in.
_REAL_DEADLINES = ea_plan.deadlines
SCHEMA = (pathlib.Path(__file__).resolve().parents[1]
          / "central_command" / "db" / "schema.sql").read_text()


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    monkeypatch.setattr(settings, "test_model", True)
    monkeypatch.setattr(settings, "ea_timezone", TZ)
    monkeypatch.setattr(settings, "plan_day_start", "08:00")
    monkeypatch.setattr(settings, "plan_day_end", "18:00")
    monkeypatch.setattr(settings, "plan_min_block_minutes", 30)
    monkeypatch.setattr(settings, "plan_overbooked_free_minutes", 120)
    monkeypatch.setattr(settings, "plan_padding_min_samples", 5)


# --- 1. week() ------------------------------------------------------------------------


def _stamp(day: str, clock: str) -> str:
    hours, minutes = (int(x) for x in clock.split(":"))
    return datetime.combine(date.fromisoformat(day), datetime.min.time(),
                            tzinfo=ZoneInfo(TZ)).replace(hour=hours, minute=minutes).isoformat()


def _ev(day: str, start: str, end: str, **kw) -> dict:
    return {"event_id": kw.pop("event_id", f"ev_{uuid.uuid4().hex[:8]}"),
            "start": _stamp(day, start), "end": _stamp(day, end),
            "all_day": False, "summary": "Meeting", "declined": False, **kw}


class _Week:
    """A calendar keyed by local date: a list of events, or an exception to
    raise, or "truncated". Records each read and how many were in flight."""

    def __init__(self):
        self.days: dict[str, object] = {}
        self.calls: list[str] = []
        self.in_flight = 0
        self.max_in_flight = 0

    async def __call__(self, time_min, time_max, calendar_id="primary"):
        day = time_min[:10]
        self.calls.append(day)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(0)
            answer = self.days.get(day, [])
            if isinstance(answer, Exception):
                raise answer
            if answer == "truncated":
                return {"events": [], "truncated": True}
            return {"events": list(answer), "truncated": False}
        finally:
            self.in_flight -= 1


def _local(offset: int) -> str:
    return (datetime.now(ZoneInfo(TZ)).date() + timedelta(days=offset)).isoformat()


@pytest.fixture()
def cal(monkeypatch):
    w = _Week()
    monkeypatch.setattr(calendar_facade, "provider_configured", lambda: True)
    monkeypatch.setattr(calendar_facade, "list_events", w)
    return w


async def test_week_mixes_known_and_unknown_days_and_reads_in_turn(cal):
    d1, d2, d3, d4 = _local(1), _local(2), _local(3), _local(4)
    cal.days[d1] = [_ev(d1, "09:00", "17:00")]                     # 120 free: not overbooked
    cal.days[d2] = [_ev(d2, "09:00", "17:30"),                      # 90 free: overbooked
                    _ev(d2, "10:00", "11:00"),                      # and a conflict
                    _ev(d2, "12:00", "13:00", declined=True)]
    cal.days[d3] = calendar_facade.CalendarFacadeError(
        "list_events — HTTP 422: the provider refused the window", status_code=422)
    cal.days[d4] = "truncated"
    out = await ea_calendar.week(1, 5)
    assert cal.calls == [_local(n) for n in range(1, 6)], "one read per day, in order"
    assert cal.max_in_flight == 1, "never two reads at once against the provider"
    assert out["available"] is True and [d["date"] for d in out["days"]] == cal.calls
    by = {d["date"]: d for d in out["days"]}
    assert by[d1]["available"] is True and by[d1]["free_minutes"] == 120
    assert by[d1]["overbooked"] is False and by[d1]["conflicts"] == 0
    assert by[d1]["timed_events"] == 1 and len(by[d1]["free_time"]) == 2
    assert by[d2]["free_minutes"] == 90 and by[d2]["overbooked"] is True
    assert by[d2]["conflicts"] == 1 and by[d2]["timed_events"] == 2, "declined is not busy"
    assert by[d3] == {"date": d3, "day": by[d3]["day"], "available": False,
                      "reason": by[d3]["reason"]}
    assert "HTTP 422" in by[d3]["reason"] and "free_minutes" not in by[d3]
    assert by[d4]["available"] is False and by[d4]["reason"] == ea_calendar.TRUNCATED_REASON
    assert by[_local(5)]["free_minutes"] == 600, "an empty known day is free"
    assert out["days_available"] == 3 and out["days_unknown"] == 2
    assert out["free_minutes_known_days"] == 120 + 90 + 600, "unknown days count nothing"
    assert out["overbooked_days"] == [d2] and out["days_with_conflicts"] == [d2]


async def test_the_overbooked_threshold_is_the_setting(cal, monkeypatch):
    d1 = _local(1)
    cal.days[d1] = [_ev(d1, "09:00", "17:00")]                     # 120 free
    monkeypatch.setattr(settings, "plan_overbooked_free_minutes", 121)
    assert (await ea_calendar.week(1, 1))["days"][0]["overbooked"] is True
    monkeypatch.setattr(settings, "plan_overbooked_free_minutes", 120)
    out = await ea_calendar.week(1, 1)
    assert out["days"][0]["overbooked"] is False and out["overbooked_below_minutes"] == 120


async def test_an_outage_on_any_day_raises(cal):
    cal.days[_local(3)] = calendar_facade.CalendarFacadeError(
        "list_events — HTTP 503", status_code=503)
    with pytest.raises(calendar_facade.CalendarFacadeError):
        await ea_calendar.week(1, 7)
    assert cal.calls == [_local(1), _local(2), _local(3)], "stops at the outage"


async def test_an_unconfigured_calendar_makes_no_read(monkeypatch):
    async def explode(*a, **k):
        raise AssertionError("no read when the calendar is not configured")

    monkeypatch.setattr(calendar_facade, "provider_configured", lambda: False)
    monkeypatch.setattr(calendar_facade, "list_events", explode)
    assert await ea_calendar.week() == {"available": False, "reason": "not configured"}


@needs_pg
async def test_the_week_block_tells_commitments_from_the_assistants_events(cal):
    d1 = _local(1)
    block, place = f"wb_{uuid.uuid4().hex[:10]}", f"ph_{uuid.uuid4().hex[:10]}"
    await repo.record_calendar_plan_event(event_id=block, calendar_id="primary",
                                          kind="work_block", issue_key="DOE-1",
                                          plan_date=d1, proposal_id="prop_wk")
    await repo.record_calendar_plan_event(event_id=place, calendar_id="primary",
                                          kind="placeholder", proposal_id="prop_wk")
    cal.days[d1] = [_ev(d1, "09:00", "10:00", event_id=block),
                    _ev(d1, "10:00", "11:00", event_id=place),
                    _ev(d1, "13:00", "14:00"), _ev(d1, "15:00", "16:00", event_id=None)]
    out = await hb_actions._annotate_week(await ea_calendar.week(1, 2))
    day1, day2 = out["days"]
    assert (day1["commitments"], day1["work_blocks"], day1["placeholders"]) == (2, 1, 1)
    assert "busy_event_ids" not in day1 and "busy_event_ids" not in day2
    assert day2["commitments"] == 0


# --- 2. deadline_coverage() -------------------------------------------------------------


def _row(key, *, due, estimate=None, tier="standard", value=None):
    return {"issue_key": key, "summary": f"S {key}", "due_date": due, "tier": tier,
            "estimate_hours": estimate, "value": value, "lateness_loss": None,
            "deadline_type": "soft"}


def _blk(key, day, minutes, *, retired=False):
    return {"event_id": f"e_{key}_{day}_{minutes}", "issue_key": key, "plan_date": day,
            "minutes": minutes, "retired_at": "2026-11-20T00:00:00Z" if retired else None}


def _d(n: int) -> str:
    return (TODAY + timedelta(days=n)).isoformat()


def test_coverage_covered_partly_none_and_order():
    rows = [_row("C", due=_d(3), estimate=2), _row("A", due=_d(1), estimate=2),
            _row("B", due=_d(1), estimate=2), _row("N", due=_d(5), estimate=1)]
    blocks = [_blk("A", _d(0), 60), _blk("A", _d(1), 60),     # 120 of 120: covered
              _blk("B", _d(1), 30),                            # 30 of 120: partly
              _blk("C", _d(2), 120)]                           # covered
    out = ea_plan.deadline_coverage(rows, blocks, TODAY, 7)
    assert [i["issue_key"] for i in out["issues"]] == ["A", "B", "C", "N"]
    cov = {i["issue_key"]: (i["coverage"], i["block_minutes"], i["needed_minutes"], i["blocks"])
           for i in out["issues"]}
    assert cov == {"A": ("covered", 120, 120, 2), "B": ("partly", 30, 120, 1),
                   "C": ("covered", 120, 120, 1), "N": ("none", 0, 60, 0)}
    assert out["counts"] == {"covered": 2, "partly": 1, "none": 1, "unestimated": 0}
    assert out["window"] == {"from": TODAY.isoformat(), "to": _d(7), "days": 7}


def test_coverage_window_edges():
    rows = [_row("PAST", due=_d(-1), estimate=1), _row("TODAY", due=_d(0), estimate=1),
            _row("LAST", due=_d(7), estimate=1), _row("LATE", due=_d(8), estimate=1),
            _row("NODATE", due=None, estimate=1)]
    keys = [i["issue_key"] for i in ea_plan.deadline_coverage(rows, [], TODAY, 7)["issues"]]
    assert keys == ["TODAY", "LAST"]


def test_unscored_issues_are_listed_unestimated():
    rows = [_row("U", due=_d(2), estimate=None, tier="unscored"),
            _row("Z", due=_d(2), estimate=0, tier="unscored"),
            _row("V", due=_d(2), estimate=3, tier="unscored")]
    out = ea_plan.deadline_coverage(rows, [_blk("U", _d(1), 90)], TODAY, 7)
    by = {i["issue_key"]: i for i in out["issues"]}
    assert by["U"]["coverage"] == "unestimated" and by["U"]["needed_minutes"] is None
    assert by["U"]["block_minutes"] == 90, "its time is still shown"
    assert by["Z"]["coverage"] == "unestimated", "an estimate of zero measures nothing"
    assert by["V"]["coverage"] == "none" and by["V"]["tier"] == "unscored"
    assert out["counts"]["unestimated"] == 2


def test_blocks_before_today_after_the_due_date_or_retired_are_ignored():
    rows = [_row("A", due=_d(3), estimate=1)]
    blocks = [_blk("A", _d(-1), 60), _blk("A", _d(4), 60), _blk("A", _d(1), 60, retired=True),
              _blk("OTHER", _d(1), 60)]
    (item,) = ea_plan.deadline_coverage(rows, blocks, TODAY, 7)["issues"]
    assert (item["coverage"], item["block_minutes"], item["blocks"]) == ("none", 0, 0)


def test_padding_raises_what_an_estimate_needs():
    rows = [_row("A", due=_d(2), estimate=2)]
    blocks = [_blk("A", _d(1), 120)]
    assert ea_plan.deadline_coverage(rows, blocks, TODAY, 7)["issues"][0]["coverage"] == "covered"
    padded = ea_plan.deadline_coverage(rows, blocks, TODAY, 7, padding=1.5)
    assert padded["padding"] == 1.5
    assert (padded["issues"][0]["coverage"], padded["issues"][0]["needed_minutes"]) == ("partly", 180)


def test_a_block_the_calendar_could_not_read_counts_as_no_time():
    rows = [_row("A", due=_d(2), estimate=1)]
    (item,) = ea_plan.deadline_coverage(rows, [_blk("A", _d(1), None)], TODAY, 7)["issues"]
    assert (item["coverage"], item["blocks"], item["blocks_unread"]) == ("none", 1, 1)


def test_deadline_coverage_is_pure():
    src = pathlib.Path(ea_plan.__file__).read_text()
    body = src.split("def deadline_coverage(")[1].split("\nasync def ")[0]
    for banned in ("await ", "repo.", "jira.", "calendar_facade.", "settings."):
        assert banned not in body, banned


@pytest.fixture()
def tracker_and_calendar(monkeypatch):
    monkeypatch.setattr(ea_plan, "deadlines", _REAL_DEADLINES)
    monkeypatch.setattr(jira, "configured", lambda: True)
    monkeypatch.setattr(ea_backlog, "_today", lambda: TODAY)
    monkeypatch.setattr(calendar_facade, "provider_configured", lambda: True)
    state = {"rows": [], "events": {}, "asked": []}

    async def read():
        ranked = ea_backlog.rank(state["rows"], TODAY, 6.0, 1.0)
        return {"ranked": ranked, "padding": {"ratio": 1.0}, "truncated": False}

    async def get_event(event_id, calendar_id="primary"):
        state["asked"].append(event_id)
        if event_id not in state["events"]:
            raise calendar_facade.CalendarFacadeError("get_event - event not found")
        return {"event_id": event_id, **state["events"][event_id]}

    monkeypatch.setattr(ea_backlog, "_read", read)
    monkeypatch.setattr(calendar_facade, "get_event", get_event)
    return state


@needs_pg
async def test_deadlines_reads_block_lengths_from_the_calendar(tracker_and_calendar):
    st = tracker_and_calendar
    key = f"DL-{uuid.uuid4().hex[:6]}"
    st["rows"] = [{"issue_key": key, "summary": "x", "due_date": _d(3), "estimate_hours": 2,
                   "value": 5}]
    seen, lost = f"dl_{uuid.uuid4().hex[:8]}", f"dl_{uuid.uuid4().hex[:8]}"
    for eid in (seen, lost):
        await repo.record_calendar_plan_event(event_id=eid, calendar_id="primary",
                                              kind="work_block", issue_key=key,
                                              plan_date=_d(1), proposal_id="prop_dl")
    st["events"][seen] = {"start": f"{_d(1)}T09:00:00-07:00", "end": f"{_d(1)}T10:30:00-07:00"}
    out = await ea_plan.deadlines(7)
    (item,) = [i for i in out["issues"] if i["issue_key"] == key]
    assert (item["coverage"], item["block_minutes"], item["blocks_unread"]) == ("partly", 90, 1)
    assert [b["event_id"] for b in out["blocks_unread"]] == [lost]
    assert set(st["asked"]) >= {seen, lost}


async def test_deadlines_is_not_configured_without_a_tracker(monkeypatch):
    monkeypatch.setattr(ea_plan, "deadlines", _REAL_DEADLINES)
    monkeypatch.setattr(jira, "configured", lambda: False)
    assert await ea_plan.deadlines(7) == {"available": False, "reason": "not configured"}


@needs_pg
async def test_a_calendar_outage_while_measuring_blocks_raises(tracker_and_calendar, monkeypatch):
    st = tracker_and_calendar
    key = f"DL-{uuid.uuid4().hex[:6]}"
    st["rows"] = [{"issue_key": key, "due_date": _d(2), "estimate_hours": 1, "value": 1}]
    await repo.record_calendar_plan_event(event_id=f"dl_{uuid.uuid4().hex[:8]}",
                                          calendar_id="primary", kind="work_block",
                                          issue_key=key, plan_date=_d(1), proposal_id="p")

    async def down(event_id, calendar_id="primary"):
        raise calendar_facade.CalendarFacadeError("get_event — HTTP 503") from \
            httpx.ConnectError("[Errno 111] Connection refused")

    monkeypatch.setattr(calendar_facade, "get_event", down)
    with pytest.raises(calendar_facade.CalendarFacadeError):
        await ea_plan.deadlines(7)


# --- 3. the time audit ----------------------------------------------------------------------


def _item(key, *, estimate=None, tier="standard"):
    return {"issue_key": key, "rank": 1, "reason": "r", "summary": f"S {key}",
            "estimate_hours": estimate, "tier": tier}


def _out(day, key, done, actual=None, planned=True):
    return {"plan_date": date.fromisoformat(day), "issue_key": key, "done": done,
            "actual_hours": actual, "planned": planned}


SINCE, UNTIL = date(2026, 11, 15), date(2026, 11, 21)


def test_a_full_window():
    plans = [
        {"plan_date": date(2026, 11, 16), "items": [_item("A", estimate=2), _item("B", estimate=1)]},
        {"plan_date": date(2026, 11, 17), "items": [_item("A", estimate=2),
                                                    _item("C", estimate=4, tier="must_start")]},
        {"plan_date": date(2026, 11, 18), "items": [_item("A", estimate=2), _item("D")]},
    ]
    outcomes = [
        _out("2026-11-16", "A", False), _out("2026-11-16", "B", True, 1.5),
        _out("2026-11-17", "C", True, 6.0),
        _out("2026-11-18", "A", True, 3.0),
        _out("2026-11-18", "X", True, 1.0, planned=False),
    ]
    out = ea_plan.audit_from(plans, outcomes, [], SINCE, UNTIL, min_samples=2)
    assert out["window"] == {"from": "2026-11-15", "to": "2026-11-21", "days": 7}
    assert out["days_with_plan"] == 3 and out["days_without_plan"] == 4
    assert "no_record" not in out
    items = out["items"]
    assert (items["planned"], items["done"], items["not_done"], items["unknown"]) == (6, 3, 1, 2)
    assert "what" in items
    h = out["hours"]
    assert h["estimated_planned"] == 2 + 1 + 2 + 4 + 2
    assert h["actual_reported_planned"] == 1.5 + 6.0 + 3.0
    assert (h["items_with_both"], h["estimate_of_those"], h["actual_of_those"]) == (3, 7, 10.5)
    assert h["actual_over_estimate"] == 1.5 and h["padding_from_these"] == 1.5
    assert out["by_tier"]["must_start"] == {"planned": 1, "done": 1, "estimated_hours": 4.0,
                                            "actual_hours": 6.0}
    slips = out["slipped"]["issues"]
    assert [s["issue_key"] for s in slips] == ["A", "D"]
    assert slips[0] == {"issue_key": "A", "summary": "S A", "planned_days": 3,
                        "slipped_days": 2, "slipped_on": ["2026-11-16", "2026-11-17"]}
    assert out["unplanned_outcomes"]["count"] == 1
    assert out["unplanned_outcomes"]["issues"][0]["issue_key"] == "X"


def test_an_empty_window_says_so_rather_than_reading_as_perfect():
    out = ea_plan.audit_from([], [], [], SINCE, UNTIL, min_samples=5)
    assert out["available"] is True and out["days_with_plan"] == 0
    assert "no record of planned time" in out["no_record"]
    assert "not a clean one" in out["no_record"]
    for key in ("items", "hours", "slipped", "by_tier"):
        assert key not in out, f"{key} would read as a perfect record"


def test_the_ratio_is_none_without_both_numbers_and_padding_holds_below_min_samples():
    plans = [{"plan_date": date(2026, 11, 16), "items": [_item("A", estimate=2)]}]
    out = ea_plan.audit_from(plans, [_out("2026-11-16", "A", True)], [], SINCE, UNTIL, 5)
    assert out["hours"]["actual_over_estimate"] is None and out["hours"]["items_with_both"] == 0
    plans[0]["items"][0]["estimate_hours"] = 1
    out = ea_plan.audit_from(plans, [_out("2026-11-16", "A", True, 2.0)], [], SINCE, UNTIL, 5)
    assert out["hours"]["actual_over_estimate"] == 2.0
    assert out["hours"]["padding_from_these"] == 1.0, "one sample is below the minimum"


def test_blocks_created_moved_and_retired():
    blocks = [
        {"plan_date": date(2026, 11, 17), "created_for": "2026-11-17", "retired_at": None},
        {"plan_date": date(2026, 11, 19), "created_for": "2026-11-17", "retired_at": None},
        {"plan_date": date(2026, 11, 18), "created_for": "2026-11-18", "retired_at": "x"},
        {"plan_date": date(2026, 11, 18), "created_for": None, "retired_at": None},
    ]
    out = ea_plan.audit_from([], [], blocks, SINCE, UNTIL, 5)["blocks"]
    assert (out["created"], out["moved_off_day"], out["retired"], out["origin_unknown"]) == (4, 1, 1, 1)
    assert "what" in out


def test_audit_is_pure():
    src = pathlib.Path(ea_plan.__file__).read_text()
    body = src.split("def audit_from(")[1].split("\nasync def ")[0]
    for banned in ("await ", "repo.", "jira.", "calendar_facade.", "settings."):
        assert banned not in body, banned


@needs_pg
async def test_audit_reads_the_record_of_its_window():
    year = 3000 + uuid.uuid4().int % 900
    today = date(year, 5, 10)
    inside, edge, outside = date(year, 5, 4), date(year, 5, 9), date(year, 5, 2)
    key = f"AU-{uuid.uuid4().hex[:6]}"
    for d in (inside, edge, outside, today):
        await repo.record_day_plan(plan_date=d, items=[_item(key, estimate=2)],
                                   proposal_id="prop_audit")
    await repo.record_day_plan_outcomes(plan_date=inside, proposal_id="prop_audit",
                                        outcomes=[{"issue_key": key, "done": True,
                                                   "actual_hours": 3}])
    out = await ea_plan.audit(7, today)
    assert out["window"] == {"from": date(year, 5, 3).isoformat(),
                             "to": edge.isoformat(), "days": 7}
    assert out["days_with_plan"] == 2, "only the window's plans; not today's, not before"
    assert (out["items"]["planned"], out["items"]["done"], out["items"]["unknown"]) == (2, 1, 1)
    assert out["hours"]["actual_over_estimate"] == 1.5
    assert out["slipped"]["issues"][0]["slipped_on"] == [edge.isoformat()]
    empty = await ea_plan.audit(7, date(year, 9, 1))
    assert empty["days_with_plan"] == 0 and "no_record" in empty


@needs_pg
async def test_the_ledger_read_gives_each_block_the_date_its_proposal_gave_it():
    agent = "audit-agent-" + uuid.uuid4().hex[:6]
    sid, pid = "sess_au_" + uuid.uuid4().hex[:8], "prop_au_" + uuid.uuid4().hex[:8]
    await repo.upsert_agent(agent, agent, "demo", "test charter")
    await repo.save_paused_session(sid, agent, {})
    create = {"capability": "calendar.create_event", "target_ref": {"system": "calendar", "id": "x"},
              "reversibility": "reversible",
              "arguments": {"title": "t", "plan": {"kind": "work_block", "issue_key": "AU-1",
                                                   "date": "2031-03-03"}}}
    await repo.save_proposal(proposal_id=pid, session_id=sid, agent_id=agent, intent="blocks",
                             actions=[create], evidence=[], expected_effect="",
                             idempotency_key=f"{pid}:0", status="EXECUTED")
    stay, moved, orphan = (f"au_{uuid.uuid4().hex[:8]}" for _ in range(3))
    for eid, prop in ((stay, pid), (moved, pid), (orphan, "prop_not_on_record")):
        await repo.record_calendar_plan_event(event_id=eid, calendar_id="primary",
                                              kind="work_block", issue_key="AU-1",
                                              plan_date="2031-03-03", proposal_id=prop)
    await repo.move_calendar_plan_event_date(moved, "primary", plan_date="2031-03-05")
    await repo.retire_calendar_plan_event(stay, "primary", proposal_id="prop_gone")
    now = datetime.now(timezone.utc)
    rows = {r["event_id"]: r for r in await repo.list_work_blocks_created_between(
        now - timedelta(minutes=5), now + timedelta(minutes=5))}
    assert rows[stay]["created_for"] == "2031-03-03" and rows[stay]["retired_at"] is not None
    assert rows[moved]["created_for"] == "2031-03-03"
    assert str(rows[moved]["plan_date"]) == "2031-03-05"
    assert rows[orphan]["created_for"] is None
    out = ea_plan.audit_from([], [], [rows[stay], rows[moved], rows[orphan]],
                             SINCE, UNTIL, 5)["blocks"]
    assert (out["created"], out["moved_off_day"], out["retired"], out["origin_unknown"]) == (3, 1, 1, 1)


# --- 4. the briefs -----------------------------------------------------------------------------


@pytest.fixture()
def contact(monkeypatch):
    from central_command.api import routes

    monkeypatch.setattr(settings, "dispatch_approval_limit", 10_000)
    monkeypatch.setattr(settings, "ea_contact_budget_per_day", 0)
    monkeypatch.setattr(settings, "plan_quarter_days", 90)
    seen: list[str] = []

    async def fake(brief, title, agent_id, actor=None):
        seen.append((brief, title))
        return {"task_id": "task_wq_test", "session_id": "sess_wq_test"}

    monkeypatch.setattr(routes, "create_and_run_task", fake)
    calls = {"week": [], "deadlines": [], "audit": [], "day": []}

    async def week(start_offset=1, days=7):
        calls["week"].append((start_offset, days))
        return {"available": True, "days": []}

    async def deadlines(days):
        calls["deadlines"].append(days)
        return {"available": True, "issues": [], "window": {"days": days}}

    async def audit(days_back, today=None):
        calls["audit"].append(days_back)
        return {"available": True, "window": {"days": days_back}, "days_with_plan": 0,
                "no_record": "none"}

    async def day(offset=0):
        calls["day"].append(offset)
        return {"available": False, "reason": "not configured"}

    monkeypatch.setattr(ea_calendar, "week", week)
    monkeypatch.setattr(ea_calendar, "day", day)
    monkeypatch.setattr(ea_plan, "deadlines", deadlines)
    monkeypatch.setattr(ea_plan, "audit", audit)
    return seen, calls


def _block_of(brief: str) -> dict:
    return json.loads(brief.split("```json\n")[1].split("\n```")[0])


@needs_pg
async def test_the_weekly_plan_carries_week_deadlines_and_audit(contact):
    seen, calls = contact
    await hb_actions._ea_contact("sched_wq_week", {"kind": "week_ahead"})
    brief, title = seen[0]
    block = _block_of(brief)
    assert title == "Week ahead"
    assert block["week"]["available"] is True and block["deadlines"]["window"]["days"] == 7
    assert block["audit"]["window"]["days"] == 7
    assert calls["week"] == [(1, 7)] and calls["deadlines"] == [7] and calls["audit"] == [7]
    assert calls["day"] == [1], "the calendar block is tomorrow's"
    assert "plan" not in block and "plan_review" not in block
    for phrase in ("`week` is the coming week computed for you", "`deadlines` is every",
                   "`audit` is the record's account"):
        assert phrase in brief, phrase


@needs_pg
async def test_the_quarterly_review_carries_its_blocks_and_no_week(contact):
    seen, calls = contact
    await hb_actions._ea_contact("sched_wq_quarter", {"kind": "quarter_ahead"})
    brief, title = seen[0]
    block = _block_of(brief)
    assert title == "Quarterly review"
    assert "week" not in block and calls["week"] == [], "no week, and no ninety-day read"
    assert calls["day"] == [1], "the calendar block is tomorrow's only"
    assert calls["deadlines"] == [90] and calls["audit"] == [90]
    assert block["audit"]["window"]["days"] == 90 and "lines_of_effort" in block
    assert "plan" not in block and "plan_review" not in block
    assert "`week` is the coming week" not in brief
    assert "QUARTERLY REVIEW" in brief


@pytest.mark.parametrize("kind", ["morning_report", "check_in", "digest"])
@needs_pg
async def test_the_daily_contacts_carry_none_of_them(contact, kind, monkeypatch):
    seen, calls = contact
    monkeypatch.setattr(ea_backlog, "_today", lambda: date(2998, 2, 3))   # no plan stored
    await hb_actions._ea_contact(f"sched_wq_{kind}", {"kind": kind})
    block = _block_of(seen[0][0])
    assert not {"week", "deadlines", "audit"} & set(block)
    assert calls["week"] == calls["deadlines"] == calls["audit"] == []


@needs_pg
async def test_each_block_degrades_on_failure(contact, monkeypatch):
    seen, _ = contact

    async def boom(*a, **k):
        raise ConnectionError("down")

    monkeypatch.setattr(ea_calendar, "week", boom)
    monkeypatch.setattr(ea_plan, "deadlines", boom)
    monkeypatch.setattr(ea_plan, "audit", boom)
    out = await hb_actions._ea_contact("sched_wq_down", {"kind": "week_ahead"})
    assert out["delivered"] is True
    block = _block_of(seen[0][0])
    for key in ("week", "deadlines", "audit"):
        assert block[key] == {"available": False, "error": "down"}, key


@needs_pg
async def test_unconfigured_integrations_omit_week_and_deadlines(contact, monkeypatch):
    seen, _ = contact

    async def unconfigured(*a, **k):
        return {"available": False, "reason": "not configured"}

    monkeypatch.setattr(ea_calendar, "week", unconfigured)
    monkeypatch.setattr(ea_plan, "deadlines", unconfigured)
    await hb_actions._ea_contact("sched_wq_none", {"kind": "week_ahead"})
    block = _block_of(seen[0][0])
    assert "week" not in block and "deadlines" not in block and "audit" in block


# --- 5. the new kind, everywhere a kind is checked ----------------------------------------------


def test_the_quarterly_kind_is_accepted_everywhere_and_a_misspelling_refused():
    assert "quarter_ahead" in hb_actions.EA_CONTACT_KINDS
    spec = hb_actions.ACTIONS["ea.contact"]
    assert spec.choices["kind"] == hb_actions.EA_CONTACT_KINDS
    assert "quarter_ahead" in spec.params["kind"] and "quarterly review" in spec.description
    assert hb_actions._CALENDAR_DAY["quarter_ahead"] == 1
    assert "quarter_ahead" in hb_actions._EA_FRAMING
    hb_actions.validate_action("ea.contact", {"kind": "quarter_ahead"})
    for wrong in ("quarterly", "quarter-ahead", "Quarter_ahead"):
        with pytest.raises(ValueError, match="must be one of"):
            hb_actions.validate_action("ea.contact", {"kind": wrong})


async def test_the_action_refuses_a_misspelt_kind_written_straight_in_sql():
    with pytest.raises(ValueError, match="must be one of"):
        await hb_actions._ea_contact("sched_wq_bad", {"kind": "quarterly"})


# --- 6. the quarterly seed --------------------------------------------------------------------------


_SEED = re.compile(
    r"\('ea-quarter-review',\s*'EA quarterly review',\s*'cron',\s*"
    r"'(\{[^']*\})',\s*'ea\.contact',\s*'(\{[^']*\})',\s*'seed:heartbeat'\)")


def test_the_quarterly_seed_is_declared_disabled_and_idempotent():
    m = _SEED.search(SCHEMA)
    assert m, "the quarterly review schedule is seeded"
    assert json.loads(m.group(1)) == {"expr": "0 9 1 1,4,7,10 *", "tz": "America/Denver"}
    assert json.loads(m.group(2)) == {"kind": "quarter_ahead"}
    statement = SCHEMA[SCHEMA.rindex("insert into", 0, m.start()):]
    statement = statement[:statement.index(";") + 1]
    assert "enabled" not in statement.split("values")[0], "born disabled: the table default"
    assert "on conflict (id) do nothing" in statement
    hb_actions.validate_action("ea.contact", json.loads(m.group(2)))


@needs_pg
async def test_the_quarterly_seed_is_present_disabled_and_left_alone_on_a_rerun():
    conn = await repo._conn()
    try:
        row = await conn.fetchrow("select * from heartbeat_schedule where id = 'ea-quarter-review'")
        assert row is not None and row["enabled"] is False
        assert row["action_kind"] == "ea.contact"
        await conn.execute("update heartbeat_schedule set enabled = true, "
                           "schedule = '{\"expr\": \"0 8 1 1,4,7,10 *\"}'::jsonb "
                           "where id = 'ea-quarter-review'")
        m = _SEED.search(SCHEMA)
        statement = SCHEMA[SCHEMA.rindex("insert into", 0, m.start()):]
        await conn.execute(statement[:statement.index(";") + 1])
        again = await conn.fetchrow(
            "select * from heartbeat_schedule where id = 'ea-quarter-review'")
        assert again["enabled"] is True, "a re-run never overwrites the operator's choice"
        sched = again["schedule"]
        sched = json.loads(sched) if isinstance(sched, str) else sched
        assert sched["expr"] == "0 8 1 1,4,7,10 *"
    finally:
        await conn.execute("update heartbeat_schedule set enabled = false, "
                           "schedule = '{\"expr\": \"0 9 1 1,4,7,10 *\", \"tz\": \"America/Denver\"}'::jsonb "
                           "where id = 'ea-quarter-review'")
        await conn.close()


def test_the_quarterly_cron_fires_on_the_first_of_each_quarter():
    from croniter import croniter

    tz = ZoneInfo(TZ)
    it = croniter("0 9 1 1,4,7,10 *", datetime(2026, 11, 19, tzinfo=tz))
    fires = [it.get_next(datetime) for _ in range(4)]
    assert [(f.year, f.month, f.day, f.hour) for f in fires] == [
        (2027, 1, 1, 9), (2027, 4, 1, 9), (2027, 7, 1, 9), (2027, 10, 1, 9)]


# --- 7. framings and charter ---------------------------------------------------------------------


def test_the_weekly_framing_is_the_plan():
    text = hb_actions._EA_FRAMING["week_ahead"]
    for phrase in ("WEEKLY PLAN", "top three to five priorities", "the reason it is a priority",
                   "`deadlines`", "`none` or `partly`", "`unestimated`",
                   "as ONE proposal through `propose_calendar_change`",
                   "`calendar.create_event`", '"kind": "work_block"',
                   "the day the block sits on", "Propose no `plan.record_day`",
                   "each line of effort", "Read `audit` back", "what kept slipping",
                   "dropped or delegated", "`no_record`", "never present that as a clean week",
                   "overbooked day", "unresolved conflict", "availability UNKNOWN",
                   "offer placeholders", "do not read the week's days yourself",
                   "Never propose a change to a commitment unless the operator asked",
                   "never restate or re-ask one", "Decisions Inbox"):
        assert phrase in text, phrase
    assert "read_calendar" not in text, "the week is computed for it"


def test_the_quarterly_framing_is_the_review():
    text = hb_actions._EA_FRAMING["quarter_ahead"]
    for phrase in ("QUARTERLY REVIEW", "where the planned time went", "what was finished",
                   "dropped, delegated or re-scoped", "actual to estimated hours",
                   "`no_record`", "priorities of the coming quarter",
                   "already dated in it (`deadlines`", "line of effort",
                   "standing rules", "graph.add_episode",
                   "ONE `jira.set_planning_fields` proposal", "after you conclude",
                   "Propose no calendar change beyond what the operator asks for",
                   "never restate or re-ask one"):
        assert phrase in text, phrase


def test_the_charter_names_the_rhythm():
    c = ea_profile.CHARTER
    for phrase in ("THE RHYTHM", "The morning is the PLAN", "mid-day is a REPLAN",
                   "only when the day has slipped", "the evening is the REVIEW",
                   "WEEKLY PLAN", "QUARTERLY REVIEW", "resets the priorities",
                   "Each contact's blocks are computed for you",
                   "the record's account of where planned time went",
                   "including when it is unflattering"):
        assert phrase in c, phrase
    # Nothing still true was replaced.
    for kept in ("THE STORED PLAN IS THE RECORD OF WHAT WAS PLANNED", "PLAN FIRST",
                 "OTHER AGENTS' QUESTIONS ARE NOT YOURS TO ASK"):
        assert kept in c, kept
