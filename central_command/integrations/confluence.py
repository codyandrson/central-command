"""Native Confluence client, Phase 1 read-only (D-confluence).

Same posture as `jira.py`: a plain token-REST API is better served by a
native client that lives in git and is tested by the suite, no
atlassian-python-api dependency. Envelope shape mirrors jira.py's —
`{ok, kind: "confluence", operation, ...}` on success,
`{ok: False, error, status, operation}` on failure — so charters and tools
reason about both providers the same way.

**Flavor seam (`CC_CONFLUENCE_API_FLAVOR`):**
- "cloud" (default): CRUD-shaped reads (spaces/pages/children/labels/versions)
  go through REST API v2 (`/wiki/api/v2/...`, cursor pagination via
  `_links.next`). CQL search has NO v2 equivalent — it is v1-only,
  permanently, per Atlassian's own docs — so `search()` always calls
  `/wiki/rest/api/search`. Attachment listing is also v1
  (`/wiki/rest/api/content/{id}/child/attachment`) because v2 has no
  attachments-by-page-id list endpoint as of this writing.
- "server" (Data Center 9.x): everything goes through v1
  (`{base}/rest/api/...`), because Server/DC never shipped the v2 API.
  **These paths are coded to Atlassian's published DC 9.x documentation and
  are VERIFY-ON-SITE, not proven against a live instance** — the same
  posture jira.py takes for its own DC auth-mode comment: don't guess DC
  behavior beyond what the docs say, and flag the gap rather than hide it.

Page bodies are always requested/returned in **storage** representation
(`body-format=storage` on v2; `expand=body.storage` on v1) — storage is the
one representation that round-trips macros and is documented for both
flavors, unlike `view` (rendered HTML, lossy) or `atlas_doc_format`
(Cloud-only).

Pagination: both v2 (cursor) and v1 (offset) responses carry a `_links.next`
relative path when more results exist — the same convention read by
`jira.py`'s `isLast`/token loop. `_paginate` follows that link generically
for both flavors, bounded by `MAX_PAGES` (a bounded crawl, never an
unbounded one — the same shape as `jira.search_issues`'s 10-page cap).
"""

from __future__ import annotations

import asyncio
import re
from urllib.parse import urlencode

import httpx

from central_command.config import settings
from central_command.integrations import http as http_client

ID_RE = re.compile(r"^\d+$")
MAX_CQL_CHARS = 2000
MAX_PAGES = 5          # a bounded crawl — never an unbounded one
DEFAULT_PAGE_LIMIT = 25

# Exposed as a module attribute so tests can monkeypatch it without a real
# wait — the same pattern runtime/tools.py uses for its own retry sleep.
_sleep = asyncio.sleep


class ConfluenceError(Exception):
    pass


def configured() -> bool:
    if not settings.confluence_base_url:
        return False
    if settings.confluence_auth_mode == "bearer":
        return bool(settings.confluence_api_token)
    return bool(settings.confluence_email and settings.confluence_api_token)


def _require_configured(op: str) -> None:
    if not configured():
        raise ConfluenceError(
            f"{op} — Confluence is not configured (set CC_CONFLUENCE_BASE_URL "
            "and credentials matching CC_CONFLUENCE_AUTH_MODE)"
        )


# --- validation (model-controlled strings stop here) --------------------------


def _id(value: str, field: str = "page_id") -> str:
    if not isinstance(value, str) or not ID_RE.match(value or ""):
        raise ConfluenceError(f"bad {field} {value!r} — must be a numeric id string")
    return value


