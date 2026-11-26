"""Gmail and Google Calendar through Google's own remote MCP servers (DL-237).

Google's Workspace AI Tools Addendum (effective 2027-02-01) wants access that
an AI agent drives to go through Google's MCP servers, not the REST APIs. With
`CC_GOOGLE_PROVIDER=mcp` the two façade functions (`email_facade._call`,
`calendar_facade._call`) hand the SAME payload to this module instead of the
n8n webhook (`n8n`, the default) or the REST client (`native`,
`integrations/google.py`). Nothing here runs, and nothing here is imported,
under either of the other two values.

**The contract is google.py's.** `mail_request(payload, timeout)` and
`calendar_request(payload)` take the payload the façades post and answer the
envelope the REST client answers, or raise the same `GoogleError` (mail 422 a
refusal, 503 a failure; calendar 500 a failure, `{ok: false, message}` a NO)
and `NotConfigured`. The payload validators, the message normaliser and the
event flags are google.py's own functions, called here, so a refusal of a bad
payload reads exactly as it does on the REST path. Where Google's MCP tools
cannot give what the REST client gave, the field is left empty the way the
normaliser writes "absent", and the gap is recorded in DL-237:

- mail `attachment` (one file's bytes): the Gmail MCP server has no attachment
  tool, so the mode is a 422 refusal and the file is named "not read" with the
  reason (`ingest/mail_attachments.py` treats a mailbox NO as a record). Mail
  send never reaches this module: `email_facade.send` refuses on every Gmail
  path before any provider is asked.
- `message`: read as `RAW` MIME and normalised by the standard library's
  `email` parser into the shape `normalize_message` reads. Google refuses RAW
  for a message with attachments or other non-body parts; that message is read
  as `FULL_CONTENT`, which carries no headers, so its `list_unsubscribe`,
  `list_unsubscribe_post`, `authentication_results` and `dkim_signatures` are
  empty (read as "not eligible" for a one-click unsubscribe, never "eligible"),
  `from`/`to`/`cc` are rebuilt from Google's address lists, an attachment's
  `size` is unknown (None) and `is_inline` False, a calendar part has no
  `inline_text`, and `body_truncated` is False.
- `list`: Gmail's MCP search is by THREAD (`search_threads`, at most 50 a
  page), so the references are every message of every matching thread
  (`get_thread` when a thread holds more than the page shows). That is a
  superset of the REST `messages.list` answer, never a subset: mail is re-read,
  never skipped. Under an inbox query a message that is not in the inbox is
  left out (`_in_scope`); for any other query the siblings stay, which is why
  bulk dismiss asks `email_facade.lists_whole_conversations()` and refuses a
  match it cannot pin. A thread Google withholds (`omittedContent`) cannot be
  read at all: when the reason is a missing approval the page is a 422 naming
  the grant command; when it is Google's own filtering (a suspicious sender,
  suspected prompt injection) the rest is listed and the count is logged at
  warning level, because anyone can send such a mail and a refusal would let
  one message stop every poll.
- `report_spam`: `mark_message_spam` answers no labels, so `label_ids` is [].
- calendar `create_event`: `create_event` takes no client-chosen event id, so
  the DL-219 duplicate check (Google's 409 on our id, read back as
  `already_existed`) cannot be made, and a read before the create would be the
  similarity guess DL-219 forbids. The client id is validated as before and NOT
  sent; a create retried after its answer was lost can land twice.
- `update_event` with attendees: the MCP tool adds and removes attendees
  rather than replacing the list, so the event is read first and the
  difference sent (read, then write: not one atomic PATCH).

**Approval is the operator's act, never the code's.** Google may answer any
call with `resultType: "input_required"` (a human approval, MCP form
elicitation). This module NEVER answers one: the call is refused (mail 422,
calendar `{ok: false}`) with a sentence naming the tool and the grant command
`python -m central_command.google_mcp_grant`, which the operator runs at a
terminal to see Google's question and type the choice. A denial
(`UserDeniedAction`) or an administrator block (`AdminPolicyBlock`) is a
refusal too; every other tool error, HTTP status or transport failure is the
outage class (mail 503, calendar 500), so it parks and the retry sweep re-drives
it once the `google` probe answers. A 401 refreshes the token once and re-sends;
a refresh that fails is `google.py`'s re-consent sentence.

**The wire.** Google's servers are stateless JSON-RPC over HTTP POST (the
2026-07-28 MCP protocol): every request carries the bearer token,
`MCP-Protocol-Version`, `Mcp-Method: tools/call`, `Mcp-Name: <tool>` (without
the last two Google answers -32020 "Header mismatch") and `params._meta` with
the protocol version, this client's name and version and the form-elicitation
capability. The answer is JSON or Server-Sent Events (`data:` lines); both are
parsed. Library check (2026-11-26): the `mcp` package pinned here (1.30.0,
`<2` in pyproject.toml) speaks protocol 2025-11-25 and sends neither header nor
understands `input_required`; `mcp` 2.3.0 (PyPI's newest) does, but needs
`httpx2` and `mcp-types` and a major-version move of a package pydantic-ai's MCP
client uses, which means regenerating `requirements.lock`, so this is a small
httpx client. `httpx-sse` (in the lock only as a dependency of mcp 1.x, not
declared here) parses a streamed response and refuses a non-SSE content type;
the dozen lines below parse the `data:` lines of a body already read.

**The credential** is google.py's: the same OAuth client and refresh token, the
same cached google-auth `Credentials` and the same refresh through
`google_auth_httplib2` (the `google` extra). No token, secret, authorization
code or message body is put in a message or a log line.

**Tier.** Imported lazily by the two façades and the grant command only;
`runtime/` may never import it (`tests/test_governance.py`).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from datetime import datetime
from email import message_from_bytes
from email.header import decode_header, make_header
from email.message import Message
from email.policy import compat32
from typing import Any

import httpx

from central_command import __version__
from central_command.config import settings
from central_command.integrations import google
from central_command.integrations import http as http_client
from central_command.integrations.google import (
    GoogleError,
    NotConfigured,
    answer_text,
    missing_settings,
    stamp,
)

__all__ = [
    "GoogleError",
    "NotConfigured",
    "answer_text",
    "calendar_request",
    "configured",
    "mail_request",
    "missing_settings",
    "selected",
    "stamp",
]

log = logging.getLogger(__name__)

PROTOCOL_VERSION = "2026-07-28"
CLIENT_NAME = "Central Command"
GRANT_COMMAND = "python -m central_command.google_mcp_grant"
CONSENT_COMMAND = "python -m central_command.google_consent"

# The tools this provider calls, by server. The grant command probes the reads
# and names the writes (`google_mcp_grant.py`).
GMAIL_READS = ("search_threads", "get_thread", "get_message")
GMAIL_WRITES = ("mark_message_spam",)
CALENDAR_READS = ("list_events", "get_event")
CALENDAR_WRITES = ("create_event", "update_event", "delete_event")

# `search_threads` pageSize maximum; 50 pages is the REST client's reference
# cap (5 pages of 500) counted in threads, each holding at least one message.
SEARCH_PAGE_SIZE = 50
LIST_MAX_PAGES = 50
# `search_threads` shows at most this many messages of a thread.
THREAD_SUBSET = 5
# Every event type, as the REST `events.list` returns them when `eventTypes`
# is unset (the MCP tool returns only four types by default).
EVENT_TYPES = (
    "DEFAULT",
    "OUT_OF_OFFICE",
    "FOCUS_TIME",
    "WORKING_LOCATION",
    "BIRTHDAY",
    "FROM_GMAIL",
)

_MAIL_PREFIX = "google-mcp mail: "
_CAL_PREFIX = "google-mcp calendar: "
_RAW_REFUSED = re.compile(r"RAW format is not supported", re.IGNORECASE)
_NOT_FOUND = re.compile(r"\bnot found\b", re.IGNORECASE)
_DENIED = re.compile(r"declined the confirmation request|administrator has blocked", re.IGNORECASE)
_DENIAL_REASONS = frozenset({"UserDeniedAction", "AdminPolicyBlock"})
# JSON-RPC -32021: the call needs form elicitation and the client did not
# declare it. Retrying cannot help, so it is a refusal.
_MISSING_CAPABILITY = -32021


def selected() -> bool:
    """THE switch for this module: the façades route here only when this is
    true (and Exchange is not configured, which still wins)."""
    return settings.google_provider == "mcp"


def configured() -> bool:
    """Selected AND every credential setting present."""
    return selected() and not missing_settings()


# --- failures ----------------------------------------------------------------


class _Fail(Exception):
    """A failure inside this module. `refusal` is the caller's to act on (mail
    422, calendar `{ok: false}`); anything else is the outage class."""

    def __init__(self, message: str, *, refusal: bool = False):
        super().__init__(message)
        self.refusal = refusal


class _ApprovalNeeded(_Fail):
    """Google answered `input_required`. Never answered here."""

    def __init__(self, server: str, tool: str):
        super().__init__(
            f"{server} tool {tool} needs the operator's approval: run {GRANT_COMMAND} "
            "and answer Google (never answered here)",
            refusal=True,
        )
        self.tool = tool


class _ToolError(_Fail):
    """`result.isError`: Google's text, its error code and reasons when it sent
    them (`_meta["google.com/error"]`)."""

    def __init__(self, tool: str, text: str, code: int | None, reasons: frozenset[str]):
        self.tool = tool
        self.text = text
        self.code = code
        self.reasons = reasons
        denied = bool(reasons & _DENIAL_REASONS) or bool(_DENIED.search(text))
        detail = f"code {code}, " if code is not None else ""
        super().__init__(
            f"{tool} answered an error ({detail}{text[:160]})"
            + (" - the approval was declined or blocked" if denied else ""),
            refusal=denied,
        )

    @property
    def not_found(self) -> bool:
        return self.code in (404, 410) or bool(_NOT_FOUND.search(self.text))


# --- the token ----------------------------------------------------------------


def _refresh(force: bool) -> str:
    """google.py's cached credentials, refreshed when missing, near expiry or
    after a 401 (`force`). Raises google.py's `_TokenUnavailable`."""
    import google_auth_httplib2
    from google.auth import exceptions as gexc

    creds = google._credentials()
    with google._creds_lock:
        if force or not creds.valid:
            try:
                creds.refresh(
                    google_auth_httplib2.Request(google._http(google.TOKEN_TIMEOUT_SECONDS))
                )
            except (gexc.RefreshError, gexc.TransportError) as e:
                raise google._token_failure(e) from None
        return str(creds.token)


