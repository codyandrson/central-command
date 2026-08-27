"""Governed-charter tests (Phase 3, M11).

The properties that matter: **exactly one CURRENT version per agent** (a SQL
partial unique index, not application code), **history is append-only** (a
revert is a NEW version carrying old content), and **the agent actually runs
on the governed charter** — the DB's CURRENT version reaches the system prompt,
with the built-in code charter as the v0 fallback.
"""

from __future__ import annotations

import json

import pytest

from central_command.config import settings
from central_command.db import repo
from central_command.runtime.run import ingest_and_propose
from central_command.runtime.spike_model import make_spike_model
from tests.conftest import needs_pg

AGENT = "inbox-triage"


pytestmark = needs_pg


@pytest.fixture(autouse=True)
async def clean(monkeypatch):
    monkeypatch.setattr(settings, "executor_mode", "dry_run")
    monkeypatch.setattr(settings, "demo_mode", True)
    await repo.upsert_agent(AGENT, "Inbox Triage", "inbox-triage", "test")
    await _wipe()
    yield
    await _wipe()


async def _wipe():
    conn = await repo._conn()
    try:
        await conn.execute("delete from guidance_version where agent_id = $1", AGENT)
    finally:
        await conn.close()


async def test_versions_are_monotonic_and_exactly_one_is_current():
    v1 = await repo.save_charter_version(AGENT, "charter one", "first")
    v2 = await repo.save_charter_version(AGENT, "charter two", "second")
    assert (v1["version"], v2["version"]) == (1, 2)

    current = await repo.current_charter(AGENT)
    assert current["version"] == 2 and current["content"] == "charter two"

    versions = await repo.list_charter_versions(AGENT)
    assert [v["version"] for v in versions] == [2, 1]
    assert [v["status"] for v in versions] == ["CURRENT", "SUPERSEDED"]


async def test_revert_is_a_new_version_not_a_status_flip():
    await repo.save_charter_version(AGENT, "the good charter", None)
    await repo.save_charter_version(AGENT, "the bad edit", None)

    old = await repo.get_charter_version(AGENT, 1)
    v3 = await repo.save_charter_version(AGENT, old["content"], "revert to v1")

    assert v3["version"] == 3
    current = await repo.current_charter(AGENT)
    assert current["content"] == "the good charter"
    # History intact: all three versions on the record.
    assert len(await repo.list_charter_versions(AGENT)) == 3


async def test_agent_runs_on_the_governed_charter():
    """The end-to-end property: the DB charter reaches the persisted run's
    system prompt (visible in the paused session's message history)."""
    marker = "GOVERNED-CHARTER-MARKER: propose via propose_action."
    await repo.save_charter_version(AGENT, marker, "test charter")

    run = await ingest_and_propose("DEMO-1 slipped to Aug 3.", model=make_spike_model())
    state = await repo.load_paused_session(run["session_id"])
    dumped = json.dumps(state["messages"])
    assert marker in dumped
    # The date injection still applies on top of the governed content.
    assert "Today's date is" in dumped


async def test_charter_get_carries_the_fields_the_config_tab_reads():
    """The cockpit hand-declares its TypeScript interface over this payload, so
    a field the API stops sending is invisible to `tsc` AND to every frontend
    test — the tab just silently renders nothing. Guard the wire shape here.

    Read by web/src/features/workspace/tabs/ConfigTab.tsx via
    /api/cc/charter/:agentId.
    """
    from central_command.api.routes import get_charter

    # v0 fallback: an agent with no governed version still renders.
    fresh = await get_charter(AGENT)
    assert fresh["current"]["version"] == 0
    assert fresh["current"]["content"] and fresh["versions"] == []

    await repo.save_charter_version(AGENT, "governed charter", "because")
    body = await get_charter(AGENT)

    current = body["current"]
    for field in ("version", "content", "rationale", "created_by", "created_at"):
        assert field in current, f"the Config tab reads current.{field}"
    assert current["version"] == 1 and current["content"] == "governed charter"

    assert len(body["versions"]) == 1
    for field in ("version", "status", "created_at"):
        assert field in body["versions"][0], f"the Config tab reads versions[].{field}"


async def test_save_charter_404s_for_an_unknown_agent():
    from fastapi import HTTPException

    from central_command.api import routes

    with pytest.raises(HTTPException) as exc:
        await routes.save_charter(
            "nonexistent-xyz", routes.CharterIn(content="hello", rationale=None)
        )
    assert exc.value.status_code == 404


async def test_without_versions_the_builtin_charter_applies():
    run = await ingest_and_propose("DEMO-1 slipped to Aug 3.", model=make_spike_model())
    state = await repo.load_paused_session(run["session_id"])
    assert "inbox-triage agent" in json.dumps(state["messages"])
