"""An unconfigured n8n façade fails instead of parking (epic small-fixes story
11.10, R30).

Before this, `calendar_facade` had no configured check and its default URL is
never empty, so an unconfigured deployment POSTed to a loopback port and the
resulting transport error read as an outage: the approval parked for a retry
that could never succeed. Now `configured()` is url AND token, `_call` raises
"not configured" first (no transient marker word, so it classifies SEMANTIC and
the approval lands FAILED), and a configured façade whose n8n is DOWN still
parks TRANSIENT.
"""

from __future__ import annotations

import httpx
import pytest

from central_command.config import settings
from central_command.contract import failures
from central_command.integrations import calendar_facade, email_facade, exchange
from tests.conftest import needs_pg
from tests.test_no_live_world import _approve, _one_action_model

URL = {
    email_facade: ("email_facade_url", "email_facade_token",
                   "http://n8n.example.com:5678/webhook/cc-email-facade"),
    calendar_facade: ("calendar_facade_url", "calendar_facade_token",
                      "http://n8n.example.com:5678/webhook/cc-calendar-facade"),
}
CALLS = {
    email_facade: lambda: email_facade._call({"mode": "report_spam"}),
    calendar_facade: lambda: calendar_facade._call({"mode": "create"}),
}


# The two write capabilities that reach the n8n path. `mail.send` is not one:
# the Gmail façade has no send mode, so on n8n it refuses with its own
# "needs a native mailbox client" text before any façade call (the ticket named
# mail.send; `mail.report_spam` is the n8n mail write).
APPROVALS = [
    ("mail.report_spam", {"provider_uuid": "msg-1"}, email_facade),
    ("calendar.create_event", {"title": "Sync", "start": "2026-11-02T10:00:00Z",
                               "end": "2026-11-02T10:30:00Z"}, calendar_facade),
]


@pytest.fixture(autouse=True)
def on_n8n(monkeypatch):
    monkeypatch.setattr(settings, "test_model", True)
    for name in ("exchange_url", "exchange_username", "exchange_password"):
        monkeypatch.setattr(settings, name, "", raising=False)
    assert exchange.configured() is False


@pytest.mark.parametrize("module", [email_facade, calendar_facade])
def test_configured_needs_url_and_token(module, monkeypatch):
    url_key, token_key, url = URL[module]
    monkeypatch.setattr(settings, url_key, url)
    monkeypatch.setattr(settings, token_key, "")
    assert module.configured() is False
    monkeypatch.setattr(settings, token_key, "t")
    assert module.configured() is True
    monkeypatch.setattr(settings, url_key, "")
    assert module.configured() is False


@pytest.mark.asyncio
@pytest.mark.parametrize("module", [email_facade, calendar_facade])
async def test_unconfigured_raises_a_semantic_error_before_any_socket(module, monkeypatch):
    url_key, token_key, url = URL[module]
    monkeypatch.setattr(settings, url_key, url)  # the default URL is never empty
    monkeypatch.setattr(settings, token_key, "")

    def boom(*a, **k):
        raise AssertionError("an unconfigured façade must not open a client")

    monkeypatch.setattr(httpx, "AsyncClient", boom)
    error = {email_facade: email_facade.EmailFacadeError,
             calendar_facade: calendar_facade.CalendarFacadeError}[module]
    with pytest.raises(error, match="not configured") as caught:
        await CALLS[module]()
    assert failures.classify_failure(caught.value) == failures.SEMANTIC
    assert failures.classify_failure_text(str(caught.value)) == failures.SEMANTIC


@pytest.mark.asyncio
@pytest.mark.parametrize("module", [email_facade, calendar_facade])
async def test_configured_but_down_stays_transient(module, monkeypatch):
    url_key, token_key, url = URL[module]
    monkeypatch.setattr(settings, url_key, url)
    monkeypatch.setattr(settings, token_key, "t")

    async def refuse(self, *a, **k):
        raise httpx.ConnectError("All connection attempts failed")  # httpx's own text for a down host

    monkeypatch.setattr(httpx.AsyncClient, "post", refuse)
    with pytest.raises(Exception, match="unreachable at") as caught:
        await CALLS[module]()
    assert failures.classify_failure(caught.value) == failures.TRANSIENT


@needs_pg
@pytest.mark.parametrize("capability, arguments, module", APPROVALS)
async def test_an_unconfigured_facade_approval_lands_failed_not_configured(
    capability, arguments, module,
):
    out, row = await _approve(_one_action_model(capability, arguments))
    assert out["ok"] is False and out["failed_capability"] == capability
    assert "not configured" in out["error"]
    assert row["status"] == "FAILED", row["status"]  # never RETRY_PENDING


@needs_pg
@pytest.mark.parametrize("capability, arguments, module", APPROVALS)
async def test_a_configured_facade_with_n8n_down_parks_transient(
    capability, arguments, module, monkeypatch,
):
    url_key, token_key, url = URL[module]
    monkeypatch.setattr(settings, url_key, url)
    monkeypatch.setattr(settings, token_key, "t")

    async def refuse(self, *a, **k):
        raise httpx.ConnectError("All connection attempts failed")

    monkeypatch.setattr(httpx.AsyncClient, "post", refuse)
    out, row = await _approve(_one_action_model(capability, arguments))
    assert out["execution"] == "parked", out
    assert row["status"] == "RETRY_PENDING", row["status"]
