"""The EA's follow-up read — slice B of EA widening (2026-08-06).

Mirrors tests/test_ea_calendar.py's shape: `open_loops()` is a SIBLING read
over HTTP that can fail, and the anchor property is that a graph outage
degrades the EA's contact and never cancels it (see
tests/test_ea_calendar.py's `test_a_calendar_outage_degrades_the_contact_but_never_cancels_it`
for the analogous calendar assertion; the follow-up equivalent lives here).

NO NETWORK: `graphiti.search_private_facts` is monkeypatched in every test.
"""

from __future__ import annotations

import json

import pytest

from central_command.config import settings
from central_command.db import repo
from central_command.heartbeat import actions as hb_actions
from central_command.integrations import calendar_facade, graphiti
from central_command.reports import ea_followups
from central_command.runtime.ea_profile import AGENT_ID
from tests.conftest import needs_pg


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "executor_mode", "dry_run")
    monkeypatch.setattr(settings, "dispatch_approval_limit", 10_000)
    monkeypatch.setattr(settings, "ea_contact_budget_per_day", 0)
    # No calendar token: keeps these tests about follow_ups, not calendar.
    monkeypatch.setattr(settings, "calendar_facade_token", "")


# --- the reader itself -----------------------------------------------------


async def test_open_loops_returns_a_compact_fact_list(monkeypatch):
    async def fake(agent_id, query, max_facts=8):
        assert agent_id == AGENT_ID
        return [{"fact": "Ping vendor about the SLA by Friday", "valid_at": "2026-08-05"}]

    monkeypatch.setattr(graphiti, "search_private_facts", fake)
    out = await ea_followups.open_loops()
    assert out == {
        "available": True,
        "count": 1,
        "facts": [{"fact": "Ping vendor about the SLA by Friday",
                   "valid_at": "2026-08-05", "invalid_at": None}],
    }


async def test_open_loops_is_empty_and_honest_when_there_is_nothing_open(monkeypatch):
    async def fake(agent_id, query, max_facts=8):
        return []

    monkeypatch.setattr(graphiti, "search_private_facts", fake)
    out = await ea_followups.open_loops()
    assert out == {"available": True, "count": 0, "facts": []}


async def test_open_loops_raises_on_a_transport_failure():
    """It does NOT swallow its own failure — same shape as `ea_calendar.day()`,
    which also raises and leaves degrading to the caller (heartbeat/actions.py).
    """

    async def boom(agent_id, query, max_facts=8):
        raise ConnectionError("graph down")

    import central_command.reports.ea_followups as mod

    orig = graphiti.search_private_facts
    try:
        graphiti.search_private_facts = boom
        with pytest.raises(ConnectionError):
            await mod.open_loops()
    finally:
        graphiti.search_private_facts = orig


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


@needs_pg
async def test_the_brief_carries_follow_ups_and_says_to_narrate_them(
    monkeypatch, stub_task
):
    async def fake(agent_id, query, max_facts=8):
        return [{"fact": "Waiting on the vendor's SLA answer", "valid_at": None}]

    monkeypatch.setattr(graphiti, "search_private_facts", fake)

    await hb_actions._ea_contact("sched_fu_ok", {"kind": "check_in"})

    brief = stub_task[0]
    assert "prior private episodes" in brief
    block = json.loads(brief.split("```json\n")[1].split("\n```")[0])
    assert block["follow_ups"]["available"] is True
    assert block["follow_ups"]["count"] == 1


@needs_pg
async def test_a_follow_up_outage_degrades_the_contact_but_never_cancels_it(
    monkeypatch, stub_task
):
    async def boom(agent_id, query, max_facts=8):
        raise ConnectionError("graph down")

    monkeypatch.setattr(graphiti, "search_private_facts", boom)

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
    block = json.loads(stub_task[0].split("```json\n")[1].split("\n```")[0])
    assert block["follow_ups"]["available"] is False
    assert block["follow_ups"]["error"]


@needs_pg
async def test_a_budget_deferred_contact_makes_zero_graph_calls(monkeypatch):
    """Same rule as the calendar: nothing delivered, nothing spent — including
    no read of the EA's own graph partition."""
    monkeypatch.setattr(settings, "ea_contact_budget_per_day", 3)

    async def _explode(*a, **kw):
        raise AssertionError("the graph must not be read for a deferred contact")

    monkeypatch.setattr(graphiti, "search_private_facts", _explode)
    monkeypatch.setattr(calendar_facade, "list_events", _explode)

    async def spent(kind, actor, since):
        return 3

    monkeypatch.setattr(repo, "count_events_since", spent)

    out = await hb_actions._ea_contact("sched_fu_broke", {"kind": "digest"})
    assert out["delivered"] is False
