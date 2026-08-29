"""Importing a SKILL.md + references/ folder.

This is the path that makes the library usable: pointing at a directory beats
pasting eleven documents into a textarea, and any Claude-style skill folder
imports as-is.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone

import pytest

from central_command.db import repo
from central_command.skills.importer import import_skill_folder, parse_frontmatter
from tests.conftest import needs_pg

pytestmark_db = needs_pg


# --- frontmatter parsing (pure) ---------------------------------------------


def test_frontmatter_is_split_from_the_body():
    raw = "---\nname: my-skill\ndescription: does a thing\n---\n\n# Body\ntext"
    meta, body = parse_frontmatter(raw)
    assert meta["name"] == "my-skill"
    assert meta["description"] == "does a thing"
    assert body.startswith("# Body")


def test_missing_frontmatter_is_not_an_error():
    """A hand-written guidance doc has no frontmatter and is still perfectly
    valid material — refusing it would make the easy path the unusable one."""
    meta, body = parse_frontmatter("# Just a document\ntext")
    assert meta == {}
    assert body.startswith("# Just a document")


# --- import (needs Postgres) -------------------------------------------------


@pytest.fixture()
async def clean_skills():
    conn = await repo._conn()
    try:
        await conn.execute("delete from skill where id like 'cc-test-%'")
    finally:
        await conn.close()
    yield
    conn = await repo._conn()
    try:
        await conn.execute("delete from skill where id like 'cc-test-%'")
    finally:
        await conn.close()


@pytestmark_db
@pytest.mark.asyncio
async def test_imports_guidance_and_every_reference(clean_skills, tmp_path):
    folder = tmp_path / "my-skill"
    (folder / "references").mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        "---\nname: my-skill\ndescription: how to do the thing\n---\n\n# Doing it\nsteps"
    )
    (folder / "references" / "API.md").write_text("# Endpoints\nGET /thing")
    (folder / "references" / "GUIDE.md").write_text("# Guide\nlonger prose")

    result = await import_skill_folder(folder, skill_id="cc-test-my-skill")

    assert result["skill_id"] == "cc-test-my-skill"
    assert sorted(result["references"]) == ["api", "guide"]

    docs = await repo.list_skill_docs("cc-test-my-skill")
    kinds = {d["doc_key"]: d["kind"] for d in docs}
    assert kinds == {"guidance": "guidance", "api": "reference", "guide": "reference"}

    skill = await repo.get_skill("cc-test-my-skill")
    assert skill["summary"] == "how to do the thing"


@pytestmark_db
@pytest.mark.asyncio
async def test_reimporting_creates_new_versions_not_duplicates(clean_skills, tmp_path):
    """Re-importing after the upstream docs change is the normal refresh path —
    it must version, not accumulate parallel copies."""
    folder = tmp_path / "my-skill"
    folder.mkdir()
    (folder / "SKILL.md").write_text("# One\nfirst")
    await import_skill_folder(folder, skill_id="cc-test-my-skill")

    (folder / "SKILL.md").write_text("# One\nsecond")
    await import_skill_folder(folder, skill_id="cc-test-my-skill")

    current = await repo.list_skill_docs("cc-test-my-skill")
    assert len(current) == 1
    assert current[0]["version"] == 2
    assert "second" in current[0]["content"]


@pytestmark_db
@pytest.mark.asyncio
async def test_folder_without_a_skill_md_is_rejected(clean_skills, tmp_path):
    folder = tmp_path / "empty"
    folder.mkdir()
    with pytest.raises(FileNotFoundError):
        await import_skill_folder(folder, skill_id="cc-test-empty")


@pytestmark_db
@pytest.mark.asyncio
@pytest.mark.skipif(
    sys.platform == "win32",
    reason="NTFS is case-insensitive; the collision this asserts presents "
    "differently — needs a Windows-shaped equivalent (2026-08-21 W7)",
)
async def test_colliding_reference_filenames_are_rejected_and_nothing_is_written(
    clean_skills, tmp_path
):
    """API.md and api.md both slug to doc_key 'api' — importing must refuse
    instead of letting the second file silently supersede the first, and it
    must leave the skill with no docs at all rather than a partial import."""
    folder = tmp_path / "my-skill"
    (folder / "references").mkdir(parents=True)
    (folder / "SKILL.md").write_text("# One\nguidance")
    (folder / "references" / "API.md").write_text("# Endpoints A\nfirst")
    (folder / "references" / "api.md").write_text("# Endpoints B\nsecond")

    with pytest.raises(ValueError) as exc_info:
        await import_skill_folder(folder, skill_id="cc-test-my-skill")

    message = str(exc_info.value)
    assert "API.md" in message
    assert "api.md" in message

    docs = await repo.list_skill_docs("cc-test-my-skill")
    assert docs == []


@pytestmark_db
@pytest.mark.asyncio
async def test_reference_colliding_with_the_guidance_key_is_rejected(
    clean_skills, tmp_path
):
    """A reference file literally named guidance.md slugs to the same doc_key
    ("guidance") the SKILL.md body is stored under — it must be caught as a
    collision against SKILL.md itself, not just against other references."""
    folder = tmp_path / "my-skill"
    (folder / "references").mkdir(parents=True)
    (folder / "SKILL.md").write_text("# One\nguidance body")
    (folder / "references" / "guidance.md").write_text("# Not the guidance\nsneaky")

    with pytest.raises(ValueError) as exc_info:
        await import_skill_folder(folder, skill_id="cc-test-my-skill")

    message = str(exc_info.value)
    assert "SKILL.md" in message
    assert "guidance.md" in message

    docs = await repo.list_skill_docs("cc-test-my-skill")
    assert docs == []


@pytestmark_db
@pytest.mark.asyncio
async def test_undecodable_reference_file_is_rejected_and_nothing_is_written(
    clean_skills, tmp_path
):
    """A reference file with invalid UTF-8 bytes must fail the whole import
    before any document is written — not partway through the loop, leaving
    earlier references committed and this one crashing uncaught."""
    folder = tmp_path / "my-skill"
    (folder / "references").mkdir(parents=True)
    (folder / "SKILL.md").write_text("# One\nguidance")
    (folder / "references" / "GOOD.md").write_text("# Good\nfine")
    (folder / "references" / "BAD.md").write_bytes(b"\xff\xfe not valid utf-8")

    with pytest.raises(ValueError) as exc_info:
        await import_skill_folder(folder, skill_id="cc-test-my-skill")

    assert "BAD.md" in str(exc_info.value)

    docs = await repo.list_skill_docs("cc-test-my-skill")
    assert docs == []


# --- final-review fixes: freshness metadata and derived-id collisions ---------


@pytestmark_db
@pytest.mark.asyncio
async def test_import_records_captured_at_on_every_document(clean_skills, tmp_path):
    """The spec's rule is that ALL paths capture `captured_at`, because
    freshness metadata nobody enters is freshness metadata nobody has. The
    route did; the importer did not, so every imported document — i.e. every
    document in the live library — carried NULL, and both `_guidance_text` and
    `format_hits` omit the `Captured:` line when it is None. The freshness
    argument the design rests on therefore reached no agent at all."""
    before = datetime.now(timezone.utc)
    folder = tmp_path / "my-skill"
    (folder / "references").mkdir(parents=True)
    (folder / "SKILL.md").write_text("# Doing it\nsteps")
    (folder / "references" / "API.md").write_text("# Endpoints\nGET /thing")

    await import_skill_folder(folder, skill_id="cc-test-my-skill")

    docs = await repo.list_skill_docs("cc-test-my-skill")
    assert len(docs) == 2
    for d in docs:
        assert d["captured_at"] is not None, f"{d['doc_key']} has no captured_at"
        assert d["captured_at"] >= before


@pytestmark_db
@pytest.mark.asyncio
async def test_a_derived_id_that_already_exists_is_refused(clean_skills, tmp_path):
    """`Pydantic AI`, `pydantic-ai` and `pydantic_ai` all derive to the same id,
    and `repo.create_skill` is an upsert — so importing a second vendor's folder
    beside an existing skill of that name used to produce no error and no 409,
    just one skill holding two vendors' documentation and every holder silently
    re-pointed. The derived case is refused; naming the target explicitly is how
    the operator says "yes, this folder updates that skill"."""
    first = tmp_path / "cc-test-thing"
    first.mkdir()
    (first / "SKILL.md").write_text("---\nname: cc-test-thing\n---\n\n# A\nvendor one")
    await import_skill_folder(first)
    assert (await repo.get_skill("cc-test-thing"))["title"] == "cc-test-thing"

    second = tmp_path / "other"
    second.mkdir()
    (second / "SKILL.md").write_text("---\nname: CC Test Thing\n---\n\n# B\nvendor two")

    with pytest.raises(ValueError) as exc:
        await import_skill_folder(second)
    assert "cc-test-thing" in str(exc.value)

    # The refusal happened BEFORE any write: vendor one's document is intact.
    docs = await repo.list_skill_docs("cc-test-thing")
    assert len(docs) == 1
    assert "vendor one" in docs[0]["content"]

    # Naming the target explicitly is still allowed — that is the refresh path.
    await import_skill_folder(second, skill_id="cc-test-thing")
    guidance = [d for d in await repo.list_skill_docs("cc-test-thing")
                if d["doc_key"] == "guidance"][0]
    assert "vendor two" in guidance["content"]


@pytestmark_db
@pytest.mark.asyncio
async def test_an_explicit_skill_id_must_still_be_a_legal_id(clean_skills, tmp_path):
    """An id reaches the model inside a tool name, so the importer validates it
    too — not only the create route."""
    folder = tmp_path / "my-skill"
    folder.mkdir()
    (folder / "SKILL.md").write_text("# A\nbody")
    with pytest.raises(ValueError):
        await import_skill_folder(folder, skill_id="cc test thing")
