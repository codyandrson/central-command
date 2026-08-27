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
"""

from __future__ import annotations

from central_command.config import settings


def client_kwargs() -> dict:
    """`{}` when CC_CA_BUNDLE/CC_CLIENT_CERT/CC_CLIENT_KEY are all unset (the
    default). `verify` takes a custom CA bundle path; `cert` takes either a
    cert-only path (combined PEM) or a (cert, key) pair — same shapes httpx
    itself accepts.
    """
    kwargs: dict = {}
    if settings.ca_bundle:
        kwargs["verify"] = settings.ca_bundle
    if settings.client_cert:
        kwargs["cert"] = (
            (settings.client_cert, settings.client_key)
            if settings.client_key
            else settings.client_cert
        )
    return kwargs
