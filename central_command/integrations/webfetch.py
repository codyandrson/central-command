"""Client for the web-fetch capability (D-web-read): an UNGATED READ that lets
a granted agent pull one URL's content, converted to model-digestible
text/markdown.

Ungated because a GET is not a write to anyone's world but the far server's
access log — the same reasoning as `graph.search_facts` or `calendar.list_events`
(DESIGN §3: reading is layered, writing is gated). What makes this read worth a
config knob is outbound IDENTITY: some internal/corporate endpoints require
client-PKI (mTLS) to answer at all. `CC_FETCH_CLIENT_CERT`/`_KEY`/`_CA_BUNDLE`
configure ONE outbound identity for every fetch this process makes — never a
per-call argument, so an agent cannot pick which certificate it authenticates
as (see policy note in gateway/capabilities.py).
"""

from __future__ import annotations

import httpx
import trafilatura
from bs4 import BeautifulSoup
from markdownify import markdownify as _to_markdown

from central_command.config import settings
from central_command.integrations import http as http_client

# Tags whose content is noise for a model reading a page, not just formatting
# to strip — decomposed (removed with their contents) before conversion, since
# markdownify's own `strip=` keeps a stripped tag's TEXT (verified: it would
# leak <script>/<style> bodies into the output otherwise).
_NOISE_TAGS = ("script", "style", "nav", "noscript")

# Content-types returned as-is (already text a model can read).
_PASSTHROUGH_PREFIXES = ("text/plain", "application/json", "text/csv", "text/xml", "application/xml")


def _cert_allowed_for(host: str) -> bool:
    """Whether the configured client cert may be presented to `host`.

    `CC_FETCH_CERT_HOSTS` (comma-separated host suffixes) scopes the outbound
    PKI identity: unset means every host (homelab default); set means only an
    exact host or a `.suffix` match receives it. The point is that an
    agent-named URL to an arbitrary external host must not be handed the
    corporate certificate — the identity leak the security review flagged.

    Recorded residual: the check is on the REQUESTED host; a redirect from an
    allowed host rides the same client and would still present the cert.
    """
    allowed = [h.strip().lower() for h in settings.fetch_cert_hosts.split(",") if h.strip()]
    if not allowed:
        return True
    host = host.lower()
    return any(host == h or host.endswith("." + h) for h in allowed)


def _client_kwargs(host: str) -> dict:
    """The one outbound identity + timeout, built from settings for every call.

    `httpx.Client.cert` accepts either a cert-only path or a (cert, key) pair;
    `verify` swaps in a private CA bundle when the target isn't trusted by the
    system store.

    Layers `integrations.http.client_kwargs()` as the base identity (so the
    generic CC_CA_BUNDLE/CC_CLIENT_CERT/CC_CLIENT_KEY cover this call site
    too), then lets the fetch-specific settings below override — they carry
    the host-scoping (`_cert_allowed_for`) an agent-named, possibly-external
    URL needs that the generic settings deliberately don't have.
    """
    kwargs: dict = {"follow_redirects": True, "timeout": settings.fetch_timeout}
    kwargs.update(http_client.client_kwargs())
    if settings.fetch_client_cert and _cert_allowed_for(host):
        if settings.fetch_client_key:
            kwargs["cert"] = (settings.fetch_client_cert, settings.fetch_client_key)
        else:
            kwargs["cert"] = settings.fetch_client_cert
    if settings.fetch_ca_bundle:
        kwargs["verify"] = settings.fetch_ca_bundle
    return kwargs


def _html_to_markdown(html: str, url: str) -> str:
    """Primary path: trafilatura's boilerplate-aware extraction (drops nav/
    footer/cookie-banner chrome that plain tag-stripping can't tell from
    content). `extract()` returns None when it finds nothing worth extracting
    (e.g. a JS-rendered empty shell, or a page too short to score) — fall back
    to the old BeautifulSoup+markdownify path so a page never comes back
    emptier than before this swap.
    """
    extracted = trafilatura.extract(
        html, output_format="markdown", url=url, include_links=True, include_tables=True
    )
    if extracted is not None:
        return extracted
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(_NOISE_TAGS):
        tag.decompose()
    return _to_markdown(str(soup)).strip()


def _content_type(headers: httpx.Headers) -> str:
    return headers.get("content-type", "").split(";")[0].strip().lower()


async def fetch(url: str) -> dict:
    """GET `url` under the configured outbound identity and return a dict:
    `{"ok": bool, "status": int, "text": str}` — network/connection failures
    (timeout, DNS, refused) are RAISED for the caller's own retry+degradation,
    matching every other wrapped read in runtime/tools.py; a reachable server
    answering with a non-2xx, or with content this tool cannot render, is a
    SEMANTIC outcome and comes back as `ok=False` with an honest `text`,
    never an exception.
    """
    parsed = httpx.URL(url)
    if parsed.scheme not in ("http", "https"):
        return {
            "ok": False,
            "status": 0,
            "text": f"unsupported URL scheme {parsed.scheme!r} — only http/https are fetchable",
        }
    max_bytes = max(1, int(settings.fetch_max_bytes))
    async with httpx.AsyncClient(**_client_kwargs(parsed.host)) as client:
        async with client.stream("GET", url) as response:
            if response.status_code < 200 or response.status_code >= 300:
                return {
                    "ok": False,
                    "status": response.status_code,
                    "text": f"HTTP {response.status_code} fetching {url}",
                }

            content_type = _content_type(response.headers)
            body = bytearray()
            truncated = False
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) >= max_bytes:
                    truncated = True
                    del body[max_bytes:]
                    break

            text = _render(bytes(body), content_type, url)
            if truncated:
                text += f"\n\n[truncated at {max_bytes} bytes]"
            return {"ok": True, "status": response.status_code, "text": text}


def _render(body: bytes, content_type: str, url: str) -> str:
    if content_type == "text/html":
        return _html_to_markdown(body.decode("utf-8", errors="replace"), url)
    if content_type.startswith(_PASSTHROUGH_PREFIXES) or content_type.startswith("text/"):
        return body.decode("utf-8", errors="replace")
    return f"(unsupported content type {content_type or 'unknown'}, {len(body)} bytes, from {url})"
