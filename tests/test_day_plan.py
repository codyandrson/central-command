"""Step 4a of the EA planning design (DL-221, FR-54; the design record's D6 and
D9's evening row): the stored day plan (`plan.record_day`, table `day_plan`),
the evening review computed in code (`reports/ea_plan.review`), the outcome
record (`plan.record_review`, table `day_plan_outcome`), the padding ratio of
estimate against actual and its use by the backlog, releasing an event from
the plan ledger (`calendar.release_event`), a moved work block's plan date, the
brief blocks, the framings and the charter.

NO NETWORK: the tracker and the calendar are fakes (`jira.get_issue` /
`jira.search_issues` / `calendar_facade.*` replaced, or the `httpx.MockTransport`
Google of tests/test_action_key.py), and every approval that executes runs
under `tests/fake_world.py`. Each test plans on its own far-future date, so rows
another test wrote never meet it.
"""

from __future__ import annotations

import json
import math
import pathlib
import re
import uuid
from datetime import date, timedelta

import httpx
import pytest
from pydantic_ai import CallDeferred, ModelRetry

from central_command.config import settings
from central_command.contract import ARG_SPECS, Action, Proposal, Reversibility, validate_action_args
from central_command.contract.dependencies import CAPABILITY_DEPENDENCY
from central_command.db import repo
from central_command.gateway import capabilities, executor
from central_command.heartbeat import actions as hb_actions
from central_command.integrations import calendar_facade, jira
from central_command.reports import ea_backlog, ea_plan
from central_command.runtime import ea_profile, packs, tools
from central_command.runtime.templates import EA_TEMPLATE
from tests.conftest import needs_pg
from tests.fake_world import fake_world
from tests.test_action_key import (  # noqa: F401 — fixtures, used by name
    google,
    settled,
)
from tests.test_calendar_plan_ledger import _ORIGINAL_CLIENT, _ea_drafts, _Planner, _rows

TZ = "America/Denver"
# Captured at import, before the session guard in conftest swaps in its stand-in.
_REAL_BACKLOG = ea_backlog.backlog


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    monkeypatch.setattr(settings, "test_model", True)
    monkeypatch.setattr(settings, "ea_timezone", TZ)
    monkeypatch.setattr(settings, "plan_hours_per_day", 6.0)
    monkeypatch.setattr(settings, "plan_padding_window_days", 30)
    monkeypatch.setattr(settings, "plan_padding_min_samples", 5)


_YEARS = iter(range(2100, 2900))


def _day() -> str:
    """A date no other test plans on (a fresh year each call)."""
    return f"{next(_YEARS)}-{uuid.uuid4().int % 12 + 1:02d}-15"


def _pid() -> str:
    return f"prop_dp{uuid.uuid4().hex[:12]}"


def _issue(key, *, value=None, lateness=None, estimate=None, due=None, deadline=None,
           category="indeterminate", status="In Progress", summary=None):
    custom = {"Stakes": f"Stakes of {key}", "Value": value, "Lateness loss": lateness,
              "Deadline type": deadline}
    return {"issue_key": key, "summary": summary or f"Summary {key}", "status": status,
            "status_category": category, "priority": "High", "assignee": "Jo Doe",
            "due_date": due, "estimate_hours": estimate, "custom_fields": custom}


class _Tracker:
    """A fake tracker: `issues` answer get_issue, `gone` answer a 404 NO,
    `down` makes every call an outage. `asked` records every key read."""

    def __init__(self):
        self.issues: dict[str, dict] = {}
        self.gone: set[str] = set()
        self.down = False
        self.asked: list[str] = []
        self.searches: list[str] = []
        self.search_rows: list[dict] = []
        self.search_error: Exception | None = None

    async def get_issue(self, key):
        self.asked.append(key)
        if self.down:
            raise httpx.ConnectError("[Errno 111] Connection refused")
        if key in self.gone:
            raise jira.JiraError(f"getIssue {key} — HTTP 404: Issue does not exist",
                                 status_code=404)
        return {"ok": True, "issue": dict(self.issues[key])}

    async def search_issues(self, jql, limit=None):
        self.searches.append(jql)
        if self.down:
            raise httpx.ConnectError("[Errno 111] Connection refused")
        if self.search_error is not None:
            raise self.search_error
        return {"ok": True, "issues": list(self.search_rows), "count": len(self.search_rows),
                "truncated": False}


@pytest.fixture()
def tracker(monkeypatch):
    t = _Tracker()
    monkeypatch.setattr(jira, "configured", lambda: True)
    monkeypatch.setattr(jira, "get_issue", t.get_issue)
    monkeypatch.setattr(jira, "search_issues", t.search_issues)
    return t


def _record_day(day: str, *items: tuple[str, str]) -> Action:
    return Action(capability="plan.record_day",
                  arguments={"date": day, "items": [{"issue_key": k, "reason": r}
                                                    for k, r in items]},
                  target_ref={"system": "central_command", "id": f"day_plan:{day}"},
                  reversibility=Reversibility.reversible)


def _record_review(day: str, *outcomes: dict) -> Action:
    return Action(capability="plan.record_review",
                  arguments={"date": day, "outcomes": list(outcomes)},
                  target_ref={"system": "central_command", "id": f"day_plan:{day}"},
                  reversibility=Reversibility.reversible)


async def _plans(day: str) -> list[dict]:
    conn = await repo._conn()
    try:
        return [repo._plan_row(r) for r in await conn.fetch(
            "select * from day_plan where plan_date = $1 order by id", date.fromisoformat(day))]
    finally:
        await conn.close()


async def _outcomes(day: str) -> list[dict]:
    conn = await repo._conn()
    try:
        return [dict(r) for r in await conn.fetch(
            "select * from day_plan_outcome where plan_date = $1 order by id",
            date.fromisoformat(day))]
    finally:
        await conn.close()


# --- 1. the argument shapes, in both tiers ------------------------------------------


