"""Native Confluence client (D-confluence): auth-mode switch, the cloud/server
flavor seam, envelope shape, one bounded 429 retry, row normalization, and
that the `confluence-read` pack's tools all exist (mirrors tests/test_forge.py's
approach — mocked transport, no live network by default).
"""

from __future__ import annotations

import httpx
import pytest

from central_command.config import settings
from central_command.integrations import confluence
from central_command.runtime import packs
from central_command.runtime import tools as tools_mod

_ORIGINAL_CLIENT = httpx.AsyncClient


def _patch_client(monkeypatch, handler, *, capture: dict | None = None):
    def _factory(**kwargs):
        if capture is not None:
            capture.update(kwargs)
        kwargs["transport"] = httpx.MockTransport(handler)
        return _ORIGINAL_CLIENT(**kwargs)

    monkeypatch.setattr(confluence.httpx, "AsyncClient", _factory)


@pytest.fixture(autouse=True)
def configured_cloud(monkeypatch):
    monkeypatch.setattr(settings, "confluence_base_url", "https://example.atlassian.net")
    monkeypatch.setattr(settings, "confluence_email", "bot@example.com")
    monkeypatch.setattr(settings, "confluence_api_token", "tok")
    monkeypatch.setattr(settings, "confluence_auth_mode", "basic")
    monkeypatch.setattr(settings, "confluence_api_flavor", "cloud")


# --- configured() / unconfigured message ---------------------------------------


async def test_unconfigured_gives_a_clear_message(monkeypatch):
    monkeypatch.setattr(settings, "confluence_base_url", "")
    assert confluence.configured() is False
    with pytest.raises(confluence.ConfluenceError, match="not configured"):
        await confluence.list_spaces()


def test_bearer_mode_needs_no_email():
    settings.confluence_auth_mode = "bearer"
    settings.confluence_email = ""
    try:
        assert confluence.configured() is True
    finally:
        settings.confluence_auth_mode = "basic"
        settings.confluence_email = "bot@example.com"


# --- auth mode switch ------------------------------------------------------


def test_basic_auth_kwargs():
    assert confluence._auth_kwargs() == {"auth": ("bot@example.com", "tok")}


def test_bearer_auth_kwargs(monkeypatch):
    monkeypatch.setattr(settings, "confluence_auth_mode", "bearer")
    assert confluence._auth_kwargs() == {"headers": {"Authorization": "Bearer tok"}}


async def test_basic_mode_sends_auth_tuple_on_the_wire(monkeypatch):
    capture: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("authorization", "").startswith("Basic ")
        return httpx.Response(200, json={"results": []})

    _patch_client(monkeypatch, handler, capture=capture)
    await confluence.list_spaces()
    assert capture.get("auth") == ("bot@example.com", "tok")


async def test_bearer_mode_sends_authorization_header(monkeypatch):
    monkeypatch.setattr(settings, "confluence_auth_mode", "bearer")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("authorization") == "Bearer tok"
        return httpx.Response(200, json={"results": []})

    _patch_client(monkeypatch, handler)
    await confluence.list_spaces()


# --- flavor seam: URL construction ----------------------------------------


async def test_cloud_uses_v2_paths_for_spaces_and_pages(monkeypatch):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if "/spaces" in request.url.path:
            return httpx.Response(200, json={"results": []})
        return httpx.Response(200, json={"id": "1", "title": "Home",
                                          "body": {"storage": {"value": "<p>hi</p>"}}})

    _patch_client(monkeypatch, handler)
    await confluence.list_spaces()
    await confluence.get_page("1")
    assert seen[0] == "/wiki/api/v2/spaces"
    assert seen[1] == "/wiki/api/v2/pages/1"


async def test_server_uses_v1_paths_for_spaces_and_pages(monkeypatch):
    monkeypatch.setattr(settings, "confluence_api_flavor", "server")
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if "/space" in request.url.path:
            return httpx.Response(200, json={"results": []})
        return httpx.Response(200, json={"id": "1", "title": "Home",
                                          "body": {"storage": {"value": "<p>hi</p>"}}})

    _patch_client(monkeypatch, handler)
    await confluence.list_spaces()
    await confluence.get_page("1")
    assert seen[0] == "/rest/api/space"
    assert seen[1] == "/rest/api/content/1"


async def test_search_is_always_v1_on_cloud(monkeypatch):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json={"results": []})

    _patch_client(monkeypatch, handler)
    await confluence.search("type=page")
    assert seen[0] == "/wiki/rest/api/search"


