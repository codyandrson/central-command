"""Governed-charter tests (Phase 3, M11).

The properties that matter: **exactly one CURRENT version per agent** (a SQL
partial unique index, not application code), **history is append-only** (a
revert is a NEW version carrying old content), and **the agent actually runs
on the governed charter** — the DB's CURRENT version reaches the system prompt,
with the built-in code charter as the v0 fallback.
"""

from __future__ import annotations

import json

import uuid

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
    monkeypatch.setattr(settings, "test_model", True)
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


# ── the stamp on the session row ────────────────────────────────────────────


async def test_every_session_creator_stamps_the_current_version():
    current = await repo.save_charter_version(AGENT, "The stamped charter.", "test")
    running, lane, paused = ("sess_" + uuid.uuid4().hex[:12] for _ in range(3))
    await repo.create_running_session(running, AGENT)
    await repo.create_conversation_session(lane, AGENT)
    await repo.save_paused_session(paused, AGENT, {"messages": []})
    for session_id in (running, lane, paused):
        assert (await repo.get_session(session_id))["charter_version"] == current["version"]


async def test_the_built_in_charter_stamps_zero():
    agent = "stamp-" + uuid.uuid4().hex[:8]
    await repo.upsert_agent(agent, agent, "", "")
    session_id = "sess_" + uuid.uuid4().hex[:12]
    await repo.create_running_session(session_id, agent)
    row = await repo.get_session(session_id)
    assert (row["charter_version"], row["charter_current"]) == (0, 0)


async def test_a_row_from_before_the_stamp_stays_null_with_no_derivation():
    current = await repo.save_charter_version(AGENT, "A charter that exists.", "test")
    session_id = "sess_" + uuid.uuid4().hex[:12]
    conn = await repo._conn()
    try:
        await conn.execute(
            "insert into session (id, agent_id, status) values ($1, $2, 'DONE')",
            session_id, AGENT)
    finally:
        await conn.close()
    row = await repo.get_session(session_id)
    assert (row["charter_version"], row["charter_current"]) == (None, current["version"])
    listed = next(r for r in await repo.list_sessions(limit=500, agent_id=AGENT)
                  if r["id"] == session_id)
    assert listed["charter_version"] is None
    assert listed["charter_current"] == current["version"]


async def test_an_ingest_run_stamps_through_the_one_creator():
    current = await repo.save_charter_version(AGENT, "The triage charter.", "test")
    out = await ingest_and_propose("Please look at this.", model=make_spike_model())
    assert (await repo.get_session(out["session_id"]))["charter_version"] == current["version"]
