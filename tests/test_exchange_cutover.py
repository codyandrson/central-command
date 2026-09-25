"""The cutover: one signature, two providers (Exchange native client design, D1).

`email_facade.py` and `calendar_facade.py` keep every signature and every
caller; each function asks `exchange.configured()` and routes. The routing
lives INSIDE the function, not in a decorator, so a test that patches
`email_facade.list_refs` still patches the whole thing.

What is worth pinning here is the SWITCH, not the clients (those are
`test_exchange.py`'s job):

* with Exchange configured, each façade function reaches `exchange.*` and no
  HTTP happens at all;
* `ExchangeError` becomes `EmailFacadeError` / `CalendarFacadeError`, so every
  existing `except` in the codebase keeps covering both providers;
* with it unset, the n8n webhook is called exactly as before — byte-for-byte,
  which is what makes unsetting the keys a real rollback;
* the packs withhold per provider, and the toolset, the charter and the
  gateway's granted-capability check agree about it;
* the ledger prefers a supplied Message-ID and stamps the right `source`;
* the two new capabilities have a spec, a handler and a registry row.
"""

from __future__ import annotations

import json

import pytest

from central_command.config import settings
from central_command.contract.args import ARG_SPECS
from central_command.gateway.capabilities import REGISTRY
from central_command.gateway.executor import HANDLERS as EXECUTOR_HANDLERS
from central_command.ingest import ledger
from central_command.integrations import calendar_facade, email_facade, exchange
from central_command.runtime import packs


@pytest.fixture
def on_exchange(monkeypatch):
    monkeypatch.setattr(settings, "exchange_url",
                        "https://mail.corp.example/ews/exchange.asmx", raising=False)
    monkeypatch.setattr(settings, "exchange_username", "operator@corp.example",
                        raising=False)
    monkeypatch.setattr(settings, "exchange_password", "secret", raising=False)
    monkeypatch.setattr(settings, "exchange_email", "operator@corp.example",
                        raising=False)
    assert exchange.configured() is True


@pytest.fixture
def on_gmail(monkeypatch):
    monkeypatch.setattr(settings, "exchange_url", "", raising=False)
    monkeypatch.setattr(settings, "exchange_username", "", raising=False)
    monkeypatch.setattr(settings, "exchange_password", "", raising=False)
    assert exchange.configured() is False


@pytest.fixture
def no_http(monkeypatch):
    """Any HTTP at all under `on_exchange` is the routing having failed."""
    import httpx

    def boom(*args, **kwargs):
        raise AssertionError("the n8n façade was called on an Exchange deployment")

    monkeypatch.setattr(httpx.AsyncClient, "post", boom)


# --- email façade -------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("name,args,expected", [
    ("list_refs", ("in:inbox",), [{"uuid": "AAA=", "conversation_id": "c",
                                   "message_id": "<a@corp.example>"}]),
    ("get_message", ("AAA=",), {"uuid": "AAA=", "subject": "Budget"}),
    ("report_spam", ("AAA=",), {"ok": True, "folder": "Junk Email"}),
])
async def test_the_email_facade_routes_to_exchange(on_exchange, no_http, monkeypatch,
                                                   name, args, expected):
    async def fake(*a, **k):
        assert a == args
        return expected

    monkeypatch.setattr(exchange, name, fake)
    assert await getattr(email_facade, name)(*args) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("name,args", [
    ("list_refs", ("in:inbox",)),
    ("get_message", ("AAA=",)),
    ("report_spam", ("AAA=",)),
    ("send", (["a@x.example"], "s", "b")),
    ("move", ("AAA=", "Invoices")),
    ("list_folders", ()),
])
async def test_an_exchange_error_becomes_an_email_facade_error(on_exchange, no_http,
                                                              monkeypatch, name, args):
    """Every caller in the codebase catches `EmailFacadeError`. If an
    `ExchangeError` escaped, the feed loop, `mail_search` and the dispatcher
    would each meet an exception type they do not handle."""
    async def boom(*a, **k):
        raise exchange.ExchangeError("EWS said no")

    monkeypatch.setattr(exchange, name, boom)
    with pytest.raises(email_facade.EmailFacadeError, match="EWS said no"):
        await getattr(email_facade, name)(*args)


