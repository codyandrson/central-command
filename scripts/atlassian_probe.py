"""Read-only connectivity probe for Jira and Confluence (Jira Data Center
flavor design, 2026-09-23, decision 4).

    python scripts/atlassian_probe.py

Walks every endpoint the configured flavors actually use and prints one line
per check:

    PASS|FAIL|SKIP <product> <METHOD> <path> — <status> <first ~120 chars>

It reads `.env` through `central_command.config.settings`, makes only GETs and
the two POSTs that ask a question (JQL search, CQL search), and never prints a
token or an email address. Exit 0 when every non-SKIP check passed, 1 otherwise.

Why it exists: nothing about Jira Data Center can be verified from the
reference deployment, so the Data Center shapes in `integrations/jira.py` are
coded to Atlassian's published Data Center REST reference. This script is how
an operator proves them against a real instance — and it also reports the two
things the design record could NOT settle from the docs: the shape of
`description` on a real issue (ADF document vs wiki-markup string), and
whether epics surface as `parent` or as an "Epic Link" custom field.

Deliberately NOT routed through `jira._call`: that triggers the once-per-process
auth check, which is one of the things being probed. It uses the same auth and
trust seams (`jira._auth_kwargs()`, `integrations.http.client_kwargs()`) so a
PASS here means the real client's transport works too.

Pure Python + httpx, no shell tricks — it runs on Windows as-is.
"""

from __future__ import annotations

import asyncio
import pathlib
import re
import sys
from urllib.parse import urlencode

# Run from a checkout without installing it, and prefer THIS checkout over an
# editable install that points somewhere else (worktrees).
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import httpx

from central_command.config import settings
from central_command.integrations import confluence, jira
from central_command.integrations import http as http_client

BODY_CHARS = 120
_WS = re.compile(r"\s+")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

_failures = 0
_checks = 0


def _secrets() -> list[str]:
    return [v for v in (settings.jira_api_token, settings.jira_email,
                        settings.confluence_api_token, settings.confluence_email)
            if v]


def _scrub(text: str) -> str:
    for secret in _secrets():
        text = text.replace(secret, "<redacted>")
    return _EMAIL.sub("<redacted-email>", text)


def _snippet(text: str) -> str:
    collapsed = _WS.sub(" ", _scrub(text or "")).strip()
    return collapsed[:BODY_CHARS]


def info(message: str) -> None:
    print(f"INFO {message}", flush=True)


def skip(product: str, what: str, why: str) -> None:
    global _checks
    _checks += 1
    print(f"SKIP {product} {what} — {why}", flush=True)


def report(product: str, method: str, path: str, resp: httpx.Response | None,
           *, ok: bool | None = None, note: str = "") -> None:
    """One check line. `ok` defaults to the HTTP status being 2xx."""
    global _checks, _failures
    _checks += 1
    if resp is None:
        _failures += 1
        print(f"FAIL {product} {method} {path} — no response {note}", flush=True)
        return
    good = (200 <= resp.status_code < 300) if ok is None else ok
    if not good:
        _failures += 1
    tail = _snippet(resp.text)
    if note:
        tail = f"{note} | {tail}"
    print(f"{'PASS' if good else 'FAIL'} {product} {method} {path} — "
          f"{resp.status_code} {tail}", flush=True)


async def _get(base: str, path: str, auth: dict,
               method: str = "GET", body=None) -> httpx.Response | None:
    url = base.rstrip("/") + path
    try:
        async with httpx.AsyncClient(
            timeout=30, follow_redirects=True, **auth, **http_client.client_kwargs()
        ) as client:
            return await client.request(method, url, json=body)
    except Exception as exc:  # noqa: BLE001 — a probe reports, never raises
        print(f"FAIL transport {method} {path} — {type(exc).__name__}: "
              f"{_snippet(str(exc))}", flush=True)
        return None


def _json(resp: httpx.Response | None):
    if resp is None or not (200 <= resp.status_code < 300):
        return None
    try:
        return resp.json()
    except Exception:  # noqa: BLE001
        return None


