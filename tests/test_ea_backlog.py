"""The EA's ranked backlog (the EA planning design of 2026-11-18, step 2; DL-221).

`ea_backlog.rank()` is pure and tested exhaustively; `backlog()` is the outside
read, tested against a faked Jira client. Then the brief that carries the
block, the snapshot's `lines_of_effort`, the native estimate on both API
flavours, the charter and the template pack.

NO NETWORK: conftest holds `ea_backlog.backlog` unconfigured for the whole
suite; `_REAL_BACKLOG` is the function as imported, put back where needed, with
`jira.list_fields` / `jira.search_issues` (or `jira._call`) faked under it.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest

from central_command.config import settings
from central_command.db import repo
from central_command.heartbeat import actions as hb_actions
from central_command.integrations import calendar_facade, jira
from central_command.reports import ea_backlog, ea_report
from central_command.reports.ea_backlog import rank
from central_command.runtime import ea_profile
from tests.conftest import needs_pg

_REAL_BACKLOG = ea_backlog.backlog
TODAY = date(2026, 11, 18)


def _issue(key: str, **kw) -> dict:
    """A fully scored issue unless a field is overridden (None = absent)."""
    base = {
        "issue_key": key,
        "summary": f"Summary {key}",
        "status": "To Do",
        "priority": "Medium",
        "assignee": "Jo Doe",
        "due_date": None,
        "estimate_hours": 2.0,
        "stakes": "Gets the thing done.",
        "value": 4,
        "lateness_loss": 6,
        "deadline_type": "soft",
    }
    base.update(kw)
    return base


def _tier(out: dict, name: str) -> list[str]:
    return [r["issue_key"] for r in out["tiers"][name]]


def _row(out: dict, key: str) -> dict:
    return next(r for t in out["tiers"].values() for r in t if r["issue_key"] == key)


# --- rank(): tiers ------------------------------------------------------------


def test_every_issue_lands_in_exactly_one_tier():
    issues = [
        _issue("A-1", due_date=TODAY.isoformat()),  # must start
        _issue("A-2", due_date=(TODAY + timedelta(days=30)).isoformat()),
        _issue("A-3"),  # no due date
        _issue("A-4", value=None),  # unscored
    ]
    out = rank(issues, TODAY, 6)
    keys = [r["issue_key"] for t in out["tiers"].values() for r in t]
    assert sorted(keys) == ["A-1", "A-2", "A-3", "A-4"]
    assert _tier(out, "must_start") == ["A-1"]
    assert _tier(out, "standard") == ["A-2", "A-3"]
    assert _tier(out, "unscored") == ["A-4"]
    assert out["counts"] == {"past_hard": 0, "must_start": 1, "standard": 2, "unscored": 1}
    for name, rows in out["tiers"].items():
        assert all(r["tier"] == name for r in rows)
        assert [r["rank"] for r in rows] == list(range(1, len(rows) + 1))


def test_latest_start_exactly_today_is_must_start_and_tomorrow_is_not():
    # 12 h at 6 h/day = 2 working days: due tomorrow -> latest start today.
    today_start = _issue("B-1", estimate_hours=12, due_date=(TODAY + timedelta(days=1)).isoformat())
    # Due the day after: latest start tomorrow, not yet.
    tomorrow_start = _issue(
        "B-2", estimate_hours=12, due_date=(TODAY + timedelta(days=2)).isoformat()
    )
    out = rank([today_start, tomorrow_start], TODAY, 6)
    assert _row(out, "B-1")["latest_start"] == TODAY.isoformat()
    assert _row(out, "B-1")["tier"] == "must_start"
    assert _row(out, "B-2")["latest_start"] == (TODAY + timedelta(days=1)).isoformat()
    assert _row(out, "B-2")["tier"] == "standard"


def test_the_latest_start_arithmetic():
    due = TODAY + timedelta(days=10)
    cases = [(0.5, 0), (6, 0), (6.01, 1), (12, 1), (13, 2), (30, 4)]
    for hours, back in cases:
        out = rank([_issue("C-1", estimate_hours=hours, due_date=due.isoformat())], TODAY, 6)
        assert _row(out, "C-1")["latest_start"] == (due - timedelta(days=back)).isoformat(), hours
    # hours_per_day changes the day count
    out = rank([_issue("C-2", estimate_hours=12, due_date=due.isoformat())], TODAY, 4)
    assert _row(out, "C-2")["latest_start"] == (due - timedelta(days=2)).isoformat()


def test_padding_moves_the_date_not_the_ratio():
    due = (TODAY + timedelta(days=1)).isoformat()
    plain = rank([_issue("D-1", estimate_hours=6, due_date=due)], TODAY, 6)
    padded = rank([_issue("D-1", estimate_hours=6, due_date=due)], TODAY, 6, padding=1.5)
    assert _row(plain, "D-1")["tier"] == "standard"  # latest start tomorrow
    assert _row(padded, "D-1")["tier"] == "must_start"  # 9 h -> 2 days -> today
    assert _row(padded, "D-1")["latest_start"] == TODAY.isoformat()
    assert _row(padded, "D-1")["score"] == 1.0  # 6 / 6, padding not applied
    assert _row(padded, "D-1")["estimate_hours"] == 6.0


def test_a_passed_hard_deadline_is_a_decision_and_leaves_the_ranking():
    yesterday = (TODAY - timedelta(days=1)).isoformat()
    out = rank(
        [_issue("E-1", due_date=yesterday, deadline_type="hard", value=8, estimate_hours=2)],
        TODAY,
        6,
    )
    assert _tier(out, "past_hard") == ["E-1"]
    assert _tier(out, "must_start") == [] and _tier(out, "standard") == []
    row = _row(out, "E-1")
    assert row["tier"] == "past_hard" and row["score"] is None and row["rank"] == 1
    assert row["needs_decision"] == "close_or_redate"
    assert out["counts"] == {"past_hard": 1, "must_start": 0, "standard": 0, "unscored": 0}


@pytest.mark.parametrize(
    "lacks",
    [
        {},  # fully scored
        {"estimate_hours": None},  # no estimate
        {"value": None},  # no value
        {"lateness_loss": None},  # no lateness loss
        {"estimate_hours": None, "value": None, "lateness_loss": None, "stakes": None},
        {"estimate_hours": 0},  # a zero estimate reads as absent
    ],
)
def test_a_passed_hard_deadline_is_past_hard_whatever_else_it_lacks(lacks):
    last_week = (TODAY - timedelta(days=7)).isoformat()
    row = _row(
        rank([_issue("E-3", due_date=last_week, deadline_type="hard", **lacks)], TODAY, 6), "E-3"
    )
    assert row["tier"] == "past_hard" and row["score"] is None
    assert row["needs_decision"] == "close_or_redate"


def test_a_past_hard_row_is_past_hard_with_the_deadline_type_spelled_any_way():
    last_week = (TODAY - timedelta(days=7)).isoformat()
    assert _tier(
        rank([_issue("E-4", due_date=last_week, deadline_type=["Hard"])], TODAY, 6), "past_hard"
    ) == ["E-4"]


def test_past_hard_lists_the_oldest_due_date_first_then_the_key():
    def old(key, days):
        return _issue(
            key,
            due_date=(TODAY - timedelta(days=days)).isoformat(),
            deadline_type="hard",
            value=100 - days,
        )

    out = rank([old("Z-1", 3), old("Z-3", 10), old("Z-2", 10), old("Z-0", 1)], TODAY, 6)
    assert _tier(out, "past_hard") == ["Z-2", "Z-3", "Z-1", "Z-0"]
    assert [r["rank"] for r in out["tiers"]["past_hard"]] == [1, 2, 3, 4]
    assert all(r["score"] is None for r in out["tiers"]["past_hard"])


def test_the_tiers_come_in_the_order_past_hard_first():
    assert ea_backlog.TIERS == ("past_hard", "must_start", "standard", "unscored")


def test_only_a_past_hard_row_carries_needs_decision():
    yesterday = (TODAY - timedelta(days=1)).isoformat()
    out = rank(
        [
            _issue("Y-1", due_date=yesterday, deadline_type="hard"),  # past_hard
            _issue("Y-2", due_date=yesterday, deadline_type="soft"),  # must_start
            _issue("Y-3", due_date=(TODAY + timedelta(days=30)).isoformat()),  # standard
            _issue("Y-4", value=None),  # unscored
        ],
        TODAY,
        6,
    )
    have = {r["issue_key"] for t in out["tiers"].values() for r in t if "needs_decision" in r}
    assert have == {"Y-1"}
    assert _row(out, "Y-2")["tier"] == "must_start"


def test_a_hard_deadline_due_today_is_not_passed():
    out = rank([_issue("E-2", due_date=TODAY.isoformat(), deadline_type="Hard")], TODAY, 6)
    assert _tier(out, "must_start") == ["E-2"] and _tier(out, "past_hard") == []
    assert _row(out, "E-2")["deadline_type"] == "hard"
    assert "needs_decision" not in _row(out, "E-2")


def test_a_passed_soft_deadline_stays_in_must_start():
    out = rank(
        [_issue("F-1", due_date=(TODAY - timedelta(days=5)).isoformat(), deadline_type="soft")],
        TODAY,
        6,
    )
    assert _tier(out, "must_start") == ["F-1"] and _tier(out, "past_hard") == []
    assert "needs_decision" not in _row(out, "F-1")


def test_no_deadline_type_is_treated_as_soft_and_named_missing():
    out = rank(
        [_issue("F-2", due_date=(TODAY - timedelta(days=2)).isoformat(), deadline_type=None)],
        TODAY,
        6,
    )
    row = _row(out, "F-2")
    assert row["tier"] == "must_start"
    assert row["missing"] == ["deadline_type"]


def test_no_lateness_loss_ranks_on_value_and_says_what_is_missing():
    out = rank(
        [_issue("G-1", due_date=TODAY.isoformat(), lateness_loss=None, deadline_type=None)],
        TODAY,
        6,
    )
    row = _row(out, "G-1")
    assert row["tier"] == "standard"
    assert row["missing"] == ["lateness_loss", "deadline_type"]
    assert row["latest_start"] == TODAY.isoformat()


def test_lateness_without_value_still_must_start_when_its_time_has_come():
    out = rank([_issue("G-2", due_date=TODAY.isoformat(), value=None)], TODAY, 6)
    row = _row(out, "G-2")
    assert row["tier"] == "must_start" and row["missing"] == ["value"]
    # ...and is unscored while there is still time
    out = rank(
        [_issue("G-3", due_date=(TODAY + timedelta(days=9)).isoformat(), value=None)], TODAY, 6
    )
    assert _tier(out, "unscored") == ["G-3"]


# --- rank(): order and ties ---------------------------------------------------


def test_must_start_orders_by_lateness_per_hour_then_due_then_key():
    due0, due_past = TODAY.isoformat(), (TODAY - timedelta(days=1)).isoformat()
    issues = [
        _issue("H-1", due_date=due0, lateness_loss=10, estimate_hours=5),  # 2.0
        _issue("H-2", due_date=due0, lateness_loss=9, estimate_hours=1),  # 9.0
        _issue("H-4", due_date=due0, lateness_loss=4, estimate_hours=2),  # 2.0 tie
        _issue("H-3", due_date=due0, lateness_loss=4, estimate_hours=2),  # 2.0 tie
        _issue("H-5", due_date=due_past, lateness_loss=2, estimate_hours=1),  # 2.0 tie, earlier
    ]
    out = rank(issues, TODAY, 6)
    assert _tier(out, "must_start") == ["H-2", "H-5", "H-1", "H-3", "H-4"]
    assert [r["score"] for r in out["tiers"]["must_start"]] == [9.0, 2.0, 2.0, 2.0, 2.0]


def test_standard_orders_by_value_per_hour_then_due_none_last_then_key():
    far = (TODAY + timedelta(days=40)).isoformat()
    farther = (TODAY + timedelta(days=50)).isoformat()
    issues = [
        _issue("J-1", value=3, estimate_hours=1),  # 3.0, no due
        _issue("J-2", value=10, estimate_hours=2, due_date=farther),  # 5.0
        _issue("J-3", value=6, estimate_hours=2, due_date=farther),  # 3.0
        _issue("J-4", value=6, estimate_hours=2, due_date=far),  # 3.0, earliest
        _issue("J-0", value=3, estimate_hours=1),  # 3.0, no due
    ]
    out = rank(issues, TODAY, 6)
    assert _tier(out, "standard") == ["J-2", "J-4", "J-3", "J-0", "J-1"]


def test_unscored_is_listed_by_due_then_key_with_no_score():
    issues = [
        _issue("K-2", value=None),
        _issue("K-1", estimate_hours=None),
        _issue("K-3", value=None, due_date=(TODAY + timedelta(days=3)).isoformat()),
    ]
    out = rank(issues, TODAY, 6)
    assert _tier(out, "unscored") == ["K-3", "K-1", "K-2"]
    assert all(r["score"] is None for r in out["tiers"]["unscored"])


def test_rank_is_deterministic_whatever_the_input_order():
    issues = [
        _issue(
            f"L-{i}",
            value=i % 3,
            estimate_hours=1 + i % 2,
            due_date=(TODAY + timedelta(days=i % 4)).isoformat(),
        )
        for i in range(1, 13)
    ]
    first = rank(issues, TODAY, 6)
    second = rank(list(reversed(issues)), TODAY, 6)
    assert first == second


# --- rank(): missing and bad numbers ------------------------------------------


def test_missing_names_exactly_what_is_absent_in_every_combination():
    fields = {
        "stakes": "stakes",
        "value": "value",
        "lateness_loss": "lateness_loss",
        "deadline_type": "deadline_type",
        "estimate_hours": "estimate",
        "due_date": "due_date",
    }
    full = _issue("M-0", due_date=(TODAY + timedelta(days=20)).isoformat())
    names = list(fields)
    for mask in range(1 << len(names)):
        absent = [n for i, n in enumerate(names) if mask & (1 << i)]
        issue = dict(full, **{n: None for n in absent})
        row = _row(rank([issue], TODAY, 6), "M-0")
        expected = [fields[n] for n in names if n in absent]
        assert row["missing"] == [m for m in ea_backlog.PLANNING_INPUTS if m in expected], absent
        scored = "value" not in absent and "estimate_hours" not in absent
        if not scored and row["tier"] != "must_start":
            assert row["tier"] == "unscored", absent


@pytest.mark.parametrize(
    "bad", ["5", "", "five", -1, -0.5, float("nan"), float("inf"), True, [3], {"value": 3}]
)
def test_a_bad_number_is_absent_and_named_never_raised(bad):
    for field, name in (
        ("value", "value"),
        ("lateness_loss", "lateness_loss"),
        ("estimate_hours", "estimate"),
    ):
        row = _row(rank([_issue("N-1", **{field: bad})], TODAY, 6), "N-1")
        assert name in row["missing"], (field, bad)
        assert row[field] is None, (field, bad)


def test_a_zero_estimate_is_absent_but_zero_value_is_a_value():
    row = _row(rank([_issue("N-2", estimate_hours=0)], TODAY, 6), "N-2")
    assert row["tier"] == "unscored" and "estimate" in row["missing"]
    row = _row(rank([_issue("N-3", value=0)], TODAY, 6), "N-3")
    assert row["tier"] == "standard" and row["score"] == 0.0 and row["missing"] == ["due_date"]


@pytest.mark.parametrize("bad", ["2026-02-31", "tomorrow", 20261118, ""])
def test_a_bad_due_date_is_absent(bad):
    row = _row(rank([_issue("N-4", due_date=bad)], TODAY, 6), "N-4")
    assert row["due_date"] is None and "due_date" in row["missing"]
    assert row["latest_start"] is None


@pytest.mark.parametrize("bad", ["firm", 1, "", None, ["hard", "soft"]])
def test_a_bad_deadline_type_is_absent(bad):
    row = _row(rank([_issue("N-5", deadline_type=bad)], TODAY, 6), "N-5")
    assert row["deadline_type"] is None and "deadline_type" in row["missing"]


def test_a_single_option_list_deadline_type_is_read():
    row = _row(rank([_issue("N-6", deadline_type=["Hard"])], TODAY, 6), "N-6")
    assert row["deadline_type"] == "hard"


def test_rows_carry_the_documented_fields_and_bound_the_text():
    issue = _issue(
        "P-1",
        summary="s" * 1000,
        stakes="t" * 1000,
        due_date=TODAY.isoformat(),
        url="https://jira.example.com/browse/P-1",
    )
    row = _row(rank([issue], TODAY, 6), "P-1")
    assert set(row) == {
        "issue_key",
        "summary",
        "status",
        "priority",
        "assignee",
        "due_date",
        "estimate_hours",
        "stakes",
        "value",
        "lateness_loss",
        "deadline_type",
        "tier",
        "rank",
        "score",
        "latest_start",
        "missing",
        "url",
    }
    assert len(row["summary"]) <= ea_backlog.SUMMARY_CHARS + 1
    assert len(row["stakes"]) <= ea_backlog.STAKES_CHARS + 1
    assert row["value"] == 4.0 and row["lateness_loss"] == 6.0


@pytest.mark.parametrize("hpd,pad", [(0, 1.0), (-6, 1.0), (6, 0), (6, -1)])
def test_a_bad_configuration_is_refused(hpd, pad):
    with pytest.raises(ValueError):
        rank([], TODAY, hpd, padding=pad)


def test_an_empty_backlog_ranks_to_empty_tiers():
    assert rank([], TODAY, 6) == {
        "tiers": {"past_hard": [], "must_start": [], "standard": [], "unscored": []},
        "counts": {"past_hard": 0, "must_start": 0, "standard": 0, "unscored": 0},
    }


# --- backlog(): the outside read ------------------------------------------------


_DECLARED = [
    {"id": "customfield_1", "name": "Stakes", "type": "textarea", "writable": True},
    {"id": "customfield_2", "name": "Value", "type": "float", "writable": True},
    {"id": "customfield_3", "name": "Lateness loss", "type": "float", "writable": True},
]


def _row_in(key, custom=None, **kw):
    row = {
        "issue_key": key,
        "summary": f"S {key}",
        "status": "To Do",
        "priority": "High",
        "assignee": "Jo Doe",
        "due_date": None,
        "estimate_hours": 2.0,
        "url": f"https://jira.example.com/browse/{key}",
    }
    row.update(kw)
    if custom:
        row["custom_fields"] = custom
    return row


@pytest.fixture()
def real_backlog(monkeypatch):
    monkeypatch.setattr(ea_backlog, "backlog", _REAL_BACKLOG)
    monkeypatch.setattr(jira, "configured", lambda: True)
    monkeypatch.setattr(ea_backlog, "_today", lambda: TODAY)
    monkeypatch.setattr(settings, "plan_hours_per_day", 6.0)
    monkeypatch.setattr(settings, "plan_brief_items", 25)
    monkeypatch.setattr(settings, "plan_backlog_limit", 200)
    # The padding is the evening review's record (step 4a); these tests rank
    # with the estimate as entered. tests/test_day_plan.py covers the padding.
    from central_command.reports import ea_plan

    async def no_padding(today=None):
        return {"ratio": 1.0, "samples": 0, "window_days": 30, "min_samples": 5}

    monkeypatch.setattr(ea_plan, "padding", no_padding)


def _fake_jira(monkeypatch, rows, *, declared=_DECLARED, truncated=False, note=None):
    seen: dict = {}

    async def list_fields():
        return {"ok": True, "fields": list(declared), "count": len(declared)}

    async def search(jql, limit=None):
        seen["jql"], seen["limit"] = jql, limit
        return {
            "ok": True,
            "issues": list(rows),
            "count": len(rows),
            "truncated": truncated,
            **({"custom_fields_error": note} if note else {}),
        }

    monkeypatch.setattr(jira, "list_fields", list_fields)
    monkeypatch.setattr(jira, "search_issues", search)
    return seen


async def test_backlog_reads_the_configured_jql_and_ranks(monkeypatch, real_backlog):
    rows = [
        _row_in(
            "Q-1",
            {"Stakes": "Ship it", "Value": 8, "Lateness loss": 20},
            due_date=TODAY.isoformat(),
        ),
        _row_in("Q-2", {"Value": 3}),
        _row_in("Q-3"),
    ]
    seen = _fake_jira(monkeypatch, rows)
    monkeypatch.setattr(settings, "plan_backlog_jql", "project = TASKS")
    out = await ea_backlog.backlog()
    assert seen == {"jql": "project = TASKS", "limit": 200}
    assert out["available"] is True and out["unit"] == settings.plan_value_unit
    assert out["planning_fields"] == {
        "declared": ["Stakes", "Value", "Lateness loss"],
        "missing": ["Deadline type"],
    }
    assert out["counts"] == {"past_hard": 0, "must_start": 1, "standard": 1, "unscored": 1}
    assert [i["issue_key"] for i in out["items"]] == ["Q-1", "Q-2", "Q-3"]
    assert out["items"][0]["stakes"] == "Ship it" and out["items"][0]["score"] == 10.0
    assert out["truncated"] is False and out["items_omitted"] == 0


def test_the_default_jql_is_the_operators_open_work():
    from central_command.config import Settings

    assert Settings.model_fields["plan_backlog_jql"].default == (
        "assignee = currentUser() AND statusCategory != Done"
    )
    defaults = {
        k: Settings.model_fields[k].default
        for k in (
            "plan_field_stakes",
            "plan_field_value",
            "plan_field_lateness",
            "plan_field_deadline_type",
            "plan_value_unit",
            "plan_hours_per_day",
            "plan_backlog_limit",
            "plan_brief_items",
        )
    }
    assert defaults == {
        "plan_field_stakes": "Stakes",
        "plan_field_value": "Value",
        "plan_field_lateness": "Lateness loss",
        "plan_field_deadline_type": "Deadline type",
        "plan_value_unit": "points",
        "plan_hours_per_day": 6.0,
        "plan_backlog_limit": 200,
        "plan_brief_items": 25,
    }


async def test_field_names_are_settings_resolved_by_name(monkeypatch, real_backlog):
    declared = [
        {"id": "customfield_9", "name": "Worth", "type": "float", "writable": True},
        {"id": "customfield_8", "name": "Kind", "type": "select", "writable": True},
    ]
    monkeypatch.setattr(settings, "plan_field_value", "Worth")
    monkeypatch.setattr(settings, "plan_field_deadline_type", "Kind")
    _fake_jira(monkeypatch, [_row_in("R-1", {"Worth": 5, "Kind": "hard"})], declared=declared)
    out = await ea_backlog.backlog()
    assert out["planning_fields"]["declared"] == ["Worth", "Kind"]
    assert out["planning_fields"]["missing"] == ["Stakes", "Lateness loss"]
    item = out["items"][0]
    assert item["value"] == 5.0 and item["deadline_type"] == "hard"


async def test_a_field_named_in_another_case_is_the_configured_field(monkeypatch, real_backlog):
    """An operator who creates "Deadline Type" for the setting "Deadline type"
    has created the field (found live, 2026-11-19): it is declared, and its
    values are read under the tracker's spelling."""
    declared = [
        *_DECLARED[:2],
        {"id": "customfield_3", "name": "Lateness Loss", "type": "float", "writable": True},
        {"id": "customfield_4", "name": " deadline TYPE", "type": "select", "writable": True},
    ]
    _fake_jira(
        monkeypatch,
        [_row_in("R-9", {"Value": 5, "Lateness Loss": 3, " deadline TYPE": "hard"})],
        declared=declared,
    )
    out = await ea_backlog.backlog()
    assert out["planning_fields"] == {
        "declared": ["Stakes", "Value", "Lateness Loss", " deadline TYPE"],
        "missing": [],
    }
    item = out["items"][0]
    assert item["lateness_loss"] == 3.0 and item["deadline_type"] == "hard"
    assert await ea_backlog.tracker_field_names() == {
        "stakes": "Stakes",
        "value": "Value",
        "lateness_loss": "Lateness Loss",
        "deadline_type": " deadline TYPE",
    }


