"""The shared idempotent-hire seam (`runtime/hiring.ensure_hired`).

Extracted from the steward when the EA became the second self-hiring agent.
The steward's own tests (tests/test_steward.py) stay green unchanged and are
the extraction's real regression net; what is added here is coverage of the
part that was too subtle to hand-copy — the race-loss branch — which no test
reached while it lived inside `steward.ensure_registered`.
"""

from __future__ import annotations

import pytest

from central_command.db import repo
from central_command.runtime import hiring
from central_command.runtime.templates import STEWARD_TEMPLATE
from tests.conftest import needs_pg

AGENT = "hiringtest-a"


@pytest.fixture(autouse=True)
async def cleanup():
    yield
    try:
        conn = await repo._conn()
    except Exception:  # noqa: BLE001 — no Postgres: the DB tests skipped anyway
        return
    try:
        await conn.execute("delete from agent_grant where agent_id = $1", AGENT)
        await conn.execute("delete from guidance_version where agent_id = $1", AGENT)
        await conn.execute("delete from agent where id = $1", AGENT)
    finally:
        await conn.close()


def test_the_steward_delegates_rather_than_keeping_a_second_copy():
    """The whole point of the extraction: one copy of the race-loss handling.
    A re-inlined `repo.hire_agent` call in a profile module is the drift this
    guards — it would pass every behavioural test above and still be the
    duplication that loses the ValueError branch next time."""
    from pathlib import Path

    from central_command.runtime import steward

    source = Path(steward.__file__).read_text(encoding="utf-8")
    assert "ensure_hired" in source
    assert "repo.hire_agent" not in source, (
        "the steward re-inlined the hire — the race-loss branch now exists in "
        "two places, which is exactly what hiring.py was extracted to prevent"
    )


@needs_pg
async def test_ensure_hired_creates_a_full_roster_member_and_is_idempotent():
    await hiring.ensure_hired(
        agent_id=AGENT, name="Hiring Test", role="exercise the hire seam",
        charter="A charter, because an agent cannot be hired without one.",
        template=STEWARD_TEMPLATE, consultable=False,
        deviations="test fixture: neither taskable nor consultable",
    )
    first = await repo.get_agent(AGENT)
    await hiring.ensure_hired(
        agent_id=AGENT, name="Hiring Test", role="exercise the hire seam",
        charter="A charter, because an agent cannot be hired without one.",
        template=STEWARD_TEMPLATE, consultable=False,
        deviations="test fixture: neither taskable nor consultable",
    )
    again = await repo.get_agent(AGENT)

    assert first and again
    assert first["created_at"] == again["created_at"], "the second call re-hired"
    # Roster membership IS a non-empty role column.
    assert first["role"].strip() and first["status"] == "ACTIVE"
    # The template supplies the loadout and the lifecycle flags.
    assert first["taskable"] is STEWARD_TEMPLATE.taskable
    assert first["conversational"] is STEWARD_TEMPLATE.conversational
    assert first["template"] == STEWARD_TEMPLATE.id
    grants = {g["pack"] for g in await repo.list_grants(AGENT) if g["revoked_at"] is None}
    assert grants == set(STEWARD_TEMPLATE.default_packs)
    current = await repo.current_charter(AGENT)
    assert current and current["content"].strip()


@needs_pg
async def test_losing_the_hire_race_is_the_outcome_asked_for(monkeypatch):
    """Two processes doing the same idempotent hire: the loser sees
    `hire_agent`'s ValueError on the unique violation, and an agent that now
    EXISTS is precisely what it wanted. Simulated by having the hire land the
    row and then raise, which is what the loser observes."""
    real_hire = repo.hire_agent

    async def racing_hire(*args, **kwargs):
        await real_hire(*args, **kwargs)          # the winner's row lands
        raise ValueError("agent id already exists")  # ...then we lose

    monkeypatch.setattr(repo, "hire_agent", racing_hire)
    await hiring.ensure_hired(
        agent_id=AGENT, name="Hiring Test", role="exercise the hire seam",
        charter="A charter.", template=STEWARD_TEMPLATE,
        deviations="test fixture",
    )
    assert await repo.get_agent(AGENT) is not None


@needs_pg
async def test_a_ValueError_with_no_agent_behind_it_still_raises(monkeypatch):
    """The other half of the same branch: `hire_agent` also raises ValueError
    for an empty charter or no grants. Swallowing those would leave a caller
    believing an agent exists when nothing was created."""
    async def always_fails(*args, **kwargs):
        raise ValueError("an agent cannot be hired without capability grants")

    monkeypatch.setattr(repo, "hire_agent", always_fails)
    with pytest.raises(ValueError):
        await hiring.ensure_hired(
            agent_id=AGENT, name="Hiring Test", role="r", charter="c",
            template=STEWARD_TEMPLATE, deviations="test fixture",
        )
    assert await repo.get_agent(AGENT) is None


@needs_pg
async def test_hire_route_treats_an_empty_packs_list_like_omitted():
    """`packs: []` used to be distinguished from omitted (`is not None`), so it
    fell through to the "no capability grants" 422 instead of the template's
    defaults."""
    from central_command.api import routes

    agent_id = None
    try:
        out = await routes.hire_agent(routes.HireIn(
            name="Hiring Empty Packs Test", mission="exercise the empty-packs hire path",
            template="general", packs=[],
        ))
        agent_id = out["agent"]["id"]
        assert out["ok"] is True
        grants = {g["pack"] for g in await repo.list_grants(agent_id) if g["revoked_at"] is None}
        assert grants  # the template's default_packs, not empty
    finally:
        if agent_id:
            conn = await repo._conn()
            try:
                await conn.execute("delete from agent_grant where agent_id = $1", agent_id)
                await conn.execute("delete from guidance_version where agent_id = $1", agent_id)
                await conn.execute("delete from agent where id = $1", agent_id)
            finally:
                await conn.close()
