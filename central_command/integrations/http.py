"""Pluggable-trust httpx kwargs for outbound integration clients (work-transition
compatibility design, decision 3; profile §2.2).

An enterprise network may require client-certificate auth (mTLS) and/or a
private CA trust bundle for internal services — "public CA only" and
"username/password is enough" are the documented failure modes there. This
module is the ONE seam: `client_kwargs()` reads `CC_CA_BUNDLE` /
`CC_CLIENT_CERT` / `CC_CLIENT_KEY` and returns the httpx kwargs a call site
splats into its own `httpx.AsyncClient(...)` alongside its existing
timeout/base_url/auth. All three settings default to "" — on the homelab
that means an EMPTY dict, i.e. exactly today's `httpx.AsyncClient(verify=True,
cert=None)` behavior. Zero behavior change until an operator sets them.

Distinct from `webfetch._client_kwargs`'s `CC_FETCH_*` settings on purpose:
`web.fetch` hands an agent-named, possibly-external URL to the client, so it
additionally scopes WHICH hosts get the cert (`CC_FETCH_CERT_HOSTS`). The
settings here are one outbound identity for the fixed, operator-configured
internal integrations (Jira, Graphiti, the LiteLLM admin/embed calls) — no
per-request host varies, so no scoping is needed. `webfetch` layers this
module's kwargs as its base and lets its own `CC_FETCH_*` values override,
so setting the generic identity covers it too when nothing more specific is
configured.

**`CC_TLS_INSECURE=1` is ruled global (epic small-fixes Decision 11,
2026-10-29, R5):** it turns verification off for EVERY client built from
`client_kwargs()` — the agents' web fetch of arbitrary public URLs and the
Systems probes included — and WINS over `CC_CA_BUNDLE`; when both are set one
warning per process names both. The callers that do not splat `client_kwargs()`
(the n8n loopback, the provider public endpoints, the GitHub update download)
stay unwired.
"""

from __future__ import annotations

import logging

import httpx

from central_command.config import settings

log = logging.getLogger(__name__)

# `client_kwargs()` runs per request; the warning must print once per process.
_insecure_warned = False


def _warn_insecure_once() -> None:
    global _insecure_warned
    if _insecure_warned:
        return
    _insecure_warned = True
    overridden = (
        f" It OVERRIDES CC_CA_BUNDLE={settings.ca_bundle}, which is ignored."
        if settings.ca_bundle
        else ""
    )
    log.warning(
        "WARN CC_TLS_INSECURE=1 — server certificates are NOT verified by any "
        "integration client (Jira, Confluence, LiteLLM, graph, forge, speech, "
        "Exchange, web fetch, the self-check and Systems probes).%s Prefer "
        "CC_CA_BUNDLE with your internal CA.",
        overridden,
    )


# The ONE clock the app holds on a model completion: CONNECT only. A read
# timeout is "no bytes for N seconds", and a non-streamed completion sends
# nothing until it is finished, so any read timeout is really a cap on queue
# time plus generation — and the model server is a single FIFO slot. LiteLLM
# declares the per-model `timeout` (6000 s on the local slot, the operator's
# 2026-08-20 decision: wait, never crash for waiting) and owns the retry, so no
# client here may hold a shorter one or resend on its own. The 2026-11-02 queue
# storm was exactly that: the SDK's default 600 s + 2 retries re-sent ten
# 30k-token agent turns behind their still-running originals, and a 180 s
# extractor cycled four copies of one mail (DL-204).
LLM_CONNECT_TIMEOUT_SECONDS = 5.0


def llm_timeout() -> httpx.Timeout:
    """`httpx.Timeout(None, connect=5)`: a dead proxy still fails fast, a slow
    one is waited for. Every model-completion client takes its timeout here."""
    return httpx.Timeout(None, connect=LLM_CONNECT_TIMEOUT_SECONDS)


def openai_client(api_key: str, base_url: str):
    """The AsyncOpenAI the app waits on — agents (pydantic-ai's provider), the
    graph client (graphiti-core) and anything else that speaks the SDK: no
    read timeout, `max_retries=0` (LiteLLM owns routing AND retry; the SDK's
    default of 2 re-sends the whole prompt as a NEW request while the old one
    keeps the slot), and the integration trust settings (CC_CA_BUNDLE /
    CC_CLIENT_CERT / CC_TLS_INSECURE) the other outbound clients already use."""
    from openai import AsyncOpenAI, DefaultAsyncHttpxClient

    kwargs = client_kwargs()
    extra = {"http_client": DefaultAsyncHttpxClient(**kwargs)} if kwargs else {}
    return AsyncOpenAI(api_key=api_key, base_url=base_url,
                       timeout=llm_timeout(), max_retries=0, **extra)


def client_kwargs() -> dict:
    """`{}` when CC_CA_BUNDLE/CC_CLIENT_CERT/CC_CLIENT_KEY/CC_TLS_INSECURE are
    all unset (the default). `verify` takes a custom CA bundle path, or False
    under CC_TLS_INSECURE (which wins over the bundle); `cert` takes either a
    cert-only path (combined PEM) or a (cert, key) pair — same shapes httpx
    itself accepts.
    """
    kwargs: dict = {}
    if settings.tls_insecure:
        kwargs["verify"] = False
        _warn_insecure_once()
    elif settings.ca_bundle:
        kwargs["verify"] = settings.ca_bundle
    if settings.client_cert:
        kwargs["cert"] = (
            (settings.client_cert, settings.client_key)
            if settings.client_key
            else settings.client_cert
        )
    return kwargs
