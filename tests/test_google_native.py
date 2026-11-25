"""The native Google client (DL-234), one operation at a time, through the SAME
façade functions the callers use. Each test pins the exact request the client
sends (method, URL, query, body) and what a realistic Google answer becomes.
Google is `tests/google_fakes.FakeGoogle` behind `httpx.MockTransport`: no
real account, no n8n. The parity with the n8n path is `test_google_parity.py`.
"""

from __future__ import annotations

import asyncio
import json
import os

import httpx
import pytest

# The `google` extra (DL-235): an install that did not select Google skips this file.
pytest.importorskip("googleapiclient")

from central_command.config import settings
from central_command.contract import dependencies as deps
from central_command.contract.failures import classify_failure, is_down
from central_command.heartbeat import probes
from central_command.integrations import calendar_facade, email_facade, google
from tests import google_fakes as gf
from tests.google_fakes import CAL, GMAIL, FakeGoogle


@pytest.fixture
def native(monkeypatch):
    gf.select_native(monkeypatch)
    yield
    gf.reset_credentials()


def _wire(monkeypatch, fake: FakeGoogle) -> FakeGoogle:
    gf.install_native(monkeypatch, fake)
    return fake


def _api(fake):
    """The non-token requests, in order."""
    return [r for r in fake.requests if str(r.url) != gf.TOKEN]


# --- the switch ---------------------------------------------------------------


def test_the_default_is_n8n_and_no_google_code_runs(monkeypatch):
    assert settings.google_provider == "n8n"
    assert email_facade._google() is None and calendar_facade._google() is None

    def boom(*a, **kw):
        raise AssertionError("the n8n default reached the native Google client")

    monkeypatch.setattr(google, "mail_request", boom)
    monkeypatch.setattr(google, "calendar_request", boom)
    with pytest.raises(email_facade.EmailFacadeError, match="CC_EMAIL_FACADE_URL"):
        asyncio.run(email_facade.list_refs("in:inbox"))


def test_the_default_does_not_import_the_module():
    import subprocess
    import sys

    out = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys, central_command.api.app; "
                "print(any(m in sys.modules for m in ('central_command.integrations.google', "
                "'googleapiclient', 'google_auth_oauthlib')))"
            ),
        ],
        capture_output=True,
        text=True,
        timeout=110,
        check=False,
        env={**os.environ, "CC_GOOGLE_PROVIDER": "n8n"},
    )
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip().splitlines()[-1] == "False"


def test_a_bad_provider_value_refuses_to_start():
    from pydantic import ValidationError

    from central_command.config import Settings

    with pytest.raises(ValidationError):
        Settings(google_provider="gmail")


async def test_exchange_still_wins_over_a_selected_native_google(monkeypatch, native):
    from central_command.integrations import exchange

    monkeypatch.setattr(exchange, "configured", lambda: True)

    async def ex_list(q):
        return [{"uuid": "ex-1"}]

    monkeypatch.setattr(exchange, "list_refs", ex_list)
    assert await email_facade.list_refs("in:inbox") == [{"uuid": "ex-1"}]


@pytest.mark.parametrize(
    "missing", ["google_client_id", "google_client_secret", "google_refresh_token"]
)
async def test_selected_but_unconfigured_fails_as_a_no_before_any_socket(
    monkeypatch, native, missing
):
    monkeypatch.setattr(settings, missing, "")

    def no_socket(*a, **kw):
        raise AssertionError("an unconfigured client opened a socket")

    monkeypatch.setattr(google, "_http", no_socket)
    key = "CC_" + missing.upper()
    with pytest.raises(email_facade.EmailFacadeError, match=key) as mail:
        await email_facade.list_refs("in:inbox")
    with pytest.raises(calendar_facade.CalendarFacadeError, match=key) as cal:
        await calendar_facade.get_event("ev1")
    for e in (mail.value, cal.value):
        assert classify_failure(e) == "semantic" and not is_down(e)
    assert calendar_facade.provider_configured() is False


def test_provider_configured_counts_a_complete_native_client(monkeypatch, native):
    monkeypatch.setattr(settings, "calendar_facade_token", "")
    assert calendar_facade.provider_configured() is True


# --- mail: list ---------------------------------------------------------------


async def test_list_sends_q_and_maxresults_and_returns_references_only(monkeypatch, native):
    fake = _wire(
        monkeypatch,
        FakeGoogle(require_auth=True).on(
            "GET",
            f"{GMAIL}/messages",
            (
                200,
                {
                    "messages": [
                        {"id": "18a1", "threadId": "18a0"},
                        {"id": "18b2", "threadId": "18b2"},
                    ],
                    "resultSizeEstimate": 2,
                },
            ),
        ),
    )
    refs = await email_facade.list_refs("in:inbox newer_than:1d")
    assert refs == [
        {"uuid": "18a1", "conversation_id": "18a0"},
        {"uuid": "18b2", "conversation_id": "18b2"},
    ]
    (req,) = _api(fake)
    assert req.method == "GET"
    assert gf.path(req) == f"{GMAIL}/messages"
    assert gf.params(req) == {"q": "in:inbox newer_than:1d", "maxResults": "500"}
    assert req.headers["authorization"] == "Bearer ya29.fake-access-1"