def _cql(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_CQL_CHARS:
        raise ConfluenceError(
            f"search — cql must be a non-empty string under {MAX_CQL_CHARS} chars"
        )
    return value


def _limit(value: int | None, default: int = DEFAULT_PAGE_LIMIT, cap: int = 100) -> int:
    if value is None:
        return default
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ConfluenceError(f"limit {value!r} must be a positive integer")
    return min(value, cap)


# --- write validation (Phase 2 — model-controlled strings stop here too) ------

MAX_TITLE_CHARS = 255
MAX_LABEL_CHARS = 255
MAX_FILENAME_CHARS = 255
MAX_BODY_CHARS = 500_000
MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024
SPACE_KEY_RE = re.compile(r"^[A-Z0-9~][A-Z0-9._~-]*$")


def _title(value: str, field: str = "title", cap: int = MAX_TITLE_CHARS) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > cap:
        raise ConfluenceError(f"{field} must be a non-empty string under {cap} chars")
    return value


def _body_text(value: str) -> str:
    if not isinstance(value, str) or len(value) > MAX_BODY_CHARS:
        raise ConfluenceError(f"body_storage must be a string under {MAX_BODY_CHARS} chars")
    return value


def _space_ref(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfluenceError("space_id_or_key must be a non-empty string")
    return value


def _space_key(value: str) -> str:
    if not isinstance(value, str) or not SPACE_KEY_RE.match(value or ""):
        raise ConfluenceError(f"bad space key {value!r} — must match {SPACE_KEY_RE.pattern}")
    return value


def _filename(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_FILENAME_CHARS:
        raise ConfluenceError(f"filename must be a non-empty string under {MAX_FILENAME_CHARS} chars")
    return value


def _labels(values: list) -> list[str]:
    if not isinstance(values, list) or not values:
        raise ConfluenceError("labels must be a non-empty list of strings")
    out = []
    for v in values:
        if not isinstance(v, str) or not v.strip() or len(v) > MAX_LABEL_CHARS:
            raise ConfluenceError(
                f"bad label {v!r} — must be a non-empty string under {MAX_LABEL_CHARS} chars"
            )
        out.append(v)
    return out


def _version_number(value: int, field: str = "expected_version") -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ConfluenceError(f"{field} must be a positive integer")
    return value


# --- transport ----------------------------------------------------------------


def _auth_kwargs() -> dict:
    """Cloud (default) sends Basic email+token; Server/DC sends a Bearer PAT
    — same auth-mode-only seam as jira.py's `_auth_kwargs`, no endpoint-shape
    changes here."""
    if settings.confluence_auth_mode == "bearer":
        return {"headers": {"Authorization": f"Bearer {settings.confluence_api_token}"}}
    return {"auth": (settings.confluence_email, settings.confluence_api_token)}


def _retry_after(resp: httpx.Response, default: float = 1.0, cap: float = 30.0) -> float:
    raw = resp.headers.get("retry-after")
    try:
        return min(max(float(raw), 0.0), cap) if raw else default
    except ValueError:
        return default


async def _call(
    method: str, path: str, params: dict | None = None, json: dict | list | None = None
) -> httpx.Response:
    """One outbound call, with one 429 retry honoring Retry-After — cloud
    rate-limits real traffic and a single bounded retry is enough for a read
    (the runtime tools' own `_read_with_retry` layers a second, generic
    retry on top of whatever this raises). `json`, added for Phase 2 writes,
    is resent unchanged on the retry — same idempotence assumption the caller
    already makes about `params`."""
    url = settings.confluence_base_url.rstrip("/") + path
    async with httpx.AsyncClient(
        timeout=30, follow_redirects=True, **_auth_kwargs(), **http_client.client_kwargs()
    ) as client:
        resp = await client.request(method, url, params=params, json=json)
        if resp.status_code == 429:
            await _sleep(_retry_after(resp))
            resp = await client.request(method, url, params=params, json=json)
        return resp


async def _call_multipart(path: str, filename: str, content_bytes: bytes) -> httpx.Response:
    """Attachment upload — same auth/trust seams as `_call`, but multipart with
    the exact header Confluence requires to accept a non-XSRF-token POST:
    `X-Atlassian-Token: no-check`."""
    url = settings.confluence_base_url.rstrip("/") + path
    files = {"file": (filename, content_bytes)}
    data = {"minorEdit": "true"}
    headers = {"X-Atlassian-Token": "no-check"}
    async with httpx.AsyncClient(
        timeout=30, follow_redirects=True, **_auth_kwargs(), **http_client.client_kwargs()
    ) as client:
        resp = await client.request("POST", url, files=files, data=data, headers=headers)
        if resp.status_code == 429:
            await _sleep(_retry_after(resp))
            resp = await client.request("POST", url, files=files, data=data, headers=headers)
        return resp


def _check(resp: httpx.Response, op: str, ref: str | None = None) -> None:
    if 200 <= resp.status_code < 300:
        return
    r = f" for {ref}" if ref else ""
    detail = resp.text[:300]
    st = resp.status_code
    if st == 404:
        raise ConfluenceError(f"{op} — not found{r}")
    if st in (401, 403):
        raise ConfluenceError(
            f"{op} — auth failed ({st}){r} — check CC_CONFLUENCE_EMAIL/"
            "CC_CONFLUENCE_API_TOKEN/CC_CONFLUENCE_AUTH_MODE"
        )
    if st == 429:
        raise ConfluenceError(f"{op} — rate limited{r} — retry later")
    raise ConfluenceError(f"{op} — provider rejected the request ({st}){r}: {detail}")


# --- endpoint seam (flavor -> base path) ---------------------------------------


def _flavor() -> str:
    return settings.confluence_api_flavor


def _v2(path: str) -> str:
    """Cloud-only CRUD base — never called under flavor 'server'."""
    return "/wiki/api/v2" + path


def _v1(path: str) -> str:
    """v1 base for the flavor currently configured: `/wiki/rest/api/...` on
    Cloud, `/rest/api/...` on Server/DC (server has no `/wiki` prefix)."""
    prefix = "/rest/api" if _flavor() == "server" else "/wiki/rest/api"
    return prefix + path


def _crud(path: str) -> str:
    """The base for spaces/pages/children/labels/versions: v2 on Cloud, v1 on
    Server/DC (Server/DC never shipped a v2 API)."""
    return _v1(path) if _flavor() == "server" else _v2(path)


def _contextualize(path: str) -> str:
    """Cloud responses emit `_links` paths relative to the /wiki CONTEXT, not
    the site root (`_links.base` is `https://site/wiki`, `next` reads
    `/rest/api/search?...`) — measured live 2026-08-21. Joining them onto the
    bare base_url lands on the Jira root. Server/DC paths are already
    site-root-relative and pass through untouched."""
    if _flavor() == "server" or path.startswith("/wiki"):
        return path
    return "/wiki" + (path if path.startswith("/") else "/" + path)


# --- pagination (shared: both v2 cursors and v1 offsets expose _links.next) ---


async def _paginate(first_path: str, limit: int | None, key: str = "results") -> tuple[list[dict], bool]:
    """GET `first_path`, then follow `_links.next` up to MAX_PAGES or until
    `limit` items are collected. Returns (items, truncated) — `truncated`
    means more rows exist than what's returned, the same honesty rule
    jira.search_issues follows (never let an empty tail read as "no more")."""
    items: list[dict] = []
    path = first_path
    for _ in range(MAX_PAGES):
        resp = await _call("GET", path)
        _check(resp, "paginate")
        body = resp.json()
        items.extend(body.get(key) or [])
        if limit is not None and len(items) >= limit:
            return items[:limit], bool((body.get("_links") or {}).get("next")) or len(items) > limit
        nxt = (body.get("_links") or {}).get("next")
        if not nxt:
            return items, False
        path = _contextualize(nxt)
    return items[:limit] if limit else items, True


# --- row normalizers (provider shape -> the flat shape agents reason over) ----


def _webui_url(obj: dict) -> str | None:
    webui = (obj.get("_links") or {}).get("webui")
    if not webui:
        return None
    base = settings.confluence_base_url.rstrip("/")
    # Server/DC's webui link is site-root-relative ("/display/SPACE/Title");
    # Cloud's is relative to the /wiki context ("/spaces/SPACE/..."), so it
    # needs the /wiki prefix or the link 404s on the Jira root — measured
    # live 2026-08-21.
    return base + _contextualize(webui)


def _space_row(s: dict) -> dict:
    return {
        "id": s.get("id"),
        "key": s.get("key"),
        "name": s.get("name"),
        "type": s.get("type"),
        "status": s.get("status"),
        "url": _webui_url(s),
    }


def _storage_value(obj: dict) -> str | None:
    storage = (obj.get("body") or {}).get("storage")
    return storage.get("value") if isinstance(storage, dict) else None


def _page_row(p: dict, *, with_body: bool = False) -> dict:
    space = p.get("space") if isinstance(p.get("space"), dict) else {}
    version = p.get("version") if isinstance(p.get("version"), dict) else {}
    row = {
        "id": p.get("id"),
        "title": p.get("title"),
        "status": p.get("status"),
        "space_key": space.get("key"),
        "space_id": p.get("spaceId") or space.get("id"),
        "version": version.get("number"),
        "url": _webui_url(p),
    }
    if with_body:
        row["body"] = _storage_value(p)
    return row


def _attachment_row(a: dict) -> dict:
    return {
        "id": a.get("id"),
        "title": a.get("title"),
        "media_type": (a.get("metadata") or {}).get("mediaType"),
        "file_size": (a.get("extensions") or {}).get("fileSize"),
        "url": _webui_url(a) or (a.get("_links") or {}).get("download"),
    }


def _label_row(entry: dict) -> dict:
    return {"id": entry.get("id"), "name": entry.get("name"), "prefix": entry.get("prefix")}


def _version_row(v: dict) -> dict:
    by = v.get("by") if isinstance(v.get("by"), dict) else {}
    return {
        "number": v.get("number"),
        "when": v.get("when") or v.get("createdAt"),
        "by": by.get("displayName"),
        "message": v.get("message"),
    }


# --- reads ----------------------------------------------------------------


async def list_spaces(limit: int | None = None) -> dict:
    _require_configured("listSpaces")
    bound = _limit(limit)
    path = _crud(f"/space?limit={bound}") if _flavor() == "server" else _crud(f"/spaces?limit={bound}")
    rows, truncated = await _paginate(path, limit)
    return {"ok": True, "kind": "confluence", "operation": "listSpaces",
            "spaces": [_space_row(s) for s in rows], "count": len(rows), "truncated": truncated}


async def get_page(page_id: str) -> dict:
    _require_configured("getPage")
    pid = _id(page_id)
    if _flavor() == "server":
        path = _crud(f"/content/{pid}?expand=body.storage,version,space")
    else:
        path = _crud(f"/pages/{pid}?body-format=storage")
    resp = await _call("GET", path)
    _check(resp, "getPage", pid)
    return {"ok": True, "kind": "confluence", "operation": "getPage",
            "page": _page_row(resp.json(), with_body=True)}


async def search(cql: str, limit: int | None = None) -> dict:
    """CQL search — v1-ONLY on both flavors: Cloud's v2 API has no search
    endpoint at all (permanent, per Atlassian's own docs), and Server/DC
    never had a v2 API to begin with."""
    _require_configured("search")
    q = _cql(cql)
    bound = _limit(limit)
    path = _v1("/search?" + urlencode({"cql": q, "limit": bound}))
    rows, truncated = await _paginate(path, limit)
    results = []
    for r in rows:
        content = r.get("content") if isinstance(r.get("content"), dict) else {}
        row = _page_row(content) if content else {}
        row["excerpt"] = r.get("excerpt")
        results.append(row)
    return {"ok": True, "kind": "confluence", "operation": "search",
            "cql": q, "results": results, "count": len(results), "truncated": truncated}


async def list_children(page_id: str, limit: int | None = None) -> dict:
    _require_configured("listChildren")
    pid = _id(page_id)
    bound = _limit(limit)
    if _flavor() == "server":
        path = _crud(f"/content/{pid}/child/page?limit={bound}")
    else:
        path = _crud(f"/pages/{pid}/children?limit={bound}")
    rows, truncated = await _paginate(path, limit)
    return {"ok": True, "kind": "confluence", "operation": "listChildren",
            "page_id": pid, "children": [_page_row(c) for c in rows],
            "count": len(rows), "truncated": truncated}


async def list_attachments(page_id: str, limit: int | None = None) -> dict:
    """Always v1 — v2 has no attachments-by-page-id list endpoint as of this
    writing, on either flavor."""
    _require_configured("listAttachments")
    pid = _id(page_id)
    bound = _limit(limit)
    path = _v1(f"/content/{pid}/child/attachment?limit={bound}")
    rows, truncated = await _paginate(path, limit)
    return {"ok": True, "kind": "confluence", "operation": "listAttachments",
            "page_id": pid, "attachments": [_attachment_row(a) for a in rows],
            "count": len(rows), "truncated": truncated}


async def list_labels(page_id: str, limit: int | None = None) -> dict:
    _require_configured("listLabels")
    pid = _id(page_id)
    bound = _limit(limit)
    if _flavor() == "server":
        path = _crud(f"/content/{pid}/label?limit={bound}")
    else:
        path = _crud(f"/pages/{pid}/labels?limit={bound}")
    rows, truncated = await _paginate(path, limit)
    return {"ok": True, "kind": "confluence", "operation": "listLabels",
            "page_id": pid, "labels": [_label_row(l) for l in rows],
            "count": len(rows), "truncated": truncated}


async def get_page_versions(page_id: str, limit: int | None = None) -> dict:
    _require_configured("getPageVersions")
    pid = _id(page_id)
    bound = _limit(limit, default=25)
    if _flavor() == "server":
        path = _crud(f"/content/{pid}/version?limit={bound}")
    else:
        path = _crud(f"/pages/{pid}/versions?limit={bound}")
    rows, truncated = await _paginate(path, limit)
    return {"ok": True, "kind": "confluence", "operation": "getPageVersions",
            "page_id": pid, "versions": [_version_row(v) for v in rows],
            "count": len(rows), "truncated": truncated}


# --- writes (Phase 2, Executor-called, credential-holding side) ---------------
#
# Every function here is called ONLY by the Executor after operator approval —
# the trust boundary is the import graph (runtime/ never imports this at write
# time; it only calls the confluence_* READ tools). Content that rides a
# proposal (upload_attachment's bytes) is captured at PROPOSE time and handed
# in whole — these functions never re-fetch from anywhere else (the
# mcp.sync_source doctrine: the bytes the operator reviewed are the bytes that
# land).


async def _resolve_space_id(space_ref: str) -> str:
    """Cloud page-create needs a numeric spaceId; a key is resolved via the v1
    space endpoint (works identically on both flavors, since Server/DC only
    has v1 to begin with)."""
    if ID_RE.match(space_ref):
        return space_ref
    resp = await _call("GET", _v1(f"/space/{space_ref}"))
    _check(resp, "resolveSpace", space_ref)
    sid = resp.json().get("id")
    if sid is None:
        raise ConfluenceError(f"resolveSpace — space {space_ref!r} has no id")
    return str(sid)


async def create_page(
    space_id_or_key: str, title: str, body_storage: str, parent_id: str | None = None
) -> dict:
    """Server addressing is key-based (space.key in the body): pass a space
    KEY, not a numeric id, when CC_CONFLUENCE_API_FLAVOR=server. Cloud accepts
    either — a key is resolved to the numeric spaceId the v2 API requires."""
    _require_configured("createPage")
    space_ref = _space_ref(space_id_or_key)
    t = _title(title)
    b = _body_text(body_storage)
    pid = _id(parent_id, "parent_id") if parent_id else None
    if _flavor() == "server":
        payload: dict = {
            "type": "page", "title": t, "space": {"key": space_ref},
            "body": {"storage": {"value": b, "representation": "storage"}},
        }
        if pid:
            payload["ancestors"] = [{"id": pid}]
        resp = await _call("POST", _crud("/content"), json=payload)
    else:
        sid = await _resolve_space_id(space_ref)
        payload = {
            "spaceId": sid, "status": "current", "title": t,
            "body": {"representation": "storage", "value": b},
        }
        if pid:
            payload["parentId"] = pid
        resp = await _call("POST", _crud("/pages"), json=payload)
    _check(resp, "createPage")
    return {"ok": True, "kind": "confluence", "operation": "createPage", "page": _page_row(resp.json())}


async def update_page(page_id: str, title: str, body_storage: str, expected_version: int) -> dict:
    """Refuses to write over a version newer than `expected_version` — the
    operator approved a diff against that version, and executing over a newer
    one would make the review theater (the mcp.sync_source doctrine again,
    applied to a version number instead of file bytes)."""
    _require_configured("updatePage")
    pid = _id(page_id)
    t = _title(title)
    b = _body_text(body_storage)
    ev = _version_number(expected_version)
    current = await get_page(pid)
    page = current["page"]
    actual = page["version"]
    if actual != ev:
        raise ConfluenceError(
            f"updatePage — stale version for page {pid}: this proposal was "
            f"approved against version {ev}, but the current version is "
            f"{actual}. Read the page again and redraft — executing over a "
            "newer version would silently overwrite an edit nobody reviewed."
        )
    new_version = ev + 1
    if _flavor() == "server":
        payload = {
            "id": pid, "type": "page", "title": t,
            "space": {"key": page["space_key"]},
            "body": {"storage": {"value": b, "representation": "storage"}},
            "version": {"number": new_version},
        }
        resp = await _call("PUT", _crud(f"/content/{pid}"), json=payload)
    else:
        payload = {
            "id": pid, "status": "current", "title": t,
            "spaceId": page["space_id"],
            "body": {"representation": "storage", "value": b},
            "version": {"number": new_version},
        }
        resp = await _call("PUT", _crud(f"/pages/{pid}"), json=payload)
    _check(resp, "updatePage", pid)
    return {"ok": True, "kind": "confluence", "operation": "updatePage", "page": _page_row(resp.json())}


async def move_page(page_id: str, new_parent_id: str) -> dict:
    """Both flavors' update endpoints are full replaces, so this carries the
    page's own current title/body forward unchanged and only repoints the
    parent — simplest correct implementation, not a dedicated 'move' verb
    (Cloud's v2 API has none; Server/DC's v1 PUT is a full content update)."""
    _require_configured("movePage")
    pid = _id(page_id)
    npid = _id(new_parent_id, "new_parent_id")
    current = await get_page(pid)
    page = current["page"]
    new_version = page["version"] + 1
    if _flavor() == "server":
        payload = {
            "id": pid, "type": "page", "title": page["title"],
            "space": {"key": page["space_key"]},
            "body": {"storage": {"value": page["body"], "representation": "storage"}},
            "version": {"number": new_version},
            "ancestors": [{"id": npid}],
        }
        resp = await _call("PUT", _crud(f"/content/{pid}"), json=payload)
    else:
        payload = {
            "id": pid, "status": "current", "title": page["title"],
            "spaceId": page["space_id"], "parentId": npid,
            "body": {"representation": "storage", "value": page["body"]},
            "version": {"number": new_version},
        }
        resp = await _call("PUT", _crud(f"/pages/{pid}"), json=payload)
    _check(resp, "movePage", pid)
    return {"ok": True, "kind": "confluence", "operation": "movePage", "page": _page_row(resp.json())}


async def trash_page(page_id: str) -> dict:
    """Cloud DELETE /pages/{id} moves to trash by default (no `draft` param
    sent, so the current — not a draft — page is trashed). Server/DC DELETE
    /content/{id} trashes a current page on its first call too (a second
    DELETE on an already-trashed page purges it — out of scope here)."""
    _require_configured("trashPage")
    pid = _id(page_id)
    path = _crud(f"/content/{pid}") if _flavor() == "server" else _crud(f"/pages/{pid}")
    resp = await _call("DELETE", path)
    _check(resp, "trashPage", pid)
    return {"ok": True, "kind": "confluence", "operation": "trashPage", "page_id": pid}


async def upload_attachment(page_id: str, filename: str, content_bytes: bytes) -> dict:
    _require_configured("uploadAttachment")
    pid = _id(page_id)
    fname = _filename(filename)
    if not isinstance(content_bytes, (bytes, bytearray)):
        raise ConfluenceError("uploadAttachment — content_bytes must be bytes")
    if len(content_bytes) > MAX_ATTACHMENT_BYTES:
        raise ConfluenceError(
            f"uploadAttachment — attachment exceeds {MAX_ATTACHMENT_BYTES} bytes"
        )
    resp = await _call_multipart(_v1(f"/content/{pid}/child/attachment"), fname, bytes(content_bytes))
    _check(resp, "uploadAttachment", pid)
    rows = resp.json().get("results") or []
    return {
        "ok": True, "kind": "confluence", "operation": "uploadAttachment",
        "page_id": pid, "attachment": _attachment_row(rows[0]) if rows else None,
    }


async def set_labels(page_id: str, labels: list[str]) -> dict:
    """ADDITIVE — adds the given labels, never removes or replaces existing
    ones (Confluence's label-write endpoint is add-only; see `remove_labels`
    for the explicit, non-magic way to take one off)."""
    _require_configured("setLabels")
    pid = _id(page_id)
    labs = _labels(labels)
    payload = [{"prefix": "global", "name": name} for name in labs]
    resp = await _call("POST", _v1(f"/content/{pid}/label"), json=payload)
    _check(resp, "setLabels", pid)
    rows = resp.json().get("results") or []
    return {
        "ok": True, "kind": "confluence", "operation": "setLabels",
        "page_id": pid, "labels": [_label_row(r) for r in rows] if rows else labs,
    }


async def remove_labels(page_id: str, labels: list[str]) -> dict:
    """The explicit counterpart to `set_labels`'s add-only write — one DELETE
    per label, the only shape Confluence's v1 label API supports."""
    _require_configured("removeLabels")
    pid = _id(page_id)
    labs = _labels(labels)
    for name in labs:
        resp = await _call("DELETE", _v1(f"/content/{pid}/label/{name}"))
        _check(resp, "removeLabels", pid)
    return {"ok": True, "kind": "confluence", "operation": "removeLabels", "page_id": pid, "removed": labs}


async def create_space(key: str, name: str, description: str = "") -> dict:
    """NOT reversible here — no delete-space capability is exposed (deleting a
    space deletes every page in it, a bigger one-way door than this client
    takes on lightly). A mistaken space stays until removed by hand in the
    Confluence UI."""
    _require_configured("createSpace")
    k = _space_key(key)
    nm = _title(name, field="name")
    path = _crud("/space") if _flavor() == "server" else _crud("/spaces")
    resp = await _call(
        "POST", path,
        json={"key": k, "name": nm,
              "description": {"plain": {"value": description or "", "representation": "plain"}}},
    )
    _check(resp, "createSpace", k)
    return {"ok": True, "kind": "confluence", "operation": "createSpace", "space": _space_row(resp.json())}
