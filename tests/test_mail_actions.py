"""mail actions (2026-09-12): report-spam and one-click unsubscribe. Offline —
façade, repo and the outbound POST are stubbed. The load-bearing checks: the
eligibility rule is decided from the message's own headers, and the Executor
re-derives the unsubscribe URL from the mailbox rather than trusting the
proposal's."""

from typing import ClassVar

import pytest
from pydantic_ai import CallDeferred, ModelRetry

from central_command.contract.mail import one_click_unsubscribe
from central_command.gateway import executor
from central_command.integrations import email_facade
from central_command.runtime import tools
from central_command.runtime.deps import TriageDeps

# Shaped like the façade's `message` mode after the 2026-09-12 edit; the
# header values are modelled on real Gmail-delivered mail.
ELIGIBLE = {
    "uuid": "u1", "from": "News <news@shop.example>", "subject": "Weekly",
    "list_unsubscribe": "<mailto:unsub@shop.example>, <https://shop.example/u/abc>",
    "list_unsubscribe_post": "List-Unsubscribe=One-Click",
    "authentication_results": [
        ("i=1; mx.google.com;       dkim=pass header.i=@shop.example header.s=s1 "
         "header.b=AbCd;       spf=pass (google.com: domain designates 1.2.3.4)"),
    ],
    "dkim_signatures": [
        ("v=1; a=rsa-sha256; c=relaxed/relaxed; d=shop.example; s=s1;        "
         "h=list-unsubscribe-post:list-unsubscribe:date:subject:to:from;        "
         "bh=x;        b=AbCdEfGh\n         IjKl"),
    ],
}
# The verdict's header.b= is the prefix of the covering signature's b= — the
# signature Gmail verified IS the one covering the headers.


class _Ctx:
    def __init__(self, item_id=None):
        self.deps = TriageDeps(item_id=item_id)


def test_eligibility_is_decided_from_the_headers():
    assert one_click_unsubscribe(ELIGIBLE) == (
        "https://shop.example/u/abc", "eligible (signed by shop.example)")


@pytest.mark.parametrize("change,why", [
    ({"list_unsubscribe": "<mailto:unsub@shop.example>"}, "mailto only"),
    ({"list_unsubscribe_post": ""}, "one-click"),
    ({"authentication_results": ["mx.google.com; dkim=fail header.i=@shop.example"]},
     "DKIM did not pass"),
    # A verdict under any other authserv-id is the sender's to forge.
    ({"authentication_results": ["relay.attacker.example; dkim=pass header.i=@shop.example"]},
     "no authentication verdict"),
    # So is one claiming mx.google.com that sits BELOW Gmail's own (Gmail
    # prepends; only the first counts).
    ({"authentication_results": ["mx.google.com; dkim=fail header.i=@shop.example",
                                 "mx.google.com; dkim=pass header.i=@shop.example header.b=AbCd"]},
     "DKIM did not pass"),
    ({"dkim_signatures": ["v=1; d=shop.example; h=from:subject:date; b=AbCdEfGh"]}, "covers"),
    # A covering signature that is NOT the one Gmail verified (b= mismatch).
    ({"dkim_signatures": ["v=1; d=attacker.example; "
                          "h=list-unsubscribe:list-unsubscribe-post:from; b=ZZZZ"]}, "covers"),
    ({"list_unsubscribe": "<https://127.0.0.1/u/abc>"}, "not a public hostname"),
    ({"list_unsubscribe": "<https://localhost/u/abc>"}, "not a public hostname"),
    ({"list_unsubscribe": "<https://[::1]/u/abc>"}, "not a public hostname"),
    # A façade predating the edit sends none of the fields: not eligible, never a crash.
    ({"list_unsubscribe": None, "list_unsubscribe_post": None,
      "authentication_results": None, "dkim_signatures": None}, "no https"),
])
def test_anything_short_of_rfc8058_is_ineligible(change, why):
    url, reason = one_click_unsubscribe({**ELIGIBLE, **change})
    assert url is None and why in reason


@pytest.mark.asyncio
async def test_propose_report_spam_pins_the_item_the_run_is_handling(monkeypatch):
    from central_command.db import repo

    async def item(ref):
        assert ref == "wi_1"
        return {"id": "wi_1", "provider_uuid": "u1",
                "sender": "Prize Desk <x@spam.example>", "subject": "You won"}

    monkeypatch.setattr(repo, "mail_item", item)
    with pytest.raises(CallDeferred) as exc:
        await tools.propose_report_spam(_Ctx(item_id="wi_1"), "unsolicited prize scam")
    action = exc.value.metadata["proposal"]["actions"][0]
    assert action["capability"] == "mail.report_spam@v1"
    assert action["arguments"]["provider_uuid"] == "u1"
    assert action["arguments"]["sender"] == "Prize Desk <x@spam.example>"
    assert action["reversibility"] == "reversible"


@pytest.mark.asyncio
async def test_propose_report_spam_refuses_a_hand_fed_item(monkeypatch):
    from central_command.db import repo

    async def item(ref):
        return {"id": "wi_2", "provider_uuid": None, "sender": "", "subject": ""}

    monkeypatch.setattr(repo, "mail_item", item)
    with pytest.raises(ModelRetry):
        await tools.propose_report_spam(_Ctx(item_id="wi_2"), "spam")