async def test_list_follows_the_page_token_and_concatenates_pages(monkeypatch, native):
    fake = _wire(
        monkeypatch,
        FakeGoogle().on(
            "GET",
            f"{GMAIL}/messages",
            (200, {"messages": [{"id": "p1", "threadId": "t1"}], "nextPageToken": "tok2"}),
            (200, {"messages": [{"id": "p2", "threadId": "t2"}], "nextPageToken": "tok3"}),
            (200, {"messages": [{"id": "p3", "threadId": "t3"}]}),
        ),
    )
    refs = await email_facade.list_refs("x")
    assert [r["uuid"] for r in refs] == ["p1", "p2", "p3"]
    tokens = [r.url.params.get("pageToken") for r in _api(fake)]
    assert tokens == [None, "tok2", "tok3"], "no pageToken on the first request, then each page's"


async def test_an_empty_result_omits_messages_and_is_an_empty_list(monkeypatch, native):
    _wire(
        monkeypatch, FakeGoogle().on("GET", f"{GMAIL}/messages", (200, {"resultSizeEstimate": 0}))
    )
    assert await email_facade.list_refs("in:inbox") == []


async def test_five_full_pages_with_more_remaining_is_the_page_cap_refusal(monkeypatch, native):
    fake = _wire(
        monkeypatch,
        FakeGoogle().on(
            "GET",
            f"{GMAIL}/messages",
            (200, {"messages": [{"id": "a", "threadId": "a"}], "nextPageToken": "more"}),
        ),
    )
    with pytest.raises(email_facade.EmailFacadeError) as e:
        await email_facade.list_refs("in:anywhere")
    assert e.value.status_code == 422 and "pagination cap hit (5 pages fetched" in str(e.value)
    assert len(_api(fake)) == 5
    assert classify_failure(e.value) == "semantic"


async def test_an_out_of_alphabet_id_is_dropped_and_all_dropped_is_a_failure(monkeypatch, native):
    _wire(
        monkeypatch,
        FakeGoogle().on(
            "GET",
            f"{GMAIL}/messages",
            (200, {"messages": [{"id": "ok1", "threadId": "t"}, {"id": "../x", "threadId": "t"}]}),
        ),
    )
    assert await email_facade.list_refs("q") == [{"uuid": "ok1", "conversation_id": "t"}]
    gf.reset_credentials()
    _wire(
        monkeypatch,
        FakeGoogle().on(
            "GET", f"{GMAIL}/messages", (200, {"messages": [{"id": "a b", "threadId": "t"}]})
        ),
    )
    with pytest.raises(email_facade.EmailFacadeError) as e:
        await email_facade.list_refs("q")
    assert e.value.status_code == 503 and "dropped ALL 1 entries" in str(e.value)


async def test_an_empty_query_is_refused_before_any_request(monkeypatch, native):
    fake = _wire(monkeypatch, FakeGoogle())
    with pytest.raises(email_facade.EmailFacadeError) as e:
        await email_facade.list_refs("   ")
    assert (
        e.value.status_code == 422
        and "requires scope_query (non-empty Gmail search string)" in str(e.value)
    )
    assert fake.requests == []


# --- mail: message ------------------------------------------------------------


async def test_message_reads_format_full_and_normalises_the_record(monkeypatch, native):
    fake = _wire(
        monkeypatch,
        FakeGoogle().on("GET", f"{GMAIL}/messages/18c0ffee00aa11bb", (200, gf.multipart_message())),
    )
    msg = await email_facade.get_message("18c0ffee00aa11bb")
    (req,) = _api(fake)
    assert req.method == "GET" and gf.path(req) == f"{GMAIL}/messages/18c0ffee00aa11bb"
    assert gf.params(req) == {"format": "full"}
    assert msg["uuid"] == "18c0ffee00aa11bb" and msg["conversation_id"] == "18c0ffee00aa0000"
    assert msg["from"] == "Jane Doe <jane.doe@example.com>"
    assert msg["cc"] == "John Rivers <john.rivers@example.com>"
    assert msg["subject"] == "Your November statement"
    assert msg["date"] == "2025-11-18T10:00:00.123Z", "internalDate, as toISOString writes it"
    assert msg["body_text"] == "Statement ready — €12.50 due.\r\n"
    # iso-8859-1 is WHATWG's windows-1252: 0x80 is the euro sign.
    assert msg["body_html"] == "<p>Statement € ready ~ café</p>"
    assert msg["authentication_results"][0].startswith("mx.google.com;"), "header order kept"
    assert len(msg["authentication_results"]) == 2 and len(msg["dkim_signatures"]) == 1
    assert msg["list_unsubscribe_post"] == "List-Unsubscribe=One-Click"
    assert msg["body_truncated"] is False and msg["has_attachments"] is True
    pdf, png, ics = msg["attachments"][:3]
    assert pdf == {
        "filename": "statement.pdf",
        "mime_type": "application/pdf",
        "size": 52311,
        "attachment_id": "ANGjdJ_pdf-1",
        "is_inline": False,
    }, "attachment disposition wins over a Content-ID"
    assert png["is_inline"] is True and png["attachment_id"] == "ANGjdJ_png-2"
    assert ics["mime_type"] == "text/calendar" and ics["attachment_id"] == ""
    assert ics["filename"] == "invite.ics" and ics["inline_text"].startswith("BEGIN:VCALENDAR")
    assert len(msg["attachments"]) == 3, "the forwarded message's parts are not walked"
    assert "forwarded" not in msg["body_text"]