async def _token(force: bool = False) -> str:
    return await asyncio.to_thread(_refresh, force)


# --- the wire -----------------------------------------------------------------


def _meta() -> dict:
    return {
        "io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
        "io.modelcontextprotocol/clientInfo": {"name": CLIENT_NAME, "version": __version__},
        "io.modelcontextprotocol/clientCapabilities": {"elicitation": {"form": {}}},
    }


def _headers(token: str, tool: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": PROTOCOL_VERSION,
        "Mcp-Method": "tools/call",
        "Mcp-Name": tool,
    }


def request_body(tool: str, arguments: dict, extra: dict | None = None) -> dict:
    """The JSON-RPC `tools/call`. `extra` is the grant command's retry
    (`requestState`, `inputResponses`), top level in `params` as Google's
    elicitation guide requires; the request path never passes it."""
    params: dict[str, Any] = {"name": tool, "arguments": arguments, "_meta": _meta()}
    if extra:
        params.update(extra)
    return {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params}


def _sse_messages(text: str) -> list:
    """The JSON of every event's `data:` lines (an event's lines joined by a
    newline, one leading space dropped, as the SSE format defines)."""
    out = []
    for block in re.split(r"\r?\n\r?\n", text):
        data = []
        for line in block.splitlines():
            if line.startswith("data:"):
                value = line[5:]
                data.append(value.removeprefix(" "))
        if data:
            try:
                out.append(json.loads("\n".join(data)))
            except ValueError:
                continue
    return out


