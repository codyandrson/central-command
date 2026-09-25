"""The native Exchange client, driven through a fake exchangelib (D8).

No Exchange server exists outside the second deployment's network, and none
ever will here — so the shapes are pinned by fixtures and the wiring is proven
against a real mailbox by `scripts/exchange_smoke.py`. Everything in this file
is OFFLINE by construction: the fakes below stand in for `Account`, `Folder`,
`Message` and `CalendarItem`, and nothing imports a network path.

What it pins, and why each one is worth a test:

* `translate_query` — the whole mail dialect, one pure function. Every token
  agents are taught, free text becoming AQS, and an unknown token becoming
  free text RATHER than silently vanishing (a dropped token widens the set a
  bulk dismissal pins; a search term narrows it, and narrowing is the safe
  direction).
* the id rule (D4) — `uuid` is the mutable EWS ItemId, `message_id` is the RFC
  822 InternetMessageId. Swapping them re-enrolls every filed message.
* the record shape — identical to the Gmail façade's, headers included, so no
  caller can tell which provider answered.
* the writes — a move resolves its folder generously, and a reply's
  In-Reply-To/References come from the REFERENCED MESSAGE'S own headers.
* the adapter — the global trust knobs actually reach the TLS layer.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pytest

from central_command.config import settings
from central_command.integrations import exchange


# --- the fake mailbox ---------------------------------------------------------


class FakeId:
    def __init__(self, value):
        self.id = value


class FakeMailbox:
    def __init__(self, email_address, name=""):
        self.email_address = email_address
        self.name = name


class FakeHeader:
    def __init__(self, name, value):
        self.name = name
        self.value = value


class FakeMessage:
    def __init__(self, item_id="AAA=", message_id="<a@corp.example>", subject="Budget",
                 sender="dana.rivers@example.com", conversation="conv-1",
                 received=None, text="plain words", html="<p>plain words</p>",
                 headers=None, references=""):
        self.id = FakeId(item_id)
        self.message_id = message_id
        self.subject = subject
        self.sender = FakeMailbox(sender, "Dana Rivers")
        self.conversation_id = FakeId(conversation)
        self.datetime_received = received or datetime(2026, 9, 25, 14, 30, tzinfo=timezone.utc)
        self.datetime_sent = self.datetime_received
        self.text_body = text
        self.body = html
        self.headers = headers if headers is not None else []
        self.references = references
        self.to_recipients = [FakeMailbox("operator@example.com")]
        self.in_reply_to = None
        self.moved_to = None
        self.sent = False

    def move(self, folder):
        self.moved_to = folder
        self.id = FakeId(self.id.id + "-moved")

    def send(self, save_copy=True, copy_to_folder=None):
        self.sent = True


class FakeQuery:
    """A queryset that records what it was asked and slices a list."""

    def __init__(self, items, folder=None):
        self.items = list(items)
        self.folder = folder

    def only(self, *args):
        return self

    def order_by(self, *args):
        return self

    def __iter__(self):
        return iter(self.items)

    def __getitem__(self, key):
        return self.items[key] if isinstance(key, int) else FakeQuery(self.items[key])


class FakeFolder:
    def __init__(self, name, items=(), path=None, total=0, unread=0, children=()):
        self.name = name
        self.absolute = path if path is not None else f"/root/{name}"
        self.total_count = total
        self.unread_count = unread
        self.items = list(items)
        self.children = list(children)
        self.last_filter = None

    def walk(self):
        for child in self.children:
            yield child
            yield from child.walk()

    def filter(self, *args, **kwargs):
        self.last_filter = (args, kwargs)
        return FakeQuery(self.items, self)

    def all(self):
        self.last_filter = ((), {})
        return FakeQuery(self.items, self)

    def view(self, start=None, end=None, max_items=None):
        self.last_filter = ("view", start, end)
        return FakeQuery(self.items, self)


class FakeAccount:
    def __init__(self, inbox_items=(), calendar_items=(), folders=()):
        self.inbox = FakeFolder("Inbox", inbox_items, path="/root/Inbox", total=42, unread=3)
        self.junk = FakeFolder("Junk Email", path="/root/Junk Email")
        self.trash = FakeFolder("Deleted Items", path="/root/Deleted Items")
        self.drafts = FakeFolder("Drafts", path="/root/Drafts")
        self.sent = FakeFolder("Sent Items", path="/root/Sent Items")
        self.archive_msg_folder_root = FakeFolder("Archive", path="/root/Archive")
        self.calendar = FakeFolder("Calendar", calendar_items, path="/root/Calendar")
        extra = list(folders) or [FakeFolder("Invoices", path="/root/Invoices",
                                             total=7, unread=1)]
        self.msg_folder_root = FakeFolder(
            "Top of Information Store", path="/root",
            children=[self.inbox, self.junk, self.sent, *extra],
        )
        self.fetched = []

    def fetch(self, ids=None, **kwargs):
        wanted = {getattr(i, "id", i) for i in (ids or [])}
        self.fetched.append(wanted)
        for folder in (self.inbox, self.junk, self.sent, self.calendar):
            for item in folder.items:
                if item.id.id in wanted:
                    yield item


@pytest.fixture
def mailbox(monkeypatch):
    """A configured deployment with a fake `Account` — every public helper in
    the module goes through `exchange.account()`, so this is the one seam."""
    monkeypatch.setattr(settings, "exchange_url",
                        "https://mail.corp.example/ews/exchange.asmx", raising=False)
    monkeypatch.setattr(settings, "exchange_username", "CORP\\operator", raising=False)
    monkeypatch.setattr(settings, "exchange_password", "secret", raising=False)
    monkeypatch.setattr(settings, "exchange_email", "operator@corp.example", raising=False)

    account = FakeAccount(inbox_items=[FakeMessage()])
    monkeypatch.setattr(exchange, "account", lambda: account)
    return account


# --- configured() -------------------------------------------------------------


def test_configured_needs_url_username_and_password(monkeypatch):
    monkeypatch.setattr(settings, "exchange_url", "", raising=False)
    monkeypatch.setattr(settings, "exchange_username", "", raising=False)
    monkeypatch.setattr(settings, "exchange_password", "", raising=False)
    assert exchange.configured() is False

    monkeypatch.setattr(settings, "exchange_url", "https://mail/ews/exchange.asmx",
                        raising=False)
    monkeypatch.setattr(settings, "exchange_username", "CORP\\operator", raising=False)
    # Two of three is NOT configured: a half-set mailbox must route to the n8n
    # path, not fail every mail call with an auth error.
    assert exchange.configured() is False
    monkeypatch.setattr(settings, "exchange_password", "secret", raising=False)
    assert exchange.configured() is True


def test_the_email_address_falls_back_to_a_upn_username(monkeypatch):
    monkeypatch.setattr(settings, "exchange_email", "", raising=False)
    monkeypatch.setattr(settings, "exchange_username", "operator@corp.example",
                        raising=False)
    assert exchange._email_address() == "operator@corp.example"

    # A DOMAIN\user username is not an address — say so here rather than
    # letting exchangelib ValueError out of a thread.
    monkeypatch.setattr(settings, "exchange_username", "CORP\\operator", raising=False)
    with pytest.raises(exchange.ExchangeError, match="CC_EXCHANGE_EMAIL"):
        exchange._email_address()


# --- translate_query (D5) -----------------------------------------------------


def test_translate_query_reads_every_token_it_teaches():
    filters, aqs, folder = exchange.translate_query(
        "from:dana@example.com to:ops@example.com subject:Budget "
        "after:2026/09/01 before:2026/09/30 in:Invoices"
    )
    assert folder == "Invoices"
    assert aqs is None
    assert filters["sender"] == "dana@example.com"
    assert filters["to_recipients"] == "ops@example.com"
    assert filters["subject__icontains"] == "Budget"
    assert filters["datetime_received__gt"] == datetime(2026, 9, 1, tzinfo=timezone.utc)
    assert filters["datetime_received__lt"] == datetime(2026, 9, 30, tzinfo=timezone.utc)


def test_translate_query_handles_the_default_feed_query():
    """`CC_FEED_QUERY` defaults to exactly this, and it must work unchanged on
    Exchange — that is the whole point of translating rather than re-teaching."""
    filters, aqs, folder = exchange.translate_query("in:inbox newer_than:1d")
    assert folder == "inbox"
    assert aqs is None
    cut = filters["datetime_received__gt"]
    assert timedelta(hours=23) < datetime.now(timezone.utc) - cut < timedelta(hours=25)


def test_translate_query_reads_older_than_as_the_other_bound():
    filters, _, _ = exchange.translate_query("older_than:30d")
    assert "datetime_received__lt" in filters


def test_translate_query_accepts_the_backlog_sweepers_epoch_dates():
    """`ingest/feed.py`'s sweeper emits `after:<epoch> before:<epoch>`, not
    YYYY/MM/DD — both spellings are live, so both must parse."""
    filters, _, _ = exchange.translate_query("after:1700000000 before:1700086400")
    assert filters["datetime_received__gt"] == datetime(2023, 11, 14, 22, 13, 20,
                                                       tzinfo=timezone.utc)
    assert filters["datetime_received__lt"] == datetime(2023, 11, 15, 22, 13, 20,
                                                        tzinfo=timezone.utc)


def test_translate_query_quotes_survive_a_phrase():
    filters, aqs, _ = exchange.translate_query('subject:"Your weekly digest"')
    assert filters["subject__icontains"] == "Your weekly digest"
    assert aqs is None


def test_free_text_becomes_an_aqs_query_string():
    filters, aqs, folder = exchange.translate_query("invoice overdue")
    assert (filters, folder) == ({}, None)
    assert aqs == "invoice overdue"


def test_an_unknown_token_becomes_free_text_rather_than_vanishing():
    """A dropped token WIDENS the set a bulk dismissal pins; a search term
    narrows it. Gmail's `-` negation and `category:` land here on purpose."""
    filters, aqs, _ = exchange.translate_query("-category:promotions has:attachment")
    assert filters == {}
    assert "-category:promotions" in aqs and "has:attachment" in aqs


