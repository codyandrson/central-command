"""Bulk dismissal, operator path (slice 1 of the 2026-08-17 design:
docs/superpowers/specs/2026-08-17-bulk-dismissal-design.md).

A bulk dismissal is a PINNED set, never a standing rule: the query is resolved
to concrete uuids at preview/execute time, and only those rows move. New mail
matching the same query later is untouched — that's deliberate, not a gap
(the design defers standing rules to trust-tier graduation).

Ungated: this is the operator path (The operator is the author, graph-curation
precedent), so there is no propose/approve step here — see the agent path
(slice 2) for the gated version riding the fold machinery.
"""

from __future__ import annotations

import hashlib

from central_command import events
from central_command.db import repo
from central_command.ingest.ledger import provider_message_id
from central_command.integrations import email_facade

# ponytail: Gmail's list mode caps at ~2,500 refs/query. feed.py's backlog
# sweeper handles this by bisecting into date windows, but that's a stateful,
# resumable sweep (durable cursor, retry/backoff) — not a clean fit for one
# synchronous resolve call. v1 refuses loudly instead of silently truncating;
# upgrade path if operators hit this often: factor the sweeper's window-bisect
# into a shared helper both paths call.
_OVER_CAP_HINT = (
    "query matched too many messages for a single list (provider cap ~2,500 refs) "
    "— narrow it, e.g. with before:/after: (\"category:promotions before:2026/01/01\")"
)


async def resolve_query(query: str) -> dict:
    """Resolve a Gmail search to the ledger rows it would touch. Only rows
    currently UNPROCESSED count — CLAIMED/terminal rows are never candidates
    (decision 2 of the design)."""
    try:
        refs = await email_facade.list_refs(query)
    except email_facade.EmailFacadeError as e:
        raise ValueError(f"{_OVER_CAP_HINT}: {e}") from e

    uuid_by_message_id = {provider_message_id(r["uuid"]): r["uuid"] for r in refs}
    rows = await repo.unprocessed_items_for_message_ids(list(uuid_by_message_id))
    message_ids = sorted(r["message_id"] for r in rows)
    return {
        "query": query,
        "provider_matches": len(refs),
        "unprocessed": len(message_ids),
        "message_ids": message_ids,
        "sample_uuids": [uuid_by_message_id[mid] for mid in message_ids[:5]],
    }


def _digest(message_ids: list[str]) -> str:
    """Order-independent fingerprint of the matched set — execute recomputes
    this and refuses if it drifted from what the operator previewed."""
    joined = "\n".join(sorted(message_ids))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


async def preview(query: str) -> dict:
    """resolve_query + up to 5 hydrated samples (from/subject/date) of what
    would actually be dismissed, plus the digest `execute` checks against."""
    resolved = await resolve_query(query)
    samples = []
    for uuid in resolved["sample_uuids"]:
        msg = await email_facade.get_message(uuid)
        samples.append({
            "from": msg.get("from"),
            "subject": msg.get("subject"),
            "date": msg.get("date"),
        })
    resolved["samples"] = samples
    resolved["match_digest"] = _digest(resolved["message_ids"])
    return resolved


async def execute(query: str, match_digest: str) -> dict:
    """Re-resolve and dismiss the matched UNPROCESSED rows. Refuses if the
    resolved set drifted since the operator's preview (new/removed mail)."""
    resolved = await resolve_query(query)
    digest = _digest(resolved["message_ids"])
    if digest != match_digest:
        raise ValueError(
            "matched set changed since preview (mail arrived or was processed) "
            "— preview again before executing"
        )

    rationale = f"operator bulk dismissal: {query}"
    dismissed = await repo.bulk_dismiss_unprocessed(resolved["message_ids"], rationale)

    result = {
        "query": query,
        "provider_matches": resolved["provider_matches"],
        "dismissed": dismissed,
        "digest": digest,
    }
    # After the write on purpose: this records a completed operator action and
    # authorises nothing (CLAUDE.md emit-before rule's stated exception,
    # `graph.curated` precedent).
    await events.emit("work.bulk_dismissed", payload=result, actor="operator")
    return result
