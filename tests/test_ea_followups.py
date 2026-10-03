"""The EA's follow-up read — the issue tracker's open follow-up issues.

Until v2.59.0 follow-ups were private graph episodes; they are tracker issues
now (see `reports/ea_followups.py` for why). Mirrors tests/test_ea_calendar.py's
shape: `tracked()` is a SIBLING read over HTTP that can fail, and the anchor
property is that a tracker outage degrades the EA's contact and never cancels
it.

NO NETWORK: `jira.search_issues` is monkeypatched in every test. conftest
replaces `ea_followups.tracked` for the whole suite; `_REAL_TRACKED` is the
function as imported, put back by the fixture below.
"""

from __future__ import annotations

import json

import pytest

from central_command.config import settings
from central_command.db import repo
from central_command.heartbeat import actions as hb_actions
from central_command.integrations import calendar_facade, jira
from central_command.reports import ea_followups
from central_command.runtime import ea_profile, packs
from tests.conftest import needs_pg

_REAL_TRACKED = ea_followups.tracked

_ISSUE = {
    "issue_key": "TASKS-7", "summary": "Vendor owes the SLA answer",
    "status": "To Do", "issue_type": "Task", "labels": ["follow-up"],
    "due_date": "2026-10-09", "updated": "2026-10-02T10:00:00Z",
    "assignee": None, "parent": None, "link_count": 0,
    "url": "https://jira.example.com/browse/TASKS-7",
}


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "executor_mode", "dry_run")
    monkeypatch.setattr(settings, "dispatch_approval_limit", 10_000)
    monkeypatch.setattr(settings, "ea_contact_budget_per_day", 0)
    # No calendar token: keeps these tests about follow-ups, not calendar.
    monkeypatch.setattr(settings, "calendar_facade_token", "")
    monkeypatch.setattr(ea_followups, "tracked", _REAL_TRACKED)
    monkeypatch.setattr(jira, "configured", lambda: True)


def _found(*issues):
    async def fake(jql, limit=None):
        assert ea_profile.FOLLOW_UP_LABEL in jql
        assert "statusCategory != Done" in jql
        return {"ok": True, "issues": list(issues), "count": len(issues),
                "truncated": False}
    return fake


# --- the reader itself -----------------------------------------------------


async def test_tracked_returns_a_compact_issue_list(monkeypatch):
    monkeypatch.setattr(jira, "search_issues", _found(_ISSUE))
    out = await ea_followups.tracked()
    assert out == {
        "available": True,
        "count": 1,
        "truncated": False,
        "issues": [{"issue_key": "TASKS-7",
                    "summary": "Vendor owes the SLA answer",
                    "status": "To Do", "due_date": "2026-10-09",
                    "assignee": None,
                    "url": "https://jira.example.com/browse/TASKS-7"}],
    }


async def test_tracked_is_empty_and_honest_when_there_is_nothing_open(monkeypatch):
    monkeypatch.setattr(jira, "search_issues", _found())
    out = await ea_followups.tracked()
    assert out == {"available": True, "count": 0, "truncated": False, "issues": []}


async def test_an_unconfigured_tracker_is_said_so_and_never_called(monkeypatch):
    async def _explode(*a, **kw):
        raise AssertionError("an unconfigured tracker must not be called")

    monkeypatch.setattr(jira, "configured", lambda: False)
    monkeypatch.setattr(jira, "search_issues", _explode)
    assert await ea_followups.tracked() == {
        "available": False, "reason": "not configured"}


async def test_tracked_raises_on_a_transport_failure(monkeypatch):
    """It does NOT swallow its own failure — same shape as `ea_calendar.day()`,
    which also raises and leaves degrading to the caller (heartbeat/actions.py).
    """

    async def boom(jql, limit=None):
        raise ConnectionError("tracker down")

    monkeypatch.setattr(jira, "search_issues", boom)
    with pytest.raises(ConnectionError):
        await ea_followups.tracked()


# --- the EA's contact --------------------------------------------------------


@pytest.fixture()
def stub_task(monkeypatch):
    from central_command.api import routes

    seen: list[str] = []

    async def fake(brief, title, agent_id, actor=None):
        seen.append(brief)
        return {"task_id": "task_fu_test", "session_id": "sess_fu_test"}

    monkeypatch.setattr(routes, "create_and_run_task", fake)
    return seen


def _block(brief: str) -> dict:
    return json.loads(brief.split("```json\n")[1].split("\n```")[0])