async def test_a_single_part_html_message_dates_from_the_header(monkeypatch, native):
    _wire(
        monkeypatch,
        FakeGoogle().on("GET", f"{GMAIL}/messages/18c0ffee00bb22cc", (200, gf.single_part_html())),
    )
    msg = await email_facade.get_message("18c0ffee00bb22cc")
    assert msg["body_html"] == "<b>Hi</b>\n" and msg["body_text"] == ""
    assert msg["date"] == "2025-11-19T15:30:15.000Z"


async def test_an_unknown_message_is_an_outage_as_the_facade_answers_it(monkeypatch, native):
    """The n8n façade answers an unknown message 503 by design (the attachment
    re-read relies on it): `message not found` is not a stated refusal."""
    _wire(
        monkeypatch,
        FakeGoogle().on(
            "GET",
            f"{GMAIL}/messages/gone1",
            (404, gf.error_body(404, "Requested entity was not found.", "NOT_FOUND")),
        ),
    )
    with pytest.raises(email_facade.EmailFacadeError) as e:
        await email_facade.get_message("gone1")
    assert e.value.status_code == 503 and "message not found for the given reference" in str(
        e.value
    )
    assert is_down(e.value)


async def test_a_reference_outside_the_alphabet_is_refused_without_a_request(monkeypatch, native):
    fake = _wire(monkeypatch, FakeGoogle())
    with pytest.raises(email_facade.EmailFacadeError) as e:
        await email_facade.get_message("a/../drafts")
    assert e.value.status_code == 422 and "requires source_ref.uuid" in str(e.value)
    assert fake.requests == []


# --- mail: attachment ---------------------------------------------------------


async def test_attachment_reads_one_part_and_decodes_base64url(monkeypatch, native):
    payload = bytes(range(256)) * 3 + b"\xfb\xff"  # forces - and _ in base64url
    fake = _wire(
        monkeypatch,
        FakeGoogle().on(
            "GET",
            f"{GMAIL}/messages/18c0ffee00aa11bb/attachments/ANGjdJ_pdf-1",
            (
                200,
                {"attachmentId": "ANGjdJ_pdf-1", "size": len(payload), "data": gf.b64url(payload)},
            ),
        ),
    )
    data = await email_facade.get_attachment("18c0ffee00aa11bb", "ANGjdJ_pdf-1")
    assert data == payload
    (req,) = _api(fake)
    assert (
        req.method == "GET"
        and gf.path(req) == f"{GMAIL}/messages/18c0ffee00aa11bb/attachments/ANGjdJ_pdf-1"
    )


@pytest.mark.parametrize("status", [400, 404])
async def test_a_stale_attachment_id_is_a_refusal(monkeypatch, native, status):
    _wire(
        monkeypatch,
        FakeGoogle().on(
            "GET",
            f"{GMAIL}/messages/m1/attachments/old",
            (status, gf.error_body(status, "Invalid attachment token")),
        ),
    )
    with pytest.raises(email_facade.EmailFacadeError) as e:
        await email_facade.get_attachment("m1", "old")
    assert (
        e.value.status_code == 422
        and f"attachment not found for the given reference ({status})" in str(e.value)
    )
    assert not is_down(e.value)


async def test_an_attachment_answer_without_data_is_a_failure(monkeypatch, native):
    _wire(
        monkeypatch,
        FakeGoogle().on("GET", f"{GMAIL}/messages/m1/attachments/a1", (200, {"size": 3})),
    )
    with pytest.raises(email_facade.EmailFacadeError) as e:
        await email_facade.get_attachment("m1", "a1")
    assert e.value.status_code == 503


# --- mail: the one label write ------------------------------------------------


async def test_report_spam_adds_spam_removes_inbox_and_returns_the_envelope(monkeypatch, native):
    fake = _wire(
        monkeypatch,
        FakeGoogle().on(
            "POST",
            f"{GMAIL}/messages/18c0ffee00aa11bb/modify",
            (
                200,
                {
                    "id": "18c0ffee00aa11bb",
                    "threadId": "18c0ffee00aa0000",
                    "labelIds": ["SPAM", "UNREAD"],
                },
            ),
        ),
    )
    out = await email_facade.report_spam("18c0ffee00aa11bb")
    assert out == {
        "kind": "email",
        "mode": "report_spam",
        "source_ref": {"uuid": "18c0ffee00aa11bb"},
        "ok": True,
        "label_ids": ["SPAM", "UNREAD"],
    }
    (req,) = _api(fake)
    assert req.method == "POST" and gf.path(req) == f"{GMAIL}/messages/18c0ffee00aa11bb/modify"
    assert gf.body_json(req) == {"addLabelIds": ["SPAM"], "removeLabelIds": ["INBOX"]}


