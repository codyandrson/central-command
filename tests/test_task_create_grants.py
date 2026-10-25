"""Who holds the `task-create` pack (story 4.9, GATE-032 / FR-19).

The operator's ruling of 2026-10-22: every active agent except the auditor holds
the direct `create_task` tool, the orchestrator included, and the orchestrator's
assignment loop (`assign_work`, plan review) is unchanged. Grants are history, so
`task-propose` is revoked by timestamp and never deleted, and a fresh database
must reproduce the rule from schema.sql alone.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

import pytest

from central_command.db import repo
from central_command.runtime import packs
from central_command.runtime.templates import (
    ACCOUNTABILITY_TEMPLATE, EA_TEMPLATE, EDITOR_TEMPLATE,
    GRAPH_CURATOR_TEMPLATE, MCP_MANAGER_TEMPLATE, STEWARD_TEMPLATE, TEMPLATES,
)
from tests.conftest import needs_pg

# The founding roster schema.sql seeds (the EA, steward and the rest are hired).
FOUNDING = ("inbox-triage", "jira-expert", "auditor", "orchestrator",
            "litellm-manager", "coach", "confluence-expert", "wiki-agent")
GRANTED = tuple(a for a in FOUNDING if a != "auditor")

_SCHEMA = Path(repo.__file__).with_name("schema.sql")
_MARKER = "-- task-create for every active agent but the auditor (story 4.9"


async def _grants(agent_id: str, *, active: bool = True) -> set[str]:
    conn = await repo._conn()
    try:
        rows = await conn.fetch(
            "select pack from agent_grant where agent_id = $1"
            + (" and revoked_at is null" if active else ""), agent_id)
    finally:
        await conn.close()
    return {r["pack"] for r in rows}


@needs_pg
async def test_a_fresh_database_gives_every_founding_agent_but_the_auditor_the_pack():
    for agent_id in GRANTED:
        held = await _grants(agent_id)
        assert "task-create" in held, f"{agent_id} cannot create a task"
        assert "task-propose" not in held, f"{agent_id} still holds task-propose"
    assert await _grants("auditor") == set(), "the auditor takes no toolsets"


@needs_pg
async def test_the_orchestrator_holds_task_create_beside_its_assignment_loop():
    held = await _grants("orchestrator")
    assert {"orchestrate", "task-create"} <= held
    assert "assign_work" in packs.PACKS["orchestrate"].tool_names


@needs_pg
async def test_task_propose_is_revoked_by_timestamp_never_deleted():
    conn = await repo._conn()
    try:
        rows = await conn.fetch(
            "select agent_id, revoked_at, revoked_reason from agent_grant "
            "where pack = 'task-propose'")
    finally:
        await conn.close()
    assert rows, "the revoked rows are the history; the seeds still write them"
    assert all(r["revoked_at"] is not None and r["revoked_reason"] for r in rows)


@needs_pg
async def test_default_packs_match_the_seeded_task_create_grants():
    for agent_id in GRANTED:
        if agent_id in packs.DEFAULT_PACKS:
            assert "task-create" in packs.DEFAULT_PACKS[agent_id]
    assert "auditor" not in packs.DEFAULT_PACKS
    assert not any("task-propose" in v for v in packs.DEFAULT_PACKS.values())


@needs_pg
async def test_the_deviation_texts_say_what_is_now_true():
    conn = await repo._conn()
    try:
        rows = {r["id"]: r["deviations"] for r in await conn.fetch(
            "select id, deviations from agent where id in ('auditor', 'orchestrator')")}
    finally:
        await conn.close()
    assert "no task-create grant" in rows["auditor"]
    assert "takes no toolsets" in rows["auditor"]
    assert "holds task-create" in rows["orchestrator"]
    assert "assign_work" in rows["orchestrator"]
    assert "task-propose" not in rows["auditor"] + rows["orchestrator"]


def test_every_hire_path_grants_the_pack():
    """The hired agents are not in schema.sql's seeds: their templates are the
    fresh-database path, so each purpose-built loadout carries the pack too."""
    loadouts = [*TEMPLATES.values(), STEWARD_TEMPLATE, GRAPH_CURATOR_TEMPLATE,
                EA_TEMPLATE, MCP_MANAGER_TEMPLATE, EDITOR_TEMPLATE,
                ACCOUNTABILITY_TEMPLATE]
    for t in loadouts:
        assert "task-create" in t.default_packs, f"{t.id} cannot create a task"


def test_the_trailing_migration_never_names_the_auditor_as_a_target():
    sql = _SCHEMA.read_text()
    block = sql[sql.index(_MARKER):]
    assert "a.id <> 'auditor'" in block
    assert not re.search(r"\bdelete\s+from\s+agent_grant", sql, re.I), (
        "grants are history: a revocation is a timestamp, never a delete")


@needs_pg
async def test_the_widening_migration_is_additive_idempotent_and_respects_history():
    sql = _SCHEMA.read_text()
    block = sql[sql.index(_MARKER):]
    tag = uuid.uuid4().hex[:8]
    holder, revoked, bare, retired = (f"tc9-{n}-{tag}" for n in
                                      ("holder", "revoked", "bare", "retired"))
    conn = await repo._conn()
    try:
        for aid, status, role in ((holder, "ACTIVE", "r"), (revoked, "ACTIVE", "r"),
                                  (bare, "ACTIVE", "r"), (retired, "RETIRED", "r")):
            await conn.execute(
                "insert into agent (id, name, model, charter, role, status) "
                "values ($1, $1, '', '', $2, $3)", aid, role, status)
        for aid in (holder, revoked, retired):
            await conn.execute(
                "insert into agent_grant (agent_id, pack) values ($1, 'jira-read')", aid)
        await conn.execute(
            "insert into agent_grant (agent_id, pack, revoked_at, revoked_reason) "
            "values ($1, 'task-create', now(), 'operator took it away')", revoked)
        await conn.execute(block)
        await conn.execute(block)  # a second run changes nothing
    finally:
        await conn.close()
    try:
        assert "task-create" in await _grants(holder)
        assert "task-create" not in await _grants(revoked)  # history kept, not resurrected
        assert "task-create" in await _grants(revoked, active=False)
        assert await _grants(bare, active=False) == set()  # takes no toolsets
        assert await _grants(retired, active=False) == {"jira-read"}  # not active
        assert await _grants("auditor", active=False) == set()
    finally:
        conn = await repo._conn()
        try:
            ids = [holder, revoked, bare, retired]
            await conn.execute("delete from agent_grant where agent_id = any($1)", ids)
            await conn.execute("delete from agent where id = any($1)", ids)
        finally:
            await conn.close()
