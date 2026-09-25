"""Read-only connectivity probe for the native Exchange client (Exchange
native client design, 2026-09-25, decision 8).

    python scripts/exchange_smoke.py

No Exchange server exists outside the second deployment's network, so nothing
in `integrations/exchange.py` can be proven from the reference homelab: the
fixtures in `tests/test_exchange.py` pin the SHAPES, and this script is what
proves them against a real mailbox. Its output is the acceptance record, and
its findings become the next fixtures.

One line per check, in the `atlassian_probe.py` idiom:

    PASS|FAIL|SKIP <check> — <what was found>

Exit 0 when every non-SKIP check passed, 1 otherwise.

It reads `.env` through `central_command.config.settings`, makes only reads
(no send, no move, no save, no delete — the write helpers are not imported),
and NEVER prints the password. The steps, in the order a failure is most
usefully diagnosed:

  1. configuration present, and which trust seam is in effect
  2. TLS handshake with the endpoint, before any authentication
  3. NTLM accepted (one cheap authenticated call)
  4. the inbox resolves, with its total and unread counts
  5. one `newer_than:1d` search through the real query translator (count only)
  6. one 7-day calendar window (count only)
  7. the server's product name and build number
  8. whether the newest inbox message exposes `InternetMessageId` and an
     `Authentication-Results` header — the two facts the design record could
     NOT settle from the documentation, because the ledger's identity rule
     (D4) depends on the first and a future Exchange unsubscribe on the second
"""

from __future__ import annotations

import pathlib
import socket
import ssl
import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

# Run from a checkout without installing it, and prefer THIS checkout over an
# editable install that points somewhere else (worktrees).
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from central_command.config import settings  # noqa: E402
from central_command.integrations import exchange  # noqa: E402
from central_command.integrations import http as http_client  # noqa: E402

_failures = 0
_checks = 0


def _scrub(text: str) -> str:
    """The password never reaches stdout, whatever an exception carries."""
    password = settings.exchange_password
    return text.replace(password, "<redacted>") if password else text


def report(ok: bool | None, check: str, detail: str = "") -> None:
    global _failures, _checks

    if ok is None:
        print(f"SKIP {check} — {_scrub(detail)}", flush=True)
        return
    _checks += 1
    if not ok:
        _failures += 1
    print(f"{'PASS' if ok else 'FAIL'} {check} — {_scrub(detail)}", flush=True)


def check_config() -> bool:
    if not exchange.configured():
        missing = [name for name, value in (
            ("CC_EXCHANGE_URL", settings.exchange_url),
            ("CC_EXCHANGE_USERNAME", settings.exchange_username),
            ("CC_EXCHANGE_PASSWORD", settings.exchange_password),
        ) if not value]
        report(False, "config", f"unset: {', '.join(missing)}")
        return False
    report(True, "config",
           f"endpoint {settings.exchange_url}, user "
           f"{settings.exchange_username.split('@')[0].split(chr(92))[-1]}@…, "
           f"mailbox {settings.exchange_email or '(from username)'}")

    # Which of the three trust answers is actually in effect. An operator who
    # set a CA bundle AND left CC_TLS_INSECURE=1 on should see both, and see
    # which one wins.
    kwargs = http_client.client_kwargs()
    parts = []
    if kwargs.get("verify"):
        parts.append(f"CA bundle {kwargs['verify']}")
    if kwargs.get("cert"):
        parts.append(f"client certificate {kwargs['cert']}")
    insecure = exchange._tls_insecure()
    if insecure:
        parts.append("CC_TLS_INSECURE=1 — verification OFF (this wins over the CA)")
    report(True, "trust", "; ".join(parts) or "system trust store, no client certificate")
    return True


def check_tls() -> None:
    """The handshake alone, before authentication — an internal-CA failure and
    a wrong password are two different days of work."""
    url = urlparse(settings.exchange_url)
    host, port = url.hostname or "", url.port or 443
    context = ssl.create_default_context(cafile=settings.ca_bundle or None)
    if exchange._tls_insecure():
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    if settings.client_cert:
        context.load_cert_chain(settings.client_cert, settings.client_key or None)
    try:
        with socket.create_connection((host, port), timeout=20) as raw:
            with context.wrap_socket(raw, server_hostname=host) as tls:
                peer = tls.getpeercert() or {}
                issuer = ", ".join(
                    v for rdn in peer.get("issuer", ()) for _, v in rdn
                ) or "(not reported — verification is off)"
                report(True, "tls",
                       f"{host}:{port} handshake ok, {tls.version()}, issuer {issuer}")
    except Exception as e:  # noqa: BLE001 — every failure here is one report line
        report(False, "tls", f"{host}:{port}: {type(e).__name__}: {e}")


