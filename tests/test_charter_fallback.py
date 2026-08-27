"""An agent with no governed charter runs on ITS OWN built-in v0.

`build_charter` resolves a None charter to the inbox-triage default, so a
founder that never got coached (the orchestrator: no guidance_version row)
introduced itself as "an inbox-triage agent" on every surface that went
through `load_charter` — chat and non-project tasks (2026-08-18).
"""

import pytest

from central_command.runtime import agent as agent_mod


@pytest.mark.asyncio
async def test_load_charter_falls_back_to_the_agents_own_builtin(monkeypatch):
    from central_command.db import repo

    async def no_governed_version(agent_id):
        return None

    monkeypatch.setattr(repo, "current_charter", no_governed_version)
    got = await agent_mod.load_charter("orchestrator")
    assert got == agent_mod.builtin_charter("orchestrator")
    assert got != agent_mod.CHARTER


@pytest.mark.asyncio
async def test_a_hired_agent_with_no_version_still_gets_none(monkeypatch):
    from central_command.db import repo

    async def no_governed_version(agent_id):
        return None

    monkeypatch.setattr(repo, "current_charter", no_governed_version)
    assert await agent_mod.load_charter("some-hired-agent") is None
