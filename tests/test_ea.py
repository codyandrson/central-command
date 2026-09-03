"""The executive assistant — the hire, its activity read, and the attention loop
walked end to end.

The e2e below is the acceptance test the plan asked for: a scheduled firing
becomes a task, the task opens a conversation lane seeded with the digest, the
operator replies, the agent concludes, the paused task resumes, and the work-log
proposal parks behind the gate. Every step is the SHARED machinery — the
heartbeat's task path, `questions.park_question`, `converse`, `resume_with_answer`,
`park_proposal` — which is the point: the EA got no private plumbing.

NO FRONTEND CHANGE ACCOMPANIES ANY OF THIS, and that is a finding rather than an
omission. The lane an agent opens is already announced to the cockpit as
agent-opened (`openedBy == "agent"`, `blockedTaskTitle`), which is the toast and
the "asked you" chip — see tests/test_agent_initiated.py, whose wire-shape
assertions cover this delivery unchanged. A dedicated digest panel would be the
log browser STATUS.md already rejected. The one assertion worth repeating here
is that the EA's lane really does come out of `_sessions_list` marked as the
agent's, so the delivery is not merely assumed.
"""

from __future__ import annotations

import asyncio

import pytest

from central_command import events
from central_command.config import settings
from central_command.db import repo
from central_command.runtime import ea
from central_command.runtime.ea_profile import AGENT_ID
from tests.conftest import needs_pg

pytestmark = needs_pg


@pytest.fixture(autouse=True)
async def demo(monkeypatch):
    # Pin demo mode anywhere a model is reachable — with a real key in .env the
    # "offline" suite would otherwise call Claude for real and spend money.
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "executor_mode", "dry_run")
    monkeypatch.setattr(settings, "dispatch_approval_limit", 10_000)
    monkeypatch.setattr(settings, "auditor_enabled", False)
    # Unlimited (0) by default, as tests/test_heartbeat.py's ea_stubs does: the
    # throttle has its own tests (tests/test_attention.py) and must not silently
    # decide these. A finite number — even a generous one — makes them pass or
    # fail on how much unrelated EA traffic the shared test database saw today,
    # which is exactly how this file went red on 2026-08-01 (66 lanes > 50).
    monkeypatch.setattr(settings, "ea_contact_budget_per_day", 0)
    yield


# --- the hire ------------------------------------------------------------------


async def test_the_ea_is_hired_as_a_full_roster_member():
    await ea.ensure_registered()
    first = await repo.get_agent(AGENT_ID)
    await ea.ensure_registered()
    again = await repo.get_agent(AGENT_ID)

    assert first is not None and again is not None
    assert first["created_at"] == again["created_at"], "the second call re-hired"
    # Roster membership IS a non-empty role column.
    assert first["role"].strip() and first["status"] == "ACTIVE"
    # TASKABLE, unlike the steward: the heartbeat drives it through
    # `create_and_run_task`, which refuses a non-taskable agent.
    assert first["taskable"] is True
    assert first["conversational"] is True
    # Consultable since 2026-08-05 (operator decision: whole roster, auditor
    # excepted); the decision is recorded in the deviations text.
    assert first["consultable"] is True
    assert first["deviations"].strip()

    grants = sorted(g["pack"] for g in await repo.list_grants(AGENT_ID)
                    if g["revoked_at"] is None)
    # calendar-propose granted 2026-08-06 (EA widening slice A): the EA can
    # PROPOSE a gated calendar create/update/delete — every one of them still
    # waits on the operator, and the write itself is Executor-only.
    # loe-read granted 2026-08-18 (goals drive work): goal-AWARE, never
    # goal-driving.
    # task-propose granted 2026-08-18, same day, by operator decision
    # revisiting that line: gates are review, not a ceiling — every proposed
    # task still waits on the operator, and usage is steered by coaching (or
    # revocation). Goal->work origination from check-in evidence stays with
    # the accountability agent and its citation duty.
    # mail-read granted 2026-09-02 (declared gap): a digest of dismissed mail
    # is re-derived from the record, never from memory.
    assert grants == ["activity-read", "ask-operator", "calendar-propose",
                      "calendar-read", "consult", "graph-propose", "graph-read",
                      "loe-read", "mail-read", "record-read", "task-propose"]
    assert "jira-propose" not in grants

    current = await repo.current_charter(AGENT_ID)
    assert current and current["content"].strip()