@pytest.mark.asyncio
async def test_without_exchange_the_email_facade_still_calls_n8n(on_gmail, monkeypatch):
    """The rollback path is unsetting three keys — so the n8n request must be
    exactly what it always was."""
    seen = {}

    class FakeResponse:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {"messages": [{"uuid": "gmail-1", "conversation_id": "t1"}]}

    async def fake_post(self, url, headers=None, json=None, **kwargs):
        seen.update(url=url, headers=headers, payload=json)
        return FakeResponse()

    import httpx
    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    refs = await email_facade.list_refs("in:inbox newer_than:1d")
    assert refs == [{"uuid": "gmail-1", "conversation_id": "t1"}]
    assert seen["url"] == settings.email_facade_url
    assert seen["payload"] == {"mode": "list", "scope_query": "in:inbox newer_than:1d"}
    assert "x-cc-token" in seen["headers"]


@pytest.mark.asyncio
async def test_send_and_move_refuse_honestly_on_gmail(on_gmail):
    """There is no n8n fallback because the façade has no such mode. The
    refusal names the seam rather than failing somewhere inside httpx."""
    with pytest.raises(email_facade.EmailFacadeError, match="CC_EXCHANGE_URL"):
        await email_facade.send(["a@x.example"], "s", "b")
    with pytest.raises(email_facade.EmailFacadeError, match="CC_EXCHANGE_URL"):
        await email_facade.move("AAA=", "Invoices")


@pytest.mark.asyncio
async def test_list_folders_on_gmail_answers_with_labels(on_gmail):
    """"Folders are labels here" is a real answer; an error would teach the
    agent that the mailbox is broken rather than differently shaped."""
    folders = await email_facade.list_folders()
    addresses = {f["path"] for f in folders}
    assert "in:inbox" in addresses
    assert all(f["total"] is None for f in folders), "blank means not reported, not zero"


# --- calendar façade ----------------------------------------------------------


@pytest.mark.asyncio
async def test_the_calendar_facade_routes_every_mode_to_exchange(on_exchange, no_http,
                                                                 monkeypatch):
    calls = []

    async def fake_list(time_min, time_max, calendar_id="primary"):
        calls.append(("list_events", time_min, time_max, calendar_id))
        return {"events": [], "truncated": False}

    async def fake_create(*a, **k):
        calls.append(("create_event", a, k))
        return {"event_id": "EV1="}

    async def fake_update(*a, **k):
        calls.append(("update_event", a, k))
        return {"event_id": "EV1="}

    async def fake_delete(event_id, calendar_id="primary"):
        calls.append(("delete_event", event_id))
        return event_id

    monkeypatch.setattr(exchange, "list_events", fake_list)
    monkeypatch.setattr(exchange, "create_event", fake_create)
    monkeypatch.setattr(exchange, "update_event", fake_update)
    monkeypatch.setattr(exchange, "delete_event", fake_delete)

    assert await calendar_facade.list_events("2026-09-25T00:00:00Z",
                                             "2026-09-26T00:00:00Z") == {
        "events": [], "truncated": False}
    assert await calendar_facade.create_event("Standup", "2026-09-25T09:00:00Z",
                                              "2026-09-25T09:15:00Z") == {"event_id": "EV1="}
    assert await calendar_facade.update_event("EV1=", title="Standup!") == {"event_id": "EV1="}
    assert await calendar_facade.delete_event("EV1=") == "EV1="
    assert [c[0] for c in calls] == ["list_events", "create_event", "update_event",
                                     "delete_event"]


@pytest.mark.asyncio
async def test_an_exchange_error_becomes_a_calendar_facade_error(on_exchange, no_http,
                                                                 monkeypatch):
    async def boom(*a, **k):
        raise exchange.ExchangeError("EWS said no")

    monkeypatch.setattr(exchange, "list_events", boom)
    with pytest.raises(calendar_facade.CalendarFacadeError, match="EWS said no"):
        await calendar_facade.list_events("2026-09-25T00:00:00Z", "2026-09-26T00:00:00Z")


