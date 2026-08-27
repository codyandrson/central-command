"""Client for cc-crawler (central_command/crawler/service.py): rung-2 docs
ingestion when a site is a JS-rendered SPA that `webfetch` reads as an empty
shell.

Ungated for the same reason as webfetch: a GET is not a write to anyone's
world but the far server's access log. `settings.crawler_url` is ONE address
for the whole process, never a per-call agent argument (the webfetch/sandbox
precedent).

HONEST DEGRADATION, not an exception: the crawler is a chromebox-pinned pod, so
"not reachable" is a NORMAL state (chromebox down, or the service simply never
deployed). Both entry points return a result carrying `degraded` instead of
raising, so a caller gets a sentence it can put in front of the operator rather
than a traceback.
"""

from __future__ import annotations

import httpx

from central_command.config import settings

# Mirrors the server-side ceiling in crawler/service.py. Duplicated on purpose:
# the client may not import the service (it lives in a container), and a caller
# asking for 5000 pages deserves the honest number back either way.
MAX_PAGES_CEILING = 200

_UNAVAILABLE = "crawler unavailable — the chromebox may be down or cc-crawler not deployed"


def _degraded(exc: Exception) -> str:
    return f"{_UNAVAILABLE} ({exc.__class__.__name__}: {exc})"


async def crawl_site(
    url: str,
    max_depth: int = 2,
    max_pages: int = 50,
    path_prefix: str | None = None,
    timeout_s: int = 300,
    filter_mode: str = "pruning",
) -> dict:
    """Deep-crawl `url` and return {pages, truncated, errors, warnings, degraded}.

    `degraded` is "" on success and a sentence explaining the miss otherwise.
    `filter_mode="llm"` asks the service for LLM-cleaned markdown (one call per
    page against the cluster's own LiteLLM). It is best-effort by design: if
    the proxy is unconfigured or unreachable the service falls back to pruning
    and says so in `warnings` — the crawl still succeeds.
    """
    body = {
        "url": url,
        "max_depth": max_depth,
        "max_pages": max(1, min(max_pages, MAX_PAGES_CEILING)),
        "path_prefix": path_prefix,
        "timeout_s": timeout_s,
        "filter": filter_mode,
    }
    try:
        # Client timeout must outlast the server's own crawl budget, or the
        # client gives up on a crawl that is about to answer.
        async with httpx.AsyncClient(base_url=settings.crawler_url, timeout=timeout_s + 30) as c:
            r = await c.post("/crawl", json=body)
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPError as e:
        return {
            "pages": [],
            "truncated": False,
            "errors": [],
            "warnings": [],
            "degraded": _degraded(e),
        }
    return {
        "pages": data.get("pages", []),
        "truncated": bool(data.get("truncated", False)),
        "errors": data.get("errors", []),
        # e.g. "filter='llm' requested but ... — used pruning instead"
        "warnings": data.get("warnings", []),
        "degraded": "",
    }


async def fetch_rendered(url: str, filter_mode: str = "pruning") -> dict:
    """Browser-render ONE page: {url, title, markdown, warnings, degraded}."""
    try:
        async with httpx.AsyncClient(base_url=settings.crawler_url, timeout=120.0) as c:
            r = await c.post("/fetch", json={"url": url, "filter": filter_mode})
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPError as e:
        return {
            "url": url,
            "title": "",
            "markdown": "",
            "warnings": [],
            "degraded": _degraded(e),
        }
    return {
        "url": data.get("url", url),
        "title": data.get("title", ""),
        "markdown": data.get("markdown", ""),
        "warnings": data.get("warnings", []),
        # The service reports a failed render in `error`; surface it in the
        # same field a transport failure uses, so callers check one thing.
        "degraded": data.get("error", ""),
    }
