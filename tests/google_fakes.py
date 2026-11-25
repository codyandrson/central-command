"""A stand-in for Google's endpoints at the HTTP layer, for the native Google
client's tests (DL-234). No real Google account is ever contacted: every
request the client makes goes through `httpx.MockTransport` to `FakeGoogle`,
which records it and answers from a script. The native client reaches it
through `FakeHttp`, an `httplib2.Http` stand-in at the client library's own
HTTP layer; the n8n path through `httpx.MockTransport` (`install`).

The fixtures follow Google's published reference, not memory:

- Gmail `users.messages.list` — https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/list
  (`messages[]` of `{id, threadId}`, `nextPageToken`, `resultSizeEstimate`;
  an empty result omits `messages`)
- Gmail `users.messages.get` and the `Message`/`MessagePart`/`MessagePartBody`
  resources — https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages
  (`internalDate` is an int64 millisecond string, `payload.headers[]` of
  `{name, value}`, `body.data` base64url, `body.attachmentId` for a part held
  by reference)
- Gmail `users.messages.attachments.get` — https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages.attachments/get
  (a `MessagePartBody`: `{attachmentId, size, data}`)
- Gmail `users.messages.modify` — https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/modify
  (returns the `Message` with its new `labelIds`)
- Calendar `events.list/get/insert/patch/delete` and the `Event` resource —
  https://developers.google.com/workspace/calendar/api/v3/reference/events
  (`kind: calendar#events`, `items[]`, `nextPageToken`; an event's
  `start`/`end` carry `dateTime` or `date`; delete answers 204 with no body,
  410 when already deleted)
- Errors — https://developers.google.com/workspace/gmail/api/guides/handle-errors
  (`{"error": {"code", "message", "errors": [...], "status"}}`)
- Token refresh — https://developers.google.com/identity/protocols/oauth2/native-app
  ("Refreshing an access token": POST `https://oauth2.googleapis.com/token`,
  form `client_id`, `client_secret`, `refresh_token`, `grant_type=refresh_token`;
  answer `{access_token, expires_in, scope, token_type}`)
"""

from __future__ import annotations

import base64
import json
from urllib.parse import parse_qs

import httpx

from central_command.config import settings
from central_command.integrations import google

GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
CAL = "https://www.googleapis.com/calendar/v3/calendars/"


def path(request: httpx.Request) -> str:
    return str(request.url).split("?")[0]


def params(request: httpx.Request) -> dict:
    """The query, without the `alt=json` the client library adds to every
    request (the n8n nodes sent none; Google's default is JSON either way)."""
    return {k: v for k, v in request.url.params.multi_items() if k != "alt"}


TOKEN = "https://oauth2.googleapis.com/token"

# Fake credentials (never real ones): the client id has Google's shape so
# nothing downstream is surprised by it.
CLIENT_ID = "123456789012-abcdefghijklmnop.apps.googleusercontent.com"
CLIENT_SECRET = "fake-client-secret-not-real"
REFRESH_TOKEN = "1//fake-refresh-token-not-real"

_real_async = httpx.AsyncClient
_real_sync = httpx.Client