def parse_answer(resp: httpx.Response) -> dict:
    """The JSON-RPC answer in a 2xx body, JSON or SSE."""
    text = resp.text
    ctype = resp.headers.get("content-type", "").lower()
    if "text/event-stream" in ctype or text.lstrip().startswith(("data:", "event:", "id:")):
        answers = [
            m
            for m in _sse_messages(text)
            if isinstance(m, dict) and ("result" in m or "error" in m)
        ]
        if not answers:
            raise _Fail("the event stream carried no JSON-RPC answer")
        return answers[-1]
    try:
        body = json.loads(text)
    except ValueError:
        raise _Fail("the answer is neither JSON nor an event stream") from None
    if not isinstance(body, dict):
        raise _Fail("the answer is not a JSON-RPC object")
    return body


def _client(timeout: float) -> httpx.AsyncClient:
    """The global trust knobs (`integrations/http.client_kwargs()`)."""
    return httpx.AsyncClient(timeout=timeout, **http_client.client_kwargs())


async def post(url: str, tool: str, arguments: dict, timeout: float, extra: dict | None = None):
    """One `tools/call` and its `result` as Google sent it, `input_required`
    included. A 401 refreshes the token once and re-sends once. Raises `_Fail`
    for an HTTP status, a transport failure, an unreadable answer or a JSON-RPC
    error, and google.py's `_TokenUnavailable` when no token can be had."""
    token = await _token()
    resp = None
    for attempt in range(2):
        try:
            async with _client(timeout) as client:
                resp = await client.post(
                    url, headers=_headers(token, tool), json=request_body(tool, arguments, extra)
                )
        except httpx.HTTPError as e:
            raise _Fail(
                f"{tool} request failed before a response (connection/transport): "
                f"{type(e).__name__}"
            ) from e
        if resp.status_code == 401 and attempt == 0:
            token = await _token(force=True)
            continue
        break
    assert resp is not None
    status = resp.status_code
    if status == 401:
        raise _Fail(
            f"provider auth failed (401) - re-run {CONSENT_COMMAND}, the stored refresh "
            "token looks stale"
        )
    if not 200 <= status < 300:
        sentence = google._auth_or_rate(status, "check the Google MCP server is enabled")
        raise _Fail(sentence or f"{tool} provider call failed ({status})")
    answer = parse_answer(resp)
    error = answer.get("error")
    if isinstance(error, dict):
        code = error.get("code")
        text = str(error.get("message") or "")[:160]
        raise _Fail(
            f"{tool} answered JSON-RPC error {code} ({text})",
            refusal=code == _MISSING_CAPABILITY,
        )
    result = answer.get("result")
    if not isinstance(result, dict):
        raise _Fail(f"{tool} answered without a result")
    return result