def test_a_malformed_newer_than_is_free_text_not_a_crash():
    _, aqs, _ = exchange.translate_query("newer_than:soon")
    assert aqs == "newer_than:soon"


def test_a_bad_date_is_refused_loudly():
    with pytest.raises(exchange.ExchangeError, match="not a real calendar date"):
        exchange.translate_query("after:2026/13/45")


# --- the record shape and the id rule (D4) ------------------------------------


@pytest.mark.asyncio
async def test_get_message_returns_the_gmail_facade_record_shape(mailbox):
    record = await exchange.get_message("AAA=")
    assert set(record) == {
        "uuid", "message_id", "conversation_id", "from", "subject", "date",
        "body_text", "body_html", "snippet", "list_unsubscribe",
        "list_unsubscribe_post", "authentication_results", "dkim_signatures",
    }
    assert record["from"] == "Dana Rivers <dana.rivers@example.com>"
    assert record["subject"] == "Budget"
    assert record["date"] == "2026-09-25T14:30:00Z"
    assert record["body_text"] == "plain words"
    assert record["body_html"] == "<p>plain words</p>"
    assert record["conversation_id"] == "conv-1"


@pytest.mark.asyncio
async def test_the_uuid_is_the_item_id_and_the_message_id_is_the_header(mailbox):
    """THE id rule. The ItemId is a fetch handle that changes on a move; the
    RFC 822 InternetMessageId is what the ledger keys on, kept exactly."""
    record = await exchange.get_message("AAA=")
    assert record["uuid"] == "AAA="
    assert record["message_id"] == "<a@corp.example>"


