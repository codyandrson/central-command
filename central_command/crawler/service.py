"""cc-crawler — rung-2 fallback for whole-site docs ingestion.

STANDALONE by design, like central_command/sandbox/runner.py: this module imports
NOTHING from `central_command` and runs in its own container (deploy/k3s/70-crawler.yaml),
not in the app venv. It holds no credentials and reaches nothing but the target
site — Crawl4AI's optional LLM features are NOT used, so there is no provider
call anywhere in this process.

WHY A CONTAINER ON THE CHROMEBOX: Crawl4AI drives a real Playwright/Chromium.
That is a ~1 GB browser and a memory-hungry process, i.e. exactly what the
placement rule keeps off the Pi. Required nodeAffinity to the chromebox,
accepted consequence: chromebox down = crawler down (same call as the sandbox).

WHEN TO USE IT: rung 1 is a plain HTTP fetch (`integrations/webfetch.py`).
Reach for this only when the site is a JS-rendered SPA that plain fetching
reads as an empty shell, or when a whole documentation tree is wanted.

THE ONE MODEL CALL, AND IT IS OPT-IN AND LOCAL: `filter="llm"` swaps the
heuristic PruningContentFilter for Crawl4AI's LLMContentFilter, which costs one
model call per page (chunked). It points ONLY at this cluster's own LiteLLM
proxy (LLM_BASE_URL/LLM_API_KEY/LLM_MODEL, from the optional `cc-crawler-llm`
Secret) — never a provider directly. Unset key or an unreachable proxy FALLS
BACK to pruning and says so in `warnings`; a crawl must not fail because a
nice-to-have filter was unavailable. Default stays "pruning": zero model cost.

API (crawl4ai 0.9.x, read from docs.crawl4ai.com 2026-08-07):
  BFSDeepCrawlStrategy(max_depth, include_external, max_pages, filter_chain)
  DefaultMarkdownGenerator(content_filter=PruningContentFilter(...))
    -> result.markdown.fit_markdown / .raw_markdown
  LLMContentFilter(llm_config=LLMConfig(provider=..., api_token=..., base_url=...),
                   instruction=..., chunk_token_threshold=...)
    provider is a LITELLM model id, so an OpenAI-compatible proxy is
    "openai/<alias>" + base_url — the same shape Graphiti uses against this
    LiteLLM.
  stream=True makes arun() an async iterator, which is what lets a timeout
  return the pages already crawled instead of nothing.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Literal

from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig, LLMConfig
from crawl4ai.content_filter_strategy import LLMContentFilter, PruningContentFilter
from crawl4ai.deep_crawling import BFSDeepCrawlStrategy
from crawl4ai.deep_crawling.filters import FilterChain, URLPatternFilter
from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator
from fastapi import FastAPI
from pydantic import BaseModel, Field

MAX_PAGES_CEILING = 200
MAX_TIMEOUT_S = 900
# Per-page browser budget, so one hung page cannot eat the whole crawl budget.
PAGE_TIMEOUT_MS = 60_000

app = FastAPI(title="cc-crawler")

# ponytail: ONE crawl at a time, process-wide. Chromium under a 3Gi limit will
# OOM long before concurrency buys anything, and a queued request is a better
# failure than a killed pod. Upgrade path if throughput ever matters: a
# semaphore sized to the memory limit, or crawl4ai's own dispatcher.
_lock = asyncio.Lock()

_BROWSER = BrowserConfig(headless=True, verbose=False)

# The LiteLLM proxy inside this cluster. Service name is `litellm` (the
# Deployment is cc-litellm; the Service is not) — see deploy/k3s/30-litellm.yaml.
# `/v1` is part of the base URL, matching what cc-graphiti is given.
_LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://litellm:4000/v1")
_LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
_LLM_MODEL = os.environ.get("LLM_MODEL", "cc-default")

_LLM_INSTRUCTION = """
Extract the page's substantive content as clean markdown, preserving the
original wording, headings, code blocks and tables exactly.
Remove only navigation, sidebars, cookie banners, ads and footers.
Do not summarise, comment, or add anything of your own.
"""


class CrawlRequest(BaseModel):
    url: str
    max_depth: int = 2
    max_pages: int = 50
    path_prefix: str | None = None
    timeout_s: int = 300
    # OPT-IN: "llm" costs one model call per page against the local proxy.
    filter: Literal["pruning", "llm"] = "pruning"


class FetchRequest(BaseModel):
    url: str
    filter: Literal["pruning", "llm"] = "pruning"


class Page(BaseModel):
    url: str
    title: str = ""
    markdown: str = ""
    # Only ever set on /fetch, where there is no errors[] to carry it — a
    # failed render must not look like an empty page.
    error: str = ""
    # Only on /fetch, same reason: an LLM-filter fallback must be visible.
    warnings: list[str] = Field(default_factory=list)


class CrawlResponse(BaseModel):
    pages: list[Page] = Field(default_factory=list)
    truncated: bool = False
    errors: list[dict] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def _pruning() -> PruningContentFilter:
    return PruningContentFilter(threshold=0.45, threshold_type="dynamic")


async def _llm_reachable() -> bool:
    """One cheap preflight against the proxy's /models.

    Done BEFORE the crawl rather than trusting the filter to fail gracefully:
    an unreachable proxy discovered per-page would turn every page into an
    error instead of into pruned markdown. ponytail: /models is the smallest
    authenticated call LiteLLM answers; it proves reach + credential, not that
    the model itself will answer.
    """
    import httpx  # local: only the llm path needs it

    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            r = await c.get(
                f"{_LLM_BASE_URL.rstrip('/')}/models",
                headers={"authorization": f"Bearer {_LLM_API_KEY}"},
            )
        return r.status_code < 400
    except Exception:
        return False


async def _llm_post_filter(mode: str) -> tuple[Any | None, list[str]]:
    """The LLMContentFilter to apply per page AFTER the crawl — or None.

    NEVER raises and never refuses: 'llm' is a quality upgrade, so every way it
    can be unavailable degrades to pruning with a sentence saying so.

    Why post-hoc rather than in the run config: the filter's litellm calls are
    SYNCHRONOUS. Inside crawl4ai's markdown generation they run on the event
    loop and starve it — health probes time out and the kubelet kills the pod
    (observed live, 2026-08-07). So the crawl itself always uses pruning, and
    'llm' mode re-filters each page's HTML in a worker thread afterwards,
    falling back to that page's pruned markdown on any failure.
    """
    if mode != "llm":
        return None, []
    if not _LLM_API_KEY:
        return None, [
            "filter='llm' requested but LLM_API_KEY is unset "
            "(Secret cc-crawler-llm not applied) — used pruning instead"
        ]
    if not await _llm_reachable():
        return None, [
            f"filter='llm' requested but the LiteLLM proxy at {_LLM_BASE_URL} "
            "did not answer — used pruning instead"
        ]
    return (
        LLMContentFilter(
            # litellm model id: an OpenAI-compatible proxy is openai/<alias>,
            # with base_url pointing at the proxy. Same shape as cc-graphiti.
            llm_config=LLMConfig(
                provider=f"openai/{_LLM_MODEL}",
                api_token=_LLM_API_KEY,
                base_url=_LLM_BASE_URL,
            ),
            instruction=_LLM_INSTRUCTION,
            # Smaller chunks process in parallel; the default is one giant chunk.
            chunk_token_threshold=4096,
            verbose=False,
        ),
        [],
    )


def _llm_markdown(llm_filter: Any, result: Any, fallback: str) -> tuple[str, str]:
    """Run the LLM filter over one page's HTML (called via asyncio.to_thread).

    Returns (markdown, warning) — warning is "" on success, and the fallback is
    always the page's already-pruned markdown, so a filter miss costs quality,
    never the page."""
    html = getattr(result, "cleaned_html", "") or getattr(result, "html", "")
    url = getattr(result, "url", "?")
    if not html:
        return fallback, f"llm filter: no HTML captured for {url} — kept pruned markdown"
    try:
        chunks = llm_filter.filter_content(html)
    except Exception as e:  # noqa: BLE001 — degradation, not control flow
        return fallback, f"llm filter failed for {url}: {e} — kept pruned markdown"
    text = "\n\n".join(c for c in chunks if c and c.strip()).strip()
    if not text:
        return fallback, f"llm filter returned nothing for {url} — kept pruned markdown"
    return text, ""


def _run_config(content_filter: Any, **kw: Any) -> CrawlerRunConfig:
    """Markdown + the chosen filter, cache bypassed (ingestion wants today's page)."""
    return CrawlerRunConfig(
        markdown_generator=DefaultMarkdownGenerator(content_filter=content_filter),
        cache_mode=CacheMode.BYPASS,
        page_timeout=PAGE_TIMEOUT_MS,
        **kw,
    )


def _markdown(result: Any) -> str:
    """fit_markdown when the pruning filter produced one, else raw."""
    md = getattr(result, "markdown", None)
    if isinstance(md, str):
        return md
    return getattr(md, "fit_markdown", "") or getattr(md, "raw_markdown", "") or ""


def _title(result: Any) -> str:
    return (getattr(result, "metadata", None) or {}).get("title") or ""


def _first(res: Any) -> Any:
    """arun() returns a container in non-stream mode; unwrap to one result."""
    try:
        return res[0]
    except (TypeError, KeyError, IndexError):
        return res


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}


