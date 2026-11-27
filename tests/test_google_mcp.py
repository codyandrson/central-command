"""The Google MCP client (DL-237), through the SAME façade functions the callers
use, against a local stub of Google's MCP wire protocol: stateless JSON-RPC
`tools/call` over HTTP POST, answered as JSON or as Server-Sent Events, with
`input_required` (form elicitation) and `isError` results. No request leaves
the process: every httpx client answers through `httpx.MockTransport`
(`google_fakes.install`), and the token refresh answers through
`google_fakes.FakeGoogle` at google-auth's own HTTP layer.

The stub follows Google's published reference, fetched 2026-11-26:
https://developers.google.com/workspace/guides/mcp-elicitation (headers,
`_meta`, `input_required`, the retry shape, `UserDeniedAction`),
https://developers.google.com/workspace/gmail/api/reference/mcp (the Gmail
tools: `search_threads` threads of up to 5 messages, `omittedContent`,
`get_message` formats) and
https://developers.google.com/workspace/calendar/api/v3/reference/mcp (the
Calendar tools: `list_events` answers `events`, not `items`).

Where the REST client (`integrations/google.py`) answers the same underlying
message or event, the two envelopes are compared (parity), the REST side
driven through `google_fakes.FakeHttp` as in `test_google_native.py`.
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest

# The `google` extra (DL-235): google-auth mints the token.
pytest.importorskip("google_auth_httplib2")

from central_command import __version__
from central_command.config import Settings, settings
from central_command.contract import dependencies as deps
from central_command.contract.failures import classify_failure, is_down
from central_command.heartbeat import probes
from central_command.integrations import (
    calendar_facade,
    email_facade,
    google,
    google_mcp,
    provider_extras,
)
from tests import google_fakes as gf
from tests.google_fakes import CAL, GMAIL, FakeGoogle

GMAIL_MCP = "https://gmailmcp.example.com/mcp/v1"
CAL_MCP = "https://calendarmcp.example.com/mcp/v1"


class FakeMcp:
    """Scripted MCP servers. `on(tool, *answers)`: each answer is a `result`
    dict (sent as JSON), `("sse", result)` (sent as an event stream), `(status,
    None)` (a bare HTTP status), `("rpc_error", code, message)`, or an httpx
    exception (a transport failure); the last answer repeats. Every request is
    recorded with its parsed body."""

    def __init__(self):
        self.routes: dict[str, list] = {}
        self.requests: list[httpx.Request] = []

    def on(self, tool: str, *answers) -> FakeMcp:
        self.routes[tool] = list(answers)
        return self

    def bodies(self, tool: str | None = None) -> list[dict]:
        out = [json.loads(r.content) for r in self.requests]
        return [b for b in out if tool is None or b["params"]["name"] == tool]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        assert url in (GMAIL_MCP, CAL_MCP), f"a request left the stub: {url}"
        body = json.loads(request.content)
        tool = body["params"]["name"]
        answers = self.routes.get(tool)
        if not answers:
            raise AssertionError(f"FakeMcp has no answer for {tool}")
        answer = answers.pop(0) if len(answers) > 1 else answers[0]
        if isinstance(answer, BaseException):
            raise answer
        if isinstance(answer, tuple) and answer[0] == "sse":
            msg = {"jsonrpc": "2.0", "id": body["id"], "result": answer[1]}
            text = f"event: message\r\ndata: {json.dumps(msg)}\r\n\r\n"
            return httpx.Response(200, text=text, headers={"content-type": "text/event-stream"})
        if isinstance(answer, tuple) and answer[0] == "rpc_error":
            msg = {
                "jsonrpc": "2.0",
                "id": body["id"],
                "error": {"code": answer[1], "message": answer[2]},
            }
            return httpx.Response(200, json=msg)
        if isinstance(answer, tuple) and len(answer) == 2 and isinstance(answer[1], dict):
            return httpx.Response(answer[0], json=answer[1])
        if isinstance(answer, tuple):
            return httpx.Response(answer[0])
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": answer})


def ok(data: dict) -> dict:
    return {"resultType": "complete", "structuredContent": data, "content": []}


def tool_error(text: str, reason: str | None = None, code: int | None = None) -> dict:
    res: dict = {
        "resultType": "complete",
        "isError": True,
        "content": [{"type": "text", "text": text}],
    }
    if reason or code:
        err: dict = {"code": code or 400, "message": text}
        if reason:
            err["details"] = [
                {"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": reason}
            ]
        res["_meta"] = {"google.com/error": err}
    return res


INPUT_REQUIRED = {
    "resultType": "input_required",
    "inputRequests": {
        "approval_choice": {
            "method": "elicitation/create",
            "params": {
                "mode": "form",
                "message": "**Subject: secret plans**",
                "requestedSchema": {
                    "type": "object",
                    "required": ["approval_choice"],
                    "properties": {
                        "approval_choice": {
                            "type": "string",
                            "title": "Allow Central Command to use the tool?",
                            "enum": ["ALLOW_ONCE", "ALLOW_TOOL_ALWAYS", "DENY_ONCE"],
                            "enumNames": ["Allow once", "Always allow this tool", "Don't allow"],
                        }
                    },
                },
            },
        }
    },
    "requestState": '{"elicitationId":"e1","requestBinding":"b1"}',
}


@pytest.fixture
def mcp(monkeypatch):
    """CC_GOOGLE_PROVIDER=mcp with fake credentials, both MCP endpoints on the
    stub, and the token endpoint on FakeGoogle. Yields (FakeMcp, FakeGoogle)."""
    gf.select_native(monkeypatch)
    monkeypatch.setattr(settings, "google_provider", "mcp")
    monkeypatch.setattr(settings, "google_mcp_gmail_url", GMAIL_MCP)
    monkeypatch.setattr(settings, "google_mcp_calendar_url", CAL_MCP)
    token = FakeGoogle()
    gf.install_native(monkeypatch, token)
    fake = FakeMcp()
    gf.install(monkeypatch, fake)
    google_mcp._EXPANDED.clear()
    google_mcp._CALLS.clear()
    yield fake, token
    gf.reset_credentials()


# --- the wire -----------------------------------------------------------------


async def test_every_call_sends_the_headers_and_meta_google_requires(mcp):
    fake, _ = mcp
    fake.on("search_threads", ok({}))
    await email_facade.list_refs("in:inbox")
    (req,) = fake.requests
    assert req.method == "POST" and str(req.url) == GMAIL_MCP
    h = req.headers
    assert h["authorization"] == "Bearer ya29.fake-access-1"
    assert h["content-type"] == "application/json"
    assert h["accept"] == "application/json, text/event-stream"
    assert h["mcp-protocol-version"] == "2026-07-28"
    assert h["mcp-method"] == "tools/call" and h["mcp-name"] == "search_threads"
    body = json.loads(req.content)
    assert body["jsonrpc"] == "2.0" and body["method"] == "tools/call"
    assert body["params"]["name"] == "search_threads"
    assert body["params"]["arguments"] == {"query": "in:inbox", "pageSize": 50}
    assert body["params"]["_meta"] == {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientInfo": {"name": "Central Command", "version": __version__},
        "io.modelcontextprotocol/clientCapabilities": {"elicitation": {"form": {}}},
    }
    assert "requestState" not in body["params"] and "inputResponses" not in body["params"]


@pytest.mark.parametrize("wire", ["json", "sse"])
async def test_a_json_and_an_event_stream_answer_read_the_same(mcp, wire):
    fake, _ = mcp
    data = {
        "threads": [{"id": "t1", "messageCount": 1, "messages": [{"id": "m1", "threadId": "t1"}]}]
    }
    fake.on("search_threads", ok(data) if wire == "json" else ("sse", ok(data)))
    assert await email_facade.list_refs("q") == [{"uuid": "m1", "conversation_id": "t1"}]


def test_an_event_stream_with_several_events_answers_with_the_json_rpc_message():
    text = (
        ": keep-alive\n\n"
        'event: message\ndata: {"jsonrpc":"2.0","method":"notifications/progress"}\n\n'
        'data: {"jsonrpc":"2.0","id":1,\ndata: "result":{"structuredContent":{"a":1}}}\n\n'
    )
    resp = httpx.Response(200, text=text, headers={"content-type": "text/event-stream"})
    assert google_mcp.parse_answer(resp)["result"]["structuredContent"] == {"a": 1}


def test_the_default_endpoints_are_googles_and_the_probe_pins_them():
    fresh = Settings(_env_file=None)
    assert fresh.google_mcp_gmail_url == "https://gmailmcp.googleapis.com/mcp/v1"
    assert fresh.google_mcp_calendar_url == "https://calendarmcp.googleapis.com/mcp/v1"
    assert probes.GOOGLE_MCP_HOSTS == (
        "https://oauth2.googleapis.com",
        probes._root(fresh.google_mcp_gmail_url),
        probes._root(fresh.google_mcp_calendar_url),
    )
    assert google.TOKEN_URI.startswith(probes.GOOGLE_MCP_HOSTS[0] + "/")


# --- mail: list ---------------------------------------------------------------


async def test_list_follows_pages_reads_long_threads_and_dedupes(mcp):
    fake, _ = mcp
    long_thread = {
        "id": "t2",
        "messageCount": 7,
        "messages": [{"id": f"m2{i}", "threadId": "t2"} for i in range(5)],
    }
    fake.on(
        "search_threads",
        ok(
            {
                "threads": [
                    {
                        "id": "t1",
                        "messageCount": 2,
                        "messages": [
                            {"id": "m11", "threadId": "t1"},
                            {"id": "m12", "threadId": "t1"},
                        ],
                    }
                ],
                "nextPageToken": "p2",
            }
        ),
        ok(
            {
                "threads": [
                    long_thread,
                    {
                        "id": "t1",
                        "messageCount": 2,
                        "messages": [
                            {"id": "m11", "threadId": "t1"},
                            {"id": "m12", "threadId": "t1"},
                        ],
                    },
                ]
            }
        ),
    )
    fake.on(
        "get_thread",
        ok({"id": "t2", "messages": [{"id": f"m2{i}", "threadId": "t2"} for i in range(7)]}),
    )
    refs = await email_facade.list_refs("in:inbox newer_than:1d")
    assert refs == [
        {"uuid": "m11", "conversation_id": "t1"},
        {"uuid": "m12", "conversation_id": "t1"},
        *({"uuid": f"m2{i}", "conversation_id": "t2"} for i in range(7)),
    ]
    searches = fake.bodies("search_threads")
    assert [b["params"]["arguments"].get("pageToken") for b in searches] == [None, "p2"]
    assert fake.bodies("get_thread")[0]["params"]["arguments"] == {
        "threadId": "t2",
        "messageFormat": "METADATA_ONLY",
    }


async def test_a_thread_google_refuses_to_expand_lists_what_the_search_showed(mcp):
    """A block by Gmail never stops the poll (the operator, 2026-11-27): the
    refused thread's shown messages are listed and the other thread is whole."""
    fake, _ = mcp
    fake.on(
        "search_threads",
        ok(
            {
                "threads": [
                    {
                        "id": "t2",
                        "messageCount": 7,
                        "messages": [{"id": f"m2{i}", "threadId": "t2"} for i in range(5)],
                    },
                    {"id": "t1", "messageCount": 1, "messages": [{"id": "m11", "threadId": "t1"}]},
                ]
            }
        ),
    )
    fake.on("get_thread", tool_error("blocked", reason="AdminPolicyBlock", code=403))
    refs = await email_facade.list_refs("newer_than:1d")
    assert [r["uuid"] for r in refs] == [*(f"m2{i}" for i in range(5)), "m11"]


