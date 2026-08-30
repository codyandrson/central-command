"""Sources-catalog slice 1: the filesystem watcher. Deterministic inventory
(Decision 1) — no LLM, no ledger writes. Walks a source's config.root, extracts
each file's text (`ingest/extract.py`), and records lineage/version/location
rows through `repo.record_observation`. Per-kind watcher logic stays code (the
closed-registry posture of ACTIONS/PACKS/HANDLERS) — a source `kind` this
module does not know raises loudly rather than silently doing nothing.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
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


_WATCHERS = {
    "filesystem": _walk_filesystem,
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