async def test_the_ea_is_taskable_so_the_heartbeat_can_drive_it():
    from central_command.runtime.roster import taskable_agent_ids

    await ea.ensure_registered()
    assert AGENT_ID in await taskable_agent_ids()


async def test_the_charter_writes_no_capability_list_of_its_own():
    """Capability lists are GENERATED from grants (D25); a hand-written one can
    drift from what the agent holds, and did once."""
    from central_command.runtime.agent import build_charter
    from central_command.runtime.ea_profile import CHARTER

    assert "GRANTED CAPABILITIES" not in CHARTER
    assert "capability = '" not in CHARTER
    assert "arguments  =" not in CHARTER

    await ea.ensure_registered()
    grants = [g["pack"] for g in await repo.list_grants(AGENT_ID)
              if g["revoked_at"] is None]
    assembled = build_charter(CHARTER, grants)
    assert assembled.count("YOUR GRANTED CAPABILITIES") == 1
    assert "capability = 'graph.add_episode'" in assembled


async def test_the_charter_carries_the_attention_doctrine():
    """The doctrine has to be IN the standing charter, not in a brief: a brief
    is one run's context, and this is what every later run must inherit —
    including the conclude-before-propose ordering, which no mechanism
    enforces (see the seam test below)."""
    from central_command.runtime.ea_profile import CHARTER

    lowered = CHARTER.lower()
    assert "budget" in lowered and "enforce" in lowered
    assert "digest" in lowered
    assert "conclude_discussion" in CHARTER and "propose" in lowered
    assert "```json" in CHARTER or "json" in lowered  # the data block is ground truth


async def test_the_ea_is_not_seeded_in_the_schema():
    """A fresh database reproduces it through the HIRE path, never by touching
    schema.sql's founding-roster seeds and their `role = ''` guards."""
    from pathlib import Path

    schema = (Path(__file__).parents[1] / "central_command" / "db" / "schema.sql").read_text(encoding="utf-8")
    seeding = [
        line for line in schema.splitlines()
        if "'ea'" in line and not line.lstrip().startswith("--")
    ]
    assert seeding == [], f"the EA is seeded in schema.sql: {seeding}"


# --- the activity read ---------------------------------------------------------


class _Ctx:
    deps = None


async def test_read_team_activity_windows_and_names_its_source():
    from central_command.runtime import tools

    mine = await events.emit(
        "gap.declared", ref_id="eatool_1",
        payload={"kind": "missing_tool", "subject": "eatool",
                 "need": "a widget only this test wants"},
        actor="agent:eatool",
    )
    out = await tools.read_team_activity(_Ctx(), since_hours=24)

    assert "TEAM ACTIVITY over the last 24h" in out
    assert f"event:{mine['id']}" in out, (
        "an activity read that does not name the event id cannot be followed up"
    )
    assert "a widget only this test wants" in out
    # Current-state depths ride along, labelled as such.
    assert "WAITING ON THE OPERATOR RIGHT NOW" in out
    assert "proposals_awaiting_decision" in out


async def test_read_team_activity_excludes_kinds_outside_the_fixed_list():
    """The agent chooses the WINDOW, never the query. An event kind outside the
    fixed vocabulary is invisible however interesting it looks."""
    from central_command.runtime import tools

    off_list = await events.emit(
        "session.step", ref_id="eatool_2", payload={"note": "not activity"},
        actor="agent:eatool",
    )
    out = await tools.read_team_activity(_Ctx(), since_hours=24)
    assert f"event:{off_list['id']}" not in out
    assert "session.step" not in out


