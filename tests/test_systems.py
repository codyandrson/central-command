"""Wire-shape test for GET /api/systems — the cockpit's launchpad.

Health checks (HTTP GET, TCP connect, Graphiti's get_status) are monkeypatched
so the test needs no live services and no database. What it actually proves:
every entry carries the required keys, no credential VALUE ever reaches the
payload, and CC_N8N_UI_URL flows through to the n8n entry's url.
"""

from __future__ import annotations

from central_command.api import systems
from central_command.config import settings

_SENTINEL = "SENTINEL_CREDENTIAL_VALUE_MUST_NOT_LEAK"


async def test_systems_wire_shape_and_no_credential_leak(monkeypatch):
    monkeypatch.setattr(settings, "llm_proxy_admin_key", _SENTINEL)
    # A DSN carries its password — the first cut leaked it as `health_url`.
    monkeypatch.setattr(settings, "database_url", f"postgresql://u:{_SENTINEL}@127.0.0.1:5442/db")
    monkeypatch.setattr(settings, "litellm_db_url", f"postgresql://u:{_SENTINEL}@127.0.0.1:5443/db")
    monkeypatch.setattr(settings, "llm_proxy_ui_url", "https://litellm.tail.example/ui")
    monkeypatch.setattr(settings, "n8n_ui_url", "https://n8n.tail.example/")
    monkeypatch.setattr(settings, "vlogs_ui_url", "https://vlogs.tail.example/")
    monkeypatch.setattr(settings, "neo4j_browser_url", "https://neo4j.tail.example/")
    monkeypatch.setattr(settings, "jira_base_url", "")
    monkeypatch.setattr(settings, "confluence_base_url", "")

    async def fake_http_check(url):
        return ("up", 12.3) if url else ("unknown", None)

    async def fake_tcp_check(url, default_port):
        return ("up", 4.5) if url else ("unknown", None)

    async def fake_neo4j_status():
        return ("up", None)

    monkeypatch.setattr(systems, "_http_check", fake_http_check)
    monkeypatch.setattr(systems, "_tcp_check", fake_tcp_check)
    monkeypatch.setattr(systems, "_neo4j_status_via_graphiti", fake_neo4j_status)

    out = await systems.list_systems()
    payload = out["systems"]
    assert payload, "expected at least one system entry"

    required = {"id", "name", "kind", "url", "status", "latency_ms", "credential"}
    for row in payload:
        assert required <= row.keys(), row
        assert row["kind"] in ("ui", "api", "store", "external")
        assert "health_url" not in row
        assert row["status"] in ("up", "down", "unknown")
        assert "label" in row["credential"] and "location" in row["credential"]
        # No credential VALUE anywhere in the row — only its label/location.
        assert _SENTINEL not in str(row)

    by_id = {row["id"]: row for row in payload}
    assert by_id["n8n"]["url"] == "https://n8n.tail.example/"
    assert by_id["litellm"]["url"] == "https://litellm.tail.example/ui"
    assert by_id["victorialogs"]["url"] == "https://vlogs.tail.example/"
    assert by_id["neo4j"]["url"] == "https://neo4j.tail.example/"
    assert by_id["cockpit"]["status"] == "up"

    # External SaaS entries are omitted entirely when their base URL is empty.
    assert "jira" not in by_id
    assert "confluence" not in by_id


async def test_systems_includes_external_entries_only_when_configured(monkeypatch):
    monkeypatch.setattr(settings, "jira_base_url", "https://example.atlassian.net")
    monkeypatch.setattr(settings, "confluence_base_url", "")

    async def fake_http_check(url):
        return ("unknown", None)

    async def fake_tcp_check(url, default_port):
        return ("unknown", None)

    async def fake_neo4j_status():
        return ("unknown", None)

    monkeypatch.setattr(systems, "_http_check", fake_http_check)
    monkeypatch.setattr(systems, "_tcp_check", fake_tcp_check)
    monkeypatch.setattr(systems, "_neo4j_status_via_graphiti", fake_neo4j_status)

    out = await systems.list_systems()
    by_id = {row["id"]: row for row in out["systems"]}
    assert by_id["jira"]["url"] == "https://example.atlassian.net"
    assert "confluence" not in by_id