async def test_a_long_thread_is_read_once_until_it_changes(mcp):
    """The feed lists the same day every minute against a DAILY request quota
    (429 "Daily Free Requests", live 2026-11-27): an unchanged long thread costs
    no second `get_thread`; a new message or a changed shown message does."""
    fake, _ = mcp

    def page(count, history="h1"):
        shown = [{"id": f"m{i}", "threadId": "t2", "historyId": "h0"} for i in range(4)]
        shown.append({"id": "m4", "threadId": "t2", "historyId": history})
        return ok({"threads": [{"id": "t2", "messageCount": count, "messages": shown}]})

    def whole(count):
        return ok(
            {"id": "t2", "messages": [{"id": f"m{i}", "threadId": "t2"} for i in range(count)]}
        )

    fake.on("search_threads", page(7), page(7), page(8), page(8, "h2"))
    fake.on("get_thread", whole(7), whole(8), whole(8))
    first = await email_facade.list_refs("newer_than:1d")
    again = await email_facade.list_refs("newer_than:1d")
    assert first == again and len(first) == 7
    assert len(fake.bodies("get_thread")) == 1
    assert len(await email_facade.list_refs("newer_than:1d")) == 8  # a new message
    assert len(fake.bodies("get_thread")) == 2
    await email_facade.list_refs("newer_than:1d")  # a shown message changed
    assert len(fake.bodies("get_thread")) == 3