async def test_search_is_v1_on_server_too(monkeypatch):
    monkeypatch.setattr(settings, "confluence_api_flavor", "server")
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json={"results": []})

    _patch_client(monkeypatch, handler)
    await confluence.search("type=page")
    assert seen[0] == "/rest/api/search"


# --- validation --------------------------------------------------------------


async def test_bad_page_id_is_rejected_before_any_request(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request should be made")

    _patch_client(monkeypatch, handler)
    with pytest.raises(confluence.ConfluenceError, match="page_id"):
        await confluence.get_page("not-a-number")


async def test_empty_cql_is_rejected():
    with pytest.raises(confluence.ConfluenceError, match="cql"):
        await confluence.search("   ")


# --- envelope on error status --------------------------------------------------


async def test_not_found_is_a_clear_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="No page found")

    _patch_client(monkeypatch, handler)
    with pytest.raises(confluence.ConfluenceError, match="not found"):
        await confluence.get_page("123")


async def test_auth_failure_names_the_settings(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="Unauthorized")

    _patch_client(monkeypatch, handler)
    with pytest.raises(confluence.ConfluenceError, match="CC_CONFLUENCE"):
        await confluence.list_spaces()


# --- cloud next-links are /wiki-context-relative (measured live 2026-08-21) ----


async def test_cloud_next_link_is_rejoined_under_wiki_context(monkeypatch):
    """Cloud emits `_links.next` relative to the /wiki context
    ("/rest/api/search?..."); joining it onto the bare base_url lands on the
    Jira root. `_paginate` must re-prefix /wiki — this was a live-found bug."""
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if len(seen) == 1:
            return httpx.Response(200, json={
                "results": [{"content": {"id": "1", "title": "A"}}],
                "_links": {"next": "/rest/api/search?cursor=abc&cql=type%3Dpage"},
            })
        return httpx.Response(200, json={
            "results": [{"content": {"id": "2", "title": "B"}}], "_links": {},
        })

    _patch_client(monkeypatch, handler)
    out = await confluence.search("type=page", limit=5)
    assert [r["id"] for r in out["results"]] == ["1", "2"]
    assert seen == ["/wiki/rest/api/search", "/wiki/rest/api/search"]


# --- 429 retry honored once ----------------------------------------------------


async def test_429_is_retried_once_then_succeeds(monkeypatch):
    calls = {"n": 0}
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(confluence, "_sleep", fake_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "2"}, text="slow down")
        return httpx.Response(200, json={"results": []})

    _patch_client(monkeypatch, handler)
    out = await confluence.list_spaces()
    assert out["ok"] is True
    assert calls["n"] == 2          # exactly one retry
    assert slept == [2.0]           # Retry-After respected


async def test_429_that_persists_is_the_final_error(monkeypatch):
    async def fake_sleep(seconds):
        return None

    monkeypatch.setattr(confluence, "_sleep", fake_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="slow down")

    _patch_client(monkeypatch, handler)
    with pytest.raises(confluence.ConfluenceError, match="rate limited"):
        await confluence.list_spaces()


# --- row normalization ----------------------------------------------------------


async def test_get_page_normalizes_storage_body_and_url(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "id": "42", "title": "Roadmap", "status": "current",
            "spaceId": "9", "version": {"number": 3},
            "body": {"storage": {"value": "<p>plan</p>", "representation": "storage"}},
            "_links": {"webui": "/spaces/OPS/pages/42/Roadmap"},
        })

    _patch_client(monkeypatch, handler)
    out = await confluence.get_page("42")
    page = out["page"]
    assert page["id"] == "42"
    assert page["title"] == "Roadmap"
    assert page["version"] == 3
    assert page["body"] == "<p>plan</p>"
    assert page["url"] == "https://example.atlassian.net/wiki/spaces/OPS/pages/42/Roadmap"


async def test_list_spaces_normalizes_rows(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": [
            {"id": "9", "key": "OPS", "name": "Operations", "type": "global",
             "status": "current", "_links": {"webui": "/spaces/OPS"}},
        ]})

    _patch_client(monkeypatch, handler)
    out = await confluence.list_spaces()
    assert out["spaces"] == [{
        "id": "9", "key": "OPS", "name": "Operations", "type": "global",
        "status": "current", "url": "https://example.atlassian.net/wiki/spaces/OPS",
    }]


