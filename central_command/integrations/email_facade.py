"""Client for the n8n email tool façade (cc-email-facade → lib-email-provider).

Gmail credentials live only inside n8n — Central Command never sees them. The
provider's list mode returns references only, a message record lists its
attachments by name, type and size, and one attachment's bytes are read with
`get_attachment` (DL-216; the bulk `include_attachments` flag stays refused). One write mode exists (`report_spam`, 2026-09-12) and it is
called from the Executor alone, after approval — the runtime tier only reads.
Untrusted email content passes through here as data; the callers
(ledger/agent prompts) own the data-not-commands discipline.

**Two providers behind one signature (Exchange native client design, 2026-09-25,
D1).** Every function here first asks `integrations/exchange.py:configured()`
and routes to the native EWS client when this deployment's mail is on-premises
Microsoft Exchange; otherwise it calls the n8n webhook exactly as before. The
n8n path below is byte-for-byte what it always was, and the routing lives INSIDE
each function rather than in a wrapper so a test that patches
`email_facade.list_refs` still patches the whole thing. `ExchangeError` is
mapped to `EmailFacadeError` at the seam: a caller's `except EmailFacadeError`
covers both providers.
"""

from __future__ import annotations

import base64
import binascii

import httpx

from central_command.config import settings
from central_command.contract.failures import ProviderStatusError


class EmailFacadeError(ProviderStatusError):
    pass


def _exchange():
    """The native Exchange client when this deployment has one, else None. The
    import is deferred so a Gmail install never imports exchangelib."""
    from central_command.integrations import exchange

    return exchange if exchange.configured() else None


def configured() -> bool:
    """True when the n8n email façade has an address AND a token. The default
    URL is never empty, so the token is the signal (as selfcheck and the EA
    calendar already treat it)."""
    return bool(settings.email_facade_url and settings.email_facade_token)


async def _call(payload: dict, timeout: float = 60.0) -> dict:
    if not configured():
        # No transient marker word here ('connection', 'timeout' — see
        # contract/failures.py): this must classify SEMANTIC and land FAILED,
        # never park for a retry that cannot succeed. Exchange has already
        # routed away before any caller reaches this n8n path.
        raise EmailFacadeError(
            f"email façade {payload.get('mode')} not configured — set "
            "CC_EMAIL_FACADE_URL and CC_EMAIL_FACADE_TOKEN"
        )
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                settings.email_facade_url,
                headers={"x-cc-token": settings.email_facade_token},
                json=payload,
            )
    except httpx.HTTPError as e:
        # An install without n8n (CC_ENABLE_N8N=0) has no façade at all; a raw
        # ConnectError here was a 500 + three toasts from the bulk-dismiss
        # preview (2026-09-18 Windows walk). Same error type as an HTTP failure.
        raise EmailFacadeError(
            f"email façade {payload.get('mode')} unreachable at {settings.email_facade_url}: {e}"
        ) from e
    if resp.status_code != 200:
        raise EmailFacadeError(
            f"email façade {payload.get('mode')} failed: HTTP {resp.status_code} "
            f"{resp.text[:200]}",
            status_code=resp.status_code,
        )
    return resp.json()


async def list_refs(scope_query: str) -> list[dict]:
    """References only ({uuid, conversation_id}) matching a Gmail search. On
    Exchange the same query is translated to EWS filters plus AQS free text
    (`exchange.translate_query`) and each ref also carries `message_id`."""
    provider = _exchange()
    if provider is not None:
        try:
            return await provider.list_refs(scope_query)
        except provider.ExchangeError as e:
            raise EmailFacadeError(str(e)) from e
    out = await _call({"mode": "list", "scope_query": scope_query})
    return out.get("messages", [])


async def get_message(uuid: str) -> dict:
    """One normalized message (from/subject/date/body_text/body_html/snippet,
    plus the unsubscribe headers and DKIM facts `contract.mail` reads:
    list_unsubscribe, list_unsubscribe_post, authentication_results[],
    dkim_signatures[] — a façade predating 2026-09-12 omits them, which reads
    as "not eligible", never as "eligible"). `attachments[]` lists each
    attached file ({filename, mime_type, size, attachment_id, is_inline}); a
    calendar part delivered inline carries its text as `inline_text`."""
    provider = _exchange()
    if provider is not None:
        try:
            return await provider.get_message(uuid)
        except provider.ExchangeError as e:
            raise EmailFacadeError(str(e)) from e
    out = await _call({"mode": "message", "source_ref": {"uuid": uuid}})
    messages = out.get("messages", [])
    if not messages:
        raise EmailFacadeError(f"message {uuid}: empty envelope from provider")
    return messages[0]


