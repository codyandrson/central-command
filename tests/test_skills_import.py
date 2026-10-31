"""Importing a SKILL.md + references/ folder.

This is the path that makes the library usable: pointing at a directory beats
pasting eleven documents into a textarea, and any Claude-style skill folder
imports as-is.
"""

from __future__ import annotations

import hashlib
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


# --- bundled provenance (epic-skills-refresh-on-update R1) -------------------


def _checkout(tmp_path, monkeypatch, release: str | None = "9.9.9"):
    """A tmp checkout the importer treats as the one it runs from, with a copy
    of a real MARKED bundled skill under its skills/ tree."""
    import shutil
    from pathlib import Path

    from central_command.skills import importer

    root = tmp_path / "checkout"
    (root / "skills").mkdir(parents=True)
    if release is not None:
        (root / "VERSION").write_text(f"version={release}\nmin_upgrade_from=2.0.0\n")
    real = Path(__file__).resolve().parents[1] / "skills" / "litellm"
    shutil.copytree(real, root / "skills" / "litellm")
    monkeypatch.setattr(importer, "checkout_root", lambda: root)
    return root


def _overlay_dir(tmp_path, monkeypatch, **fields: str) -> None:
    from central_command.config import settings

    d = tmp_path / "overlay"
    d.mkdir()
    for name, text in fields.items():
        (d / f"{name}.md").write_text(text + "\n")
    monkeypatch.setattr(settings, "skill_overlay_dir", str(d))


async def _text_rows(hashes: list[str]) -> dict[str, dict]:
    conn = await repo._conn()
    try:
        rows = await conn.fetch(
            "select * from skill_bundled_text where hash = any($1::text[])", hashes
        )
    finally:
        await conn.close()
    return {r["hash"]: dict(r) for r in rows}


@pytestmark_db
@pytest.mark.asyncio
async def test_an_import_from_under_the_checkout_stamps_every_doc(
    clean_skills, tmp_path, monkeypatch
):
    root = _checkout(tmp_path, monkeypatch)
    _overlay_dir(tmp_path, monkeypatch, model_server_layout="DEMO")

    result = await import_skill_folder(root / "skills" / "litellm", skill_id="cc-test-litellm")

    assert result["bundled_release"] == "9.9.9"
    docs = await repo.list_skill_docs("cc-test-litellm")
    assert len(docs) == 5
    paths = {
        d["doc_key"]: d["bundled_path"] for d in docs
    }
    assert paths["guidance"] == "skills/litellm/SKILL.md"
    assert paths["routing-doctrine"] == "skills/litellm/references/routing-doctrine.md"
    assert all(d["bundled_release"] == "9.9.9" for d in docs)
    # The hash is over the STORED text — the rendered text, not the file.
    assert all(
        d["bundled_hash"] == hashlib.sha256(d["content"].encode()).hexdigest() for d in docs
    )
    marked = next(d for d in docs if d["doc_key"] == "models-and-model-info")
    assert "DEMO" in marked["content"]
    assert marked["bundled_hash"] != hashlib.sha256(
        (root / "skills/litellm/references/models-and-model-info.md").read_bytes()
    ).hexdigest()
    # One text row per distinct text, holding exactly the stored content.
    hashes = sorted({d["bundled_hash"] for d in docs})
    rows = await _text_rows(hashes)
    assert sorted(rows) == hashes
    assert all(r["first_seen_release"] == "9.9.9" for r in rows.values())
    by_hash = {d["bundled_hash"]: d["content"] for d in docs}
    assert all(rows[h]["content"] == by_hash[h] for h in hashes)


@pytestmark_db
@pytest.mark.asyncio
async def test_two_docs_with_the_same_text_share_one_text_row(
    clean_skills, tmp_path, monkeypatch
):
    root = _checkout(tmp_path, monkeypatch)
    folder = root / "skills" / "twins"
    (folder / "references").mkdir(parents=True)
    (folder / "SKILL.md").write_text("---\nname: twins\n---\n\n# Twins\nguidance")
    (folder / "references" / "a.md").write_text("# Same\nbody")
    (folder / "references" / "b.md").write_text("---\nx: y\n---\n# Same\nbody")

    await import_skill_folder(folder, skill_id="cc-test-twins")

    docs = await repo.list_skill_docs("cc-test-twins")
    assert len(docs) == 3
    hashes = {d["bundled_hash"] for d in docs}
    assert len(hashes) == 2          # frontmatter is stripped before hashing
    assert len(await _text_rows(sorted(hashes))) == 2


@pytestmark_db
@pytest.mark.asyncio
async def test_an_import_from_elsewhere_stamps_nothing(clean_skills, tmp_path, monkeypatch):
    _checkout(tmp_path, monkeypatch)
    folder = tmp_path / "elsewhere"
    folder.mkdir()
    (folder / "SKILL.md").write_text("# One\nguidance")
    conn = await repo._conn()
    try:
        before = await conn.fetchval("select count(*) from skill_bundled_text")
    finally:
        await conn.close()

    result = await import_skill_folder(folder, skill_id="cc-test-elsewhere")

    assert result["bundled_release"] is None
    (doc,) = await repo.list_skill_docs("cc-test-elsewhere")
    assert (doc["bundled_release"], doc["bundled_path"], doc["bundled_hash"]) == (None, None, None)
    conn = await repo._conn()
    try:
        assert await conn.fetchval("select count(*) from skill_bundled_text") == before
    finally:
        await conn.close()


