"""The coach is a real rostered agent (stage 5a, Decision 1).

Before this, coaching ran on `Agent(name=f"{agent_id}-coach")` — ephemeral,
unrostered, with a two-sentence hardcoded prompt: no roster row, no governed
charter, and no way to coach it. That was an undocumented deviation from the
uniform-management rule sitting in the tree. Hiring it converts the deviation
into a governed team member, and is what makes "work with the coach" (5c) and
"thumbs-down goes somewhere" (5d) name something concrete.
"""

from __future__ import annotations

import uuid

import pytest

from central_command.config import settings
from central_command.db import repo
from tests.conftest import needs_pg

# --- no database needed -------------------------------------------------------


def test_the_coach_is_in_the_founding_seed_with_a_builtin_charter():
    from central_command.runtime.agent import builtin_charter
    from central_command.runtime.roster import SEED

    coach = next((a for a in SEED if a.id == "coach"), None)
    assert coach is not None, "coach missing from the founding SEED"
    assert coach.role.strip()
    assert coach.deviations.strip(), (
        "coach is not consultable — uniform management requires a recorded reason"
    )
    assert builtin_charter("coach").strip()


def test_the_coach_charter_writes_no_capability_list():
    """Capability lists are GENERATED from grants (D25). A hand-written one in
    charter text is exactly the drift the 2026-07-22 incident cost us."""
    from central_command.runtime.coach_profile import CHARTER

    assert "capability = '" not in CHARTER
    assert "charter.update" not in CHARTER


def test_the_coach_is_seeded_in_schema_sql():
    """A fresh database must reproduce the roster — and the founding INSERT
    must not seed `role`, or the guarded UPDATE below it silently no-ops (the
    2026-07-26 clean-database failure)."""
    from pathlib import Path

    schema = (Path(__file__).parents[1] / "central_command" / "db" / "schema.sql").read_text(encoding="utf-8")
    assert "('coach'," in schema
    assert "where id = 'coach' and role = ''" in schema
    for pack in ("record-read", "charter-propose"):
        assert f"('coach', '{pack}'" in schema or f"('coach',    '{pack}'" in schema, (
            f"coach is not granted {pack} in schema.sql"
        )


# --- database -----------------------------------------------------------------

pg = needs_pg


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)


@pg
async def test_the_live_roster_holds_the_coach_with_its_grants():
    from central_command.runtime.packs import granted_packs
    from central_command.runtime.roster import roster_ids

    assert "coach" in await roster_ids()
    assert set(await granted_packs("coach")) >= {"record-read", "charter-propose"}


@pg
async def test_the_coachs_governed_charter_is_its_system_prompt():
    """Decision 5, made executable. If a coached charter cannot change how the
    coach runs, hiring it bought nothing but a row in a table."""
    from central_command.runtime.run import build_coach_agent

    marker = "COACH-MARKER-" + uuid.uuid4().hex[:8]
    await repo.save_charter_version(
        "coach", f"You are the team's coach.\n\n{marker}", "prompt test",
        created_by="operator",
    )
    agent = await build_coach_agent(await _coach_loadout())

    prompt = "\n".join(agent._system_prompts)
    assert marker in prompt, "the governed charter is not the coach's prompt"
    # ...and the generated capability section rode along, proving the grants
    # were assembled rather than a bare string being passed through.
    assert "GRANTED CAPABILITIES" in prompt
    assert "charter.update" in prompt
    # ...and no other agent's charter leaked in via build_charter's fallback.
    assert "You are an inbox-triage agent" not in prompt


async def _coach_loadout():
    from central_command.runtime.run import coach_loadout

    return await coach_loadout()


@pg
async def test_a_coach_run_parks_the_proposal_under_the_coach():
    """The proposal row identifies the agent that GENERATED it. The target is
    a property of the action, not of the proposal — which is what makes the
    executor's drafter/target split mean anything."""
    from central_command.runtime.run import run_coach
    from central_command.runtime.spike_model import make_coach_model

    target = "coach-target-" + uuid.uuid4().hex[:6]
    await repo.upsert_agent(target, target, "demo", "c")
    await repo.save_charter_version(target, "CHARTER for a coaching test.",
                                    "seed", created_by="operator")
    out = await run_coach(
        target, model=make_coach_model(), note="Check for an existing link first.",
        note_event_id=(await _note_event(target))["id"],
    )

    assert out["deferred"] is True
    row = await repo.load_proposal(out["proposal_id"])
    assert row["agent_id"] == "coach", "the drafter owns the proposal row"
    assert row["actions"][0]["arguments"]["agent_id"] == target, (
        "the target belongs in the action arguments"
    )
    assert out["target"] == target


@pg
async def test_a_coach_ask_carries_the_task_id_to_the_operator_item():
    """The coach's ask path must thread task_id into `park_question`, or the
    answered question's `_land_task_question_outcome` no-ops on a NULL task_id
    and the task is orphaned in REVIEW forever (task_e1b2d1061513, 2026-08-01)."""
    from central_command.runtime.run import run_coach
    from central_command.runtime.spike_model import make_deferred_tool_model

    target = "coach-ask-" + uuid.uuid4().hex[:6]
    await repo.upsert_agent(target, target, "demo", "c")
    await repo.save_charter_version(target, "CHARTER for a coaching test.",
                                    "seed", created_by="operator")
    task_id = "task_ca_" + uuid.uuid4().hex[:8]
    await repo.create_task(task_id, "Coach " + target, "coach it", "coach")

    out = await run_coach(
        target, model=make_deferred_tool_model("ask_operator"),
        note="Is the charter adequate?",
        note_event_id=(await _note_event(target))["id"],
        task_id=task_id,
    )

    assert out.get("asked") is True
    item = await repo.get_operator_item(out["item_id"])
    assert item["task_id"] == task_id, (
        "the coach ask dropped task_id — answering it can never resolve the task"
    )


async def _note_event(target: str) -> dict:
    from central_command import events

    return await events.emit(
        "agent.coach_requested", ref_id=target,
        payload={"signal_ids": [], "note": "Check for an existing link first."},
        actor="operator",
    )
