"""Step 3 of the EA planning design (DL-221, FR-54; the design record's D2 and
D5): free time computed in code, the plan ledger that tells the assistant's own
events (work blocks and placeholders) from the operator's commitments, the
`plan` argument and the `calendar.adopt_event` capability, the ledger's
retirement on delete, the brief's annotation, the framings and the charter.

NO NETWORK: every calendar call goes to a fake (`calendar_facade._call`
replaced, or an `httpx.MockTransport` Google), and every approval that executes
runs under `tests/fake_world.py`.
"""

from __future__ import annotations

import pathlib
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
import pytest
from pydantic_ai import CallDeferred, ModelRetry
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from central_command.config import settings
from central_command.contract import Action, Proposal, Reversibility, validate_action_args
from central_command.db import repo
from central_command.gateway import capabilities, executor
from central_command.heartbeat import actions as hb_actions
from central_command.integrations import calendar_facade
from central_command.reports import ea_calendar
from central_command.runtime import ea_profile, packs, tools
from tests.conftest import needs_pg
from tests.fake_world import fake_world
from tests.test_action_key import (  # noqa: F401 — fixtures, used by name
    _approve_parks_then_sweep_converges,
    google,
    settled,
)

TZ = "America/Denver"
DENVER = ZoneInfo(TZ)
_ORIGINAL_CLIENT = httpx.AsyncClient


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    monkeypatch.setattr(settings, "test_model", True)
    monkeypatch.setattr(settings, "ea_timezone", TZ)
    monkeypatch.setattr(settings, "plan_day_start", "08:00")
    monkeypatch.setattr(settings, "plan_day_end", "18:00")
    monkeypatch.setattr(settings, "plan_min_block_minutes", 30)


def _at(hh: int, mm: int = 0, day: date = date(2026, 11, 20)) -> datetime:
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=DENVER)


def _ev(start: datetime, end: datetime, **kw) -> dict:
    base = {"event_id": kw.pop("event_id", f"e{uuid.uuid4().hex[:8]}"),
            "start": start.isoformat(), "end": end.isoformat(), "all_day": False,
            "summary": "Meeting", "declined": False}
    base.update(kw)
    return base


def _stretches(out):
    return [(datetime.fromisoformat(s["from"]).strftime("%H:%M"),
             datetime.fromisoformat(s["to"]).strftime("%H:%M"), s["minutes"]) for s in out]


# --- 1. free time, computed in code ------------------------------------------------


def test_no_events_is_the_whole_window():
    out = ea_calendar.free_time([], _at(8), _at(18), 30)
    assert _stretches(out) == [("08:00", "18:00", 600)]


def test_one_event_splits_the_window():
    out = ea_calendar.free_time([_ev(_at(10), _at(11))], _at(8), _at(18), 30)
    assert _stretches(out) == [("08:00", "10:00", 120), ("11:00", "18:00", 420)]


def test_overlapping_events_are_merged():
    evs = [_ev(_at(10), _at(12)), _ev(_at(11), _at(13)), _ev(_at(11, 30), _at(11, 45))]
    out = ea_calendar.free_time(evs, _at(8), _at(18), 30)
    assert _stretches(out) == [("08:00", "10:00", 120), ("13:00", "18:00", 300)]


def test_back_to_back_events_leave_no_gap():
    evs = [_ev(_at(9), _at(10)), _ev(_at(10), _at(11))]
    out = ea_calendar.free_time(evs, _at(8), _at(18), 30)
    assert _stretches(out) == [("08:00", "09:00", 60), ("11:00", "18:00", 420)]


def test_unsorted_input_is_sorted():
    evs = [_ev(_at(15), _at(16)), _ev(_at(9), _at(10))]
    out = ea_calendar.free_time(evs, _at(8), _at(18), 30)
    assert _stretches(out) == [("08:00", "09:00", 60), ("10:00", "15:00", 300),
                               ("16:00", "18:00", 120)]


def test_events_are_clipped_at_both_ends_of_the_window():
    evs = [_ev(_at(6), _at(9)), _ev(_at(17), _at(20))]
    out = ea_calendar.free_time(evs, _at(8), _at(18), 30)
    assert _stretches(out) == [("09:00", "17:00", 480)]


def test_an_event_covering_the_whole_window_leaves_nothing():
    out = ea_calendar.free_time([_ev(_at(7), _at(19))], _at(8), _at(18), 30)
    assert out == []


def test_events_wholly_outside_the_window_change_nothing():
    evs = [_ev(_at(6), _at(7)), _ev(_at(19), _at(20)),
           _ev(_at(9, day=date(2026, 11, 21)), _at(10, day=date(2026, 11, 21)))]
    out = ea_calendar.free_time(evs, _at(8), _at(18), 30)
    assert _stretches(out) == [("08:00", "18:00", 600)]


def test_all_day_and_declined_events_are_not_commitments():
    evs = [
        {"start": "2026-11-20", "end": "2026-11-21", "all_day": True, "summary": "Vacation"},
        _ev(_at(10), _at(12), declined=True),
        _ev(_at(14), _at(15)),
    ]
    out = ea_calendar.free_time(evs, _at(8), _at(18), 30)
    assert _stretches(out) == [("08:00", "14:00", 360), ("15:00", "18:00", 180)]


def test_an_event_without_a_start_or_end_is_skipped_and_a_naive_one_is_local():
    evs = [{"summary": "broken", "start": None, "end": None},
           {"summary": "naive", "start": "2026-11-20T09:00:00", "end": "2026-11-20T10:00:00"}]
    out = ea_calendar.free_time(evs, _at(8), _at(18), 30)
    assert _stretches(out) == [("08:00", "09:00", 60), ("10:00", "18:00", 480)]