def test_match_field_name_prefers_exact_and_refuses_an_ambiguous_case():
    from central_command.integrations import jira

    both = [{"name": "Value"}, {"name": "VALUE"}]
    assert jira.match_field_name("Value", both) == "Value", "exact wins"
    assert jira.match_field_name("value", both) is None, "two loose matches: no guess"
    assert jira.match_field_name("value", both[:1]) == "Value"
    assert jira.match_field_name("Worth", both) is None
    assert jira.match_field_name("Value", [{"name": None}, {}]) is None


def test_a_plain_text_paragraph_field_is_writable_as_text():
    """Cloud reports a plain-text paragraph field as `longtext`; unmapped, a
    Stakes field of that type read as unreadable (found live, 2026-11-19)."""
    from central_command.integrations import jira

    schema = {
        "type": "string",
        "custom": "com.atlassian.jira.plugin.system.customfieldtypes:longtext",
    }
    assert jira._shape_of(schema) == "text"
    assert jira._coerce_custom("Stakes", "text", "Roof leaks by spring") == "Roof leaks by spring"


async def test_no_planning_field_declared_at_all(monkeypatch, real_backlog):
    _fake_jira(monkeypatch, [_row_in("R-2")], declared=[])
    out = await ea_backlog.backlog()
    assert out["planning_fields"]["declared"] == []
    assert out["planning_fields"]["missing"] == [
        "Stakes",
        "Value",
        "Lateness loss",
        "Deadline type",
    ]
    assert out["counts"]["unscored"] == 1