async def test_a_rate_limit_says_which_quota_and_what_was_spent(mcp):
    fake, _ = mcp
    said = "Quota exceeded for quota metric 'Daily Free Requests' and limit 'Requests per day' of service 'gmailmcp.googleapis.com' for consumer 'project_number:123456'."
    body = {
        "id": 1,
        "jsonrpc": "2.0",
        "result": {"content": [{"type": "text", "text": said}], "isError": True},
    }
    fake.on("search_threads", (429, body))
    with pytest.raises(email_facade.EmailFacadeError) as e:
        await email_facade.list_refs("in:inbox")
    text = str(e.value)
    assert e.value.status_code == 503
    assert "provider rate limit hit - retry later" in text
    assert "Daily Free Requests" in text and "project_number" not in text
    assert "search_threads 1" in text


@pytest.mark.parametrize(
    ("query", "kept"),
    [
        ("in:inbox newer_than:1d", ["m1", "m3"]),
        ("IN:INBOX", ["m1", "m3"]),
        ("-in:inbox newer_than:1d", ["m1", "m2", "m3"]),
        ("{in:inbox in:sent}", ["m1", "m2", "m3"]),
        ("in:inbox OR in:sent", ["m1", "m2", "m3"]),
        ("newer_than:1d", ["m1", "m2", "m3"]),
    ],
)
async def test_an_inbox_query_leaves_out_a_threads_messages_that_are_not_in_the_inbox(
    mcp, query, kept
):
    """Google searches by thread: the operator's own reply (SENT, no INBOX)
    comes back with the thread and must not be enrolled by the inbox feed. A
    message with no label list is kept (m3): never skip."""
    fake, _ = mcp
    fake.on(
        "search_threads",
        ok(
            {
                "threads": [
                    {
                        "id": "t1",
                        "messageCount": 3,
                        "messages": [
                            {"id": "m1", "threadId": "t1", "labelIds": ["INBOX", "UNREAD"]},
                            {"id": "m2", "threadId": "t1", "labelIds": ["SENT"]},
                            {"id": "m3", "threadId": "t1"},
                        ],
                    }
                ]
            }
        ),
    )
    refs = await email_facade.list_refs(query)
    assert [r["uuid"] for r in refs] == kept


async def test_an_empty_object_is_zero_matches(mcp):
    fake, _ = mcp
    fake.on("search_threads", ok({}))
    assert await email_facade.list_refs("q") == []


async def test_a_thread_google_filters_does_not_stop_the_list_and_is_logged(mcp, caplog):
    """Anyone can send a mail Google classes as suspicious. Refusing the list
    for it would let one message stop every poll; the rest is listed and the
    count is logged at warning level."""
    fake, _ = mcp
    fake.on(
        "search_threads",
        ok(
            {
                "threads": [
                    {"id": "t1", "messageCount": 1, "messages": [{"id": "m1", "threadId": "t1"}]}
                ],
                "omittedContent": {
                    "omittedReasonCounts": [{"reason": "SUSPICIOUS_SENDER", "omittedCount": 2}]
                },
            }
        ),
    )
    with caplog.at_level("WARNING"):
        refs = await email_facade.list_refs("q")
    assert refs == [{"uuid": "m1", "conversation_id": "t1"}]
    assert "SUSPICIOUS_SENDER 2" in caplog.text


async def test_data_protection_policy_names_the_grant_command(mcp):
    fake, _ = mcp
    fake.on(
        "search_threads",
        ok(
            {
                "omittedContent": {
                    "omittedReasonCounts": [{"reason": "DATA_PROTECTION_POLICY", "omittedCount": 3}]
                }
            }
        ),
    )
    with pytest.raises(email_facade.EmailFacadeError, match="google_mcp_grant") as e:
        await email_facade.list_refs("q")
    assert e.value.status_code == 422


async def test_more_pages_than_the_cap_is_the_page_cap_refusal(mcp, monkeypatch):
    fake, _ = mcp
    monkeypatch.setattr(google_mcp, "LIST_MAX_PAGES", 2)
    fake.on("search_threads", ok({"threads": [], "nextPageToken": "more"}))
    with pytest.raises(email_facade.EmailFacadeError, match="pagination cap hit") as e:
        await email_facade.list_refs("q")
    assert e.value.status_code == 422 and len(fake.requests) == 2


async def test_an_empty_query_is_refused_before_any_request(mcp):
    fake, _ = mcp
    with pytest.raises(email_facade.EmailFacadeError, match="requires scope_query") as e:
        await email_facade.list_refs("  ")
    assert e.value.status_code == 422 and fake.requests == []


# --- mail: message ------------------------------------------------------------

_HTML_B64 = base64.b64encode(b"<p>Statement \x80 ready ~ caf\xe9</p>").decode()
RAW_MIME = (
    "Delivered-To: operator@example.com\r\n"
    "Authentication-Results: mx.google.com; dkim=pass header.i=@example.com; spf=pass\r\n"
    "DKIM-Signature: v=1; a=rsa-sha256; d=example.com; s=s1;\r\n"
    " h=from:list-unsubscribe:list-unsubscribe-post\r\n"
    "Authentication-Results: relay.example.net; spf=pass\r\n"
    "From: Jane Doe <jane.doe@example.com>\r\n"
    "To: operator@example.com\r\n"
    "Cc: John Rivers <john.rivers@example.com>\r\n"
    "Subject: =?UTF-8?Q?Caf=C3=A9_statement?=\r\n"
    "Date: Tue, 18 Nov 2025 10:00:00 +0000\r\n"
    "List-Unsubscribe: <https://example.com/unsub?u=1>, <mailto:unsub@example.com>\r\n"
    "List-Unsubscribe-Post: List-Unsubscribe=One-Click\r\n"
    "Message-ID: <abc123@example.com>\r\n"
    "MIME-Version: 1.0\r\n"
    'Content-Type: multipart/alternative; boundary="b2"\r\n'
    "\r\n"
    "--b2\r\n"
    'Content-Type: text/plain; charset="UTF-8"\r\n'
    "Content-Transfer-Encoding: quoted-printable\r\n"
    "\r\n"
    "Statement ready =E2=80=94 =E2=82=AC12.50 due.\r\n"
    "--b2\r\n"
    "Content-Type: text/html; charset=iso-8859-1\r\n"
    "Content-Transfer-Encoding: base64\r\n"
    "\r\n" + _HTML_B64 + "\r\n"
    "--b2--\r\n"
).encode()


def _hdrs(*pairs):
    return [gf.hdr(n, v) for n, v in pairs]