@pytest.mark.asyncio
async def test_without_exchange_the_calendar_facade_still_calls_n8n(on_gmail, monkeypatch):
    seen = {}

    class FakeResponse:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {"ok": True, "events": [{"summary": "Standup"}], "truncated": False}

    async def fake_post(self, url, headers=None, json=None, **kwargs):
        seen.update(url=url, payload=json)
        return FakeResponse()

    import httpx
    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    out = await calendar_facade.list_events("2026-09-25T00:00:00Z", "2026-09-26T00:00:00Z")
    assert out["events"] == [{"summary": "Standup"}]
    assert seen["url"] == settings.calendar_facade_url
    assert seen["payload"]["mode"] == "list"


# --- provider withholding (D6) ------------------------------------------------


EXCHANGE_ONLY_TOOLS = {"propose_mail_send", "propose_mail_move"}
GMAIL_ONLY_TOOLS = {"propose_unsubscribe"}
EXCHANGE_ONLY_CAPS = {"mail.send", "mail.move"}
GMAIL_ONLY_CAPS = {"mail.unsubscribe"}

MAIL_PACKS = ("mail-read", "mail-spam-propose", "mail-unsubscribe-propose",
              "mail-send-propose", "mail-file-propose", "mail-rule-propose")


def _offered_everything(pack_names):
    tools, caps = set(), set()
    for name in pack_names:
        offered_tools, offered_caps = packs._offered(packs.PACKS[name])
        tools |= set(offered_tools)
        caps |= {c.name for c in offered_caps}
    return tools, caps


def test_gmail_withholds_the_exchange_only_members(on_gmail):
    tools, caps = _offered_everything(MAIL_PACKS)
    assert not (tools & EXCHANGE_ONLY_TOOLS)
    assert not (caps & EXCHANGE_ONLY_CAPS)
    assert GMAIL_ONLY_TOOLS <= tools and GMAIL_ONLY_CAPS <= caps


def test_exchange_withholds_the_gmail_only_members(on_exchange):
    tools, caps = _offered_everything(MAIL_PACKS)
    assert not (tools & GMAIL_ONLY_TOOLS)
    assert not (caps & GMAIL_ONLY_CAPS)
    assert EXCHANGE_ONLY_TOOLS <= tools and EXCHANGE_ONLY_CAPS <= caps


def test_everything_else_is_offered_on_both_providers(on_gmail, monkeypatch):
    gmail_tools, gmail_caps = _offered_everything(MAIL_PACKS)
    monkeypatch.setattr(settings, "exchange_url", "https://mail/ews/exchange.asmx",
                        raising=False)
    monkeypatch.setattr(settings, "exchange_username", "operator@corp.example",
                        raising=False)
    monkeypatch.setattr(settings, "exchange_password", "secret", raising=False)
    exchange_tools, exchange_caps = _offered_everything(MAIL_PACKS)
    assert gmail_tools - GMAIL_ONLY_TOOLS == exchange_tools - EXCHANGE_ONLY_TOOLS
    assert gmail_caps - GMAIL_ONLY_CAPS == exchange_caps - EXCHANGE_ONLY_CAPS


@pytest.mark.parametrize("fixture,withheld", [("on_gmail", EXCHANGE_ONLY_CAPS),
                                              ("on_exchange", GMAIL_ONLY_CAPS)])
def test_granted_capability_names_agrees_with_offered(request, fixture, withheld):
    """The toolset, the generated charter and the gateway's policy check all go
    through `_offered` — if `granted_capability_names` disagreed, an agent
    could hold a tool whose capability the gateway then refused."""
    request.getfixturevalue(fixture)
    granted = packs.granted_capability_names(MAIL_PACKS)
    assert not (granted & withheld)


def test_a_withheld_capability_is_still_KNOWN(on_gmail):
    """Withheld is UNGRANTED, never INVENTED — `known_capability_names` is the
    propose-time validity check and must keep telling those two apart, or a
    proposal naming mail.send on Gmail would be rejected as a fabrication."""
    assert EXCHANGE_ONLY_CAPS <= packs.known_capability_names()