async def test_an_empty_window_says_so_rather_than_reaching_further_back():
    """'Nothing happened' is a complete answer, and it must not read as an
    outage — a report built on a silent failure is worse than no report."""
    from central_command.runtime import tools

    async def nothing(since):
        return []

    from central_command.reports import ea_report

    real = ea_report.snapshot

    async def empty_snapshot(since):
        snap = await real(since)
        for entry in snap["activity"].values():
            entry.update(count=0, examples=[], truncated=0)
        return snap

    ea_report.snapshot, saved = empty_snapshot, real
    try:
        out = await tools.read_team_activity(_Ctx(), since_hours=1)
    finally:
        ea_report.snapshot = saved
    assert "nothing at all in this window" in out
    assert "unavailable" not in out
    del nothing


# --- the attention loop, end to end -------------------------------------------


async def test_fire_to_lane_to_conclude_to_resume_to_gated_proposal(monkeypatch):
    """The whole slice, on demo models, through the shared machinery only."""
    from central_command.api import nerve_gateway
    from central_command.heartbeat.engine import fire
    from central_command.runtime import converse
    from central_command.runtime.spike_model import make_ea_conclude_model

    await ea.ensure_registered()
    since_id = await repo.latest_event_id()

    # 1. The scheduled firing. THE task path, with the deterministic block.
    out = await fire({"id": "ea-digest", "action_kind": "ea.contact",
                      "action_params": {"kind": "digest"}})
    assert out["ok"], out
    result = out["result"]
    assert result["delivered"] is True
    task_id = result["task_id"]

    # 2. It opened a LANE, not just a queued question — tier 3.
    item_id = result["item_id"]
    item = await repo.get_operator_item(item_id)
    assert item["status"] == "OPEN"
    lane_id = item["discussion_session_id"]
    assert lane_id, "the digest did not open a conversation"
    # Seeded with the digest itself: a chat that opens empty makes the operator
    # hunt for what they were asked.
    seeded = await repo.get_session_run_state(lane_id)
    assert "Which of the proposals awaiting your decision" in str(seeded)
    # The paused task is parked in REVIEW, blocked on the question.
    assert (await repo.get_task(task_id))["status"] == "REVIEW"

    # 3. The cockpit sees it as AGENT-OPENED — the whole delivery mechanism,
    # and the reason no frontend change was needed.
    rows = (await nerve_gateway._sessions_list({}))["sessions"]
    lane_row = next(r for r in rows if r["key"].endswith(lane_id))
    assert lane_row["openedBy"] == "agent"

    # 4. The operator answers in the lane; the agent concludes.
    turn = await converse.conversation_turn(
        lane_id, "Chase the oldest one first, then the blocked task.",
        model=make_ea_conclude_model(),
    )
    assert turn.get("concluded") is True
    assert turn["item_id"] == item_id
    # The summary is re-checked against what the operator actually said.
    assert turn["claim_matches_source"] is True

    # 5. Concluding resumes the paused task IN THE BACKGROUND (the operator's
    # chat must not sit waiting on a full agent turn), so wait for the record.
    # Scoped to THIS run's proposals — the dev database is shared, and an EA
    # proposal left AWAITING_HUMAN by an earlier run would satisfy a global
    # read instantly and prove nothing.
    for _ in range(200):
        await asyncio.sleep(0.05)
        mine = [p for p in await repo.list_proposals("AWAITING_HUMAN")
                if p["agent_id"] == AGENT_ID and p["session_id"] == result["session_id"]]
        if mine:
            break
    else:  # pragma: no cover — a failure here is a real hang, not flake
        pytest.fail("the concluded discussion never resumed its task")

    # 6. The resumed run parked its work-log proposal behind the gate.
    proposal = await repo.load_proposal(mine[0]["id"])
    assert proposal["agent_id"] == AGENT_ID
    assert [a["capability"] for a in proposal["actions"]] == ["graph.add_episode"]
    assert proposal["status"] == "AWAITING_HUMAN", "an EA proposal must still gate"
    # The task it blocked is back in REVIEW, now on the proposal.
    assert (await repo.get_task(task_id))["status"] == "REVIEW"

    # 7. The record tells the whole story, in order. Read per kind rather than
    # as one window: `list_events` caps at 200 rows, and the shared database
    # makes a single window a lottery.
    seen: dict[str, int] = {}
    for kind in ("heartbeat.fired", "task.created", "agent.asked",
                 "agent.discussion_opened", "ea.contact_delivered",
                 "agent.discussion_concluded", "proposal.created"):
        rows = await repo.list_events(since_id=since_id, kind=kind)
        assert rows, f"{kind} missing from the record"
        seen[kind] = rows[0]["id"]
    assert seen["agent.discussion_opened"] < seen["agent.discussion_concluded"]
    assert seen["agent.asked"] < seen["ea.contact_delivered"], (
        "the delivery anchor must be stamped only once the contact really landed"
    )