# --- confluence-read pack: every tool_name is real ------------------------------


def test_confluence_read_pack_tools_all_exist():
    pack = packs.PACKS["confluence-read"]
    assert pack.tool_names, "confluence-read pack lists no tools"
    for tool_name in pack.tool_names:
        fn = getattr(tools_mod, tool_name, None)
        assert fn is not None, f"{tool_name!r} is not defined in runtime/tools.py"
        assert fn.__module__ == "central_command.runtime.tools"
    assert not any(t.startswith("propose_") for t in pack.tool_names)  # Phase 1: read-only


# --- Phase 2 writes: URL/flavor construction ------------------------------------


async def test_create_page_cloud_resolves_key_to_id_and_posts_v2(monkeypatch):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.url.path == "/wiki/rest/api/space/OPS":
            return httpx.Response(200, json={"id": "9"})
        assert request.url.path == "/wiki/api/v2/pages"
        body = request.read()
        import json as _json
        payload = _json.loads(body)
        assert payload["spaceId"] == "9"
        assert payload["title"] == "New Page"
        assert payload["body"] == {"representation": "storage", "value": "<p>hi</p>"}
        return httpx.Response(200, json={"id": "55", "title": "New Page",
                                          "spaceId": "9", "version": {"number": 1}})

    _patch_client(monkeypatch, handler)
    out = await confluence.create_page("OPS", "New Page", "<p>hi</p>")
    assert out["ok"] is True
    assert out["page"]["id"] == "55"
    assert seen[0] == ("GET", "/wiki/rest/api/space/OPS")
    assert seen[1] == ("POST", "/wiki/api/v2/pages")


async def test_create_page_cloud_skips_resolution_for_numeric_space_id(monkeypatch):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json={"id": "55", "title": "New Page",
                                          "spaceId": "9", "version": {"number": 1}})

    _patch_client(monkeypatch, handler)
    await confluence.create_page("9", "New Page", "<p>hi</p>")
    assert seen == ["/wiki/api/v2/pages"]


async def test_create_page_server_posts_v1_content_with_space_key(monkeypatch):
    monkeypatch.setattr(settings, "confluence_api_flavor", "server")
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        import json as _json
        payload = _json.loads(request.read())
        assert payload["type"] == "page"
        assert payload["space"] == {"key": "OPS"}
        return httpx.Response(200, json={"id": "55", "title": "New Page",
                                          "space": {"key": "OPS"}, "version": {"number": 1}})

    _patch_client(monkeypatch, handler)
    out = await confluence.create_page("OPS", "New Page", "<p>hi</p>")
    assert seen == [("POST", "/rest/api/content")]
    assert out["page"]["id"] == "55"


async def test_create_page_rejects_oversized_body():
    with pytest.raises(confluence.ConfluenceError, match="body_storage"):
        await confluence.create_page("OPS", "T", "x" * (confluence.MAX_BODY_CHARS + 1))


async def test_update_page_refuses_a_stale_expected_version(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        # get_page for the version check
        return httpx.Response(200, json={
            "id": "42", "title": "Roadmap", "spaceId": "9",
            "version": {"number": 5},
            "body": {"storage": {"value": "<p>current</p>"}},
        })

    _patch_client(monkeypatch, handler)
    with pytest.raises(confluence.ConfluenceError, match="stale version"):
        await confluence.update_page("42", "Roadmap", "<p>new</p>", expected_version=3)


async def test_update_page_submits_version_plus_one_when_current(monkeypatch):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, json={
                "id": "42", "title": "Roadmap", "spaceId": "9",
                "version": {"number": 5},
                "body": {"storage": {"value": "<p>current</p>"}},
            })
        assert request.method == "PUT"
        import json as _json
        payload = _json.loads(request.read())
        assert payload["version"] == {"number": 6}
        return httpx.Response(200, json={"id": "42", "title": "Roadmap",
                                          "spaceId": "9", "version": {"number": 6}})

    _patch_client(monkeypatch, handler)
    out = await confluence.update_page("42", "Roadmap", "<p>new</p>", expected_version=5)
    assert out["page"]["version"] == 6


