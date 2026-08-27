"""Whole-site doc import for the skills library.

A ladder, cheapest rung first: an `llms-full.txt` the site publishes on
purpose for LLM consumers (rung 0, no crawling at all), else a sitemap walked
with trafilatura's extractor (rung 1), else — only when the caller asks for
it, because it needs the separate `cc-crawler` service — a browser-rendered
crawl for JS-only sites (rung 2, `central_command/integrations/crawler.py`).

Lives in `central_command/skills/`, same tier as `importer.py`: network-using but
filesystem-free, and it must not import `runtime/` or `gateway/`. Only the API
tier calls this. Event emission is the ROUTE's job (one `skill.site_imported`
summary event, not one per doc) — this module only writes rows via `repo`.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

import httpx
from trafilatura.sitemaps import sitemap_search
from trafilatura.spider import focused_crawler

from central_command.db import repo
from central_command.integrations import webfetch

# Own cap for the llms-full.txt fetch — deliberately NOT settings.fetch_max_bytes,
# which is the per-page cap `webfetch.fetch` enforces for every other read.
_LLMS_FULL_MAX_BYTES = 25_000_000
_MAX_PAGES_CAP = 200
_CONCURRENCY = 4
# Merge sections of an llms-full.txt so a huge doc doesn't become hundreds of
# one-paragraph rows. ponytail: fixed thresholds, not a size-balancing
# algorithm — good enough for "sane number of docs", revisit if a real site
# produces a pathological heading distribution.
_MIN_SECTION_CHARS = 800
_MAX_LLMS_FULL_DOCS = 60

_HEADING_RE = re.compile(r"^#{1,2}\s+(.+)$", re.MULTILINE)


def _slug(text: str) -> str:
    # Cap at 80 chars: llms-full.txt headings can embed whole URLs, and the
    # 2026-08-19 litellm import minted ~150-char doc_keys from them. `_keyed`
    # still disambiguates two headings that collide after the cut.
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:80].rstrip("-") or "doc"


def _keyed(prefix: str | None, text: str, seen: dict[str, int]) -> str:
    """Slugify `text` into a doc_key, prefixed and disambiguated against
    every key already used in this import (two pages/sections can slug to the
    same thing)."""
    key = _slug(text)
    if prefix:
        key = f"{prefix}-{key}"
    n = seen.get(key, 0)
    seen[key] = n + 1
    return key if n == 0 else f"{key}-{n + 1}"


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "", "", ""))


def _degraded(rung: str, notes: list[str]) -> dict:
    return {"imported": [], "skipped": [], "truncated": False, "rung": rung, "notes": notes}


async def _get_raw(url: str, max_bytes: int) -> tuple[bool, str]:
    """GET `url` under the same outbound identity `webfetch` uses (client
    cert/CA), but this module's own size cap. Returns (ok, text); any
    connection failure or non-200 is `ok=False`, never an exception — same
    contract as `webfetch.fetch`."""
    try:
        parsed = httpx.URL(url)
        async with httpx.AsyncClient(**webfetch._client_kwargs(parsed.host)) as client:
            # Streamed so the cap bounds what is DOWNLOADED, not just what is
            # kept — a typo'd URL to a huge file must not balloon the Pi's
            # memory before a post-hoc slice.
            async with client.stream("GET", url) as resp:
                if resp.status_code != 200:
                    return False, ""
                chunks: list[bytes] = []
                got = 0
                async for chunk in resp.aiter_bytes():
                    chunks.append(chunk)
                    got += len(chunk)
                    if got >= max_bytes:
                        break
    except httpx.HTTPError:
        return False, ""
    return True, b"".join(chunks)[:max_bytes].decode("utf-8", errors="replace")


def _split_llms_full(text: str) -> list[tuple[str, str]]:
    """Split on top-level (`#`/`##`) headings, merging consecutive small
    sections so the result stays a sane number of docs."""
    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        return [("full", text)]

    sections: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections.append((m.group(1).strip(), text[m.start():end]))

    merged: list[tuple[str, str]] = []
    for title, body in sections:
        if merged and len(merged[-1][1]) < _MIN_SECTION_CHARS:
            prev_title, prev_body = merged[-1]
            merged[-1] = (prev_title, prev_body + "\n\n" + body)
        else:
            merged.append((title, body))

    while len(merged) > _MAX_LLMS_FULL_DOCS:
        (t0, b0), (_, b1) = merged[0], merged[1]
        merged[0:2] = [(t0, b0 + "\n\n" + b1)]

    return merged


async def _import_llms_full(
    skill_id: str, origin: str, doc_key_prefix: str | None, describes: str | None,
) -> dict | None:
    """Rung 0. Returns None (not a degraded result) when no llms-full.txt is
    published, so the caller falls through to rung 1."""
    url = f"{origin}/llms-full.txt"
    ok, text = await _get_raw(url, _LLMS_FULL_MAX_BYTES)
    if not ok or not text.strip():
        return None

    sections = _split_llms_full(text)
    captured_at = datetime.now(timezone.utc)
    seen: dict[str, int] = {}
    imported = []
    for title, body in sections:
        key = _keyed(doc_key_prefix, title, seen)
        await repo.add_skill_doc(
            skill_id, key, "reference", title, body.strip(),
            source_url=url, describes=describes, captured_at=captured_at,
        )
        imported.append({"doc_key": key, "title": title, "source_url": url})

    return {
        "imported": imported, "skipped": [], "truncated": False, "rung": "llms-full",
        "notes": [f"parsed {len(sections)} sections from {url}"],
    }


async def _llms_txt_hint(origin: str) -> str | None:
    ok, _ = await _get_raw(f"{origin}/llms.txt", 1_000_000)
    if ok:
        return f"{origin}/llms.txt exists but is index-only (no llms-full.txt) — not parsed into docs"
    return None


async def _fetch_one(
    skill_id: str, url: str, doc_key_prefix: str | None, describes: str | None,
    captured_at: datetime, seen: dict[str, int], imported: list[dict], skipped: list[dict],
    sem: asyncio.Semaphore,
) -> None:
    async with sem:
        try:
            result = await webfetch.fetch(url)
        except Exception as e:  # noqa: BLE001 — connection failures land here, same as webfetch's own contract
            skipped.append({"url": url, "reason": str(e)})
            return
        if not result["ok"] or not result["text"].strip():
            skipped.append({"url": url, "reason": result["text"] or "empty extraction"})
            return
        path = urlsplit(url).path.strip("/") or url
        key = _keyed(doc_key_prefix, path, seen)
        doc = await repo.add_skill_doc(
            skill_id, key, "reference", path, result["text"],
            source_url=url, describes=describes, captured_at=captured_at,
        )
        imported.append({"doc_key": key, "title": doc["title"], "source_url": url})


async def _import_sitemap(
    skill_id: str, url: str, doc_key_prefix: str | None, max_pages: int,
    path_prefix: str, describes: str | None, notes: list[str],
) -> dict:
    """Rung 1: sitemap enumeration, falling back to trafilatura's bounded
    focused_crawler when no sitemap is published. Both are synchronous
    network calls internal to trafilatura — run off the event loop."""
    urls = await asyncio.to_thread(sitemap_search, url)
    if not urls:
        to_visit, _known = await asyncio.to_thread(focused_crawler, url, max_pages)
        urls = to_visit
        notes.append("no sitemap found; used trafilatura's focused_crawler fallback")

    candidates = [u for u in urls if urlsplit(u).path.startswith(path_prefix)]
    truncated = len(candidates) > max_pages
    candidates = candidates[:max_pages]

    captured_at = datetime.now(timezone.utc)
    seen: dict[str, int] = {}
    imported: list[dict] = []
    skipped: list[dict] = []
    sem = asyncio.Semaphore(_CONCURRENCY)
    await asyncio.gather(*(
        _fetch_one(skill_id, u, doc_key_prefix, describes, captured_at, seen, imported, skipped, sem)
        for u in candidates
    ))

    return {"imported": imported, "skipped": skipped, "truncated": truncated, "rung": "sitemap", "notes": notes}


async def _import_via_crawler(
    skill_id: str, url: str, doc_key_prefix: str | None, max_pages: int,
    path_prefix: str, describes: str | None, filter_mode: str = "pruning",
) -> dict:
    """Rung 2: hand off to `central_command.integrations.crawler` (built
    concurrently with this module) for JS-rendered sites. Imported lazily and
    called defensively — this module does not know its final signature."""
    try:
        from central_command.integrations import crawler
    except Exception as e:  # noqa: BLE001
        return _degraded("crawler", [f"cc-crawler unavailable: {e}"])

    # crawl_site(url, max_pages=, path_prefix=) returns
    # {pages: [{url, title, markdown}], truncated, errors, warnings, degraded}
    # where degraded is "" on success and a sentence when the service is down.
    try:
        result = await crawler.crawl_site(
            url, max_pages=max_pages, path_prefix=path_prefix, filter_mode=filter_mode,
        )
    except Exception as e:  # noqa: BLE001
        return _degraded("crawler", [f"cc-crawler failed: {e}"])

    if result.get("degraded"):
        return _degraded("crawler", [result["degraded"]])
    pages = result.get("pages", [])
    crawl_truncated = bool(result.get("truncated", False))
    crawl_notes = list(result.get("warnings", []))
    crawl_notes += [f"page failed: {e.get('url')}: {e.get('error')}" for e in result.get("errors", [])]
    if not pages:
        return _degraded("crawler", ["cc-crawler returned no pages", *crawl_notes])

    captured_at = datetime.now(timezone.utc)
    seen: dict[str, int] = {}
    imported: list[dict] = []
    skipped: list[dict] = []
    for page in pages:
        page_url = page.get("url") or url
        text = page.get("markdown") or page.get("content") or ""
        if not text.strip():
            skipped.append({"url": page_url, "reason": "empty extraction"})
            continue
        title = page.get("title") or urlsplit(page_url).path.strip("/") or page_url
        key = _keyed(doc_key_prefix, urlsplit(page_url).path or title, seen)
        await repo.add_skill_doc(
            skill_id, key, "reference", title, text,
            source_url=page_url, describes=describes, captured_at=captured_at,
        )
        imported.append({"doc_key": key, "title": title, "source_url": page_url})

    return {"imported": imported, "skipped": skipped, "truncated": crawl_truncated, "rung": "crawler", "notes": crawl_notes}


async def import_site(
    skill_id: str,
    url: str,
    *,
    doc_key_prefix: str | None = None,
    max_pages: int = 100,
    path_prefix: str | None = None,
    describes: str | None = None,
    render: bool = False,
    filter_mode: str = "pruning",
) -> dict:
    """Import a whole documentation site into `skill_id`. Climbs the ladder:
    llms-full.txt, then sitemap+trafilatura, then (only if `render=True`)
    browser rendering via cc-crawler. `filter_mode='llm'` asks cc-crawler for
    LLM-cleaned markdown via the cluster's LiteLLM (render path only —
    rungs 0/1 never touch a model)."""
    max_pages = min(max(1, max_pages), _MAX_PAGES_CAP)
    origin = _origin(url)
    prefix = path_prefix or urlsplit(url).path

    if render:
        return await _import_via_crawler(
            skill_id, url, doc_key_prefix, max_pages, prefix, describes,
            filter_mode=filter_mode,
        )

    llms_result = await _import_llms_full(skill_id, origin, doc_key_prefix, describes)
    if llms_result is not None:
        return llms_result

    notes: list[str] = []
    hint = await _llms_txt_hint(origin)
    if hint:
        notes.append(hint)
    return await _import_sitemap(skill_id, url, doc_key_prefix, max_pages, prefix, describes, notes)
