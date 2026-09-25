"""Native Microsoft Exchange client — EWS over NTLM, behind the seams that
already exist (Exchange native client design, 2026-09-25).

The second Central Command deployment lives on an air-gapped Windows network
whose mail is on-premises Exchange: EWS SOAP at `https://<host>/ews/exchange.asmx`,
no Microsoft Graph, no OAuth. Two authentication layers at once — a PKI client
certificate for the TLS handshake and NTLM (`DOMAIN\\user` + password) at the
application layer — and a server certificate signed by an internal CA.

**The toolbox rule, second application (D1).** `integrations/jira.py` says it:
n8n earns its place where it already solved a hard integration problem (the
Gmail OAuth in cc-email-facade); a plain credentialed API is better served by a
native client that lives in git and is tested by the suite. So this module is
not a new surface — it is the second implementation of two existing ones.
`integrations/email_facade.py` and `integrations/calendar_facade.py` keep their
signatures and every caller, and each function routes here when
`configured()` is true. Unset `CC_EXCHANGE_*` and the Gmail path is back; no
deploy, no code path lost.

**The trust boundary is unchanged.** Nothing here is reachable from `runtime/`:
the read helpers are called by the feed, the ledger and the mail read tools
through the façades, and `send`/`move`/`create_event`/`update_event`/
`delete_event` are called ONLY from `gateway/executor.py`, after a human
approved the proposal that named them
(`tests/test_ea_calendar.py::test_only_the_executor_calls_the_calendar_write_helpers`
walks the sources for the calendar three). Mail content passes through here as
data; the callers own the data-not-commands discipline, and a body reaches a
model only through `ingest/mailtext.py`.

**Trust comes from the global knobs, never a per-integration one (D3).** The
site's notes proposed `CC_EXCHANGE_CERT_PATH` / `CC_EXCHANGE_VERIFY_TLS`;
the operator's recorded decision (2026-09-23) is ONE trust surface. This client
reads `integrations/http.py:client_kwargs()` — `CC_CA_BUNDLE` for the internal
CA, `CC_CLIENT_CERT`/`CC_CLIENT_KEY` for the PKI identity — exactly as the
Jira, Graphiti and LiteLLM clients do, and honours `CC_TLS_INSECURE=1` with the
same single WARN every consumer prints. Mechanically that is an
`requests.adapters.HTTPAdapter` subclass installed as
`BaseProtocol.HTTP_ADAPTER_CLS` (exchangelib's own `NoVerifyHTTPAdapter` is the
precedent); NTLM stays the `auth_type` and `autodiscover` is always False — the
site knows its endpoint and the air gap may not answer DNS.

**exchangelib is synchronous** (`requests`), so every public function here is
`async` and runs the library in `asyncio.to_thread`. One `Account` is built
lazily per process under a lock and rebuilt when the settings fingerprint
changes, because an `Account` holds a session pool and building one costs a
round trip.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shlex
import threading
from datetime import datetime, timedelta, timezone

from central_command.config import settings
from central_command.integrations import http as http_client

log = logging.getLogger(__name__)


class ExchangeError(Exception):
    """Every failure this module raises. The façades map it to their own error
    type so callers' `except EmailFacadeError` / `except CalendarFacadeError`
    keep working across the cutover."""


# A list read that returns more than this is a scope failure, not a result:
# say so rather than silently showing a prefix (the no-silent-caps rule the
# backlog sweeper's bisect depends on — it bisects the window on ANY list
# failure).
MAX_REFS = 2500

# Distinguished folder names EWS knows by id, accepted in `in:` and in
# `mail.move`'s `folder` argument alongside display names and paths. The value
# is the `Account` attribute that resolves it.
DISTINGUISHED_FOLDERS = {
    "inbox": "inbox",
    "junkemail": "junk",
    "junk": "junk",
    "deleteditems": "trash",
    "trash": "trash",
    "drafts": "drafts",
    "sentitems": "sent",
    "sent": "sent",
    "archive": "archive_msg_folder_root",
}

_lock = threading.Lock()
_account = None
_fingerprint: tuple | None = None
_insecure_warned = False


def configured() -> bool:
    """True when this deployment has an Exchange mailbox — the ONE switch the
    façades route on. URL, username and password; `CC_EXCHANGE_EMAIL` is
    optional (the username serves when it is a UPN)."""
    return bool(
        settings.exchange_url
        and settings.exchange_username
        and settings.exchange_password
    )


def _tls_insecure() -> bool:
    """`CC_TLS_INSECURE=1` — read from the environment rather than a Settings
    field because that is where it lives: it is a DEPLOYMENT knob every
    consumer (curl, uv, npm, podman, LiteLLM) reads for itself, and `.env` is
    mirrored into `os.environ` by `config.py`."""
    return (os.environ.get("CC_TLS_INSECURE") or "").strip() in ("1", "true", "yes")


def _trust() -> tuple[object, object]:
    """`(cert, verify)` for the EWS session, from the GLOBAL trust knobs.

    Prefer the CA bundle over insecure; both are the operator's. The insecure
    WARN is printed ONCE per process at first use — a per-request warning
    becomes noise nobody reads, and this one has to be read.
    """
    global _insecure_warned

    kwargs = http_client.client_kwargs()
    cert = kwargs.get("cert")
    verify = kwargs.get("verify", True)
    if _tls_insecure():
        verify = False
        if not _insecure_warned:
            _insecure_warned = True
            log.warning(
                "WARN CC_TLS_INSECURE=1 — the Exchange EWS endpoint's server "
                "certificate is NOT verified. Prefer CC_CA_BUNDLE with your "
                "internal CA; this covers every EWS request this process makes."
            )
    return cert, verify


def _adapter_cls():
    """An `HTTPAdapter` subclass that injects `cert=`/`verify=` from the global
    knobs, built here so importing this module never imports `requests`.

    exchangelib instantiates it itself (`BaseProtocol.get_adapter`, protocol.py
    ~line 185, with pool_block/pool_connections/pool_maxsize/max_retries), so
    the settings cannot be passed in — they are read at send time, which is
    also what lets an operator fix a CA path without restarting the process.
    Both hooks are overridden for the same reason `NoVerifyHTTPAdapter`
    overrides both (protocol.py ~line 634): `cert_verify` is the pre-2.32.3
    seam, `get_connection_with_tls_context` the one requests ≥ 2.32.3 uses.
    """
    import requests.adapters

    class _CCTrustAdapter(requests.adapters.HTTPAdapter):
        def cert_verify(self, conn, url, verify, cert):  # noqa: D102
            our_cert, our_verify = _trust()
            super().cert_verify(conn=conn, url=url, verify=our_verify,
                                cert=our_cert if our_cert is not None else cert)

        def get_connection_with_tls_context(self, request, verify, proxies=None, cert=None):  # noqa: D102
            our_cert, our_verify = _trust()
            return super().get_connection_with_tls_context(
                request=request, verify=our_verify, proxies=proxies,
                cert=our_cert if our_cert is not None else cert,
            )

    return _CCTrustAdapter


def _email_address() -> str:
    """The mailbox's primary SMTP address. `CC_EXCHANGE_EMAIL` when set, else
    the username when it is a UPN — a `DOMAIN\\user` username is not an address
    and exchangelib refuses one, so say that rather than letting it
    `ValueError` out of the library."""
    email = (settings.exchange_email or "").strip()
    if email:
        return email
    username = (settings.exchange_username or "").strip()
    if "@" in username:
        return username
    raise ExchangeError(
        "CC_EXCHANGE_EMAIL is unset and CC_EXCHANGE_USERNAME is not a UPN "
        "(it looks like DOMAIN\\user) — set CC_EXCHANGE_EMAIL to the mailbox's "
        "primary SMTP address"
    )


def _build_account():
    """One `Account` against the configured endpoint. Synchronous — callers run
    it in a thread."""
    from exchangelib import Account, Configuration, Credentials, FaultTolerance
    from exchangelib.protocol import BaseProtocol
    from exchangelib.transport import NTLM

    BaseProtocol.HTTP_ADAPTER_CLS = _adapter_cls()
    config = Configuration(
        service_endpoint=settings.exchange_url,
        credentials=Credentials(settings.exchange_username, settings.exchange_password),
        auth_type=NTLM,
        # The site's throttling arrives INSIDE the SOAP body as
        # `ErrorServerBusy` with a `BackOffMilliseconds` value, never as HTTP
        # 429 — `FaultTolerance` is the policy that honours it. `FailFast` (the
        # exchangelib default) would surface throttling as an exception the
        # feed would read as an outage.
        retry_policy=FaultTolerance(max_wait=600),
    )
    return Account(
        primary_smtp_address=_email_address(),
        config=config,
        autodiscover=False,
        access_type="delegate",
    )


def _settings_fingerprint() -> tuple:
    return (
        settings.exchange_url,
        settings.exchange_username,
        settings.exchange_password,
        settings.exchange_email,
        settings.ca_bundle,
        settings.client_cert,
        settings.client_key,
        _tls_insecure(),
    )


def account():
    """The process's `Account`, built on first use and rebuilt when any setting
    it was built from changes. Synchronous and thread-safe; the async helpers
    call it inside their worker thread."""
    global _account, _fingerprint

    if not configured():
        raise ExchangeError(
            "Exchange is not configured — set CC_EXCHANGE_URL, "
            "CC_EXCHANGE_USERNAME and CC_EXCHANGE_PASSWORD"
        )
    fingerprint = _settings_fingerprint()
    with _lock:
        if _account is None or _fingerprint != fingerprint:
            _account = _build_account()
            _fingerprint = fingerprint
        return _account


def reset_account() -> None:
    """Drop the cached `Account` — used by the smoke script and the tests, and
    the seam a settings reload would use."""
    global _account, _fingerprint

    with _lock:
        _account = None
        _fingerprint = None


async def _thread(fn, *args, **kwargs):
    """Run one exchangelib call off the event loop and normalise its failures.

    ExchangeError passes through; everything else becomes one, because a
    caller's `except EmailFacadeError` must not be bypassed by an
    `ErrorItemNotFound` from three layers down.
    """
    def run():
        try:
            return fn(*args, **kwargs)
        except ExchangeError:
            raise
        except Exception as e:  # noqa: BLE001 — one error type crosses this seam
            raise ExchangeError(f"exchange: {type(e).__name__}: {e}") from e

    return await asyncio.to_thread(run)


# --- the query translator (D5) -------------------------------------------------
# `mail_search`, `propose_bulk_dismiss`, the backlog sweep and the feed all pass
# ONE query string to `list_refs`. On Gmail it is Gmail syntax; here the tokens
# agents are taught are translated and any remaining free text goes to EWS as an
# AQS `QueryString`. The default feed query `in:inbox newer_than:1d` therefore
# works unchanged, and the backlog sweeper's `after:<epoch> before:<epoch>`
# does too.

_TOKEN = re.compile(r"^(from|to|subject|after|before|newer_than|older_than|in):(.*)$",
                    re.IGNORECASE)


def _as_datetime(value: str, *, end_of_day: bool = False) -> datetime:
    """`YYYY/MM/DD`, `YYYY-MM-DD` or epoch seconds → an aware UTC datetime.

    Both spellings because both are live: agents are taught `after:2021/11/01`
    and the backlog sweeper emits epoch seconds (`ingest/feed.py`).
    """
    raw = value.strip()
    if raw.isdigit() and len(raw) >= 9:
        return datetime.fromtimestamp(int(raw), tz=timezone.utc)
    parts = re.split(r"[/-]", raw)
    if len(parts) != 3:
        raise ExchangeError(f"date {value!r} is neither YYYY/MM/DD nor epoch seconds")
    try:
        day = datetime(int(parts[0]), int(parts[1]), int(parts[2]), tzinfo=timezone.utc)
    except ValueError as e:
        raise ExchangeError(f"date {value!r} is not a real calendar date ({e})") from e
    return day + timedelta(days=1) if end_of_day else day


def translate_query(query: str) -> tuple[dict, str | None, str | None]:
    """`(filters, aqs, folder)` for one Gmail-shaped query.

    * `filters` — kwargs for exchangelib's Django-like `QuerySet.filter`.
    * `aqs` — the leftover free text, handed to EWS as an AQS `QueryString`,
      or None when there is none.
    * `folder` — what `in:` named, or None for the inbox.

    A pure function, so the whole dialect is testable without a mailbox.

    The tokens: `from:`, `to:`, `subject:`, `after:`/`before:` (YYYY/MM/DD or
    epoch seconds), `newer_than:Nd` / `older_than:Nd`, `in:<folder>`. Anything
    else — including Gmail's `-` negation, which EWS's restriction language
    cannot express through this seam — is free text. That is deliberate: a
    token silently DROPPED would widen the set a bulk dismissal pins, while a
    token that becomes a search term narrows it, and narrowing is the safe
    direction.

    Note the EWS rule this shape exists to respect: a `QueryString` may not be
    combined with field restrictions in one request (exchangelib
    `restriction.py` ~line 372, "Query strings cannot be combined with other
    settings"). `list_refs` therefore sends the AQS to the server and applies
    `filters` to the returned items itself — see `_matches`.
    """
    filters: dict = {}
    free: list[str] = []
    folder: str | None = None
    now = datetime.now(timezone.utc)

    try:
        words = shlex.split(query or "", posix=True)
    except ValueError:
        # An unbalanced quote is a model typo, not a reason to fail the search.
        words = (query or "").split()

    for word in words:
        match = _TOKEN.match(word)
        if not match:
            if word.strip():
                free.append(word)
            continue
        token, value = match.group(1).lower(), match.group(2).strip()
        if not value:
            continue
        if token == "from":
            filters["sender"] = value
        elif token == "to":
            filters["to_recipients"] = value
        elif token == "subject":
            filters["subject__icontains"] = value
        elif token == "after":
            filters["datetime_received__gt"] = _as_datetime(value)
        elif token == "before":
            filters["datetime_received__lt"] = _as_datetime(value)
        elif token in ("newer_than", "older_than"):
            days = re.match(r"^(\d+)\s*d$", value, re.IGNORECASE)
            if not days:
                free.append(word)
                continue
            cut = now - timedelta(days=int(days.group(1)))
            key = "datetime_received__gt" if token == "newer_than" else "datetime_received__lt"
            filters[key] = cut
        elif token == "in":
            folder = value

    return filters, (" ".join(free) or None), folder


def _matches(item, filters: dict) -> bool:
    """Apply `filters` to an item already returned by an AQS search — the
    client-side half of the rule above (a QueryString and field restrictions
    cannot ride in one EWS request)."""
    for key, want in filters.items():
        field, _, lookup = key.partition("__")
        got = getattr(item, field, None)
        if field == "sender":
            got = getattr(got, "email_address", None) or ""
            if want.lower() not in got.lower():
                return False
        elif field == "to_recipients":
            addresses = " ".join((getattr(m, "email_address", "") or "") for m in (got or []))
            if want.lower() not in addresses.lower():
                return False
        elif lookup == "icontains":
            if want.lower() not in (got or "").lower():
                return False
        elif lookup == "gt":
            if got is None or got <= want:
                return False
        elif lookup == "lt":
            if got is None or got >= want:
                return False
    return True


# --- folders -------------------------------------------------------------------


def _resolve_folder(acct, name: str):
    """A folder by distinguished name, display name or path — case-insensitive.

    Folders are the "never guess X" case (AGENTS.md): a folder name is an
    argument an agent writes, so this resolves generously and fails with the
    list of what exists rather than with a bare not-found.
    """
    wanted = (name or "").strip()
    if not wanted:
        return acct.inbox
    key = wanted.lower().replace(" ", "").replace("_", "")
    attr = DISTINGUISHED_FOLDERS.get(key)
    if attr:
        return getattr(acct, attr)

    available = []
    by_path: dict[str, object] = {}
    by_name: dict[str, object] = {}
    for folder in acct.msg_folder_root.walk():
        path = (getattr(folder, "absolute", "") or "").strip()
        display = (getattr(folder, "name", "") or "").strip()
        available.append(path or display)
        if path:
            by_path.setdefault(path.lower(), folder)
        if display:
            by_name.setdefault(display.lower(), folder)

    normalized = wanted.lower().lstrip("/")
    for candidates in (by_path, by_name):
        for candidate_key, folder in candidates.items():
            if candidate_key.lstrip("/").lstrip("root").lstrip("/") == normalized \
               or candidate_key == wanted.lower():
                return folder
    raise ExchangeError(
        f"no folder named {wanted!r} in this mailbox — "
        f"mail_list_folders shows what exists ({', '.join(sorted(available)[:25]) or 'none'})"
    )


async def list_folders() -> list[dict]:
    """The mailbox's folder tree with item counts — `[{name, path, total,
    unread}]`. The read behind `mail_list_folders`: a folder name is an
    argument, so something has to be able to answer "which folders exist"."""
    def run():
        acct = account()
        out = []
        for folder in acct.msg_folder_root.walk():
            out.append({
                "name": getattr(folder, "name", "") or "",
                "path": getattr(folder, "absolute", "") or "",
                "total": getattr(folder, "total_count", None) or 0,
                "unread": getattr(folder, "unread_count", None) or 0,
            })
        return out

    return await _thread(run)


# --- reads ---------------------------------------------------------------------


def _iso(value) -> str | None:
    """An EWSDateTime → RFC 3339 in UTC. The ledger parses it with
    `datetime.fromisoformat`, and instants are what cross this seam — never a
    local rendering."""
    if value is None:
        return None
    try:
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except (AttributeError, ValueError, TypeError):
        return str(value)


def _headers(item) -> dict[str, list[str]]:
    """`Message.headers` (EWS `item:InternetMessageHeaders`, a list of
    `MessageHeader(name, value)` — exchangelib `properties.py` ~line 455)
    folded to a lower-cased name → values map. Absent headers are absent, never
    invented: `contract.mail` reads a missing `authentication_results` as "not
    eligible", never as "eligible"."""
    out: dict[str, list[str]] = {}
    for header in getattr(item, "headers", None) or []:
        name = (getattr(header, "name", "") or "").strip().lower()
        if not name:
            continue
        out.setdefault(name, []).append((getattr(header, "value", "") or "").strip())
    return out


def _record(item) -> dict:
    """One EWS `Message` → the SAME normalised record the Gmail façade returns.

    THE ID RULE (D4): `uuid` is the EWS `ItemId` — the fetch/move handle, which
    CHANGES when a message moves folder — and `message_id` is the RFC 822
    `InternetMessageId`, which does not. exchangelib spells the latter
    `Message.message_id` (`message:InternetMessageId`, items/message.py line
    40); the ledger keys enrollment on it, so the two must never be swapped.
    """
    headers = _headers(item)
    sender = getattr(item, "sender", None) or getattr(item, "author", None)
    address = (getattr(sender, "email_address", "") or "") if sender else ""
    name = (getattr(sender, "name", "") or "") if sender else ""
    conversation = getattr(item, "conversation_id", None)
    body = getattr(item, "body", None)
    text = getattr(item, "text_body", None) or ""
    html = str(body) if body is not None else ""
    return {
        "uuid": getattr(getattr(item, "id", None), "id", None) or getattr(item, "id", "") or "",
        "message_id": (getattr(item, "message_id", "") or "").strip(),
        "conversation_id": (getattr(conversation, "id", "") or "") if conversation else "",
        "from": f"{name} <{address}>".strip() if name else address,
        "subject": getattr(item, "subject", "") or "",
        "date": _iso(getattr(item, "datetime_received", None)
                     or getattr(item, "datetime_sent", None)),
        "body_text": text,
        "body_html": html,
        "snippet": (text or "")[:200],
        "list_unsubscribe": (headers.get("list-unsubscribe") or [""])[0],
        "list_unsubscribe_post": (headers.get("list-unsubscribe-post") or [""])[0],
        "authentication_results": headers.get("authentication-results", []),
        "dkim_signatures": headers.get("dkim-signature", []),
    }


def _ref(item) -> dict:
    conversation = getattr(item, "conversation_id", None)
    return {
        "uuid": getattr(getattr(item, "id", None), "id", None) or getattr(item, "id", "") or "",
        "conversation_id": (getattr(conversation, "id", "") or "") if conversation else "",
        "message_id": (getattr(item, "message_id", "") or "").strip(),
    }


async def list_refs(scope_query: str) -> list[dict]:
    """References only — `[{uuid, conversation_id, message_id}]` — for one
    Gmail-shaped query, translated by `translate_query`.

    `message_id` rides along because the ledger keys on it (D4): the Gmail
    façade's refs carry no such field, so enrollment synthesises one from the
    uuid there and keeps the real one here.
    """
    def run():
        acct = account()
        filters, aqs, folder_name = translate_query(scope_query)
        folder = _resolve_folder(acct, folder_name) if folder_name else acct.inbox
        fields = ("id", "changekey", "message_id", "conversation_id", "datetime_received",
                  "sender", "subject", "to_recipients")
        if aqs:
            # EWS refuses a QueryString beside field restrictions, so the AQS
            # goes to the server and the tokens are applied here.
            items = folder.filter(aqs).only(*fields).order_by("-datetime_received")
            rows = [i for i in items[: MAX_REFS + 1] if _matches(i, filters)]
        else:
            query = folder.filter(**filters) if filters else folder.all()
            rows = list(query.only(*fields).order_by("-datetime_received")[: MAX_REFS + 1])
        if len(rows) > MAX_REFS:
            raise ExchangeError(
                f"{scope_query!r} matches more than {MAX_REFS} messages — narrow "
                "it with an explicit after:/before: window"
            )
        return [_ref(i) for i in rows]

    return await _thread(run)


def _fetch(acct, uuid: str):
    from exchangelib.properties import ItemId

    items = list(acct.fetch(ids=[ItemId(id=uuid)]))
    if not items:
        raise ExchangeError(f"message {uuid}: not found in this mailbox")
    item = items[0]
    if isinstance(item, Exception):
        raise ExchangeError(f"message {uuid}: {item}")
    return item


async def get_message(uuid: str) -> dict:
    """One normalised message, in the shape the Gmail façade returns: from,
    subject, date, body_text, body_html, snippet, conversation_id, uuid,
    message_id, plus the unsubscribe headers and DKIM facts `contract.mail`
    reads. `uuid` is the EWS ItemId.

    The bodies are returned RAW and converted exactly once, by
    `ingest/mailtext.py` through `ledger.provider_body` — nothing here reads
    them for a model (`tests/test_mailtext.py` walks the source for any other
    reader).
    """
    def run():
        return _record(_fetch(account(), uuid))

    return await _thread(run)


# --- writes: EXECUTOR-ONLY (see the module docstring) --------------------------


async def report_spam(uuid: str) -> dict:
    """Move one message to the Junk folder — the Exchange spelling of Gmail's
    +SPAM/−INBOX. Returns `{ok, folder}`; the Executor's result line reads it
    the way it reads the façade's `label_ids`."""
    def run():
        acct = account()
        item = _fetch(acct, uuid)
        item.move(acct.junk)
        return {"ok": True, "folder": getattr(acct.junk, "name", "Junk Email")}

    return await _thread(run)


async def move(uuid: str, folder: str) -> dict:
    """Move one message to a folder named by display name, path or
    distinguished name (inbox / junkemail / deleteditems / drafts / sentitems /
    archive), case-insensitively. Executor-only.

    Moving CHANGES the ItemId — EWS mints a new one in the destination folder —
    which is exactly why the ledger keys on the RFC 822 Message-ID and not on
    this handle (D4). The new uuid is returned so the Executor's result line
    can state it.
    """
    def run():
        acct = account()
        item = _fetch(acct, uuid)
        target = _resolve_folder(acct, folder)
        item.move(target)
        return {
            "ok": True,
            "folder": getattr(target, "name", folder),
            "uuid": getattr(getattr(item, "id", None), "id", None) or uuid,
        }

    return await _thread(run)


async def send(to: list[str], subject: str, body: str, cc: list[str] | None = None,
               reply_to_ref: str | None = None) -> dict:
    """Send one plain-text message as the configured mailbox. Executor-only,
    after approval — sending is external and irreversible.

    `reply_to_ref` is the ItemId of the message being replied to, and the
    threading headers are taken from THAT MESSAGE'S OWN headers, never from the
    proposal: `In-Reply-To` is its `InternetMessageId` and `References` is its
    own References chain plus that id. Same rule as the unsubscribe URL — an
    id an agent can write into its own arguments is an agent-authored claim.
    """
    def run():
        from exchangelib import Message

        acct = account()
        message = Message(
            account=acct,
            folder=acct.sent,
            subject=subject,
            body=body,
            to_recipients=list(to or []),
            cc_recipients=list(cc or []),
        )
        threaded_to = ""
        if reply_to_ref:
            parent = _fetch(acct, reply_to_ref)
            parent_id = (getattr(parent, "message_id", "") or "").strip()
            if parent_id:
                message.in_reply_to = parent_id
                chain = (getattr(parent, "references", "") or "").strip()
                message.references = f"{chain} {parent_id}".strip()
                threaded_to = parent_id
        # `send_meeting_invitations` stays at its SEND_TO_NONE default — this
        # is a plain message, not a meeting request (items/base.py line 49
        # lists the three values that field takes).
        message.send(save_copy=True, copy_to_folder=acct.sent)
        return {
            "ok": True,
            "to": list(to or []),
            "cc": list(cc or []),
            "subject": subject,
            "in_reply_to": threaded_to,
        }

    return await _thread(run)


# --- calendar ------------------------------------------------------------------
# The SAME normalised event shape `integrations/calendar_facade.py` documents,
# produced here in Python instead of in the n8n lib: start, end, all_day,
# summary, location, attendee_count, organizer_is_self, has_agenda, declined,
# event_id. A caller (the EA's brief, the `read_calendar` tool) must not be able
# to tell which provider answered.

MAX_EVENTS = 250


def _event(item, self_address: str) -> dict:
    organizer = getattr(item, "organizer", None)
    organizer_address = (getattr(organizer, "email_address", "") or "") if organizer else ""
    attendees = list(getattr(item, "required_attendees", None) or []) + \
        list(getattr(item, "optional_attendees", None) or [])
    body = getattr(item, "body", None)
    return {
        "event_id": getattr(getattr(item, "id", None), "id", None) or getattr(item, "id", "") or "",
        "summary": getattr(item, "subject", "") or "",
        "start": _iso(getattr(item, "start", None)),
        "end": _iso(getattr(item, "end", None)),
        "all_day": bool(getattr(item, "is_all_day", False)),
        "location": getattr(item, "location", "") or "",
        "attendee_count": len(attendees),
        "organizer_is_self": bool(
            organizer_address and organizer_address.lower() == (self_address or "").lower()
        ),
        "has_agenda": bool((str(body) if body is not None else "").strip()),
        "declined": (getattr(item, "my_response_type", "") or "") == "Decline",
    }


def _ews_datetime(value: str, field: str):
    from exchangelib import EWSDateTime

    try:
        parsed = datetime.fromisoformat((value or "").replace("Z", "+00:00"))
    except ValueError as e:
        raise ExchangeError(
            f"{field} {value!r} must be RFC3339 with an explicit offset or Z"
        ) from e
    if parsed.tzinfo is None:
        raise ExchangeError(
            f"{field} {value!r} has no offset — instants cross this seam, never "
            "local wall-clock times"
        )
    return EWSDateTime.from_datetime(parsed.astimezone(timezone.utc))


async def list_events(time_min: str, time_max: str, calendar_id: str = "primary") -> dict:
    """Events in an RFC3339 window: `{"events": [...], "truncated": bool}`.

    The envelope, not a bare list, for the reason `calendar_facade` gives:
    `truncated` is a fact about the READ that a brief must be able to state
    honestly. `CalendarView` (`FolderCollection.view`, collections.py ~line 94)
    is what expands recurrences — `filter` would return only the master item of
    a recurring meeting, which is how a daily standup vanishes from a brief.
    """
    def run():
        acct = account()
        calendar = acct.calendar if calendar_id in ("", "primary") else \
            _resolve_folder(acct, calendar_id)
        start = _ews_datetime(time_min, "time_min")
        end = _ews_datetime(time_max, "time_max")
        items = list(calendar.view(start=start, end=end)[: MAX_EVENTS + 1])
        truncated = len(items) > MAX_EVENTS
        self_address = _email_address()
        return {
            "events": [_event(i, self_address) for i in items[:MAX_EVENTS]],
            "truncated": truncated,
        }

    return await _thread(run)


async def create_event(title: str, start: str, end: str, description: str | None = None,
                       attendees: list[str] | None = None,
                       calendar_id: str = "primary") -> dict:
    """Create an event. Executor-only. `start`/`end` are RFC3339 with an
    explicit offset or Z — instants, compared as instants."""
    def run():
        from exchangelib import CalendarItem
        from exchangelib.items import SEND_ONLY_TO_ALL

        acct = account()
        calendar = acct.calendar if calendar_id in ("", "primary") else \
            _resolve_folder(acct, calendar_id)
        item = CalendarItem(
            account=acct,
            folder=calendar,
            subject=title,
            body=description or "",
            start=_ews_datetime(start, "start"),
            end=_ews_datetime(end, "end"),
            required_attendees=list(attendees or []),
        )
        item.save(send_meeting_invitations=SEND_ONLY_TO_ALL)
        return _event(item, _email_address())

    return await _thread(run)


async def update_event(event_id: str, title: str | None = None, start: str | None = None,
                       end: str | None = None, description: str | None = None,
                       attendees: list[str] | None = None,
                       calendar_id: str = "primary") -> dict:
    """Patch an existing event. Executor-only. Only the fields supplied change —
    moving a meeting is an update, never a delete-and-recreate, because
    recreating loses the attendees' responses."""
    def run():
        from exchangelib.items import SEND_TO_ALL_AND_SAVE_COPY

        acct = account()
        item = _fetch(acct, event_id)
        changed: list[str] = []
        if title is not None:
            item.subject = title
            changed.append("subject")
        if description is not None:
            item.body = description
            changed.append("body")
        if start is not None:
            item.start = _ews_datetime(start, "start")
            changed.append("start")
        if end is not None:
            item.end = _ews_datetime(end, "end")
            changed.append("end")
        if attendees is not None:
            item.required_attendees = list(attendees)
            changed.append("required_attendees")
        if not changed:
            raise ExchangeError("update_event: no field to change was supplied")
        item.save(update_fields=changed,
                  send_meeting_invitations=SEND_TO_ALL_AND_SAVE_COPY)
        return _event(item, _email_address())

    return await _thread(run)


async def delete_event(event_id: str, calendar_id: str = "primary") -> str:
    """Cancel an event, returning the id that was cancelled. Executor-only.
    Exchange notifies the attendees; nothing restores their acceptances, which
    is why the registry calls this irreversible and the proposal must carry a
    reason."""
    def run():
        from exchangelib.items import SEND_TO_ALL_AND_SAVE_COPY

        acct = account()
        item = _fetch(acct, event_id)
        # `CalendarItem.cancel()` (calendar_item.py line 212) sends a
        # CancelCalendarItem, which is what NOTIFIES the attendees; it is the
        # organizer's verb. A meeting someone else owns cannot be cancelled,
        # only removed from this calendar — `delete` with cancellations on is
        # the honest fallback, and it is the same irreversibility either way.
        cancel = getattr(item, "cancel", None)
        if callable(cancel):
            try:
                cancel()
                return event_id
            except Exception:  # noqa: BLE001 — not the organizer; fall through
                pass
        item.delete(send_meeting_cancellations=SEND_TO_ALL_AND_SAVE_COPY)
        return event_id

    return await _thread(run)