def b64url(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def error_body(code: int, message: str = "error", status: str = "FAILED_PRECONDITION") -> dict:
    return {
        "error": {
            "code": code,
            "message": message,
            "errors": [{"message": message, "domain": "global", "reason": "failed"}],
            "status": status,
        }
    }


class FakeGoogle:
    """Scripted Google. `on(method, url, *answers)` queues answers for an exact
    URL path (the query is matched separately when `query=` is given); each
    answer is `(status, body)` or an exception to raise as a transport
    failure; the last answer repeats. Every request is recorded."""

    def __init__(self, *, require_auth: bool = False):
        self.routes: list[tuple[str, str, dict | None, list]] = []
        self.requests: list[httpx.Request] = []
        self.require_auth = require_auth
        self.issued = 0
        self.token_answers: list = []

    def on(self, method: str, url: str, *answers, query: dict | None = None) -> FakeGoogle:
        self.routes.append((method, url, query, list(answers)))
        return self

    def token(self, *answers) -> FakeGoogle:
        self.token_answers = list(answers)
        return self

    def of(self, method: str, url: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == method and str(r.url).split("?")[0] == url]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url).split("?")[0]
        if request.method == "POST" and url == TOKEN:
            return self._token(request)
        if self.require_auth and not request.headers.get("authorization", "").startswith(
            "Bearer ya29."
        ):
            return httpx.Response(
                401,
                json=error_body(
                    401, "Request had invalid authentication credentials.", "UNAUTHENTICATED"
                ),
            )
        query = {k: v[-1] for k, v in parse_qs(request.url.query.decode()).items()}
        for method, path, want, answers in self.routes:
            if (
                method == request.method
                and path == url
                and (want is None or all(query.get(k) == v for k, v in want.items()))
            ):
                answer = answers.pop(0) if len(answers) > 1 else answers[0]
                if isinstance(answer, BaseException):
                    raise answer
                status, body = answer
                if body is None:
                    return httpx.Response(status)
                return httpx.Response(status, json=body)
        raise AssertionError(f"FakeGoogle has no route for {request.method} {request.url}")

    def _token(self, request: httpx.Request) -> httpx.Response:
        form = {k: v[-1] for k, v in parse_qs(request.content.decode()).items()}
        assert form.get("grant_type") == "refresh_token", form
        assert form.get("refresh_token") == settings.google_refresh_token
        assert form.get("client_id") == settings.google_client_id
        assert form.get("client_secret") == settings.google_client_secret
        if self.token_answers:
            answer = (
                self.token_answers.pop(0) if len(self.token_answers) > 1 else self.token_answers[0]
            )
            if isinstance(answer, BaseException):
                raise answer
            return httpx.Response(answer[0], json=answer[1])
        self.issued += 1
        return httpx.Response(
            200,
            json={
                "access_token": f"ya29.fake-access-{self.issued}",
                "expires_in": 3599,
                "scope": " ".join(google.SCOPES),
                "token_type": "Bearer",
            },
        )


def install(monkeypatch, handler) -> None:
    """Every httpx client (async and sync) built from now on answers through
    `handler`: the seam the suite already uses for Jira and the probes (here:
    the n8n webhook in the parity test, and the probe)."""
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda *a, **kw: _real_async(*a, **{**kw, "transport": transport})
    )
    monkeypatch.setattr(
        httpx, "Client", lambda *a, **kw: _real_sync(*a, **{**kw, "transport": transport})
    )


class FakeHttp:
    """`httplib2.Http` for the client library (its HTTP layer: what
    `googleapiclient.http.HttpMock` stands in for), answering from a
    `FakeGoogle`. A transport failure the script raises as an httpx error is
    raised as the OSError httplib2 would raise. Records the timeout it was
    built with."""

    def __init__(self, google: FakeGoogle, timeout=None):
        self.google = google
        self.timeout = timeout
        self.timeouts = []

    def request(
        self, uri, method="GET", body=None, headers=None, redirections=5, connection_type=None
    ):
        import httplib2

        content = body.encode("utf-8") if isinstance(body, str) else body
        request = httpx.Request(method, uri, content=content, headers=headers or {})
        try:
            resp = self.google(request)
        except httpx.TimeoutException as e:
            raise TimeoutError(str(e)) from None
        except httpx.HTTPError as e:
            raise ConnectionRefusedError(str(e)) from None
        info = {k.lower(): v for k, v in resp.headers.items()}
        info["status"] = str(resp.status_code)
        out = httplib2.Response(info)
        out.reason = resp.reason_phrase
        return out, resp.content