def test_a_stretch_shorter_than_the_minimum_block_is_dropped():
    evs = [_ev(_at(8, 20), _at(9)), _ev(_at(9, 29), _at(17, 30))]
    out = ea_calendar.free_time(evs, _at(8), _at(18), 30)
    # 08:00-08:20 (20) and 09:00-09:29 (29) are under 30; 17:30-18:00 is exactly 30.
    assert _stretches(out) == [("17:30", "18:00", 30)]


def test_an_empty_or_inverted_window_has_no_free_time():
    assert ea_calendar.free_time([], _at(18), _at(18), 30) == []
    assert ea_calendar.free_time([], _at(19), _at(18), 30) == []


def test_a_different_offset_on_an_event_is_compared_as_an_instant():
    utc = timezone.utc
    ev = {"start": datetime(2026, 11, 20, 17, 0, tzinfo=utc).isoformat(),  # 10:00 Denver
          "end": datetime(2026, 11, 20, 18, 0, tzinfo=utc).isoformat().replace("+00:00", "Z"),
          "all_day": False, "declined": False}
    out = ea_calendar.free_time([ev], _at(8), _at(18), 30)
    assert _stretches(out) == [("08:00", "10:00", 120), ("11:00", "18:00", 420)]


def test_the_working_window_comes_from_the_settings_and_starts_at_now_today():
    day = date(2026, 11, 20)
    assert ea_calendar.working_window(day) == (_at(8), _at(18))
    assert ea_calendar.working_window(day, now=_at(13, 15)) == (_at(13, 15), _at(18))
    assert ea_calendar.working_window(day, now=_at(7)) == (_at(8), _at(18)), (
        "now before the start leaves the window where it is")
    start, end = ea_calendar.working_window(day, now=_at(19))
    assert start > end and ea_calendar.free_time([], start, end, 30) == []


def test_a_window_whose_end_is_not_after_its_start_is_refused(monkeypatch):
    monkeypatch.setattr(settings, "plan_day_end", "08:00")
    with pytest.raises(ValueError, match="plan_day_end"):
        ea_calendar.working_window(date(2026, 11, 20))


@pytest.mark.parametrize("bad", ["8am", "25:00", "08:61", "", "08"])
def test_the_settings_refuse_a_time_that_is_not_hh_mm(bad):
    from central_command.config import Settings

    with pytest.raises(ValueError):
        Settings(plan_day_start=bad)


def test_the_minimum_block_setting_must_be_positive():
    from central_command.config import Settings

    with pytest.raises(ValueError):
        Settings(plan_min_block_minutes=0)
    assert Settings().plan_min_block_minutes == 30
    assert (Settings().plan_day_start, Settings().plan_day_end) == ("08:00", "18:00")


class _Facade:
    def __init__(self, events_, truncated=False):
        self.events, self.truncated = events_, truncated

    async def __call__(self, time_min, time_max, calendar_id="primary"):
        return {"events": self.events, "truncated": self.truncated}


def _frozen_now(monkeypatch, now: datetime):
    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz) if tz else now

    monkeypatch.setattr(ea_calendar, "datetime", _Frozen)


@pytest.fixture()
def calendar_on(monkeypatch):
    monkeypatch.setattr(settings, "calendar_facade_url", "http://n8n.example.com/webhook/cal")
    monkeypatch.setattr(settings, "calendar_facade_token", "tok")
    monkeypatch.setattr(calendar_facade, "_exchange", lambda: None)


async def test_today_s_free_time_starts_at_now(monkeypatch, calendar_on):
    _frozen_now(monkeypatch, _at(13, 10))
    monkeypatch.setattr(calendar_facade, "list_events", _Facade([_ev(_at(15), _at(16))]))
    out = await ea_calendar.day(0)
    assert _stretches(out["free_time"]) == [("13:10", "15:00", 110), ("16:00", "18:00", 120)]
    assert out["free_minutes"] == 230


async def test_tomorrow_s_free_time_is_the_whole_working_window(monkeypatch, calendar_on):
    _frozen_now(monkeypatch, _at(17))
    tomorrow = date(2026, 11, 21)
    monkeypatch.setattr(calendar_facade, "list_events", _Facade(
        [_ev(_at(12, day=tomorrow), _at(13, day=tomorrow))]))
    out = await ea_calendar.day(1)
    assert _stretches(out["free_time"]) == [("08:00", "12:00", 240), ("13:00", "18:00", 300)]
    assert out["free_time"][0]["from"].startswith("2026-11-21T08:00")


async def test_after_the_working_day_today_has_no_free_time(monkeypatch, calendar_on):
    _frozen_now(monkeypatch, _at(19))
    monkeypatch.setattr(calendar_facade, "list_events", _Facade([]))
    out = await ea_calendar.day(0)
    assert out["available"] is True and out["free_time"] == [] and out["free_minutes"] == 0


async def test_a_day_that_is_not_available_carries_no_free_time_key(monkeypatch, calendar_on):
    monkeypatch.setattr(calendar_facade, "list_events", _Facade([], truncated=True))
    out = await ea_calendar.day(0)
    assert out["available"] is False
    assert "free_time" not in out and "free_minutes" not in out
    monkeypatch.setattr(settings, "calendar_facade_token", "")
    monkeypatch.setattr(calendar_facade, "_exchange", lambda: None)
    out = await ea_calendar.day(0)
    assert out == {"available": False, "reason": "not configured"}


