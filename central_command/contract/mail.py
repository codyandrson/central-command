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
  covers both headers. Gmail records its verdict in an Authentication-Results
  header whose authserv-id is `mx.google.com` — a sender can forge one under
  any OTHER id, so only that one counts — and the list of signed headers is
  the DKIM-Signature `h=` tag.
"""

from __future__ import annotations

import re

_HTTPS_URI = re.compile(r"<(https://[^>\s]+)>")
_SIGNED_HEADERS = re.compile(r"(?:^|;)\s*h=([^;]+)")
_GMAIL_VERDICT = re.compile(r"^\s*(?:i=\d+;\s*)?mx\.google\.com\s*;")
_ONE_CLICK = "list-unsubscribe=one-click"
_COVERED = {"list-unsubscribe", "list-unsubscribe-post"}


def one_click_unsubscribe(msg: dict) -> tuple[str | None, str]:
    """(url, reason): the POST target when the message qualifies, else None
    with the reason in words an operator (or the agent) can act on."""
    urls = _HTTPS_URI.findall(msg.get("list_unsubscribe") or "")
    if not urls:
        return None, "the message carries no https List-Unsubscribe URL (none, or mailto only)"
    if (msg.get("list_unsubscribe_post") or "").strip().lower() != _ONE_CLICK:
        return None, "the sender does not offer one-click unsubscribe (no List-Unsubscribe-Post header)"
    verdicts = [a for a in msg.get("authentication_results") or [] if _GMAIL_VERDICT.match(a)]
    if not any(re.search(r"\bdkim=pass\b", a) for a in verdicts):
        return None, "DKIM did not pass at Gmail, so the unsubscribe headers cannot be trusted"
    # ponytail: dkim=pass and the covering h= are not proven to be the SAME
    # signature; match Authentication-Results header.b= to the signature's b=
    # if a double-signed message ever bites.
    if not any(_COVERED <= _signed(s) for s in msg.get("dkim_signatures") or []):
        return None, "the DKIM signature does not cover the List-Unsubscribe headers"
    return urls[0], "eligible"


def _signed(signature: str) -> set[str]:
    m = _SIGNED_HEADERS.search(signature)
    return {h.strip().lower() for h in m.group(1).split(":")} if m else set()
