"""Jira auth-mode seam (work-transition compatibility design, decision 2):
"basic" (default, Cloud) sends email+token Basic auth; "bearer" (Data Center)
sends the API token as a Bearer PAT and ignores email. Auth-only — no
endpoint-shape assertions here, those are verified on-site.
"""

from __future__ import annotations

from central_command.config import settings
from central_command.integrations import jira


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
