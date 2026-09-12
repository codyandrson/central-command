"""One-click unsubscribe eligibility (RFC 8058), decided from the message's
own headers. It lives in the contract layer because BOTH tiers decide it from
the same facts: the runtime tool refuses to propose an ineligible message, and
the Executor re-derives the URL from the mailbox and refuses to POST anywhere
else (runtime may never import gateway, so the shared function sits below
both — the `claim_supported` precedent).

The facts, from RFC 8058 and Gmail's own headers (research 2026-09-12):

- eligible = an https URI in `List-Unsubscribe` AND the exact header
  `List-Unsubscribe-Post: List-Unsubscribe=One-Click`;
- the receiver SHOULD NOT offer one-click unless a passing DKIM signature
  covers both headers. Gmail records its verdict in the Authentication-Results
  header it PREPENDS on receipt (authserv-id `mx.google.com`), and the list
  of signed headers is the DKIM-Signature `h=` tag.

Three things a sender controls and must not be able to satisfy on its own
(security review, v2.26.1): a sender can add its own `Authentication-Results:
mx.google.com; dkim=pass` header, so only the FIRST one — the one Gmail put
on top — is read; a sender can sign one thing and let another pass, so the
signature that COVERS the headers must be the signature Gmail VERIFIED
(`header.b=` in the verdict is the prefix of its `b=`); and the URL host must
be a real public name, never an IP literal or `localhost` (the Executor
additionally resolves it and refuses private, loopback, link-local and
tailnet addresses before it connects).

What this deliberately does NOT require: alignment between the signing
domain and the From domain. RFC 8058 does not ask for it, and newsletters
sent through a mailing service are routinely signed by the service's
domain — the signer is surfaced in the proposal instead, so the operator
sees who vouches for the link.
"""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlparse

_HTTPS_URI = re.compile(r"<(https://[^>\s]+)>")
_SIGNED_HEADERS = re.compile(r"(?:^|;)\s*h=([^;]+)")
_SIG_TAG_B = re.compile(r"(?:^|;)\s*b=([^;]*)")
_SIG_TAG_D = re.compile(r"(?:^|;)\s*d=([^;\s]+)")
_GMAIL_VERDICT = re.compile(r"^\s*(?:i=\d+;\s*)?mx\.google\.com\s*;")
_DKIM_PASS = re.compile(r"\bdkim=pass\b([^;]*)")
_VERDICT_B = re.compile(r"\bheader\.b=([A-Za-z0-9+/=]+)")
_ONE_CLICK = "list-unsubscribe=one-click"
_COVERED = {"list-unsubscribe", "list-unsubscribe-post"}


def one_click_unsubscribe(msg: dict) -> tuple[str | None, str]:
    """(url, verdict): the POST target when the message qualifies — with the
    verdict naming the signing domain — else None with the reason in words an
    operator (or the agent) can act on."""
    urls = _HTTPS_URI.findall(msg.get("list_unsubscribe") or "")
    if not urls:
        return None, "the message carries no https List-Unsubscribe URL (none, or mailto only)"
    url = urls[0]
    host = (urlparse(url).hostname or "").lower()
    if not _public_hostname(host):
        return None, f"the List-Unsubscribe URL host {host!r} is not a public hostname"
    if (msg.get("list_unsubscribe_post") or "").strip().lower() != _ONE_CLICK:
        return None, "the sender does not offer one-click unsubscribe (no List-Unsubscribe-Post header)"
    results = msg.get("authentication_results") or []
    first = results[0] if results else ""
    if not _GMAIL_VERDICT.match(first):
        return None, "Gmail recorded no authentication verdict for this message"
    passes = [m.group(1) for m in _DKIM_PASS.finditer(first)]
    if not passes:
        return None, "DKIM did not pass at Gmail, so the unsubscribe headers cannot be trusted"
    verified_prefixes = {m.group(1) for clause in passes for m in [_VERDICT_B.search(clause)] if m}
    for sig in msg.get("dkim_signatures") or []:
        if not _COVERED <= _signed(sig):
            continue
        b = re.sub(r"\s+", "", (_SIG_TAG_B.search(sig) or [None, ""])[1])
        if verified_prefixes and not any(b.startswith(p) for p in verified_prefixes):
            continue  # covers the headers, but it is not the signature Gmail verified
        d = (_SIG_TAG_D.search(sig) or [None, "?"])[1]
        return url, f"eligible (signed by {d})"
    return None, "no DKIM signature that Gmail verified covers the List-Unsubscribe headers"


def _public_hostname(host: str) -> bool:
    if not host or host == "localhost" or host.endswith(".localhost") or "." not in host:
        return False
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return True   # a name, resolved and re-checked by the Executor
    return False      # an IP literal is never a mailing list's endpoint


def _signed(signature: str) -> set[str]:
    m = _SIGNED_HEADERS.search(signature)
    return {h.strip().lower() for h in m.group(1).split(":")} if m else set()
