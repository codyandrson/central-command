"""The native Google provider: Gmail and Google Calendar through Google's
client libraries (DL-234).

The n8n façades (`deploy/n8n/workflows/`) were the only reason n8n ran: two
webhook workflows over two provider workflows, raw HTTP calls to about ten
Gmail and Calendar endpoints under the OAuth credential n8n held. This module
is those provider workflows in Python, called from the SAME façade functions
(`email_facade`, `calendar_facade`) when `CC_GOOGLE_PROVIDER=native`. With the
setting at its default (`n8n`) nothing here runs and nothing here is imported.

**Parity is the contract.** The shipped workflow JSON is the specification:
`mail_request(payload)` takes exactly the payload `email_facade._call` posts
to `cc-email-facade` and returns exactly the envelope that workflow's last
node answers (`lib-email-provider`: Validate request → the mode's Gmail call →
Normalize response / Build … envelope → Return envelope), and
`calendar_request(payload)` does the same for `cc-calendar-facade` →
`lib-google-calendar`. So the façades' own post-processing (the `messages[0]`
pick, the strict base64 decode, the `ok` check, `already_existed`) is shared
code on both paths, not a copy. The node each function reproduces is named in
its docstring; quirks are kept, and the ones that look like bugs are listed in
DL-234 rather than fixed here.

**Refusal or failure, as n8n answered it.** An error is a `GoogleError` whose
`status_code` is the status the n8n path's caller saw for the same situation,
never Google's own (that is `google_status`):

- mail: the `Classify provider error` node's rule — 422 when the sentence is a
  refusal the provider states in words (the page cap, an unparseable query, a
  malformed reference, an attachment id Gmail no longer knows), 503 for
  everything else (auth, rate limit, transport, an unknown message, a page
  that cannot be read). `_mail_status` is that node's regex list.
- calendar: every `fail()` reached Python as n8n's HTTP 500 "Error in
  workflow" (the calendar webhook has no classifier), so every failure here is
  500, validation included; a NO the provider answered is the envelope
  `{ok: false, message}` and the façade raises it as a refusal.

Both 503 and 500 are transient (`contract.failures.TRANSIENT_STATUS`), so a
Google outage parks exactly as an n8n-relayed one did. The façades stamp the
`google` dependency on the error (`contract.dependencies.GOOGLE`), so the
park waits on the Google probe, not on the n8n host.

**The credential** is the operator's own OAuth client (id and secret in
`.env`) and the refresh token the consent command wrote there
(`central_command/google_consent.py`). Every request is Google's own
libraries: google-api-python-client builds, sends and pages it from the
discovery documents it bundles, over an `httplib2.Http` made per call (with an
explicit timeout and the trust knobs of `integrations/http.client_kwargs()`),
and google-auth (`Credentials.refresh` through `google_auth_httplib2`) holds
the token. A 401 refreshes once and re-sends, as n8n's OAuth2 credential
does; a refresh that fails is the provider's auth failing, which
the n8n path answered 503 (mail) and 500 (calendar) — so a revoked or expired
refresh token parks the work, and the retry sweep reports it once per episode
(`retry.failing_despite_probe`) because Google itself still answers. No token,
secret or authorization code is ever put in a message or a log line.

**Tier.** Imported lazily by the two façades only. `runtime/` reaches mail and
calendar reads through the façades, as it reaches Exchange, and may never
import this module (`tests/test_governance.py` bans it by name): the writes
here (`report_spam`, the three calendar writes) are reachable only from the
Executor through the façade functions that guard them today.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import threading
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from central_command.config import settings
from central_command.contract.failures import ProviderStatusError
from central_command.integrations import http as http_client

log = logging.getLogger(__name__)

# google-auth's token endpoint for an installed-app client (the probe in
# `heartbeat/probes.GOOGLE_HOSTS` names its host and the two API hosts the
# bundled discovery documents give as `rootUrl`; a test pins them).
TOKEN_URI = "https://oauth2.googleapis.com/token"

# The minimum the operations need (DL-234): Gmail search with `q`, full
# message and attachment reads and the one label write (`messages.modify`)
# need `gmail.modify` — `gmail.readonly` cannot modify and `gmail.metadata`
# cannot search with `q` or read a body; Calendar list/get/insert/patch/delete
# on any calendar the account can reach need `calendar.events`.
SCOPES = (
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar.events",
)

# The clocks (httplib2's socket timeout). Mail keeps the façade client's own
# (60 s, 180 s for one attachment: `email_facade._call`); the calendar keeps
# the workflow's HTTP node timeout (15000 ms on every lib-google-calendar
# request node). A clock that fires raises, and the error parks like any outage.
CALENDAR_TIMEOUT_SECONDS = 15.0
TOKEN_TIMEOUT_SECONDS = 30.0

# `List messages (Gmail API)`: maxResults 500, at most 5 requests.
LIST_PAGE_SIZE = 500
LIST_MAX_PAGES = 5


class GoogleError(ProviderStatusError):
    """A failure of the native path. `status_code` is what the n8n façade
    answered for the same situation (422/503 mail, 500 calendar), `message`
    the provider workflow's own sentence."""

    def __init__(self, message: str, *, status_code: int):
        super().__init__(message, status_code=status_code)
        self.message = message


class NotConfigured(Exception):
    """Selected but missing a setting: the façades raise their own "not
    configured" error from it (semantic, FAILED, never parked)."""


def selected() -> bool:
    """THE switch: the façades route here only when this is true (and
    Exchange is not configured, which still wins)."""
    return settings.google_provider == "native"


def missing_settings() -> list[str]:
    """The `.env` keys a selected native client still needs, by name."""
    return [key for key, value in (
        ("CC_GOOGLE_CLIENT_ID", settings.google_client_id),
        ("CC_GOOGLE_CLIENT_SECRET", settings.google_client_secret),
        ("CC_GOOGLE_REFRESH_TOKEN", settings.google_refresh_token),
    ) if not value]


def configured() -> bool:
    """Selected AND every credential setting present."""
    return selected() and not missing_settings()


# --- the library: one Http and one service per call ---------------------------
#
# google-api-python-client is synchronous (httplib2), so each façade call runs
# its whole operation in ONE `asyncio.to_thread`, as `integrations/exchange.py`
# runs exchangelib. "httplib2.Http() objects are not thread-safe … each thread
# … must have its own instance" (docs/thread_safety.md of the library), so
# every call builds its own `Http` and its own service around it. The service
# is built from the discovery document the library BUNDLES
# (`static_discovery=True`, `cache_discovery=False`): no run-time fetch of a
# discovery document, ever. `execute(num_retries=0)`: the n8n nodes never
# retried, and the outage machinery is the retry.


