"""The EA-hosted team tour (spec: docs/superpowers/specs/2026-08-23-ea-hosted-
team-tour-design.md).

Three things are worth pinning and nothing else is new machinery:

1. `onboarding_tour` is a registered `ea.contact` kind — the same registry
   parity the other four kinds have, because an unregistered kind cannot be
   scheduled OR fired.
2. ONE intro task, walked in demo mode: the lane opens and the episode
   proposal parks through `park_proposal`. That is the whole per-agent shape
   the tour repeats.
3. THE CAPABILITY AUDIT, as a guard. The spec's "guidance-needs-a-mechanism"
   clause says never ship a prompt promising a proposal an agent cannot make,
   so the brief's promise is computed from the live grants — and this file
   pins the answer that computation gives TODAY. A pack change that flips one
   of these is not a broken test; it is the finding this guard exists to
   surface, and re-deciding the row below is the work.
"""

from __future__ import annotations

import asyncio

import pytest

from central_command.config import settings
from central_command.db import repo
from central_command.heartbeat import actions as hb_actions
from central_command.runtime import ea, tour
from tests.conftest import aresolve, needs_pg

pytestmark = needs_pg


@pytest.fixture(autouse=True)
async def demo(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "executor_mode", "dry_run")
    monkeypatch.setattr(settings, "auditor_enabled", False)
    # 0 = unlimited, as tests/test_ea.py does: the throttle has its own tests.
    monkeypatch.setattr(settings, "ea_contact_budget_per_day", 0)
    yield


# --- 1. registration parity ----------------------------------------------------


def test_onboarding_tour_is_a_registered_contact_kind():
    spec = hb_actions.ACTIONS["ea.contact"]
    assert "onboarding_tour" in hb_actions.EA_CONTACT_KINDS
    # The registry's `choices` IS the create-time gate — a kind the tuple knows
    # but the spec does not cannot be scheduled.
    assert spec.choices["kind"] == hb_actions.EA_CONTACT_KINDS
    assert "onboarding_tour" in spec.params["kind"]
    hb_actions.validate_action("ea.contact", {"kind": "onboarding_tour"})
    with pytest.raises(ValueError):
        hb_actions.validate_action("ea.contact", {"kind": "onboarding_tourz"})


def test_the_tour_kind_takes_no_calendar_day():
    """The four windowed contacts read a calendar day; the tour is not a report
    about a window and must not acquire one by copy-paste."""
    assert "onboarding_tour" not in hb_actions._CALENDAR_DAY
    assert "onboarding_tour" not in hb_actions._EA_FRAMING


# --- 2. the capability + reachability audit, as a guard ------------------------

# What the live grants and the live roster say TODAY (2026-08-23). Read the
# module docstring before "fixing" a failure here.
_AUDIT = {
    #                     can propose an episode, accepts a direct task
    "ea":                 (True,  True),
    "inbox-triage":       (True,  False),
    "jira-expert":        (True,  True),
    "knowledge-steward":  (True,  False),
    "coach":              (False, False),
}


async def _hire_the_core_five() -> None:
    """The two `ensure_hired` members of the arc are not schema seeds — a fresh
    test database has neither until something registers them."""
    from central_command.runtime import steward

    await ea.ensure_registered()
    await steward.ensure_registered()


async def test_the_core_five_capability_audit_is_what_the_prompt_assumes():
    from central_command.runtime.roster import roster

    await _hire_the_core_five()
    by_id = {a.id: a for a in await roster()}
    for agent_id, (episode, taskable) in _AUDIT.items():
        assert agent_id in by_id, f"{agent_id} is not on the roster"
        assert await tour.can_propose_episode(agent_id) is episode, agent_id
        assert by_id[agent_id].taskable is taskable, agent_id


async def test_the_intro_brief_promises_a_proposal_only_where_grants_back_it():
    """The mechanism behind the promise, not just its current answer."""
    await _hire_the_core_five()
    for entry in await tour.arc():
        promises = "propose a `graph.add_episode`" in entry["instructions"]
        assert promises is await tour.can_propose_episode(entry["agent_id"])
        if not promises:
            # The honest fallback, per the spec: summarize aloud and name the
            # carrier — never imply something was recorded.
            assert "knowledge-steward" in entry["instructions"]
            assert "cannot record it yourself" in entry["instructions"]