# --- the token ----------------------------------------------------------------


async def test_the_token_is_refreshed_once_and_reused(monkeypatch, native):
    fake = _wire(
        monkeypatch,
        FakeGoogle(require_auth=True).on("GET", f"{GMAIL}/messages", (200, {"messages": []})),
    )
    await email_facade.list_refs("a")
    await email_facade.list_refs("b")
    assert len(fake.of("POST", gf.TOKEN)) == 1, "the access token is reused until it nears expiry"


async def test_a_401_refreshes_the_token_and_resends_once(monkeypatch, native):
    fake = _wire(
        monkeypatch,
        FakeGoogle().on(
            "GET",
            f"{GMAIL}/messages/m1",
            (
                401,
                gf.error_body(
                    401, "Request had invalid authentication credentials.", "UNAUTHENTICATED"
                ),
            ),
            (200, gf.single_part_html("m1")),
        ),
    )
    msg = await email_facade.get_message("m1")
    assert msg["uuid"] == "m1"
    api = _api(fake)
    assert [r.headers["authorization"] for r in api] == [
        "Bearer ya29.fake-access-1",
        "Bearer ya29.fake-access-2",
    ]
    assert len(fake.of("POST", gf.TOKEN)) == 2


async def test_a_401_after_the_refresh_is_the_auth_failure_and_parks(monkeypatch, native):
    fake = _wire(
        monkeypatch,
        FakeGoogle().on(
            "GET", f"{GMAIL}/messages", (401, gf.error_body(401, "bad", "UNAUTHENTICATED"))
        ),
    )
    with pytest.raises(email_facade.EmailFacadeError) as e:
        await email_facade.list_refs("q")
    assert e.value.status_code == 503 and "provider auth failed (401)" in str(e.value)
    assert len(_api(fake)) == 2, "one re-send after the refresh, never a loop"
    assert is_down(e.value)