def install_native(monkeypatch, google_fake: FakeGoogle) -> list:
    """Every `httplib2.Http` the native client builds (`google._http`, one per
    call and one per token refresh) answers from `google_fake`. Returns the
    list of timeouts the client asked for, in order."""
    timeouts: list = []

    def make(timeout):
        timeouts.append(timeout)
        return FakeHttp(google_fake, timeout)

    monkeypatch.setattr(google, "_http", make)
    return timeouts


def select_native(monkeypatch) -> None:
    monkeypatch.setattr(settings, "google_provider", "native")
    monkeypatch.setattr(settings, "google_client_id", CLIENT_ID)
    monkeypatch.setattr(settings, "google_client_secret", CLIENT_SECRET)
    monkeypatch.setattr(settings, "google_refresh_token", REFRESH_TOKEN)
    reset_credentials()


def reset_credentials() -> None:
    google._creds_cache.update(key=None, creds=None)


def body_json(request: httpx.Request):
    return json.loads(request.content) if request.content else None


# --- realistic Gmail messages -------------------------------------------------


def hdr(name: str, value: str) -> dict:
    return {"name": name, "value": value}


def multipart_message(mid: str = "18c0ffee00aa11bb", tid: str = "18c0ffee00aa0000") -> dict:
    """A newsletter-ish mail as `format=full` returns it: alternative text and
    HTML (the HTML in windows-1252 labelled iso-8859-1, as many senders do), a
    PDF by reference, an inline signature image with a Content-ID, an inline
    calendar invite, and a forwarded message whose own text must not win the
    body slots. Two Authentication-Results (Gmail's first), a DKIM signature
    and the RFC 8058 unsubscribe pair."""
    return {
        "id": mid,
        "threadId": tid,
        "labelIds": ["INBOX", "UNREAD", "CATEGORY_UPDATES"],
        "snippet": "Your November statement is ready",
        "historyId": "987654",
        "internalDate": "1763460000123",
        "sizeEstimate": 48213,
        "payload": {
            "partId": "",
            "mimeType": "multipart/mixed",
            "filename": "",
            "headers": [
                hdr("Delivered-To", "operator@example.com"),
                hdr(
                    "Authentication-Results",
                    "mx.google.com; dkim=pass header.i=@example.com; spf=pass",
                ),
                hdr(
                    "DKIM-Signature",
                    "v=1; a=rsa-sha256; d=example.com; s=s1; h=from:list-unsubscribe:list-unsubscribe-post",
                ),
                hdr("Authentication-Results", "relay.example.net; spf=pass"),
                hdr("From", "Jane Doe <jane.doe@example.com>"),
                hdr("To", "operator@example.com"),
                hdr("Cc", "John Rivers <john.rivers@example.com>"),
                hdr("Subject", "Your November statement"),
                hdr("Date", "Tue, 18 Nov 2025 10:00:00 +0000"),
                hdr(
                    "List-Unsubscribe",
                    "<https://example.com/unsub?u=1>, <mailto:unsub@example.com>",
                ),
                hdr("List-Unsubscribe-Post", "List-Unsubscribe=One-Click"),
                hdr("Message-ID", "<abc123@example.com>"),
            ],
            "body": {"size": 0},
            "parts": [
                {
                    "partId": "0",
                    "mimeType": "multipart/alternative",
                    "filename": "",
                    "headers": [hdr("Content-Type", 'multipart/alternative; boundary="b2"')],
                    "body": {"size": 0},
                    "parts": [
                        {
                            "partId": "0.0",
                            "mimeType": "text/plain",
                            "filename": "",
                            "headers": [hdr("Content-Type", 'text/plain; charset="UTF-8"')],
                            "body": {
                                "size": 31,
                                "data": b64url("Statement ready — €12.50 due.\r\n"),
                            },
                        },
                        {
                            "partId": "0.1",
                            "mimeType": "text/html",
                            "filename": "",
                            "headers": [hdr("Content-Type", "text/html; charset=iso-8859-1")],
                            "body": {
                                "size": 40,
                                "data": b64url(b"<p>Statement \x80 ready ~ caf\xe9</p>"),
                            },
                        },
                    ],
                },
                {
                    "partId": "1",
                    "mimeType": "application/pdf",
                    "filename": "statement.pdf",
                    "headers": [
                        hdr("Content-Type", 'application/pdf; name="statement.pdf"'),
                        hdr("Content-Disposition", 'attachment; filename="statement.pdf"'),
                        hdr("Content-ID", "<pdf1>"),
                    ],
                    "body": {"attachmentId": "ANGjdJ_pdf-1", "size": 52311},
                },
                {
                    "partId": "2",
                    "mimeType": "image/png",
                    "filename": "logo.png",
                    "headers": [hdr("Content-Type", "image/png"), hdr("Content-ID", "<logo>")],
                    "body": {"attachmentId": "ANGjdJ_png-2", "size": 2048},
                },
                {
                    "partId": "3",
                    "mimeType": "text/calendar",
                    "filename": "",
                    "headers": [
                        hdr("Content-Type", "text/calendar; charset=UTF-8; method=REQUEST")
                    ],
                    "body": {
                        "size": 60,
                        "data": b64url("BEGIN:VCALENDAR\r\nMETHOD:REQUEST\r\nEND:VCALENDAR\r\n"),
                    },
                },
                {
                    "partId": "4",
                    "mimeType": "message/rfc822",
                    "filename": "fwd.eml",
                    "headers": [hdr("Content-Type", "message/rfc822")],
                    "body": {"size": 0},
                    "parts": [
                        {
                            "partId": "4.0",
                            "mimeType": "text/plain",
                            "filename": "",
                            "headers": [hdr("Content-Type", "text/plain")],
                            "body": {"size": 9, "data": b64url("forwarded")},
                        }
                    ],
                },
            ],
        },
    }


