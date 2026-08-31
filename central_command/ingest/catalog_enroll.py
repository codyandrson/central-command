"""Sources-catalog slices 3+4: the deterministic tier's enrollment sweep.

The watcher writes inventory (`ingest/watcher.py`); this decides which of it a
steward must actually READ — the queue holds judgment, not existence
(Decision 1). Three outcomes per version, and two of them cost no agent time:

- **autofolded** — a formatting-only re-save whose whitespace-normalized text
  matches its older sibling diffs to nothing (Decision 4).
- **skipped** — extraction produced nothing readable, so there is no text to
  extract facts from; enrolling it would spend a run on an empty page.
- **enrolled** — a work item on the ordinary ledger (Decision 5), one per
  version, `kind='document'` so the dispatcher's existing `_handle_document`
  runs it unchanged. The diff shape is built HERE, at enroll time, which is
  why no handler needed touching.

Ordering and serialization are the ledger's, not ours: `received_at =
seen_at` puts a lineage's versions in the claim query's newest-first order, and
`thread_id = document_id` puts the whole lineage behind the relatedness lock,
so an older version's diff item only runs once its newer neighbour is done.
"""

from __future__ import annotations

import difflib
import uuid

from central_command import events
from central_command.db import repo

# Per-source pacing (Decision 2): the dispatch valves are global, so a
# 5,000-file backfill enrolling in one sweep would sit in front of live email
# in the backlog feed for weeks. Enrollment is idempotent and re-driven every
# walk, so the remainder simply enrolls on the next sweep.
DEFAULT_PACE_PER_WALK = 25

# A revision that changed more than this fraction of the newer version is a
# REWRITE, not an edit: its diff reads as two unrelated documents interleaved,
# so the older version's full text is the honest input (Decision 5).
REWRITE_RATIO = 0.5


def _norm(text: str) -> str:
    return "".join((text or "").split())


def _diff(older: dict, newer: dict, older_no: int, newer_no: int) -> tuple[str, int]:
    """(unified diff text, changed-line count)."""
    lines = list(difflib.unified_diff(
        (older["extracted_text"] or "").splitlines(),
        (newer["extracted_text"] or "").splitlines(),
        fromfile=f"v{older_no}", tofile=f"v{newer_no}", lineterm="",
    ))
    changed = sum(
        1 for line in lines
        if (line.startswith("+") and not line.startswith("+++"))
        or (line.startswith("-") and not line.startswith("---"))
    )
    return "\n".join(lines), changed


def catalog_document_input(row: dict, newer: dict | None) -> str:
    """The text handed to the steward for one catalog version.

    Everything the document itself says rides as DATA under a labelled
    delimiter — the steward is told upstream that document content is never
    instructions (same posture as `ledger.document_input`). `newer` is the
    immediately newer sibling when this version is NOT the head; None makes
    this a full read.
    """
    header = [
        f"Title: {row['title'] or row['lineage_key']}",
        f"Source: {row['source_id']}",
        f"Lineage key: {row['lineage_key']}",
        f"Version {row['version_no']} of {row['head_version_no']}",
        f"Seen at: {row['seen_at']}",
        "",
    ]

    if newer is None:
        if row["head_version_no"] > 1:
            header.append(
                "This is the CURRENT version. Older versions of this document "
                "will follow as their own items, each presented as the diff "
                "against its newer neighbour."
            )
            header.append("")
        header.append("--- document text ---")
        return "\n".join(header + [row["extracted_text"] or ""])

    newer_no = newer["version_no"]
    diff_text, changed = _diff(row, newer, row["version_no"], newer_no)
    newer_lines = len((newer["extracted_text"] or "").splitlines()) or 1

    header.append(
        f"This is the CHANGE HISTORY between v{row['version_no']} (seen "
        f"{row['seen_at']}) and v{newer_no} (seen {newer['seen_at']}) of this "
        f"document. v{newer_no} is ALREADY PROCESSED — a lineage is worked "
        "newest-first, one version at a time. Extract what v"
        f"{newer_no} introduced and what STOPPED BEING STATED at v{newer_no}, "
        "using the version dates as temporal bounds: a date the document "
        "itself states wins, and the version's seen-at date is the fallback "
        "and the bound. A fact absent from the newer version stopped being "
        "STATED then — not necessarily stopped being true."
    )
    header.append("")

    if changed > REWRITE_RATIO * newer_lines:
        header.append(
            f"The revision to v{newer_no} was a REWRITE ({changed} changed "
            f"lines against {newer_lines} lines of v{newer_no}), so a diff "
            f"would not be readable. Below is the FULL text of v"
            f"{row['version_no']} instead — read it as the superseded "
            "statement of this document."
        )
        header.append("")
        header.append(f"--- v{row['version_no']} full text ---")
        return "\n".join(header + [row["extracted_text"] or ""])

    header.append(f"--- unified diff v{row['version_no']} -> v{newer_no} ---")
    return "\n".join(header + [diff_text])