async def test_a_revoked_refresh_token_is_the_auth_failure_and_names_the_consent_command(
    monkeypatch, native
):
    fake = _wire(
        monkeypatch,
        FakeGoogle().token(
            (
                400,
                {
                    "error": "invalid_grant",
                    "error_description": "Token has been expired or revoked.",
                },
            )
        ),
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
    assert _api(fake) == [], "no API call without a token"


async def test_an_unreachable_token_endpoint_is_an_outage(monkeypatch, native):
    _wire(monkeypatch, FakeGoogle().token(httpx.ConnectError("connection refused")))
    with pytest.raises(email_facade.EmailFacadeError) as e:
        await email_facade.list_refs("q")
    assert e.value.status_code == 503 and is_down(e.value)


# --- mail: every error class --------------------------------------------------


@pytest.mark.parametrize(
    "status, expected, text",
    [
        (400, 422, "rejected the list query (400)"),
        (403, 503, "provider auth failed (403)"),
        (404, 503, "provider list failed (404)"),
        (409, 503, "provider list failed (409)"),
        (429, 503, "provider rate limit hit"),
        (500, 503, "provider list failed (500)"),
        (503, 503, "provider list failed (503)"),
    ],
)
async def test_list_maps_each_google_status_to_the_facade_status(
    monkeypatch, native, status, expected, text
):
    _wire(monkeypatch, FakeGoogle().on("GET", f"{GMAIL}/messages", (status, gf.error_body(status))))
    with pytest.raises(email_facade.EmailFacadeError) as e:
        await email_facade.list_refs("q")
    assert e.value.status_code == expected and text in str(e.value)
    assert is_down(e.value) is (expected == 503)
    assert getattr(e.value, deps.DEPENDENCIES_ATTR) == (deps.GOOGLE,)


@pytest.mark.parametrize(
    "status, expected",
    [(400, 503), (403, 503), (404, 503), (409, 503), (429, 503), (500, 503), (503, 503)],
)
async def test_message_maps_each_google_status_to_the_facade_status(
    monkeypatch, native, status, expected
):
    _wire(
        monkeypatch, FakeGoogle().on("GET", f"{GMAIL}/messages/m1", (status, gf.error_body(status)))
    )
    with pytest.raises(email_facade.EmailFacadeError) as e:
        await email_facade.get_message("m1")
    assert e.value.status_code == expected


@pytest.mark.parametrize(
    "exc", [httpx.ConnectError("connection refused"), httpx.ReadTimeout("timed out")]
)
async def test_a_transport_failure_is_an_outage_on_every_mail_mode(monkeypatch, native, exc):
    _wire(
        monkeypatch,
        FakeGoogle()
        .on("GET", f"{GMAIL}/messages", exc)
        .on("GET", f"{GMAIL}/messages/m1", exc)
        .on("GET", f"{GMAIL}/messages/m1/attachments/a1", exc)
        .on("POST", f"{GMAIL}/messages/m1/modify", exc),
    )
    for call in (
        lambda: email_facade.list_refs("q"),
        lambda: email_facade.get_message("m1"),
        lambda: email_facade.get_attachment("m1", "a1"),
        lambda: email_facade.report_spam("m1"),
    ):
        with pytest.raises(email_facade.EmailFacadeError) as e:
            await call()
        assert e.value.status_code == 503 and is_down(e.value)
        assert isinstance(e.value.__cause__, google.GoogleError)


# --- calendar -----------------------------------------------------------------


async def test_list_events_sends_the_window_and_normalises_each_event(monkeypatch, native):
    items = [
        gf.event(
            "ev1",
            description="Agenda: budget",
            attendees=[
                {"email": "operator@example.com", "self": True, "responseStatus": "accepted"},
                {"email": "jane.doe@example.com", "responseStatus": "needsAction"},
            ],
        ),
        gf.event(
            "ev2",
            start={"date": "2025-11-20"},
            end={"date": "2025-11-21"},
            organizer={"email": "john.rivers@example.com"},
            summary="Holiday",
            location="Home",
        ),
        gf.event(
            "ev3",
            description="   ",
            attendees=[
                {"email": "operator@example.com", "self": True, "responseStatus": "declined"}
            ],
            organizer={"email": "jane.doe@example.com"},
        ),
        gf.event("ev4", status="cancelled", attachments=[{"fileUrl": "https://example.com/doc"}]),
    ]
    fake = _wire(
        monkeypatch, FakeGoogle().on("GET", f"{CAL}primary/events", (200, gf.events_page(items)))
    )
    out = await calendar_facade.list_events(
        "2025-11-20T00:00:00-07:00", "2025-11-21T00:00:00-07:00"
    )
    (req,) = _api(fake)
    assert req.method == "GET"
    assert gf.path(req) == f"{CAL}primary/events"
    assert gf.params(req) == {
        "timeMin": "2025-11-20T00:00:00-07:00",
        "timeMax": "2025-11-21T00:00:00-07:00",
        "maxResults": "250",
        "singleEvents": "true",
        "orderBy": "startTime",
        "showDeleted": "false",
    }
    assert out["truncated"] is False
    e1, e2, e3, e4 = out["events"]
    assert e1 == {
        "event_id": "ev1",
        "start": "2025-11-20T09:00:00-07:00",
        "end": "2025-11-20T09:30:00-07:00",
        "all_day": False,
        "summary": "Event ev1",
        "location": "",
        "attendee_count": 2,
        "organizer_is_self": True,
        "has_agenda": True,
        "declined": False,
    }
    assert (
        e2["all_day"] is True and e2["start"] == "2025-11-20" and e2["organizer_is_self"] is False
    )
    assert e3["has_agenda"] is False and e3["declined"] is True
    assert e4["declined"] is True and e4["has_agenda"] is True


async def test_another_calendar_id_is_encoded_like_encodeuricomponent(monkeypatch, native):
    fake = _wire(
        monkeypatch,
        FakeGoogle().on("GET", f"{CAL}team%40example.com/events", (200, gf.events_page([]))),
    )
    await calendar_facade.list_events(
        "2025-11-20T00:00:00Z", "2025-11-21T00:00:00Z", "team@example.com"
    )
    assert gf.path(_api(fake)[0]) == f"{CAL}team%40example.com/events"


@pytest.mark.parametrize(
    "page, truncated",
    [
        (gf.events_page(None), False),
        (gf.events_page([], nextPageToken="next"), True),
    ],
)
async def test_an_empty_day_and_a_cut_window(monkeypatch, native, page, truncated):
    _wire(monkeypatch, FakeGoogle().on("GET", f"{CAL}primary/events", (200, page)))
    out = await calendar_facade.list_events("2025-11-20T00:00:00Z", "2025-11-21T00:00:00Z")
    assert out == {"events": [], "truncated": truncated}


async def test_a_list_answer_that_is_not_an_events_list_is_a_no(monkeypatch, native):
    _wire(
        monkeypatch,
        FakeGoogle().on("GET", f"{CAL}primary/events", (200, {"kind": "calendar#calendar"})),
    )
    with pytest.raises(calendar_facade.CalendarFacadeError, match="without an items array") as e:
        await calendar_facade.list_events("2025-11-20T00:00:00Z", "2025-11-21T00:00:00Z")
    assert e.value.status_code is None and not is_down(e.value)


async def test_get_event_returns_the_restore_record(monkeypatch, native):
    ev = gf.event("ev9", organizer={"email": "jane.doe@example.com"}, location="Room 1")
    fake = _wire(monkeypatch, FakeGoogle().on("GET", f"{CAL}primary/events/ev9", (200, ev)))
    out = await calendar_facade.get_event("ev9")
    assert out == {
        "event_id": "ev9",
        "status": "confirmed",
        "start": "2025-11-20T09:00:00-07:00",
        "end": "2025-11-20T09:30:00-07:00",
        "all_day": False,
        "summary": "Event ev9",
        "location": "Room 1",
        "attendee_count": 0,
        "organizer": "jane.doe@example.com",
        "organizer_is_self": False,
        "has_agenda": False,
        "declined": False,
    }
    assert gf.path(_api(fake)[0]) == f"{CAL}primary/events/ev9" and gf.params(_api(fake)[0]) == {}


@pytest.mark.parametrize("status", [404, 410])
async def test_get_event_on_a_gone_event_is_a_no(monkeypatch, native, status):
    _wire(
        monkeypatch,
        FakeGoogle().on("GET", f"{CAL}primary/events/ev9", (status, gf.error_body(status))),
    )
    with pytest.raises(
        calendar_facade.CalendarFacadeError, match=f"event not found \\({status}\\)"
    ) as e:
        await calendar_facade.get_event("ev9")
    assert not is_down(e.value)


async def test_create_event_posts_the_body_with_send_updates_and_no_time_zone(monkeypatch, native):
    created = gf.event("abc123", summary="Budget review")
    fake = _wire(monkeypatch, FakeGoogle().on("POST", f"{CAL}primary/events", (200, created)))
    out = await calendar_facade.create_event(
        "Budget review",
        "2025-11-20T09:00:00-07:00",
        "2025-11-20T09:30:00-07:00",
        description="Q4",
        attendees=["jane.doe@example.com"],
    )
    (req,) = _api(fake)
    assert req.method == "POST" and gf.path(req) == f"{CAL}primary/events"
    assert gf.params(req) == {"sendUpdates": "all"}
    assert gf.body_json(req) == {
        "summary": "Budget review",
        "start": {"dateTime": "2025-11-20T09:00:00-07:00"},
        "end": {"dateTime": "2025-11-20T09:30:00-07:00"},
        "description": "Q4",
        "attendees": [{"email": "jane.doe@example.com"}],
    }
    assert out == {
        "event_id": "abc123",
        "html_link": "https://www.google.com/calendar/event?eid=abc123",
        "summary": "Budget review",
        "start": "2025-11-20T09:00:00-07:00",
        "end": "2025-11-20T09:30:00-07:00",
    }


async def test_a_retried_create_reads_its_own_409_back(monkeypatch, native):
    """DL-219: the client id is the event id; Google's 409 on it is the proof an
    earlier attempt of the same action created it."""
    cid = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
    fake = _wire(
        monkeypatch,
        FakeGoogle()
        .on(
            "POST",
            f"{CAL}primary/events",
            (409, gf.error_body(409, "The requested identifier already exists.", "ALREADY_EXISTS")),
        )
        .on("GET", f"{CAL}primary/events/{cid}", (200, gf.event(cid, summary="Block"))),
    )
    out = await calendar_facade.create_event(
        "Block", "2025-11-20T09:00:00Z", "2025-11-20T10:00:00Z", client_event_id=cid
    )
    assert out["already_existed"] is True and out["event_id"] == cid
    post, get = _api(fake)
    assert gf.body_json(post)["id"] == cid and get.method == "GET"


async def test_a_409_on_an_event_since_cancelled_is_refused_never_recreated(monkeypatch, native):
    cid = "a1b2c3d4e5f6"
    _wire(
        monkeypatch,
        FakeGoogle()
        .on("POST", f"{CAL}primary/events", (409, gf.error_body(409)))
        .on("GET", f"{CAL}primary/events/{cid}", (200, gf.event(cid, status="cancelled"))),
    )
    with pytest.raises(calendar_facade.CalendarFacadeError, match="since been cancelled") as e:
        await calendar_facade.create_event(
            "Block", "2025-11-20T09:00:00Z", "2025-11-20T10:00:00Z", client_event_id=cid
        )
    assert not is_down(e.value)


async def test_a_409_without_a_client_id_is_a_failure(monkeypatch, native):
    _wire(monkeypatch, FakeGoogle().on("POST", f"{CAL}primary/events", (409, gf.error_body(409))))
    with pytest.raises(calendar_facade.CalendarFacadeError) as e:
        await calendar_facade.create_event("Block", "2025-11-20T09:00:00Z", "2025-11-20T10:00:00Z")
    assert e.value.status_code == 500 and "create - provider call failed (409)" in str(e.value)


async def test_update_event_patches_only_what_was_given(monkeypatch, native):
    fake = _wire(
        monkeypatch,
        FakeGoogle().on(
            "PATCH", f"{CAL}primary/events/ev1", (200, gf.event("ev1", summary="Moved"))
        ),
    )
    out = await calendar_facade.update_event(
        "ev1", title="Moved", start="2025-11-20T11:00:00Z", end="2025-11-20T11:30:00Z"
    )
    (req,) = _api(fake)
    assert req.method == "PATCH" and gf.path(req) == f"{CAL}primary/events/ev1"
    assert gf.params(req) == {"sendUpdates": "all"}
    assert gf.body_json(req) == {
        "summary": "Moved",
        "start": {"dateTime": "2025-11-20T11:00:00Z"},
        "end": {"dateTime": "2025-11-20T11:30:00Z"},
    }
    assert out["event_id"] == "ev1" and out["summary"] == "Moved"


@pytest.mark.parametrize(
    "status, body", [(204, None), (410, gf.error_body(410, "Resource has been deleted"))]
)
async def test_delete_event_sends_updates_and_already_gone_is_success(
    monkeypatch, native, status, body
):
    fake = _wire(monkeypatch, FakeGoogle().on("DELETE", f"{CAL}primary/events/ev1", (status, body)))
    assert await calendar_facade.delete_event("ev1") == "ev1"
    (req,) = _api(fake)
    assert req.method == "DELETE" and gf.path(req) == f"{CAL}primary/events/ev1"
    assert gf.params(req) == {"sendUpdates": "all"}


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 429, 500, 503])
async def test_every_calendar_failure_is_the_500_n8n_answered(monkeypatch, native, status):
    """Every `fail()` in lib-google-calendar reached Python as n8n's HTTP 500,
    so every one parks (a stated quirk, kept: a 404 on update parks too)."""
    _wire(
        monkeypatch,
        FakeGoogle().on("PATCH", f"{CAL}primary/events/ev1", (status, gf.error_body(status))),
    )
    with pytest.raises(calendar_facade.CalendarFacadeError) as e:
        await calendar_facade.update_event("ev1", title="x")
    assert e.value.status_code == 500 and is_down(e.value)
    assert getattr(e.value, deps.DEPENDENCIES_ATTR) == (deps.GOOGLE,)