async def get_attachment(uuid: str, attachment_id: str) -> bytes:
    """One attachment's bytes, by the `attachment_id` a `get_message` record
    lists. Ids go stale (Gmail mints new ones on a message read), so a NO
    here is answered by ONE re-read of the message for fresh ids
    (`ingest/mail_attachments.py`), never by trusting a stored id twice.

    A read, called by the dispatcher at claim time and nowhere in `runtime/`:
    the bytes go straight to the isolated reader
    (`ingest/mail_attachments.py`) and only extracted text is kept. The
    façade's old `include_attachments` flag (every attachment of a message in
    one answer) stays refused; this mode returns one file the caller chose
    after reading its size."""
    provider = _exchange()
    if provider is not None:
        try:
            return await provider.get_attachment(uuid, attachment_id)
        except provider.ExchangeError as e:
            raise EmailFacadeError(str(e)) from e
    out = await _call(
        {"mode": "attachment", "source_ref": {"uuid": uuid, "attachment_id": attachment_id}},
        timeout=180.0,
    )
    data = out.get("data_b64")
    if not isinstance(data, str):
        raise EmailFacadeError(f"message {uuid}: attachment answer carried no data")
    try:
        return base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as e:
        raise EmailFacadeError(f"message {uuid}: attachment data is not base64 ({e})") from e


async def report_spam(uuid: str) -> dict:
    """Move one message to Spam and out of the inbox (Gmail +SPAM/-INBOX). The
    façade's ONE write mode: Executor-only, after approval; the OAuth stays in
    n8n. Returns the envelope ({ok, label_ids}).

    On Exchange this is a move to the Junk folder and the envelope is
    ({ok, folder}) — the Executor's result line reads whichever is present."""
    provider = _exchange()
    if provider is not None:
        try:
            return await provider.report_spam(uuid)
        except provider.ExchangeError as e:
            raise EmailFacadeError(str(e)) from e
    out = await _call({"mode": "report_spam", "source_ref": {"uuid": uuid}})
    if not out.get("ok"):
        raise EmailFacadeError(f"message {uuid}: report_spam refused: {str(out)[:200]}")
    return out


# --- Exchange-only writes (Exchange native client design, D6) -----------------
# EXECUTOR-ONLY, after approval. There is no n8n fallback because the Gmail
# façade has no such mode: `packs._offered()` WITHHOLDS mail.send / mail.move
# and their propose tools on Gmail, so an agent there is never taught a verb
# its mailbox cannot answer.


async def send(to: list[str], subject: str, body: str, cc: list[str] | None = None,
               reply_to_ref: str | None = None) -> dict:
    """Send one message as the operator. `reply_to_ref` threads the reply from
    the referenced message's OWN headers, never from the proposal."""
    provider = _exchange()
    if provider is None:
        raise EmailFacadeError(
            "mail.send needs a native mailbox client — the n8n Gmail façade has "
            "no send mode; set CC_EXCHANGE_URL/_USERNAME/_PASSWORD"
        )
    try:
        return await provider.send(to, subject, body, cc=cc, reply_to_ref=reply_to_ref)
    except provider.ExchangeError as e:
        raise EmailFacadeError(str(e)) from e


async def move(uuid: str, folder: str) -> dict:
    """Move one message to a named folder."""
    provider = _exchange()
    if provider is None:
        raise EmailFacadeError(
            "mail.move needs a native mailbox client — the n8n Gmail façade has "
            "no move mode; set CC_EXCHANGE_URL/_USERNAME/_PASSWORD"
        )
    try:
        return await provider.move(uuid, folder)
    except provider.ExchangeError as e:
        raise EmailFacadeError(str(e)) from e


async def list_folders() -> list[dict]:
    """The mailbox's folders with counts — [{name, path, total, unread}].

    On Gmail there are no folders: a label is not a folder and the façade has
    no list-labels mode, so this answers with the ONE scoping vocabulary
    `list_refs` actually accepts there. Saying that is the point — an agent
    told "folders are labels here" stops looking for a folder tree.
    """
    provider = _exchange()
    if provider is not None:
        try:
            return await provider.list_folders()
        except provider.ExchangeError as e:
            raise EmailFacadeError(str(e)) from e
    return [
        {"name": name, "path": f"in:{name}", "total": None, "unread": None}
        for name in ("inbox", "sent", "spam", "trash", "starred", "important",
                     "unread", "category:promotions", "category:social")
    ]