async def test_the_brief_routes_around_agents_a_task_would_fail_for():
    """`task.create` refuses a non-taskable agent AT EXECUTION, so proposing
    one would be approved by the operator and then fail. The brief must send
    those introductions to the chat lane instead."""
    await _hire_the_core_five()
    entries = await tour.arc()
    # The arc order is the spec's, and it is the only hardcoded roster fact.
    assert [e["agent_id"] for e in entries] == list(tour.TOUR_ARC)
    text = await tour.brief()
    for e in entries:
        assert e["agent_id"] in text
    assert "NOT TASKABLE" in text, "no arc agent is unreachable-by-task any more?"
    for e in entries:
        # An agent reached in an EXISTING chat lane must not be told to open
        # one — the delivery slot tracks the route, not just the prose.
        opens_a_lane = "ask_operator(needs_discussion=True)" in e["instructions"]
        assert opens_a_lane is e["taskable"], e["agent_id"]
    # Day-one regression: the host closed in text after the calibration
    # exchange and the whole tour ended with nine teammates unmet. The brief
    # must state, at that exact decision point, that calibration is not the
    # end of the tour.
    assert "does not end at calibration" in text
    assert "IN THE SAME TURN" in text
    # The Inbox-answer door: a resumed turn that closes in plain text kills
    # the tour identically to the day-one calibration death, so the brief
    # must state the re-ask rule that survives it.
    assert "CLOSES the lane" in text
    assert text.count("`ask_operator(needs_discussion=True)` again") >= 1


# --- 3. one intro task, walked ------------------------------------------------


async def test_an_intro_task_opens_the_lane_and_parks_its_episode(monkeypatch):
    """The per-agent shape the tour repeats, on demo models, end to end.

    jira-expert is the arc member that is BOTH taskable and episode-capable,
    so it is the one whose intro runs the full promised path.
    """
    from central_command.api import routes
    from central_command.runtime import converse
    from central_command.runtime import models as models_mod
    from central_command.runtime.spike_model import (
        make_ea_conclude_model, make_intro_model,
    )

    intro = aresolve(lambda *a, **k: make_intro_model())
    monkeypatch.setattr(routes, "resolve_model", intro)
    # The RESUME resolves its own model (`questions.resume_with_answer` imports
    # from the module at call time), so patching only the routes binding leaves
    # the resumed half running on the inbox-triage double.
    monkeypatch.setattr(models_mod, "resolve_model", intro)
    since_id = await repo.latest_event_id()

    result = await routes.create_and_run_task(
        await tour.intro_task_instructions("jira-expert"),
        "Introduce yourself", "jira-expert", actor="agent:ea",
    )

    # The lane opened, seeded with the introduction itself.
    item = await repo.get_operator_item(result["item_id"])
    lane_id = item["discussion_session_id"]
    assert item["status"] == "OPEN"
    assert lane_id, "the introduction did not open a conversation"
    assert (await repo.get_task(result["task_id"]))["status"] == "REVIEW"

    # The operator answers; the agent concludes; the task resumes in the
    # background and parks its episode.
    turn = await converse.conversation_turn(
        lane_id, "Ask me before you touch anything with a deadline on it.",
        model=make_ea_conclude_model(),
    )
    assert turn.get("concluded") is True

    for _ in range(200):
        await asyncio.sleep(0.05)
        mine = [p for p in await repo.list_proposals("AWAITING_HUMAN")
                if p["session_id"] == result["session_id"]]
        if mine:
            break
    else:  # pragma: no cover — a hang, not flake
        pytest.fail("the concluded introduction never resumed its task")

    proposal = await repo.load_proposal(mine[0]["id"])
    assert [a["capability"] for a in proposal["actions"]] == ["graph.add_episode"]
    assert proposal["status"] == "AWAITING_HUMAN", "an intro proposal must gate"
    # Through park_proposal — which is what makes the queue index complete.
    created = await repo.list_events(since_id=since_id, kind="proposal.created")
    assert any(e["ref_id"] == mine[0]["id"] for e in created)


# --- the contact itself --------------------------------------------------------


async def test_firing_the_tour_contact_hands_the_ea_its_host_brief():
    from central_command.heartbeat.engine import fire

    await ea.ensure_registered()
    out = await fire({"id": "tour-test", "action_kind": "ea.contact",
                      "action_params": {"kind": "onboarding_tour"}})
    assert out["ok"], out
    result = out["result"]
    assert result["delivered"] is True and result["kind"] == "onboarding_tour"

    task = await repo.get_task(result["task_id"])
    assert "ONBOARDING TOUR" in task["instructions"]
    assert task["agent_id"] == ea.AGENT_ID
    # It opened the tour lane, which is the whole delivery.
    assert result.get("discussion_session_id") or result.get("item_id")