@pytest.mark.parametrize("args,problem", [
    ({"date": "2026-11-20", "items": [{"issue_key": "DOE-1", "reason": "due Friday"}]}, None),
    ({"date": "2026-11-20", "items": [{"issue_key": f"DOE-{n}", "reason": "r"}
                                       for n in range(10)]}, None),
    ({"date": "2026-11-20"}, "missing required argument(s) items"),
    ({"date": "2026-11-20", "items": []}, "missing required argument(s) items"),
    ({"items": [{"issue_key": "DOE-1", "reason": "r"}]}, "missing required argument(s) date"),
    ({"date": "2026-11-31", "items": [{"issue_key": "DOE-1", "reason": "r"}]}, "date must be"),
    ({"date": "2026-11-20T09:00", "items": [{"issue_key": "DOE-1", "reason": "r"}]},
     "date must be"),
    ({"date": "2026-11-20", "items": "DOE-1"}, "items must be a list"),
    ({"date": "2026-11-20", "items": [{"issue_key": f"DOE-{n}", "reason": "r"}
                                       for n in range(11)]}, "at most 10"),
    ({"date": "2026-11-20", "items": [{"issue_key": "DOE-1", "reason": "r"},
                                       {"issue_key": "DOE-1", "reason": "r"}]}, "named twice"),
    ({"date": "2026-11-20", "items": [{"reason": "r"}]}, "issue_key is required"),
    ({"date": "2026-11-20", "items": [{"issue_key": "DOE-1"}]}, "reason is required"),
    ({"date": "2026-11-20", "items": [{"issue_key": "DOE-1", "reason": " "}]},
     "reason is required"),
    ({"date": "2026-11-20", "items": [{"issue_key": "DOE-1", "reason": "r", "value": 8}]},
     "unknown key(s) value"),
    ({"date": "2026-11-20", "items": ["DOE-1"]}, "must be an object"),
])
def test_the_record_day_shape(args, problem):
    problems = validate_action_args("plan.record_day", args)
    if problem is None:
        assert problems == []
    else:
        assert any(problem in p for p in problems), problems


@pytest.mark.parametrize("outcome,problem", [
    ({"issue_key": "DOE-1", "done": True}, None),
    ({"issue_key": "DOE-1", "done": False, "actual_hours": 0}, None),
    ({"issue_key": "DOE-1", "done": True, "actual_hours": 2.5, "note": "took longer"}, None),
    ({"issue_key": "DOE-1", "done": True, "actual_hours": None, "note": None}, None),
    ({"issue_key": "DOE-1"}, "done must be true or false"),
    ({"issue_key": "DOE-1", "done": "yes"}, "done must be true or false"),
    ({"issue_key": "DOE-1", "done": True, "actual_hours": -1}, "actual_hours must be"),
    ({"issue_key": "DOE-1", "done": True, "actual_hours": "2"}, "actual_hours must be"),
    ({"issue_key": "DOE-1", "done": True, "actual_hours": True}, "actual_hours must be"),
    ({"issue_key": "DOE-1", "done": True, "actual_hours": math.inf}, "actual_hours must be"),
    ({"issue_key": "DOE-1", "done": True, "note": 5}, "note must be text"),
    ({"issue_key": "DOE-1", "done": True, "note": "x" * 501}, "at most 500"),
    ({"issue_key": "DOE-1", "done": True, "estimate_hours": 2}, "unknown key(s) estimate_hours"),
    ({"done": True}, "issue_key is required"),
])
def test_the_record_review_shape(outcome, problem):
    problems = validate_action_args("plan.record_review",
                                    {"date": "2026-11-20", "outcomes": [outcome]})
    if problem is None:
        assert problems == []
    else:
        assert any(problem in p for p in problems), problems


def test_a_review_names_each_issue_once_and_a_release_names_its_event():
    twice = {"date": "2026-11-20", "outcomes": [{"issue_key": "DOE-1", "done": True},
                                                {"issue_key": "DOE-1", "done": False}]}
    assert any("named twice" in p for p in validate_action_args("plan.record_review", twice))
    assert validate_action_args("calendar.release_event", {"event_id": "e1"}) == []
    assert validate_action_args("calendar.release_event", {"calendar_id": "primary"})


def _proposal(*actions: Action) -> Proposal:
    return Proposal(intent="Today's plan", expected_effect="a stored plan", evidence=[],
                    confidence={"level": "high", "rationale": "The ranking is the control plane's."},
                    actions=list(actions))


async def test_the_runtime_hands_a_plan_carrying_numbers_back_to_the_model():
    bad = _record_day("2026-11-20", ("DOE-1", "due"))
    bad.arguments["items"][0]["estimate_hours"] = 1
    with pytest.raises(ModelRetry, match="unknown key"):
        await tools.propose_action(None, _proposal(bad))
    with pytest.raises(CallDeferred):
        await tools.propose_action(None, _proposal(_record_day("2026-11-20", ("DOE-1", "due"))))
    with pytest.raises(ModelRetry, match="done must be true or false"):
        await tools.propose_action(None, _proposal(_record_review(
            "2026-11-20", {"issue_key": "DOE-1", "done": "mostly"})))


async def test_the_executor_refuses_a_bad_shape_before_anything_runs(monkeypatch):
    log = fake_world(monkeypatch)
    bad = _record_review("2026-11-20", {"issue_key": "DOE-1", "done": True, "actual_hours": -2})
    with pytest.raises(executor.ExecutionFailed, match="actual_hours must be") as exc:
        await executor.execute([_record_day("2026-11-20", ("DOE-1", "r")), bad],
                               "human:lee", [], proposal_id=_pid())
    assert exc.value.completed == [] and log == [], "the first action never ran"


# --- 2. plan.record_day: numbers from the tracker, supersession, retry ----------------


@pytest.fixture()
def real_plan(monkeypatch):
    fake_world(monkeypatch, real=("plan.record_day", "plan.record_review",
                                  "calendar.release_event", "calendar.update_event"))


@needs_pg
async def test_the_numbers_are_re_read_at_execution_not_taken_from_the_agent(
        tracker, real_plan):
    day = _day()
    tracker.issues["DOE-1"] = _issue("DOE-1", value=8, lateness=20, estimate=4.0,
                                     due="2099-01-01", deadline="soft")
    tracker.issues["DOE-2"] = _issue("DOE-2", value=3, estimate=2.0)
    pid = _pid()
    out = await executor.execute(
        [_record_day(day, ("DOE-1", "it is due"), ("DOE-2", "small win"))],
        "human:lee", [], proposal_id=pid)
    (plan,) = await _plans(day)
    assert plan["proposal_id"] == pid and plan["superseded_at"] is None
    one, two = plan["items"]
    assert (one["issue_key"], one["rank"], one["reason"]) == ("DOE-1", 1, "it is due")
    assert (one["value"], one["lateness_loss"], one["estimate_hours"], one["due_date"],
            one["tier"], one["readable"]) == (8.0, 20.0, 4.0, "2099-01-01", "standard", True)
    assert (two["value"], two["estimate_hours"], two["tier"]) == (3.0, 2.0, "standard")
    assert tracker.asked == ["DOE-1", "DOE-2"]
    assert f"day plan for {day} stored: 2 item(s) (DOE-1, DOE-2)" in out.result_text
    assert "re-read from the tracker" in out.result_text