@pytest.mark.asyncio
async def test_headers_become_the_unsubscribe_and_dkim_fields(mailbox):
    mailbox.inbox.items[0].headers = [
        FakeHeader("List-Unsubscribe", "<https://news.example/u/1>"),
        FakeHeader("List-Unsubscribe-Post", "List-Unsubscribe=One-Click"),
        FakeHeader("Authentication-Results", "mx.google.com; dkim=pass"),
        FakeHeader("DKIM-Signature", "v=1; a=rsa-sha256"),
        FakeHeader("Subject", "Budget"),
    ]
    record = await exchange.get_message("AAA=")
    assert record["list_unsubscribe"] == "<https://news.example/u/1>"
    assert record["list_unsubscribe_post"] == "List-Unsubscribe=One-Click"
    assert record["authentication_results"] == ["mx.google.com; dkim=pass"]
    assert record["dkim_signatures"] == ["v=1; a=rsa-sha256"]


@pytest.mark.asyncio
async def test_missing_headers_read_as_absent_never_as_eligible(mailbox):
    """`contract.mail` reads an empty `authentication_results` as "not
    eligible". Inventing one here would manufacture an unsubscribe verdict."""
    record = await exchange.get_message("AAA=")
    assert record["list_unsubscribe"] == ""
    assert record["authentication_results"] == []
    assert record["dkim_signatures"] == []


