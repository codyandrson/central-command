"""No-past-due-dates policy tests (the first real gateway policy check).

The incident it guards: real Claude resolved "Aug 10" to 2024-08-10, the quote
was faithful (so the verbatim check correctly passed), and the wrong date
survived human review. The guard is mechanical: a past date flags amber for the
reviewer and, if approved anyway, the flag rides the decision record.
"""

from __future__ import annotations

from datetime import date

import pytest

from central_command.config import settings
from central_command.db import repo
from central_command.gateway import gateway, policy
from central_command.runtime.run import ingest_and_propose
from central_command.runtime.spike_model import make_spike_model
from tests.conftest import needs_pg

TODAY = date(2026, 7, 19)


# --- the check itself (pure, no DB) ------------------------------------------


def test_past_due_date_flags_the_proposal_for_review():
    flags = policy.check_actions(
        [{"capability": "jira.set_due_date@v1",
          "arguments": {"issue_key": "TASKS-12", "due_date": "2024-08-10"}}],
        today=TODAY,
    )
    assert len(flags) == 1
    f = flags[0]
    assert f["policy"] == "no-past-due-dates"
    assert f["capability"] == "jira.set_due_date"
    assert f["value"] == "2024-08-10"
    assert "in the past" in f["problem"]


def test_today_and_future_dates_are_clean():
    actions = [
        {"capability": "jira.set_due_date",
         "arguments": {"issue_key": "T-1", "due_date": TODAY.isoformat()}},
        {"capability": "jira.update_attributes",
         "arguments": {"issue_key": "T-1", "priority": "High",
                       "due_date": "2026-12-01", "labels": []}},
    ]
    assert policy.check_actions(actions, today=TODAY) == []


def test_clearing_a_date_is_not_a_hazard():
    actions = [
        {"capability": "jira.update_attributes",
         "arguments": {"issue_key": "T-1", "priority": "Low",
                       "due_date": None, "labels": []}},
    ]
    assert policy.check_actions(actions, today=TODAY) == []


def test_malformed_date_flags_too():
    flags = policy.check_actions(
        [{"capability": "jira.set_due_date",
          "arguments": {"issue_key": "T-1", "due_date": "next Tuesday"}}],
        today=TODAY,
    )
    assert len(flags) == 1
    assert "not an ISO date" in flags[0]["problem"]


def test_dateless_actions_are_ignored():
    actions = [
        {"capability": "jira.add_comment",
         "arguments": {"issue_key": "T-1", "body": "due 1999-01-01 (prose, not a date arg)"}},
    ]
    assert policy.check_actions(actions, today=TODAY) == []
    # A dateless graph EPISODE is the one deliberate exception (2026-08-16):
    # extraction stamps it as becoming true today, so it flags for review.
    flags = policy.check_actions([
        {"capability": "graph.add_episode",
         "arguments": {"name": "n", "episode_body": "b", "source_description": "s"}},
    ], today=TODAY)
    assert [f["policy"] for f in flags] == ["episodes-carry-their-time"]


# --- wiring: review surface + decision record (needs Postgres) ---------------


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "executor_mode", "dry_run")


@needs_pg
async def test_flags_ride_the_review_surface_and_the_decision_record(monkeypatch):
    """The spike proposal's 2026-08-03 due date becomes 'past' when the clock is
    pinned to 2027 — exercising the real wiring, not a synthetic flag."""
    monkeypatch.setattr(policy, "_today", lambda: date(2027, 1, 1))
    from central_command.api import routes

    model = make_spike_model()
    run = await ingest_and_propose("Dana: DEMO-1 slipped, due Aug 3.", model=model)

    # Review time: the reviewer sees the amber flag on the proposal.
    row = await routes.get_proposal(run["proposal_id"])
    assert row["policy_flags"], "expected the past due date to flag for review"
    assert row["policy_flags"][0]["policy"] == "no-past-due-dates"

    # Approving over the flag is allowed — and recorded as exactly that.
    await gateway.approve_and_execute(run["session_id"], run["proposal_id"], model=model)
    decided = [
        e for e in await repo.list_events(ref_id=run["proposal_id"])
        if e["kind"] == "proposal.decided"
    ]
    assert decided[0]["payload"]["policy_flags"][0]["policy"] == "no-past-due-dates"

    # History never flags: once executed, review-time hazards are not recomputed.
    row = await routes.get_proposal(run["proposal_id"])
    assert "policy_flags" not in row