async def test_a_declared_field_the_search_cannot_read_is_said_so(monkeypatch, real_backlog):
    declared = [
        *_DECLARED[:2],
        {"id": "customfield_3", "name": "Lateness loss", "type": "calculated", "writable": False},
    ]
    _fake_jira(monkeypatch, [], declared=declared)
    out = await ea_backlog.backlog()
    assert out["planning_fields"]["unreadable"] == ["Lateness loss"]


async def test_truncation_is_reported(monkeypatch, real_backlog):
    monkeypatch.setattr(settings, "plan_backlog_limit", 2)
    seen = _fake_jira(monkeypatch, [_row_in("S-1"), _row_in("S-2")], truncated=True)
    out = await ea_backlog.backlog()
    assert seen["limit"] == 2 and out["truncated"] is True


async def test_the_brief_cap_takes_must_start_then_standard_then_unscored(
    monkeypatch, real_backlog
):
    monkeypatch.setattr(settings, "plan_brief_items", 3)
    rows = [
        _row_in("T-9"),  # unscored
        _row_in("T-1", {"Value": 1}),  # standard 0.5
        _row_in("T-2", {"Value": 9}),  # standard 4.5
        _row_in("T-3", {"Value": 1, "Lateness loss": 1}, due_date=TODAY.isoformat()),
    ]
    _fake_jira(monkeypatch, rows)
    out = await ea_backlog.backlog()
    assert [i["issue_key"] for i in out["items"]] == ["T-3", "T-2", "T-1"]
    assert out["items_omitted"] == 1
    assert out["counts"] == {"past_hard": 0, "must_start": 1, "standard": 2, "unscored": 1}


