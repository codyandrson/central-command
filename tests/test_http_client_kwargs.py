"""integrations/http.py: settings -> httpx kwargs, work-transition
compatibility design decision 3. Empty settings (the homelab default) must
map to {} — zero behavior change from today's bare httpx.AsyncClient(...).

Story 11.10 (epic small-fixes, R5, INGEST-005; ruled 2026-10-29, Decision 11):
`CC_TLS_INSECURE` is `settings.tls_insecure`, turns verification off for every
`client_kwargs()` client, wins over `CC_CA_BUNDLE`, tolerates an empty env
value, warns once per process, and is read from nowhere but the setting.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest

from central_command.config import settings
from central_command.integrations import http as http_client


def test_empty_settings_yield_no_kwargs(monkeypatch):
    monkeypatch.setattr(settings, "ca_bundle", "")
    monkeypatch.setattr(settings, "client_cert", "")
    monkeypatch.setattr(settings, "client_key", "")
    assert http_client.client_kwargs() == {}


def test_ca_bundle_maps_to_verify(monkeypatch):
    monkeypatch.setattr(settings, "ca_bundle", "/etc/ssl/corp-ca.pem")
    monkeypatch.setattr(settings, "client_cert", "")
    monkeypatch.setattr(settings, "client_key", "")
    assert http_client.client_kwargs() == {"verify": "/etc/ssl/corp-ca.pem"}


def test_client_cert_alone_is_a_combined_pem(monkeypatch):
    monkeypatch.setattr(settings, "ca_bundle", "")
    monkeypatch.setattr(settings, "client_cert", "/etc/pki/client.pem")
    monkeypatch.setattr(settings, "client_key", "")
    assert http_client.client_kwargs() == {"cert": "/etc/pki/client.pem"}


def test_client_cert_and_key_form_a_pair(monkeypatch):
    monkeypatch.setattr(settings, "ca_bundle", "/etc/ssl/corp-ca.pem")
    monkeypatch.setattr(settings, "client_cert", "/etc/pki/client.crt")
    monkeypatch.setattr(settings, "client_key", "/etc/pki/client.key")
    assert http_client.client_kwargs() == {
        "verify": "/etc/ssl/corp-ca.pem",
        "cert": ("/etc/pki/client.crt", "/etc/pki/client.key"),
    }


# --- CC_TLS_INSECURE (story 11.10 / R5) -----------------------------------------


@pytest.fixture
def blank_trust(monkeypatch):
    monkeypatch.setattr(settings, "ca_bundle", "")
    monkeypatch.setattr(settings, "client_cert", "")
    monkeypatch.setattr(settings, "client_key", "")
    monkeypatch.setattr(settings, "tls_insecure", False)
    monkeypatch.setattr(http_client, "_insecure_warned", False)


def test_insecure_turns_verification_off(blank_trust, monkeypatch):
    monkeypatch.setattr(settings, "tls_insecure", True)
    assert http_client.client_kwargs() == {"verify": False}


def test_insecure_wins_over_a_ca_bundle_and_keeps_the_client_cert(blank_trust, monkeypatch):
    monkeypatch.setattr(settings, "tls_insecure", True)
    monkeypatch.setattr(settings, "ca_bundle", "/etc/ssl/corp-ca.pem")
    monkeypatch.setattr(settings, "client_cert", "/etc/pki/client.pem")
    assert http_client.client_kwargs() == {"verify": False, "cert": "/etc/pki/client.pem"}


def test_one_warning_across_many_calls_naming_the_flag_and_the_bundle(
    blank_trust, monkeypatch, caplog
):
    monkeypatch.setattr(settings, "tls_insecure", True)
    monkeypatch.setattr(settings, "ca_bundle", "/etc/ssl/corp-ca.pem")
    with caplog.at_level(logging.WARNING):
        for _ in range(25):
            http_client.client_kwargs()
    warnings = [r.getMessage() for r in caplog.records if "CC_TLS_INSECURE" in r.getMessage()]
    assert len(warnings) == 1
    assert "CC_CA_BUNDLE" in warnings[0] and "/etc/ssl/corp-ca.pem" in warnings[0]


def test_no_warning_when_verification_is_on(blank_trust, caplog):
    with caplog.at_level(logging.WARNING):
        http_client.client_kwargs()
    assert not [r for r in caplog.records if "CC_TLS_INSECURE" in r.getMessage()]


@pytest.mark.parametrize("raw,expected", [
    ("", False), ("  ", False), ("0", False), ("1", True), ("true", True), ("yes", True),
])
def test_an_empty_or_odd_env_value_boots_and_parses(monkeypatch, raw, expected):
    """A plain bool field fails validation on `CC_TLS_INSECURE=` and would stop
    the boot; the before-validator reads blank as false."""
    from central_command.config import Settings

    monkeypatch.setenv("CC_TLS_INSECURE", raw)
    assert Settings().tls_insecure is expected


def test_nothing_reads_cc_tls_insecure_from_the_environment():
    """The setting is the one reader (R5): a second reader is how the Exchange
    client came to honour a flag the other clients ignored."""
    root = Path(__file__).resolve().parents[1]
    offenders = []
    for base in ("central_command", "scripts"):
        for path in (root / base).rglob("*.py"):
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if re.search(r"environ.*CC_TLS_INSECURE|_tls_insecure\b", line):
                    offenders.append(f"{path.relative_to(root)}:{n}")
    assert not offenders, offenders