# The same message as Gmail's REST `format=full` returns it, written out by
# hand from the reference (headers unfolded and decoded, bodies the
# transfer-decoded bytes in base64url).
REST_FULL = {
    "id": "18c0ffee00aa11bb",
    "threadId": "18c0ffee00aa0000",
    "labelIds": ["INBOX"],
    "snippet": "Statement ready",
    "internalDate": "1763460000123",
    "payload": {
        "partId": "",
        "mimeType": "multipart/alternative",
        "filename": "",
        "headers": _hdrs(
            ("Delivered-To", "operator@example.com"),
            ("Authentication-Results", "mx.google.com; dkim=pass header.i=@example.com; spf=pass"),
            (
                "DKIM-Signature",
                "v=1; a=rsa-sha256; d=example.com; s=s1; h=from:list-unsubscribe:list-unsubscribe-post",
            ),
            ("Authentication-Results", "relay.example.net; spf=pass"),
            ("From", "Jane Doe <jane.doe@example.com>"),
            ("To", "operator@example.com"),
            ("Cc", "John Rivers <john.rivers@example.com>"),
            ("Subject", "Café statement"),
            ("Date", "Tue, 18 Nov 2025 10:00:00 +0000"),
            ("List-Unsubscribe", "<https://example.com/unsub?u=1>, <mailto:unsub@example.com>"),
            ("List-Unsubscribe-Post", "List-Unsubscribe=One-Click"),
            ("Message-ID", "<abc123@example.com>"),
            ("MIME-Version", "1.0"),
            ("Content-Type", 'multipart/alternative; boundary="b2"'),
        ),
        "body": {"size": 0},
        "parts": [
            {
                "partId": "0",
                "mimeType": "text/plain",
                "filename": "",
                "headers": _hdrs(
                    ("Content-Type", 'text/plain; charset="UTF-8"'),
                    ("Content-Transfer-Encoding", "quoted-printable"),
                ),
                "body": {"size": 33, "data": gf.b64url("Statement ready — €12.50 due.")},
            },
            {
                "partId": "1",
                "mimeType": "text/html",
                "filename": "",
                "headers": _hdrs(
                    ("Content-Type", "text/html; charset=iso-8859-1"),
                    ("Content-Transfer-Encoding", "base64"),
                ),
                "body": {"size": 31, "data": gf.b64url(b"<p>Statement \x80 ready ~ caf\xe9</p>")},
            },
        ],
    },
}


async def _rest_message(monkeypatch, gmail_message: dict) -> dict:
    """What the REST client answers for the same message."""
    monkeypatch.setattr(settings, "google_provider", "native")
    gf.reset_credentials()
    gf.install_native(
        monkeypatch,
        FakeGoogle().on("GET", f"{GMAIL}/messages/{gmail_message['id']}", (200, gmail_message)),
    )
    try:
        return await email_facade.get_message(gmail_message["id"])
    finally:
        monkeypatch.setattr(settings, "google_provider", "mcp")


async def test_a_raw_message_normalises_exactly_as_the_rest_client_does(mcp, monkeypatch):
    fake, _ = mcp
    fake.on(
        "get_message",
        ok(
            {
                "id": REST_FULL["id"],
                "threadId": REST_FULL["threadId"],
                "internalDate": REST_FULL["internalDate"],
                "snippet": REST_FULL["snippet"],
                "raw": gf.b64url(RAW_MIME),
            }
        ),
    )
    got = await email_facade.get_message(REST_FULL["id"])
    assert fake.bodies("get_message")[0]["params"]["arguments"] == {
        "messageId": REST_FULL["id"],
        "messageFormat": "RAW",
    }
    assert len(fake.requests) == 1
    want = await _rest_message(monkeypatch, REST_FULL)
    assert got == want
    assert got["list_unsubscribe_post"] == "List-Unsubscribe=One-Click"
    assert got["body_html"] == "<p>Statement € ready ~ café</p>"


async def test_a_raw_answer_without_ids_and_date_reads_them_from_a_minimal_read(mcp):
    fake, _ = mcp
    fake.on(
        "get_message",
        ok({"raw": gf.b64url(RAW_MIME)}),
        ok(
            {
                "id": "m1",
                "threadId": "t1",
                "snippet": "Statement ready",
                "date": "2025-11-18T10:00:00Z",
            }
        ),
    )
    got = await email_facade.get_message("m1")
    assert [b["params"]["arguments"]["messageFormat"] for b in fake.bodies()] == ["RAW", "MINIMAL"]
    assert got["uuid"] == "m1" and got["conversation_id"] == "t1"
    assert got["date"] == "2025-11-18T10:00:00.000Z" and got["snippet"] == "Statement ready"


async def test_raw_refused_for_attachments_falls_back_to_full_content(mcp):
    fake, _ = mcp
    fake.on(
        "get_message",
        tool_error("RAW format is not supported for messages with attachments or non-body parts"),
        ok(
            {
                "id": "m1",
                "threadId": "t1",
                "snippet": "See attached",
                "subject": "Invoice",
                "sender": "jane.doe@example.com",
                "toRecipients": ["operator@example.com", "john.rivers@example.com"],
                "ccRecipients": [],
                "internalDate": "1763460000123",
                "addressDisplayNames": [
                    {"emailAddress": "jane.doe@example.com", "displayName": "Doe, Jane"},
                    {"emailAddress": "john.rivers@example.com", "displayName": "John Rivers"},
                ],
                "plaintextBody": "See attached.",
                "htmlBody": "<p>See attached.</p>",
                "attachments": [
                    {
                        "id": "ANGjdJ_pdf-1",
                        "mimeType": "application/pdf",
                        "filename": "invoice.pdf",
                        "partId": "1",
                    }
                ],
            }
        ),
    )
    got = await email_facade.get_message("m1")
    assert [b["params"]["arguments"]["messageFormat"] for b in fake.bodies()] == [
        "RAW",
        "FULL_CONTENT",
    ]
    assert got == {
        "uuid": "m1",
        "conversation_id": "t1",
        "from": '"Doe, Jane" <jane.doe@example.com>',
        "to": "operator@example.com, John Rivers <john.rivers@example.com>",
        "cc": "",
        "subject": "Invoice",
        "list_unsubscribe": "",
        "list_unsubscribe_post": "",
        "authentication_results": [],
        "dkim_signatures": [],
        "date": "2025-11-18T10:00:00.123Z",
        "snippet": "See attached",
        "body_text": "See attached.",
        "body_html": "<p>See attached.</p>",
        "body_truncated": False,
        "has_attachments": True,
        "attachments": [
            {
                "filename": "invoice.pdf",
                "mime_type": "application/pdf",
                "size": None,
                "attachment_id": "ANGjdJ_pdf-1",
                "is_inline": False,
            }
        ],
    }
    # The record has the same keys the REST normaliser writes.
    assert set(got) == set(google.normalize_message(REST_FULL))


