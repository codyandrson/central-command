"""Offline tests for the web-fetch capability (D-web-read): HTML->markdown,
truncation, non-2xx degradation, and mTLS plumbing — all via httpx.MockTransport
so the suite never touches the real network.
"""

from __future__ import annotations

import httpx
import pytest

from central_command.config import settings
from central_command.integrations import webfetch
from central_command.runtime import tools as tools_mod

_ORIGINAL_CLIENT = httpx.AsyncClient


def _patch_client(monkeypatch, handler, *, capture: dict | None = None):
    """Replace webfetch's httpx.AsyncClient with one that answers via a
    MockTransport instead of the network, optionally recording the kwargs the
    real constructor was called with (for the mTLS assertion)."""

    def _factory(**kwargs):
        if capture is not None:
            capture.update(kwargs)
        kwargs["transport"] = httpx.MockTransport(handler)
        return _ORIGINAL_CLIENT(**kwargs)

    monkeypatch.setattr(webfetch.httpx, "AsyncClient", _factory)


async def test_html_is_converted_to_readable_markdown(monkeypatch):
    # A realistic-sized article: trafilatura's boilerplate scoring needs
    # actual body text to discriminate nav/footer/cookie-banner chrome from
    # content — a two-word fragment scores everything as content (see the
    # None-fallback test below for that case).
    html = (
        "<html><head><style>.x{color:red}</style></head><body>"
        "<nav><a href='/'>Home</a> <a href='/about'>About</a></nav>"
        "<div class='cookie-banner'>We use cookies to improve your experience. "
        "Accept all cookies to continue browsing this site.</div>"
        "<script>alert(1)</script>"
        "<article><h1>Title</h1>"
        "<p>Hello world, this is the real article body with enough text for "
        "trafilatura's extractor to recognize it as the main content of the "
        "page rather than boilerplate chrome around it.</p></article>"
        "<footer>Copyright 2026 Example Corp. All rights reserved. "
        "<a href='/privacy'>Privacy</a></footer>"
        "</body></html>"
    )

    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/html"}, text=html)

    _patch_client(monkeypatch, handler)
    result = await webfetch.fetch("https://example.test/page")
    assert result["ok"] is True
    assert "Title" in result["text"]
    assert "Hello world" in result["text"]
    # script/nav/footer/cookie-banner content must not leak into the
    # model-facing text.
    assert "alert(1)" not in result["text"]
    assert "Home" not in result["text"]
    assert "cookies" not in result["text"]
    assert "Copyright 2026" not in result["text"]


async def test_html_with_no_extractable_content_falls_back_to_bs4(monkeypatch):
    """trafilatura.extract() returns None for a too-short/empty page (e.g. a
    JS-rendered shell) — fetch() must still return the body text via the old
    BeautifulSoup+markdownify path rather than an empty result."""
    html = "<html><head><style>.x{color:red}</style></head><body><p>Hi</p></body></html>"

    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/html"}, text=html)

    _patch_client(monkeypatch, handler)
    result = await webfetch.fetch("https://example.test/empty")
    assert result["ok"] is True
    assert "Hi" in result["text"]
    assert "color:red" not in result["text"]


async def test_truncation_note_appears_past_the_byte_cap(monkeypatch):
    body = "x" * 1000

    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/plain"}, text=body)

    _patch_client(monkeypatch, handler)
    saved = settings.fetch_max_bytes
    settings.fetch_max_bytes = 100
    try:
        result = await webfetch.fetch("https://example.test/big")
    finally:
        settings.fetch_max_bytes = saved
    assert result["ok"] is True
    assert "[truncated at 100 bytes]" in result["text"]
    assert len(result["text"]) < len(body) + 100  # actually truncated, not the whole body


async def test_non_2xx_yields_a_degradation_string_not_an_exception(monkeypatch):
    def handler(request):
        return httpx.Response(503, text="down for maintenance")

    _patch_client(monkeypatch, handler)
    result = await webfetch.fetch("https://example.test/down")
    assert result["ok"] is False
    assert "503" in result["text"]

    # And through the tool: still no exception, house degradation wording.
    class _Ctx:
        pass

    out = await tools_mod.fetch_url(_Ctx(), "https://example.test/down")
    assert out.startswith("(web fetch degraded:")
    assert "503" in out


async def test_binary_content_degrades_to_an_honest_note_never_raw_bytes(monkeypatch):
    def handler(request):
        return httpx.Response(200, headers={"content-type": "image/png"}, content=b"\x89PNG\r\n\x1a\n")

    _patch_client(monkeypatch, handler)
    result = await webfetch.fetch("https://example.test/pic.png")
    assert result["ok"] is True
    assert "unsupported content type image/png" in result["text"]
    assert "\x89PNG" not in result["text"]


async def test_mtls_identity_comes_from_config_not_the_call(monkeypatch, tmp_path):
    """Policy guard: cert/key/CA-bundle are read from settings and handed to
    httpx.AsyncClient for every fetch — fetch_url's only argument is the URL,
    so an agent has no way to name or override the outbound identity."""
    cert = tmp_path / "client.pem"
    key = tmp_path / "client.key"
    ca = tmp_path / "ca.pem"
    cert.write_text("cert")
    key.write_text("key")
    ca.write_text("ca")

    monkeypatch.setattr(settings, "fetch_client_cert", str(cert))
    monkeypatch.setattr(settings, "fetch_client_key", str(key))
    monkeypatch.setattr(settings, "fetch_ca_bundle", str(ca))

    captured: dict = {}

    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/plain"}, text="ok")

    _patch_client(monkeypatch, handler, capture=captured)
    result = await webfetch.fetch("https://example.test/secure")
    assert result["ok"] is True
    assert captured.get("cert") == (str(cert), str(key))
    assert captured.get("verify") == str(ca)

    import inspect
    assert "url" in inspect.signature(tools_mod.fetch_url).parameters
    assert "cert" not in inspect.signature(tools_mod.fetch_url).parameters


async def test_non_http_schemes_are_refused(monkeypatch):
    """file:// and friends are not fetchable — refused semantically, before
    any client is even built."""

    def handler(request):  # pragma: no cover — must never be reached
        raise AssertionError("a non-http scheme reached the transport")

    _patch_client(monkeypatch, handler)
    result = await webfetch.fetch("file:///etc/passwd")
    assert result["ok"] is False
    assert "unsupported URL scheme" in result["text"]


async def test_cert_is_withheld_from_hosts_outside_the_allowlist(monkeypatch, tmp_path):
    """CC_FETCH_CERT_HOSTS scopes the outbound PKI identity: a matching host
    (exact or dot-suffix) is handed the cert; any other host is not — the
    identity-leak guard from the 2026-08-02 security review."""
    cert = tmp_path / "client.pem"
    cert.write_text("cert")
    monkeypatch.setattr(settings, "fetch_client_cert", str(cert))
    monkeypatch.setattr(settings, "fetch_client_key", "")
    monkeypatch.setattr(settings, "fetch_cert_hosts", "corp.example.com, tools.internal")

    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/plain"}, text="ok")

    for url, expect_cert in (
        ("https://corp.example.com/x", True),          # exact
        ("https://jira.corp.example.com/x", True),     # dot-suffix
        ("https://evilcorp.example.net/x", False),     # elsewhere
        ("https://notcorp.example.com.evil.net/x", False),  # suffix spoof
    ):
        captured: dict = {}
        _patch_client(monkeypatch, handler, capture=captured)
        result = await webfetch.fetch(url)
        assert result["ok"] is True
        assert ("cert" in captured) is expect_cert, url
