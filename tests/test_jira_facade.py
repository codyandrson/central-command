"""Integration test for the n8n Jira façade. Skipped unless the façade is
configured and reachable — so the unit suite stays green without homelab infra.

Read-only by design: it never mutates Jira. The write path is exercised by the
Executor milestone (M3) against a controlled, reversible target."""

import httpx
import pytest

from central_command.config import settings
from central_command.integrations import n8n_facade


def _facade_available() -> bool:
    if not settings.jira_facade_token:
        return False
    try:
        httpx.post(
            settings.n8n_jira_url,
            headers={"x-cc-token": "ping"},
            json={"operation": "searchIssues", "jql": "project = TASKS", "limit": 1},
            timeout=5,
        )
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _facade_available(), reason="Jira façade not configured/reachable"
)


async def test_search_issues_read():
    result = await n8n_facade.search_issues("project = TASKS ORDER BY updated DESC", limit=1)
    assert result["ok"] is True
    assert result["operation"] == "searchIssues"
    assert isinstance(result["issues"], list)


async def test_wrong_token_rejected():
    saved = settings.jira_facade_token
    settings.jira_facade_token = "definitely-wrong"
    try:
        with pytest.raises(n8n_facade.JiraFacadeError):
            await n8n_facade.search_issues("project = TASKS", limit=1)
    finally:
        settings.jira_facade_token = saved