def _http(timeout: float):
    """A new `httplib2.Http` with an explicit socket timeout and the global
    trust knobs (`integrations/http.client_kwargs()`: CC_CA_BUNDLE,
    CC_CLIENT_CERT/_KEY, CC_TLS_INSECURE). The one seam tests replace."""
    import httplib2

    kw = http_client.client_kwargs()
    verify = kw.get("verify", True)
    http = httplib2.Http(
        timeout=timeout,
        ca_certs=verify if isinstance(verify, str) else None,
        disable_ssl_certificate_validation=verify is False,
    )
    cert = kw.get("cert")
    if cert:
        certfile, keyfile = cert if isinstance(cert, tuple) else (cert, None)
        http.add_certificate(keyfile, certfile, "")
    return http


_creds_lock = threading.Lock()
_creds_cache: dict = {"key": None, "creds": None}


def _credentials():
    """One `Credentials` per (client, refresh token), so the access token is
    reused until it nears expiry and a changed setting starts afresh."""
    from google.oauth2.credentials import Credentials

    key = (settings.google_client_id, settings.google_client_secret,
           settings.google_refresh_token)
    with _creds_lock:
        if _creds_cache["key"] != key:
            _creds_cache["key"] = key
            # No `scopes=`: a refresh then returns the scopes the consent
            # granted, and never asks for a different set.
            _creds_cache["creds"] = Credentials(
                token=None,
                refresh_token=settings.google_refresh_token,
                token_uri=TOKEN_URI,
                client_id=settings.google_client_id,
                client_secret=settings.google_client_secret,
            )
        return _creds_cache["creds"]


class _TokenUnavailable(Exception):
    """The refresh failed: what Google said (an error code, never a secret)."""


def _token_failure(e: BaseException) -> _TokenUnavailable:
    from google.auth import exceptions as gexc

    if isinstance(e, gexc.RefreshError):
        said = str(e.args[0]) if e.args else type(e).__name__
        return _TokenUnavailable(f"token refresh refused: {said[:160]}")
    return _TokenUnavailable(f"token endpoint unreachable: {type(e).__name__}")


def _service(api: str, version: str, timeout: float):
    """A service for this call. The token is refreshed here, under a lock, when
    it is missing or near expiry, so concurrent calls do not all refresh;
    `AuthorizedHttp` re-sends once after a 401 (`max_refresh_attempts=1`), as
    n8n's OAuth2 credential did."""
    import google_auth_httplib2
    from google.auth import exceptions as gexc
    from googleapiclient.discovery import build

    creds = _credentials()
    with _creds_lock:
        if not creds.valid:
            try:
                creds.refresh(google_auth_httplib2.Request(_http(TOKEN_TIMEOUT_SECONDS)))
            except (gexc.RefreshError, gexc.TransportError) as e:
                raise _token_failure(e) from None
    authed = google_auth_httplib2.AuthorizedHttp(creds, http=_http(timeout), max_refresh_attempts=1)
    return build(api, version, http=authed, static_discovery=True, cache_discovery=False)


class _Transport(Exception):
    """No HTTP status: the request never got an answer."""


def _exec(request) -> tuple[int, object]:
    """`(status, body)` for one library request. A non-2xx (`HttpError`) is
    data here, not an exception, because the n8n request nodes ran with
    `neverError` and their Code nodes classified the status; the body is the
    parsed error JSON, or its text. A 2xx answers 200 with the parsed body
    (`""` for 204). Raises `_Transport` or `_TokenUnavailable`."""
    import httplib2
    from google.auth import exceptions as gexc
    from googleapiclient.errors import HttpError

    try:
        return 200, request.execute(num_retries=0)
    except HttpError as e:
        try:
            body = json.loads(e.content) if e.content else {}
        except ValueError:
            body = e.content.decode("utf-8", errors="replace")
        return e.status_code, body
    except (gexc.RefreshError, gexc.TransportError) as e:
        raise _token_failure(e) from None
    except (httplib2.HttpLib2Error, OSError) as e:  # DNS, refused, reset, TLS, timeout
        raise _Transport(f"{type(e).__name__}: {e}"[:200]) from e


def _get(obj, key, default=None):
    return obj.get(key, default) if isinstance(obj, dict) else default


def _js_str(value) -> str:
    """JavaScript's `String(v)` for the values a message quotes."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return ",".join("" if v is None else _js_str(v) for v in value)
    if isinstance(value, dict):
        return "[object Object]"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


# --- mail: lib-email-provider -------------------------------------------------

_MAIL_PREFIX = "lib-email-provider: "
_MAIL_MODES = ("message", "thread", "list", "report_spam", "attachment")
# Ids are URL path segments: the provider id alphabet (JS `^[A-Za-z0-9_-]+$`;
# fullmatch, because Python's `$` also matches before a trailing newline).
_ID_RE = re.compile(r"[A-Za-z0-9_-]+")

# `Classify provider error` (cc-email-facade), the same six patterns: a match
# is a refusal (422), anything else an outage (503).
_REFUSALS = (
    re.compile(r"pagination cap hit", re.IGNORECASE),
    re.compile(r"rejected the list query \(400\)", re.IGNORECASE),
    re.compile(r"bad or missing mode", re.IGNORECASE),
    re.compile(r"requires (scope_query|source_ref)", re.IGNORECASE),
    re.compile(r"must be (boolean|a positive number)", re.IGNORECASE),
    re.compile(r"attachment not found for the given reference", re.IGNORECASE),
)


class _MailFail(Exception):
    """A thrown error inside lib-email-provider; the text is the node's own."""


def _mail_status(message: str) -> int:
    return 422 if any(p.search(message) for p in _REFUSALS) else 503


def _id_ok(value) -> bool:
    return bool(_ID_RE.fullmatch("" if value is None else _js_str(value)))


