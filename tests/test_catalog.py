"""Sources-catalog slice 1: repo functions, the filesystem watcher, and the
API routes. Real Postgres, real filesystem (tmp_path) — no mocked SQL, per the
ledger rule (tests/test_ledger.py).
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException

from central_command.db import repo
from central_command.ingest.watcher import walk_source
from tests.conftest import needs_pg

pytestmark = needs_pg


def _sid() -> str:
    return "src-test-" + uuid.uuid4().hex[:8]


@pytest.fixture()
async def cleanup():
    ids: list[str] = []
    yield ids
    conn = await repo._conn()
    try:
        for sid in ids:
            docs = await conn.fetch("select id from catalog_document where source_id = $1", sid)
            doc_ids = [d["id"] for d in docs]
            if doc_ids:
                await conn.execute("delete from catalog_version where document_id = any($1::text[])", doc_ids)
                await conn.execute("delete from catalog_location where document_id = any($1::text[])", doc_ids)
                await conn.execute("delete from catalog_document where id = any($1::text[])", doc_ids)
            await conn.execute("delete from source where id = $1", sid)
    finally:
        await conn.close()


async def _make_source(cleanup, root, **config_extra) -> dict:
    sid = _sid()
    cleanup.append(sid)
    config = {"root": str(root), **config_extra}
    return await repo.upsert_source(sid, "filesystem", "Test source", config, enabled=True)


async def test_walk_catalogs_files_with_correct_hashes(tmp_path, cleanup):
    (tmp_path / "a.txt").write_text("hello world")
    source = await _make_source(cleanup, tmp_path)

    summary = await walk_source(source)
    assert summary["new_lineages"] == 1
    assert summary["new_versions"] == 1

    docs = await repo.list_catalog_documents(source["id"])
    assert len(docs) == 1
    assert docs[0]["lineage_key"] == "a.txt"
    assert docs[0]["latest_version"] == 1
    assert docs[0]["location_count"] == 1

    conn = await repo._conn()
    try:
        ver = await conn.fetchrow(
            "select * from catalog_version where document_id = $1", docs[0]["id"]
        )
    finally:
        await conn.close()
    import hashlib
    assert ver["content_hash"] == hashlib.sha256(b"hello world").hexdigest()
    assert ver["extracted_text"] == "hello world"


async def test_rewalk_with_no_changes_writes_nothing_new_and_emits_no_event(tmp_path, cleanup):
    (tmp_path / "a.txt").write_text("stable content")
    source = await _make_source(cleanup, tmp_path)
    await walk_source(source)

    before_id = await repo.latest_event_id()
    summary = await walk_source(source)
    assert summary["new_lineages"] == 0
    assert summary["new_versions"] == 0
    assert summary["marked_missing"] == 0

    events = await repo.list_events(since_id=before_id, kind="catalog.walk.completed", ref_id=source["id"])
    assert events == []


async def test_modified_file_gets_a_new_version(tmp_path, cleanup):
    f = tmp_path / "a.txt"
    f.write_text("version one")
    source = await _make_source(cleanup, tmp_path)
    await walk_source(source)

    f.write_text("version two")
    summary = await walk_source(source)
    assert summary["new_versions"] == 1
    assert summary["new_lineages"] == 0

    docs = await repo.list_catalog_documents(source["id"])
    assert docs[0]["latest_version"] == 2


async def test_deleted_file_marks_missing_and_reappearance_clears_it(tmp_path, cleanup):
    f = tmp_path / "a.txt"
    f.write_text("here today")
    source = await _make_source(cleanup, tmp_path)
    await walk_source(source)

    f.unlink()
    summary = await walk_source(source)
    assert summary["marked_missing"] == 1

    docs = await repo.list_catalog_documents(source["id"])
    assert docs[0]["missing_count"] == 1

    f.write_text("here today")  # gone tomorrow, back the day after
    await walk_source(source)
    docs = await repo.list_catalog_documents(source["id"])
    assert docs[0]["missing_count"] == 0


async def test_empty_extraction_records_a_version_hashed_on_raw_bytes(tmp_path, cleanup):
    junk = b"\x89PNG\r\n\x1a\nnot really a png, no text markitdown can find"
    (tmp_path / "scan.png").write_bytes(junk)
    source = await _make_source(cleanup, tmp_path)
    summary = await walk_source(source)
    assert summary["new_versions"] == 1

    docs = await repo.list_catalog_documents(source["id"])
    conn = await repo._conn()
    try:
        ver = await conn.fetchrow(
            "select * from catalog_version where document_id = $1", docs[0]["id"]
        )
    finally:
        await conn.close()
    import hashlib
    import json as _json
    meta = ver["meta"] if isinstance(ver["meta"], dict) else _json.loads(ver["meta"])
    assert meta["extraction"] in ("empty", "failed")
    assert ver["content_hash"] == hashlib.sha256(junk).hexdigest()


async def test_oversize_file_is_skipped(tmp_path, cleanup, monkeypatch):
    from central_command.ingest import watcher

    monkeypatch.setattr(watcher, "MAX_CATALOG_FILE_BYTES", 10)
    (tmp_path / "big.txt").write_text("this is definitely more than ten bytes")
    source = await _make_source(cleanup, tmp_path)
    summary = await walk_source(source)
    assert summary["skipped_oversize"] == 1
    assert summary["new_lineages"] == 0

    docs = await repo.list_catalog_documents(source["id"])
    assert docs == []


async def test_rescind_and_reinstate_flip_status_and_never_delete(tmp_path, cleanup):
    (tmp_path / "a.txt").write_text("policy text")
    source = await _make_source(cleanup, tmp_path)
    await walk_source(source)
    docs = await repo.list_catalog_documents(source["id"])
    doc_id = docs[0]["id"]

    assert await repo.rescind_document(doc_id, "superseded by v2", "operator")
    doc = await repo.get_catalog_document(doc_id)
    assert doc["status"] == "RESCINDED"
    assert doc["rescinded_reason"] == "superseded by v2"

    assert await repo.reinstate_document(doc_id)
    doc = await repo.get_catalog_document(doc_id)
    assert doc["status"] == "ACTIVE"
    assert doc["rescinded_reason"] is None


async def test_unknown_source_kind_raises(cleanup):
    sid = _sid()
    cleanup.append(sid)
    source = await repo.upsert_source(sid, "sharepoint", "Not built yet", {}, enabled=True)
    with pytest.raises(ValueError):
        await walk_source(source)


async def test_walk_of_disabled_source_via_api_returns_409(tmp_path, cleanup):
    from central_command.api import routes

    (tmp_path / "a.txt").write_text("x")
    source = await _make_source(cleanup, tmp_path)
    await repo.set_source_enabled(source["id"], False)

    with pytest.raises(HTTPException) as e:
        await routes.walk_source_route(source["id"])
    assert e.value.status_code == 409


async def test_wiki_claim_table_accepts_a_row(cleanup):
    """Shape smoke test — nothing populates this table in slice 1, but the
    schema (Decision 9) must exist and accept a well-formed row."""
    conn = await repo._conn()
    claim_id = "clm_" + uuid.uuid4().hex[:8]
    try:
        await conn.execute(
            """
            insert into wiki_claim (id, page_ref, statement, evidence)
            values ($1, $2, $3, $4::jsonb)
            """,
            claim_id, None, "The library holds 40 documents.",
            '[{"kind": "catalog", "source_ref": "doc_x", "version": 1}]',
        )
        row = await conn.fetchrow("select * from wiki_claim where id = $1", claim_id)
        assert row["statement"] == "The library holds 40 documents."
    finally:
        await conn.execute("delete from wiki_claim where id = $1", claim_id)
        await conn.close()