async def test_the_brief_block_lists_the_past_hard_issues_first(monkeypatch, real_backlog):
    declared = [
        *_DECLARED,
        {"id": "customfield_4", "name": "Deadline type", "type": "select", "writable": True},
    ]
    old = (TODAY - timedelta(days=9)).isoformat()
    recent = (TODAY - timedelta(days=2)).isoformat()
    rows = [
        _row_in(
            "W-1",
            {"Value": 9, "Lateness loss": 9, "Deadline type": "soft"},
            due_date=TODAY.isoformat(),
        ),  # must_start
        _row_in("W-2", {"Value": 5}),  # standard
        _row_in("W-3", {"Deadline type": "hard"}, due_date=recent),  # past_hard, no value
        _row_in(
            "W-4", {"Value": 9, "Lateness loss": 9, "Deadline type": "hard"}, due_date=old
        ),  # past_hard
    ]
    _fake_jira(monkeypatch, rows, declared=declared)
    out = await ea_backlog.backlog()
    assert [i["issue_key"] for i in out["items"]] == ["W-4", "W-3", "W-1", "W-2"]
    assert [i["tier"] for i in out["items"]] == ["past_hard", "past_hard", "must_start", "standard"]
    assert out["counts"] == {"past_hard": 2, "must_start": 1, "standard": 1, "unscored": 0}
    assert [i.get("needs_decision") for i in out["items"]] == [
        "close_or_redate",
        "close_or_redate",
        None,
        None,
    ]
    assert out["items"][0]["score"] is None


