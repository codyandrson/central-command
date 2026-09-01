"""Systems — the cockpit's launchpad: one static table of every deployed or
integrated system (name, browser link, live up/down, and WHERE its
credential lives — never the value), enriched with a concurrent health
fan-out at request time. Internal probe targets stay OUT of the payload on
purpose: a DSN carries its password, and the page must never be a place a
credential value can appear.

Own module + router (not routes.py) so this lands with zero risk of
colliding with concurrent work there — registered in api/app.py exactly like
`nerve_gateway_router`.
"""

from __future__ import annotations

import asyncio
import time
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter

from central_command.config import settings
from central_command.integrations import http as http_client

router = APIRouter(prefix="/api")

_TIMEOUT = 2.0


async def _http_check(url: str | None) -> tuple[str, float | None]:
    """GET `url` with a short timeout. Reachability is the question, not
    success — any HTTP response (even a 404) means "up"; a connection error
    or timeout means "down". No internal URL configured means "unknown"."""
    if not url:
        return "unknown", None
    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, **http_client.client_kwargs()) as client:
            await client.get(url)
        return "up", round((time.monotonic() - start) * 1000, 1)
    except Exception:
        return "down", None


async def _tcp_check(url: str | None, default_port: int) -> tuple[str, float | None]:
    """A bare TCP connect for services with no HTTP surface (Postgres,
    LiteLLM's own Postgres) — same up/down/unknown contract as `_http_check`."""
    if not url:
        return "unknown", None
    parts = urlsplit(url)
    host = parts.hostname
    port = parts.port or default_port
    if not host:
        return "unknown", None
    start = time.monotonic()
    try:
        _reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=_TIMEOUT
        )
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return "up", round((time.monotonic() - start) * 1000, 1)
    except Exception:
        return "down", None


def _origin(url: str) -> str | None:
    """Scheme+host(:port) of `url`, or None — used to turn a webhook path
    (e.g. the n8n façade URL) into the service's root for a liveness probe."""
    if not url:
        return None
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}"


def _api_health_url() -> str | None:
    """This process's own /health — host normalised to loopback since
    api_host is typically 0.0.0.0 (bind-all), which cannot be dialed back."""
    host = settings.api_host
    if not host or host == "0.0.0.0":
        host = "127.0.0.1"
    return f"http://{host}:{settings.api_port}/health"


