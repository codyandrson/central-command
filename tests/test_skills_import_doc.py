"""Tests for `POST /api/skills/import-doc` — a single document, from a URL
(fetched server-side) or pasted content, into an EXISTING skill.

No TestClient pattern exists anywhere in this suite (grep confirms it): every
route is exercised by calling its handler function directly, the same as
`test_webfetch.py` does for the tool layer. This file follows that.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import HTTPException

from central_command.api import routes
from central_command.db import repo
from tests.conftest import needs_pg

_ORIGINAL_CLIENT = httpx.AsyncClient


pytestmark_db = needs_pg


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
async def test_url_and_content_both_present_is_422(clean_skills):
    await repo.create_skill("cc-test-doc-import", "T", "S")
    body = routes.SkillDocImportIn(
        skill_id="cc-test-doc-import", doc_key="api",
        url="https://example.test/x", content="hi",
    )
    with pytest.raises(HTTPException) as exc:
        await routes.import_skill_doc_route(body)
    assert exc.value.status_code == 422


@pytestmark_db
@pytest.mark.asyncio
async def test_url_and_content_both_absent_is_422(clean_skills):
    await repo.create_skill("cc-test-doc-import", "T", "S")
    body = routes.SkillDocImportIn(skill_id="cc-test-doc-import", doc_key="api")
    with pytest.raises(HTTPException) as exc:
        await routes.import_skill_doc_route(body)
    assert exc.value.status_code == 422


@pytestmark_db
@pytest.mark.asyncio
async def test_unknown_skill_is_404(clean_skills):
    body = routes.SkillDocImportIn(
        skill_id="cc-test-doc-import-missing", doc_key="api", content="hi",
    )
    with pytest.raises(HTTPException) as exc:
        await routes.import_skill_doc_route(body)
    assert exc.value.status_code == 404


@pytestmark_db
@pytest.mark.asyncio
async def test_content_import_lands_a_current_doc_version(clean_skills):
    await repo.create_skill("cc-test-doc-import", "T", "S")
    body = routes.SkillDocImportIn(
        skill_id="cc-test-doc-import", doc_key="api", title="API",
        content="# Endpoints\nGET /thing", describes="v1",
    )
    doc = await routes.import_skill_doc_route(body)
    assert doc["version"] == 1
    assert doc["is_current"] is True
    assert doc["kind"] == "reference"
    assert doc["source_url"] is None
    assert "GET /thing" in doc["content"]

    docs = await repo.list_skill_docs("cc-test-doc-import")
    assert [d["doc_key"] for d in docs] == ["api"]


@pytestmark_db
@pytest.mark.asyncio
async def test_guidance_doc_key_is_kind_guidance(clean_skills):
    await repo.create_skill("cc-test-doc-import", "T", "S")
    body = routes.SkillDocImportIn(
        skill_id="cc-test-doc-import", doc_key="guidance", content="# Guide",
    )
    doc = await routes.import_skill_doc_route(body)
    assert doc["kind"] == "guidance"


@pytestmark_db
@pytest.mark.asyncio
async def test_url_import_fetches_and_records_provenance(clean_skills, monkeypatch):
    await repo.create_skill("cc-test-doc-import", "T", "S")

    def handler(request):
        return httpx.Response(
            200, headers={"content-type": "text/html"},
            text="<html><body><h1>Hi</h1></body></html>",
        )

    from central_command.integrations import webfetch

    def _factory(**kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return _ORIGINAL_CLIENT(**kwargs)

    monkeypatch.setattr(webfetch.httpx, "AsyncClient", _factory)

    body = routes.SkillDocImportIn(
        skill_id="cc-test-doc-import", doc_key="api",
        url="https://example.test/page",
    )
    doc = await routes.import_skill_doc_route(body)
    assert doc["source_url"] == "https://example.test/page"
    assert doc["captured_at"] is not None
    assert "Hi" in doc["content"]


@pytestmark_db
@pytest.mark.asyncio
async def test_url_fetch_failure_is_422_not_a_crash(clean_skills, monkeypatch):
    await repo.create_skill("cc-test-doc-import", "T", "S")

    def handler(request):
        return httpx.Response(503, text="down")

    from central_command.integrations import webfetch

    def _factory(**kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return _ORIGINAL_CLIENT(**kwargs)

    monkeypatch.setattr(webfetch.httpx, "AsyncClient", _factory)

    body = routes.SkillDocImportIn(
        skill_id="cc-test-doc-import", doc_key="api",
        url="https://example.test/down",
    )
    with pytest.raises(HTTPException) as exc:
        await routes.import_skill_doc_route(body)
    assert exc.value.status_code == 422