async def test_a_past_hard_issue_is_not_offered_for_enrichment(monkeypatch, real_backlog):
    declared = [
        *_DECLARED,
        {"id": "customfield_4", "name": "Deadline type", "type": "select", "writable": True},
    ]
    old = (TODAY - timedelta(days=9)).isoformat()
    rows = [
        _row_in("V-1", {"Deadline type": "hard"}, due_date=old),  # past_hard, no value
        _row_in("V-2", {"Value": 3}, due_date=(TODAY + timedelta(days=9)).isoformat()),
        _row_in("V-3"),  # unscored
    ]
    _fake_jira(monkeypatch, rows, declared=declared)
    out = await ea_backlog.planning_gaps()
    assert [i["issue_key"] for i in out["issues"]] == ["V-2", "V-3"]
    assert out["totals"]["read"] == 3 and out["totals"]["needing_enrichment"] == 2


async def test_an_unconfigured_tracker_is_said_so_and_never_called(monkeypatch, real_backlog):
    async def _explode(*a, **kw):
        raise AssertionError("an unconfigured tracker must not be called")

    monkeypatch.setattr(jira, "configured", lambda: False)
    monkeypatch.setattr(jira, "list_fields", _explode)
    monkeypatch.setattr(jira, "search_issues", _explode)
    assert await ea_backlog.backlog() == {"available": False, "reason": "not configured"}