@app.post("/crawl", response_model=CrawlResponse)
async def crawl(req: CrawlRequest) -> CrawlResponse:
    max_pages = max(1, min(req.max_pages, MAX_PAGES_CEILING))
    deadline = time.monotonic() + max(1, min(req.timeout_s, MAX_TIMEOUT_S))

    # Only when asked: a substring pattern on the full URL (crawl4ai's
    # URLPatternFilter is fnmatch-style over the whole URL, not path-anchored),
    # which is what "stay under /docs/" means in practice. It applies to
    # DISCOVERED links; the start URL is always crawled.
    chain = (
        FilterChain([URLPatternFilter(patterns=[f"*{req.path_prefix.rstrip('/')}*"])])
        if req.path_prefix
        else FilterChain([])
    )

    llm_filter, warnings = await _llm_post_filter(req.filter)
    config = _run_config(
        _pruning(),
        stream=True,
        deep_crawl_strategy=BFSDeepCrawlStrategy(
            max_depth=req.max_depth,
            include_external=False,
            max_pages=max_pages,
            filter_chain=chain,
        ),
    )

    pages: list[Page] = []
    errors: list[dict] = []
    truncated = False

    async with _lock:
        async with AsyncWebCrawler(config=_BROWSER) as crawler:
            async for result in await crawler.arun(req.url, config=config):
                if getattr(result, "success", False):
                    md = _markdown(result)
                    if llm_filter is not None and time.monotonic() < deadline:
                        md, warn = await asyncio.to_thread(_llm_markdown, llm_filter, result, md)
                        if warn:
                            warnings.append(warn)
                    pages.append(Page(url=result.url, title=_title(result), markdown=md))
                else:
                    errors.append(
                        {"url": getattr(result, "url", req.url),
                         "error": getattr(result, "error_message", "") or "crawl failed"}
                    )
                if len(pages) >= max_pages or time.monotonic() >= deadline:
                    truncated = True
                    break

    return CrawlResponse(pages=pages, truncated=truncated, errors=errors, warnings=warnings)


@app.post("/fetch", response_model=Page)
async def fetch(req: FetchRequest) -> Page:
    llm_filter, warnings = await _llm_post_filter(req.filter)
    async with _lock:
        async with AsyncWebCrawler(config=_BROWSER) as crawler:
            result = _first(await crawler.arun(req.url, config=_run_config(_pruning())))
    if not getattr(result, "success", False):
        return Page(
            url=req.url,
            error=getattr(result, "error_message", "") or "fetch failed",
            warnings=warnings,
        )
    md = _markdown(result)
    if llm_filter is not None:
        md, warn = await asyncio.to_thread(_llm_markdown, llm_filter, result, md)
        if warn:
            warnings.append(warn)
    return Page(url=result.url, title=_title(result), markdown=md, warnings=warnings)
