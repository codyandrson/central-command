"""Tests for `POST /api/skills/import-site` — whole-site doc import via the
llms-full.txt / sitemap / crawler ladder (`central_command.skills.site_import`).

Follows `test_skills_import_doc.py`'s style: no TestClient, call the route
function directly; mock the HTTP layer with `httpx.MockTransport` (patching
`httpx.AsyncClient` patches the one shared `httpx` module object, so it covers
both `site_import`'s own client and `webfetch.fetch`'s).
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import HTTPException

from central_command.api import routes
from central_command.db import repo
from central_command.skills import site_import
from tests.conftest import needs_pg

_ORIGINAL_CLIENT = httpx.AsyncClient


pytestmark_db = needs_pg


@pytest.fixture()
async def clean_skills():
    conn = await repo._conn()
    try:
        await conn.execute("delete from skill where id like 'cc-test-site-%'")
    finally:
        await conn.close()
    yield
    conn = await repo._conn()
    try:
        await conn.execute("delete from skill where id like 'cc-test-site-%'")
    finally:
        await conn.close()


def _mock_client(monkeypatch, handler):
    def _factory(**kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return _ORIGINAL_CLIENT(**kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _factory)


@pytestmark_db
@pytest.mark.asyncio
async def test_unknown_skill_is_404():
    body = routes.SkillSiteImportIn(skill_id="cc-test-site-missing", url="https://example.test/docs")
    with pytest.raises(HTTPException) as exc:
        await routes.import_skill_site_route(body)
    assert exc.value.status_code == 404


@pytestmark_db
@pytest.mark.asyncio
async def test_bad_url_scheme_is_422(clean_skills):
    await repo.create_skill("cc-test-site-x", "T", "S")
    body = routes.SkillSiteImportIn(skill_id="cc-test-site-x", url="ftp://example.test/docs")
    with pytest.raises(HTTPException) as exc:
        await routes.import_skill_site_route(body)
    assert exc.value.status_code == 422


@pytestmark_db
@pytest.mark.asyncio
async def test_llms_full_happy_path_splits_into_sane_docs(clean_skills, monkeypatch):
    await repo.create_skill("cc-test-site-x", "T", "S")
    para = "Lorem ipsum dolor sit amet. " * 60  # > min-section size, so no merging

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/llms-full.txt":
            body = (
                f"# Section One\n{para}\n\n"
                f"## Section Two\n{para}\n\n"
                f"## Section Three\n{para}\n"
            )
            return httpx.Response(200, text=body)
        return httpx.Response(404, text="not found")

    _mock_client(monkeypatch, handler)

    body = routes.SkillSiteImportIn(skill_id="cc-test-site-x", url="https://example.test/docs")
    result = await routes.import_skill_site_route(body)

    assert result["rung"] == "llms-full"
    assert [d["doc_key"] for d in result["imported"]] == ["section-one", "section-two", "section-three"]
    assert result["skipped"] == []
    assert result["truncated"] is False

    docs = await repo.list_skill_docs("cc-test-site-x")
    assert {d["doc_key"] for d in docs} == {"section-one", "section-two", "section-three"}


@pytestmark_db
@pytest.mark.asyncio
async def test_llms_full_tiny_sections_get_merged(clean_skills, monkeypatch):
    await repo.create_skill("cc-test-site-x", "T", "S")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/llms-full.txt":
            body = "\n\n".join(f"## Tiny {i}\nshort." for i in range(30))
            return httpx.Response(200, text=body)
        return httpx.Response(404, text="not found")

    _mock_client(monkeypatch, handler)

    body = routes.SkillSiteImportIn(skill_id="cc-test-site-x", url="https://example.test/docs")
    result = await routes.import_skill_site_route(body)

    assert result["rung"] == "llms-full"
    # 30 one-line sections must not become 30 docs
    assert len(result["imported"]) < 30


@pytestmark_db
@pytest.mark.asyncio
async def test_sitemap_path_imports_pages_and_skips_empty(clean_skills, monkeypatch):
    await repo.create_skill("cc-test-site-x", "T", "S")
    pages = {
        "https://example.test/docs/a": "<html><body><h1>A</h1><p>content A here, long enough to extract.</p></body></html>",
        "https://example.test/docs/b": "<html><body><h1>B</h1><p>content B here, long enough to extract.</p></body></html>",
        "https://example.test/docs/c": "<html><body></body></html>",  # empty extraction -> skipped
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path in ("/llms-full.txt", "/llms.txt"):
            return httpx.Response(404, text="not found")
        html = pages.get(str(request.url))
        if html is None:
            return httpx.Response(404, text="not found")
        return httpx.Response(200, headers={"content-type": "text/html"}, text=html)

    _mock_client(monkeypatch, handler)
    monkeypatch.setattr(site_import, "sitemap_search", lambda url: list(pages.keys()))

    body = routes.SkillSiteImportIn(skill_id="cc-test-site-x", url="https://example.test/docs")
    result = await routes.import_skill_site_route(body)

    assert result["rung"] == "sitemap"
    assert {d["source_url"] for d in result["imported"]} == {
        "https://example.test/docs/a", "https://example.test/docs/b",
    }
    assert len(result["skipped"]) == 1
    assert result["truncated"] is False


@pytestmark_db
@pytest.mark.asyncio
async def test_sitemap_max_pages_caps_and_reports_truncated(clean_skills, monkeypatch):
    await repo.create_skill("cc-test-site-x", "T", "S")
    urls = [f"https://example.test/docs/p{i}" for i in range(5)]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path in ("/llms-full.txt", "/llms.txt"):
            return httpx.Response(404, text="not found")
        return httpx.Response(
            200, headers={"content-type": "text/html"},
            text="<html><body><h1>Page</h1><p>enough content to extract from this page.</p></body></html>",
        )

    _mock_client(monkeypatch, handler)
    monkeypatch.setattr(site_import, "sitemap_search", lambda url: urls)

    body = routes.SkillSiteImportIn(skill_id="cc-test-site-x", url="https://example.test/docs", max_pages=2)
    result = await routes.import_skill_site_route(body)

    assert result["rung"] == "sitemap"
    assert result["truncated"] is True
    assert len(result["imported"]) == 2