async def test_a_calendar_transport_failure_is_an_outage(monkeypatch, native):
    _wire(
        monkeypatch,
        FakeGoogle().on("GET", f"{CAL}primary/events", httpx.ConnectTimeout("timed out")),
    )
    with pytest.raises(calendar_facade.CalendarFacadeError) as e:
        await calendar_facade.list_events("2025-11-20T00:00:00Z", "2025-11-21T00:00:00Z")
    assert e.value.status_code == 500 and "no HTTP status" in str(e.value) and is_down(e.value)


async def test_a_calendar_validation_failure_is_the_500_too_and_sends_nothing(monkeypatch, native):
    fake = _wire(monkeypatch, FakeGoogle())
    with pytest.raises(calendar_facade.CalendarFacadeError) as e:
        await calendar_facade.create_event("x", "2025-11-20T10:00:00Z", "2025-11-20T09:00:00Z")
    assert e.value.status_code == 500 and "end must be after start" in str(e.value)
    assert fake.requests == []


# --- the outage machinery -----------------------------------------------------


def test_google_is_a_declared_and_probed_dependency():
    assert deps.GOOGLE in deps.DEPENDENCIES and deps.GOOGLE in deps.PROBED
    assert probes.GOOGLE == deps.GOOGLE and deps.GOOGLE in probes.PROBES


