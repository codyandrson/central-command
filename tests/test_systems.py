"""Wire-shape test for GET /api/systems — the cockpit's launchpad.

Health checks (HTTP GET, TCP connect, the Neo4j ping, the graphiti patch state) are monkeypatched
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
    monkeypatch.setattr(settings, "llama_swap_ui_url", "http://swap.tail.example:8081/ui")
    monkeypatch.setattr(settings, "neo4j_browser_url", "https://neo4j.tail.example/")
    monkeypatch.setattr(settings, "sandbox_docs_url", "https://sandbox.tail.example/docs")
    monkeypatch.setattr(settings, "crawler_docs_url", "https://crawler.tail.example/docs")
    monkeypatch.setattr(settings, "db_ui_url", "https://adminer.tail.example/")
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
    monkeypatch.setattr(systems, "_neo4j_status", fake_neo4j_status)
    monkeypatch.setattr(systems, "_graphiti_status", fake_neo4j_status)

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
    assert by_id["llama-swap"]["url"] == "http://swap.tail.example:8081/ui"
    assert by_id["llama-swap"]["kind"] == "ui"
    assert by_id["neo4j"]["url"] == "https://neo4j.tail.example/"
    assert by_id["cockpit"]["status"] == "up"
    # Swagger links: the control plane's is same-origin relative (proxied by
    # the cockpit server), the others flow from their display settings — and
    # each carries the label the cockpit renders instead of "Open".
    assert by_id["control-plane-api"]["url"] == "/api/docs"
    assert by_id["sandbox-runner"]["url"] == "https://sandbox.tail.example/docs"
    assert by_id["crawler"]["url"] == "https://crawler.tail.example/docs"
    for sid in ("control-plane-api", "sandbox-runner", "crawler"):
        assert by_id[sid]["link_label"] == "Swagger"
    assert by_id["db-ui"]["url"] == "https://adminer.tail.example/"

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
    monkeypatch.setattr(systems, "_neo4j_status", fake_neo4j_status)
    monkeypatch.setattr(systems, "_graphiti_status", fake_neo4j_status)

    out = await systems.list_systems()
    by_id = {row["id"]: row for row in out["systems"]}
    assert by_id["jira"]["url"] == "https://example.atlassian.net"
    assert "confluence" not in by_id


async def test_llama_swap_probe_is_the_browser_origin(monkeypatch):
    """llama-swap has no loopback carrier — the probe is the browser URL's
    ORIGIN (never the /ui path, so a UI path change cannot read as down),
    and an unset URL is 'unknown' with no probe at all."""
    monkeypatch.setattr(settings, "llama_swap_ui_url", "http://swap.tail.example:8081/ui")
    probed: list[str | None] = []

    async def fake_http_check(url):
        probed.append(url)
        return ("up", 1.0) if url else ("unknown", None)

    async def fake_tcp_check(url, default_port):
        return ("unknown", None)

    async def fake_neo4j_status():
        return ("unknown", None)

    monkeypatch.setattr(systems, "_http_check", fake_http_check)
    monkeypatch.setattr(systems, "_tcp_check", fake_tcp_check)
    monkeypatch.setattr(systems, "_neo4j_status", fake_neo4j_status)
    monkeypatch.setattr(systems, "_graphiti_status", fake_neo4j_status)

    by_id = {row["id"]: row for row in (await systems.list_systems())["systems"]}
    assert by_id["llama-swap"]["status"] == "up"
    assert "http://swap.tail.example:8081" in probed
    assert "http://swap.tail.example:8081/ui" not in probed

    monkeypatch.setattr(settings, "llama_swap_ui_url", "")
    by_id = {row["id"]: row for row in (await systems.list_systems())["systems"]}
    assert by_id["llama-swap"]["url"] is None
    assert by_id["llama-swap"]["status"] == "unknown"


async def _patched_probes(monkeypatch):
    async def up(*_a):
        return ("up", None)

    monkeypatch.setattr(systems, "_http_check", up)
    monkeypatch.setattr(systems, "_tcp_check", up)
    monkeypatch.setattr(systems, "_neo4j_status", up)
    monkeypatch.setattr(systems, "_graphiti_status", up)


async def test_the_graph_row_carries_the_ingest_queue_line_and_no_other_row_does(monkeypatch):
    """A stuck queue (a mis-scoped key answers 403, which is retried forever)
    must show on the Systems page, on the EXISTING graph row — not only on a
    job row nobody opens."""
    from central_command.db import repo

    await _patched_probes(monkeypatch)

    async def summary():
        return {"QUEUED": 3, "RUNNING": 1, "FAILED": 2,
                "retry_error": "Error code: 403 - key not allowed to access model " + "x" * 400}

    monkeypatch.setattr(repo, "ingest_queue_summary", summary)

    rows = {r["id"]: r for r in (await systems.list_systems())["systems"]}
    detail = rows["graphiti"]["detail"]
    assert detail.startswith("ingest queue: 3 queued, 1 running, 2 failed — retrying after: Error code: 403")
    queue_part = detail.split(" · reranker:")[0]
    assert len(queue_part) < 300 and queue_part.endswith("…")  # the error is clipped
    assert [i for i, r in rows.items() if "detail" in r] == ["graphiti"]
    assert "detail_probe" not in rows["graphiti"]


async def test_a_quiet_queue_says_only_its_counts(monkeypatch):
    from central_command.db import repo

    await _patched_probes(monkeypatch)

    async def summary():
        return {"QUEUED": 0, "RUNNING": 0, "FAILED": 0, "retry_error": None}

    monkeypatch.setattr(repo, "ingest_queue_summary", summary)
    monkeypatch.setattr(settings, "graph_rerank_alias", "")
    rows = {r["id"]: r for r in (await systems.list_systems())["systems"]}
    assert rows["graphiti"]["detail"] == (
        "ingest queue: 0 queued, 0 running, 0 failed · "
        "reranker: none — rank fusion only (map cc-rerank to enable)")


async def test_an_unreadable_queue_drops_only_the_queue_line(monkeypatch):
    from central_command.db import repo

    await _patched_probes(monkeypatch)

    async def broken():
        raise OSError("connection refused")

    monkeypatch.setattr(repo, "ingest_queue_summary", broken)
    monkeypatch.setattr(settings, "graph_rerank_alias", "")
    rows = {r["id"]: r for r in (await systems.list_systems())["systems"]}
    assert rows["graphiti"]["detail"].startswith("reranker: none")
    assert "ingest queue" not in rows["graphiti"]["detail"]
    assert rows["graphiti"]["status"] == "up"


async def test_the_graph_row_names_the_reranker_and_its_last_failure(monkeypatch):
    """A configured reranker that fails makes fact search ERROR; the Systems
    row is where the operator sees which reranker and why."""
    from central_command.db import repo
    from central_command.integrations import graphiti_client

    await _patched_probes(monkeypatch)

    async def summary():
        return {"QUEUED": 0, "RUNNING": 0, "FAILED": 0, "retry_error": None}

    monkeypatch.setattr(repo, "ingest_queue_summary", summary)
    monkeypatch.setattr(settings, "graph_rerank_alias", "cc-rerank")
    monkeypatch.setattr(settings, "graph_rerank_kind", "chat")
    monkeypatch.setattr(graphiti_client, "rerank_stats", lambda: {
        "calls": 12, "failures": 2, "malformed": 1, "median_latency_s": 6.1,
        "last_error": "RerankError: the chat reranker 'cc-rerank' answered 'We'",
        "last_error_at": "2026-10-05T12:00:00Z"})
    detail = {r["id"]: r for r in (await systems.list_systems())["systems"]}["graphiti"]["detail"]
    assert detail.endswith(
        "reranker: chat via 'cc-rerank' (12 calls, median 6.1 s) — 2 failed, 1 malformed; "
        "last: RerankError: the chat reranker 'cc-rerank' answered 'We'")

    monkeypatch.setattr(settings, "graph_rerank_kind", "bogus")
    detail = {r["id"]: r for r in (await systems.list_systems())["systems"]}["graphiti"]["detail"]
    assert "reranker: MISCONFIGURED" in detail and "CC_GRAPH_RERANK_KIND" in detail