def test_the_toolset_can_be_assembled_on_both_providers(on_exchange):
    """`toolset_for` does `getattr(tools_mod, name)` — a pack naming a tool
    that does not exist fails here rather than at the first agent run."""
    assert packs.toolset_for(MAIL_PACKS) is not None


# --- the ledger's ids (D4) ----------------------------------------------------


def test_a_supplied_message_id_wins_over_the_synthesised_form():
    supplied = {"uuid": "AAA=", "conversation_id": "c", "message_id": "<a@corp.example>"}
    assert ledger.ref_message_id(supplied) == "<a@corp.example>"
    # Kept EXACTLY — it is an RFC 822 id, not a template to fill in.
    assert ledger._provider_parsed({**supplied, "subject": "s", "from": "f",
                                    "body_text": "", "body_html": "",
                                    "snippet": ""})["message_id"] == "<a@corp.example>"


def test_without_one_the_gmail_form_is_synthesised():
    assert ledger.ref_message_id({"uuid": "gmail-1"}) == \
        "<gmail-msg-gmail-1@central_command.feed>"
    assert ledger.ref_message_id({"uuid": "gmail-1", "message_id": ""}) == \
        "<gmail-msg-gmail-1@central_command.feed>"


def test_provider_source_follows_the_configured_mailbox(on_gmail, monkeypatch):
    assert ledger.provider_source() == "gmail"
    monkeypatch.setattr(settings, "exchange_url", "https://mail/ews/exchange.asmx",
                        raising=False)
    monkeypatch.setattr(settings, "exchange_username", "operator@corp.example",
                        raising=False)
    monkeypatch.setattr(settings, "exchange_password", "secret", raising=False)
    assert ledger.provider_source() == "exchange"


def test_the_thread_id_shape_is_unchanged():
    """Provider-neutral in NAMING only: changing the shape would orphan every
    existing thread lock."""
    assert ledger.provider_thread_id({"conversation_id": "c1"}) == \
        "<gmail-thread-c1@central_command.feed>"


# --- parity for the two new capabilities --------------------------------------


@pytest.mark.parametrize("name", sorted(EXCHANGE_ONLY_CAPS))
def test_the_new_capabilities_have_a_spec_a_handler_and_a_registry_row(name):
    assert name in ARG_SPECS, "the handler subscripts these — a spec is the promise"
    assert name in EXECUTOR_HANDLERS, "approved and then nothing executes it"
    row = next(c for c in REGISTRY if c.name == name)
    assert row.kind == "write" and row.gate == "human approval"
    assert row.holder == "Executor"
    assert row.risk.strip()


def test_mail_send_is_declared_irreversible_in_its_risk_text():
    """The registry text is what the operator reads at approval. Sending is
    external and cannot be recalled, and the text has to say so."""
    row = next(c for c in REGISTRY if c.name == "mail.send")
    assert "IRREVERSIBLE" in row.risk and "external" in row.risk.lower()


def test_the_arg_specs_require_what_the_handlers_subscript():
    assert set(ARG_SPECS["mail.send"].required) == {"to", "subject", "body"}
    assert set(ARG_SPECS["mail.move"].required) == {"provider_uuid", "folder"}


def test_the_registry_routes_mention_the_exchange_seam():
    """One clause each, so the operator reading a decision knows which system
    the approval actually reaches."""
    for name in ("calendar.create_event", "calendar.update_event",
                 "calendar.delete_event", "calendar.list_events",
                 "mail.report_spam", "email.list_refs", "email.get_message"):
        row = next(c for c in REGISTRY if c.name == name)
        assert "exchange" in row.route.lower(), name


def test_the_new_capability_rows_serialise(on_exchange):
    """The registry is served to the cockpit as JSON; a non-serialisable field
    is a 500 at the Decisions pane, not at import."""
    rows = [c for c in REGISTRY if c.name in EXCHANGE_ONLY_CAPS]
    assert json.loads(json.dumps([r.__dict__ for r in rows]))