def _tool_error(tool: str, result: dict) -> _ToolError:
    content = result.get("content")
    text = " ".join(
        str(c.get("text") or "")
        for c in (content if isinstance(content, list) else [])
        if isinstance(c, dict) and c.get("type") == "text"
    ).strip()
    meta = result.get("_meta")
    err = meta.get("google.com/error") if isinstance(meta, dict) else None
    err = err if isinstance(err, dict) else {}
    code = err.get("code") if isinstance(err.get("code"), int) else None
    details = err.get("details")
    reasons = frozenset(
        str(d.get("reason"))
        for d in (details if isinstance(details, list) else [])
        if isinstance(d, dict) and d.get("reason")
    )
    return _ToolError(tool, text, code, reasons)


def structured(result: dict) -> dict:
    """The tool's data: `structuredContent`, else the JSON of its first text
    content, else `{}` (Google: "an empty JSON object represents zero matching
    items")."""
    data = result.get("structuredContent")
    if isinstance(data, dict):
        return data
    content = result.get("content")
    if isinstance(content, list) and content and isinstance(content[0], dict):
        try:
            parsed = json.loads(str(content[0].get("text") or ""))
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            return parsed
    return {}


async def call_tool(server: str, url: str, tool: str, arguments: dict, timeout: float) -> dict:
    """The request path's call: the data of a completed call. An
    `input_required` answer is NEVER answered (`_ApprovalNeeded`, and no second
    request is sent); `isError` is a `_ToolError`."""
    result = await post(url, tool, arguments, timeout)
    if result.get("resultType") == "input_required":
        log.info("google mcp: %s %s asked for an approval; refused, not answered", server, tool)
        raise _ApprovalNeeded(server, tool)
    if result.get("isError"):
        raise _tool_error(tool, result)
    return structured(result)


async def _gmail(tool: str, arguments: dict, timeout: float) -> dict:
    return await call_tool("Gmail", settings.google_mcp_gmail_url, tool, arguments, timeout)


async def _calendar(tool: str, arguments: dict) -> dict:
    return await call_tool(
        "Google Calendar",
        settings.google_mcp_calendar_url,
        tool,
        arguments,
        google.CALENDAR_TIMEOUT_SECONDS,
    )


# --- mail ---------------------------------------------------------------------


def _header_value(value: Any) -> str:
    """A header as Gmail's `payload.headers` gives it: unfolded, RFC 2047
    encoded words decoded, 8-bit bytes read as UTF-8."""
    text = str(value)
    if any("\udc80" <= ch <= "\udcff" for ch in text):
        text = text.encode("utf-8", "surrogateescape").decode("utf-8", "replace")
    text = re.sub(r"\r?\n(?=[ \t])", "", text)
    if "=?" in text:
        try:
            text = str(make_header(decode_header(text)))
        except (ValueError, LookupError, UnicodeError):
            pass
    return text