def _validate_mail(payload: dict) -> dict:
    """`Validate request` (lib-email-provider), for the fields the façade sends."""
    mode = payload.get("mode")
    if mode not in _MAIL_MODES:
        raise _MailFail(_MAIL_PREFIX + f"bad or missing mode '{_js_str(mode) if mode is not None else 'undefined'}' "
                        "(must be 'message', 'thread', 'list', 'report_spam', or 'attachment')")
    ref = payload.get("source_ref") or {}
    if mode in ("message", "report_spam", "attachment") and not _id_ok(ref.get("uuid")):
        raise _MailFail(_MAIL_PREFIX + f'mode "{mode}" requires source_ref.uuid (non-empty, [A-Za-z0-9_-] only)')
    if mode == "attachment" and not _id_ok(ref.get("attachment_id")):
        raise _MailFail(_MAIL_PREFIX + 'mode "attachment" requires source_ref.attachment_id (non-empty, [A-Za-z0-9_-] only)')
    query = payload.get("scope_query")
    if mode == "list" and (not isinstance(query, str) or query.strip() == ""):
        raise _MailFail(_MAIL_PREFIX + 'mode "list" requires scope_query (non-empty Gmail search string)')
    if mode == "thread" and not _id_ok(ref.get("conversation_id")):
        raise _MailFail(_MAIL_PREFIX + 'mode "thread" requires source_ref.conversation_id (non-empty, [A-Za-z0-9_-] only)')
    return ref


_MAIL_REAUTH = "check the shared email credential"


def _auth_or_rate(status: int, reauth: str) -> str | None:
    """The 401/403 and 429 sentences every mail and calendar node shares (the
    caller adds its prefix), or None for any other status."""
    if status in (401, 403):
        return f"provider auth failed ({status}) - {reauth}"
    if status == 429:
        return "provider rate limit hit - retry later"
    return None


def _http_body(status: int, body, what: str):
    """`httpBody()` of `Normalize response`."""
    if 200 <= status < 300:
        return body or {}
    if status == 404:
        raise _MailFail(_MAIL_PREFIX + what + " not found for the given reference")
    if sentence := _auth_or_rate(status, _MAIL_REAUTH):
        raise _MailFail(_MAIL_PREFIX + sentence)
    raise _MailFail(_MAIL_PREFIX + f"provider fetch failed ({status})")


# The charset labels a mail part may name, as WHATWG's Encoding Standard lists
# them (https://encoding.spec.whatwg.org/#names-and-labels), each with the
# Python codec that decodes that encoding. n8n's Code node decoded with
# `TextDecoder`, which knows exactly these labels; the sender controls the
# label, and Python's codec registry is far wider (utf-7, unicode-escape,
# rot13, punycode …), so only a label in this table picks a codec: any other
# falls back to UTF-8 with replacement, as `decode()` falls through when
# `new TextDecoder(label)` throws. Omitted on purpose, because TextDecoder
# refuses them too: the `replacement` labels (iso-2022-kr, iso-2022-cn,
# hz-gb-2312 …). Omitted for want of a Python codec: x-user-defined (it falls
# back to UTF-8; TextDecoder would map 0x80-0xFF into the private use area).
# Written out by hand: `webencodings`, which carries this table, is not
# installed here. Note the windows-1252 row: WHATWG reads iso-8859-1/latin1 as
# windows-1252, which Python's codec of that name is not.
_WHATWG_ENCODINGS: tuple[tuple[str, str], ...] = (
    ("utf-8", "unicode-1-1-utf-8 unicode11utf8 unicode20utf8 utf-8 utf8 x-unicode20utf8"),
    ("cp866", "866 cp866 csibm866 ibm866"),
    ("iso8859_2", ("csisolatin2 iso-8859-2 iso-ir-101 iso8859-2 iso88592 iso_8859-2 "
                  "iso_8859-2:1987 l2 latin2")),
    ("iso8859_3", ("csisolatin3 iso-8859-3 iso-ir-109 iso8859-3 iso88593 iso_8859-3 "
                  "iso_8859-3:1988 l3 latin3")),
    ("iso8859_4", ("csisolatin4 iso-8859-4 iso-ir-110 iso8859-4 iso88594 iso_8859-4 "
                  "iso_8859-4:1988 l4 latin4")),
    ("iso8859_5", ("csisolatincyrillic cyrillic iso-8859-5 iso-ir-144 iso8859-5 iso88595 "
                  "iso_8859-5 iso_8859-5:1988")),
    ("iso8859_6", ("arabic asmo-708 csiso88596e csiso88596i csisolatinarabic ecma-114 "
                  "iso-8859-6 iso-8859-6-e iso-8859-6-i iso-ir-127 iso8859-6 iso88596 "
                  "iso_8859-6 iso_8859-6:1987")),
    ("iso8859_7", ("csisolatingreek ecma-118 elot_928 greek greek8 iso-8859-7 iso-ir-126 "
                  "iso8859-7 iso88597 iso_8859-7 iso_8859-7:1987 sun_eu_greek")),
    ("iso8859_8", ("csiso88598e csisolatinhebrew hebrew iso-8859-8 iso-8859-8-e iso-ir-138 "
                  "iso8859-8 iso88598 iso_8859-8 iso_8859-8:1988 visual "
                  "csiso88598i iso-8859-8-i logical")),
    ("iso8859_10", "csisolatin6 iso-8859-10 iso-ir-157 iso8859-10 iso885910 l6 latin6"),
    ("iso8859_13", "iso-8859-13 iso8859-13 iso885913"),
    ("iso8859_14", "iso-8859-14 iso8859-14 iso885914"),
    ("iso8859_15", "csisolatin9 iso-8859-15 iso8859-15 iso885915 iso_8859-15 l9"),
    ("iso8859_16", "iso-8859-16"),
    ("koi8_r", "cskoi8r koi koi8 koi8-r koi8_r"),
    ("koi8_u", "koi8-ru koi8-u"),
    ("mac_roman", "csmacintosh mac macintosh x-mac-roman"),
    ("cp874", "dos-874 iso-8859-11 iso8859-11 iso885911 tis-620 windows-874"),
    ("cp1250", "cp1250 windows-1250 x-cp1250"),
    ("cp1251", "cp1251 windows-1251 x-cp1251"),
    ("cp1252", ("ansi_x3.4-1968 ascii cp1252 cp819 csisolatin1 ibm819 iso-8859-1 iso-ir-100 "
               "iso8859-1 iso88591 iso_8859-1 iso_8859-1:1987 l1 latin1 us-ascii "
               "windows-1252 x-cp1252")),
    ("cp1253", "cp1253 windows-1253 x-cp1253"),
    ("cp1254", ("cp1254 csisolatin5 iso-8859-9 iso-ir-148 iso8859-9 iso88599 iso_8859-9 "
               "iso_8859-9:1989 l5 latin5 windows-1254 x-cp1254")),
    ("cp1255", "cp1255 windows-1255 x-cp1255"),
    ("cp1256", "cp1256 windows-1256 x-cp1256"),
    ("cp1257", "cp1257 windows-1257 x-cp1257"),
    ("cp1258", "cp1258 windows-1258 x-cp1258"),
    ("mac_cyrillic", "x-mac-cyrillic x-mac-ukrainian"),
    ("gbk", "chinese csgb2312 csiso58gb231280 gb2312 gb_2312 gb_2312-80 gbk iso-ir-58 x-gbk"),
    ("gb18030", "gb18030"),
    ("big5hkscs", "big5 big5-hkscs cn-big5 csbig5 x-x-big5"),
    ("euc_jp", "cseucpkdfmtjapanese euc-jp x-euc-jp"),
    ("iso2022_jp", "csiso2022jp iso-2022-jp"),
    ("cp932", "csshiftjis ms932 ms_kanji shift-jis shift_jis sjis windows-31j x-sjis"),
    ("cp949", ("cseuckr csksc56011987 euc-kr iso-ir-149 korean ks_c_5601-1987 ks_c_5601-1989 "
              "ksc5601 ksc_5601 windows-949")),
    ("utf-16-be", "unicodefffe utf-16be"),
    ("utf-16-le", "csunicode iso-10646-ucs-2 ucs-2 unicode unicodefeff utf-16 utf-16le"),
)
_WHATWG = {label: codec for codec, labels in _WHATWG_ENCODINGS for label in labels.split()}