# --- known-capabilities-only (the charter-v2 incident) ------------------------


def test_unknown_capability_flags_the_proposal():
    flags = policy.check_actions(
        [{"capability": "jira.create_epic@v1",
          "arguments": {"project_key": "TASKS", "summary": "x"}}],
        today=TODAY,
    )
    assert len(flags) == 1
    f = flags[0]
    assert f["policy"] == "known-capabilities-only"
    assert f["capability"] == "jira.create_epic"
    assert "not in the capability registry" in f["problem"]


def test_every_registered_write_is_clean():
    from central_command.gateway.capabilities import gated_write_names

    actions = [{"capability": name, "arguments": {}} for name in sorted(gated_write_names())]
    assert policy.check_actions(actions, today=TODAY) == []


def test_capability_outside_grants_is_flagged():
    """D25: a proposal carrying a capability the proposing agent's pack
    grants don't cover flags amber — grant/charter drift made visible at
    review time. `charter.update` is NOT exempt: since stage 5a it is a
    granted pack capability (`charter-propose`, one holder: the coach), so an
    agent proposing it without that grant is exactly the drift this check
    exists to catch."""
    granted = {"jira.set_due_date", "graph.add_episode"}
    flags = policy.check_grants(
        [{"capability": "jira.add_comment@v1",
          "arguments": {"issue_key": "TASKS-12", "body": "hi"}}],
        granted,
    )
    assert len(flags) == 1
    assert flags[0]["policy"] == "within-granted-capabilities"
    assert flags[0]["capability"] == "jira.add_comment"

    assert policy.check_grants(
        [{"capability": "jira.set_due_date@v1", "arguments": {}}], granted
    ) == []
    # No exemption: an agent proposing charter.update WITHOUT the
    # charter-propose grant is flagged, same as any other ungranted write.
    flags = policy.check_grants(
        [{"capability": "charter.update", "arguments": {}}], set()
    )
    assert len(flags) == 1
    assert flags[0]["policy"] == "within-granted-capabilities"
    assert flags[0]["capability"] == "charter.update"

    # ...and a proposer that DOES hold charter-propose (i.e. its granted set
    # includes charter.update) is clean.
    assert policy.check_grants(
        [{"capability": "charter.update", "arguments": {}}], {"charter.update"}
    ) == []


def test_charter_referencing_unknown_capability_is_flagged():
    content = (
        "Supported action capabilities:\n"
        "  capability = 'jira.set_due_date'\n"
        "  capability = 'jira.delete_project'  (use for cleanup)\n"
    )
    flags = policy.check_charter_content(content)
    assert len(flags) == 1
    assert flags[0]["policy"] == "charter-references-real-capabilities"
    assert flags[0]["capability"] == "jira.delete_project"
    # A charter quoting only real capabilities is clean.
    assert policy.check_charter_content("capability = 'jira.create_issue'") == []


def test_a_dateless_episode_is_flagged_for_the_reviewer():
    """Graphiti stamps a dateless claim as becoming true at ingestion — newly
    LEARNED read as newly TRUE. The reviewer decides with that visible."""
    flags = policy.check_actions([{
        "capability": "graph.add_episode@v1",
        "arguments": {"episode_body": "Bonnie Doe is the cousin of the operator."},
    }])
    assert len(flags) == 1
    assert flags[0]["policy"] == "episodes-carry-their-time"
    assert flags[0]["argument"] == "episode_body"


def test_an_episode_stating_its_time_is_clean():
    for body in (
        "the operator worked at Initech from June 2011 until September 2022.",
        "Melody Doe has worked for Prairie Home Services since May 2023.",
        "The TASKS-12 due date moved to 2026-08-20.",
        # The pack guidance's honest-uncertainty forms count as stated time.
        "Jane is the operator's mother (long-standing).",
        "the operator owns a 1996 Volvo 850; start date unknown.",
    ):
        assert policy.check_actions([{
            "capability": "graph.add_episode",
            "arguments": {"episode_body": body},
        }]) == [], f"falsely flagged: {body!r}"


def test_non_episode_actions_never_carry_the_time_flag():
    flags = policy.check_actions([{
        "capability": "jira.add_comment",
        "arguments": {"issue_key": "T-1", "body": "no dates here at all"},
    }])
    assert flags == []