def _entries() -> list[dict]:
    """The static table. `health` is a coroutine (or None for entries with no
    live check) resolved by the caller; everything else is fixed shape."""
    entries: list[dict] = [
        {
            "id": "cockpit",
            "name": "Cockpit",
            "kind": "ui",
            "url": None,
            "health": None,  # answering this request IS the liveness proof
            "credential": {"label": "session auth", "location": "web/.env"},
        },
        {
            "id": "control-plane-api",
            "name": "Control Plane API",
            "kind": "api",
            # Same-origin relative link: FastAPI's Swagger UI lives under the
            # /api prefix (app.py) and the cockpit server proxies it, so this
            # works from wherever the cockpit is being viewed — no setting.
            "url": "/api/docs",
            "link_label": "Swagger",
            "health": _http_check(_api_health_url()),
            "credential": {"label": "none (internal)", "location": "n/a"},
        },
        {
            "id": "litellm",
            "name": "LiteLLM",
            "kind": "api",
            "url": settings.llm_proxy_ui_url or None,
            "health": _http_check(
                f"{settings.llm_proxy_base_url}/health/liveliness"
                if settings.llm_proxy_base_url
                else None
            ),
            "credential": {
                "label": "admin key + UI login",
                "location": "CC_LLM_PROXY_ADMIN_KEY (+ UI_USERNAME/UI_PASSWORD in deploy/pi/.env)",
            },
        },
        {
            "id": "litellm-db",
            "name": "LiteLLM DB",
            "kind": "store",
            "url": None,
            "health": _tcp_check(settings.litellm_db_url or None, 5443),
            "credential": {
                "label": "salt key + DB password",
                "location": "CC_LITELLM_SALT_KEY + LITELLM_POSTGRES_PASSWORD in deploy/pi/.env",
            },
        },
        {
            "id": "postgres",
            "name": "Postgres",
            "kind": "store",
            "url": None,
            "health": _tcp_check(settings.database_url, 5442),
            "credential": {"label": "DB URL", "location": "CC_DATABASE_URL"},
        },
        {
            "id": "graphiti",
            "name": "Graphiti MCP",
            "kind": "api",
            "url": None,
            "health": _http_check(
                f"{_origin(settings.graphiti_mcp_url)}/health"
                if _origin(settings.graphiti_mcp_url)
                else None
            ),
            "credential": {"label": "none (internal)", "location": "n/a"},
        },
        {
            "id": "neo4j",
            "name": "Neo4j",
            "kind": "ui",
            "url": settings.neo4j_browser_url or None,
            "health": _neo4j_status_via_graphiti(),
            "credential": {
                "label": "database password",
                "location": "NEO4J_PASSWORD in deploy/pi/.env",
            },
        },
        {
            "id": "n8n",
            "name": "n8n",
            "kind": "ui",
            "url": settings.n8n_ui_url or None,
            "health": _http_check(_origin(settings.email_facade_url)),
            "credential": {
                "label": "n8n login + encryption key (Gmail OAuth lives inside n8n)",
                "location": "n8n login + N8N_ENCRYPTION_KEY in deploy/pi/.env",
            },
        },
        {
            "id": "victorialogs",
            "name": "VictoriaLogs",
            "kind": "ui",
            "url": settings.vlogs_ui_url or None,
            # Not settings-driven: no internal CC_* URL exists for it (only
            # the browser URL is configured). ServiceLB carries the real port
            # to loopback on the Pi, where cc-uvicorn runs, same as every
            # other CC_* endpoint (deploy/k3s/80-vlogs.yaml).
            "health": _http_check(
                "http://127.0.0.1:9428/health" if settings.vlogs_ui_url else None
            ),
            "credential": {"label": "none (internal)", "location": "n/a"},
        },
        {
            "id": "db-ui",
            "name": "pgweb (DB UI)",
            "kind": "ui",
            "url": settings.db_ui_url or None,
            # Same pattern as VictoriaLogs: no internal CC_* URL exists, only
            # the browser one — the loopback hostPort is fixed by the manifest
            # (deploy/k3s/85-pgweb.yaml).
            "health": _http_check(
                "http://127.0.0.1:8092/" if settings.db_ui_url else None
            ),
            "credential": {
                "label": "none — auto-connected to the spine",
                "location": "tailnet-gated (tailscale serve)",
            },
        },
        {
            "id": "sandbox-runner",
            "name": "Sandbox Runner",
            "kind": "api",
            "url": settings.sandbox_docs_url or None,
            "link_label": "Swagger",
            # No /health route on the runner (it's /sessions and friends) —
            # a bare GET still proves the process is listening, same
            # reachability contract as everywhere else.
            "health": _http_check(settings.sandbox_runner_url or None),
            "credential": {
                "label": "shared bearer token",
                "location": "CC_SANDBOX_RUNNER_TOKEN",
            },
        },
        {
            "id": "crawler",
            "name": "Crawler",
            "kind": "api",
            "url": settings.crawler_docs_url or None,
            "link_label": "Swagger",
            "health": _http_check(
                f"{settings.crawler_url}/healthz" if settings.crawler_url else None
            ),
            "credential": {"label": "none (internal)", "location": "n/a"},
        },
    ]
    # External SaaS: only listed when configured — an empty base URL is "not
    # applicable to this install", not "misconfigured", unlike the internal
    # services above which the operator wants to SEE as unconfigured.
    if settings.jira_base_url:
        entries.append({
            "id": "jira",
            "name": "Jira",
            "kind": "external",
            "url": settings.jira_base_url,
            "health": _unknown(),
            "credential": {"label": "API token", "location": "CC_JIRA_API_TOKEN"},
        })
    if settings.confluence_base_url:
        entries.append({
            "id": "confluence",
            "name": "Confluence",
            "kind": "external",
            "url": settings.confluence_base_url,
            "health": _unknown(),
            "credential": {"label": "API token", "location": "CC_CONFLUENCE_API_TOKEN"},
        })
    return entries


async def _unknown() -> tuple[str, float | None]:
    return "unknown", None


async def _neo4j_status_via_graphiti() -> tuple[str, float | None]:
    """No direct Neo4j health surface is exposed to this process (bolt reads
    go through neo4j_reader for the Graph panel, not through here) — take
    Graphiti's own get_status as a proxy: if Graphiti reports the graph is
    reachable, Neo4j is up behind it. Any failure (Graphiti down, MCP error,
    status missing the field) reads as "unknown", never a false "down"."""
    from central_command.integrations import graphiti

    try:
        status = await asyncio.wait_for(graphiti.get_status(), timeout=_TIMEOUT)
    except Exception:
        return "unknown", None
    ok = status.get("status") or status.get("ok") or status.get("neo4j")
    if ok in (True, "ok", "healthy", "connected"):
        return "up", None
    return "unknown", None


@router.get("/systems")
async def list_systems() -> dict:
    entries = _entries()
    healths = await asyncio.gather(
        *[
            (e["health"] if e["health"] is not None else _unknown())
            for e in entries
        ]
    )
    out = []
    for entry, (status, latency_ms) in zip(entries, healths):
        row = {k: v for k, v in entry.items() if k != "health"}
        if entry["id"] == "cockpit":
            status, latency_ms = "up", None
        row["status"] = status
        row["latency_ms"] = latency_ms
        out.append(row)
    return {"systems": out}
