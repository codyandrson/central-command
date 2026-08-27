"""The Jira client's auth sanity check (R3, 2026-08-26).

A dead/expired token does NOT fail loudly on list-shaped reads: Jira Cloud
answers an ANONYMOUS caller with 200 and an empty page, so `list_projects`
returned `{"projects": [], "count": 0}` and agents reasoned from "this
instance has no projects". Only identity-style endpoints refuse, so `_call`
verifies `/rest/api/3/myself` once per process before any result is returned.
"""

from __future__ import annotations

import httpx
import pytest

from central_command.config import settings
from central_command.integrations import jira

_ORIGINAL_CLIENT = httpx.AsyncClient


def _patch_client(monkeypatch, handler):
    def _factory(**kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return _ORIGINAL_CLIENT(**kwargs)

    monkeypatch.setattr(jira.httpx, "AsyncClient", _factory)


@pytest.fixture(autouse=True)
def native(monkeypatch):
    monkeypatch.setattr(settings, "jira_base_url", "https://example.atlassian.net")
    monkeypatch.setattr(settings, "jira_email", "bot@example.com")
    monkeypatch.setattr(settings, "jira_api_token", "tok")
    monkeypatch.setattr(settings, "jira_auth_mode", "basic")
    monkeypatch.setattr(jira, "_auth_verified", False)


def _handler(paths, *, myself_status=200):
    def handle(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == jira.MYSELF_PATH:
            return httpx.Response(myself_status, json={"accountId": "abc"})
        # The silent-degradation shape: 200 with an empty page.
        return httpx.Response(200, json={"values": [], "isLast": True, "total": 0})

    return handle


async def test_dead_token_raises_loudly_instead_of_reporting_an_empty_instance(monkeypatch):
    paths: list[str] = []
    _patch_client(monkeypatch, _handler(paths, myself_status=401))
    with pytest.raises(jira.JiraError, match="CC_JIRA_EMAIL/CC_JIRA_API_TOKEN"):
        await jira.list_projects()
    assert paths == [jira.MYSELF_PATH]   # the empty list was never fetched, let alone returned


async def test_a_live_token_passes_straight_through(monkeypatch):
    paths: list[str] = []
    _patch_client(monkeypatch, _handler(paths))
    out = await jira.list_projects()
    assert out["ok"] and out["count"] == 0
    assert paths == [jira.MYSELF_PATH, "/rest/api/3/project/search"]


async def test_the_check_runs_once_per_process_not_once_per_call(monkeypatch):
    paths: list[str] = []
    _patch_client(monkeypatch, _handler(paths))
    await jira.list_projects()
    await jira.list_projects()
    await jira.list_filters()
    assert paths.count(jira.MYSELF_PATH) == 1