@needs_pg
async def test_an_unreadable_issue_is_stored_without_numbers_and_named(tracker, real_plan):
    day = _day()
    tracker.issues["DOE-3"] = _issue("DOE-3", value=5, estimate=1.0)
    tracker.gone.add("DOE-404")
    out = await executor.execute(
        [_record_day(day, ("DOE-3", "r"), ("DOE-404", "the operator asked"))],
        "human:lee", [], proposal_id=_pid())
    (plan,) = await _plans(day)
    gone = plan["items"][1]
    assert gone["readable"] is False and gone["reason"] == "the operator asked"
    assert all(gone[n] is None for n in ea_plan.PLANNED_NUMBERS)
    assert "404" in gone["unreadable_reason"]
    assert "stored without numbers, unreadable: DOE-404" in out.result_text


@needs_pg
async def test_a_down_tracker_parks_the_plan_on_jira_and_stores_nothing(tracker, real_plan):
    day = _day()
    tracker.down = True
    with pytest.raises(executor.ExecutionFailed) as exc:
        await executor.execute([_record_day(day, ("DOE-1", "r"))], "human:lee", [],
                               proposal_id=_pid())
    assert exc.value.transient is True and exc.value.dependencies == ("jira",)
    assert await _plans(day) == []


@needs_pg
async def test_an_unconfigured_tracker_stores_the_plan_without_numbers(monkeypatch, real_plan):
    day = _day()
    monkeypatch.setattr(jira, "configured", lambda: False)
    out = await executor.execute([_record_day(day, ("DOE-1", "r"))], "human:lee", [],
                                 proposal_id=_pid())
    (plan,) = await _plans(day)
    assert plan["items"][0]["unreadable_reason"] == "the tracker is not configured"
    assert "unreadable: DOE-1 (the tracker is not configured)" in out.result_text


@needs_pg
async def test_a_later_plan_supersedes_the_earlier_and_one_stays_active(tracker, real_plan):
    day = _day()
    tracker.issues.update({k: _issue(k, value=1, estimate=1.0) for k in ("DOE-1", "DOE-2")})
    first, second = _pid(), _pid()
    await executor.execute([_record_day(day, ("DOE-1", "r"))], "human:lee", [], proposal_id=first)
    out = await executor.execute([_record_day(day, ("DOE-2", "r"))], "human:lee", [],
                                 proposal_id=second)
    old, new = await _plans(day)
    assert old["proposal_id"] == first and old["superseded_at"] is not None
    assert new["proposal_id"] == second and new["superseded_at"] is None
    assert f"supersedes the earlier plan for that date (#{old['id']})" in out.result_text
    assert (await repo.get_day_plan(day))["proposal_id"] == second
    conn = await repo._conn()
    try:
        with pytest.raises(Exception, match="day_plan_active_uq"):
            await conn.execute("update day_plan set superseded_at = null where id = $1", old["id"])
    finally:
        await conn.close()


@needs_pg
async def test_a_retried_execution_stores_one_plan_and_reads_nothing(tracker, real_plan):
    day = _day()
    tracker.issues["DOE-1"] = _issue("DOE-1", value=1, estimate=1.0)
    pid = _pid()
    await executor.execute([_record_day(day, ("DOE-1", "r"))], "human:lee", [], proposal_id=pid)
    tracker.down = True    # a retry must not need the tracker
    again = await executor.execute([_record_day(day, ("DOE-1", "r"))], "human:lee", [],
                                   proposal_id=pid)
    assert "already stored by an earlier attempt" in again.result_text
    assert len(await _plans(day)) == 1 and tracker.asked == ["DOE-1"]


@needs_pg
async def test_the_repo_write_is_idempotent_on_the_action_key_itself():
    day = _day()
    a = await repo.record_day_plan(plan_date=day, items=[{"issue_key": "X-1"}],
                                   proposal_id="p", action_key=f"k:{day}")
    b = await repo.record_day_plan(plan_date=day, items=[{"issue_key": "X-2"}],
                                   proposal_id="p", action_key=f"k:{day}")
    assert a["already_existed"] is False and b["already_existed"] is True
    assert b["id"] == a["id"] and len(await _plans(day)) == 1


# --- 3. the morning proposal: the plan first, the blocks after, end to end ------------


def _morning(day: str, *blocks: tuple[str, str, tuple[str, str]], with_plan=True) -> dict:
    actions = []
    if with_plan:
        actions.append({
            "capability": "plan.record_day",
            "arguments": {"date": day, "items": [{"issue_key": k, "reason": f"{t} first"}
                                                 for k, t, _w in blocks]},
            "target_ref": {"system": "central_command", "id": f"day_plan:{day}",
                           "read_version": "unknown"},
            "reversibility": "reversible",
        })
    actions += [{
        "capability": "calendar.create_event",
        "arguments": {"title": f"{key} {title}", "start": start, "end": end,
                      "plan": {"kind": "work_block", "issue_key": key, "date": day}},
        "target_ref": {"system": "calendar", "id": "primary", "read_version": "unknown"},
        "reversibility": "reversible",
    } for key, title, (start, end) in blocks]
    return {
        "intent": "Today's plan, stored, with two work blocks",
        "actions": actions,
        "evidence": [{"kind": "operator", "source_ref": "operator:task",
                      "locator": "morning plan", "claim": "The ranked backlog."}],
        "expected_effect": "a stored plan and two work blocks",
        "confidence": {"level": "high", "rationale": "The ranking is the control plane's."},
    }


def _two(day: str):
    return (("DOE-31", "Budget", (f"{day}T09:00:00-07:00", f"{day}T10:00:00-07:00")),
            ("DOE-32", "Hiring", (f"{day}T10:30:00-07:00", f"{day}T11:30:00-07:00")))


@needs_pg
async def test_the_morning_plan_stores_the_plan_then_blocks_the_time(
        monkeypatch, settled, google, tracker):
    from central_command.gateway import gateway

    fake_world(monkeypatch, real=("calendar.create_event", "plan.record_day"))
    day = _day()
    tracker.issues.update({k: _issue(k, value=2, estimate=1.0) for k in ("DOE-31", "DOE-32")})
    planner = _Planner(_morning(day, *_two(day)))
    row = await _ea_drafts(monkeypatch, planner)
    assert [a["capability"] for a in row["actions"]] == [
        "plan.record_day", "calendar.create_event", "calendar.create_event"]

    await gateway.approve_and_execute(row["session_id"], row["id"], model=planner.model)
    assert (await repo.load_proposal(row["id"]))["status"] == "EXECUTED"
    (plan,) = await _plans(day)
    assert plan["proposal_id"] == row["id"]
    assert [i["issue_key"] for i in plan["items"]] == ["DOE-31", "DOE-32"]
    assert [p["title"] for p in google.payloads] == ["DOE-31 Budget", "DOE-32 Hiring"]
    for n in (1, 2):
        (ledger,) = await _rows(executor.google_event_id(f"{row['id']}:{n}"))
        assert ledger["proposal_id"] == row["id"]
    assert planner.returns and f"day plan for {day} stored" in planner.returns[-1]


