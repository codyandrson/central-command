"""Jira auth-mode seam (work-transition compatibility design, decision 2):
"basic" (Cloud) sends email+token Basic auth; "bearer" (Data Center) sends
the API token as a Bearer PAT and ignores email. Unset (the default since the
2026-09-23 Data Center flavor design) means DERIVE FROM THE FLAVOR, and an
explicit value always wins — Basic works on both products. Endpoint SHAPES
live in test_jira_flavor.py.
"""

from __future__ import annotations

from central_command.config import settings
from central_command.integrations import confluence, jira


def test_default_mode_is_basic_email_and_token(monkeypatch):
    monkeypatch.setattr(settings, "jira_auth_mode", "basic")
    monkeypatch.setattr(settings, "jira_email", "test@example.com")
    monkeypatch.setattr(settings, "jira_api_token", "tok")
    assert jira._auth_kwargs() == {"auth": ("test@example.com", "tok")}


def test_bearer_mode_sends_the_token_as_a_bearer_header_and_ignores_email(monkeypatch):
    monkeypatch.setattr(settings, "jira_auth_mode", "bearer")
    monkeypatch.setattr(settings, "jira_email", "test@example.com")
    monkeypatch.setattr(settings, "jira_api_token", "pat-abc123")
    assert jira._auth_kwargs() == {"headers": {"Authorization": "Bearer pat-abc123"}}


# --- the flavor picks the default mode (Jira DC flavor design, decision 1) ----


def test_an_unset_mode_resolves_basic_under_cloud(monkeypatch):
    monkeypatch.setattr(settings, "jira_auth_mode", "")
    monkeypatch.setattr(settings, "jira_api_flavor", "cloud")
    monkeypatch.setattr(settings, "jira_email", "test@example.com")
    monkeypatch.setattr(settings, "jira_api_token", "tok")
    assert jira.auth_mode() == "basic"
    assert jira._auth_kwargs() == {"auth": ("test@example.com", "tok")}


def test_an_unset_mode_resolves_bearer_under_server(monkeypatch):
    monkeypatch.setattr(settings, "jira_auth_mode", "")
    monkeypatch.setattr(settings, "jira_api_flavor", "server")
    monkeypatch.setattr(settings, "jira_email", "test@example.com")
    monkeypatch.setattr(settings, "jira_api_token", "pat-abc123")
    assert jira.auth_mode() == "bearer"
    assert jira._auth_kwargs() == {"headers": {"Authorization": "Bearer pat-abc123"}}


def test_an_explicit_mode_wins_over_the_flavor_default(monkeypatch):
    """Basic works on BOTH products, so a Data Center operator may keep it."""
    monkeypatch.setattr(settings, "jira_auth_mode", "basic")
    monkeypatch.setattr(settings, "jira_api_flavor", "server")
    monkeypatch.setattr(settings, "jira_email", "test@example.com")
    monkeypatch.setattr(settings, "jira_api_token", "tok")
    assert jira.auth_mode() == "basic"
    assert jira._auth_kwargs() == {"auth": ("test@example.com", "tok")}


def test_a_token_alone_is_configured_under_bearer_but_not_under_basic(monkeypatch):
    """Requiring an email under bearer silently routed a PAT-configured Data
    Center deployment back to the n8n façade — the cutover never happened."""
    monkeypatch.setattr(settings, "jira_email", "")
    monkeypatch.setattr(settings, "jira_api_token", "pat-abc123")
    monkeypatch.setattr(settings, "jira_auth_mode", "")
    monkeypatch.setattr(settings, "jira_api_flavor", "server")
    assert jira.configured() is True
    monkeypatch.setattr(settings, "jira_api_flavor", "cloud")
    assert jira.configured() is False
    monkeypatch.setattr(settings, "jira_api_token", "")
    monkeypatch.setattr(settings, "jira_api_flavor", "server")
    assert jira.configured() is False


def test_confluence_auth_mode_follows_its_own_flavor(monkeypatch):
    monkeypatch.setattr(settings, "confluence_auth_mode", "")
    monkeypatch.setattr(settings, "confluence_api_flavor", "server")
    monkeypatch.setattr(settings, "confluence_api_token", "pat")
    monkeypatch.setattr(settings, "confluence_email", "x@example.com")
    assert confluence.auth_mode() == "bearer"
    assert confluence._auth_kwargs() == {"headers": {"Authorization": "Bearer pat"}}
    monkeypatch.setattr(settings, "confluence_api_flavor", "cloud")
    assert confluence.auth_mode() == "basic"
    assert confluence._auth_kwargs() == {"auth": ("x@example.com", "pat")}
    monkeypatch.setattr(settings, "confluence_auth_mode", "bearer")
    assert confluence.auth_mode() == "bearer"