async def test_an_unknown_message_is_an_outage_as_the_rest_client_answers_it(mcp):
    fake, _ = mcp
    fake.on("get_message", tool_error("Requested entity was not found.", code=404))
    with pytest.raises(email_facade.EmailFacadeError) as e:
        await email_facade.get_message("m1")
    assert e.value.status_code == 503 and is_down(e.value)


# --- approvals ----------------------------------------------------------------


async def test_input_required_is_refused_naming_the_grant_command_and_never_answered(mcp):
    fake, _ = mcp
    fake.on("get_message", INPUT_REQUIRED, ok({"raw": "x"}))
    with pytest.raises(email_facade.EmailFacadeError) as e:
        await email_facade.get_message("m1")
    assert e.value.status_code == 422
    assert "python -m central_command.google_mcp_grant" in str(e.value) and "get_message" in str(
        e.value
    )
    assert classify_failure(e.value) == "semantic" and not is_down(e.value)
    assert len(fake.requests) == 1, "an approval question is never answered by the request path"
    assert "secret plans" not in str(e.value)


async def test_a_calendar_write_google_wants_approved_is_a_no_not_an_outage(mcp):
    fake, _ = mcp
    fake.on("create_event", INPUT_REQUIRED)
    with pytest.raises(calendar_facade.CalendarFacadeError, match="google_mcp_grant") as e:
        await calendar_facade.create_event("Sync", "2025-11-20T09:00:00Z", "2025-11-20T09:30:00Z")
    assert "refused" in str(e.value) and classify_failure(e.value) == "semantic"
    assert not is_down(e.value) and len(fake.requests) == 1


@pytest.mark.parametrize("reason", ["UserDeniedAction", "AdminPolicyBlock"])
async def test_a_denied_or_blocked_call_is_a_refusal(mcp, reason):
    fake, _ = mcp
    fake.on("mark_message_spam", tool_error("Action canceled.", reason=reason))
    with pytest.raises(email_facade.EmailFacadeError) as e:
        await email_facade.report_spam("m1")
    assert e.value.status_code == 422 and not is_down(e.value)


async def test_a_missing_capability_error_is_a_refusal(mcp):
    fake, _ = mcp
    fake.on("search_threads", ("rpc_error", -32021, "This action requires user confirmation"))
    with pytest.raises(email_facade.EmailFacadeError) as e:
        await email_facade.list_refs("q")
    assert e.value.status_code == 422


# --- mail: the modes with no MCP equivalent, and the write --------------------


async def test_attachment_bytes_are_refused_without_a_request(mcp):
    fake, _ = mcp
    with pytest.raises(email_facade.EmailFacadeError, match="no attachment tool") as e:
        await email_facade.get_attachment("m1", "ANGjdJ_pdf-1")
    assert e.value.status_code == 422 and not is_down(e.value) and fake.requests == []


async def test_send_is_refused_on_the_mcp_path_and_reaches_no_server(mcp):
    fake, _ = mcp
    with pytest.raises(email_facade.EmailFacadeError, match="no send mode"):
        await email_facade.send(["jane.doe@example.com"], "Hi", "Hello")
    assert fake.requests == []


async def test_report_spam_marks_the_message_and_answers_the_envelope(mcp):
    fake, _ = mcp
    fake.on("mark_message_spam", ok({}))
    out = await email_facade.report_spam("m1")
    assert out == {
        "kind": "email",
        "mode": "report_spam",
        "source_ref": {"uuid": "m1"},
        "ok": True,
        "label_ids": [],
    }
    assert fake.bodies()[0]["params"]["arguments"] == {"messageId": "m1"}


# --- mail: the draft (DL-238) ---------------------------------------------------

DRAFT = {
    "id": "r-8821",
    "messageId": "18c0ffee",
    "threadId": "18c0ffee00aa0000",
    "viewUrl": "https://mail.google.com/mail/u/0/#drafts/18c0ffee",
    "subject": "Thursday",
}


async def test_a_draft_is_created_with_plain_text_arguments_only_and_never_sent(mcp):
    fake, _ = mcp
    fake.on("create_draft", ok(DRAFT))
    out = await email_facade.draft(
        to=["jane.doe@example.com"],
        subject="Thursday",
        body="Thursday works for me.",
        cc=["john.rivers@example.com"],
        bcc=["dana.doe@example.com"],
        reply_to="18c0ffee00aa1111",
    )
    assert out == {
        "kind": "email",
        "mode": "draft",
        "ok": True,
        "draft_id": "r-8821",
        "thread_id": "18c0ffee00aa0000",
        "view_url": "https://mail.google.com/mail/u/0/#drafts/18c0ffee",
    }
    [body] = fake.bodies()
    assert body["params"]["name"] == "create_draft"
    # Exactly the pinned fields: no htmlBody, no attachments, no includeSignature
    # (Google's default for the signature is left alone), and the reply target is
    # Google's `replyToMessageId`.
    assert body["params"]["arguments"] == {
        "subject": "Thursday",
        "body": "Thursday works for me.",
        "to": ["jane.doe@example.com"],
        "cc": ["john.rivers@example.com"],
        "bcc": ["dana.doe@example.com"],
        "replyToMessageId": "18c0ffee00aa1111",
    }
    assert fake.requests[0].headers["mcp-name"] == "create_draft"
    assert str(fake.requests[0].url) == GMAIL_MCP


async def test_a_reply_with_no_recipient_omits_to_and_a_new_mail_omits_the_reply_target(mcp):
    fake, _ = mcp
    fake.on("create_draft", ok(DRAFT))
    await email_facade.draft(to=[], subject="Re: Thursday", body="Yes.", reply_to="m1")
    await email_facade.draft(to=["jane.doe@example.com"], subject="Hi", body="Hello.")
    first, second = (b["params"]["arguments"] for b in fake.bodies())
    assert first == {"subject": "Re: Thursday", "body": "Yes.", "replyToMessageId": "m1"}
    assert second == {"subject": "Hi", "body": "Hello.", "to": ["jane.doe@example.com"]}


async def test_no_send_tool_is_ever_called_and_the_draft_touches_one_tool_only(mcp):
    fake, _ = mcp
    fake.on("create_draft", ok(DRAFT))
    await email_facade.draft(to=["jane.doe@example.com"], subject="Hi", body="Hello.")
    names = {b["params"]["name"] for b in fake.bodies()}
    assert names == {"create_draft"}
    assert not names & {"send_message", "reply"}