async def test_backlog_does_not_catch_its_own_outage(monkeypatch, real_backlog):
    async def boom(*a, **kw):
        raise ConnectionError("tracker down")

    monkeypatch.setattr(jira, "list_fields", boom)
    with pytest.raises(ConnectionError):
        await ea_backlog.backlog()


async def test_a_failed_custom_field_listing_is_not_a_ranking(monkeypatch, real_backlog):
    _fake_jira(
        monkeypatch, [_row_in("U-1")], note="searchIssues — custom fields could not be listed"
    )
    with pytest.raises(RuntimeError, match="custom fields could not be listed"):
        await ea_backlog.backlog()


# --- the native estimate, both flavours ------------------------------------------


class _Resp:
    def __init__(self, body):
        self.status_code = 200
        self._body = body
        self.text = json.dumps(body)

    def json(self):
        return self._body


@pytest.fixture()
def native_jira(monkeypatch):
    monkeypatch.setattr(settings, "jira_base_url", "https://jira.example.com")
    monkeypatch.setattr(settings, "jira_email", "bot@example.com")
    monkeypatch.setattr(settings, "jira_api_token", "tok")
    monkeypatch.setattr(settings, "jira_auth_mode", "basic")

    def answer(bodies):
        calls = []

        async def fake(method, path, json_body=None):
            calls.append((method, path, json_body))
            return _Resp(bodies.pop(0))

        monkeypatch.setattr(jira, "_call", fake)
        return calls

    return answer