def _b64url_bytes(data) -> bytes:
    """Gmail's base64url (unpadded), by the standard library."""
    text = str(data)
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _decode(data, charset: str) -> str:
    """`decode()` of `Normalize response`: base64url, then the part's charset;
    a failure is the empty string, never an exception."""
    if not data:
        return ""
    try:
        buf = _b64url_bytes(data)
        cs = (charset or "utf-8").lower()
        if cs in ("utf-8", "utf8", "us-ascii", "ascii"):
            return buf.decode("utf-8", errors="replace")
        # A WHATWG label only (`_WHATWG`); anything else is the node's
        # fall-through to UTF-8. (The node's latin1 branch after TextDecoder
        # is unreachable: TextDecoder knows all four of its labels.)
        return buf.decode(_WHATWG.get(cs, "utf-8"), errors="replace")
    except Exception:  # noqa: BLE001 — the node's `catch (e) { return ''; }`
        return ""


_CHARSET_RE = re.compile(r"""charset\s*=\s*"?([\w.:-]+)"?""", re.IGNORECASE)


def _headers(part) -> list:
    hs = _get(part, "headers")
    return hs if isinstance(hs, list) else []


def _header_map(payload) -> dict:
    out: dict = {}
    for h in _headers(payload):
        if isinstance(h, dict) and h.get("name"):
            out[str(h["name"]).lower()] = h.get("value")
    return out


def _header_all(payload, name: str) -> list[str]:
    return [str(h.get("value") or "") for h in _headers(payload)
            if isinstance(h, dict) and h.get("name") and str(h["name"]).lower() == name]


def _charset_of(part) -> str:
    for h in _headers(part):
        if isinstance(h, dict) and h.get("name") and str(h["name"]).lower() == "content-type":
            m = _CHARSET_RE.search(str(h.get("value") or ""))
            if m:
                return m.group(1).lower()
    return ""


def _disposition_of(part) -> tuple[bool, bool]:
    disp, cid = "", ""
    for h in _headers(part):
        if not isinstance(h, dict) or not h.get("name"):
            continue
        n = str(h["name"]).lower()
        if n == "content-disposition":
            disp = str(h.get("value") or "").lower()
        elif n == "content-id":
            cid = str(h.get("value") or "")
    is_attachment = disp.startswith("attachment")
    return is_attachment, (not is_attachment and (disp.startswith("inline") or bool(cid)))


def _size(body, fallback=None):
    return body["size"] if isinstance(body, dict) and "size" in body else fallback


def _walk(part, acc: dict) -> None:
    if not part:
        return
    mime = str(_get(part, "mimeType") or "").lower()
    body = _get(part, "body") or {}
    is_attachment, is_inline = _disposition_of(part)
    if _get(body, "attachmentId"):
        acc["attachments"].append({
            "filename": _get(part, "filename") or "", "mime_type": _get(part, "mimeType") or "",
            "size": _size(body), "attachment_id": body["attachmentId"], "is_inline": is_inline,
        })
        if mime in ("text/plain", "text/html") and not is_attachment and not _get(body, "data"):
            acc["body_truncated"] = True
    elif mime == "text/plain" and _get(body, "data") and not is_attachment and not acc["body_text"]:
        acc["body_text"] = _decode(body["data"], _charset_of(part))
    elif mime == "text/html" and _get(body, "data") and not is_attachment and not acc["body_html"]:
        acc["body_html"] = _decode(body["data"], _charset_of(part))
    elif mime == "text/calendar" and _get(body, "data"):
        text = _decode(body["data"], _charset_of(part))
        if text:
            entry = {"filename": _get(part, "filename") or "invite.ics", "mime_type": "text/calendar",
                     "size": _size(body, len(text)), "attachment_id": "", "is_inline": False}
            entry.update({"inline_text": text} if len(text) <= 200000 else {"too_large": True})
            acc["attachments"].append(entry)
    if mime == "message/rfc822":
        return
    parts = _get(part, "parts")
    if isinstance(parts, list):
        for p in parts:
            _walk(p, acc)


def _iso(dt: datetime) -> str:
    """`Date.prototype.toISOString()`: UTC, milliseconds, `Z`."""
    return dt.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _iso_from_ms(value) -> str:
    """`new Date(Number(internalDate)).toISOString()`."""
    try:
        ms = float(str(value).strip())
        return _iso(datetime.fromtimestamp(ms / 1000, UTC))
    except (ValueError, OverflowError, OSError):
        # Not a number, or a number no date has (JS: an Invalid Date's
        # toISOString() throws "Invalid time value").
        raise _MailFail("Invalid time value") from None


def _iso_from_header(value: str) -> str | None:
    """`Date.parse(Date header)`, by `email.utils.parsedate_to_datetime`; a
    header without a zone is read as UTC."""
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    return _iso(dt if dt.tzinfo else dt.replace(tzinfo=UTC))