def test_no_code_path_names_a_send_tool():
    """`send_message` and `reply` send mail immediately. They exist in Google's
    schema and are referenced in prose only: no string in the client or the grant
    command is one of them."""
    import ast
    import inspect

    from central_command import google_mcp_grant

    for module in (google_mcp, google_mcp_grant):
        tree = ast.parse(inspect.getsource(module))
        names = {
            n.value
            for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
        }
        assert not names & {"send_message", "reply", "sendMessage"}, module.__name__


async def test_a_draft_google_wants_approved_is_refused_naming_the_grant_command(mcp):
    fake, _ = mcp
    fake.on("create_draft", INPUT_REQUIRED)
    with pytest.raises(email_facade.EmailFacadeError) as e:
        await email_facade.draft(to=["jane.doe@example.com"], subject="Hi", body="Hello.")
    assert e.value.status_code == 422 and not is_down(e.value)
    assert "create_draft" in str(e.value) and "google_mcp_grant" in str(e.value)
    assert classify_failure(e.value) == "semantic"
    assert len(fake.requests) == 1, "an approval is never answered, so no second request"
    assert "inputResponses" not in fake.bodies()[0]["params"]


async def test_a_draft_answered_without_an_id_is_a_refusal_not_a_retry(mcp):
    fake, _ = mcp
    fake.on("create_draft", ok({"threadId": "t1"}))
    with pytest.raises(email_facade.EmailFacadeError, match="Drafts folder") as e:
        await email_facade.draft(to=["jane.doe@example.com"], subject="Hi", body="Hello.")
    assert e.value.status_code == 422 and classify_failure(e.value) == "semantic"
    assert len(fake.requests) == 1


async def test_a_draft_a_tool_error_or_a_transport_failure_is_the_outage_class(mcp):
    fake, _ = mcp
    fake.on("create_draft", tool_error("Internal error.", code=500))
    with pytest.raises(email_facade.EmailFacadeError) as down:
        await email_facade.draft(to=["jane.doe@example.com"], subject="Hi", body="Hello.")
    fake.on("create_draft", httpx.ConnectError("boom"))
    with pytest.raises(email_facade.EmailFacadeError) as lost:
        await email_facade.draft(to=["jane.doe@example.com"], subject="Hi", body="Hello.")
    for e in (down.value, lost.value):
        assert e.status_code == 503 and is_down(e)


async def test_a_denied_draft_is_a_refusal(mcp):
    fake, _ = mcp
    fake.on("create_draft", tool_error("The user declined the confirmation request."))
    with pytest.raises(email_facade.EmailFacadeError) as e:
        await email_facade.draft(to=["jane.doe@example.com"], subject="Hi", body="Hello.")
    assert e.value.status_code == 422 and not is_down(e.value)


async def test_a_create_retried_after_a_lost_answer_can_land_twice_and_nothing_looks_for_the_first(
    mcp,
):
    """The accepted gap (DL-238), the draft's twin of `create_event` (DL-237):
    Google's `create_draft` takes no client id, so a create whose answer was lost
    is created again by the retry. Nothing reads the Drafts folder first: that
    would be the similarity guess DL-219 forbids."""
    fake, _ = mcp
    fake.on("create_draft", httpx.ReadTimeout("lost"), ok(DRAFT))
    with pytest.raises(email_facade.EmailFacadeError) as first:
        await email_facade.draft(to=["jane.doe@example.com"], subject="Hi", body="Hello.")
    assert is_down(first.value)
    out = await email_facade.draft(to=["jane.doe@example.com"], subject="Hi", body="Hello.")
    assert out["draft_id"] == "r-8821"
    assert [b["params"]["name"] for b in fake.bodies()] == ["create_draft", "create_draft"]
    args = [b["params"]["arguments"] for b in fake.bodies()]
    assert args[0] == args[1], "no client id or marker distinguishes the two requests"


@pytest.mark.parametrize(
    "draft",
    [
        {"subject": "Hi", "body": "Hello."},
        {"to": ["Jane <jane.doe@example.com>"], "subject": "Hi", "body": "Hello."},
        {"to": ["jane.doe@example.com"], "subject": "Hi", "body": "Hello.", "htmlBody": "<b>x</b>"},
        {"to": ["jane.doe@example.com"], "subject": "Hi", "body": "Hello.", "attachments": []},
        {"to": ["jane.doe@example.com"], "subject": "Hi", "body": "Hello.", "reply_to": "a b"},
    ],
)
async def test_a_draft_the_shared_rules_refuse_sends_no_request(mcp, draft):
    from central_command.integrations import google_mcp

    fake, _ = mcp
    with pytest.raises(google_mcp.GoogleError) as e:
        await google_mcp.mail_request({"mode": "draft", "draft": draft})
    assert e.value.status_code == 422 and fake.requests == []


@pytest.mark.parametrize("provider", ["n8n", "native"])
async def test_a_draft_is_refused_clearly_on_the_other_google_providers(mcp, monkeypatch, provider):
    fake, _ = mcp
    monkeypatch.setattr(settings, "google_provider", provider)
    with pytest.raises(email_facade.EmailFacadeError, match="CC_GOOGLE_PROVIDER=mcp") as e:
        await email_facade.draft(to=["jane.doe@example.com"], subject="Hi", body="Hello.")
    assert classify_failure(e.value) == "semantic" and not is_down(e.value)
    assert fake.requests == []


async def test_a_draft_is_refused_on_exchange_even_when_mcp_is_selected(mcp, monkeypatch):
    fake, _ = mcp
    monkeypatch.setattr(settings, "exchange_url", "https://mail.corp.example/ews/exchange.asmx")
    monkeypatch.setattr(settings, "exchange_username", "operator@corp.example")
    monkeypatch.setattr(settings, "exchange_password", "secret")
    with pytest.raises(email_facade.EmailFacadeError, match="CC_GOOGLE_PROVIDER=mcp"):
        await email_facade.draft(to=["jane.doe@example.com"], subject="Hi", body="Hello.")
    assert fake.requests == []


async def test_the_rest_client_has_no_draft_mode(mcp, monkeypatch):
    """google.py mirrors the shipped workflow, which has no draft mode, and its
    validator must keep refusing one: the draft is served by the MCP client only."""
    with pytest.raises(google._MailFail, match="bad or missing mode 'draft'"):
        google._validate_mail({"mode": "draft"})


# --- the token and the outage class --------------------------------------------