def test_the_calendar_report_reads_no_database():
    src = pathlib.Path(ea_calendar.__file__).read_text(encoding="utf-8")
    assert "central_command.db" not in src and "repo." not in src


# --- 2. the `plan` argument's shape, in both tiers -----------------------------------


_EVENT = {"title": "DOE-1 Draft the budget", "start": "2026-11-20T09:00:00-07:00",
          "end": "2026-11-20T10:00:00-07:00"}


@pytest.mark.parametrize("plan,problem", [
    (None, None),
    ({"kind": "placeholder"}, None),
    ({"kind": "work_block", "issue_key": "DOE-1", "date": "2026-11-20"}, None),
    ({"kind": "work_block", "date": "2026-11-20"}, "plan.issue_key is required"),
    ({"kind": "work_block", "issue_key": " ", "date": "2026-11-20"}, "plan.issue_key is required"),
    ({"kind": "work_block", "issue_key": "DOE-1"}, "plan.date must be"),
    ({"kind": "work_block", "issue_key": "DOE-1", "date": "2026-11-31"}, "plan.date must be"),
    ({"kind": "work_block", "issue_key": "DOE-1", "date": "2026-11-20T09:00"}, "plan.date must be"),
    ({"kind": "placeholder", "issue_key": "DOE-1"}, "plan.issue_key is refused for a placeholder"),
    ({"kind": "placeholder", "date": "2026-11-20"}, "plan.date is refused for a placeholder"),
    ({"kind": "focus"}, "plan.kind='focus' is not one of placeholder, work_block"),
    ({}, "plan.kind=None"),
    ({"kind": "placeholder", "colour": "red"}, "unknown key(s) colour"),
    ("work_block", "plan must be an object"),
])
def test_the_plan_shape(plan, problem):
    args = dict(_EVENT, **({"plan": plan} if plan is not None else {}))
    problems = validate_action_args("calendar.create_event", args)
    if problem is None:
        assert problems == []
    else:
        assert any(problem in p for p in problems), problems


def _calendar_proposal(*actions: dict) -> Proposal:
    return Proposal(
        intent="Block today's work", expected_effect="blocks on the calendar", evidence=[],
        confidence={"level": "high", "rationale": "The ranked backlog says so."},
        actions=[Action(capability=a.pop("_cap", "calendar.create_event"), arguments=a,
                        target_ref={"system": "calendar", "id": "primary"},
                        reversibility=Reversibility.reversible) for a in actions],
    )


async def test_the_runtime_hands_a_bad_plan_back_to_the_model():
    bad = _calendar_proposal(dict(_EVENT, plan={"kind": "work_block", "issue_key": "DOE-1"}))
    with pytest.raises(ModelRetry, match="plan.date must be"):
        await tools.propose_calendar_change(None, bad)
    good = _calendar_proposal(dict(_EVENT, plan={"kind": "work_block", "issue_key": "DOE-1",
                                                 "date": "2026-11-20"}))
    with pytest.raises(CallDeferred):
        await tools.propose_calendar_change(None, good)


async def test_the_executor_refuses_a_bad_plan_before_anything_runs(monkeypatch):
    log = fake_world(monkeypatch)
    actions = [Action(capability="calendar.create_event", arguments=dict(_EVENT),
                      target_ref={"system": "calendar", "id": "primary"},
                      reversibility=Reversibility.reversible),
               Action(capability="calendar.create_event",
                      arguments=dict(_EVENT, plan={"kind": "placeholder", "date": "2026-11-20"}),
                      target_ref={"system": "calendar", "id": "primary"},
                      reversibility=Reversibility.reversible)]
    with pytest.raises(executor.ExecutionFailed, match="refused for a placeholder") as exc:
        await executor.execute(actions, "human:lee", [], proposal_id="prop_shape")
    assert exc.value.completed == [] and log == [], "the first action never ran"


def test_adopt_has_a_spec_and_its_kind_is_fixed():
    assert validate_action_args("calendar.adopt_event",
                                {"event_id": "e1", "kind": "placeholder"}) == []
    assert validate_action_args("calendar.adopt_event", {"event_id": "e1"})
    assert validate_action_args("calendar.adopt_event", {"event_id": "e1", "kind": "work_block"})


# --- 3. the ledger written on create, from the result's id ----------------------------


def _create(title="DOE-1 Draft the budget", plan=None) -> Action:
    args = dict(_EVENT, title=title)
    if plan is not None:
        args["plan"] = plan
    return Action(capability="calendar.create_event", arguments=args,
                  target_ref={"system": "calendar", "id": "primary"},
                  reversibility=Reversibility.reversible)


_BLOCK = {"kind": "work_block", "issue_key": "DOE-1", "date": "2026-11-20"}


def _pid() -> str:
    return f"prop_led{uuid.uuid4().hex[:12]}"


async def _rows(event_id: str) -> list[dict]:
    conn = await repo._conn()
    try:
        return [dict(r) for r in await conn.fetch(
            "select * from calendar_plan_event where event_id = $1 order by id", event_id)]
    finally:
        await conn.close()


@needs_pg
async def test_a_planned_create_writes_one_ledger_row_from_the_result_id(google):
    pid = _pid()
    out = await executor.execute([_create(plan=_BLOCK)], "human:lee", [], proposal_id=pid)
    (sent,) = google.payloads
    assert "plan" not in sent, "plan is control-plane metadata, never sent to the provider"
    event_id = executor.google_event_id(f"{pid}:0")
    (row,) = await _rows(event_id)
    assert (row["kind"], row["issue_key"], row["plan_date"], row["calendar_id"],
            row["proposal_id"], row["retired_at"]) == (
        "work_block", "DOE-1", date(2026, 11, 20), "primary", pid, None)
    assert "ledger: work block for DOE-1 on 2026-11-20" in out.result_text


