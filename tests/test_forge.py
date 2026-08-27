"""Git-hosting read integration (work-transition compatibility design, "Git
hosting" bullet): instance-string parsing, per-kind auth headers, normalized
read shapes across GitHub and GitLab (mocked transport, no live network by
default), and the unconfigured-integration message. A guarded live smoke test
(CC_LIVE_FORGE_TESTS=1) proves the real wire against public GitHub.
"""

from __future__ import annotations

import os

import httpx
import pytest

from central_command.config import settings
from central_command.integrations import forge

_ORIGINAL_CLIENT = httpx.AsyncClient


def _patch_client(monkeypatch, handler, *, capture: dict | None = None):
    def _factory(**kwargs):
        if capture is not None:
            capture.update(kwargs)
        kwargs["transport"] = httpx.MockTransport(handler)
        return _ORIGINAL_CLIENT(**kwargs)

    monkeypatch.setattr(forge.httpx, "AsyncClient", _factory)


# --- instance-string parsing ---------------------------------------------------


def test_parses_one_instance():
    out = forge._parse_instances("github=github:https://api.github.com")
    assert set(out) == {"github"}
    assert out["github"].kind == "github"
    assert out["github"].base_url == "https://api.github.com"


def test_parses_multiple_instances_incl_multi_gitlab():
    raw = ("github=github:https://api.github.com,"
           "work-gl=gitlab:https://gitlab.example.com,"
           "mirror-gl=gitlab:https://gitlab-mirror.example.com")
    out = forge._parse_instances(raw)
    assert set(out) == {"github", "work-gl", "mirror-gl"}
    assert out["work-gl"].kind == "gitlab"
    assert out["mirror-gl"].base_url == "https://gitlab-mirror.example.com"


def test_empty_string_is_no_instances():
    assert forge._parse_instances("") == {}
    assert forge._parse_instances("   ") == {}


@pytest.mark.parametrize("bad", [
    "no-equals-sign",
    "name=nocolon",
    "name=badkind:https://example.com",
    "=github:https://example.com",
    "name=github:",
])
def test_bad_entry_fails_loud_naming_itself(bad):
    with pytest.raises(forge.ForgeError, match="malformed|unknown kind") as exc_info:
        forge._parse_instances(bad)
    assert bad in str(exc_info.value)


def test_duplicate_name_fails_loud():
    with pytest.raises(forge.ForgeError, match="dupe"):
        forge._parse_instances("dupe=github:https://a.example.com,dupe=gitlab:https://b.example.com")


# --- auth header selection per kind --------------------------------------------


def test_github_uses_bearer(monkeypatch):
    monkeypatch.setenv("CC_FORGE_TOKEN_GH", "tok-gh")
    inst = forge.Instance("gh", "github", "https://api.github.com")
    assert forge._auth_headers(inst) == {"Authorization": "Bearer tok-gh"}


def test_gitlab_uses_private_token_header(monkeypatch):
    # Verified against docs.gitlab.com/api/rest/authentication (2026-08-21):
    # "the recommended approach is passing the token via the PRIVATE-TOKEN
    # header" — GitLab's own curl examples use PRIVATE-TOKEN, not Bearer.
    monkeypatch.setenv("CC_FORGE_TOKEN_WORK_GL", "tok-gl")
    inst = forge.Instance("work-gl", "gitlab", "https://gitlab.example.com")
    assert forge._auth_headers(inst) == {"PRIVATE-TOKEN": "tok-gl"}


def test_no_token_means_no_auth_header(monkeypatch):
    monkeypatch.delenv("CC_FORGE_TOKEN_UNSET", raising=False)
    inst = forge.Instance("unset", "github", "https://api.github.com")
    assert forge._auth_headers(inst) == {}


def test_name_uppercased_and_dash_becomes_underscore(monkeypatch):
    monkeypatch.setenv("CC_FORGE_TOKEN_WORK_GL", "tok")
    assert forge._token("work-gl") == "tok"


# --- unconfigured integration ---------------------------------------------------