async def test_a_revoked_refresh_token_is_the_re_consent_message(mcp):
    fake, token = mcp
    token.token(
        (400, {"error": "invalid_grant", "error_description": "Token has been expired or revoked."})
    )
    with pytest.raises(email_facade.EmailFacadeError) as mail:
        await email_facade.list_refs("q")
    with pytest.raises(calendar_facade.CalendarFacadeError) as cal:
        await calendar_facade.delete_event("ev1")
    assert mail.value.status_code == 503 and cal.value.status_code == 500
    for e in (mail.value, cal.value):
        assert "invalid_grant" in str(e) and "google_consent" in str(e)
        assert gf.REFRESH_TOKEN not in str(e) and gf.CLIENT_SECRET not in str(e)
        assert is_down(e)
    assert fake.requests == [], "no MCP call without a token"


async def test_a_401_refreshes_once_and_resends_once(mcp):
    fake, _ = mcp
    fake.on("search_threads", (401, None), ok({}))
    assert await email_facade.list_refs("q") == []
    assert [r.headers["authorization"] for r in fake.requests] == [
        "Bearer ya29.fake-access-1",
        "Bearer ya29.fake-access-2",
    ]


async def test_a_401_after_the_refresh_is_the_re_consent_message_and_parks(mcp):
    fake, _ = mcp
    fake.on("search_threads", (401, None))
    with pytest.raises(email_facade.EmailFacadeError, match="google_consent") as e:
        await email_facade.list_refs("q")
    assert e.value.status_code == 503 and len(fake.requests) == 2 and is_down(e.value)


@pytest.mark.parametrize(
    "answer", [(500, None), (503, None), (429, None), (403, None), httpx.ConnectError("refused")]
)
async def test_every_other_failure_is_the_outage_class(mcp, answer):
    fake, _ = mcp
    fake.on("search_threads", answer)
    fake.on("list_events", answer)
    with pytest.raises(email_facade.EmailFacadeError) as mail:
        await email_facade.list_refs("q")
    with pytest.raises(calendar_facade.CalendarFacadeError) as cal:
        await calendar_facade.list_events("2025-11-20T00:00:00Z", "2025-11-21T00:00:00Z")
    assert mail.value.status_code == 503 and cal.value.status_code == 500
    assert is_down(mail.value) and is_down(cal.value)
    assert deps.failed_dependencies(mail.value) == ("google",)


def test_nothing_secret_or_tokenlike_is_in_any_message_of_the_module():
    import inspect

    src = inspect.getsource(google_mcp)
    for needle in ("creds.token}", "refresh_token}", "client_secret}"):
        assert needle not in src


# --- calendar -----------------------------------------------------------------

WINDOW = ("2025-11-20T00:00:00-07:00", "2025-11-21T00:00:00-07:00")
EVENTS = [
    gf.event("ev1"),
    gf.event(
        "ev2",
        start={"date": "2025-11-20"},
        end={"date": "2025-11-21"},
        summary="Holiday",
        organizer={"email": "jane.doe@example.com"},
        attendees=[{"email": "operator@example.com", "self": True, "responseStatus": "declined"}],
    ),
]


async def _rest_calendar(monkeypatch, fake_google: FakeGoogle, call):
    monkeypatch.setattr(settings, "google_provider", "native")
    gf.reset_credentials()
    gf.install_native(monkeypatch, fake_google)
    try:
        return await call()
    finally:
        monkeypatch.setattr(settings, "google_provider", "mcp")


async def test_list_events_matches_the_rest_client_and_asks_for_every_event_type(mcp, monkeypatch):
    fake, _ = mcp
    fake.on("list_events", ok({"summary": "operator@example.com", "events": EVENTS}))
    got = await calendar_facade.list_events(*WINDOW)
    args = fake.bodies("list_events")[0]["params"]["arguments"]
    assert args == {
        "calendarId": "primary",
        "startTime": WINDOW[0],
        "endTime": WINDOW[1],
        "pageSize": 250,
        "orderBy": "startTime",
        "eventType": list(google_mcp.EVENT_TYPES),
    }
    want = await _rest_calendar(
        monkeypatch,
        FakeGoogle().on("GET", f"{CAL}primary/events", (200, gf.events_page(EVENTS))),
        lambda: calendar_facade.list_events(*WINDOW),
    )
    assert got == want and got["events"][0]["event_id"] == "ev1"


@pytest.mark.parametrize(
    "data, truncated", [({}, False), ({"events": [], "nextPageToken": "n"}, True)]
)
async def test_an_empty_day_and_a_cut_window(mcp, data, truncated):
    fake, _ = mcp
    fake.on("list_events", ok(data))
    assert await calendar_facade.list_events(*WINDOW) == {"events": [], "truncated": truncated}


async def test_get_event_matches_the_rest_client(mcp, monkeypatch):
    fake, _ = mcp
    fake.on("get_event", ok(EVENTS[1]))
    got = await calendar_facade.get_event("ev2")
    assert fake.bodies()[0]["params"]["arguments"] == {"calendarId": "primary", "eventId": "ev2"}
    want = await _rest_calendar(
        monkeypatch,
        FakeGoogle().on("GET", f"{CAL}primary/events/ev2", (200, EVENTS[1])),
        lambda: calendar_facade.get_event("ev2"),
    )
    assert got == want


async def test_get_event_on_a_gone_event_is_a_no(mcp):
    fake, _ = mcp
    fake.on("get_event", tool_error("Event not found."))
    with pytest.raises(calendar_facade.CalendarFacadeError, match="event not found") as e:
        await calendar_facade.get_event("ev9")
    assert classify_failure(e.value) == "semantic"


async def test_create_event_sends_no_client_id_and_answers_the_rest_envelope(mcp, monkeypatch):
    fake, _ = mcp
    created = gf.event("abc123", summary="Sync")
    fake.on("create_event", ok(created))
    got = await calendar_facade.create_event(
        "Sync",
        "2025-11-20T09:00:00-07:00",
        "2025-11-20T09:30:00-07:00",
        attendees=["jane.doe@example.com"],
        client_event_id="0123456789abcdef",
    )
    assert fake.bodies()[0]["params"]["arguments"] == {
        "calendarId": "primary",
        "summary": "Sync",
        "startTime": "2025-11-20T09:00:00-07:00",
        "endTime": "2025-11-20T09:30:00-07:00",
        "description": "",
        "attendees": [{"email": "jane.doe@example.com"}],
        "notificationLevel": "ALL",
    }
    assert "already_existed" not in got and len(fake.requests) == 1
    want = await _rest_calendar(
        monkeypatch,
        FakeGoogle().on("POST", f"{CAL}primary/events", (200, created)),
        lambda: calendar_facade.create_event(
            "Sync",
            "2025-11-20T09:00:00-07:00",
            "2025-11-20T09:30:00-07:00",
            attendees=["jane.doe@example.com"],
        ),
    )
    assert got == want


