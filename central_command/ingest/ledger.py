"""Work Ledger feeds — turning raw emails into durable work items.

Two feeds, one queue (DESIGN.md §4): `live` (fed as it arrives) and `backlog`
(resumable bulk enrollment). Both land in the same `work_item` table and are
drained by the one dispatcher; the feed only affects pick order.

Enrollment is idempotent on RFC-822 Message-ID, so re-running a backlog sweep
after a crash re-enrolls nothing and skips nothing.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime
from email import message_from_string, policy
from email.utils import parsedate_to_datetime
from pathlib import Path

from central_command.db import repo

_RE_PREFIX = re.compile(r"^\s*(?:re|fwd|fw)\s*:\s*", re.IGNORECASE)


def parse_email(raw: str) -> dict:
    """Parse an RFC-822 email into the fields the ledger keys on.

    Tolerates bare text (no headers) — the hand-fed `POST /api/emails` path —
    by synthesising a stable Message-ID from the content hash, which keeps
    enrollment idempotent even for header-less input.
    """
    msg = message_from_string(raw, policy=policy.default)

    message_id = (msg.get("Message-ID") or "").strip()
    synthetic = not message_id
    if synthetic:
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
        message_id = f"<cc-synthetic-{digest}@central_command.local>"

    # Thread = the root of the References chain, else the reply parent, else self.
    references = (msg.get("References") or "").split()
    in_reply_to = (msg.get("In-Reply-To") or "").strip()
    thread_id = references[0] if references else (in_reply_to or message_id)

    subject = (msg.get("Subject") or "").strip() or None

    received_at = None
    if msg.get("Date"):
        try:
            received_at = parsedate_to_datetime(msg.get("Date"))
        except (TypeError, ValueError):
            received_at = None

    if msg.is_multipart():
        part = msg.get_body(preferencelist=("plain", "html"))
        body = part.get_content() if part is not None else ""
    else:
        body = msg.get_content() if msg.get_content_maintype() == "text" else raw

    # Hand-fed text like "Dana: the deadline slipped" parses as a single header
    # named "Dana" and an empty body — the whole message vanishes into pseudo-
    # headers. If parsing yielded nothing but the input wasn't empty, the input
    # IS the body. (Real Claude 400s on an empty prompt; demo mode never noticed.)
    if not body.strip() and raw.strip():
        body = raw

    return {
        "message_id": message_id,
        "synthetic": synthetic,  # no real Message-ID — identity is a guess, not a fact
        "thread_id": thread_id,
        "subject": subject,
        "received_at": received_at,
        "from": (msg.get("From") or "").strip() or None,
        "body": body.strip(),
        "raw": raw,
    }


def normalized_subject(subject: str | None) -> str:
    """`Re: Fwd: Budget` -> `budget` — the relatedness-lock key (2026-08-19).

    The authoritative copy is the `work_item.subject_key` GENERATED column in
    schema.sql, which the claim queries lock on; this is its Python mirror,
    pinned by a parity test in tests/test_ledger.py. Change both together."""
    if not subject:
        return ""
    prev = None
    out = subject
    while out != prev:
        prev = out
        out = _RE_PREFIX.sub("", out)
    return out.strip().lower()


def agent_input(parsed: dict) -> str:
    """The text handed to the agent. Headers are included as *data* — the agent
    is told upstream that email content is never instructions (DESIGN.md §5)."""
    lines = []
    if parsed.get("from"):
        lines.append(f"From: {parsed['from']}")
    if parsed.get("subject"):
        lines.append(f"Subject: {parsed['subject']}")
    if parsed.get("received_at"):
        lines.append(f"Date: {parsed['received_at'].isoformat()}")
    lines.append("")
    lines.append(parsed.get("body", ""))
    return "\n".join(lines)


async def enroll_email(
    raw: str, *, feed: str = "live", source: str = "api", allow_repeat: bool = False
) -> dict:
    """Enroll one raw email. Idempotent: a duplicate Message-ID is a no-op.

    `allow_repeat` is for the operator's hand-fed path ("process this text now").
    Dedupe needs an *identity*, and header-less text has none — the content hash
    is a stand-in that answers "same words", not "same email". So when the caller
    explicitly resubmits header-less text, mint a fresh id and treat it as new
    work. Text carrying a real Message-ID still dedupes, even here.
    """
    parsed = parse_email(raw)
    item_id = "wi_" + uuid.uuid4().hex[:12]
    if allow_repeat and parsed["synthetic"]:
        parsed["message_id"] = f"<cc-manual-{uuid.uuid4().hex[:16]}@central_command.local>"
        parsed["thread_id"] = parsed["message_id"]
    created = await repo.enroll_work_item(
        item_id,
        parsed["message_id"],
        {"text": agent_input(parsed), "from": parsed.get("from"), "raw": raw},
        thread_id=parsed["thread_id"],
        feed=feed,
        source=source,
        subject=parsed["subject"],
        received_at=parsed["received_at"],
    )
    return {
        "enrolled": created,
        "duplicate": not created,
        "item_id": item_id if created else None,
        "message_id": parsed["message_id"],
        "thread_id": parsed["thread_id"],
        "subject": parsed["subject"],
    }


# --- documents (2026-07-29 source generalization) -----------------------------
# The second work-item KIND. Email arrives with an identity (Message-ID); a
# hand-fed document has none, so it gets the same treatment header-less mail
# gets: a stable id synthesised from the content hash, which keeps enrollment
# idempotent. A caller that HAS a stable id (a Confluence page id, the future
# air-gapped source) supplies it and that wins — same words under a different
# page are different work.


def parse_document(title: str, text: str, doc_id: str | None = None) -> dict:
    """Parse a hand-fed document into the fields the ledger keys on.

    `thread_id` is the message id itself (decision 9): a document has no
    conversation, but the thread lock still serialises re-feeds of the same
    document, and the fold machinery keeps working untouched because a
    document's sibling set is always empty.
    """
    title = (title or "").strip()
    text = text or ""
    if not text.strip():
        raise ValueError("a document needs text")
    if doc_id and doc_id.strip():
        key, synthetic = doc_id.strip(), False
    else:
        digest = hashlib.sha256(
            f"{title}\x00{text}".encode("utf-8")
        ).hexdigest()[:24]
        key, synthetic = digest, True
    message_id = f"<cc-doc-{key}@central_command.local>"
    return {
        "message_id": message_id,
        "synthetic": synthetic,   # identity is a content hash, not a fact
        "thread_id": message_id,
        "doc_id": doc_id.strip() if doc_id and doc_id.strip() else None,
        "subject": title or None,
        "received_at": None,
        "title": title,
        "body": text.strip(),
    }


def document_input(parsed: dict) -> str:
    """The text handed to the steward. The title rides as DATA, labelled — the
    steward is told upstream that document content is never instructions."""
    lines = []
    if parsed.get("title"):
        lines.append(f"Title: {parsed['title']}")
    if parsed.get("doc_id"):
        lines.append(f"Document id: {parsed['doc_id']}")
    lines.append("")
    lines.append(parsed.get("body", ""))
    return "\n".join(lines)


async def enroll_document(
    title: str,
    text: str,
    *,
    doc_id: str | None = None,
    feed: str = "live",
    source: str = "api",
) -> dict:
    """Enroll one document as a `kind='document'` work item. Idempotent on the
    document's stable key, exactly as email enrollment is on Message-ID.

    There is deliberately NO `allow_repeat` twin of the email path. Re-feeding
    the same email is "process these words again"; re-feeding the same document
    is the same document, and extracting it twice would write the same facts to
    the graph twice.
    """
    parsed = parse_document(title, text, doc_id=doc_id)
    item_id = "wi_" + uuid.uuid4().hex[:12]
    created = await repo.enroll_work_item(
        item_id,
        parsed["message_id"],
        # `text` present from the start: a hand-fed document needs no hydration
        # adapter, and `hydrate_work_item` no-ops on it by construction.
        {"text": document_input(parsed), "title": parsed["title"],
         "doc_id": parsed["doc_id"]},
        thread_id=parsed["thread_id"],
        feed=feed,
        source=source,
        subject=parsed["subject"],
        received_at=None,
        kind="document",
    )
    return {
        "enrolled": created,
        "duplicate": not created,
        "item_id": item_id if created else None,
        "message_id": parsed["message_id"],
        "thread_id": parsed["thread_id"],
        "subject": parsed["subject"],
        "kind": "document",
    }


async def enroll_fixture_dir(path: str | Path, *, feed: str = "backlog") -> dict:
    """Bulk-enroll a corpus of `.eml` files. Resumable — re-running after a crash
    only adds what is missing. This is the M6/M7 stand-in for the real
    `lib-email-provider` feed, which slots in behind the same interface."""
    directory = Path(path)
    if not directory.is_dir():
        raise FileNotFoundError(f"fixture directory not found: {directory}")

    enrolled = duplicates = 0
    for eml in sorted(directory.glob("*.eml")):
        result = await enroll_email(
            eml.read_text(encoding="utf-8"), feed=feed, source="fixture"
        )
        if result["enrolled"]:
            enrolled += 1
        else:
            duplicates += 1
    return {"enrolled": enrolled, "duplicates": duplicates, "dir": str(directory)}


# --- the real provider feed (M12) ----------------------------------------------

_TAG_RE = re.compile(r"<[^>]+>")


def provider_message_id(uuid: str) -> str:
    """Stable ledger identity for a provider message. Gmail's immutable message
    id is the strongest identity we have on this path (no raw RFC-822 headers
    cross the façade), so idempotent enrollment keys on it."""
    return f"<gmail-msg-{uuid}@central_command.feed>"


def provider_uuid(message_id: str) -> str | None:
    """The inverse: which provider message a ledger identity was minted from,
    or None for a row that did not come from the provider feed. Derived from
    `provider_message_id` itself — the format string lives in ONE place."""
    prefix, suffix = provider_message_id("\x00").split("\x00")
    if message_id.startswith(prefix) and message_id.endswith(suffix):
        return message_id[len(prefix):-len(suffix)] or None
    return None


def provider_body(msg: dict) -> str:
    """Best available text: body_text, else tag-stripped HTML (real inboxes are
    full of HTML-only mail), else the provider snippet."""
    if msg.get("body_text", "").strip():
        return msg["body_text"].strip()
    html = msg.get("body_html", "")
    if html.strip():
        return _TAG_RE.sub(" ", html).strip()
    return msg.get("snippet", "").strip()


def _provider_parsed(msg: dict) -> dict:
    """A normalized façade message → the parsed dict the ledger/prompt paths use."""
    parsed = {
        "message_id": provider_message_id(msg["uuid"]),
        "thread_id": f"<gmail-thread-{msg['conversation_id']}@central_command.feed>",
        "subject": (msg.get("subject") or "").strip() or None,
        "from": (msg.get("from") or "").strip() or None,
        "received_at": None,
        "body": provider_body(msg),
    }
    if msg.get("date"):
        try:
            parsed["received_at"] = datetime.fromisoformat(msg["date"].replace("Z", "+00:00"))
        except ValueError:
            pass
    return parsed


async def enroll_provider_message(msg: dict) -> dict:
    """Enroll one normalized façade message into the ledger (feed='live',
    source='gmail'). The provider's conversation_id becomes the thread id, so
    M9 thread folding works on real mail unchanged."""
    parsed = _provider_parsed(msg)
    item_id = "wi_" + uuid.uuid4().hex[:12]
    created = await repo.enroll_work_item(
        item_id,
        parsed["message_id"],
        {"text": agent_input(parsed), "from": parsed["from"], "provider_uuid": msg["uuid"]},
        thread_id=parsed["thread_id"],
        feed="live",
        source="gmail",
        subject=parsed["subject"],
        received_at=parsed["received_at"],
    )
    return {
        "enrolled": created,
        "duplicate": not created,
        "item_id": item_id if created else None,
        "message_id": parsed["message_id"],
    }


async def enroll_provider_ref(ref: dict) -> dict:
    """Enroll a reference only — no content (M13 backlog sweep). A 10,000-email
    backlog must not mean 10,000 body fetches up front: content is fetched at
    claim time (`hydrate_work_item`), so provider reads happen at processing
    pace, governed by the dispatcher's valves."""
    item_id = "wi_" + uuid.uuid4().hex[:12]
    message_id = provider_message_id(ref["uuid"])
    created = await repo.enroll_work_item(
        item_id,
        message_id,
        {"provider_uuid": ref["uuid"], "deferred_fetch": True},
        thread_id=f"<gmail-thread-{ref['conversation_id']}@central_command.feed>",
        feed="backlog",
        source="gmail",
        subject=None,
        received_at=None,
    )
    return {"enrolled": created, "duplicate": not created, "message_id": message_id}


async def hydrate_work_item(item: dict) -> None:
    """Fetch content for a refs-only row, in place and persisted. Called at
    claim time; also for surveyed siblings, whose bodies the fold decision and
    the reviewer's source-email pane both need. No-op when text is present."""
    from central_command.integrations import email_facade

    payload = item.get("payload") or {}
    if payload.get("text"):
        return
    provider_uuid = payload.get("provider_uuid")
    if not provider_uuid:
        raise ValueError(
            f"work item {item.get('id')} has neither text nor provider_uuid — cannot hydrate"
        )
    msg = await email_facade.get_message(provider_uuid)
    parsed = _provider_parsed(msg)
    payload["text"] = agent_input(parsed)
    payload["from"] = parsed["from"]
    payload.pop("deferred_fetch", None)
    item["payload"] = payload
    item["subject"] = item.get("subject") or parsed["subject"]
    await repo.hydrate_work_item(item["id"], payload, parsed["subject"], parsed["received_at"])