def single_part_html(mid: str = "18c0ffee00bb22cc") -> dict:
    """A one-part HTML mail with no internalDate: the body sits on the payload
    itself and the date comes from the Date header."""
    return {
        "id": mid,
        "threadId": mid,
        "labelIds": ["INBOX"],
        "snippet": "Hi",
        "payload": {
            "partId": "",
            "mimeType": "text/html",
            "filename": "",
            "headers": [
                hdr("From", "jane.doe@example.com"),
                hdr("Subject", "Hi"),
                hdr("Date", "Wed, 19 Nov 2025 08:30:15 -0700"),
                hdr("Content-Type", "text/html; charset=utf-8"),
            ],
            "body": {"size": 11, "data": b64url("<b>Hi</b>\n")},
        },
    }


def event(eid: str, **kw) -> dict:
    base = {
        "kind": "calendar#event",
        "etag": '"3412"',
        "id": eid,
        "status": "confirmed",
        "htmlLink": f"https://www.google.com/calendar/event?eid={eid}",
        "created": "2025-11-01T10:00:00.000Z",
        "updated": "2025-11-02T10:00:00.000Z",
        "summary": f"Event {eid}",
        "creator": {"email": "operator@example.com", "self": True},
        "organizer": {"email": "operator@example.com", "self": True},
        "start": {"dateTime": "2025-11-20T09:00:00-07:00", "timeZone": "America/Denver"},
        "end": {"dateTime": "2025-11-20T09:30:00-07:00", "timeZone": "America/Denver"},
        "iCalUID": f"{eid}@google.com",
        "sequence": 0,
        "eventType": "default",
    }
    base.update(kw)
    return base


def events_page(items: list | None, **kw) -> dict:
    page = {
        "kind": "calendar#events",
        "etag": '"p1"',
        "summary": "operator@example.com",
        "updated": "2025-11-19T10:00:00.000Z",
        "timeZone": "America/Denver",
        "accessRole": "owner",
        "defaultReminders": [],
    }
    if items is not None:
        page["items"] = items
    page.update(kw)
    return page