@needs_pg
async def test_a_block_that_fails_after_the_plan_is_stored_keeps_the_plan(
        monkeypatch, settled, google, tracker):
    """The second create is refused (a semantic NO): the plan and the first
    block are on the record, and the proposal lands FAILED with that prefix."""
    from central_command.gateway import gateway

    fake_world(monkeypatch, real=("calendar.create_event", "plan.record_day"))
    day = _day()
    tracker.issues.update({k: _issue(k, value=2, estimate=1.0) for k in ("DOE-31", "DOE-32")})
    handler = google.handler

    def refuse_second(request: httpx.Request) -> httpx.Response:
        if json.loads(request.content)["title"].startswith("DOE-32"):
            google.payloads.append(json.loads(request.content))
            return httpx.Response(200, json={"ok": False, "message": "create - invalid time range"})
        return handler(request)

    def factory(**kwargs):
        kwargs["transport"] = httpx.MockTransport(refuse_second)
        return _ORIGINAL_CLIENT(**kwargs)

    monkeypatch.setattr(calendar_facade.httpx, "AsyncClient", factory)
    planner = _Planner(_morning(day, *_two(day)))
    row = await _ea_drafts(monkeypatch, planner)
    await gateway.approve_and_execute(row["session_id"], row["id"], model=planner.model)
    final = await repo.load_proposal(row["id"])
    assert final["status"] == "FAILED"
    assert len(await _plans(day)) == 1, "the plan stored before the failure stays stored"
    assert len(await _rows(executor.google_event_id(f"{row['id']}:1"))) == 1
    assert await _rows(executor.google_event_id(f"{row['id']}:2")) == []
    completed = (final.get("retry_state") or {}).get("completed_results") or []
    assert completed and f"day plan for {day} stored" in completed[0]


# --- 4. review() --------------------------------------------------------------------


@pytest.fixture()
def calendar_now(monkeypatch):
    """The calendar as it holds each event NOW: `events` answer get_event,
    anything else is the provider's NO; `down` is an outage."""
    state = {"events": {}, "down": False, "asked": []}
    monkeypatch.setattr(calendar_facade, "provider_configured", lambda: True)

    async def get_event(event_id, calendar_id="primary"):
        state["asked"].append(event_id)
        if state["down"]:
            raise calendar_facade.CalendarFacadeError("get_event — HTTP 503") from \
                httpx.ConnectError("[Errno 111] Connection refused")
        if event_id not in state["events"]:
            raise calendar_facade.CalendarFacadeError("get_event - event not found")
        return {"event_id": event_id, **state["events"][event_id]}

    monkeypatch.setattr(calendar_facade, "get_event", get_event)
    return state


async def _plan(day: str, *keys: str, estimate=1.0, proposal_id="prop_review") -> dict:
    items = [{"issue_key": k, "rank": n, "reason": f"why {k}", "readable": True,
              "summary": f"S {k}", "value": 1.0, "lateness_loss": None, "due_date": None,
              "estimate_hours": estimate, "tier": "standard"}
             for n, k in enumerate(keys, start=1)]
    return await repo.record_day_plan(plan_date=day, items=items, proposal_id=proposal_id)


async def _block(day: str, key: str, *, retire=False) -> str:
    event_id = f"blk_{uuid.uuid4().hex[:10]}"
    await repo.record_calendar_plan_event(event_id=event_id, calendar_id="primary",
                                          kind="work_block", issue_key=key, plan_date=day,
                                          proposal_id="prop_review")
    if retire:
        await repo.retire_calendar_plan_event(event_id, "primary", proposal_id="prop_gone")
    return event_id


@needs_pg
async def test_no_plan_stored(tracker, calendar_now):
    assert await ea_plan.review(_day()) == {"available": False, "reason": "no plan stored"}
    assert tracker.asked == [] and calendar_now["asked"] == []


@needs_pg
async def test_the_review_reads_done_from_the_status_category(tracker, calendar_now):
    day = _day()
    await _plan(day, "DOE-41", "DOE-42", "DOE-43")
    tracker.issues["DOE-41"] = _issue("DOE-41", category="done", status="Shipped")
    tracker.issues["DOE-42"] = _issue("DOE-42", category="indeterminate")
    tracker.issues["DOE-43"] = _issue("DOE-43", category=None, status="Weird")
    out = await ea_plan.review(day)
    assert out["available"] is True and out["date"] == day
    t = {i["issue_key"]: i["tracker"] for i in out["items"]}
    assert t["DOE-41"] == {"readable": True, "status": "Shipped", "status_category": "done",
                           "done": True}
    assert t["DOE-42"]["done"] is False
    assert t["DOE-43"]["done"] is None, "no category is not done"
    first = out["items"][0]
    assert first["rank"] == 1 and first["reason"] == "why DOE-41"
    assert first["planned"]["estimate_hours"] == 1.0 and first["outcome"] is None


@needs_pg
async def test_an_unreadable_issue_is_never_assumed_done(tracker, calendar_now):
    day = _day()
    await _plan(day, "DOE-44")
    tracker.gone.add("DOE-44")
    (item,) = (await ea_plan.review(day))["items"]
    assert item["tracker"]["readable"] is False and item["tracker"]["done"] is None
    assert "404" in item["tracker"]["reason"]


@needs_pg
async def test_retired_and_moved_blocks_and_one_the_calendar_lost(tracker, calendar_now):
    day = _day()
    next_day = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
    await _plan(day, "DOE-45")
    tracker.issues["DOE-45"] = _issue("DOE-45")
    kept = await _block(day, "DOE-45")
    moved = await _block(day, "DOE-45")
    lost = await _block(day, "DOE-45")
    gone = await _block(day, "DOE-45", retire=True)
    other = await _block(day, "DOE-99")
    calendar_now["events"][kept] = {"start": f"{day}T14:00:00-07:00", "end": f"{day}T15:00:00-07:00"}
    calendar_now["events"][moved] = {"start": f"{next_day}T09:00:00-07:00",
                                     "end": f"{next_day}T10:00:00-07:00"}
    calendar_now["events"][other] = {"start": f"{day}T16:00:00-07:00", "end": f"{day}T17:00:00-07:00"}
    out = await ea_plan.review(day)
    blocks = {b["event_id"]: b for b in out["items"][0]["blocks"]}
    assert blocks[kept]["state"] == "active" and blocks[kept]["start"].startswith(f"{day}T14:00")
    assert "moved_off_day" not in blocks[kept]
    assert blocks[moved]["moved_off_day"] == next_day
    assert blocks[lost]["calendar"] == "not found"
    assert blocks[gone]["state"] == "retired" and blocks[gone]["retired_by"] == "prop_gone"
    assert gone not in calendar_now["asked"], "a retired block is not read"
    assert [b["event_id"] for b in out["blocks_not_on_plan"]] == [other]


