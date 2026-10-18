"""Bundled skills declare default holders (`default_agents:` front-matter) and
the import grants them CREATE-ONLY: a row in any state blocks the grant, so a
revocation is never undone; unknown / non-roster ids are reported, not fatal."""

from __future__ import annotations

import pytest

from central_command.db import repo
from central_command.skills.importer import import_skill_folder, parse_id_list
from tests.conftest import needs_pg

SKILL = "cc-test-dg"
ROSTER = ("cc-test-dg-a", "cc-test-dg-b")
OFF_ROSTER = "cc-test-dg-off"


def test_default_agents_accepts_bare_and_bracketed_lists():
    assert parse_id_list("a, b ,c") == ["a", "b", "c"]
    assert parse_id_list("[a, 'b']") == ["a", "b"]
    assert parse_id_list("") == []


async def _wipe():
    conn = await repo._conn()
    try:
        await conn.execute("delete from skill where id = $1", SKILL)
        for a in (*ROSTER, OFF_ROSTER):
            await conn.execute("delete from agent where id = $1", a)
    finally:
        await conn.close()


@pytest.fixture()
async def world():
    await _wipe()
    for a in ROSTER:
        await repo.upsert_agent(a, a, "m", "c")
    await repo.upsert_agent(OFF_ROSTER, "off", "m", "c")
    conn = await repo._conn()
    try:
        await conn.execute(
            "update agent set role = 'tester' where id = any($1::text[])", list(ROSTER)
        )
    finally:
        await conn.close()
    yield
    await _wipe()


def _folder(tmp_path, agents: str):
    f = tmp_path / "s"
    f.mkdir(exist_ok=True)
    (f / "SKILL.md").write_text(f"---\nname: s\ndefault_agents: {agents}\n---\n# s\nbody")
    return f


async def _grants():
    return {g["agent_id"]: g for g in await _all()}


async def _all():
    conn = await repo._conn()
    try:
        rows = await conn.fetch("select * from agent_skill_grant where skill_id = $1", SKILL)
        return [dict(r) for r in rows]
    finally:
        await conn.close()


@needs_pg
@pytest.mark.asyncio
async def test_import_grants_roster_agents_and_reports_the_rest(world, tmp_path):
    folder = _folder(tmp_path, f"{ROSTER[0]}, {ROSTER[1]}, {OFF_ROSTER}, cc-test-dg-nobody")
    r = await import_skill_folder(folder, skill_id=SKILL, apply_default_grants=True)
    assert r["default_grants"] == {
        "applied": list(ROSTER), "skipped": [],
        "unknown": [OFF_ROSTER, "cc-test-dg-nobody"],
    }
    g = await _grants()
    assert set(g) == set(ROSTER)
    assert all(row["granted_by"] == "seed:skill" for row in g.values())


@needs_pg
@pytest.mark.asyncio
async def test_reimport_skips_existing_and_never_resurrects_a_revoked_grant(world, tmp_path):
    folder = _folder(tmp_path, ", ".join(ROSTER))
    await import_skill_folder(folder, skill_id=SKILL, apply_default_grants=True)
    assert await repo.revoke_skill(ROSTER[0], SKILL, "operator said no")

    r = await import_skill_folder(folder, skill_id=SKILL, apply_default_grants=True)

    assert r["default_grants"]["applied"] == []
    assert sorted(r["default_grants"]["skipped"]) == sorted(ROSTER)
    g = await _grants()
    assert g[ROSTER[0]]["revoked_at"] is not None, "a revocation was undone by re-import"
    assert g[ROSTER[1]]["revoked_at"] is None


@needs_pg
@pytest.mark.asyncio
async def test_a_skill_without_default_agents_grants_nothing(world, tmp_path):
    f = tmp_path / "plain"
    f.mkdir()
    (f / "SKILL.md").write_text("---\nname: plain\n---\n# p")
    r = await import_skill_folder(f, skill_id=SKILL)
    assert r["default_grants"] == {"applied": [], "skipped": [], "unknown": []}


@needs_pg
@pytest.mark.asyncio
async def test_default_agents_is_ignored_unless_the_caller_opts_in(world, tmp_path):
    folder = _folder(tmp_path, ROSTER[0])
    r = await import_skill_folder(folder, skill_id=SKILL)
    assert r["default_grants"] == {"applied": [], "skipped": [], "unknown": []}
    assert await _all() == []