def mime_payload(part: Message, part_id: str = "") -> dict:
    """One MIME part as Gmail's `format=full` `MessagePart`: partId, mimeType,
    filename, headers in order, `body.data` the transfer-decoded bytes in
    base64url (the charset is decoded later, by `normalize_message`)."""
    out: dict[str, Any] = {
        "partId": part_id,
        "mimeType": part.get_content_type(),
        "filename": part.get_filename() or "",
        "headers": [{"name": k, "value": _header_value(v)} for k, v in part.items()],
    }
    if part.is_multipart():
        children = part.get_payload()
        out["body"] = {"size": 0}
        out["parts"] = [
            mime_payload(child, f"{part_id}.{i}" if part_id else str(i))
            for i, child in enumerate(children if isinstance(children, list) else [])
            if isinstance(child, Message)
        ]
        return out
    data = part.get_payload(decode=True)
    data = data if isinstance(data, bytes) else b""
    body: dict[str, Any] = {"size": len(data)}
    if data:
        body["data"] = _b64url(data)
    out["body"] = body
    return out


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _ms_from_rfc3339(value: Any) -> str | None:
    """Google's RFC 3339 `date` as the epoch-millisecond string `internalDate` is."""
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value)  # Python 3.11+ reads a trailing Z
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return str(int(dt.timestamp() * 1000))


async def _from_raw(mid: str, data: dict, timeout: float) -> dict:
    raw = data.get("raw")
    if not isinstance(raw, str) or not raw:
        raise _Fail("get_message answered RAW without the raw message")
    try:
        mime = google._b64url_bytes(raw)
    except ValueError:
        raise _Fail("get_message answered RAW that is not base64url") from None
    meta: dict = {}
    if any(k not in data for k in ("threadId", "internalDate", "snippet")):
        meta = await _gmail("get_message", {"messageId": mid, "messageFormat": "MINIMAL"}, timeout)

    def pick(key: str):
        return data[key] if key in data else meta.get(key)

    internal = pick("internalDate") or _ms_from_rfc3339(pick("date"))
    message: dict[str, Any] = {
        "id": pick("id") or mid,
        "threadId": pick("threadId"),
        "snippet": pick("snippet") or "",
        "payload": mime_payload(message_from_bytes(mime, policy=compat32)),
    }
    if internal:
        message["internalDate"] = internal
    return google.normalize_message(message)


_SPECIALS = set('()<>@,;:\\".[]')


def _address(addr: str, names: dict[str, str]) -> str:
    name = names.get(addr.lower())
    if not name:
        return addr
    if any(ch in _SPECIALS for ch in name):
        name = '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return f"{name} <{addr}>"


def _addresses(values: Any, names: dict[str, str]) -> str:
    return ", ".join(
        _address(a, names)
        for a in (values if isinstance(values, list) else [])
        if a and isinstance(a, str)
    )


def from_full_content(data: dict) -> dict:
    """A `FULL_CONTENT` message (no headers, no part tree) as the record
    `normalize_message` builds: every field it can fill, and the ones it
    cannot as `normalize_message` writes "absent" (see the module docstring)."""
    names: dict[str, str] = {}
    for entry in data.get("addressDisplayNames") or []:
        if isinstance(entry, dict) and entry.get("emailAddress") and entry.get("displayName"):
            names.setdefault(str(entry["emailAddress"]).lower(), str(entry["displayName"]))
    date = None
    if data.get("internalDate"):
        date = google._iso_from_ms(data["internalDate"])
    elif (ms := _ms_from_rfc3339(data.get("date"))) is not None:
        date = google._iso_from_ms(ms)
    attachments = [
        {
            "filename": a.get("filename") or "",
            "mime_type": a.get("mimeType") or "",
            "size": None,
            "attachment_id": a.get("id") or "",
            "is_inline": False,
        }
        for a in (data.get("attachments") or [])
        if isinstance(a, dict)
    ]
    sender = data.get("sender")
    out = {
        "uuid": data.get("id"),
        "conversation_id": data.get("threadId"),
        "from": _address(sender, names) if isinstance(sender, str) and sender else "",
        "to": _addresses(data.get("toRecipients"), names),
        "cc": _addresses(data.get("ccRecipients"), names),
        "subject": data.get("subject") or "",
        "list_unsubscribe": "",
        "list_unsubscribe_post": "",
        "authentication_results": [],
        "dkim_signatures": [],
        "date": date,
        "snippet": data.get("snippet") or "",
        "body_text": data.get("plaintextBody") or "",
        "body_html": data.get("htmlBody") or "",
        "body_truncated": False,
        "has_attachments": len(attachments) > 0,
        "attachments": attachments,
    }
    return {k: v for k, v in out.items() if not (k in ("uuid", "conversation_id") and v is None)}


async def _mail_message(payload: dict, ref: dict, timeout: float) -> dict:
    """mode `message`: RAW, else (Google refused RAW for this message)
    FULL_CONTENT."""
    mid = ref["uuid"]
    try:
        data = await _gmail("get_message", {"messageId": mid, "messageFormat": "RAW"}, timeout)
        msg = await _from_raw(mid, data, timeout)
    except _ToolError as e:
        if not _RAW_REFUSED.search(e.text):
            raise
        data = await _gmail(
            "get_message", {"messageId": mid, "messageFormat": "FULL_CONTENT"}, timeout
        )
        msg = from_full_content(data)
    return {
        "kind": "email",
        "mode": "message",
        "source_ref": payload.get("source_ref") or {},
        "messages": [msg],
    }