@pytest.mark.asyncio
async def test_propose_unsubscribe_pins_the_url_it_read(monkeypatch):
    from central_command.db import repo

    async def item(ref):
        return {"id": "wi_1", "provider_uuid": "u1",
                "sender": "News <news@shop.example>", "subject": "Weekly"}

    async def get_message(uuid):
        assert uuid == "u1"
        return ELIGIBLE

    monkeypatch.setattr(repo, "mail_item", item)
    monkeypatch.setattr(email_facade, "get_message", get_message)
    with pytest.raises(CallDeferred) as exc:
        await tools.propose_unsubscribe(_Ctx(item_id="wi_1"), "never wanted this list")
    action = exc.value.metadata["proposal"]["actions"][0]
    assert action["capability"] == "mail.unsubscribe@v1"
    assert action["arguments"]["url"] == "https://shop.example/u/abc"
    assert action["reversibility"] == "irreversible"


@pytest.mark.asyncio
async def test_propose_unsubscribe_proposes_nothing_without_one_click(monkeypatch):
    from central_command.db import repo

    async def item(ref):
        return {"id": "wi_1", "provider_uuid": "u1", "sender": "N", "subject": "W"}

    async def get_message(uuid):
        return {**ELIGIBLE, "list_unsubscribe_post": ""}

    monkeypatch.setattr(repo, "mail_item", item)
    monkeypatch.setattr(email_facade, "get_message", get_message)
    out = await tools.propose_unsubscribe(_Ctx(item_id="wi_1"), "never wanted this list")
    assert "not available" in out and "Nothing proposed" in out


class _Client:
    """Stands in for httpx.AsyncClient: records the POST, answers `status`."""
    status = 200
    posted: ClassVar[list] = []
    kwargs: ClassVar[dict] = {}

    def __init__(self, **kw):
        _Client.kwargs = kw

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, **kw):
        _Client.posted.append((url, kw))

        class R:
            status_code = _Client.status
        return R()


@pytest.fixture
def outbound(monkeypatch):
    _Client.posted.clear()
    _Client.status = 200
    monkeypatch.setattr(executor.httpx, "AsyncClient", _Client)

    async def resolves_public(url):
        return None

    monkeypatch.setattr(executor, "_refuse_non_public", resolves_public)
    return _Client


class _Loop:
    def __init__(self, addrs):
        self.addrs = addrs

    async def getaddrinfo(self, host, port, **kw):
        return [(None, None, None, "", (a, port)) for a in self.addrs]


@pytest.mark.parametrize("addr", ["127.0.0.1", "10.0.0.5", "100.113.118.28", "169.254.1.1", "::1", "fd00::1"])
@pytest.mark.asyncio
async def test_executor_refuses_a_host_that_resolves_inward(monkeypatch, addr):
    class A:
        @staticmethod
        def get_running_loop():
            return _Loop(["93.184.216.34", addr])

    monkeypatch.setattr(executor, "asyncio", A)
    with pytest.raises(executor.ExecutorError):
        await executor._refuse_non_public("https://shop.example/u/abc")


@pytest.mark.asyncio
async def test_executor_accepts_a_host_that_resolves_public(monkeypatch):
    class A:
        @staticmethod
        def get_running_loop():
            return _Loop(["93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"])

    monkeypatch.setattr(executor, "asyncio", A)
    await executor._refuse_non_public("https://shop.example/u/abc")


@pytest.mark.asyncio
async def test_executor_posts_a_bare_one_click_to_the_messages_own_url(monkeypatch, outbound):
    async def get_message(uuid):
        return ELIGIBLE

    monkeypatch.setattr(email_facade, "get_message", get_message)
    args = {"provider_uuid": "u1", "url": "https://shop.example/u/abc", "sender": "News"}
    out = await executor._mail_unsubscribe(args, "operator", "inbox-triage")
    assert "HTTP 200" in out
    (url, kw), = outbound.posted
    assert url == "https://shop.example/u/abc"
    assert kw["content"] == b"List-Unsubscribe=One-Click"
    assert outbound.kwargs["follow_redirects"] is False


@pytest.mark.asyncio
async def test_executor_refuses_a_url_the_message_does_not_carry(monkeypatch, outbound):
    async def get_message(uuid):
        return ELIGIBLE

    monkeypatch.setattr(email_facade, "get_message", get_message)
    args = {"provider_uuid": "u1", "url": "https://attacker.example/x"}
    with pytest.raises(executor.ExecutorError):
        await executor._mail_unsubscribe(args, "operator", "inbox-triage")
    assert outbound.posted == []


@pytest.mark.asyncio
async def test_executor_refuses_when_the_mailbox_says_ineligible(monkeypatch, outbound):
    async def get_message(uuid):
        return {**ELIGIBLE, "authentication_results": []}

    monkeypatch.setattr(email_facade, "get_message", get_message)
    args = {"provider_uuid": "u1", "url": "https://shop.example/u/abc"}
    with pytest.raises(executor.ExecutorError):
        await executor._mail_unsubscribe(args, "operator", "inbox-triage")
    assert outbound.posted == []


@pytest.mark.asyncio
async def test_executor_treats_a_redirect_as_failure(monkeypatch, outbound):
    async def get_message(uuid):
        return ELIGIBLE

    monkeypatch.setattr(email_facade, "get_message", get_message)
    outbound.status = 302
    with pytest.raises(executor.ExecutorError):
        await executor._mail_unsubscribe(
            {"provider_uuid": "u1", "url": "https://shop.example/u/abc"}, "operator", None)


@pytest.mark.asyncio
async def test_executor_reports_spam_through_the_facade(monkeypatch):
    calls = []

    async def report_spam(uuid):
        calls.append(uuid)
        return {"ok": True, "label_ids": ["SPAM"]}

    monkeypatch.setattr(email_facade, "report_spam", report_spam)
    out = await executor._mail_report_spam(
        {"provider_uuid": "u1", "sender": "S", "subject": "T"}, "operator", "inbox-triage")
    assert calls == ["u1"] and "SPAM" in out