@pytest.mark.asyncio
async def test_a_missing_message_is_an_exchange_error(mailbox):
    with pytest.raises(exchange.ExchangeError, match="not found"):
        await exchange.get_message("NOPE=")


# --- list_refs ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_refs_carries_the_message_id_alongside_the_uuid(mailbox):
    refs = await exchange.list_refs("in:inbox newer_than:1d")
    assert refs == [{"uuid": "AAA=", "conversation_id": "conv-1",
                     "message_id": "<a@corp.example>"}]


@pytest.mark.asyncio
async def test_list_refs_applies_the_tokens_client_side_under_an_aqs_search(mailbox):
    """EWS refuses a QueryString beside field restrictions, so the AQS goes to
    the server and the translated tokens are applied to what comes back."""
    mailbox.inbox.items = [
        FakeMessage(item_id="AAA=", sender="dana@example.com"),
        FakeMessage(item_id="BBB=", message_id="<b@corp.example>",
                    sender="someone-else@example.com"),
    ]
    refs = await exchange.list_refs("from:dana@example.com invoice")
    assert mailbox.inbox.last_filter[0] == ("invoice",)
    assert [r["uuid"] for r in refs] == ["AAA="]


# --- folders ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_folders_reports_names_paths_and_counts(mailbox):
    folders = await exchange.list_folders()
    by_name = {f["name"]: f for f in folders}
    assert by_name["Inbox"]["path"] == "/root/Inbox"
    assert (by_name["Inbox"]["total"], by_name["Inbox"]["unread"]) == (42, 3)
    assert by_name["Invoices"]["total"] == 7


def test_a_folder_resolves_by_distinguished_name_case_insensitively(mailbox):
    assert exchange._resolve_folder(mailbox, "JunkEmail") is mailbox.junk
    assert exchange._resolve_folder(mailbox, "deleteditems") is mailbox.trash
    assert exchange._resolve_folder(mailbox, "Sent Items") is mailbox.sent


def test_a_folder_resolves_by_display_name_and_by_path(mailbox):
    invoices = exchange._resolve_folder(mailbox, "invoices")
    assert invoices.name == "Invoices"
    assert exchange._resolve_folder(mailbox, "/root/Invoices") is invoices