def _omitted(data: dict) -> dict[str, int]:
    omitted = data.get("omittedContent")
    counts = omitted.get("omittedReasonCounts") if isinstance(omitted, dict) else None
    out: dict[str, int] = {}
    for entry in counts if isinstance(counts, list) else []:
        if isinstance(entry, dict):
            n = entry.get("omittedCount")
            n = int(n) if isinstance(n, (int, str)) and str(n).isdigit() else 0
            if n:
                reason = str(entry.get("reason") or "OMISSION_REASON_UNSPECIFIED")
                out[reason] = out.get(reason, 0) + n
    return out


def _thread_complete(thread: dict, messages: list) -> bool:
    count = thread.get("messageCount")
    if isinstance(count, int) and not isinstance(count, bool):
        return count <= len(messages)
    return len(messages) < THREAD_SUBSET


# A plain `in:inbox` term, not negated. A query that uses OR or braces is left
# alone: its inbox term may be one alternative of several.
_IN_INBOX = re.compile(r"(?<![\w:-])in:inbox(?![\w-])", re.IGNORECASE)
_ALTERNATIVES = re.compile(r"[{}]|(?<!\S)OR(?!\S)")


def _in_scope(query: str, message: dict) -> bool:
    """Google searches by THREAD, so a matching thread comes back with all of
    its messages, the operator's own replies among them. For the inbox query
    (the feed's) a message Google labels and that is not in the inbox is not a
    match, as it is not for the REST client's message search. A message with no
    label list is kept: re-reading is acceptable, skipping is not."""
    labels = message.get("labelIds")
    if not isinstance(labels, list) or _ALTERNATIVES.search(query):
        return True
    if _IN_INBOX.search(query):
        return "INBOX" in labels
    return True


async def _mail_list(payload: dict, timeout: float) -> dict:
    """mode `list`: `search_threads` page by page, then every message of every
    matching thread (`get_thread` when the page shows only part of one) →
    the REST client's references-only envelope. Under an inbox query, a
    thread's messages that are not in the inbox are left out (`_in_scope`)."""
    query = payload["scope_query"]
    threads: list[dict] = []
    omitted: dict[str, int] = {}
    token = None
    pages = 0
    while True:
        args: dict[str, Any] = {"query": query, "pageSize": SEARCH_PAGE_SIZE}
        if token:
            args["pageToken"] = token
        data = await _gmail("search_threads", args, timeout)
        pages += 1
        for reason, n in _omitted(data).items():
            omitted[reason] = omitted.get(reason, 0) + n
        page = data.get("threads")
        threads.extend(t for t in (page if isinstance(page, list) else []) if isinstance(t, dict))
        token = data.get("nextPageToken")
        if not token:
            break
        if pages >= LIST_MAX_PAGES:
            raise _Fail(
                f"list pagination cap hit ({pages} pages fetched, more remain) - refusing to "
                "silently truncate; narrow scope_query",
                refusal=True,
            )
    if "DATA_PROTECTION_POLICY" in omitted:
        # A reason the operator can clear: the read tool is not approved yet.
        said = ", ".join(f"{r} {n}" for r, n in sorted(omitted.items()))
        raise _Fail(
            f"list: approve search_threads ({GRANT_COMMAND}); Google withheld matching "
            f"threads ({said}) - refusing a list that silently skips mail",
            refusal=True,
        )
    if omitted:
        # Google's own filtering (a suspicious sender, suspected prompt
        # injection, spam, confidential or encrypted mail): nothing here can
        # clear it, and anyone can send such a mail, so refusing the list would
        # let one message stop every poll. The rest is listed and the count is
        # logged at warning level: never silent, never a stalled feed.
        log.warning(
            "google mcp: list for a %d-thread match: Google withheld %s",
            len(threads),
            ", ".join(f"{r} {n}" for r, n in sorted(omitted.items())),
        )
    messages: list[dict] = []
    seen: set[str] = set()
    dropped = 0
    for thread in threads:
        tid = thread.get("id")
        shown = thread.get("messages")
        shown = [m for m in (shown if isinstance(shown, list) else []) if isinstance(m, dict)]
        if not _thread_complete(thread, shown):
            full = await _gmail(
                "get_thread", {"threadId": tid, "messageFormat": "METADATA_ONLY"}, timeout
            )
            got = full.get("messages")
            shown = [m for m in (got if isinstance(got, list) else []) if isinstance(m, dict)]
        for m in shown:
            if not _in_scope(query, m):
                continue
            mid, conv = m.get("id"), m.get("threadId") or tid
            if google._id_ok(mid) and google._id_ok(conv):
                if mid not in seen:
                    seen.add(mid)
                    messages.append({"uuid": mid, "conversation_id": conv})
            else:
                dropped += 1
                log.info("google mcp: list dropped an out-of-alphabet entry")
    if dropped and not messages:
        raise _Fail(
            f"list dropped ALL {dropped} entries as out-of-alphabet - id shape breach, "
            "refusing to return an empty envelope"
        )
    return {
        "kind": "email",
        "mode": "list",
        "source_ref": {"scope_query": query},
        "messages": messages,
        "dropped": dropped,
    }


