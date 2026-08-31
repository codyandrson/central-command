"""Decision 9's freshness sweep — the wiki forgets deterministically.

The claims ledger (`gateway/wiki_claims.py`) records what each page asserts and
on which evidence VERSION; this module compares that against the catalog and
does the two things the comparison authorises:

- **Enrolls repair work** on the ordinary ledger (`kind='wiki_repair'`), one
  item per page, ONCE per stale-SET. Repair is scheduled, paced and queued —
  never event-triggered churn — and it rides the SAME thread key as the
  lineage's steward item (`thread_id = the stale catalog document id`), so the
  relatedness lock guarantees the graph is current before the page is repaired.
- **Annotates the page**, and only when the operator has flipped
  `CC_WIKI_ANNOTATION_MODE=auto`. That path is a fixed template over claim
  rows with no model in it, which is the whole reason it can be graduated at
  all; the default 'off' means staleness reaches pages only through gated
  repair proposals.

It lives in `ingest/` beside `catalog_enroll` because what it produces is
ledger enrollment, and it must stay importable from the heartbeat — i.e. it
may never import the gateway tier.
"""

from __future__ import annotations

import hashlib
import html
import logging
import uuid

from central_command import events
from central_command.config import settings
from central_command.db import repo

log = logging.getLogger(__name__)

DEFAULT_PAGE_CAP = 5

START = "<!-- cc-freshness-start -->"
END = "<!-- cc-freshness-end -->"


def stale_set_hash(claim_ids) -> str:
    """The change-detection key: a page's stale claim SET, order-independent.

    It is both the emit guard (an unchanged stale set re-emits nothing — never
    re-state an unchanged fact every tick) and half the work item's message
    id, so a changed set is new work and an unchanged one is already enrolled.
    """
    joined = "\x00".join(sorted(claim_ids))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:12]


def repair_message_id(page_ref: str, claim_ids) -> str:
    return f"<cc-wr-{page_ref}-{stale_set_hash(claim_ids)}@central_command.local>"


def _reasons(entry: dict) -> str:
    return "; ".join(entry["reasons"]) or entry["state"]


def repair_brief(page_ref: str, entries: list[dict]) -> str:
    """The labelled brief the wiki agent is shown. Everything it states comes
    from claim rows and the catalog — no model produced any of it."""
    lines = [
        "You are repairing a wiki page whose recorded evidence has moved on.",
        f"Page: {page_ref}",
        "",
        ("Read the page (confluence_get_page), re-read the evidence below in "
         "the catalog (catalog_get_document), and propose ONE "
         "confluence.update_page that states what the sources say NOW — "
         "citing them again so the claims ledger is rewritten against the "
         "current versions. A claim whose source is RESCINDED cannot be "
         "re-verified: rewrite it on other evidence you actually read, or "
         "remove it. Never restate a claim you did not re-check."),
        "",
        "--- claims needing repair ---",
    ]
    for e in entries:
        claim = e["claim"]
        lines.append(f"[{e['state']}] {claim['statement']}")
        lines.append(f"  why: {_reasons(e)}")
        lines.append(f"  evidence: {claim['evidence']}")
    return "\n".join(lines)


def _first_catalog_document(entries: list[dict]) -> str | None:
    for e in entries:
        for ev in e["claim"]["evidence"] or []:
            if ev.get("kind") == "catalog" and ev.get("source_ref"):
                return ev["source_ref"]
    return None


# --- annotation (the 'auto' rung) ---------------------------------------------


def render_panel(entries: list[dict]) -> str:
    """The bounded stale panel, storage format. Macro-minimal on purpose: an
    `info` macro is core Confluence on every edition/flavor, and a macro the
    instance lacks renders as a broken placeholder with no write-time error."""
    stale = [e for e in entries if e["state"] == "stale"]
    unsupported = [e for e in entries if e["state"] == "unsupported"]
    head = (
        f"This page has {len(stale)} claim(s) citing an UPDATED source and "
        f"{len(unsupported)} citing a RESCINDED source."
    )
    items = "".join(
        f"<li><strong>{html.escape(e['state'])}</strong>: "
        f"{html.escape(e['claim']['statement'])} "
        f"<em>({html.escape(_reasons(e))})</em></li>"
        for e in unsupported + stale
    )
    return (
        f"{START}"
        '<ac:structured-macro ac:name="info">'
        f"<ac:rich-text-body><p>{html.escape(head)}</p><ul>{items}</ul>"
        "<p>Recorded automatically from the claims ledger "
        "(system:wiki-freshness).</p>"
        "</ac:rich-text-body></ac:structured-macro>"
        f"{END}"
    )