def normalize_message(msg) -> dict:
    """`normalizeOne()` of `Normalize response`: raw Gmail → the canonical record."""
    acc = {"body_text": "", "body_html": "", "attachments": [], "body_truncated": False}
    payload = _get(msg, "payload")
    hdr = _header_map(payload)
    if payload:
        _walk(payload, acc)
        pbody = _get(payload, "body") or {}
        if not acc["body_text"] and not acc["body_html"] and _get(pbody, "data"):
            d = _decode(pbody["data"], _charset_of(payload))
            if "html" in str(_get(payload, "mimeType") or ""):
                acc["body_html"] = d
            else:
                acc["body_text"] = d
    date = None
    if _get(msg, "internalDate"):
        date = _iso_from_ms(msg["internalDate"])
    elif hdr.get("date"):
        date = _iso_from_header(str(hdr["date"]))
    out = {
        "uuid": _get(msg, "id"),
        "conversation_id": _get(msg, "threadId"),
        "from": hdr.get("from") or "",
        "to": hdr.get("to") or "",
        "cc": hdr.get("cc") or "",
        "subject": hdr.get("subject") or "",
        "list_unsubscribe": hdr.get("list-unsubscribe") or "",
        "list_unsubscribe_post": hdr.get("list-unsubscribe-post") or "",
        "authentication_results": _header_all(payload, "authentication-results"),
        "dkim_signatures": _header_all(payload, "dkim-signature"),
        "date": date,
        "snippet": _get(msg, "snippet") or "",
        "body_text": acc["body_text"],
        "body_html": acc["body_html"],
        "body_truncated": acc["body_truncated"],
        "has_attachments": len(acc["attachments"]) > 0,
        "attachments": acc["attachments"],
    }
    # JSON.stringify drops a key whose value is undefined.
    return {k: v for k, v in out.items() if not (k in ("uuid", "conversation_id") and v is None)}


def _gmail(timeout: float):
    return _service("gmail", "v1", timeout).users()


def _mail_message(payload: dict, ref: dict, timeout: float) -> dict:
    """mode `message`: `Get message (Gmail API)` (messages.get, format=full)
    → `Normalize response` → `Return envelope`."""
    status, body = _exec(_gmail(timeout).messages().get(userId="me", id=ref["uuid"], format="full"))
    msg = _http_body(status, body, "message")
    return {"kind": "email", "mode": "message", "source_ref": payload.get("source_ref") or {},
            "messages": [normalize_message(msg)]}


def _page_body(status: int, body):
    """`pageBody()` of `Build list envelope`."""
    if 200 <= status < 300:
        if not isinstance(body, dict):
            raise _MailFail(_MAIL_PREFIX + "list page body is not a JSON object - refusing to treat as an empty page")
        return body
    if status == 400:
        raise _MailFail(_MAIL_PREFIX + "provider rejected the list query (400) - check scope_query syntax")
    if sentence := _auth_or_rate(status, _MAIL_REAUTH):
        raise _MailFail(_MAIL_PREFIX + sentence)
    raise _MailFail(_MAIL_PREFIX + f"provider list failed ({status})")


def _mail_list(payload: dict, timeout: float) -> dict:
    """mode `list`: `List messages (Gmail API)` (messages.list, q,
    maxResults=500, at most 5 requests; the next page by the library's
    `list_next`, which stops when a page has no nextPageToken) → `Build list
    envelope` (references only)."""
    query = payload["scope_query"]
    messages_api = _gmail(timeout).messages()
    request = messages_api.list(userId="me", q=query, maxResults=LIST_PAGE_SIZE)
    bodies = []
    for _ in range(LIST_MAX_PAGES):
        try:
            status, raw = _exec(request)
        except _Transport as e:
            raise _MailFail(_MAIL_PREFIX + f"list request failed before a response (connection/transport): {e}") \
                from e.__cause__
        body = _page_body(status, raw)
        bodies.append(body)
        request = messages_api.list_next(request, body)
        if request is None:
            break
    if bodies and bodies[-1].get("nextPageToken"):
        raise _MailFail(_MAIL_PREFIX + f"list pagination cap hit ({len(bodies)} pages fetched, more remain) "
                        "- refusing to silently truncate; narrow scope_query")
    messages, dropped = [], 0
    for b in bodies:
        for m in (b.get("messages") if isinstance(b.get("messages"), list) else []):
            if _id_ok(_get(m, "id")) and _id_ok(_get(m, "threadId")):
                messages.append({"uuid": m["id"], "conversation_id": m["threadId"]})
            else:
                dropped += 1
                log.info("google: list dropped an out-of-alphabet entry")
    if dropped and not messages:
        raise _MailFail(_MAIL_PREFIX + f"list dropped ALL {dropped} entries as out-of-alphabet - id shape breach, "
                        "refusing to return an empty envelope")
    return {"kind": "email", "mode": "list", "source_ref": {"scope_query": query},
            "messages": messages, "dropped": dropped}


def _mail_report_spam(payload: dict, ref: dict, timeout: float) -> dict:
    """mode `report_spam`: `Report spam (Gmail API)` (messages.modify +SPAM
    −INBOX) → `Build spam envelope`. The one mail write; Executor-only."""
    s, body = _exec(_gmail(timeout).messages().modify(
        userId="me", id=ref["uuid"], body={"addLabelIds": ["SPAM"], "removeLabelIds": ["INBOX"]}))
    if s == 404:
        raise _MailFail(_MAIL_PREFIX + "message not found for the given reference")
    if sentence := _auth_or_rate(s, _MAIL_REAUTH):
        raise _MailFail(_MAIL_PREFIX + sentence)
    if s < 200 or s >= 300:
        raise _MailFail(_MAIL_PREFIX + f"report_spam failed ({s})")
    return {"kind": "email", "mode": "report_spam", "source_ref": payload.get("source_ref") or {},
            "ok": True, "label_ids": _get(body, "labelIds") or []}