async def _mail_report_spam(payload: dict, ref: dict, timeout: float) -> dict:
    """mode `report_spam`: `mark_message_spam`. The one mail write;
    Executor-only. The tool answers no labels."""
    await _gmail("mark_message_spam", {"messageId": ref["uuid"]}, timeout)
    return {
        "kind": "email",
        "mode": "report_spam",
        "source_ref": payload.get("source_ref") or {},
        "ok": True,
        "label_ids": [],
    }


_ATTACHMENT_REFUSED = (
    "attachment bytes are not available through Google's Gmail MCP server (it has no "
    "attachment tool) - the file is not read on CC_GOOGLE_PROVIDER=mcp"
)


async def _mail(payload: dict, timeout: float) -> dict:
    try:
        ref = google._validate_mail(payload)
        mode = payload["mode"]
        if mode == "message":
            return await _mail_message(payload, ref, timeout)
        if mode == "list":
            return await _mail_list(payload, timeout)
        if mode == "report_spam":
            return await _mail_report_spam(payload, ref, timeout)
        if mode == "attachment":
            raise _Fail(_ATTACHMENT_REFUSED, refusal=True)
        raise _Fail(f"mode '{mode}' is not served by the MCP client")
    except google._MailFail as e:
        message = str(e)[:500]
        raise GoogleError(message, status_code=google._mail_status(message)) from None
    except google._TokenUnavailable as e:
        raise GoogleError(
            (_MAIL_PREFIX + f"provider auth failed ({e}) - re-run {CONSENT_COMMAND}")[:500],
            status_code=503,
        ) from None
    except _Fail as e:
        raise GoogleError(
            (_MAIL_PREFIX + str(e))[:500], status_code=422 if e.refusal else 503
        ) from e.__cause__
    except Exception as e:
        raise GoogleError(
            (_MAIL_PREFIX + f"{payload.get('mode')} failed: {type(e).__name__}")[:500],
            status_code=503,
        ) from e


async def mail_request(payload: dict, timeout: float = 60.0) -> dict:
    """What `google.mail_request` answers for `payload`, through Google's Gmail
    MCP server: the envelope, or a `GoogleError` (422 refusal, 503 failure)."""
    if missing_settings():
        raise NotConfigured(", ".join(missing_settings()))
    return await _mail(payload, timeout)


# --- calendar -----------------------------------------------------------------


def _when(value: dict) -> str:
    return str(value["dateTime"])


async def _cal_list(req: dict) -> dict:
    data = await _calendar(
        "list_events",
        {
            "calendarId": req["calendar_id"],
            "startTime": req["time_min"],
            "endTime": req["time_max"],
            "pageSize": req["max_results"],
            "orderBy": "startTime",
            "eventType": list(EVENT_TYPES),
        },
    )
    raw = data.get("events", [])
    if not isinstance(raw, list):
        return {"ok": False, "message": "list - provider answered without an events array"}
    events = [
        {"event_id": google._get(ev, "id") or "", **google._flags(ev)}
        for ev in raw
        if isinstance(ev, dict)
    ]
    return {
        "ok": True,
        "events": events,
        "truncated": bool(data.get("nextPageToken")) or len(events) >= req["max_results"],
    }


async def _read_event(req: dict) -> dict:
    return await _calendar(
        "get_event", {"calendarId": req["calendar_id"], "eventId": req["event_id"]}
    )