@needs_pg
async def test_unplanned_completions_come_from_one_search(monkeypatch, tracker, calendar_now):
    day = _day()
    after = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
    await _plan(day, "DOE-46")
    tracker.issues["DOE-46"] = _issue("DOE-46", category="done")
    tracker.search_rows = [{"issue_key": "DOE-46", "summary": "planned", "status": "Done"},
                           {"issue_key": "DOE-47", "summary": "a fire", "status": "Done"}]
    monkeypatch.setattr(settings, "plan_backlog_jql",
                        "assignee = currentUser() AND statusCategory != Done")
    out = await ea_plan.review(day)
    assert tracker.searches == [
        f'(assignee = currentUser() AND statusCategory = Done) AND resolved >= "{day}" '
        f'AND resolved < "{after}"']
    assert out["unplanned_done"]["asked"] == tracker.searches[0]
    assert [i["issue_key"] for i in out["unplanned_done"]["issues"]] == ["DOE-47"]


@pytest.mark.parametrize("jql,why", [
    ("project = TASKS", "no single `statusCategory != Done` clause"),
    ("statusCategory != Done AND statusCategory != Done", "no single"),
    ("assignee = currentUser() AND statusCategory != Done ORDER BY due", "ORDER BY"),
])
@needs_pg
async def test_a_backlog_jql_that_cannot_be_turned_around_omits_the_key(
        monkeypatch, tracker, calendar_now, jql, why):
    day = _day()
    await _plan(day, "DOE-48")
    tracker.issues["DOE-48"] = _issue("DOE-48")
    monkeypatch.setattr(settings, "plan_backlog_jql", jql)
    out = await ea_plan.review(day)
    assert "unplanned_done" not in out and why in out["unplanned_done_omitted"]
    assert tracker.searches == []


@needs_pg
async def test_a_refused_search_omits_the_key_and_an_outage_raises(tracker, calendar_now):
    day = _day()
    await _plan(day, "DOE-49")
    tracker.issues["DOE-49"] = _issue("DOE-49")
    tracker.search_error = jira.JiraError("searchIssues — HTTP 400: bad JQL", status_code=400)
    out = await ea_plan.review(day)
    assert "the tracker refused the search" in out["unplanned_done_omitted"]
    tracker.down = True
    with pytest.raises(httpx.ConnectError):
        await ea_plan.review(day)


@needs_pg
async def test_a_calendar_outage_raises(tracker, calendar_now):
    day = _day()
    await _plan(day, "DOE-50")
    tracker.issues["DOE-50"] = _issue("DOE-50")
    await _block(day, "DOE-50")
    calendar_now["down"] = True
    with pytest.raises(calendar_facade.CalendarFacadeError):
        await ea_plan.review(day)


@needs_pg
async def test_the_review_carries_recorded_and_unplanned_outcomes(monkeypatch, calendar_now):
    monkeypatch.setattr(jira, "configured", lambda: False)
    day = _day()
    await _plan(day, "DOE-51")
    await repo.record_day_plan_outcomes(plan_date=day, outcomes=[
        {"issue_key": "DOE-51", "done": True, "actual_hours": 2.0, "note": "ok"},
        {"issue_key": "DOE-52", "done": True, "actual_hours": 1.0}], proposal_id="prop_o")
    out = await ea_plan.review(day)
    (item,) = out["items"]
    assert item["outcome"]["done"] is True and item["outcome"]["actual_hours"] == 2.0
    assert item["tracker"] == {"readable": False, "reason": "the tracker is not configured",
                               "done": None}
    assert [o["issue_key"] for o in out["unplanned_outcomes"]] == ["DOE-52"]
    assert out["unplanned_done_omitted"] == "the tracker is not configured"


# --- 5. plan.record_review -----------------------------------------------------------


@needs_pg
async def test_a_review_records_outcomes_marks_unplanned_and_replaces(real_plan):
    day = _day()
    await _plan(day, "DOE-61")
    first = _pid()
    out = await executor.execute([_record_review(
        day, {"issue_key": "DOE-61", "done": True, "actual_hours": 3},
        {"issue_key": "DOE-62", "done": True, "note": "a fire"})], "human:lee", [],
        proposal_id=first)
    assert "done: DOE-61, DOE-62" in out.result_text
    assert "recorded as unplanned): DOE-62" in out.result_text
    rows = {r["issue_key"]: r for r in await _outcomes(day)}
    assert rows["DOE-61"]["planned"] is True and rows["DOE-61"]["actual_hours"] == 3.0
    assert rows["DOE-62"]["planned"] is False and rows["DOE-62"]["note"] == "a fire"
    assert rows["DOE-62"]["actual_hours"] is None

    again = await executor.execute([_record_review(
        day, {"issue_key": "DOE-61", "done": False, "note": "not really"})], "human:lee", [],
        proposal_id=_pid())
    assert "replaces the earlier record for DOE-61" in again.result_text
    active = await repo.list_day_plan_outcomes(day)
    assert {(r["issue_key"], r["done"]) for r in active} == {("DOE-61", False), ("DOE-62", True)}
    assert len(await _outcomes(day)) == 3, "the earlier record is superseded, never deleted"


@needs_pg
async def test_a_retried_review_records_once(real_plan):
    day = _day()
    pid = _pid()
    action = _record_review(day, {"issue_key": "DOE-63", "done": True, "actual_hours": 1})
    await executor.execute([action], "human:lee", [], proposal_id=pid)
    again = await executor.execute([action], "human:lee", [], proposal_id=pid)
    assert "already recorded by an earlier attempt" in again.result_text
    assert len(await _outcomes(day)) == 1


def test_only_the_executor_writes_the_plan_tables():
    pkg = pathlib.Path(repo.__file__).parents[1]
    offenders = []
    for path in sorted(pkg.rglob("*.py")):
        rel = path.relative_to(pkg).as_posix()
        if rel in ("db/repo.py", "gateway/executor.py"):
            continue
        src = path.read_text(encoding="utf-8")
        for name in ("record_day_plan", "record_day_plan_outcomes"):
            if re.search(rf"\b{name}\(", src):
                offenders.append((rel, name))
        if re.search(r"(insert into|update)\s+day_plan", src, re.I):
            offenders.append((rel, "sql"))
    assert offenders == []


def test_the_schema_declares_both_tables_idempotently():
    sql = (pathlib.Path(repo.__file__).with_name("schema.sql")).read_text(encoding="utf-8")
    assert "create table if not exists day_plan (" in sql
    assert "create table if not exists day_plan_outcome (" in sql
    assert ("create unique index if not exists day_plan_active_uq\n"
            "    on day_plan (plan_date) where superseded_at is null;") in sql
    assert ("create unique index if not exists day_plan_outcome_active_uq\n"
            "    on day_plan_outcome (plan_date, issue_key) where superseded_at is null;") in sql


