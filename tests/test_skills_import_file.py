"""Tests for `POST /api/skills/import-file` — a single document, uploaded as
a file (PDF/docx/pptx/xlsx/…) and converted to markdown server-side via
markitdown, into an EXISTING skill.

Same direct-handler-call pattern as `test_skills_import_doc.py` (no
TestClient anywhere in this suite). `UploadFile` is built by hand around a
BytesIO, the same shape FastAPI hands the route on a real multipart request.
"""

from __future__ import annotations

import io

import pytest
from fastapi import HTTPException, UploadFile

from central_command.api import routes
from central_command.db import repo
from tests.conftest import needs_pg

pytestmark_db = needs_pg


def _upload(data: bytes, filename: str) -> UploadFile:
    return UploadFile(file=io.BytesIO(data), filename=filename)


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
async def test_unknown_skill_is_404(clean_skills):
    with pytest.raises(HTTPException) as exc:
        await routes.import_skill_file_route(
            skill_id="cc-test-file-import-missing", doc_key="api",
            title=None, describes=None, file=_upload(b"hello", "notes.txt"),
        )
    assert exc.value.status_code == 404


@pytestmark_db
@pytest.mark.asyncio
async def test_file_import_lands_a_current_doc_version_with_filename_title(clean_skills):
    await repo.create_skill("cc-test-file-import", "T", "S")
    doc = await routes.import_skill_file_route(
        skill_id="cc-test-file-import", doc_key="api",
        title=None, describes="v1",
        file=_upload(b"# Endpoints\nGET /thing", "my-notes.md"),
    )
    assert doc["version"] == 1
    assert doc["is_current"] is True
    assert doc["kind"] == "reference"
    assert doc["source_url"] is None
    assert doc["added_by"] == "operator"
    assert doc["title"] == "my-notes"  # defaults to the filename stem
    assert "GET /thing" in doc["content"]

    docs = await repo.list_skill_docs("cc-test-file-import")
    assert [d["doc_key"] for d in docs] == ["api"]


@pytestmark_db
@pytest.mark.asyncio
async def test_guidance_doc_key_is_kind_guidance(clean_skills):
    await repo.create_skill("cc-test-file-import", "T", "S")
    doc = await routes.import_skill_file_route(
        skill_id="cc-test-file-import", doc_key="guidance",
        title="Guide", describes=None,
        file=_upload(b"# Guide", "guide.md"),
    )
    assert doc["kind"] == "guidance"


@pytestmark_db
@pytest.mark.asyncio
async def test_oversize_upload_is_422(clean_skills, monkeypatch):
    await repo.create_skill("cc-test-file-import", "T", "S")
    monkeypatch.setattr(routes, "MAX_IMPORT_FILE_BYTES", 10)
    with pytest.raises(HTTPException) as exc:
        await routes.import_skill_file_route(
            skill_id="cc-test-file-import", doc_key="api",
            title=None, describes=None,
            file=_upload(b"this is way more than ten bytes", "big.txt"),
        )
    assert exc.value.status_code == 422


@pytestmark_db
@pytest.mark.asyncio
async def test_conversion_failure_is_422_carrying_markitdowns_error(clean_skills, monkeypatch):
    await repo.create_skill("cc-test-file-import", "T", "S")

    from markitdown import MarkItDown
    from markitdown._exceptions import UnsupportedFormatException

    def _boom(self, stream, *, file_extension=None, **kwargs):
        raise UnsupportedFormatException("no converter attempted a conversion")

    monkeypatch.setattr(MarkItDown, "convert_stream", _boom)

    with pytest.raises(HTTPException) as exc:
        await routes.import_skill_file_route(
            skill_id="cc-test-file-import", doc_key="api",
            title=None, describes=None,
            file=_upload(b"\x00\x01garbage", "weird.bin"),
        )
    assert exc.value.status_code == 422
    assert "no converter attempted a conversion" in exc.value.detail
