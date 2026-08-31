"""Decision 9's claims capture — every wiki citation becomes a claim row.

Lives in the GATEWAY tier, not in an executor handler: a handler sees only its
own arguments, and a claim needs the proposal's EVIDENCE plus the fact that the
execution actually succeeded. It runs after `set_proposal_executed`, from
`_record_execution_success`, so the approve path and the retried path both get
it from one place.

Two rules the shape encodes:

- **Non-fatal, always.** The world already changed and is already recorded; a
  claims-write failure must never turn an executed proposal into a failed one.
  It emits `wiki.claims.capture_failed` and returns.
- **The drafter is the identity.** `proposal.agent_id` is the DRAFTER (the
  provenance rule), which is exactly who authored the page text being claimed.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from central_command import events
from central_command.db import repo

log = logging.getLogger(__name__)

_PAGE_CAPABILITIES = ("confluence.create_page", "confluence.update_page")

# The create handler reports "page <id> '<title>' created: <url>" — the page id
# exists nowhere else by the time the gateway sees the outcome (create args
# cannot carry an id that does not exist yet). Parsed rather than plumbed
# through a new ExecutionOutcome field: one regex against the handler's own
# format string, and a miss is recorded as a capture failure, never guessed.
_CREATED_PAGE = re.compile(r"page (\S+) '.*' created:")


def _stamp(entry: dict, heads: dict[str, dict]) -> dict:
    """One evidence entry plus the version recorded at write time."""
    stamped = dict(entry)
    kind = entry.get("kind")
    if kind == "catalog":
        head = heads.get(entry.get("source_ref")) or {}
        stamped["version"] = head.get("head_version")
    elif kind in ("jira", "confluence"):
        stamped["stamped_at"] = datetime.now(timezone.utc).isoformat()
    # 'graph' and 'operator' keep their source_ref as-is: a graph edge uuid and
    # an append-only record pointer do not version-drift (Decision 9).
    return stamped


async def capture(proposal_id: str, result_text: str) -> dict:
    """Write the claim rows for an executed wiki page proposal.

    Returns a small summary for the caller's logs; never raises.
    """
    try:
        prop = await repo.load_proposal(proposal_id)
        if prop is None:
            return {"captured": 0}
        actions = [a for a in (prop["actions"] or [])
                   if a.get("capability") in _PAGE_CAPABILITIES]
        if not actions:
            return {"captured": 0}

        page_refs, updated = [], []
        for a in actions:
            if a["capability"] == "confluence.update_page":
                page_refs.append(a["arguments"]["page_id"])
                updated.append(a["arguments"]["page_id"])
            else:
                match = _CREATED_PAGE.search(result_text or "")
                if match is None:
                    raise ValueError(
                        "no created page id in the execution result: "
                        f"{result_text!r}"
                    )
                page_refs.append(match.group(1))

        evidence = prop["evidence"] or []
        heads = await repo.catalog_document_heads(sorted({
            e.get("source_ref") for e in evidence
            if e.get("kind") == "catalog" and e.get("source_ref")
        }))
        stamped = [_stamp(e, heads) for e in evidence]

        # Regeneration is idempotent: an update replaces the page's claims
        # wholesale, and the old set is retired (kept), never deleted.
        for page_ref in dict.fromkeys(updated):
            await repo.retire_wiki_claims(page_ref)

        rows = [
            {"page_ref": page_ref, "proposal_id": proposal_id,
             "statement": e.get("claim") or "", "evidence": [s]}
            for page_ref in dict.fromkeys(page_refs)
            for e, s in zip(evidence, stamped)
        ]
        await repo.insert_wiki_claims(rows)
        return {"captured": len(rows), "pages": list(dict.fromkeys(page_refs))}
    except Exception as e:  # see the module docstring: capture is never fatal
        log.exception("wiki claims capture failed for %s", proposal_id)
        await events.emit(
            "wiki.claims.capture_failed",
            ref_id=proposal_id,
            payload={"error": f"{type(e).__name__}: {e}"},
            actor="system",
        )
        return {"captured": 0, "error": str(e)}