# --- 6. the padding ratio --------------------------------------------------------------


def _s(actual, estimate):
    return {"actual_hours": actual, "estimate_hours": estimate}


@pytest.mark.parametrize("samples,min_samples,expected", [
    ([], 5, {"ratio": 1.0, "samples": 0}),
    ([_s(2, 1)] * 4, 5, {"ratio": 1.0, "samples": 4}),          # below the minimum
    ([_s(2, 1)] * 5, 5, {"ratio": 2.0, "samples": 5}),          # exactly the minimum
    ([_s(3, 2), _s(1, 2)], 2, {"ratio": 1.0, "samples": 2}),    # 4/4
    ([_s(3, 2), _s(4, 2)], 2, {"ratio": 1.75, "samples": 2}),   # sums, not mean of ratios
    ([_s(10, 1), _s(1, 9)], 2, {"ratio": 1.1, "samples": 2}),   # 11/10, one wild day diluted
    ([_s(1, 2)] * 5, 5, {"ratio": 1.0, "samples": 5}),          # 0.5 clamped up to 1.0
    ([_s(0, 2)] * 5, 5, {"ratio": 1.0, "samples": 5}),          # zero actual still counts
    ([_s(10, 1)] * 5, 5, {"ratio": 3.0, "samples": 5}),         # 10 clamped down to 3.0
    ([_s(3, 1)] * 5, 5, {"ratio": 3.0, "samples": 5}),          # the ceiling itself
    ([_s(2, 3)] * 3, 3, {"ratio": 1.0, "samples": 3}),
    ([_s(5, 3)] * 3, 3, {"ratio": 1.6667, "samples": 3}),       # rounded to 4 places
    ([_s(2, 1)], 0, {"ratio": 2.0, "samples": 1}),              # a minimum below 1 is 1
    ([_s(2, 1)], -3, {"ratio": 2.0, "samples": 1}),
])
def test_padding_ratio(samples, min_samples, expected):
    assert ea_plan.padding_ratio(samples, min_samples) == expected


@pytest.mark.parametrize("bad", [
    _s(None, 1), _s(1, None), _s(-1, 1), _s(1, -1), _s(1, 0), _s(True, 1), _s(1, True),
    _s("2", 1), _s(1, "2"), _s(math.nan, 1), _s(1, math.inf), _s(math.inf, 1), {},
])
def test_padding_ratio_skips_a_sample_without_both_numbers(bad):
    assert ea_plan.padding_ratio([bad] + [_s(2, 1)] * 2, 3) == {"ratio": 1.0, "samples": 2}
    assert ea_plan.padding_ratio([bad] + [_s(2, 1)] * 2, 2) == {"ratio": 2.0, "samples": 2}


def test_padding_ratio_is_pure():
    src = pathlib.Path(ea_plan.__file__).read_text(encoding="utf-8")
    body = src.split("def padding_ratio(")[1].split("\nasync def ")[0]
    assert "await" not in body and "settings" not in body and "repo" not in body


@needs_pg
async def test_padding_reads_the_window_of_recorded_outcomes(monkeypatch):
    monkeypatch.setattr(settings, "plan_padding_window_days", 10)
    monkeypatch.setattr(settings, "plan_padding_min_samples", 2)
    year = next(_YEARS)
    today = date(year, 6, 30)
    inside = [today, today - timedelta(days=9)]
    outside = [today - timedelta(days=10), today + timedelta(days=1)]
    for n, d in enumerate(inside + outside):
        key = f"PAD-{n}"
        await repo.record_day_plan(plan_date=d, items=[{"issue_key": key, "estimate_hours": 2.0}])
        await repo.record_day_plan_outcomes(plan_date=d, outcomes=[
            {"issue_key": key, "done": True, "actual_hours": 5.0 if d in inside else 100.0}])
    # an outcome with no actual hours, and one for an issue the plan never had
    await repo.record_day_plan_outcomes(plan_date=today, outcomes=[
        {"issue_key": "PAD-x", "done": True, "actual_hours": None},
        {"issue_key": "PAD-y", "done": True, "actual_hours": 9.0}])
    out = await ea_plan.padding(today)
    assert out == {"ratio": 2.5, "samples": 2, "window_days": 10, "min_samples": 2}


@needs_pg
async def test_backlog_passes_the_padding_to_rank_and_reports_it(monkeypatch):
    from tests.test_ea_backlog import TODAY, _fake_jira, _row_in

    monkeypatch.setattr(ea_backlog, "backlog", _REAL_BACKLOG)
    monkeypatch.setattr(jira, "configured", lambda: True)
    monkeypatch.setattr(ea_backlog, "_today", lambda: TODAY)
    monkeypatch.setattr(settings, "plan_brief_items", 25)
    monkeypatch.setattr(settings, "plan_backlog_limit", 200)
    seen = {}

    async def padding(today=None):
        seen["today"] = today
        return {"ratio": 2.0, "samples": 7, "window_days": 30, "min_samples": 5}

    monkeypatch.setattr(ea_plan, "padding", padding)
    due = (TODAY + timedelta(days=1)).isoformat()
    # 6 hours at 6 a day is one day (latest start = due); padded to 12, two days.
    _fake_jira(monkeypatch, [_row_in("Q-9", {"Value": 1, "Lateness loss": 6},
                                     due_date=due, estimate_hours=6.0)])
    out = await ea_backlog.backlog()
    assert seen["today"] == TODAY
    assert (out["padding"], out["padding_samples"]) == (2.0, 7)
    (item,) = out["items"]
    assert item["latest_start"] == TODAY.isoformat() and item["tier"] == "must_start"
    assert item["score"] == 1.0, "padding moves the date, not the ratio"


# --- 7. calendar.release_event ----------------------------------------------------------


@pytest.fixture()
def no_calendar_calls(monkeypatch):
    async def _call(payload, timeout=60.0):
        raise AssertionError(f"release touched the calendar ({payload.get('mode')})")

    monkeypatch.setattr(calendar_facade, "_call", _call)


def _release(event_id: str) -> Action:
    return Action(capability="calendar.release_event", arguments={"event_id": event_id},
                  target_ref={"system": "calendar", "id": event_id},
                  reversibility=Reversibility.reversible)


async def _ledger(event_id: str) -> list[dict]:
    conn = await repo._conn()
    try:
        return [dict(r) for r in await conn.fetch(
            "select * from calendar_plan_event where event_id = $1 order by id", event_id)]
    finally:
        await conn.close()