async def test_proposing_before_concluding_leaves_a_recoverable_lane(monkeypatch):
    """CONCLUDE BEFORE PROPOSE is charter doctrine with NO mechanism behind it,
    so this test documents the seam rather than asserting a guard exists.

    The reverse order silently loses the conclusion: a proposal made inside a
    conversation parks the session AWAITING_HUMAN, and when it resolves
    `gateway._continue_if_conversation` rests the session at AWAITING_OPERATOR
    carrying the post-decision transcript. A `conclude_discussion` deferred in
    that same turn is NOT re-driven by that path — the deferral is dropped — so
    the lane stays open and the paused task stays blocked.

    What this asserts is the RECOVERY property that makes the doctrine safe to
    leave unenforced: the lane is still a live conversation, the operator_item
    is still OPEN and still linked, so one more turn (or the discussion-sweep's
    S2 nudge) concludes it. Nothing is lost; only time is.
    """
    from central_command.heartbeat.engine import fire
    from central_command.runtime import converse
    from central_command.runtime.spike_model import (
        make_ea_conclude_model,
        make_ea_episode_model,
    )

    await ea.ensure_registered()
    out = await fire({"id": "ea-checkin", "action_kind": "ea.contact",
                      "action_params": {"kind": "check_in"}})
    item_id = out["result"]["item_id"]
    lane_id = (await repo.get_operator_item(item_id))["discussion_session_id"]

    # WRONG ORDER: the agent proposes inside the lane instead of concluding.
    turn = await converse.conversation_turn(
        lane_id, "Nothing urgent; log what we agreed.",
        model=make_ea_episode_model(),
    )
    assert turn.get("proposed") is True
    assert turn["awaiting"] == "approval"
    # The conclusion never happened: the item is still OPEN and still linked,
    # and the task it blocks is still parked.
    still = await repo.get_operator_item(item_id)
    assert still["status"] == "OPEN"
    assert still["discussion_session_id"] == lane_id

    # RECOVERY: reject the proposal so the conversation resumes, then conclude.
    # The redraft model answers in TEXT rather than reaching for
    # `conclude_discussion`, because `gateway.reject_with_feedback` hands any
    # deferral in the redraft straight to `proposal_from_call` — the
    # not-every-deferral-is-a-proposal bug, still live on that path. Out of
    # scope here; recorded rather than worked around silently.
    from pydantic_ai.messages import ModelResponse, TextPart
    from pydantic_ai.models.function import FunctionModel

    from central_command.gateway import gateway

    await gateway.reject_with_feedback(
        lane_id, turn["proposal_id"],
        "Conclude the discussion first — the work log goes on the resumed task.",
        model=FunctionModel(
            lambda messages, info: ModelResponse(parts=[TextPart(content="Understood.")])
        ),
    )
    session = await repo.get_session(lane_id)
    assert converse.why_not_sendable(session) is None, (
        "the lane must remain writable, or the doctrine's failure mode would "
        "be unrecoverable rather than merely slow"
    )
    again = await converse.conversation_turn(
        lane_id, "Chase the oldest one first.", model=make_ea_conclude_model(),
    )
    assert again.get("concluded") is True
