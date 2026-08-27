"""The MCP manager — hired as a template singleton (D24), following the
steward/EA precedent exactly (tests/test_steward.py, tests/test_ea.py).
"""

from __future__ import annotations

from central_command.db import repo
from central_command.runtime import mcp_manager
from central_command.runtime.mcp_manager_profile import AGENT_ID
from tests.conftest import needs_pg

pytestmark = needs_pg


async def test_it_is_hired_as_a_full_roster_member():
    await mcp_manager.ensure_registered()
    first = await repo.get_agent(AGENT_ID)
    await mcp_manager.ensure_registered()
    again = await repo.get_agent(AGENT_ID)

    assert first is not None and again is not None
    assert first["created_at"] == again["created_at"], "the second call re-hired"
    # Roster membership IS a non-empty role column.
    assert first["role"].strip() and first["status"] == "ACTIVE"
    assert first["taskable"] is True
    assert first["conversational"] is True
    # Consultable since 2026-08-05 (operator decision: whole roster, auditor
    # excepted); the decision is recorded in the deviations text.
    assert first["consultable"] is True
    assert first["deviations"].strip()

    grants = sorted(g["pack"] for g in await repo.list_grants(AGENT_ID)
                    if g["revoked_at"] is None)
    assert grants == ["ask-operator", "consult", "mcp-propose", "sandbox", "web-read"]

    current = await repo.current_charter(AGENT_ID)
    assert current and current["content"].strip()


async def test_it_is_taskable():
    from central_command.runtime.roster import taskable_agent_ids

    await mcp_manager.ensure_registered()
    assert AGENT_ID in await taskable_agent_ids()


async def test_it_holds_the_default_sandbox_source_grant():
    """The design doc's default (2026-08-03 §6): an MCP-authoring agent needs
    the `servers/` tree in its sandbox to see prior art and the pipeline's own
    conventions."""
    await mcp_manager.ensure_registered()
    active = [
        r["source_path"] for r in await repo.list_sandbox_sources(AGENT_ID)
        if r["revoked_at"] is None
    ]
    assert active == ["servers"]

    # Idempotent: calling again does not duplicate or error the grant.
    await mcp_manager.ensure_registered()
    active_again = [
        r["source_path"] for r in await repo.list_sandbox_sources(AGENT_ID)
        if r["revoked_at"] is None
    ]
    assert active_again == ["servers"]


async def test_it_is_not_seeded_in_the_schema():
    """A fresh database reproduces it through the HIRE path, never by touching
    schema.sql's founding-roster seeds and their `role = ''` guards."""
    from pathlib import Path

    schema = (Path(__file__).parents[1] / "central_command" / "db" / "schema.sql").read_text(encoding="utf-8")
    assert f"'{AGENT_ID}'" not in schema


def test_the_charter_carries_the_introspect_the_real_library_lesson():
    """The echo-demo lesson (STATUS.md, 2026-08-05): a library's API can move
    between major versions, so training data is never trusted over the real
    installed version — checked live in the sandbox."""
    from central_command.runtime.mcp_manager_profile import CHARTER

    lowered = CHARTER.lower()
    assert "training data" in lowered
    assert "sandbox" in lowered
    assert "propose_mcp_remove" in CHARTER  # coupled removal is named explicitly


def test_the_manager_delegates_the_hire_rather_than_keeping_a_second_copy():
    """Mirrors tests/test_hiring.py's steward guard: a re-inlined
    `repo.hire_agent` call here would duplicate the race-loss handling
    `hiring.ensure_hired` exists to hold in one place."""
    from pathlib import Path

    source = Path(mcp_manager.__file__).read_text(encoding="utf-8")
    assert "ensure_hired" in source
    assert "repo.hire_agent" not in source