@pytest.mark.parametrize("flavor", ["cloud", "server"])
async def test_the_estimate_is_read_on_both_flavours(monkeypatch, native_jira, flavor):
    monkeypatch.setattr(settings, "jira_api_flavor", flavor)
    fields = [
        {"summary": "flat", "timeoriginalestimate": 5400, "priority": {"name": "High"}},
        {"summary": "object", "timetracking": {"originalEstimateSeconds": 7200}},
        {
            "summary": "both",
            "timeoriginalestimate": 3600,
            "timetracking": {"originalEstimateSeconds": 99999},
        },
        {"summary": "none"},
        {"summary": "bad", "timeoriginalestimate": "1h"},
    ]
    page = {"issues": [{"key": f"V-{i}", "fields": f} for i, f in enumerate(fields, 1)]}
    page.update({"total": 5} if flavor == "server" else {"isLast": True})
    calls = native_jira([page])
    out = await jira.search_issues("project = V")
    assert {"timeoriginalestimate", "timetracking", "priority"} <= set(calls[0][2]["fields"])
    assert calls[0][1] == ("/rest/api/2/search" if flavor == "server" else "/rest/api/3/search/jql")
    assert [i["estimate_hours"] for i in out["issues"]] == [1.5, 2.0, 1.0, None, None]
    assert out["issues"][0]["priority"] == "High" and out["issues"][3]["priority"] is None

    native_jira([{"key": "V-1", "fields": {"summary": "x", "timeoriginalestimate": 1800}}])
    issue = (await jira.get_issue("V-1"))["issue"]
    assert issue["estimate_hours"] == 0.5


def test_the_existing_read_keys_are_unchanged():
    assert jira.FIELDS.startswith(
        "summary,description,priority,status,issuetype,labels,"
        "duedate,created,updated,assignee,parent,issuelinks"
    )
    assert jira.SEARCH_FIELDS[:9] == [
        "summary",
        "status",
        "issuetype",
        "labels",
        "duedate",
        "updated",
        "assignee",
        "parent",
        "issuelinks",
    ]


# --- the brief --------------------------------------------------------------------


@pytest.fixture()
def contact(monkeypatch):
    from central_command.api import routes

    monkeypatch.setattr(settings, "test_model", True)
    monkeypatch.setattr(settings, "dispatch_approval_limit", 10_000)
    monkeypatch.setattr(settings, "ea_contact_budget_per_day", 0)
    monkeypatch.setattr(settings, "calendar_facade_token", "")
    seen: list[str] = []

    async def fake(brief, title, agent_id, actor=None):
        seen.append(brief)
        return {"task_id": "task_bl_test", "session_id": "sess_bl_test"}

    monkeypatch.setattr(routes, "create_and_run_task", fake)
    return seen


def _block(brief: str) -> dict:
    return json.loads(brief.split("```json\n")[1].split("\n```")[0])


@needs_pg
async def test_the_brief_carries_the_ranked_backlog(monkeypatch, contact):
    async def ranked():
        return {
            "available": True,
            "unit": "points",
            "counts": {"must_start": 1},
            "planning_fields": {"declared": [], "missing": []},
            "items": [],
        }

    monkeypatch.setattr(ea_backlog, "backlog", ranked)
    await hb_actions._ea_contact("sched_bl_ok", {"kind": "morning_report"})
    brief = contact[0]
    assert _block(brief)["backlog"]["counts"] == {"must_start": 1}
    assert "`backlog` is the operator's OWN open work" in brief
    assert "RANKED BY THE CONTROL PLANE" in brief
    assert "never change it silently" in brief
    assert "stakes, value, lateness loss, due date and estimate together" in brief
    assert "`planning_fields.missing`" in brief and "setup gap" in brief
    assert "MORNING PLAN" in brief


@needs_pg
async def test_a_backlog_outage_degrades_the_contact_but_never_cancels_it(monkeypatch, contact):
    async def boom():
        raise ConnectionError("tracker down")

    monkeypatch.setattr(ea_backlog, "backlog", boom)
    out = await hb_actions._ea_contact("sched_bl_down", {"kind": "morning_report"})
    assert out["delivered"] is True
    block = _block(contact[0])["backlog"]
    assert block == {"available": False, "error": "tracker down"}


@needs_pg
async def test_an_unconfigured_tracker_leaves_the_backlog_out(contact):
    # conftest holds the read unconfigured
    await hb_actions._ea_contact("sched_bl_none", {"kind": "morning_report"})
    assert "backlog" not in _block(contact[0])
    assert "`backlog` is the operator's" not in contact[0]


@needs_pg
async def test_a_budget_deferred_contact_reads_no_backlog(monkeypatch):
    monkeypatch.setattr(settings, "ea_contact_budget_per_day", 3)

    async def _explode(*a, **kw):
        raise AssertionError("nothing outside is read for a deferred contact")

    monkeypatch.setattr(ea_backlog, "backlog", _explode)
    monkeypatch.setattr(calendar_facade, "list_events", _explode)

    async def spent(kind, actor, since):
        return 3

    monkeypatch.setattr(repo, "count_events_since", spent)
    out = await hb_actions._ea_contact("sched_bl_broke", {"kind": "morning_report"})
    assert out["delivered"] is False