@pytestmark_db
@pytest.mark.asyncio
async def test_a_folder_beside_skills_in_the_checkout_stamps_nothing(
    clean_skills, tmp_path, monkeypatch
):
    """Under the checkout is not enough: only its skills/ tree is bundled."""
    root = _checkout(tmp_path, monkeypatch)
    folder = root / "docs" / "notes"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text("# Notes\nx")

    result = await import_skill_folder(folder, skill_id="cc-test-notes")

    assert result["bundled_release"] is None
    (doc,) = await repo.list_skill_docs("cc-test-notes")
    assert doc["bundled_hash"] is None


@pytestmark_db
@pytest.mark.asyncio
async def test_a_hand_post_after_an_import_carries_no_provenance(
    clean_skills, tmp_path, monkeypatch
):
    from central_command.api import routes

    root = _checkout(tmp_path, monkeypatch)
    await import_skill_folder(root / "skills" / "litellm", skill_id="cc-test-litellm")

    await routes.add_skill_doc_route(
        "cc-test-litellm",
        routes.SkillDocIn(doc_key="guidance", kind="guidance", title="G", content="edited"),
    )

    v2, v1 = await repo.skill_doc_history("cc-test-litellm", "guidance")
    assert (v2["version"], v2["bundled_release"], v2["bundled_path"], v2["bundled_hash"]) == (
        2, None, None, None,
    )
    assert v1["bundled_release"] == "9.9.9" and v1["bundled_hash"] is not None


@pytestmark_db
@pytest.mark.asyncio
async def test_an_unreadable_version_refuses_the_import_before_any_write(
    clean_skills, tmp_path, monkeypatch
):
    root = _checkout(tmp_path, monkeypatch, release=None)
    with pytest.raises(ValueError, match="VERSION"):
        await import_skill_folder(root / "skills" / "litellm", skill_id="cc-test-litellm")
    assert await repo.get_skill("cc-test-litellm") is None

    (root / "VERSION").write_text("min_upgrade_from=2.0.0\n")
    with pytest.raises(ValueError, match="no version= line"):
        await import_skill_folder(root / "skills" / "litellm", skill_id="cc-test-litellm")
    assert await repo.get_skill("cc-test-litellm") is None


@pytestmark_db
@pytest.mark.asyncio
async def test_the_import_route_stamps_its_event_and_the_hand_route_does_not(
    clean_skills, tmp_path, monkeypatch
):
    from central_command.api import routes

    root = _checkout(tmp_path, monkeypatch)
    folder = tmp_path / "elsewhere"
    folder.mkdir()
    (folder / "SKILL.md").write_text("# One\nx")

    await routes.import_skill_route(
        routes.SkillImportIn(path=str(root / "skills" / "litellm"), skill_id="cc-test-litellm")
    )
    await routes.import_skill_route(
        routes.SkillImportIn(path=str(folder), skill_id="cc-test-elsewhere")
    )

    events = {
        e["ref_id"]: e["payload"]
        for e in await repo.list_events_of_kinds(["skill.imported"])
        if e["ref_id"] in ("cc-test-litellm", "cc-test-elsewhere")
    }
    assert events["cc-test-litellm"]["bundled_release"] == "9.9.9"
    assert events["cc-test-elsewhere"]["bundled_release"] is None


# --- normalise_doc_text (pure) -----------------------------------------------


def test_normalise_strips_frontmatter_then_renders_the_body_only():
    from central_command.skills.importer import normalise_doc_text

    raw = "---\nname: s\n---\n\n# T\nwhere: <!-- overlay: model_server_layout -->\n"
    doc = normalise_doc_text("guidance", raw, "mem", overlay={"model_server_layout": "HERE"})
    assert doc.meta == {"name": "s"}
    assert doc.text == "# T\nwhere: HERE\n"
    assert doc.resolved == ["model_server_layout"] and doc.unresolved == []

    bare = normalise_doc_text("reference", raw, "mem", overlay={})
    assert bare.unresolved == ["model_server_layout"]
    assert "overlay" not in bare.text


def test_normalise_refuses_a_marker_in_frontmatter_and_an_unknown_kind():
    from central_command.skills.importer import normalise_doc_text

    with pytest.raises(ValueError, match="mem:2"):
        normalise_doc_text(
            "guidance", "---\nname: <!-- overlay: model_server_layout -->\n---\nx", "mem", overlay={}
        )
    with pytest.raises(ValueError, match="unknown document kind"):
        normalise_doc_text("tome", "x", "mem", overlay={})


def test_the_hash_is_sha256_of_the_utf8_text():
    from central_command.skills.importer import bundled_text_hash

    assert bundled_text_hash("é") == hashlib.sha256("é".encode()).hexdigest()