def check_auth():
    """One cheap authenticated call. `root.refresh()` is a single GetFolder."""
    try:
        account = exchange.account()
        account.root.refresh()
        report(True, "ntlm", f"authenticated as {account.primary_smtp_address}")
        return account
    except Exception as e:  # noqa: BLE001
        report(False, "ntlm", f"{type(e).__name__}: {e}")
        return None


def check_inbox(account):
    try:
        inbox = account.inbox
        report(True, "inbox",
               f"{inbox.absolute} — {inbox.total_count} item(s), "
               f"{inbox.unread_count} unread")
        return inbox
    except Exception as e:  # noqa: BLE001
        report(False, "inbox", f"{type(e).__name__}: {e}")
        return None


def check_search(account) -> None:
    """Through the REAL translator, so this proves the query path the feed uses
    (`CC_FEED_QUERY` defaults to exactly this string), not a hand-written one."""
    query = "in:inbox newer_than:1d"
    try:
        filters, aqs, folder_name = exchange.translate_query(query)
        folder = exchange._resolve_folder(account, folder_name) if folder_name else account.inbox
        if aqs:
            rows = [i for i in folder.filter(aqs)[:200] if exchange._matches(i, filters)]
        else:
            rows = list((folder.filter(**filters) if filters else folder.all())[:200])
        report(True, "search", f"{query!r} → {len(rows)} message(s) (filters {filters})")
    except Exception as e:  # noqa: BLE001
        report(False, "search", f"{query!r}: {type(e).__name__}: {e}")


def check_calendar(account) -> None:
    from exchangelib import EWSDateTime

    start = datetime.now(timezone.utc)
    end = start + timedelta(days=7)
    try:
        events = list(account.calendar.view(
            start=EWSDateTime.from_datetime(start),
            end=EWSDateTime.from_datetime(end),
        )[:500])
        report(True, "calendar", f"7-day CalendarView → {len(events)} event(s)")
    except Exception as e:  # noqa: BLE001
        report(False, "calendar", f"{type(e).__name__}: {e}")


def check_version(account) -> None:
    try:
        version = account.version
        report(True, "server",
               f"{version.fullname} — API {version.api_version}, build {version.build}")
    except Exception as e:  # noqa: BLE001
        report(False, "server", f"{type(e).__name__}: {e}")


def check_headers(inbox) -> None:
    """THE two open questions. `InternetMessageId` is what the ledger keys on
    (D4) — without it every enrollment falls back to the uuid-derived form and
    a filed message re-enrolls. `Authentication-Results` is what a future
    Exchange unsubscribe would have to read; its ABSENCE is the expected
    answer on-premises, and recording that is the point."""
    try:
        newest = list(inbox.all().order_by("-datetime_received")[:1])
    except Exception as e:  # noqa: BLE001
        report(False, "headers", f"could not read the newest message: {type(e).__name__}: {e}")
        return
    if not newest:
        report(None, "headers", "the inbox is empty — nothing to inspect")
        return
    item = newest[0]
    message_id = (getattr(item, "message_id", "") or "").strip()
    report(bool(message_id), "headers.internet_message_id",
           message_id or "ABSENT — enrollment would fall back to the uuid-derived id")
    names = sorted({(h.name or "").lower() for h in (getattr(item, "headers", None) or [])})
    if not names:
        report(None, "headers.authentication_results",
               "the item exposed NO InternetMessageHeaders at all — the field may "
               "need an explicit only()/ItemShape on this server")
        return
    has_auth = "authentication-results" in names
    report(None, "headers.authentication_results",
           ("present" if has_auth else "absent")
           + f" ({len(names)} header(s) exposed; mail.unsubscribe stays withheld "
             "on Exchange either way)")


def main() -> int:
    if not check_config():
        return 1
    check_tls()
    account = check_auth()
    if account is None:
        return 1
    inbox = check_inbox(account)
    check_search(account)
    check_calendar(account)
    check_version(account)
    if inbox is not None:
        check_headers(inbox)
    print(f"\n{_checks - _failures}/{_checks} checks passed", flush=True)
    return 1 if _failures else 0


if __name__ == "__main__":
    sys.exit(main())
