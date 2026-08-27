"""integrations/http.py: settings -> httpx kwargs, work-transition
compatibility design decision 3. Empty settings (the homelab default) must
map to {} — zero behavior change from today's bare httpx.AsyncClient(...).
"""

from __future__ import annotations

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