def test_the_morning_framing_is_the_plan():
    # Step 3 replaced "You do not block calendar time yet": the blocking
    # sentences are pinned in tests/test_calendar_plan_ledger.py.
    text = hb_actions._EA_FRAMING["morning_report"]
    assert "MORNING PLAN" in text
    assert "three to five" in text and "`must_start`" in text
    assert "CORRECT the plan, not to supply one" in text
    assert "You do not block calendar time yet" not in text
    assert "never imply that any time has been blocked" in text
    assert "`unscored`" in text
    assert "Decisions Inbox" in text and "whose they are" in text
    assert "what the operator wants prioritised" not in text


def test_the_brief_explains_the_past_hard_tier_and_no_longer_ranks_it_standard():
    import inspect
    import re

    # the brief text is adjacent string literals: join them before looking
    src = re.sub(r'"\s*\n\s*"', "", inspect.getsource(hb_actions._ea_contact))
    assert "`past_hard`" in src and "`needs_decision`" in src
    assert "ranks in `standard`" not in src and "has already taken its loss" not in src
    assert "an issue past a soft deadline stays here" in src


@pytest.mark.parametrize("kind", ["morning_report", "week_ahead"])
def test_the_morning_and_weekly_framings_put_past_hard_issues_to_the_operator_first(kind):
    text = hb_actions._EA_FRAMING[kind]
    assert "name EVERY issue in" in text or "FIRST name EVERY issue in" in text
    assert "`past_hard` tier" in text
    assert "can it still be done? If yes it needs a new due date; if no it should be closed" in text
    assert "block calendar time for it" in text
    assert "`jira.set_planning_fields` (`due_date`)" in text
    assert text.index("`past_hard`") < text.index("`must_start`")
    assert (
        "never propose a `plan.record_day` item for it" in hb_actions._EA_FRAMING["morning_report"]
    )


def test_the_evening_and_quarterly_framings_keep_past_hard_out_of_the_plan():
    assert "never an issue in the `past_hard` tier" in hb_actions._EA_FRAMING["digest"]
    assert "`past_hard` tier first" in hb_actions._EA_FRAMING["quarter_ahead"]


def test_the_charter_says_a_missed_hard_deadline_is_a_decision():
    c = ea_profile.CHARTER
    assert "A MISSED HARD DEADLINE IS A DECISION, NOT A RANKING" in c
    assert "can it still be done?" in c and "needs a new due date" in c
    assert "never gets a calendar block" in c
    assert "plan.record_day" not in c  # the charter never names a capability


# --- lines of effort in the snapshot -----------------------------------------------


@needs_pg
async def test_the_snapshot_carries_lines_of_effort_and_their_last_check_in():
    tag = uuid.uuid4().hex[:8]
    quiet, active = f"loe-bl-quiet-{tag}", f"loe-bl-active-{tag}"
    sem = {"questions": ["Did it move?"], "thresholds": "yes = green"}
    await repo.create_loe(quiet, "Quiet line", "weekly", "accountability", sem)
    await repo.create_loe(active, "Active line", "daily", "accountability", sem)
    try:
        await repo.record_loe_checkin(active, "yellow", 60, "old")
        await repo.record_loe_checkin(active, "green", 80, "Ran three times " + "x" * 400)
        snap = await ea_report.snapshot(datetime.now(UTC) - timedelta(hours=1))
        rows = {r["loe_id"]: r for r in snap["lines_of_effort"]}
        assert rows[quiet] == {
            "loe_id": quiet,
            "name": "Quiet line",
            "cadence": "weekly",
            "last_check_in": None,
        }
        last = rows[active]["last_check_in"]
        assert rows[active]["cadence"] == "daily"
        assert last["light"] == "green" and last["summary"].startswith("Ran three times")
        assert len(last["summary"]) == 300 and last["at"]
    finally:
        await repo.retire_loe(quiet, "test")
        await repo.retire_loe(active, "test")
    snap = await ea_report.snapshot(datetime.now(UTC) - timedelta(hours=1))
    assert quiet not in {r["loe_id"] for r in snap["lines_of_effort"]}


# --- the charter and the template ------------------------------------------------


def test_the_charter_carries_the_planning_doctrine():
    charter = ea_profile.CHARTER
    assert "ATTENTION AND PLAN" in charter
    assert "DIGEST FIRST" not in charter
    assert "ARRIVE WITH A PROPOSAL, NOT A QUESTION" in charter
    assert "computed for you by the control plane" in charter
    assert "you must say why" in charter
    assert "The operator's correction wins" in charter
    assert "Always show value beside urgency" in charter
    assert "Never invent a number" in charter
    # kept rules
    assert "THE DATA BLOCK IS GROUND TRUTH" in charter
    assert "OTHER AGENTS' QUESTIONS ARE NOT YOURS TO ASK" in charter
    assert "CONCLUDE BEFORE YOU PROPOSE" in charter
    assert "THE OPERATOR'S WORDS ARE TRUSTED GROUND TRUTH" in charter
    assert (
        'Your human team lead is <<operator_name>> — "the operator" throughout this charter.'
    ) in charter
    # no capability list, no pack names written in
    assert "GRANTED CAPABILITIES" not in charter
    assert "jira-read" not in charter and "capability = '" not in charter
    assert "plan" in ea_profile.ROLE and "backlog" in ea_profile.ROLE


def test_the_template_grants_jira_read():
    from central_command.runtime.templates import EA_TEMPLATE

    assert "jira-read" in EA_TEMPLATE.default_packs
    assert "plan" in EA_TEMPLATE.description.lower()