async def test_update_event_sends_the_attendee_difference_against_the_event_now(mcp):
    fake, _ = mcp
    fake.on(
        "get_event",
        ok(
            gf.event(
                "ev1",
                attendees=[
                    {"email": "Operator@example.com", "self": True},
                    {"email": "jane.doe@example.com"},
                ],
            )
        ),
    )
    fake.on("update_event", ok(gf.event("ev1", summary="New")))
    out = await calendar_facade.update_event(
        "ev1", title="New", attendees=["operator@example.com", "john.rivers@example.com"]
    )
    assert out["event_id"] == "ev1" and out["summary"] == "New"
    assert fake.bodies("update_event")[0]["params"]["arguments"] == {
        "calendarId": "primary",
        "eventId": "ev1",
        "notificationLevel": "ALL",
        "summary": "New",
        "addedAttendees": [{"email": "john.rivers@example.com"}],
        "removedAttendeeEmails": ["jane.doe@example.com"],
    }


async def test_update_without_attendees_reads_nothing_first(mcp):
    fake, _ = mcp
    fake.on("update_event", ok(gf.event("ev1")))
    await calendar_facade.update_event("ev1", start="2025-11-20T10:00:00Z")
    assert [b["params"]["name"] for b in fake.bodies()] == ["update_event"]
    assert fake.bodies()[0]["params"]["arguments"]["startTime"] == "2025-11-20T10:00:00Z"


async def test_delete_event_and_an_already_cancelled_event_is_success(mcp):
    fake, _ = mcp
    fake.on("delete_event", ok({}))
    assert await calendar_facade.delete_event("ev1") == "ev1"
    assert fake.bodies()[0]["params"]["arguments"] == {
        "calendarId": "primary",
        "eventId": "ev1",
        "notificationLevel": "ALL",
    }
    fake.requests.clear()
    fake.on("delete_event", tool_error("Resource has been deleted", code=410))
    fake.on("get_event", ok(gf.event("ev1", status="cancelled")))
    assert await calendar_facade.delete_event("ev1") == "ev1"
    fake.on("get_event", ok(gf.event("ev1")))
    with pytest.raises(calendar_facade.CalendarFacadeError) as e:
        await calendar_facade.delete_event("ev1")
    assert e.value.status_code == 500


async def test_a_calendar_validation_failure_is_the_500_and_sends_nothing(mcp):
    fake, _ = mcp
    with pytest.raises(calendar_facade.CalendarFacadeError) as e:
        await calendar_facade.list_events("tomorrow", "later")
    assert e.value.status_code == 500 and fake.requests == []


# --- the switch, everywhere it is consulted ------------------------------------


def test_the_switch_accepts_mcp_and_routes_both_facades(mcp):
    assert Settings(_env_file=None, google_provider="mcp").google_provider == "mcp"
    assert email_facade._google() is google_mcp and calendar_facade._google() is google_mcp
    assert google_mcp.selected() and google_mcp.configured() and not google.selected()


async def test_exchange_still_wins_over_mcp(mcp, monkeypatch):
    from central_command.integrations import exchange

    monkeypatch.setattr(exchange, "configured", lambda: True)

    async def ex_list(q):
        return [{"uuid": "ex-1"}]

    monkeypatch.setattr(exchange, "list_refs", ex_list)
    assert await email_facade.list_refs("in:inbox") == [{"uuid": "ex-1"}]
    assert mcp[0].requests == []


@pytest.mark.parametrize(
    "missing", ["google_client_id", "google_client_secret", "google_refresh_token"]
)
async def test_selected_but_unconfigured_fails_as_a_no_before_any_socket(mcp, monkeypatch, missing):
    fake, _ = mcp
    monkeypatch.setattr(settings, missing, "")
    key = "CC_" + missing.upper()
    with pytest.raises(email_facade.EmailFacadeError, match=key) as mail:
        await email_facade.list_refs("in:inbox")
    with pytest.raises(calendar_facade.CalendarFacadeError, match=key) as cal:
        await calendar_facade.get_event("ev1")
    for e in (mail.value, cal.value):
        assert classify_failure(e) == "semantic" and not is_down(e)
    assert calendar_facade.provider_configured() is False and fake.requests == []


def test_provider_configured_counts_a_complete_mcp_client(mcp, monkeypatch):
    monkeypatch.setattr(settings, "calendar_facade_token", "")
    assert calendar_facade.provider_configured() is True


def test_mcp_needs_the_google_extra(mcp, monkeypatch):
    from central_command.integrations import exchange

    monkeypatch.setattr(exchange, "configured", lambda: False)
    assert provider_extras.selected() == ["google"]


@pytest.mark.parametrize("status, up", [(404, True), (401, True), (503, False)])
async def test_the_probe_asks_the_mcp_hosts_unauthenticated(mcp, monkeypatch, status, up):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(status)

    gf.install(monkeypatch, handler)
    assert await probes.probe_google() is up
    assert sorted(str(r.url) for r in seen) == sorted(
        [
            "https://oauth2.googleapis.com/",
            "https://gmailmcp.example.com/",
            "https://calendarmcp.example.com/",
        ]
    )
    assert all("authorization" not in r.headers for r in seen)


async def test_the_selfcheck_names_the_mcp_client(mcp, monkeypatch):
    from central_command import selfcheck

    async def refs(q):
        return [{"uuid": "m1"}]

    monkeypatch.setattr(email_facade, "list_refs", refs)
    out = await selfcheck._check_google_mail("the mail feed")
    assert out.status == selfcheck.PASS and "Google MCP client" in out.message
    assert selfcheck._mail_remedy() == "CC_GOOGLE_REFRESH_TOKEN"

    async def refused(q):
        raise email_facade.EmailFacadeError("HTTP 422 approval")

    monkeypatch.setattr(email_facade, "list_refs", refused)
    out = await selfcheck._check_google_mail("the mail feed")
    assert out.status != selfcheck.PASS and "google_mcp_grant" in out.message
    monkeypatch.setattr(settings, "google_refresh_token", "")
    out = await selfcheck._check_google_mail("the mail feed")
    assert "CC_GOOGLE_PROVIDER=mcp" in out.message and "CC_GOOGLE_REFRESH_TOKEN" in out.message


def test_the_default_does_not_import_the_mcp_module():
    import os
    import subprocess
    import sys

    out = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, central_command.api.app; print('central_command.integrations.google_mcp' in sys.modules)",
        ],
        capture_output=True,
        text=True,
        timeout=110,
        check=False,
        env={**os.environ, "CC_GOOGLE_PROVIDER": "n8n"},
    )
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip().splitlines()[-1] == "False"