async def _cal_get(req: dict) -> dict:
    try:
        ev = await _read_event(req)
    except _ToolError as e:
        if e.not_found and not e.refusal:
            return {"ok": False, "message": f"get - event not found ({e.code or 404})"}
        raise
    if not ev.get("id"):
        return {"ok": False, "message": "get - provider answered without an event"}
    flags = google._flags(ev)
    return {
        "ok": True,
        "event": {
            "event_id": ev["id"],
            "status": ev.get("status") or "",
            "start": flags["start"],
            "end": flags["end"],
            "all_day": flags["all_day"],
            "summary": flags["summary"],
            "location": flags["location"],
            "attendee_count": flags["attendee_count"],
            "organizer": google._get(ev.get("organizer"), "email") or "",
            "organizer_is_self": flags["organizer_is_self"],
            "has_agenda": flags["has_agenda"],
            "declined": flags["declined"],
        },
    }


async def _cal_create(req: dict) -> dict:
    """`create_event`, notifications to all. The client-chosen id the
    validator accepted (`__body["id"]`) is NOT sent: the tool has no field for
    it (DL-237)."""
    body = req["__body"]
    ev = await _calendar(
        "create_event",
        {
            "calendarId": req["calendar_id"],
            "summary": body["summary"],
            "startTime": _when(body["start"]),
            "endTime": _when(body["end"]),
            "description": body["description"],
            "attendees": body["attendees"],
            "notificationLevel": "ALL",
        },
    )
    return {"ok": True, "event": google._short_event(ev)}


async def _cal_update(req: dict) -> dict:
    body = req["__body"]
    args: dict[str, Any] = {
        "calendarId": req["calendar_id"],
        "eventId": req["event_id"],
        "notificationLevel": "ALL",
    }
    if "summary" in body:
        args["summary"] = body["summary"]
    if "start" in body:
        args["startTime"] = _when(body["start"])
    if "end" in body:
        args["endTime"] = _when(body["end"])
    if "description" in body:
        args["description"] = body["description"]
    if "attendees" in body:
        # The REST PATCH replaced the list; the tool adds and removes, so the
        # difference against the event as it is now is sent.
        current = await _read_event(req)
        have = [
            str(a["email"])
            for a in (current.get("attendees") or [])
            if isinstance(a, dict) and a.get("email")
        ]
        want = [str(a["email"]) for a in body["attendees"]]
        have_low = {a.lower() for a in have}
        want_low = {a.lower() for a in want}
        added = [{"email": a} for a in want if a.lower() not in have_low]
        removed = [a for a in have if a.lower() not in want_low]
        if added:
            args["addedAttendees"] = added
        if removed:
            args["removedAttendeeEmails"] = removed
    ev = await _calendar("update_event", args)
    return {"ok": True, "event": google._short_event(ev)}


async def _cal_delete(req: dict) -> dict:
    """`delete_event`, notifications to all. An event already cancelled is
    success, as REST's 410 was: on a tool error the event is read, and only a
    `cancelled` one turns the error into success."""
    try:
        await _calendar(
            "delete_event",
            {
                "calendarId": req["calendar_id"],
                "eventId": req["event_id"],
                "notificationLevel": "ALL",
            },
        )
    except _ToolError as e:
        if e.refusal:
            raise
        try:
            ev = await _read_event(req)
        except _Fail:
            raise e from None
        if ev.get("status") != "cancelled":
            raise
    return {"ok": True, "deleted": req["event_id"]}


async def _calendar_answer(payload: dict) -> dict:
    try:
        req = google._validate_calendar(payload)
    except google._CalFail as e:
        raise GoogleError(str(e), status_code=500) from None
    op = google._CAL_OP[req["mode"]]
    handler = {
        "list": _cal_list,
        "get_event": _cal_get,
        "create_event": _cal_create,
        "update_event": _cal_update,
        "delete_event": _cal_delete,
    }[req["mode"]]
    try:
        return await handler(req)
    except google._TokenUnavailable as e:
        raise GoogleError(
            _CAL_PREFIX + f"{op} - provider auth failed ({e}) - re-run {CONSENT_COMMAND}",
            status_code=500,
        ) from None
    except _Fail as e:
        if e.refusal:
            return {"ok": False, "message": f"{op} - {e}"}
        raise GoogleError(_CAL_PREFIX + f"{op} - {e}", status_code=500) from e.__cause__
    except Exception as e:
        raise GoogleError(
            _CAL_PREFIX + f"{op} - provider call failed ({type(e).__name__})", status_code=500
        ) from e


async def calendar_request(payload: dict) -> dict:
    """What `google.calendar_request` answers for `payload`, through Google's
    Calendar MCP server: the envelope (`ok` true, or false for a NO and for an
    approval Google wants), or a `GoogleError` with status 500."""
    if missing_settings():
        raise NotConfigured(", ".join(missing_settings()))
    return await _calendar_answer(payload)