# --- Jira ---------------------------------------------------------------------


async def probe_jira() -> None:
    if not settings.jira_base_url:
        skip("jira", "(all checks)", "CC_JIRA_BASE_URL is unset")
        return
    if not jira.configured():
        skip("jira", "(all checks)",
             "no native credentials (CC_JIRA_API_TOKEN, plus CC_JIRA_EMAIL "
             "under basic auth)")
        return

    flavor = jira._flavor()
    base, auth = settings.jira_base_url, jira._auth_kwargs()
    info(f"jira base={base} flavor={flavor} "
         f"(CC_JIRA_API_FLAVOR={settings.jira_api_flavor!r}) "
         f"auth_mode={jira.auth_mode()} "
         f"(CC_JIRA_AUTH_MODE={settings.jira_auth_mode!r})")

    # serverInfo on BOTH versions: which one answers is itself the flavor
    # evidence, and deploymentType is what the client cross-checks.
    for version_flavor in ("server", "cloud"):
        path = jira._api("/serverInfo", version_flavor)
        resp = await _get(base, path, auth)
        body = _json(resp) or {}
        note = ""
        if isinstance(body, dict):
            note = (f"deploymentType={body.get('deploymentType')!r} "
                    f"version={body.get('version')!r}")
        report("jira", "GET", path, resp, note=note)

    myself = jira._myself_path()
    resp = await _get(base, myself, auth)
    report("jira", "GET", myself, resp)

    path = jira._api("/field")
    resp = await _get(base, path, auth)
    fields = _json(resp)
    note = ""
    if isinstance(fields, list):
        names = {str(f.get("name") or "") for f in fields if isinstance(f, dict)}
        ids = {str(f.get("id") or "") for f in fields if isinstance(f, dict)}
        note = (f"{len(fields)} fields; 'Epic Link' present="
                f"{'Epic Link' in names}; 'parent' in field list="
                f"{'parent' in ids}")
    report("jira", "GET", path, resp, note=note)

    path = jira._api("/project") if flavor == "server" else \
        jira._api("/project/search?maxResults=1")
    resp = await _get(base, path, auth)
    body = _json(resp)
    if isinstance(body, list):
        note = f"{len(body)} projects (unpaginated list body)"
    elif isinstance(body, dict):
        note = f"total={body.get('total')} isLast={body.get('isLast')}"
    else:
        note = ""
    report("jira", "GET", path, resp, note=note)

    # One issue, newest first — the only place the real `description` shape and
    # the real epic-structure shape can be observed.
    # BOUNDED on purpose: Jira Cloud refuses an unbounded JQL query on
    # /search/jql outright ("Unbounded JQL queries are not allowed here"),
    # so a bare "order by updated desc" probes nothing (measured 2026-09-23).
    jql = "created >= -365d order by updated desc"
    fields_asked = ["summary", "description", "parent", "issuetype"]
    if flavor == "server":
        path = jira._api("/search")
        body_out = {"jql": jql, "startAt": 0, "maxResults": 1,
                    "fields": fields_asked}
    else:
        path = jira._api("/search/jql")
        body_out = {"jql": jql, "maxResults": 1, "fields": fields_asked}
    resp = await _get(base, path, auth, method="POST", body=body_out)
    page = _json(resp)
    issue_key = None
    note = ""
    if isinstance(page, dict):
        rows = page.get("issues") or []
        if rows and isinstance(rows[0], dict):
            issue_key = rows[0].get("key")
            issue_fields = rows[0].get("fields") or {}
            desc = issue_fields.get("description")
            kind = ("dict = ADF" if isinstance(desc, dict)
                    else "str = wiki markup" if isinstance(desc, str)
                    else "None")
            note = (f"{len(rows)} row(s); fields.description type="
                    f"{type(desc).__name__} ({kind}); parent present="
                    f"{'parent' in issue_fields}")
        else:
            note = "0 rows — no issue to sample description/parent from"
    report("jira", "POST", path, resp, note=note)

    if issue_key:
        path = jira._api(f"/issue/{issue_key}/transitions")
        resp = await _get(base, path, auth)
        body = _json(resp) or {}
        note = ""
        if isinstance(body, dict):
            names = [t.get("name") for t in (body.get("transitions") or [])
                     if isinstance(t, dict)]
            note = f"{len(names)} transitions: {', '.join(str(n) for n in names)}"
        report("jira", "GET", path, resp, note=note)
    else:
        skip("jira", "GET .../transitions", "no issue to read transitions for")

    if flavor == "server":
        # Data Center has no filter SEARCH and no dashboard search/gadget
        # catalog. These two read-only lists are what it does have — they are
        # probed so the record says what IS there, not only what isn't.
        for path in (jira._api("/filter/favourite"), jira._api("/dashboard")):
            resp = await _get(base, path, auth)
            report("jira", "GET", path, resp)
        skip("jira", "GET /filter/search, /dashboard/search, /dashboard/gadgets",
             "Cloud-only endpoints; withheld from packs under flavor 'server'")
    else:
        for path in (jira._api("/filter/search?maxResults=1"),
                     jira._api("/dashboard/search?maxResults=1"),
                     jira._api("/dashboard/gadgets")):
            resp = await _get(base, path, auth)
            report("jira", "GET", path, resp)
        skip("jira", "GET /filter/favourite, /dashboard",
             "Data-Center-only read shapes; not used under flavor 'cloud'")