async def test_unconfigured_gives_a_clear_message(monkeypatch):
    monkeypatch.setattr(settings, "forge_instances", "")
    assert forge.configured() is False
    with pytest.raises(forge.ForgeError, match="no forge instances configured"):
        await forge.search_repos("github", "whatever")
    out = await forge.list_instances()
    assert out == {
        "ok": True, "kind": "forge", "operation": "listInstances",
        "instances": [], "count": 0,
        "note": "no forge instances configured (CC_FORGE_INSTANCES is unset)",
    }


async def test_unknown_instance_name_fails_loud(monkeypatch):
    monkeypatch.setattr(settings, "forge_instances", "github=github:https://api.github.com")
    with pytest.raises(forge.ForgeError, match="unknown forge instance"):
        await forge.search_repos("no-such-instance", "x")


# --- normalized shapes: search_repos -------------------------------------------


@pytest.fixture(autouse=True)
def two_instances(monkeypatch):
    monkeypatch.setattr(
        settings, "forge_instances",
        "github=github:https://api.github.com,gl=gitlab:https://gitlab.example.com",
    )
    monkeypatch.delenv("CC_FORGE_TOKEN_GITHUB", raising=False)
    monkeypatch.delenv("CC_FORGE_TOKEN_GL", raising=False)


async def test_search_repos_normalizes_github_and_gitlab(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if "api.github.com" in str(request.url):
            return httpx.Response(200, json={"items": [
                {"full_name": "octo/hello", "description": "desc", "html_url": "https://github.com/octo/hello",
                 "default_branch": "main"}]})
        return httpx.Response(200, json=[
            {"path_with_namespace": "group/hello", "description": "desc",
             "web_url": "https://gitlab.example.com/group/hello", "default_branch": "main"}])

    _patch_client(monkeypatch, handler)
    gh = await forge.search_repos("github", "hello")
    gl = await forge.search_repos("gl", "hello")
    expected_row = {"full_name": "octo/hello", "description": "desc",
                     "url": "https://github.com/octo/hello", "default_branch": "main"}
    assert gh["repos"] == [expected_row]
    assert gl["repos"] == [{"full_name": "group/hello", "description": "desc",
                            "url": "https://gitlab.example.com/group/hello",
                            "default_branch": "main"}]
    assert set(gh["repos"][0]) == set(gl["repos"][0])  # identical shape


async def test_read_file_normalizes_and_truncates(monkeypatch):
    big = "x" * (forge.MAX_FILE_CHARS + 500)

    def handler(request: httpx.Request) -> httpx.Response:
        if "api.github.com" in str(request.url):
            return httpx.Response(200, headers={"content-type": "text/plain"}, text=big)
        return httpx.Response(200, text=big)

    _patch_client(monkeypatch, handler)
    gh = await forge.read_file("github", "octo/hello", "README.md")
    gl = await forge.read_file("gl", "group/hello", "README.md")
    for out in (gh, gl):
        f = out["file"]
        assert f["truncated"] is True
        assert len(f["content"]) < len(big)
        assert "TRUNCATED" in f["content"]
    assert set(gh["file"]) == set(gl["file"])


async def test_issues_normalize_and_prs_excluded_on_github(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if "api.github.com" in str(request.url):
            return httpx.Response(200, json=[
                {"number": 1, "title": "bug", "state": "open",
                 "user": {"login": "alice"}, "html_url": "https://github.com/octo/hello/issues/1",
                 "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-02T00:00:00Z"},
                {"number": 2, "title": "a pr", "state": "open", "pull_request": {}},
            ])
        return httpx.Response(200, json=[
            {"iid": 1, "title": "bug", "state": "opened",
             "author": {"username": "alice"}, "web_url": "https://gitlab.example.com/group/hello/-/issues/1",
             "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-02T00:00:00Z"},
        ])

    _patch_client(monkeypatch, handler)
    gh = await forge.list_issues("github", "octo/hello")
    gl = await forge.list_issues("gl", "group/hello")
    assert gh["count"] == 1  # the PR-shaped row is excluded
    assert gh["issues"][0]["number"] == 1
    assert gh["issues"][0]["author"] == "alice"
    assert gl["issues"][0]["author"] == "alice"
    assert set(gh["issues"][0]) == set(gl["issues"][0])


async def test_merge_requests_normalize(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if "api.github.com" in str(request.url):
            return httpx.Response(200, json=[
                {"number": 5, "title": "fix", "state": "open", "user": {"login": "bob"},
                 "html_url": "https://github.com/octo/hello/pull/5",
                 "head": {"ref": "feature"}, "base": {"ref": "main"},
                 "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-02T00:00:00Z"},
            ])
        return httpx.Response(200, json=[
            {"iid": 5, "title": "fix", "state": "opened", "author": {"username": "bob"},
             "web_url": "https://gitlab.example.com/group/hello/-/merge_requests/5",
             "source_branch": "feature", "target_branch": "main",
             "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-02T00:00:00Z"},
        ])

    _patch_client(monkeypatch, handler)
    gh = await forge.list_merge_requests("github", "octo/hello")
    gl = await forge.list_merge_requests("gl", "group/hello")
    assert gh["merge_requests"][0]["source_branch"] == "feature"
    assert gl["merge_requests"][0]["source_branch"] == "feature"
    assert set(gh["merge_requests"][0]) == set(gl["merge_requests"][0])


async def test_commits_normalize(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if "api.github.com" in str(request.url):
            return httpx.Response(200, json=[
                {"sha": "abc123", "commit": {"message": "fix bug", "author": {"name": "alice", "date": "2026-01-01T00:00:00Z"}},
                 "html_url": "https://github.com/octo/hello/commit/abc123"},
            ])
        return httpx.Response(200, json=[
            {"id": "abc123", "message": "fix bug", "author_name": "alice",
             "created_at": "2026-01-01T00:00:00Z",
             "web_url": "https://gitlab.example.com/group/hello/-/commit/abc123"},
        ])

    _patch_client(monkeypatch, handler)
    gh = await forge.list_commits("github", "octo/hello")
    gl = await forge.list_commits("gl", "group/hello")
    assert gh["commits"][0]["sha"] == "abc123"
    assert gl["commits"][0]["sha"] == "abc123"
    assert set(gh["commits"][0]) == set(gl["commits"][0])


async def test_get_issue_and_get_merge_request_carry_body(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "api.github.com" in url and "/issues/" in url:
            return httpx.Response(200, json={"number": 1, "title": "bug", "state": "open",
                                              "body": "details", "user": {}, "html_url": "u"})
        if "api.github.com" in url and "/pulls/" in url:
            return httpx.Response(200, json={"number": 1, "title": "fix", "state": "open",
                                              "body": "pr details", "user": {}, "html_url": "u",
                                              "head": {}, "base": {}})
        if "/issues/" in url:
            return httpx.Response(200, json={"iid": 1, "title": "bug", "state": "opened",
                                              "description": "details", "author": {}, "web_url": "u"})
        return httpx.Response(200, json={"iid": 1, "title": "fix", "state": "opened",
                                          "description": "pr details", "author": {}, "web_url": "u"})

    _patch_client(monkeypatch, handler)
    gh_issue = await forge.get_issue("github", "octo/hello", 1)
    gl_issue = await forge.get_issue("gl", "group/hello", 1)
    assert gh_issue["issue"]["body"] == "details"
    assert gl_issue["issue"]["body"] == "details"

    gh_mr = await forge.get_merge_request("github", "octo/hello", 1)
    gl_mr = await forge.get_merge_request("gl", "group/hello", 1)
    assert gh_mr["merge_request"]["body"] == "pr details"
    assert gl_mr["merge_request"]["body"] == "pr details"


async def test_not_found_is_a_clear_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="Not Found")

    _patch_client(monkeypatch, handler)
    with pytest.raises(forge.ForgeError, match="not found"):
        await forge.get_issue("github", "octo/hello", 999)


# --- live smoke (guarded, skipped by default) -----------------------------------


async def test_live_smoke_unauthenticated_github_read(monkeypatch):
    if os.getenv("CC_LIVE_FORGE_TESTS") != "1":
        pytest.skip("set CC_LIVE_FORGE_TESTS=1 to run a real, unauthenticated "
                    "GitHub read")
    monkeypatch.setattr(settings, "forge_instances", "github=github:https://api.github.com")
    monkeypatch.delenv("CC_FORGE_TOKEN_GITHUB", raising=False)
    out = await forge.read_file("github", "octocat/Hello-World", "README")
    assert out["ok"] is True
    assert out["file"]["content"]
