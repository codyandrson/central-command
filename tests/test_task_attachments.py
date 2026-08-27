"""Task-create attachments reuse the chat seam exactly (`api/attachments.py`):
documents only, and delivery requires an assignee because an unassigned task
has no resolvable model. A refusal must leave no task row — same
validate-before-the-write invariant as chat's validate-before-the-turn.
"""

from __future__ import annotations

import base64

import pytest
from fastapi import HTTPException

from central_command.api import routes
from central_command.api.routes import TaskCreateIn, create_task
from central_command.config import settings
from central_command.db import repo
from central_command.runtime.spike_model import make_jira_task_model
from tests.conftest import aresolve, needs_pg
from tests.test_attachments import _att, _scanned_pdf

pytestmark = needs_pg


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "executor_mode", "dry_run")
    monkeypatch.setattr(routes, "resolve_model", aresolve(lambda *a, **k: make_jira_task_model()))


async def test_attachment_text_lands_in_the_created_tasks_instructions():
    out = await create_task(
        TaskCreateIn(
            instructions="Review the attached note.",
            agent_id="jira-expert",
            attachments=[_att("notes.txt", "text/plain", b"Renew the Jira licence before 2026-09-01.")],
        )
    )
    task = await repo.get_task(out["task_id"])
    assert "Renew the Jira licence" in task["instructions"]
    assert '<operator-attachment name="notes.txt"' in task["instructions"]


async def test_attachment_without_assignee_is_refused_with_no_task_row():
    before = {t["id"] for t in await repo.list_tasks()}
    with pytest.raises(HTTPException) as exc:
        await create_task(
            TaskCreateIn(
                instructions="Review the attached note.",
                attachments=[_att("notes.txt", "text/plain", b"some content")],
            )
        )
    assert exc.value.status_code == 422
    assert "assignee" in exc.value.detail
    after = {t["id"] for t in await repo.list_tasks()}
    assert after == before


async def test_empty_extraction_is_refused_with_no_task_row():
    before = {t["id"] for t in await repo.list_tasks()}
    with pytest.raises(HTTPException) as exc:
        await create_task(
            TaskCreateIn(
                instructions="Review the attached contract.",
                agent_id="jira-expert",
                attachments=[_att("contract.pdf", "application/pdf", _scanned_pdf())],
            )
        )
    assert exc.value.status_code == 422
    assert "scan" in exc.value.detail.lower()
    after = {t["id"] for t in await repo.list_tasks()}
    assert after == before


async def test_image_attachment_is_refused_outright():
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
        "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    )
    before = {t["id"] for t in await repo.list_tasks()}
    with pytest.raises(HTTPException) as exc:
        await create_task(
            TaskCreateIn(
                instructions="Look at this screenshot.",
                agent_id="jira-expert",
                attachments=[_att("shot.png", "image/png", png)],
            )
        )
    assert exc.value.status_code == 422
    assert "image" in exc.value.detail.lower()
    after = {t["id"] for t in await repo.list_tasks()}
    assert after == before