async def enroll_pending(source: dict, limit: int | None = None) -> dict:
    """Enroll this source's catalog backlog, newest first, up to the source's
    pace. Idempotent: a second sweep over the same versions enrolls nothing and
    writes nothing to the event log."""
    config = source.get("config") or {}
    if limit is None:
        limit = int(config.get("pace_per_walk") or DEFAULT_PACE_PER_WALK)

    rows = await repo.unenrolled_catalog_versions(source["id"], limit)
    enrolled = autofolded = skipped = 0

    for row in rows:
        meta = row["meta"] or {}
        if meta.get("extraction", "ok") != "ok":
            # Stamped, not evented: a source full of scans would otherwise
            # write one event per file for a thing that did nothing.
            await repo.update_version_meta(
                row["id"], {"skipped_enrollment": "no extractable text"}
            )
            skipped += 1
            continue

        older = await repo.catalog_sibling_version(
            row["document_id"], row["version_no"], newer=False
        )
        if older is not None and _norm(older["extracted_text"]) == _norm(
            row["extracted_text"]
        ):
            await repo.update_version_meta(row["id"], {"autofolded": True})
            await events.emit(
                "catalog.version.autofolded",
                ref_id=row["id"],
                payload={"document_id": row["document_id"],
                         "version_no": row["version_no"]},
                actor="watcher",
            )
            autofolded += 1
            continue

        newer = None
        if row["version_no"] < row["head_version_no"]:
            newer = await repo.catalog_sibling_version(
                row["document_id"], row["version_no"], newer=True
            )

        created = await repo.enroll_work_item(
            "wi_" + uuid.uuid4().hex[:12],
            repo.catalog_message_id(row["id"]),
            {
                "text": catalog_document_input(row, newer),
                "title": row["title"] or row["lineage_key"],
                "catalog": {
                    "document_id": row["document_id"],
                    "version_id": row["id"],
                    "version_no": row["version_no"],
                    "neighbor_version_id": newer["id"] if newer else None,
                },
            },
            thread_id=row["document_id"],   # the lineage IS the thread (Decision 5)
            # `backlog`, never `live`: the claim query orders live-feed-first,
            # so email always outranks catalog work. Deliberate.
            feed="backlog",
            source=source["id"],
            subject=row["title"] or row["lineage_key"],
            received_at=row["seen_at"],
            kind="document",
        )
        enrolled += int(created)

    summary = {
        "enrolled": enrolled,
        "autofolded": autofolded,
        "skipped": skipped,
        "remaining": await repo.catalog_backlog_remaining(source["id"]),
    }
    if enrolled:
        await events.emit(
            "catalog.enrolled", ref_id=source["id"],
            payload={"source_id": source["id"], **{
                k: summary[k] for k in ("enrolled", "autofolded", "skipped")
            }},
            actor="watcher",
        )
    return summary