# --- Confluence ---------------------------------------------------------------


async def probe_confluence() -> None:
    if not confluence.configured():
        skip("confluence", "(all checks)",
             "not configured (CC_CONFLUENCE_BASE_URL + credentials)")
        return

    flavor = confluence._flavor()
    base, auth = settings.confluence_base_url, confluence._auth_kwargs()
    info(f"confluence base={base} flavor={flavor} "
         f"(CC_CONFLUENCE_API_FLAVOR={settings.confluence_api_flavor!r}) "
         f"auth_mode={confluence.auth_mode()} "
         f"(CC_CONFLUENCE_AUTH_MODE={settings.confluence_auth_mode!r})")

    path = (confluence._crud("/space?limit=1") if flavor == "server"
            else confluence._crud("/spaces?limit=1"))
    resp = await _get(base, path, auth)
    body = _json(resp) or {}
    results = body.get("results") if isinstance(body, dict) else None
    note = f"{len(results)} space(s) on page 1" if isinstance(results, list) else ""
    report("confluence", "GET", path, resp, note=note)

    # CQL search is v1-only on BOTH flavors, permanently (Atlassian's own docs).
    path = confluence._v1("/search?" + urlencode({"cql": "type=page", "limit": 1}))
    resp = await _get(base, path, auth)
    body = _json(resp) or {}
    page_id = None
    if isinstance(body, dict):
        rows = body.get("results") or []
        if rows and isinstance(rows[0], dict):
            content = rows[0].get("content") if isinstance(rows[0].get("content"), dict) else rows[0]
            page_id = str(content.get("id")) if content.get("id") else None
    report("confluence", "GET", path, resp,
           note=f"page_id={page_id}" if page_id else "0 results")

    if not page_id:
        skip("confluence", "GET <page> body.storage", "CQL search returned no page")
        return
    path = (confluence._crud(f"/content/{page_id}?expand=body.storage,version,space")
            if flavor == "server"
            else confluence._crud(f"/pages/{page_id}?body-format=storage"))
    resp = await _get(base, path, auth)
    body = _json(resp) or {}
    storage = confluence._storage_value(body) if isinstance(body, dict) else None
    note = (f"body.storage present, {len(storage)} chars" if storage
            else "NO body.storage in the response")
    report("confluence", "GET", path, resp,
           ok=bool(resp and 200 <= resp.status_code < 300 and storage),
           note=note)


async def main() -> int:
    await probe_jira()
    await probe_confluence()
    print(f"\n{_checks} checks, {_failures} failed", flush=True)
    return 1 if _failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
