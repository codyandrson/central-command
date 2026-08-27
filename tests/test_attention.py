"""The attention budget (teaming doctrine, Decision 4) and its enforcement.

Two things are under test and they are different:

1. `runtime/attention.py` — the RULE. Whose budget is finite, what counts as
   spend, where the day starts.
2. `questions._open_discussion` — the MECHANISM. A charter sentence asking an
   agent to be considerate is guidance with nothing behind it; these assert
   that an agent over budget cannot open a lane no matter what it wants, and —
   just as load-bearing — that every OTHER agent is untouched.

No frontend test accompanies this, and that is not an omission: the deferral
changes no wire shape at all. An opened lane is announced exactly as before
(`openedBy == "agent"`, covered by tests/test_agent_initiated.py); a deferred
one simply never becomes a session, so there is nothing new for the cockpit to
render.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from central_command import events
from central_command.config import settings
from central_command.db import repo
from central_command.runtime import attention, questions
from tests.conftest import needs_pg

# --- the rule (no database) ----------------------------------------------------


def test_only_the_ea_has_a_finite_budget(monkeypatch):
    """0 means UNLIMITED, and every other agent gets 0. An ordinary agent opens
    a discussion because a task it was GIVEN is ambiguous — throttling that
    would degrade requested work into a guess."""
    monkeypatch.setattr(settings, "ea_contact_budget_per_day", 3)
    assert attention.budget_for("ea") == 3
    for other in ("jira-expert", "inbox-triage", "orchestrator", "coach", None):
        assert attention.budget_for(other) == 0


def test_the_budget_is_a_dial(monkeypatch):
    monkeypatch.setattr(settings, "ea_contact_budget_per_day", 7)
    assert attention.budget_for("ea") == 7
    monkeypatch.setattr(settings, "ea_contact_budget_per_day", 0)
    assert attention.budget_for("ea") == 0  # turned all the way up = unlimited


def test_the_day_starts_at_LOCAL_midnight():
    """Not UTC's. A budget rolling over at 17:00 local would hand the EA a
    second morning report every evening."""
    midnight = attention.local_midnight(datetime(2026, 7, 30, 15, 4, tzinfo=timezone.utc))
    assert (midnight.hour, midnight.minute, midnight.second) == (0, 0, 0)
    assert midnight.tzinfo is not None
    assert midnight <= datetime(2026, 7, 30, 15, 4, tzinfo=timezone.utc)


@needs_pg
async def test_may_open_lane_at_N_minus_1_N_and_N_plus_1(monkeypatch):
    """The boundary itself. `used < budget` — so the Nth contact of the day is
    allowed and the N+1th is not."""
    monkeypatch.setattr(settings, "ea_contact_budget_per_day", 3)
    seen = {"n": 0}

    async def fake_count(kind, actor, since):
        assert kind == attention.CONTACT_EVENT and actor == "agent:ea"
        return seen["n"]

    monkeypatch.setattr(repo, "count_events_since", fake_count)

    seen["n"] = 2
    assert await attention.may_open_lane("ea") == (True, 2, 3)
    seen["n"] = 3
    assert await attention.may_open_lane("ea") == (False, 3, 3)
    seen["n"] = 4
    assert await attention.may_open_lane("ea") == (False, 4, 3)


async def test_an_unlimited_agent_costs_no_query(monkeypatch):
    """The common case must not pay a database round trip per question."""
    async def explode(*a, **k):
        raise AssertionError("an unlimited agent must not be counted")

    monkeypatch.setattr(repo, "count_events_since", explode)
    assert await attention.may_open_lane("jira-expert") == (True, 0, 0)


# --- the counter (real Postgres) ----------------------------------------------


@needs_pg
async def test_count_events_since_scopes_by_kind_actor_and_time():
    """Counted in SQL, not filtered in Python over a capped read — a capped
    read would under-count exactly when the budget is spent, failing toward
    MORE unsolicited contact. Asserted on rows this test created only."""
    actor = "agent:attentiontest"
    before = datetime.now(timezone.utc)
    await events.emit(attention.CONTACT_EVENT, ref_id="i1", payload={}, actor=actor)
    await events.emit(attention.CONTACT_EVENT, ref_id="i2", payload={}, actor=actor)
    await events.emit(attention.CONTACT_EVENT, ref_id="i3", payload={},
                      actor="agent:someone-else")
    await events.emit("agent.asked", ref_id="i4", payload={}, actor=actor)

    assert await repo.count_events_since(attention.CONTACT_EVENT, actor, before) == 2
    # A window that starts after them sees none — the time bound is real.
    later = datetime.now(timezone.utc) + timedelta(seconds=1)
    assert await repo.count_events_since(attention.CONTACT_EVENT, actor, later) == 0


# --- the mechanism ------------------------------------------------------------


class _Call:
    """A deferred `ask_operator` call, shaped as `call_args` reads it."""

    def __init__(self, question: str, needs_discussion: bool = True):
        self.tool_name = "ask_operator"
        self.tool_call_id = "call_attn"
        self.args = {"question": question, "needs_discussion": needs_discussion,
                     "why": "because"}


class _Result:
    def all_messages(self):
        return []


@pytest.fixture()
async def parked(monkeypatch):
    """Stub only the transcript dump: this is a test of the GATE, and building
    a real pydantic-ai result would prove nothing extra about it. Everything
    else — the session row, the operator item, the link — is real."""
    monkeypatch.setattr(questions, "dump_run_state", lambda result, tcid: {"messages": []})
    yield


async def _ask(session_id: str, agent_id: str, question: str = "Which first?") -> dict:
    """Park a tier-3 question from a REAL session row (operator_item carries a
    foreign key to it)."""
    from central_command.runtime import ea

    if agent_id == "ea":
        await ea.ensure_registered()
    await repo.create_running_session(session_id, agent_id)
    return await questions.park_question(
        session_id=session_id, agent_id=agent_id, result=_Result(),
        call=_Call(question),
    )


@needs_pg
async def test_the_ea_over_budget_gets_no_lane_but_keeps_an_answerable_item(
    monkeypatch, parked
):
    monkeypatch.setattr(settings, "ea_contact_budget_per_day", 3)

    async def spent(kind, actor, since):
        return 3

    monkeypatch.setattr(repo, "count_events_since", spent)
    emitted: list[str] = []
    real_emit = events.emit

    async def spy(kind, ref_id=None, payload=None, actor=None):
        emitted.append(kind)
        return await real_emit(kind, ref_id=ref_id, payload=payload, actor=actor)

    monkeypatch.setattr(events, "emit", spy)

    out = await _ask("sess_attn_over", "ea", "Which proposal first?")

    assert "discussion_session_id" not in out, "a lane was opened over budget"
    assert out["contact_deferred"] is True
    # The ask DEGRADES; it is never dropped. The item is in the queue and
    # answering it resumes the run exactly as a tier-2 ask would.
    assert out["tier"] == "question"
    item = await repo.get_operator_item(out["item_id"])
    assert item["status"] == "OPEN" and item["discussion_session_id"] is None
    assert "agent.contact_deferred" in emitted
    # `agent.asked` still records what the agent ASKED FOR — the coachable fact.
    assert "agent.asked" in emitted


@needs_pg
async def test_the_ea_under_budget_still_gets_its_lane(monkeypatch, parked):
    monkeypatch.setattr(settings, "ea_contact_budget_per_day", 3)

    async def unspent(kind, actor, since):
        return 0

    monkeypatch.setattr(repo, "count_events_since", unspent)
    out = await _ask("sess_attn_under", "ea", "Which proposal first?")
    assert out["tier"] == "discussion"
    assert out["discussion_session_id"]
    item = await repo.get_operator_item(out["item_id"])
    assert item["discussion_session_id"] == out["discussion_session_id"]


@needs_pg
async def test_a_non_ea_agent_is_never_throttled(monkeypatch, parked):
    """The regression that matters most: the budget must not leak onto agents
    whose asks are the operator's own work coming back to them."""
    monkeypatch.setattr(settings, "ea_contact_budget_per_day", 1)

    async def explode(*a, **k):
        raise AssertionError("a non-EA agent must not be budget-counted")

    monkeypatch.setattr(repo, "count_events_since", explode)
    for i in range(3):
        out = await _ask(f"sess_attn_other_{i}", "jira-expert", "Which project key?")
        assert out["tier"] == "discussion" and out["discussion_session_id"]