async def test_move_page_repoints_parent_and_bumps_version(monkeypatch):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, json={
                "id": "42", "title": "Roadmap", "spaceId": "9",
                "version": {"number": 2},
                "body": {"storage": {"value": "<p>x</p>"}},
            })
        import json as _json
        payload = _json.loads(request.read())
        assert payload["parentId"] == "7"
        assert payload["version"] == {"number": 3}
        return httpx.Response(200, json={"id": "42", "title": "Roadmap",
                                          "spaceId": "9", "version": {"number": 3}})

    _patch_client(monkeypatch, handler)
    out = await confluence.move_page("42", "7")
    assert out["page"]["version"] == 3


async def test_trash_page_cloud_deletes_v2_pages(monkeypatch):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        return httpx.Response(204)

    _patch_client(monkeypatch, handler)
    out = await confluence.trash_page("42")
    assert seen == [("DELETE", "/wiki/api/v2/pages/42")]
    assert out["page_id"] == "42"


async def test_trash_page_server_deletes_v1_content(monkeypatch):
    monkeypatch.setattr(settings, "confluence_api_flavor", "server")
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        return httpx.Response(204)

    _patch_client(monkeypatch, handler)
    await confluence.trash_page("42")
    assert seen == [("DELETE", "/rest/api/content/42")]


async def test_upload_attachment_sends_multipart_with_no_check_header(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/wiki/rest/api/content/42/child/attachment"
        assert request.headers.get("x-atlassian-token") == "no-check"
        assert b'name="file"' in request.content
        assert b"notes.txt" in request.content
        return httpx.Response(200, json={"results": [
            {"id": "att1", "title": "notes.txt", "metadata": {"mediaType": "text/plain"}},
        ]})

    _patch_client(monkeypatch, handler)
    out = await confluence.upload_attachment("42", "notes.txt", b"hello")
    assert out["attachment"]["id"] == "att1"


async def test_upload_attachment_rejects_oversized_content():
    with pytest.raises(confluence.ConfluenceError, match="exceeds"):
        await confluence.upload_attachment(
            "42", "big.bin", b"x" * (confluence.MAX_ATTACHMENT_BYTES + 1)
        )


async def test_upload_attachment_rejects_non_bytes_content():
    with pytest.raises(confluence.ConfluenceError, match="bytes"):
        await confluence.upload_attachment("42", "notes.txt", "not bytes")


async def test_set_labels_posts_v1_add_only(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/wiki/rest/api/content/42/label"
        import json as _json
        payload = _json.loads(request.read())
        assert payload == [{"prefix": "global", "name": "roadmap"}]
        return httpx.Response(200, json={"results": [{"id": "l1", "name": "roadmap"}]})

    _patch_client(monkeypatch, handler)
    out = await confluence.set_labels("42", ["roadmap"])
    assert out["labels"] == [{"id": "l1", "name": "roadmap", "prefix": None}]


async def test_set_labels_rejects_empty_list():
    with pytest.raises(confluence.ConfluenceError, match="labels"):
        await confluence.set_labels("42", [])


async def test_create_space_rejects_a_bad_key():
    with pytest.raises(confluence.ConfluenceError, match="space key"):
        await confluence.create_space("bad key!", "Name")


async def test_create_space_cloud_posts_v2_spaces(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/wiki/api/v2/spaces"
        return httpx.Response(200, json={"id": "9", "key": "OPS", "name": "Operations"})

    _patch_client(monkeypatch, handler)
    out = await confluence.create_space("OPS", "Operations")
    assert out["space"]["key"] == "OPS"


async def test_create_space_server_posts_v1_space(monkeypatch):
    monkeypatch.setattr(settings, "confluence_api_flavor", "server")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/rest/api/space"
        return httpx.Response(200, json={"id": "9", "key": "OPS", "name": "Operations"})

    _patch_client(monkeypatch, handler)
    out = await confluence.create_space("OPS", "Operations")
    assert out["space"]["key"] == "OPS"


# --- Phase 2 governance: pack/registry parity for the new writes ----------------


def test_confluence_propose_pack_capabilities_are_all_registered():
    from central_command.gateway.capabilities import gated_write_names
    from central_command.gateway import executor as executor_mod

    names = {c.name for c in packs.PACKS["confluence-propose"].capabilities}
    assert names == {
        "confluence.create_page", "confluence.update_page", "confluence.move_page",
        "confluence.trash_page", "confluence.upload_attachment", "confluence.set_labels",
    }
    assert names <= gated_write_names()
    assert names <= set(executor_mod.HANDLERS)


def test_confluence_space_propose_pack_is_isolated():
    pack = packs.PACKS["confluence-space-propose"]
    assert [c.name for c in pack.capabilities] == ["confluence.create_space"]