def test_the_probe_names_the_hosts_the_client_calls():
    """The API hosts are the `rootUrl` of the discovery documents the client
    library bundles and builds from; the token host is google-auth's."""
    import pathlib

    import googleapiclient

    docs = pathlib.Path(googleapiclient.__file__).parent / "discovery_cache" / "documents"
    roots = {
        json.loads((docs / f"{d}.json").read_text())["rootUrl"].rstrip("/")
        for d in ("gmail.v1", "calendar.v3")
    }
    assert set(probes.GOOGLE_HOSTS) == roots | {"https://oauth2.googleapis.com"}
    assert google.TOKEN_URI.startswith("https://oauth2.googleapis.com/")


async def test_the_probe_is_up_without_a_socket_when_not_selected(monkeypatch):
    def no_socket(*a, **kw):
        raise AssertionError("the google probe opened a socket on the n8n default")

    monkeypatch.setattr(httpx, "AsyncClient", no_socket)
    assert await probes.probe_google() is True


@pytest.mark.parametrize("status, up", [(404, True), (401, True), (503, False), (403, False)])
async def test_the_probe_asks_each_host_unauthenticated(monkeypatch, native, status, up):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(status)

    gf.install(monkeypatch, handler)
    assert await probes.probe_google() is up
    assert sorted(str(r.url) for r in seen) == sorted(h + "/" for h in probes.GOOGLE_HOSTS)
    assert all("authorization" not in r.headers for r in seen)


async def test_a_parked_mail_read_waits_on_google_not_on_n8n(monkeypatch, native):
    """The read map says `n8n`; the error from the Google path says `google`,
    and the error is what the park records."""
    from central_command.contract.failures import DependencyDown
    from central_command.runtime import tools

    _wire(monkeypatch, FakeGoogle().on("GET", f"{GMAIL}/messages", (503, gf.error_body(503))))
    with pytest.raises(email_facade.EmailFacadeError) as e:
        await email_facade.list_refs("q")
    with pytest.raises(DependencyDown) as down:
        tools._raise_if_down(e.value, ("n8n",), "mail_search")
    assert down.value.dependencies == ("google",)
    assert deps.failed_dependencies(e.value) == ("google",)
    assert deps.stamped_dependencies(e.value) == ("google",)


def test_an_n8n_error_still_parks_on_n8n():
    from central_command.contract.failures import DependencyDown
    from central_command.runtime import tools

    err = email_facade.EmailFacadeError("email façade list failed: HTTP 503 {}", status_code=503)
    with pytest.raises(DependencyDown) as down:
        tools._raise_if_down(err, ("n8n",), "mail_search")
    assert down.value.dependencies == ("n8n",)


