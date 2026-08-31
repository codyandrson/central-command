"""Sources-catalog slice 5: the Confluence Cloud watcher (CQL incremental
crawl as ground truth — Decision 8 / the 2026-07-29 knowledge-layer record's
Decision 3). Real Postgres for the catalog writes, like tests/test_catalog.py
— the Confluence client itself is fully mocked (confluence.configured /
search / get_page monkeypatched); the suite must never touch a live
Confluence.
"""

from __future__ import annotations

import hashlib
import json
import uuid

import pytest
from fastapi import HTTPException

from central_command.api import routes
from central_command.db import repo
from central_command.ingest.watcher import walk_source
from central_command.integrations import confluence
from tests.conftest import needs_pg

pytestmark = needs_pg


def _sid() -> str:
    return "src-cfl-" + uuid.uuid4().hex[:8]


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


async def _make_source(cleanup, **config) -> dict:
    sid = _sid()
    cleanup.append(sid)
    return await repo.upsert_source(sid, "confluence", "Test confluence source", config, enabled=True)


def _hit(page_id: str, title: str, when: str) -> dict:
    """One CQL search result row (already normalized by confluence.search)."""
    return {"id": page_id, "title": title, "last_modified": when}


def _page(page_id: str, title: str, body: str, version: int, space_key: str) -> dict:
    return {
        "id": page_id, "title": title, "body": body, "version": version,
        "space_key": space_key, "url": None,
    }


async def _version_row(document_id: str) -> dict:
    conn = await repo._conn()
    try:
        return await conn.fetchrow(
            "select * from catalog_version where document_id = $1", document_id
        )
    finally:
        await conn.close()


def _meta(ver) -> dict:
    return ver["meta"] if isinstance(ver["meta"], dict) else json.loads(ver["meta"])


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setattr(confluence, "configured", lambda: True)


async def test_unconfigured_client_raises(monkeypatch, cleanup):
    monkeypatch.setattr(confluence, "configured", lambda: False)
    source = await _make_source(cleanup, space_keys=["ENG"])
    with pytest.raises(ValueError, match="not configured"):
        await walk_source(source)


async def test_neither_cql_nor_space_keys_raises(cleanup):
    source = await _make_source(cleanup)
    with pytest.raises(ValueError, match="config.cql or"):
        await walk_source(source)


async def test_cql_built_from_space_keys(monkeypatch, cleanup):
    captured = {}

    async def fake_search(cql, limit=None):
        captured["cql"] = cql
        return {"results": []}

    monkeypatch.setattr(confluence, "search", fake_search)
    source = await _make_source(cleanup, space_keys=["ENG", "OPS"])
    await walk_source(source)
    assert captured["cql"] == '(space in ("ENG", "OPS")) and type = page order by lastmodified asc'


async def test_first_walk_records_page_with_confluence_meta(monkeypatch, cleanup):
    async def fake_search(cql, limit=None):
        return {"results": [_hit("100", "Runbook", "2026-08-20 10:00")]}

    async def fake_get_page(page_id):
        assert page_id == "100"
        return {"page": _page("100", "Runbook", "<p>hello</p>", 3, "ENG")}

    monkeypatch.setattr(confluence, "search", fake_search)
    monkeypatch.setattr(confluence, "get_page", fake_get_page)

    source = await _make_source(cleanup, space_keys=["ENG"])
    summary = await walk_source(source)
    assert summary["new_lineages"] == 1
    assert summary["new_versions"] == 1
    assert summary["marked_missing"] == 0

    docs = await repo.list_catalog_documents(source["id"])
    assert len(docs) == 1
    assert docs[0]["lineage_key"] == "100"

    ver = await _version_row(docs[0]["id"])
    meta = _meta(ver)
    assert meta["confluence_version"] == 3
    assert meta["space"] == "ENG"
    assert meta["confluence_when"] == "2026-08-20 10:00"
    assert ver["extracted_text"].strip() == "hello"


async def test_cursor_advances_and_next_walk_carries_overlap_adjusted_clause(monkeypatch, cleanup):
    calls: list[str] = []

    async def fake_search(cql, limit=None):
        calls.append(cql)
        if len(calls) == 1:
            return {"results": [_hit("100", "Page", "2026-08-20 10:00")]}
        return {"results": []}

    async def fake_get_page(page_id):
        return {"page": _page("100", "Page", "<p>hi</p>", 1, "ENG")}

    monkeypatch.setattr(confluence, "search", fake_search)
    monkeypatch.setattr(confluence, "get_page", fake_get_page)

    source = await _make_source(cleanup, space_keys=["ENG"])
    await walk_source(source)

    updated = await repo.get_source(source["id"])
    assert updated["cursor"]["last_modified"] == "2026-08-20 10:00"

    await walk_source(updated)
    assert len(calls) == 2
    assert 'lastmodified >= "2026-08-20 09:58"' in calls[1]