@needs_pg
async def test_releasing_an_active_row_retires_it_and_changes_nothing(real_plan, no_calendar_calls):
    event_id = f"rel_{uuid.uuid4().hex[:10]}"
    await repo.record_calendar_plan_event(event_id=event_id, calendar_id="primary",
                                          kind="placeholder", proposal_id="prop_adopt")
    pid = _pid()
    out = await executor.execute([_release(event_id)], "human:lee", [], proposal_id=pid)
    (row,) = await _ledger(event_id)
    assert row["retired_at"] is not None and row["retired_by"] == pid
    assert "released from the plan ledger (was a placeholder)" in out.result_text
    assert "calendar unchanged" in out.result_text
    again = await executor.execute([_release(event_id)], "human:lee", [], proposal_id=pid)
    assert "already released from the plan ledger by this proposal" in again.result_text


@needs_pg
async def test_releasing_an_event_the_ledger_does_not_hold_is_refused(real_plan, no_calendar_calls):
    with pytest.raises(executor.ExecutionFailed, match="no active row in the plan ledger") as exc:
        await executor.execute([_release(f"none_{uuid.uuid4().hex[:8]}")], "human:lee", [],
                               proposal_id=_pid())
    assert exc.value.transient is False


@needs_pg
async def test_releasing_an_already_retired_row_is_refused(real_plan, no_calendar_calls):
    event_id = f"old_{uuid.uuid4().hex[:10]}"
    await repo.record_calendar_plan_event(event_id=event_id, calendar_id="primary",
                                          kind="work_block", issue_key="DOE-70",
                                          plan_date="2031-05-05", proposal_id="prop_made")
    await repo.retire_calendar_plan_event(event_id, "primary", proposal_id="prop_deleted")
    with pytest.raises(executor.ExecutionFailed, match="no active row"):
        await executor.execute([_release(event_id)], "human:lee", [], proposal_id=_pid())
    (row,) = await _ledger(event_id)
    assert row["retired_by"] == "prop_deleted"


# --- 8. a moved block keeps its date right ---------------------------------------------


@pytest.fixture()
def updates(monkeypatch, real_plan):
    monkeypatch.setattr(calendar_facade, "_exchange", lambda: None)
    monkeypatch.setattr(settings, "calendar_facade_token", "tok")

    async def _call(payload, timeout=60.0):
        assert payload["mode"] == "update_event"
        return {"ok": True, "event": {"event_id": payload["event_id"]}}

    monkeypatch.setattr(calendar_facade, "_call", _call)


def _update(event_id: str, **fields) -> Action:
    return Action(capability="calendar.update_event", arguments={"event_id": event_id, **fields},
                  target_ref={"system": "calendar", "id": event_id},
                  reversibility=Reversibility.reversible)


@needs_pg
async def test_a_block_moved_to_another_day_takes_that_day(updates):
    event_id = f"mv_{uuid.uuid4().hex[:10]}"
    await repo.record_calendar_plan_event(event_id=event_id, calendar_id="primary",
                                          kind="work_block", issue_key="DOE-80",
                                          plan_date="2031-06-10", proposal_id="prop_made")
    # 23:30 Denver on the 10th is 05:30Z on the 11th: the LOCAL date is kept.
    out = await executor.execute([_update(event_id, start="2031-06-11T05:30:00Z",
                                          end="2031-06-11T06:30:00Z")], "human:lee", [],
                                 proposal_id=_pid())
    assert (await _ledger(event_id))[0]["plan_date"] == date(2031, 6, 10)
    assert "ledger" not in out.result_text
    out = await executor.execute([_update(event_id, start="2031-06-12T09:00:00-06:00",
                                          end="2031-06-12T10:00:00-06:00")], "human:lee", [],
                                 proposal_id=_pid())
    assert (await _ledger(event_id))[0]["plan_date"] == date(2031, 6, 12)
    assert "moved from 2031-06-10 to 2031-06-12" in out.result_text


@needs_pg
async def test_an_update_without_a_new_start_or_of_a_non_block_keeps_the_ledger(updates):
    block = f"mv_{uuid.uuid4().hex[:10]}"
    place = f"ph_{uuid.uuid4().hex[:10]}"
    await repo.record_calendar_plan_event(event_id=block, calendar_id="primary",
                                          kind="work_block", issue_key="DOE-81",
                                          plan_date="2031-06-10", proposal_id="prop_made")
    await repo.record_calendar_plan_event(event_id=place, calendar_id="primary",
                                          kind="placeholder", proposal_id="prop_made")
    await executor.execute([_update(block, title="Renamed")], "human:lee", [], proposal_id=_pid())
    await executor.execute([_update(place, start="2031-06-14T09:00:00-06:00")], "human:lee", [],
                           proposal_id=_pid())
    commitment = f"cm_{uuid.uuid4().hex[:10]}"
    out = await executor.execute([_update(commitment, start="2031-06-14T09:00:00-06:00")],
                                 "human:lee", [], proposal_id=_pid())
    assert (await _ledger(block))[0]["plan_date"] == date(2031, 6, 10)
    assert (await _ledger(place))[0]["plan_date"] is None
    assert await _ledger(commitment) == [] and "ledger" not in out.result_text


# --- 9. the brief blocks on the right contact kinds ------------------------------------


@pytest.fixture()
def contact(monkeypatch):
    from central_command.api import routes

    monkeypatch.setattr(settings, "dispatch_approval_limit", 10_000)
    monkeypatch.setattr(settings, "ea_contact_budget_per_day", 0)
    monkeypatch.setattr(settings, "calendar_facade_token", "")
    monkeypatch.setattr(calendar_facade, "_exchange", lambda: None)
    seen: list[str] = []

    async def fake(brief, title, agent_id, actor=None):
        seen.append(brief)
        return {"task_id": "task_dp_test", "session_id": "sess_dp_test"}

    monkeypatch.setattr(routes, "create_and_run_task", fake)
    return seen


def _block_of(brief: str) -> dict:
    return json.loads(brief.split("```json\n")[1].split("\n```")[0])


@needs_pg
async def test_the_evening_contact_carries_the_review_of_today(monkeypatch, contact):
    asked = []

    async def review(day):
        asked.append(day)
        return {"available": True, "date": str(day), "items": []}

    monkeypatch.setattr(ea_plan, "review", review)
    await hb_actions._ea_contact("sched_dp_digest", {"kind": "digest"})
    block = _block_of(contact[0])
    assert block["plan_review"]["available"] is True and "plan" not in block
    assert asked == [ea_backlog._today()]
    assert "`plan_review` is today's stored plan read against" in contact[0]
    assert "EVENING REVIEW" in contact[0]


@needs_pg
async def test_a_review_outage_degrades_the_evening_contact(monkeypatch, contact):
    async def boom(day):
        raise ConnectionError("tracker down")

    monkeypatch.setattr(ea_plan, "review", boom)
    out = await hb_actions._ea_contact("sched_dp_down", {"kind": "digest"})
    assert out["delivered"] is True
    assert _block_of(contact[0])["plan_review"] == {"available": False, "error": "tracker down"}