def _mail_attachment(payload: dict, ref: dict, timeout: float) -> dict:
    """mode `attachment`: `Get attachment (Gmail API)` (messages.attachments.get,
    a JSON `MessagePartBody`, not a media download) → `Build attachment
    envelope` (base64url → standard, padded base64)."""
    s, body = _exec(_gmail(timeout).messages().attachments().get(
        userId="me", messageId=ref["uuid"], id=ref["attachment_id"]))
    if s in (400, 404):
        raise _MailFail(_MAIL_PREFIX + f"attachment not found for the given reference ({s})")
    if sentence := _auth_or_rate(s, _MAIL_REAUTH):
        raise _MailFail(_MAIL_PREFIX + sentence)
    if s < 200 or s >= 300:
        raise _MailFail(_MAIL_PREFIX + f"attachment fetch failed ({s})")
    data = _get(body, "data")
    if not isinstance(data, str) or data == "":
        raise _MailFail(_MAIL_PREFIX + "attachment fetch failed (the provider answered without data)")
    try:
        b64 = base64.b64encode(_b64url_bytes(data)).decode("ascii")
    except ValueError:
        raise _MailFail(_MAIL_PREFIX + "attachment fetch failed (the data is not base64url)") from None
    return {"kind": "email", "mode": "attachment", "source_ref": payload.get("source_ref") or {},
            "size": _size(body), "data_b64": b64}


def _mail_sync(payload: dict, timeout: float) -> dict:
    try:
        ref = _validate_mail(payload)
        mode = payload["mode"]
        if mode == "message":
            return _mail_message(payload, ref, timeout)
        if mode == "list":
            return _mail_list(payload, timeout)
        if mode == "report_spam":
            return _mail_report_spam(payload, ref, timeout)
        if mode == "attachment":
            return _mail_attachment(payload, ref, timeout)
        raise _MailFail(_MAIL_PREFIX + f"mode '{mode}' is not served by the native client")
    except _MailFail as e:
        message = str(e)[:500]
        raise GoogleError(message, status_code=_mail_status(message)) from e.__cause__
    except _TokenUnavailable as e:
        message = (_MAIL_PREFIX + f"provider auth failed ({e}) - re-run "
                   "python -m central_command.google_consent")[:500]
        raise GoogleError(message, status_code=503) from e.__cause__
    except _Transport as e:
        # A request node without an error output: n8n threw, the classifier
        # did not know the sentence, the façade answered 503.
        message = (_MAIL_PREFIX + f"{payload.get('mode')} request failed before a response "
                   f"(connection/transport): {e}")[:500]
        raise GoogleError(message, status_code=503) from e.__cause__
    except Exception as e:
        message = (_MAIL_PREFIX + f"{payload.get('mode')} failed: {type(e).__name__}")[:500]
        raise GoogleError(message, status_code=503) from e


async def mail_request(payload: dict, timeout: float = 60.0) -> dict:
    """What `POST cc-email-facade` answered for `payload`: the envelope, or a
    `GoogleError` carrying the status the façade's classifier would have set.
    The whole operation runs in one worker thread (the library is
    synchronous). `thread` mode is validated like the workflow does but has no
    caller in the app, so it is not reproduced (it answers 503, as an unrouted
    mode would)."""
    if missing_settings():
        raise NotConfigured(", ".join(missing_settings()))
    return await asyncio.to_thread(_mail_sync, payload, timeout)


# --- calendar: lib-google-calendar -------------------------------------------

_CAL_MODES = ("list", "create_event", "update_event", "delete_event", "get_event")
_ISO = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})")
_CAL_ID = re.compile(r"[A-Za-z0-9._%+@#-]+")
_EVENT_ID = re.compile(r"[A-Za-z0-9_@.-]{1,1024}")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}")
_CLIENT_EVENT_ID = re.compile(r"[a-v0-9]{5,1024}")
_REAUTH = ("re-run python -m central_command.google_consent, the stored refresh token "
           "looks stale")


class _CalFail(Exception):
    """`fail()` in a lib-google-calendar Code node: n8n's HTTP 500."""


def _neutral(text: str) -> str:
    return re.sub(r":", "-", re.sub(r":\s", " - ", text))


def _fail(body: str):
    raise _CalFail("lib-google-calendar: " + _neutral(str(body)))


def _s(value, limit: int = 120) -> str:
    return re.sub(r"[\r\n\t]+", " ", _neutral(_js_str(value)))[:limit]


def _instant(value) -> datetime | None:
    """`Date.parse` of a string the ISO pattern already accepted."""
    try:
        return datetime.fromisoformat(value)
    except (ValueError, AttributeError):
        return None


