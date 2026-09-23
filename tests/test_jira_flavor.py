"""Jira Cloud and Jira Data Center are two products (Data Center flavor
design, 2026-09-23).

`CC_JIRA_API_FLAVOR` selects REST v2 vs v3, wiki markup vs ADF, offset vs
cursor search, an unpaginated project list vs `/project/search`, and a
username lead vs an account id — and withholds the endpoints Data Center
simply does not have. Transport is faked throughout; no live Jira.
"""

from __future__ import annotations

import json
import pathlib
import re

import httpx
import pytest

from central_command.config import settings
from central_command.integrations import jira


class FakeResponse:
    def __init__(self, status_code=200, body=None, text=""):
        self.status_code = status_code
        self._body = body if body is not None else {}
        self.text = text or json.dumps(self._body)

    def json(self):
        return self._body


@pytest.fixture(autouse=True)
def native(monkeypatch):
    monkeypatch.setattr(settings, "jira_base_url", "https://jira.example.com")
    monkeypatch.setattr(settings, "jira_email", "bot@example.com")
    monkeypatch.setattr(settings, "jira_api_token", "tok")
    monkeypatch.setattr(settings, "jira_auth_mode", "basic")
    monkeypatch.setattr(settings, "jira_api_flavor", "cloud")


def server(monkeypatch):
    monkeypatch.setattr(settings, "jira_api_flavor", "server")


def _fake_call(monkeypatch, responses):
    calls = []

    async def fake(method, path, json_body=None):
        calls.append((method, path, json_body))
        return responses.pop(0) if responses else FakeResponse(200, {})

    monkeypatch.setattr(jira, "_call", fake)
    return calls


# --- the version segment ------------------------------------------------------


PATHS = ["/myself", "/field", "/issue/TASKS-1", "/search", "/project",
         "/issueLink", "/filter", "/serverInfo"]


@pytest.mark.parametrize("path", PATHS)
def test_every_api_path_is_v2_under_server_and_v3_under_cloud(monkeypatch, path):
    assert jira._api(path) == "/rest/api/3" + path
    server(monkeypatch)
    assert jira._api(path) == "/rest/api/2" + path
    # An explicit flavor argument overrides the setting — the serverInfo probe.
    assert jira._api(path, "cloud") == "/rest/api/3" + path


def test_myself_path_follows_the_flavor_and_the_constant_stays_cloud(monkeypatch):
    assert jira.MYSELF_PATH == "/rest/api/3/myself"
    assert jira._myself_path() == jira.MYSELF_PATH
    server(monkeypatch)
    assert jira._myself_path() == "/rest/api/2/myself"
    assert jira.MYSELF_PATH == "/rest/api/3/myself"   # importers still see Cloud


# --- rich text ----------------------------------------------------------------


def test_rich_text_is_adf_on_cloud_and_a_plain_string_on_data_center(monkeypatch):
    doc = jira._rich("hello\nworld")
    assert isinstance(doc, dict) and doc["type"] == "doc"
    server(monkeypatch)
    assert jira._rich("hello\nworld") == "hello\nworld"


async def test_create_issue_and_add_comment_send_the_flavor_s_shape(monkeypatch):
    calls = _fake_call(monkeypatch, [
        FakeResponse(200, {"key": "TASKS-1"}), FakeResponse(200, {"id": "9"}),
    ])
    await jira.create_issue("TASKS", "S", description="body text")
    await jira.add_comment("TASKS-1", "a comment")
    assert calls[0][1] == "/rest/api/3/issue"
    assert calls[0][2]["fields"]["description"]["type"] == "doc"
    assert calls[1][1] == "/rest/api/3/issue/TASKS-1/comment"
    assert calls[1][2]["body"]["type"] == "doc"

    server(monkeypatch)
    calls = _fake_call(monkeypatch, [
        FakeResponse(200, {"key": "TASKS-2"}), FakeResponse(200, {"id": "10"}),
    ])
    await jira.create_issue("TASKS", "S", description="body text")
    await jira.add_comment("TASKS-1", "a comment")
    assert calls[0][1] == "/rest/api/2/issue"
    assert calls[0][2]["fields"]["description"] == "body text"
    assert calls[1][1] == "/rest/api/2/issue/TASKS-1/comment"
    assert calls[1][2]["body"] == "a comment"