@needs_pg
async def test_with_no_plan_the_evening_review_says_so(monkeypatch, contact):
    monkeypatch.setattr(ea_backlog, "_today", lambda: date(2999, 1, 2))
    await hb_actions._ea_contact("sched_dp_none", {"kind": "digest"})
    assert _block_of(contact[0])["plan_review"] == {"available": False,
                                                    "reason": "no plan stored"}


@pytest.mark.parametrize("kind", ["morning_report", "check_in"])
@needs_pg
async def test_the_morning_and_mid_day_contacts_carry_the_plan(monkeypatch, contact, kind):
    day = date(next(_YEARS), 3, 3)
    monkeypatch.setattr(ea_backlog, "_today", lambda: day)
    await hb_actions._ea_contact(f"sched_dp_{kind}", {"kind": kind})
    block = _block_of(contact[0])
    assert block["plan"] == {"available": False, "reason": "no plan stored",
                             "date": day.isoformat()}
    assert "plan_review" not in block
    assert "`plan` is today's STORED plan" in contact[0]

    await _plan(day.isoformat(), "DOE-90", proposal_id="prop_brief")
    await hb_actions._ea_contact(f"sched_dp_{kind}_2", {"kind": kind})
    plan = _block_of(contact[1])["plan"]
    assert plan["available"] is True and plan["proposal_id"] == "prop_brief"
    assert plan["items"] == [{"rank": 1, "issue_key": "DOE-90", "reason": "why DOE-90",
                              "estimate_hours": 1.0, "tier": "standard"}]


@needs_pg
async def test_a_plan_read_that_fails_degrades_the_morning_contact(monkeypatch, contact):
    async def boom(day=None):
        raise ConnectionError("database hiccup")

    monkeypatch.setattr(ea_plan, "brief", boom)
    await hb_actions._ea_contact("sched_dp_brief_down", {"kind": "morning_report"})
    assert _block_of(contact[0])["plan"] == {"available": False, "error": "database hiccup"}


@needs_pg
async def test_the_week_ahead_carries_neither(contact):
    await hb_actions._ea_contact("sched_dp_week", {"kind": "week_ahead"})
    block = _block_of(contact[0])
    assert "plan" not in block and "plan_review" not in block


# --- 10. framings and charter -------------------------------------------------------------


def test_the_evening_framing_is_the_review():
    text = hb_actions._EA_FRAMING["digest"]
    for phrase in ("EVENING REVIEW", "`plan_review` is today's STORED plan",
                   "it is NEVER done for you", "ASK the operator why; never guess a reason",
                   "how long each done item actually took",
                   "whether any item the tracker still shows open was in fact finished",
                   "`unplanned_done`", "added to the backlog or dropped from it",
                   "`unscored` issues tomorrow's plan would need scored",
                   "Draft TOMORROW in words", "TOMORROW's, and its `free_time`",
                   "Propose no calendar change tonight",
                   "as ONE `plan.record_review` proposal",
                   "conclude-before-you-propose rule",
                   "Never mark work done on your own word",
                   "If `plan_review` says no plan is stored, say so, and review the day "
                   "from the tracker and the calendar alone",
                   "never restate or re-ask one", "Decisions Inbox", "whose they are"):
        assert phrase in text, phrase


def test_the_morning_framing_stores_the_plan_first():
    text = hb_actions._EA_FRAMING["morning_report"]
    for phrase in ("STORE THE PLAN AND BLOCK THE TIME",
                   "as ONE proposal through `propose_calendar_change`",
                   "FIRST action is `plan.record_day`", "no numbers",
                   "one `calendar.create_event` action per block",
                   "`plan` says whether a plan is already stored for today",
                   "propose another only when the operator asks to replan",
                   "the proposal is then the `plan.record_day` action alone",
                   "nothing is stored or on the calendar until the operator approves"):
        assert phrase in text, phrase
    assert "recorded only in the evening review" in hb_actions._EA_FRAMING["check_in"]
    assert "`plan` is today's stored plan" in hb_actions._EA_FRAMING["check_in"]


def test_the_charter_carries_the_review_doctrine():
    c = ea_profile.CHARTER
    for phrase in ("THE STORED PLAN IS THE RECORD OF WHAT WAS PLANNED",
                   "the evening review compares that record with what happened",
                   "how long the work actually took", "work finished outside the tracker",
                   "record them ONCE, as one proposal, after you conclude",
                   "Never mark work done on your own word",
                   "ONE proposal: the stored plan first, then one block per action",
                   "or releasing it from the ledger"):
        assert phrase in c, phrase
    assert "capability = '" not in c and "plan.record_day" not in c


# --- 11. every place a capability and a pack touch ------------------------------------------


@pytest.mark.parametrize("name,target_args,dependency", [
    ("plan.record_day", (), ("local",)),
    ("plan.record_review", (), ("local",)),
    ("calendar.release_event", ("event_id",), ("local",)),
])
def test_each_capability_is_complete(name, target_args, dependency):
    row = next(c for c in capabilities.REGISTRY if c.name == name)
    assert (row.kind, row.gate, row.default_gate, row.reversibility, row.acknowledge,
            row.target_args) == ("write", "human approval", "human", "reversible", False,
                                 target_args)
    assert executor.HANDLERS[name].__name__ == "_" + name.replace(".", "_")
    assert name in ARG_SPECS and CAPABILITY_DEPENDENCY[name] == dependency
    assert name in packs.known_capability_names()


def test_the_plan_pack_and_the_release_capability_are_offered():
    pack = packs.PACKS["plan-propose"]
    # jira.set_planning_fields joined the pack at step 4b (the planning fields).
    assert {c.name for c in pack.capabilities} == {"plan.record_day", "plan.record_review",
                                                   "jira.set_planning_fields"}
    assert pack.tool_names == ("propose_action",)
    assert "calendar.release_event" in {c.name for c in packs.PACKS["calendar-propose"].capabilities}
    section = packs.charter_section(("plan-propose", "calendar-propose"))
    for phrase in ("capability = 'plan.record_day'", "capability = 'plan.record_review'",
                   "capability = 'calendar.release_event'", "Do NOT send value, estimate",
                   "Never mark work done on your own word", "FIRST action",
                   "changes nothing on the calendar"):
        assert phrase in section, phrase
    assert "plan-propose" in EA_TEMPLATE.default_packs


def test_the_adoption_risk_names_the_way_back():
    row = next(c for c in capabilities.REGISTRY if c.name == "calendar.adopt_event")
    assert "calendar.release_event" in row.risk


def test_the_plan_pack_is_not_seeded_in_the_schema():
    sql = (pathlib.Path(repo.__file__).with_name("schema.sql")).read_text(encoding="utf-8")
    assert "'plan-propose'" not in sql