@needs_pg
async def test_the_brief_carries_tracked_follow_ups_and_open_tasks(
    monkeypatch, stub_task
):
    monkeypatch.setattr(jira, "search_issues", _found(_ISSUE))

    await hb_actions._ea_contact("sched_fu_ok", {"kind": "check_in"})

    brief = stub_task[0]
    assert "`tracked_follow_ups` is the issue tracker's open issues" in brief
    block = _block(brief)
    assert block["tracked_follow_ups"]["count"] == 1
    assert isinstance(block["open_tasks"], list)
    # The graph-backed block is gone, and so is its instruction.
    assert "follow_ups" not in block
    assert "closing episode" not in brief


@needs_pg
async def test_an_unconfigured_tracker_leaves_the_block_out(monkeypatch, stub_task):
    monkeypatch.setattr(jira, "configured", lambda: False)

    await hb_actions._ea_contact("sched_fu_none", {"kind": "check_in"})

    assert "tracked_follow_ups" not in _block(stub_task[0])
    assert "`tracked_follow_ups` is" not in stub_task[0]


@needs_pg
async def test_a_tracker_outage_degrades_the_contact_but_never_cancels_it(
    monkeypatch, stub_task
):
    async def boom(jql, limit=None):
        raise ConnectionError("tracker down")

    monkeypatch.setattr(jira, "search_issues", boom)

    from central_command import events

    emitted: list[str] = []
    real_emit = events.emit

    async def spy(kind, ref_id=None, payload=None, actor=None):
        emitted.append(kind)
        return await real_emit(kind, ref_id=ref_id, payload=payload, actor=actor)

    monkeypatch.setattr(events, "emit", spy)

    out = await hb_actions._ea_contact("sched_fu_down", {"kind": "check_in"})

    assert out["delivered"] is True
    assert hb_actions.EA_DELIVERED_EVENT in emitted
    block = _block(stub_task[0])
    assert block["tracked_follow_ups"]["available"] is False
    assert block["tracked_follow_ups"]["error"]


@needs_pg
async def test_a_budget_deferred_contact_makes_zero_outside_calls(monkeypatch):
    """Same rule as the calendar: nothing delivered, nothing spent — including
    no read of the tracker."""
    monkeypatch.setattr(settings, "ea_contact_budget_per_day", 3)

    async def _explode(*a, **kw):
        raise AssertionError("nothing outside is read for a deferred contact")

    monkeypatch.setattr(jira, "search_issues", _explode)
    monkeypatch.setattr(calendar_facade, "list_events", _explode)

    async def spent(kind, actor, since):
        return 3

    monkeypatch.setattr(repo, "count_events_since", spent)

    out = await hb_actions._ea_contact("sched_fu_broke", {"kind": "digest"})
    assert out["delivered"] is False


# --- the rule the read replaced ----------------------------------------------


def test_the_charter_routes_follow_ups_away_from_the_graph():
    """A follow-up is state, and the graph cannot close anything. The charter
    used to tell the EA to record commitments, open questions — and, in
    practice, its own run state — as private episodes."""
    charter = ea_profile.CHARTER
    assert "TRACK FOLLOW-UPS AS THEIR OWN EPISODES" not in charter
    assert "CLOSING episode" not in charter
    assert "NEVER IN THE GRAPH" in charter
    assert f"labelled `{ea_profile.FOLLOW_UP_LABEL}`" in charter
    assert "YOUR OWN RUN STATE IS ALREADY KEPT" in charter
    assert "not a graph episode, not a task, not an issue" in charter


def test_the_episode_pack_refuses_process_state_for_every_agent():
    cap = next(c for c in packs.PACKS["graph-propose"].capabilities
               if c.name == "graph.add_episode")
    assert "PROCESS STATE IS NEVER AN EPISODE, in any scope" in cap.notes


async def test_the_curator_brief_names_no_change_as_a_valid_outcome(monkeypatch):
    """An empty extraction of a text with no durable fact in it is correct. The
    brief used to offer the curator only one outcome — make the graph say what
    the text says — so it built entities for a run-state note."""
    from central_command.gateway import graph_auditor

    async def body(row):
        return "The next resumed run should continue the tour."

    monkeypatch.setattr(graph_auditor, "_approved_episode_body", body)
    brief = await graph_auditor.remediation_instructions(
        {"id": "ver_1", "group_id": "central_command_ea", "scope": "private",
         "delta": {}}, "Should something have been created from this?")
    assert "NO CHANGE IS A VALID OUTCOME" in brief
    assert "propose nothing" in brief