def test_an_unknown_folder_names_what_does_exist(mailbox):
    """The "never guess X" rule: the refusal has to be actionable."""
    with pytest.raises(exchange.ExchangeError) as e:
        exchange._resolve_folder(mailbox, "Archive 2019")
    assert "Invoices" in str(e.value) and "mail_list_folders" in str(e.value)


# --- writes -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_report_spam_moves_to_junk(mailbox):
    out = await exchange.report_spam("AAA=")
    assert out == {"ok": True, "folder": "Junk Email"}
    assert mailbox.inbox.items[0].moved_to is mailbox.junk


@pytest.mark.asyncio
async def test_move_by_display_name_returns_the_new_uuid(mailbox):
    out = await exchange.move("AAA=", "Invoices")
    assert out["ok"] is True and out["folder"] == "Invoices"
    # A move MINTS a new ItemId — the reason the ledger keys on the header id.
    assert out["uuid"] == "AAA=-moved"


@pytest.mark.asyncio
async def test_move_by_distinguished_name(mailbox):
    out = await exchange.move("AAA=", "deleteditems")
    assert mailbox.inbox.items[0].moved_to is mailbox.trash
    assert out["folder"] == "Deleted Items"


@pytest.mark.asyncio
async def test_send_threads_a_reply_from_the_referenced_messages_own_headers(mailbox, monkeypatch):
    """An id an agent could write into its own arguments is an agent-authored
    claim — the threading headers come from the FETCHED message, and only from
    there."""
    mailbox.inbox.items = [FakeMessage(item_id="AAA=", message_id="<parent@corp.example>",
                                       references="<older@corp.example>")]
    built = {}

    def fake_message(**kwargs):
        message = FakeMessage(**{k: v for k, v in kwargs.items()
                                 if k in ("subject",)})
        message.to_recipients = kwargs.get("to_recipients")
        message.cc_recipients = kwargs.get("cc_recipients")
        message.body = kwargs.get("body")
        built["message"] = message
        return message

    import exchangelib
    monkeypatch.setattr(exchangelib, "Message", fake_message)

    out = await exchange.send(["ops@example.com"], "Re: Budget", "on it",
                              cc=["dana@example.com"], reply_to_ref="AAA=")
    sent = built["message"]
    assert sent.sent is True
    assert sent.in_reply_to == "<parent@corp.example>"
    assert sent.references == "<older@corp.example> <parent@corp.example>"
    assert out["in_reply_to"] == "<parent@corp.example>"
    assert out["to"] == ["ops@example.com"] and out["cc"] == ["dana@example.com"]


@pytest.mark.asyncio
async def test_send_without_a_reply_ref_sets_no_threading_headers(mailbox, monkeypatch):
    built = {}

    def fake_message(**kwargs):
        message = FakeMessage(subject=kwargs.get("subject"))
        built["message"] = message
        return message

    import exchangelib
    monkeypatch.setattr(exchangelib, "Message", fake_message)

    out = await exchange.send(["ops@example.com"], "Hello", "text")
    assert built["message"].in_reply_to is None
    assert out["in_reply_to"] == ""


# --- calendar -----------------------------------------------------------------


class FakeEvent:
    def __init__(self, subject="Standup", start=None, end=None, organizer="operator@corp.example",
                 attendees=(), location="Room 1", body="agenda", declined=False,
                 all_day=False, item_id="EV1="):
        self.id = FakeId(item_id)
        self.subject = subject
        self.start = start or datetime(2026, 9, 25, 9, 0, tzinfo=timezone.utc)
        self.end = end or datetime(2026, 9, 25, 9, 15, tzinfo=timezone.utc)
        self.is_all_day = all_day
        self.location = location
        self.organizer = FakeMailbox(organizer)
        self.required_attendees = list(attendees)
        self.optional_attendees = []
        self.body = body
        self.my_response_type = "Decline" if declined else "Accept"