def test_a_data_center_textarea_arrives_as_a_string_and_still_reads(monkeypatch):
    server(monkeypatch)
    assert jira._custom_value("  Saves 3h/week  ") == "Saves 3h/week"
    assert jira._custom_value("") is None


# --- search pagination --------------------------------------------------------


def _server_page(keys, total):
    return FakeResponse(200, {"issues": [{"key": k, "fields": {"summary": k}}
                                         for k in keys], "total": total})


async def test_server_search_pages_by_offset_and_stops_at_total(monkeypatch):
    server(monkeypatch)
    calls = _fake_call(monkeypatch, [
        _server_page(["A-1", "A-2"], 4), _server_page(["A-3", "A-4"], 4),
    ])
    out = await jira.search_issues("project = A")
    assert [c[1] for c in calls] == ["/rest/api/2/search"] * 2
    assert calls[0][2]["startAt"] == 0 and calls[0][2]["maxResults"] == 50
    assert calls[1][2]["startAt"] == 2
    assert "nextPageToken" not in calls[1][2]
    assert out["count"] == 4 and out["truncated"] is False


async def test_server_search_respects_the_ten_page_cap_and_says_it_cut(monkeypatch):
    server(monkeypatch)
    calls = []

    async def fake(method, path, json_body=None):
        calls.append((method, path, json_body))
        start = json_body["startAt"]
        return _server_page([f"A-{start}"], 1000)

    monkeypatch.setattr(jira, "_call", fake)
    out = await jira.search_issues("project = A")
    assert len(calls) == 10          # the page cap, never an unbounded crawl
    assert out["count"] == 10 and out["truncated"] is True


async def test_an_empty_server_page_stops_the_loop(monkeypatch):
    server(monkeypatch)
    calls = _fake_call(monkeypatch, [_server_page([], 99)])
    out = await jira.search_issues("project = A")
    assert len(calls) == 1 and out["count"] == 0 and out["truncated"] is False


# --- project listing & creation ----------------------------------------------


async def test_list_projects_is_one_unpaginated_get_under_server(monkeypatch):
    server(monkeypatch)
    calls = _fake_call(monkeypatch, [FakeResponse(200, [
        {"key": "TASKS", "name": "Tasks", "id": "1", "projectTypeKey": "software"},
        {"key": "OPS", "name": "Ops", "id": "2", "projectTypeKey": "business"},
    ])])
    out = await jira.list_projects()
    assert [c[1] for c in calls] == ["/rest/api/2/project"]
    assert out["count"] == 2 and out["projects"][0]["key"] == "TASKS"


async def test_create_project_names_the_lead_by_username_under_server(monkeypatch):
    server(monkeypatch)
    calls = _fake_call(monkeypatch, [
        FakeResponse(200, {"name": "svc-bot", "displayName": "Service Bot"}),
        FakeResponse(200, {"key": "NEW", "id": "100"}),
    ])
    await jira.create_project("NEW", "New Project")
    assert calls[0][1] == "/rest/api/2/myself"
    assert calls[1][1] == "/rest/api/2/project"
    assert calls[1][2]["lead"] == "svc-bot"
    assert "leadAccountId" not in calls[1][2]


async def test_create_project_fails_loudly_when_the_username_is_missing(monkeypatch):
    server(monkeypatch)
    _fake_call(monkeypatch, [FakeResponse(200, {"accountId": "abc"})])
    with pytest.raises(jira.JiraError, match="username"):
        await jira.create_project("NEW", "New Project")


async def test_create_project_still_sends_an_account_id_under_cloud(monkeypatch):
    calls = _fake_call(monkeypatch, [
        FakeResponse(200, {"accountId": "abc"}), FakeResponse(200, {"key": "NEW"}),
    ])
    await jira.create_project("NEW", "New Project")
    assert calls[1][2]["leadAccountId"] == "abc" and "lead" not in calls[1][2]


# --- what Data Center does not have ------------------------------------------


@pytest.mark.parametrize("call", [
    lambda: jira.list_filters(),
    lambda: jira.list_dashboards(),
    lambda: jira.list_gadgets(),
    lambda: jira.create_dashboard("my dashboard"),
])
async def test_cloud_only_operations_refuse_under_server_without_any_http(
    monkeypatch, call
):
    server(monkeypatch)
    calls = _fake_call(monkeypatch, [])
    with pytest.raises(jira.JiraError, match="not available on Jira Data Center"):
        await call()
    assert calls == []