def _validate_calendar(inp: dict) -> dict:
    """`Auth + unwrap`'s mode allowlist (cc-calendar-facade) and `Validate
    request` (lib-google-calendar), in the node's order."""
    if inp.get("mode") not in _CAL_MODES:
        # cc-calendar-facade refuses it before the lib is called.
        raise _CalFail("cc-calendar-facade - unsupported mode")
    calendar_id = inp.get("calendar_id")
    if calendar_id is None or calendar_id == "":
        calendar_id = "primary"
    if not isinstance(calendar_id, str) or len(calendar_id) > 200 or not _CAL_ID.fullmatch(calendar_id):
        _fail(f"'calendar_id' is not a valid calendar id, got '{_s(calendar_id)}'")

    def has(k):
        return inp.get(k) is not None

    if inp["mode"] == "list":
        for f in ("time_min", "time_max"):
            v = inp.get(f)
            if not isinstance(v, str) or not _ISO.fullmatch(v):
                _fail(f"'{f}' must be RFC3339 with an explicit offset or Z, got '{_s(v)}'")
            if _instant(v) is None:
                _fail(f"'{f}' is not a real datetime, got '{_s(v)}'")
        if _instant(inp["time_max"]) <= _instant(inp["time_min"]):
            _fail("time_max must be after time_min")
        raw = inp.get("max_results")
        max_results = 250 if raw is None else raw
        if isinstance(max_results, bool) or not isinstance(max_results, (int, float)) \
                or not float(max_results).is_integer() or max_results < 1:
            _fail(f"'max_results' must be an integer >= 1, got '{_s(raw)}'")
        return {"mode": "list", "time_min": inp["time_min"], "time_max": inp["time_max"],
                "calendar_id": calendar_id, "max_results": min(int(max_results), 250)}

    def event_id():
        v = inp.get("event_id")
        if not isinstance(v, str) or not _EVENT_ID.fullmatch(v):
            _fail(f"'event_id' is not a valid event id, got '{_s(v)}'")
        return v

    def title():
        v = inp.get("title")
        if not isinstance(v, str) or v.strip() == "" or len(v) > 300:
            _fail(f"'title' must be a non-empty string of at most 300 chars, got '{_s(v)}'")
        return v

    def description():
        v = inp.get("description")
        if not isinstance(v, str):
            _fail(f"'description' must be a string, got '{_s(v)}'")
        return v

    def attendees():
        a = inp.get("attendees")
        if not isinstance(a, list):
            _fail(f"'attendees' must be an array of email addresses, got '{_s(a)}'")
        if len(a) > 50:
            _fail(f"'attendees' has {len(a)} entries, the ceiling here is 50")
        for e in a:
            if not isinstance(e, str) or not _EMAIL.fullmatch(e):
                _fail(f"'attendees' holds something that is not an email address, got '{_s(e)}'")
        return [{"email": e} for e in a]

    def client_event_id():
        v = inp.get("client_event_id")
        if not isinstance(v, str) or not _CLIENT_EVENT_ID.fullmatch(v):
            _fail("'client_event_id' must be 5 to 1024 characters of a-v and 0-9 (a Google base32hex "
                  f"event id), got '{_s(v)}'")
        return v

    def when(f):
        v = inp.get(f)
        if not isinstance(v, str) or not _ISO.fullmatch(v) or _instant(v) is None:
            _fail(f"'{f}' must be RFC3339 with an explicit offset or Z, got '{_s(v)}'")
        # No timeZone key: the string carries an explicit offset.
        return {"dateTime": v}

    mode = inp["mode"]
    if mode == "create_event":
        body = {"summary": title(), "start": when("start"), "end": when("end")}
        if _instant(inp["end"]) <= _instant(inp["start"]):
            _fail("end must be after start")
        body["description"] = description() if has("description") else ""
        body["attendees"] = attendees() if has("attendees") else []
        if has("client_event_id"):
            body["id"] = client_event_id()
        return {"mode": mode, "calendar_id": calendar_id, "__body": body}
    if mode == "update_event":
        eid = event_id()
        if not [f for f in ("title", "start", "end", "description", "attendees") if has(f)]:
            _fail("'update_event' needs at least one of title, start, end, description, attendees "
                  "- a no-op write is a bug, not a success")
        body = {}
        if has("title"):
            body["summary"] = title()
        if has("start"):
            body["start"] = when("start")
        if has("end"):
            body["end"] = when("end")
        if has("start") and has("end") and _instant(inp["end"]) <= _instant(inp["start"]):
            _fail("end must be after start")
        if has("description"):
            body["description"] = description()
        if has("attendees"):
            body["attendees"] = attendees()
        return {"mode": mode, "calendar_id": calendar_id, "event_id": eid, "__body": body}
    return {"mode": mode, "calendar_id": calendar_id, "event_id": event_id()}


def _detail_log(op: str, req: dict, status, detail) -> None:
    """The workflow's `console.log` of the provider detail: the worker log,
    never the message (the detail is Google's own text, not a secret)."""
    log.info("google calendar provider detail [%s %s] status=%s :: %s",
             op, _s(req.get("calendar_id"), 64), status, str(detail)[:300])


def _cal_status(op: str, req: dict, answer, *, not_found: str | None):
    """The failure classification shared by every Return node: no status
    (`answer` None) → fail; non-2xx → fail with the status's sentence. Returns
    the body on 2xx."""
    if answer is None:
        _fail(f"{op} - provider call failed with no HTTP status - outcome unknown, see the worker log")
    status, body = answer
    if status < 200 or status >= 300:
        err = _get(body, "error") or {}
        _detail_log(op, req, status, _get(err, "message") or body)
        if sentence := _auth_or_rate(status, _REAUTH):
            _fail(f"{op} - {sentence}")
        if status == 404 and not_found is not None:
            _fail(f"{op} - {not_found} for {_s(req.get('calendar_id'), 64)}")
        _fail(f"{op} - provider call failed ({status}) - see the worker log for the provider detail")
    return body if body != "" else {}


def _short_event(ev) -> dict:
    out = {
        "event_id": _get(ev, "id"), "html_link": _get(ev, "htmlLink") or "",
        "summary": _get(ev, "summary") or "",
        "start": (_get(_get(ev, "start"), "dateTime") or _get(_get(ev, "start"), "date")) or None,
        "end": (_get(_get(ev, "end"), "dateTime") or _get(_get(ev, "end"), "date")) or None,
    }
    if out["event_id"] is None:
        del out["event_id"]  # JSON.stringify drops undefined
    return out


def _flags(ev) -> dict:
    st, en = _get(ev, "start") or {}, _get(ev, "end") or {}
    attendees = _get(ev, "attendees")
    attendees = attendees if isinstance(attendees, list) else []
    me = next((a for a in attendees if isinstance(a, dict) and a.get("self") is True), None)
    organizer, creator = _get(ev, "organizer"), _get(ev, "creator")
    description = _get(ev, "description")
    files = _get(ev, "attachments")
    return {
        "start": _get(st, "dateTime") or _get(st, "date") or None,
        "end": _get(en, "dateTime") or _get(en, "date") or None,
        "all_day": not _get(st, "dateTime") and bool(_get(st, "date")),
        "summary": _get(ev, "summary") or "",
        "location": _get(ev, "location") or "",
        "attendee_count": len(attendees),
        "organizer_is_self": bool(organizer and _get(organizer, "self"))
        or bool(creator and _get(creator, "self") and not organizer),
        "has_agenda": bool(description and _js_str(description).strip())
        or (isinstance(files, list) and len(files) > 0),
        "declined": bool((me is not None and me.get("responseStatus") == "declined")
                         or _get(ev, "status") == "cancelled"),
    }


def _cal_exec(request):
    """A request node with `onError: continueRegularOutput`: a transport
    failure reaches the Return node as an item with no status (None here)."""
    try:
        return _exec(request)
    except _Transport as e:
        log.info("google calendar transport failure: %s", e)
        return None


def _events():
    return _service("calendar", "v3", CALENDAR_TIMEOUT_SECONDS).events()


def _cal_list(req: dict) -> dict:
    """`List events (GCal REST)` → `Return: list`."""
    body = _cal_status("list", req, _cal_exec(_events().list(
        calendarId=req["calendar_id"], timeMin=req["time_min"], timeMax=req["time_max"],
        maxResults=req["max_results"], singleEvents=True, orderBy="startTime", showDeleted=False)),
        not_found="calendar not found")
    empty = _get(body, "kind") == "calendar#events" and (not isinstance(body, dict) or "items" not in body)
    items = _get(body, "items")
    if not isinstance(items, list) and not empty:
        _detail_log("list", req, 200, "2xx without an items array")
        return {"ok": False, "message": "list - provider answered 2xx without an items array"}
    raw = [] if empty else items
    events = [{"event_id": _get(ev, "id") or "", **_flags(ev)} for ev in raw]
    return {"ok": True, "events": events,
            "truncated": bool(_get(body, "nextPageToken")) or len(events) >= req["max_results"]}


