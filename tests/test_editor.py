"""The editor — hired as a template singleton (D24), following the
steward/EA/MCP-manager precedent exactly (tests/test_steward.py,
tests/test_ea.py, tests/test_mcp_manager.py).
"""

from __future__ import annotations

from central_command.db import repo
from central_command.runtime import editor
from central_command.runtime.editor_profile import AGENT_ID
from tests.conftest import needs_pg

pytestmark = needs_pg


async def test_it_is_hired_as_a_full_roster_member():
    await editor.ensure_registered()
    first = await repo.get_agent(AGENT_ID)
    await editor.ensure_registered()
    again = await repo.get_agent(AGENT_ID)

    assert first is not None and again is not None
    assert first["created_at"] == again["created_at"], "the second call re-hired"
    # Roster membership IS a non-empty role column.
    assert first["role"].strip() and first["status"] == "ACTIVE"
    assert first["taskable"] is True
    assert first["conversational"] is True
    # Consultable since hire (operator decision 2026-08-05: whole roster,
    # auditor excepted); the decision is recorded in the deviations text.
    assert first["consultable"] is True
    assert first["deviations"].strip()

    grants = sorted(g["pack"] for g in await repo.list_grants(AGENT_ID)
                    if g["revoked_at"] is None)
    # No propose packs, no consult, no web-read — a read-only critic (design
    # doc 2026-08-06).
    assert grants == ["ask-operator", "graph-read", "jira-read", "record-read"]

    current = await repo.current_charter(AGENT_ID)
    assert current and current["content"].strip()


async def test_it_is_taskable():
    from central_command.runtime.roster import taskable_agent_ids

    await editor.ensure_registered()
    assert AGENT_ID in await taskable_agent_ids()


async def test_it_is_not_seeded_in_the_schema():
    """A fresh database reproduces it through the HIRE path, never by touching
    schema.sql's founding-roster seeds and their `role = ''` guards."""
    from pathlib import Path

    schema = (Path(__file__).parents[1] / "central_command" / "db" / "schema.sql").read_text(encoding="utf-8")
    assert f"'{AGENT_ID}'" not in schema


def test_the_charter_holds_the_rubric_and_the_output_contract():
    from central_command.runtime.editor_profile import CHARTER

    lowered = CHARTER.lower()
    assert "no issues found" in lowered
    assert "never propose" in lowered or "never rewrite" in lowered
    assert "quot" in lowered  # quote/quoting the sentence being criticized


def test_the_editor_delegates_the_hire_rather_than_keeping_a_second_copy():
    """Mirrors tests/test_hiring.py's steward guard: a re-inlined
    `repo.hire_agent` call here would duplicate the race-loss handling
    `hiring.ensure_hired` exists to hold in one place."""
    from pathlib import Path

    source = Path(editor.__file__).read_text(encoding="utf-8")
    assert "ensure_hired" in source
    assert "repo.hire_agent" not in source