async def test_create_filter_still_works_under_server(monkeypatch):
    """Data Center HAS `POST /filter` — only the LISTING is missing."""
    server(monkeypatch)
    calls = _fake_call(monkeypatch, [FakeResponse(200, {"id": "7", "name": "f"})])
    out = await jira.create_filter("f", "project = A")
    assert calls[0][1] == "/rest/api/2/filter" and out["ok"]


# --- the serverInfo cross-check ----------------------------------------------

_ORIGINAL_CLIENT = httpx.AsyncClient


def _patch_client(monkeypatch, handler):
    def _factory(**kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return _ORIGINAL_CLIENT(**kwargs)

    monkeypatch.setattr(jira.httpx, "AsyncClient", _factory)
    monkeypatch.setattr(jira, "_auth_verified", False)


def _handler(paths, *, myself_status=200, v2_deployment=None, v3_deployment=None):
    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        paths.append(path)
        if path.endswith("/serverInfo"):
            reported = v2_deployment if "/api/2/" in path else v3_deployment
            body = {"version": "10.3.0"}
            if reported is not None:
                body["deploymentType"] = reported
            return httpx.Response(200, json=body)
        if path.endswith("/myself"):
            return httpx.Response(myself_status, json={"accountId": "abc"})
        return httpx.Response(200, json={"values": [], "isLast": True, "total": 0})

    return handle


async def test_a_data_center_instance_under_cloud_flavor_names_the_switch(monkeypatch):
    paths: list[str] = []
    _patch_client(monkeypatch, _handler(paths, v3_deployment="Server"))
    with pytest.raises(jira.JiraError, match="CC_JIRA_API_FLAVOR"):
        await jira.list_projects()


async def test_a_cloud_instance_under_server_flavor_names_the_switch(monkeypatch):
    server(monkeypatch)
    paths: list[str] = []
    _patch_client(monkeypatch, _handler(paths, v2_deployment="Cloud"))
    with pytest.raises(jira.JiraError, match="CC_JIRA_API_FLAVOR=cloud"):
        await jira.list_projects()


async def test_a_404_on_myself_under_cloud_reports_the_flavor_not_not_found(
    monkeypatch
):
    """The message the operator actually needed: a Data Center instance 404s
    every v3 path, so `authCheck — not found` said nothing useful."""
    paths: list[str] = []
    _patch_client(monkeypatch, _handler(paths, myself_status=404,
                                        v2_deployment="Server"))
    with pytest.raises(jira.JiraError, match="CC_JIRA_API_FLAVOR=server"):
        await jira.list_projects()
    assert "/rest/api/2/serverInfo" in paths


async def test_a_missing_deployment_type_is_not_a_failure(monkeypatch):
    """The check catches a wrong switch; it is not a new way to be down."""
    paths: list[str] = []
    _patch_client(monkeypatch, _handler(paths))       # serverInfo, no deploymentType
    out = await jira.list_projects()
    assert out["ok"] and out["count"] == 0


async def test_a_matching_deployment_type_passes_straight_through(monkeypatch):
    paths: list[str] = []
    _patch_client(monkeypatch, _handler(paths, v3_deployment="Cloud"))
    out = await jira.list_projects()
    assert out["ok"]
    assert paths == ["/rest/api/3/myself", "/rest/api/3/serverInfo",
                     "/rest/api/3/project/search"]


async def test_the_cross_check_runs_once_per_process(monkeypatch):
    paths: list[str] = []
    _patch_client(monkeypatch, _handler(paths, v3_deployment="Cloud"))
    await jira.list_projects()
    await jira.list_projects()
    assert paths.count("/rest/api/3/serverInfo") == 1


# --- the guard ----------------------------------------------------------------


SOURCE = (pathlib.Path(__file__).resolve().parent.parent
          / "central_command" / "integrations" / "jira.py")


def test_no_rest_version_literal_lives_outside_the_api_helper():
    """Every path goes through `_api()`. A literal version segment anywhere
    else is how the client got pinned to Cloud in the first place — one
    hardcoded `/rest/api/3` per call site, 23 of them, and a Data Center
    deployment 404ing on all 23."""
    offenders = [
        f"{n}: {line.strip()}"
        for n, line in enumerate(SOURCE.read_text().splitlines(), 1)
        if re.search(r"/rest/api/[23]/", line)
        and not line.startswith("MYSELF_PATH = ")
    ]
    assert offenders == [], (
        "literal REST version segments outside _api()/MYSELF_PATH: " + "; ".join(offenders)
    )