@pytest.mark.asyncio
async def test_list_events_returns_the_calendar_facade_event_shape(mailbox):
    mailbox.calendar.items = [FakeEvent(attendees=["a@x.example", "b@x.example"])]
    out = await exchange.list_events("2026-09-25T00:00:00Z", "2026-09-26T00:00:00Z")
    assert out["truncated"] is False
    event = out["events"][0]
    assert set(event) == {"event_id", "summary", "start", "end", "all_day", "location",
                          "attendee_count", "organizer_is_self", "has_agenda", "declined"}
    assert event["summary"] == "Standup"
    assert event["start"] == "2026-09-25T09:00:00Z"
    assert event["attendee_count"] == 2
    assert event["organizer_is_self"] is True
    assert event["has_agenda"] is True
    assert event["declined"] is False


@pytest.mark.asyncio
async def test_a_window_without_an_offset_is_refused(mailbox):
    """Instants cross this seam, never local wall-clock times — the façade's
    own contract, and a naive string is exactly how a brief slips a day."""
    with pytest.raises(exchange.ExchangeError, match="no offset"):
        await exchange.list_events("2026-09-25T00:00:00", "2026-09-26T00:00:00Z")


@pytest.mark.asyncio
async def test_list_events_uses_calendarview_so_recurrences_expand(mailbox):
    mailbox.calendar.items = [FakeEvent()]
    await exchange.list_events("2026-09-25T00:00:00Z", "2026-09-26T00:00:00Z")
    assert mailbox.calendar.last_filter[0] == "view"


# --- the trust adapter (D3) ---------------------------------------------------


class _Captured(Exception):
    pass


def _capture_adapter(monkeypatch):
    """Instantiate the real adapter subclass and capture what it hands the
    requests layer, without opening a socket."""
    import requests.adapters

    seen = {}

    def fake_cert_verify(self, conn, url, verify, cert):
        seen["verify"] = verify
        seen["cert"] = cert

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "cert_verify", fake_cert_verify)
    adapter = exchange._adapter_cls()()
    adapter.cert_verify(conn=None, url="https://mail.corp.example/ews/exchange.asmx",
                        verify=True, cert=None)
    return seen


def test_the_adapter_injects_the_ca_bundle_and_client_certificate(monkeypatch):
    monkeypatch.setattr(settings, "ca_bundle", "/etc/cc/corp-root.pem", raising=False)
    monkeypatch.setattr(settings, "client_cert", "/etc/cc/client.pem", raising=False)
    monkeypatch.setattr(settings, "client_key", "/etc/cc/client.key", raising=False)
    monkeypatch.delenv("CC_TLS_INSECURE", raising=False)
    seen = _capture_adapter(monkeypatch)
    assert seen["verify"] == "/etc/cc/corp-root.pem"
    assert seen["cert"] == ("/etc/cc/client.pem", "/etc/cc/client.key")


def test_the_adapter_defaults_to_the_system_trust_store(monkeypatch):
    monkeypatch.setattr(settings, "ca_bundle", "", raising=False)
    monkeypatch.setattr(settings, "client_cert", "", raising=False)
    monkeypatch.setattr(settings, "client_key", "", raising=False)
    monkeypatch.delenv("CC_TLS_INSECURE", raising=False)
    seen = _capture_adapter(monkeypatch)
    assert seen["verify"] is True and seen["cert"] is None


def test_insecure_turns_verification_off_and_warns_exactly_once(monkeypatch, caplog):
    monkeypatch.setattr(settings, "ca_bundle", "", raising=False)
    monkeypatch.setattr(settings, "client_cert", "", raising=False)
    monkeypatch.setenv("CC_TLS_INSECURE", "1")
    monkeypatch.setattr(exchange, "_insecure_warned", False)
    with caplog.at_level(logging.WARNING):
        exchange._trust()
        exchange._trust()
        exchange._trust()
    assert exchange._trust()[1] is False
    warnings = [r for r in caplog.records if "CC_TLS_INSECURE" in r.getMessage()]
    assert len(warnings) == 1, "a per-request warning is noise nobody reads"