def splice(body: str, panel: str) -> str:
    """Own the marked block idempotently and touch nothing else.

    Replace it where it exists; otherwise prepend. Authored prose outside the
    markers is never rewritten — that is what makes this an annotation rather
    than an edit.
    """
    body = body or ""
    start = body.find(START)
    end = body.find(END)
    if start != -1 and end != -1 and end > start:
        return body[:start] + panel + body[end + len(END):]
    return panel + body


def strip_panel(body: str) -> str:
    """The inverse — a page with no stale claims loses its panel."""
    return splice(body, "")


async def annotate_page(page_ref: str, entries: list[dict]) -> dict:
    """Write (or clear) one page's stale panel through the credentialed
    Confluence client. Only reached in 'auto' mode."""
    from central_command.integrations import confluence

    page = (await confluence.get_page(page_ref))["page"]
    body = page.get("body") or ""
    new_body = splice(body, render_panel(entries)) if entries else strip_panel(body)
    if new_body == body:
        return {"page_ref": page_ref, "changed": False}
    await confluence.update_page(
        page_ref, page["title"], new_body, page["version"],
    )
    await events.emit(
        "wiki.page.annotated",
        ref_id=page_ref,
        payload={"stale": sum(1 for e in entries if e["state"] == "stale"),
                 "unsupported": sum(1 for e in entries
                                    if e["state"] == "unsupported")},
        actor="system:wiki-freshness",
    )
    return {"page_ref": page_ref, "changed": True}


# --- the sweep ----------------------------------------------------------------


async def _already_emitted(page_ref: str, digest: str) -> bool:
    prior = await repo.list_events(
        kind="wiki.claims.stale", ref_id=page_ref, newest_first=True, limit=1,
    )
    return bool(prior) and (prior[0]["payload"] or {}).get("hash") == digest


async def sweep(limit: int = DEFAULT_PAGE_CAP) -> dict:
    """Compute every live claim's state, then act per page with anything
    stale or unsupported. Capped per tick, and idempotent inside the cap: a
    page whose stale set has not changed emits nothing and enrolls nothing."""
    by_page: dict[str, list[dict]] = {}
    for entry in await repo.wiki_claim_states():
        if entry["state"] == "supported":
            continue
        page_ref = entry["claim"]["page_ref"]
        if page_ref:
            by_page.setdefault(page_ref, []).append(entry)

    emitted = enrolled = annotated = 0
    pages = sorted(by_page)[:limit]
    for page_ref in pages:
        entries = by_page[page_ref]
        claim_ids = [e["claim"]["id"] for e in entries]
        digest = stale_set_hash(claim_ids)

        if not await _already_emitted(page_ref, digest):
            await events.emit(
                "wiki.claims.stale",
                ref_id=page_ref,
                payload={
                    "hash": digest,
                    "stale": sum(1 for e in entries if e["state"] == "stale"),
                    "unsupported": sum(1 for e in entries
                                       if e["state"] == "unsupported"),
                    "claim_ids": claim_ids,
                },
                actor="system:wiki-freshness",
            )
            emitted += 1

        created = await repo.enroll_work_item(
            "wi_" + uuid.uuid4().hex[:12],
            repair_message_id(page_ref, claim_ids),
            {"text": repair_brief(page_ref, entries),
             "page_ref": page_ref, "claim_ids": claim_ids},
            # The lineage IS the thread (Decision 5/9): the repair queues
            # behind the steward's item for the same document, so the graph is
            # current before the page is repaired. No catalog evidence — the
            # page is its own thread.
            thread_id=_first_catalog_document(entries) or page_ref,
            feed="backlog",
            source="wiki-freshness",
            subject=f"wiki freshness: {page_ref}",
            kind="wiki_repair",
        )
        enrolled += int(created)

        if settings.wiki_annotation_mode == "auto":
            try:
                if (await annotate_page(page_ref, entries))["changed"]:
                    annotated += 1
            except Exception as e:  # noqa: BLE001 — one unreachable page must
                # not stop the sweep, and the repair item is already enrolled.
                log.warning("wiki annotation failed for %s: %s", page_ref, e)

    return {
        "pages": len(by_page), "examined": len(pages), "emitted": emitted,
        "enrolled": enrolled, "annotated": annotated,
    }