def test_nothing_secret_is_in_any_message_of_the_module():
    """The source carries no f-string that interpolates a credential setting
    into a message."""
    with open(google.__file__, encoding="utf-8") as f:
        src = f.read()
    for name in ("google_client_secret", "google_refresh_token"):
        lines = [ln for ln in src.splitlines() if name in ln]
        assert all('f"' not in ln and "f'" not in ln for ln in lines), lines
    assert json.loads(google.answer_text(google.GoogleError("x: y", status_code=503))) == {
        "message": "x: y"
    }


# --- the library's own seams ----------------------------------------------------


async def test_the_documented_http_mock_sequence_drives_a_refresh_after_401(monkeypatch, native):
    """googleapiclient's own `HttpMockSequence` (docs/mocks.md) at the Http
    seam: token, a 401 from Gmail, a second token, the answer. One sequence is
    shared because each call builds its Http objects in that order."""
    from googleapiclient.http import HttpMockSequence

    token = json.dumps(
        {
            "access_token": "ya29.seq",
            "expires_in": 3599,
            "token_type": "Bearer",
            "scope": " ".join(google.SCOPES),
        }
    )
    seq = HttpMockSequence(
        [
            ({"status": "200"}, token),
            ({"status": "401"}, json.dumps(gf.error_body(401, "expired", "UNAUTHENTICATED"))),
            ({"status": "200"}, token),
            ({"status": "200"}, json.dumps({"id": "m1", "threadId": "t1", "labelIds": ["SPAM"]})),
        ]
    )
    monkeypatch.setattr(google, "_http", lambda timeout: seq)
    out = await email_facade.report_spam("m1")
    assert out["label_ids"] == ["SPAM"]
    assert seq._iterable == [], "every scripted answer was used, in order"


async def test_each_call_builds_its_own_http_with_the_operations_timeout(monkeypatch, native):
    """httplib2.Http is not thread-safe (the library's docs/thread_safety.md):
    one per call, plus one for the token refresh, each with its clock."""
    fake = (
        FakeGoogle()
        .on("GET", f"{GMAIL}/messages", (200, {"messages": []}))
        .on("GET", f"{GMAIL}/messages/m1/attachments/a1", (200, {"size": 1, "data": "QQ"}))
        .on("DELETE", f"{CAL}primary/events/ev1", (204, None))
    )
    timeouts = gf.install_native(monkeypatch, fake)
    await email_facade.list_refs("q")
    await email_facade.get_attachment("m1", "a1")
    await calendar_facade.delete_event("ev1")
    assert timeouts == [google.TOKEN_TIMEOUT_SECONDS, 60.0, 180.0, google.CALENDAR_TIMEOUT_SECONDS]


async def test_no_discovery_document_is_fetched_at_run_time(monkeypatch, native):
    fake = _wire(monkeypatch, FakeGoogle().on("GET", f"{GMAIL}/messages", (200, {})))
    await email_facade.list_refs("q")
    assert [gf.path(r) for r in fake.requests] == [gf.TOKEN, f"{GMAIL}/messages"]


# --- the charset label is the sender's: WHATWG labels only -------------------


def _b64url(raw: bytes) -> str:
    import base64

    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


@pytest.mark.parametrize(
    ("label", "raw"),
    [
        ("utf-7", b"+AGEAYgBj-"),  # would decode to "abc" under Python's utf-7
        ("unicode-escape", b"\\u0041\\x42"),  # would decode the escapes
        ("rot13", b"nop"),
        ("x-user-defined", b"plain"),
        ("iso-2022-kr", b"plain"),  # a WHATWG `replacement` label: TextDecoder refuses it
    ],
)
def test_a_label_outside_whatwg_decodes_as_utf8_like_the_node(label, raw):
    assert google._decode(_b64url(raw), label) == raw.decode("utf-8")


@pytest.mark.parametrize(
    ("label", "raw", "text"),
    [
        ("iso-8859-1", b"\x80 caf\xe9", "€ café"),  # WHATWG: windows-1252
        ("latin1", b"\x80", "€"),
        ("windows-1252", b"\x80", "€"),
        ("iso-8859-2", b"\xb1", "ą"),
        ("koi8-r", b"\xc1", "а"),
        ("shift_jis", "日本".encode("cp932"), "日本"),
        ("utf-16le", "hé".encode("utf-16-le"), "hé"),
    ],
)
def test_a_whatwg_label_decodes_with_its_encoding(label, raw, text):
    assert google._decode(_b64url(raw), label) == text


def test_every_whatwg_row_names_a_python_codec():
    import codecs

    for codec, labels in google._WHATWG_ENCODINGS:
        codecs.lookup(codec)
        assert labels.split() == [lbl.lower() for lbl in labels.split()]
    assert "utf-7" not in google._WHATWG and "unicode-escape" not in google._WHATWG


@pytest.mark.parametrize("value", ["1e20", "-1e300", "nan", "inf", "not a number"])
def test_an_internal_date_no_date_has_is_the_nodes_invalid_time_value(value):
    with pytest.raises(google._MailFail, match="Invalid time value"):
        google._iso_from_ms(value)