@needs_pg
async def test_the_row_uses_the_provider_s_id_never_one_the_agent_wrote(monkeypatch):
    fake_world(monkeypatch, real=("calendar.create_event",))
    monkeypatch.setattr(calendar_facade, "_exchange", lambda: None)
    monkeypatch.setattr(settings, "calendar_facade_token", "tok")
    returned = f"evt_{uuid.uuid4().hex[:10]}"

    async def _call(payload, timeout=60.0):
        return {"ok": True, "event": {"event_id": returned}}

    monkeypatch.setattr(calendar_facade, "_call", _call)
    action = _create(plan=_BLOCK)
    action.arguments["event_id"] = "agent-invented"
    await executor.execute([action], "human:lee", [], proposal_id=_pid())
    assert len(await _rows(returned)) == 1
    assert await _rows("agent-invented") == []


@needs_pg
async def test_a_retried_create_that_reads_its_event_back_ends_with_one_row(google):
    pid = _pid()
    await executor.execute([_create(plan=_BLOCK)], "human:lee", [], proposal_id=pid)
    again = await executor.execute([_create(plan=_BLOCK)], "human:lee", [], proposal_id=pid)
    assert "already created by an earlier attempt" in again.result_text
    assert "ledger: already recorded as work block" in again.result_text
    assert len(await _rows(executor.google_event_id(f"{pid}:0"))) == 1