async def test_rewalk_same_body_writes_no_new_version(monkeypatch, cleanup):
    async def fake_search(cql, limit=None):
        return {"results": [_hit("100", "Page", "2026-08-20 10:00")]}

    async def fake_get_page(page_id):
        return {"page": _page("100", "Page", "<p>same</p>", 1, "ENG")}

    monkeypatch.setattr(confluence, "search", fake_search)
    monkeypatch.setattr(confluence, "get_page", fake_get_page)

    source = await _make_source(cleanup, space_keys=["ENG"])
    await walk_source(source)
    updated = await repo.get_source(source["id"])
    summary = await walk_source(updated)
    assert summary["new_versions"] == 0
    assert summary["new_lineages"] == 0


async def test_changed_body_writes_version_two(monkeypatch, cleanup):
    state = {"v": 1}

    async def fake_search(cql, limit=None):
        return {"results": [_hit("100", "Page", "2026-08-20 10:00")]}

    async def fake_get_page(page_id):
        content = "<p>one</p>" if state["v"] == 1 else "<p>two</p>"
        return {"page": _page("100", "Page", content, state["v"], "ENG")}

    monkeypatch.setattr(confluence, "search", fake_search)
    monkeypatch.setattr(confluence, "get_page", fake_get_page)

    source = await _make_source(cleanup, space_keys=["ENG"])
    await walk_source(source)
    state["v"] = 2
    updated = await repo.get_source(source["id"])
    summary = await walk_source(updated)
    assert summary["new_versions"] == 1

    docs = await repo.list_catalog_documents(source["id"])
    assert docs[0]["latest_version"] == 2


async def test_empty_body_takes_raw_hash_path(monkeypatch, cleanup):
    async def fake_search(cql, limit=None):
        return {"results": [_hit("200", "Empty", "2026-08-20 10:00")]}

    async def fake_get_page(page_id):
        return {"page": _page("200", "Empty", "", 1, "ENG")}

    monkeypatch.setattr(confluence, "search", fake_search)
    monkeypatch.setattr(confluence, "get_page", fake_get_page)

    source = await _make_source(cleanup, space_keys=["ENG"])
    summary = await walk_source(source)
    assert summary["new_versions"] == 1

    docs = await repo.list_catalog_documents(source["id"])
    ver = await _version_row(docs[0]["id"])
    assert _meta(ver)["extraction"] == "empty"
    assert ver["content_hash"] == hashlib.sha256(b"").hexdigest()


async def test_one_page_fetch_failure_does_not_abort_the_walk(monkeypatch, cleanup):
    async def fake_search(cql, limit=None):
        return {"results": [
            _hit("300", "Bad", "2026-08-20 10:00"),
            _hit("301", "Good", "2026-08-20 11:00"),
        ]}

    async def fake_get_page(page_id):
        if page_id == "300":
            raise confluence.ConfluenceError("boom")
        return {"page": _page("301", "Good", "<p>ok</p>", 1, "ENG")}

    monkeypatch.setattr(confluence, "search", fake_search)
    monkeypatch.setattr(confluence, "get_page", fake_get_page)

    source = await _make_source(cleanup, space_keys=["ENG"])
    summary = await walk_source(source)
    assert summary["new_lineages"] == 1
    assert summary["pages_failed"] == 1
    assert summary["marked_missing"] == 0

    docs = await repo.list_catalog_documents(source["id"])
    assert len(docs) == 1
    assert docs[0]["lineage_key"] == "301"


async def test_post_sources_accepts_confluence_with_space_keys(cleanup):
    sid = _sid()
    cleanup.append(sid)
    body = routes.SourceIn(
        id=sid, kind="confluence", name="Eng wiki", config={"space_keys": ["ENG"]}, enabled=True,
    )
    out = await routes.upsert_source(body)
    assert out["ok"] is True
    assert out["source"]["kind"] == "confluence"


async def test_post_sources_rejects_confluence_without_cql_or_space_keys():
    body = routes.SourceIn(id=_sid(), kind="confluence", name="Bad", config={}, enabled=True)
    with pytest.raises(HTTPException) as exc:
        await routes.upsert_source(body)
    assert exc.value.status_code == 422
    assert "cql" in exc.value.detail and "space_keys" in exc.value.detail
