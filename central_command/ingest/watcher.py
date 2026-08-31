"""Sources-catalog slice 1: the filesystem watcher. Deterministic inventory
(Decision 1) — no LLM, no ledger writes. Walks a source's config.root, extracts
each file's text (`ingest/extract.py`), and records lineage/version/location
rows through `repo.record_observation`. Per-kind watcher logic stays code (the
closed-registry posture of ACTIONS/PACKS/HANDLERS) — a source `kind` this
module does not know raises loudly rather than silently doing nothing.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

from central_command import events
from central_command.db import repo
from central_command.ingest.extract import extract_text

# A 5,000-file backfill must not read a single ISO into memory. No measured
# document has needed more; raise this when one does.
MAX_CATALOG_FILE_BYTES = 50 * 1024 * 1024


def _matches(rel: str, patterns: list[str]) -> bool:
    return any(Path(rel).match(p) for p in patterns)


def _iter_files(root: Path, include: list[str], exclude: list[str]):
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        # Skip hidden dirs/files (any path segment starting with '.') always.
        if any(part.startswith(".") for part in rel.split("/")):
            continue
        if include and not _matches(rel, include):
            continue
        if exclude and _matches(rel, exclude):
            continue
        yield path, rel


async def _walk_filesystem(source: dict) -> dict:
    config = source.get("config") or {}
    root_str = config.get("root")
    if not root_str:
        raise ValueError(f"source {source['id']!r} has no config.root")
    root = Path(root_str)
    if not root.is_dir():
        raise ValueError(f"source {source['id']!r} root does not exist or is not a directory: {root}")

    include = config.get("include") or []
    exclude = config.get("exclude") or []

    seen_uris: list[str] = []
    new_lineages = new_versions = skipped_oversize = 0

    for path, rel in _iter_files(root, include, exclude):
        try:
            stat = path.stat()
        except OSError:
            continue
        if stat.st_size > MAX_CATALOG_FILE_BYTES:
            skipped_oversize += 1
            continue

        uri = "file://" + str(path.resolve())
        try:
            data = path.read_bytes()
        except OSError:
            # An unreadable file (permissions, vanished mid-walk) must not
            # abort the walk — and not marking it seen means an existing
            # location row goes `missing_at`, which is the honest record.
            continue
        seen_uris.append(uri)
        raw_sha256 = hashlib.sha256(data).hexdigest()
        text, status, error = extract_text(rel, data)
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest() if status == "ok" \
            else raw_sha256
        meta = {
            "size_bytes": stat.st_size,
            "mtime": stat.st_mtime,
            "raw_sha256": raw_sha256,
            "extraction": status,
        }
        if error:
            meta["error"] = error

        result = await repo.record_observation(
            source["id"], rel, uri, path.name, content_hash, text,
            "markitdown", meta,
        )
        if result["new_lineage"]:
            new_lineages += 1
        if result["new_version"]:
            new_versions += 1

    marked_missing = await repo.mark_locations_missing(source["id"], seen_uris)

    return {
        "files_seen": len(seen_uris),
        "new_lineages": new_lineages,
        "new_versions": new_versions,
        "marked_missing": len(marked_missing),
        "skipped_oversize": skipped_oversize,
    }


async def _walk_confluence(source: dict) -> dict:
    """Slice 5 (Decision 8 / the 2026-07-29 knowledge-layer record's Decision
    3): CQL incremental crawl as ground truth, ordered by `lastmodified asc`
    so the cursor always advances forward. No webhook accelerator here — the
    air-gapped work target has none reachable either, so CQL-only is already
    the target shape, not a stopgap.
    """
    from central_command.integrations import confluence

    if not confluence.configured():
        raise ValueError(
            f"source {source['id']!r} is a confluence source but Confluence is not configured"
        )

    config = source.get("config") or {}
    cql = config.get("cql")
    space_keys = config.get("space_keys")
    if cql:
        scope = cql
    elif space_keys:
        scope = "space in (" + ", ".join(f'"{k}"' for k in space_keys) + ")"
    else:
        raise ValueError(
            f"source {source['id']!r} needs config.cql or a non-empty config.space_keys"
        )

    full_cql = f"({scope}) and type = page"

    cursor = source.get("cursor") or {}
    last_modified = cursor.get("last_modified")
    if last_modified:
        # CQL's lastmodified clause is minute-granular — subtract an overlap
        # so a page modified in the same minute as the previous cursor isn't
        # skipped. Re-reads are harmless: the catalog's content-hash
        # idempotency (record_observation) writes no new version for a page
        # whose body hasn't actually changed. CAUTION: Confluence evaluates a
        # bare CQL date literal in the API USER's profile timezone, and the
        # cursor stores whatever timestamp Confluence returned — if that
        # account is not on UTC the window can shift by hours and SKIP pages.
        # Set the API account's timezone to UTC, or raise
        # config.cursor_overlap_minutes past the offset.
        overlap = int(config.get("cursor_overlap_minutes") or 2)
        cutoff = datetime.fromisoformat(last_modified) - timedelta(minutes=overlap)
        full_cql += f' and lastmodified >= "{cutoff.strftime("%Y-%m-%d %H:%M")}"'
    full_cql += " order by lastmodified asc"

    max_pages = config.get("max_pages_per_walk", 100)
    result = await confluence.search(full_cql, limit=max_pages)

    new_lineages = new_versions = pages_failed = 0
    max_seen = last_modified
    for row in result["results"]:
        page_id = row["id"]
        row_when = row.get("last_modified")
        if row_when and (max_seen is None or row_when > max_seen):
            max_seen = row_when

        try:
            page = (await confluence.get_page(page_id))["page"]
        except confluence.ConfluenceError:
            # A search hit we can't fetch (deleted between search and get,
            # transient error, ...) must not abort the walk — same posture as
            # the filesystem watcher's unreadable-file skip.
            pages_failed += 1
            continue

        raw_bytes = (page.get("body") or "").encode("utf-8")
        text, status, error = extract_text(f"{page_id}.html", raw_bytes)
        content_hash = (
            hashlib.sha256(text.encode("utf-8")).hexdigest() if status == "ok"
            else hashlib.sha256(raw_bytes).hexdigest()
        )
        meta = {
            "confluence_version": page.get("version"),
            "confluence_when": row_when,
            "space": page.get("space_key"),
            "extraction": status,
        }
        if error:
            meta["error"] = error

        obs = await repo.record_observation(
            source["id"], page_id, page.get("url") or f"confluence://{page_id}",
            page.get("title"), content_hash, text, "markitdown", meta,
        )
        if obs["new_lineage"]:
            new_lineages += 1
        if obs["new_version"]:
            new_versions += 1

    # NO missing-detection: a CQL increment only ever sees pages that changed
    # in the window, so calling mark_locations_missing here would flag every
    # untouched page as missing — the exact false-missing trap. The
    # doctrine's answer (Decision 8) is an occasional full-crawl reconcile;
    # slice 5 doesn't build that, so deletions go undetected until it does.

    return {
        "files_seen": len(result["results"]),
        "new_lineages": new_lineages,
        "new_versions": new_versions,
        "marked_missing": 0,
        "skipped_oversize": 0,
        "pages_failed": pages_failed,
        "last_modified": max_seen,
    }


_WATCHERS = {
    "filesystem": _walk_filesystem,
    "confluence": _walk_confluence,
}


async def walk_source(source: dict) -> dict:
    """Walk one source (a `source` row dict). Raises ValueError on an unknown
    kind or an unreadable root — both loud, matching the dispatcher's handler
    registry. Per-file read/extract failures never abort the walk; they land
    in that file's version meta instead.

    Emits `catalog.walk.completed` ONLY when something changed or the walk
    errored — a walk that found nothing authorises nothing and needn't be
    written (the material-events rule).
    """
    watcher = _WATCHERS.get(source["kind"])
    if watcher is None:
        raise ValueError(f"unknown source kind {source['kind']!r}")

    try:
        summary = await watcher(source)
    except Exception as exc:
        await events.emit(
            "catalog.walk.completed", ref_id=source["id"],
            payload={"error": str(exc)}, actor="watcher",
        )
        raise

    cursor = {"last_walk_at": datetime.now(timezone.utc).isoformat(), **summary}
    await repo.save_source_cursor(source["id"], cursor)

    if summary["new_lineages"] or summary["new_versions"] or summary["marked_missing"]:
        await events.emit(
            "catalog.walk.completed", ref_id=source["id"],
            payload=summary, actor="watcher",
        )
    return summary