def _cal_get(req: dict) -> dict:
    """`Get event (GCal REST)` → `Return: get_event`."""
    answer = _cal_exec(_events().get(calendarId=req["calendar_id"], eventId=req["event_id"]))
    if answer is not None and answer[0] in (404, 410):
        return {"ok": False, "message": f"get - event not found ({answer[0]})"}
    ev = _cal_status("get", req, answer, not_found="calendar or event not found")
    if not _get(ev, "id"):
        _detail_log("get", req, answer[0], "2xx without an event id")
        return {"ok": False, "message": "get - provider answered 2xx without an event"}
    flags = _flags(ev)
    return {"ok": True, "event": {
        "event_id": ev["id"], "status": _get(ev, "status") or "",
        "start": flags["start"], "end": flags["end"], "all_day": flags["all_day"],
        "summary": flags["summary"], "location": flags["location"],
        "attendee_count": flags["attendee_count"],
        "organizer": _get(_get(ev, "organizer"), "email") or "",
        "organizer_is_self": flags["organizer_is_self"], "has_agenda": flags["has_agenda"],
        "declined": flags["declined"],
    }}


def _cal_create(req: dict) -> dict:
    """`Create event (GCal REST)` (events.insert, sendUpdates=all) → `Route
    (create answer)`: a 409 on OUR client id goes to `Get existing event` →
    `Return: create_event (existing)`; anything else to `Return: create_event`
    (DL-219)."""
    answer = _cal_exec(_events().insert(calendarId=req["calendar_id"], body=req["__body"],
                                        sendUpdates="all"))
    if answer is not None and answer[0] == 409 and req["__body"].get("id"):
        return _cal_create_existing(req)
    ev = _cal_status("create", req, answer, not_found="calendar or event not found")
    return {"ok": True, "event": _short_event(ev)}


def _cal_create_existing(req: dict) -> dict:
    answer = _cal_exec(_events().get(calendarId=req["calendar_id"], eventId=req["__body"]["id"]))
    if answer is None:
        _fail("create - the event id was already taken and reading it back failed with no HTTP status "
              "- outcome unknown, see the worker log")
    if answer[0] in (404, 410):
        return {"ok": False, "message": "create - the provider refused the event id as already taken (409) "
                f"but holds no readable event under it ({answer[0]}) - nothing was created, "
                "the operator decides"}
    ev = _cal_status("create", req, answer, not_found=None)
    if not _get(ev, "id"):
        _detail_log("create-existing", req, answer[0], "2xx without an event id")
        return {"ok": False, "message": "create - the event id was already taken and the provider answered "
                "2xx without an event - nothing was created, the operator decides"}
    if _get(ev, "status") == "cancelled":
        return {"ok": False, "message": "create - this event was created by an earlier attempt of the same "
                "approved action and has since been cancelled on the calendar - it was not created again, "
                "the operator decides"}
    return {"ok": True, "already_existed": True, "event": _short_event(ev)}


def _cal_update(req: dict) -> dict:
    """`Update event (GCal REST)` (events.patch, sendUpdates=all) → `Return: update_event`."""
    answer = _cal_exec(_events().patch(calendarId=req["calendar_id"], eventId=req["event_id"],
                                       body=req["__body"], sendUpdates="all"))
    ev = _cal_status("update", req, answer, not_found="calendar or event not found")
    return {"ok": True, "event": _short_event(ev)}


def _cal_delete(req: dict) -> dict:
    """`Delete event (GCal REST)` (events.delete, sendUpdates=all) → `Return:
    delete_event`; 410 (already gone) is success."""
    answer = _cal_exec(_events().delete(calendarId=req["calendar_id"], eventId=req["event_id"],
                                        sendUpdates="all"))
    if answer is not None and answer[0] == 410:
        return {"ok": True, "deleted": req["event_id"]}
    _cal_status("delete", req, answer, not_found="calendar or event not found")
    return {"ok": True, "deleted": req["event_id"]}


_CAL_OP = {"list": "list", "get_event": "get", "create_event": "create",
           "update_event": "update", "delete_event": "delete"}


def _calendar_sync(payload: dict) -> dict:
    try:
        req = _validate_calendar(payload)
        handler = {"list": _cal_list, "get_event": _cal_get, "create_event": _cal_create,
                   "update_event": _cal_update, "delete_event": _cal_delete}[req["mode"]]
        try:
            return handler(req)
        except _TokenUnavailable as e:
            raise _CalFail("lib-google-calendar: " + _neutral(
                f"{_CAL_OP[req['mode']]} - provider auth failed ({e}) - {_REAUTH}")) from e.__cause__
        except _CalFail:
            raise
        except Exception as e:
            raise _CalFail("lib-google-calendar: " + _neutral(
                f"{_CAL_OP[req['mode']]} - provider call failed ({type(e).__name__})")) from e
    except _CalFail as e:
        raise GoogleError(str(e), status_code=500) from e.__cause__


async def calendar_request(payload: dict) -> dict:
    """What `POST cc-calendar-facade` answered for `payload` with HTTP 200 (the
    envelope, `ok` true or false), or a `GoogleError` with status 500 for every
    `fail()` — n8n's "Error in workflow". One worker thread per call."""
    if missing_settings():
        raise NotConfigured(", ".join(missing_settings()))
    return await asyncio.to_thread(_calendar_sync, payload)


def stamp(error: BaseException) -> BaseException:
    """Stamp the `google` dependency on a façade error built from a Google
    failure, so a park waits on the Google probe (`contract.dependencies`)."""
    from central_command.contract.dependencies import DEPENDENCIES_ATTR, GOOGLE

    setattr(error, DEPENDENCIES_ATTR, (GOOGLE,))
    return error


def answer_text(error: GoogleError) -> str:
    """The response body the n8n façade would have sent with this status:
    `{"message": …}`, as `JSON.stringify` writes it."""
    return json.dumps({"message": error.message}, separators=(",", ":"), ensure_ascii=False)