@needs_pg
async def test_a_ledger_write_that_fails_is_retried_into_one_event_and_one_row(
        google, monkeypatch):
    """The create landed, the ledger write raised: the retry asks Google for the
    same id, reads its own event back, and only then writes the row."""
    pid = _pid()
    real = repo.record_calendar_plan_event
    calls = {"n": 0}

    async def flaky(**kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionRefusedError("[Errno 111] Connection refused")
        return await real(**kw)

    monkeypatch.setattr(repo, "record_calendar_plan_event", flaky)
    with pytest.raises(executor.ExecutionFailed) as exc:
        await executor.execute([_create(plan=_BLOCK)], "human:lee", [], proposal_id=pid)
    assert exc.value.transient is True, "a database that refused is down, not a NO"
    assert await _rows(executor.google_event_id(f"{pid}:0")) == []
    await executor.execute([_create(plan=_BLOCK)], "human:lee", [], proposal_id=pid)
    assert len(google.events) == 1
    assert len(await _rows(executor.google_event_id(f"{pid}:0"))) == 1


@needs_pg
async def test_a_failed_create_writes_nothing(google):
    pid = _pid()
    google.events[executor.google_event_id(f"{pid}:0")] = {"summary": "x", "status": "cancelled"}
    with pytest.raises(executor.ExecutionFailed):
        await executor.execute([_create(plan=_BLOCK)], "human:lee", [], proposal_id=pid)
    assert await _rows(executor.google_event_id(f"{pid}:0")) == []


@needs_pg
async def test_a_create_without_a_plan_writes_no_row(google):
    pid = _pid()
    out = await executor.execute([_create()], "human:lee", [], proposal_id=pid)
    assert "ledger" not in out.result_text
    assert await _rows(executor.google_event_id(f"{pid}:0")) == []


@needs_pg
async def test_a_placeholder_create_writes_a_placeholder_row(google):
    pid = _pid()
    await executor.execute([_create("Focus", plan={"kind": "placeholder"})], "human:lee", [],
                           proposal_id=pid)
    (row,) = await _rows(executor.google_event_id(f"{pid}:0"))
    assert (row["kind"], row["issue_key"], row["plan_date"]) == ("placeholder", None, None)


@needs_pg
async def test_a_create_that_returns_no_id_cannot_write_its_row(monkeypatch):
    fake_world(monkeypatch, real=("calendar.create_event",))
    monkeypatch.setattr(calendar_facade, "_exchange", lambda: None)
    monkeypatch.setattr(settings, "calendar_facade_token", "tok")

    async def _call(payload, timeout=60.0):
        return {"ok": True, "event": {}}

    monkeypatch.setattr(calendar_facade, "_call", _call)
    with pytest.raises(executor.ExecutionFailed, match="no event id"):
        await executor.execute([_create(plan=_BLOCK)], "human:lee", [], proposal_id=_pid())


@needs_pg
async def test_the_schema_declares_the_ledger_idempotently():
    sql = (pathlib.Path(repo.__file__).with_name("schema.sql")).read_text(encoding="utf-8")
    assert "create table if not exists calendar_plan_event (" in sql
    assert ("create unique index if not exists calendar_plan_event_active_uq\n"
            "    on calendar_plan_event (calendar_id, event_id) where retired_at is null;") in sql
    conn = await repo._conn()
    try:
        with pytest.raises(Exception, match="check"):
            await conn.execute(
                "insert into calendar_plan_event (event_id, kind) values ($1, 'work_block')",
                f"chk_{uuid.uuid4().hex}")
        with pytest.raises(Exception, match="check"):
            await conn.execute(
                "insert into calendar_plan_event (event_id, kind, issue_key) "
                "values ($1, 'placeholder', 'DOE-1')", f"chk_{uuid.uuid4().hex}")
    finally:
        await conn.close()


def test_only_the_executor_writes_the_ledger():
    pkg = pathlib.Path(repo.__file__).parents[1]
    offenders = []
    for path in sorted(pkg.rglob("*.py")):
        rel = path.relative_to(pkg).as_posix()
        if rel in ("db/repo.py", "gateway/executor.py"):
            continue
        src = path.read_text(encoding="utf-8")
        for name in ("record_calendar_plan_event", "retire_calendar_plan_event"):
            if re.search(rf"\b{name}\(", src):
                offenders.append((rel, name))
        if re.search(r"(insert into|update)\s+calendar_plan_event", src, re.I):
            offenders.append((rel, "sql"))
    assert offenders == []


# --- 4. adoption -------------------------------------------------------------------


def _adopt(event_id: str) -> Action:
    return Action(capability="calendar.adopt_event",
                  arguments={"event_id": event_id, "kind": "placeholder"},
                  target_ref={"system": "calendar", "id": event_id},
                  reversibility=Reversibility.reversible)


@pytest.fixture()
def reads(monkeypatch):
    """A façade whose get_event knows `known` and answers ok:false otherwise;
    any other mode is a test failure (adoption changes nothing)."""
    fake_world(monkeypatch, real=("calendar.adopt_event", "calendar.delete_event"))
    monkeypatch.setattr(calendar_facade, "_exchange", lambda: None)
    monkeypatch.setattr(settings, "calendar_facade_token", "tok")
    state = {"known": set(), "modes": []}

    async def _call(payload, timeout=60.0):
        state["modes"].append(payload["mode"])
        if payload["mode"] == "get_event":
            if payload["event_id"] in state["known"]:
                return {"ok": True, "event": {
                    "event_id": payload["event_id"], "summary": "Weekly review",
                    "start": "2026-11-20T15:00:00-07:00", "end": "2026-11-20T16:00:00-07:00",
                    "attendee_count": 0, "organizer": "doe@example.com"}}
            return {"ok": False, "message": "get_event - event not found"}
        if payload["mode"] == "delete_event":
            return {"ok": True, "deleted": payload["event_id"]}
        raise AssertionError(f"unexpected mode {payload['mode']}")

    monkeypatch.setattr(calendar_facade, "_call", _call)
    return state


@needs_pg
async def test_adopting_an_existing_event_writes_a_placeholder_row_and_changes_nothing(reads):
    event_id = f"adopt_{uuid.uuid4().hex[:10]}"
    reads["known"].add(event_id)
    pid = _pid()
    out = await executor.execute([_adopt(event_id)], "human:lee", [], proposal_id=pid)
    assert reads["modes"] == ["get_event"], "adoption reads and never writes the calendar"
    (row,) = await _rows(event_id)
    assert (row["kind"], row["proposal_id"], row["issue_key"]) == ("placeholder", pid, None)
    assert "adopted as a placeholder" in out.result_text and "calendar unchanged" in out.result_text
    again = await executor.execute([_adopt(event_id)], "human:lee", [], proposal_id=pid)
    assert "already adopted as a placeholder by this proposal" in again.result_text
    assert len(await _rows(event_id)) == 1


@needs_pg
async def test_adopting_a_missing_event_is_a_semantic_refusal_and_writes_nothing(reads):
    event_id = f"gone_{uuid.uuid4().hex[:10]}"
    with pytest.raises(executor.ExecutionFailed) as exc:
        await executor.execute([_adopt(event_id)], "human:lee", [], proposal_id=_pid())
    assert exc.value.transient is False and exc.value.failed_capability == "calendar.adopt_event"
    assert await _rows(event_id) == []


@needs_pg
async def test_adopting_while_the_calendar_is_down_parks_and_writes_nothing(monkeypatch, reads):
    async def down(payload, timeout=60.0):
        raise httpx.ConnectError("[Errno 111] Connection refused")

    monkeypatch.setattr(calendar_facade, "_call", down)
    event_id = f"down_{uuid.uuid4().hex[:10]}"
    with pytest.raises(executor.ExecutionFailed) as exc:
        await executor.execute([_adopt(event_id)], "human:lee", [], proposal_id=_pid())
    assert exc.value.transient is True and exc.value.dependencies == ("calendar",)
    assert await _rows(event_id) == []


@needs_pg
async def test_adopting_an_event_already_in_the_ledger_is_refused(reads):
    event_id = f"twice_{uuid.uuid4().hex[:10]}"
    reads["known"].add(event_id)
    first = _pid()
    await repo.record_calendar_plan_event(event_id=event_id, calendar_id="primary",
                                          kind="work_block", issue_key="DOE-2",
                                          plan_date="2026-11-20", proposal_id=first)
    with pytest.raises(executor.ExecutionFailed, match="already in the plan ledger as a work block"):
        await executor.execute([_adopt(event_id)], "human:lee", [], proposal_id=_pid())
    (row,) = await _rows(event_id)
    assert row["kind"] == "work_block" and row["proposal_id"] == first


# --- 5. retirement on delete; update leaves the row ---------------------------------


def _delete(event_id: str) -> Action:
    return Action(capability="calendar.delete_event",
                  arguments={"event_id": event_id, "reason": "the operator dropped it"},
                  target_ref={"system": "calendar", "id": event_id},
                  reversibility=Reversibility.irreversible)


@needs_pg
async def test_deleting_a_ledger_event_retires_its_row_after_the_delete(reads):
    event_id = f"del_{uuid.uuid4().hex[:10]}"
    reads["known"].add(event_id)
    await repo.record_calendar_plan_event(event_id=event_id, calendar_id="primary",
                                          kind="work_block", issue_key="DOE-3",
                                          plan_date="2026-11-20", proposal_id="prop_made")
    pid = _pid()
    out = await executor.execute([_delete(event_id)], "human:lee", [], proposal_id=pid)
    assert reads["modes"] == ["get_event", "delete_event"]
    assert "ledger: work block row retired" in out.result_text
    (row,) = await _rows(event_id)
    assert row["retired_at"] is not None and row["retired_by"] == pid
    assert await repo.get_calendar_plan_event(event_id) is None


@needs_pg
async def test_deleting_a_commitment_touches_no_ledger_row(reads):
    event_id = f"commit_{uuid.uuid4().hex[:10]}"
    reads["known"].add(event_id)
    out = await executor.execute([_delete(event_id)], "human:lee", [], proposal_id=_pid())
    assert "ledger" not in out.result_text and await _rows(event_id) == []


@needs_pg
async def test_a_failed_delete_retires_nothing(monkeypatch, reads):
    event_id = f"keep_{uuid.uuid4().hex[:10]}"
    await repo.record_calendar_plan_event(event_id=event_id, calendar_id="primary",
                                          kind="placeholder", proposal_id="prop_made")
    with pytest.raises(executor.ExecutionFailed):
        await executor.execute([_delete(event_id)], "human:lee", [], proposal_id=_pid())
    assert (await _rows(event_id))[0]["retired_at"] is None


@needs_pg
async def test_an_update_leaves_the_ledger_row_as_it_is(monkeypatch):
    fake_world(monkeypatch, real=("calendar.update_event",))
    monkeypatch.setattr(calendar_facade, "_exchange", lambda: None)
    monkeypatch.setattr(settings, "calendar_facade_token", "tok")
    event_id = f"upd_{uuid.uuid4().hex[:10]}"

    async def _call(payload, timeout=60.0):
        return {"ok": True, "event": {"event_id": payload["event_id"]}}

    monkeypatch.setattr(calendar_facade, "_call", _call)
    await repo.record_calendar_plan_event(event_id=event_id, calendar_id="primary",
                                          kind="work_block", issue_key="DOE-4",
                                          plan_date="2026-11-20", proposal_id="prop_made")
    await executor.execute([Action(
        capability="calendar.update_event",
        arguments={"event_id": event_id, "start": "2026-11-20T14:00:00-07:00",
                   "end": "2026-11-20T15:00:00-07:00"},
        target_ref={"system": "calendar", "id": event_id},
        reversibility=Reversibility.reversible)], "human:lee", [], proposal_id=_pid())
    (row,) = await _rows(event_id)
    assert row["retired_at"] is None and row["issue_key"] == "DOE-4"


# --- 6. the capability touches every place a capability must ---------------------------


def test_adopt_is_a_complete_capability():
    from central_command.contract import ARG_SPECS
    from central_command.contract.dependencies import CAPABILITY_DEPENDENCY

    row = next(c for c in capabilities.REGISTRY if c.name == "calendar.adopt_event")
    assert (row.kind, row.gate, row.default_gate, row.reversibility, row.acknowledge,
            row.target_args) == ("write", "human approval", "human", "reversible", False,
                                 ("event_id",))
    assert executor.HANDLERS["calendar.adopt_event"] is executor._calendar_adopt_event
    assert ARG_SPECS["calendar.adopt_event"].required == ("event_id", "kind")
    assert CAPABILITY_DEPENDENCY["calendar.adopt_event"] == ("calendar",)
    pack = packs.PACKS["calendar-propose"]
    assert "calendar.adopt_event" in {c.name for c in pack.capabilities}
    section = packs.charter_section(("calendar-read", "calendar-propose"))
    assert "calendar.adopt_event" in section and "'plan'?" in section
    assert "changes nothing on the calendar" in section


# --- 7. the brief knows which event is which ----------------------------------------


@needs_pg
async def test_the_annotation_marks_each_event_from_the_ledger():
    block_id, place_id, gone_id = (f"{p}_{uuid.uuid4().hex[:10]}" for p in ("wb", "ph", "past"))
    today = "2031-03-04"  # a date no other test plans on
    await repo.record_calendar_plan_event(event_id=block_id, calendar_id="primary",
                                          kind="work_block", issue_key="DOE-5",
                                          plan_date=today, proposal_id="prop_a")
    await repo.record_calendar_plan_event(event_id=place_id, calendar_id="primary",
                                          kind="placeholder", proposal_id="prop_a")
    await repo.record_calendar_plan_event(event_id=gone_id, calendar_id="primary",
                                          kind="work_block", issue_key="DOE-6",
                                          plan_date=today, proposal_id="prop_a")
    other = f"other_{uuid.uuid4().hex[:10]}"
    calendar = {"available": True, "window": {"from": f"{today}T13:00:00-07:00"},
                "events": [{"event_id": block_id}, {"event_id": place_id},
                           {"event_id": other}, {"summary": "no id"}]}
    out = await hb_actions._annotate_plan_kinds(calendar)
    kinds = [(e.get("plan_kind"), e.get("issue_key"), e.get("plan_date")) for e in out["events"]]
    assert kinds == [("work_block", "DOE-5", today), ("placeholder", None, None),
                     ("commitment", None, None), ("commitment", None, None)]
    assert out["work_blocks_not_listed"] == [
        {"event_id": gone_id, "issue_key": "DOE-6", "plan_date": today}]


async def test_an_unavailable_block_is_not_annotated():
    block = {"available": False, "reason": "truncated"}
    assert await hb_actions._annotate_plan_kinds(dict(block)) == block


@pytest.fixture()
def stub_task(monkeypatch):
    from central_command.api import routes

    seen: list[str] = []

    async def fake(brief, title, agent_id, actor=None):
        seen.append(brief)
        return {"task_id": "task_plan_test", "session_id": "sess_plan_test"}

    monkeypatch.setattr(routes, "create_and_run_task", fake)
    monkeypatch.setattr(settings, "ea_contact_budget_per_day", 0)
    return seen


@pytest.mark.graph_live
@needs_pg
async def test_the_brief_carries_the_kinds_and_the_free_time(monkeypatch, stub_task, calendar_on):
    import json

    event_id = f"brief_{uuid.uuid4().hex[:10]}"
    now = datetime.now(DENVER)
    await repo.record_calendar_plan_event(event_id=event_id, calendar_id="primary",
                                          kind="work_block", issue_key="DOE-7",
                                          plan_date=now.date().isoformat(), proposal_id="prop_b")
    start = now + timedelta(minutes=5)
    monkeypatch.setattr(calendar_facade, "list_events", _Facade([
        _ev(start, start + timedelta(hours=1), event_id=event_id, summary="DOE-7 work"),
        _ev(start + timedelta(hours=2), start + timedelta(hours=3), summary="1:1"),
    ]))
    await hb_actions._ea_contact("sched_plan_brief", {"kind": "morning_report"})
    brief = stub_task[0]
    block = json.loads(brief.split("```json\n")[1].split("\n```")[0])
    kinds = [e["plan_kind"] for e in block["calendar"]["events"]]
    assert kinds == ["work_block", "commitment"]
    assert block["calendar"]["events"][0]["issue_key"] == "DOE-7"
    assert "free_time" in block["calendar"] and "free_minutes" in block["calendar"]
    assert "Every event carries `plan_kind`" in brief
    assert "the plan ledger, the control plane's record" in brief
    assert "It is the ONLY place a work block may be proposed" in brief
    assert "the minimum block of 30 minutes" in brief


# --- 8. framings and charter -----------------------------------------------------------


def test_the_morning_framing_blocks_the_day_as_one_proposal():
    text = hb_actions._EA_FRAMING["morning_report"]
    assert "You do not block calendar time yet" not in text
    for phrase in ("BLOCK THE TIME", "as ONE proposal through `propose_calendar_change`",
                   "one `calendar.create_event` action per block",
                   "inside a `free_time` stretch", "no longer than its issue's estimate",
                   "never shorter than the minimum block", '"kind": "work_block"',
                   "names the issue key and its summary", "Block only ranked issues",
                   "an `unscored` issue gets no block", "Leave the rest of the free time free",
                   "nothing is on the calendar until the operator approves",
                   "never imply that any time has been blocked",
                   "If the day has no `free_time` or the calendar is unknown, propose no blocks"):
        assert phrase in text, phrase


def test_the_check_in_replans_only_on_a_slip_and_asks_rather_than_assumes():
    text = hb_actions._EA_FRAMING["check_in"]
    for phrase in ("REPLAN only when the day has slipped", "is a SLIP",
                   "`work_blocks_not_listed`", "overlaps a commitment",
                   "ASK the operator, and never assume", "as ONE proposal",
                   "`calendar.update_event` on your OWN work blocks",
                   "on a placeholder only when the operator's standing notes allow it",
                   "into a `free_time` stretch",
                   "Never propose a change to a commitment unless the operator asked",
                   "If nothing slipped, say so in one line and propose nothing"):
        assert phrase in text, phrase


def test_the_charter_carries_the_calendar_doctrine():
    c = ea_profile.CHARTER
    for phrase in ("THE CALENDAR HAS THREE KINDS OF EVENT", "WORK BLOCK", "PLACEHOLDER",
                   "COMMITMENT", "only in the free time the control plane computed",
                   "ONE proposal, one block per action",
                   "You may propose moving what the ledger says is your own",
                   "never propose a change to a commitment unless the operator asks",
                   "offer to create them, or to adopt events the operator already keeps",
                   "the operator's rule, found in the standing notes",
                   "until the operator has stated one, ask"):
        assert phrase in c, phrase
    # the step 2 doctrine is untouched
    assert "ARRIVE WITH A PROPOSAL, NOT A QUESTION" in c
    assert "capability = '" not in c and "calendar.adopt_event" not in c


# --- 9. one proposal, several calendar actions, end to end ------------------------------


def _plan_proposal(day: str, *blocks: tuple[str, str, str]) -> dict:
    return {
        "intent": "Today's plan: three work blocks",
        "actions": [{
            "capability": "calendar.create_event",
            "arguments": {"title": f"{key} {title}", "start": start, "end": end,
                          "plan": {"kind": "work_block", "issue_key": key, "date": day}},
            "target_ref": {"system": "calendar", "id": "primary", "read_version": "unknown"},
            "reversibility": "reversible",
        } for key, title, (start, end) in blocks],
        "evidence": [{"kind": "operator", "source_ref": "operator:task",
                      "locator": "morning plan", "claim": "The ranked backlog."}],
        "expected_effect": "three work blocks on today's calendar",
        "confidence": {"level": "high", "rationale": "The ranking is the control plane's."},
    }


class _Planner:
    """The EA's morning run: one `propose_calendar_change` with the whole plan,
    then text once the Executor's result comes back."""

    def __init__(self, proposal: dict):
        self.proposal = proposal
        self.returns: list[str] = []
        self.model = FunctionModel(self._respond)

    def _respond(self, messages, info: AgentInfo) -> ModelResponse:
        parts = [p for m in messages for p in getattr(m, "parts", [])]
        back = [str(p.content) for p in parts if isinstance(p, ToolReturnPart)
                and p.tool_name == "propose_calendar_change"]
        if back:
            self.returns = back
            return ModelResponse(parts=[TextPart(content="Plan recorded.")])
        return ModelResponse(parts=[ToolCallPart(
            tool_name="propose_calendar_change", args={"proposal": self.proposal})])


async def _ea_drafts(monkeypatch, planner: _Planner) -> dict:
    from central_command.api import routes
    from central_command.runtime import ea, models

    async def fixed(*_a, **_kw):
        return planner.model

    monkeypatch.setattr(routes, "resolve_model", fixed)
    monkeypatch.setattr(models, "resolve_model", fixed)
    monkeypatch.setattr(settings, "auditor_enabled", False)
    monkeypatch.setattr(settings, "dispatch_approval_limit", 10_000)
    await ea.ensure_registered()
    task_id = f"task_plan_{uuid.uuid4().hex[:10]}"
    await repo.create_task(task_id, "Morning plan", "plan the day", "ea", created_by="operator")
    run = await routes._run_assigned_task(await repo.get_task(task_id))
    assert run.get("proposal_id"), run
    return await repo.load_proposal(run["proposal_id"])


_THREE = (("DOE-11", "Budget", ("2026-11-20T09:00:00-07:00", "2026-11-20T10:00:00-07:00")),
          ("DOE-12", "Hiring plan", ("2026-11-20T10:30:00-07:00", "2026-11-20T11:30:00-07:00")),
          ("DOE-13", "Roadmap", ("2026-11-20T13:00:00-07:00", "2026-11-20T14:30:00-07:00")))


@needs_pg
async def test_a_day_plan_is_one_proposal_executed_in_order(monkeypatch, settled, google):
    from central_command.gateway import gateway

    planner = _Planner(_plan_proposal("2026-11-20", *_THREE))
    row = await _ea_drafts(monkeypatch, planner)
    assert row["status"] == "AWAITING_HUMAN" and len(row["actions"]) == 3
    assert [a["arguments"]["plan"]["issue_key"] for a in row["actions"]] == [
        "DOE-11", "DOE-12", "DOE-13"]

    out = await gateway.approve_and_execute(row["session_id"], row["id"], model=planner.model)
    assert out.get("execution") != "parked", out
    final = await repo.load_proposal(row["id"])
    assert final["status"] == "EXECUTED"
    assert [p["title"] for p in google.payloads] == ["DOE-11 Budget", "DOE-12 Hiring plan",
                                                     "DOE-13 Roadmap"], "in order, once each"
    for n, (key, _t, _w) in enumerate(_THREE):
        (ledger,) = await _rows(executor.google_event_id(f"{row['id']}:{n}"))
        assert (ledger["issue_key"], ledger["proposal_id"]) == (key, row["id"])
    assert planner.returns and "ledger: work block for DOE-13" in planner.returns[-1]


@needs_pg
async def test_a_day_plan_that_fails_partway_keeps_the_honest_partial_record(
        monkeypatch, settled, google):
    """The second create is refused (a semantic NO): the first block landed
    with its ledger row, the third never ran, and the proposal is FAILED with
    the completed prefix in its record."""
    from central_command.gateway import gateway

    handler = google.handler

    def refuse_second(request: httpx.Request) -> httpx.Response:
        import json

        if json.loads(request.content)["title"].startswith("DOE-12"):
            google.payloads.append(json.loads(request.content))
            return httpx.Response(200, json={"ok": False, "message": "create - invalid time range"})
        return handler(request)

    def factory(**kwargs):
        kwargs["transport"] = httpx.MockTransport(refuse_second)
        return _ORIGINAL_CLIENT(**kwargs)

    monkeypatch.setattr(calendar_facade.httpx, "AsyncClient", factory)
    planner = _Planner(_plan_proposal("2026-11-20", *_THREE))
    row = await _ea_drafts(monkeypatch, planner)
    await gateway.approve_and_execute(row["session_id"], row["id"], model=planner.model)
    final = await repo.load_proposal(row["id"])
    assert final["status"] == "FAILED"
    assert [p["title"] for p in google.payloads] == ["DOE-11 Budget", "DOE-12 Hiring plan"]
    assert len(await _rows(executor.google_event_id(f"{row['id']}:0"))) == 1
    assert await _rows(executor.google_event_id(f"{row['id']}:1")) == []
    assert await _rows(executor.google_event_id(f"{row['id']}:2")) == []
    failed = [e for e in await repo.list_events(ref_id=row["id"], limit=1000)
              if e["kind"] in ("proposal.failed", "proposal.execution_failed")]
    assert failed, "the failure is on the record"


@needs_pg
async def test_a_planned_create_retried_by_the_sweep_ends_with_one_row(
        monkeypatch, settled, google):
    google.timeout_after_insert = True
    middle = {"capability": "calendar.create_event",
              "arguments": {"title": "DOE-21 Budget", "start": "2026-11-20T10:00:00Z",
                            "end": "2026-11-20T10:30:00Z", "plan": dict(_BLOCK, issue_key="DOE-21")},
              "target_ref": {"system": "calendar", "id": "primary", "read_version": "unknown"},
              "reversibility": "reversible"}
    final, results = await _approve_parks_then_sweep_converges(monkeypatch, middle)
    assert len(google.events) == 1
    assert "ledger: work block for DOE-21" in results[1]
    assert len(await _rows(executor.google_event_id(f"{final['id']}:1"))) == 1
