"""Native Jira Cloud client (D23) — Central Command's Jira surface without n8n.

The toolbox rule (the operator, 2026-07-20): n8n earns its place where it has already
solved a hard integration problem (the Gmail OAuth in cc-email-facade); a
plain token-REST API like Jira is better served by a native client that lives
in git, is tested by the suite, and grows without editing a live workflow.

The contract mirrors lib-jira's envelope, so callers see the SAME shapes the
façade returned: `{ok, kind: "jira", operation, issue|issues|transitions|link,
...}`. Input validation of model-controlled strings (issue keys, dates, link
types) happens here — the last stop before the provider.

**Cutover:** while CC_JIRA_EMAIL / CC_JIRA_API_TOKEN are unset, every call
falls through to the n8n façade, so shipping this module changes nothing
until the operator adds credentials; rollback is deleting them again.
"""

from __future__ import annotations

import functools
import re
import time
from datetime import date

import httpx

from central_command.config import settings
from central_command.integrations import http as http_client
from central_command.integrations import n8n_facade

KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,49}-[1-9][0-9]{0,9}$")
PROJECT_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,49}$")
LINK_TYPES = ("Blocks", "Relates", "Duplicate")
# Proven live against this instance: Task/Story/Epic/Subtask (no hyphen,
# subtask:true). "Sub-task" (hyphenated) is kept too — company-managed
# projects commonly use that spelling. Issue types are PER-PROJECT, so this
# list is a best-effort guard, not the final word; Jira's own 400 (already
# surfaced verbatim by `_check`) is the real validator.
ISSUE_TYPES = ("Task", "Bug", "Story", "Epic", "Subtask", "Sub-task")
# Ownership (`assignee`), dependencies (`issuelinks`), staleness (`updated`)
# and epic structure (`parent`) were added 2026-07-25: the D7 hygiene project
# stalled on their absence and jira-expert DECLARED the gap twice rather than
# guessing ("the tools never return an `assignee` or `links` field … I can't
# measure days-since-update"). They are exactly the axes its hygiene doctrine
# reasons about — and without `issuelinks` it could not check whether a link
# already existed before proposing `linkIssues`.
FIELDS = ("summary,description,priority,status,issuetype,labels,duedate,"
          "created,updated,assignee,parent,issuelinks")

# Search rows are per-issue in a board-wide sweep, so they carry the cheap
# scalars and only a link COUNT — the full link list would blow the agent's
# token budget and get string-truncated, which reads as "no more links" and is
# worse than an honest count. Drill into a specific issue with getIssue.
SEARCH_FIELDS = ["summary", "status", "issuetype", "labels", "duedate",
                 "updated", "assignee", "parent", "issuelinks"]

# An issue with more links than this is pathological; report the overflow
# honestly rather than silently showing a prefix (the no-silent-caps rule).
MAX_LINKS = 25

# Custom fields are per-INSTANCE: their ids (customfield_10073) mean nothing
# across Jiras, so nothing here may hard-code one — ask the instance what it
# declares, the same rule the gadget-prefs incident produced (2026-08-10).
# Added 2026-08-11: the operator created four cost/benefit fields, populated
# them, and jira-expert could not see them at all, because FIELDS/SEARCH_FIELDS
# are an allowlist and Jira returns exactly what you ask for.
CUSTOM_FIELDS_TTL = 300.0   # seconds; a field added in the UI is visible within
                            # 5 minutes, WITHOUT a cc-uvicorn restart — the
                            # whole point is that the operator's new field shows
                            # up. Process-lifetime caching would reproduce the
                            # bug for anyone who never restarts.
_custom_fields_cache: tuple[float, list[dict]] | None = None

# A custom field's WRITE shape is decided by `schema.custom`, never by
# `schema.type` — the four cost/benefit fields are type "string" and take an
# ADF document, because they are textareas. Anything not in this map is refused
# by name and type rather than guessed at: a wrong shape is a 400 at best and a
# silently-ignored write at worst.
CUSTOM_WRITE_SHAPES = {
    "textarea": "doc",
    "textfield": "text",
    "url": "text",
    "float": "number",
    "datepicker": "date",
    "select": "option",
    "radiobuttons": "option",
    "multiselect": "options",
    "multicheckboxes": "options",
    "labels": "labels",
}

# One custom field's rendered value, clipped. Long enough for a real cost /
# benefit note, short enough that a board-wide sweep cannot be drowned by one
# verbose field.
MAX_CUSTOM_CHARS = 500


class JiraError(Exception):
    pass


def configured() -> bool:
    return bool(settings.jira_email and settings.jira_api_token)


def _or_facade(facade_name: str):
    """Route to the n8n façade until native credentials exist — the cutover is
    a config change, never a deploy. Late-bound by NAME so the façade module
    stays patchable (tests) and swappable."""

    def deco(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            if not configured():
                return await getattr(n8n_facade, facade_name)(*args, **kwargs)
            return await fn(*args, **kwargs)

        return wrapper

    return deco


# --- validation (model-controlled strings stop here) --------------------------


def _key(value: str, field: str = "issue_key") -> str:
    if not isinstance(value, str) or not KEY_RE.match(value or ""):
        raise JiraError(f"bad {field} {value!r} — must match [A-Z][A-Z0-9_]*-<number>")
    return value


def _date(value: str, op: str, key: str) -> str:
    if not isinstance(value, str) or not re.match(r"^\d{4}-\d{2}-\d{2}$", value):
        raise JiraError(f"{op} for {key} — due_date {value!r} must be strict YYYY-MM-DD")
    y, m, d = map(int, value.split("-"))
    try:
        date(y, m, d)
    except ValueError:
        raise JiraError(f"{op} for {key} — due_date {value!r} is not a real calendar date")
    return value


# --- transport ----------------------------------------------------------------


def _auth_kwargs() -> dict:
    """Cloud (default) sends Basic email+token; Data Center sends a Bearer PAT
    via CC_JIRA_API_TOKEN (CC_JIRA_EMAIL is ignored) — auth-mode only, no
    endpoint-shape changes (work-transition compatibility design, decision 2;
    DC endpoint differences are verified on-site, not guessed here)."""
    if settings.jira_auth_mode == "bearer":
        return {"headers": {"Authorization": f"Bearer {settings.jira_api_token}"}}
    return {"auth": (settings.jira_email, settings.jira_api_token)}


MYSELF_PATH = "/rest/api/3/myself"

# A dead/expired token does NOT 401 on list-shaped reads: Jira Cloud answers an
# ANONYMOUS caller with 200 and an empty page (verified live 2026-08-26 against
# /rest/api/3/project/search — total 0, no error), so `_check` sees success and
# the agent is told "this instance has no projects" and reasons from it. Only
# identity-style endpoints refuse anonymously, so ONE of them is the sanity
# check for the whole client.
#
# Lazy and once per process: the cost lands on the first Jira call of a process,
# never on every call. A failure is deliberately NOT cached — fixing the token
# in .env must not need a cc-uvicorn restart to be believed. The bool needs no
# lock: two concurrent first-calls cost one extra /myself and nothing else (no
# loop-bound object is cached here — every call opens its own AsyncClient — so
# neo4j_reader._get_driver's loop keying has nothing to key).
_auth_verified = False


async def _verify_auth_once() -> None:
    global _auth_verified
    if _auth_verified:
        return
    resp = await _call("GET", MYSELF_PATH)
    if resp.status_code in (401, 403):
        raise JiraError(
            f"Jira token invalid/expired ({resp.status_code} from {MYSELF_PATH}) — "
            "check CC_JIRA_EMAIL/CC_JIRA_API_TOKEN. Refusing to return a result: "
            "Jira Cloud silently degrades an unauthenticated caller to ANONYMOUS "
            "on list-shaped reads (project/search answers 200 with an empty list), "
            "so this would have read as 'the instance has nothing' rather than as "
            "an auth failure."
        )
    _check(resp, "authCheck")
    _auth_verified = True


async def _call(method: str, path: str, json_body: dict | None = None) -> httpx.Response:
    if path != MYSELF_PATH:
        await _verify_auth_once()
    async with httpx.AsyncClient(
        timeout=30, **_auth_kwargs(), **http_client.client_kwargs()
    ) as client:
        return await client.request(
            method, settings.jira_base_url.rstrip("/") + path, json=json_body
        )


def _check(resp: httpx.Response, op: str, key: str | None = None) -> None:
    if 200 <= resp.status_code < 300:
        return
    ref = f" for {key}" if key else ""
    detail = resp.text[:300]
    st = resp.status_code
    if st == 404:
        raise JiraError(f"{op} — not found{ref}")
    if st in (401, 403):
        raise JiraError(f"{op} — auth failed ({st}){ref} — check CC_JIRA_EMAIL/CC_JIRA_API_TOKEN")
    if st == 429:
        raise JiraError(f"{op} — rate limited{ref} — retry later")
    raise JiraError(f"{op} — provider rejected the request ({st}){ref}: {detail}")


def _adf_to_text(node) -> str:
    """Atlassian Document Format → plain text, tolerantly (the lib-jira rule:
    unknown shapes degrade to their text content, never to '[object Object]')."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(_adf_to_text(n) for n in node)
    if not isinstance(node, dict):
        return ""
    t = node.get("type")
    if t == "text":
        return node.get("text") or ""
    if t == "hardBreak":
        return "\n"
    attrs = node.get("attrs") or {}
    if t in ("mention", "status"):
        return attrs.get("text") or ""
    if t == "emoji":
        return attrs.get("text") or attrs.get("shortName") or ""
    if t == "inlineCard":
        return attrs.get("url") or ""
    inner = _adf_to_text(node.get("content"))
    block = {"doc", "paragraph", "heading", "blockquote", "codeBlock", "listItem",
             "bulletList", "orderedList", "panel", "table", "tableRow", "rule"}
    return inner + "\n" if t in block else inner


def _adf(text: str) -> dict:
    """Plain text → the minimal ADF document REST v3 write endpoints require."""
    paragraphs = [
        {"type": "paragraph",
         "content": [{"type": "text", "text": p}] if p else []}
        for p in text.split("\n")
    ]
    return {"type": "doc", "version": 1, "content": paragraphs}


# --- field flatteners (provider shapes -> the flat rows agents reason over) ---


def _assignee(fields: dict) -> str | None:
    """The assignee's display name, or None for genuinely UNASSIGNED — the
    distinction the ownership half of a hygiene audit is made of."""
    a = fields.get("assignee")
    if not isinstance(a, dict):
        return None
    return a.get("displayName") or a.get("emailAddress") or None


def _parent(fields: dict) -> dict | None:
    p = fields.get("parent")
    if not isinstance(p, dict) or not p.get("key"):
        return None
    return {"issue_key": p["key"],
            "summary": ((p.get("fields") or {}).get("summary"))}


def _links(fields: dict) -> tuple[list[dict], int]:
    """Flatten `issuelinks` into rows an agent can act on, and return the count
    dropped by MAX_LINKS.

    `relation` is Jira's own human phrasing for the direction actually stored
    ("blocks" vs "is blocked by"), so the agent reads the dependency the right
    way round; `type` + `direction` are what a `jira.link_issues` proposal
    needs, since that capability takes from_key as the OUTWARD side.
    """
    raw = fields.get("issuelinks")
    if not isinstance(raw, list):
        return [], 0
    out = []
    for link in raw:
        if not isinstance(link, dict):
            continue
        t = link.get("type") if isinstance(link.get("type"), dict) else {}
        other, direction = link.get("outwardIssue"), "outward"
        if not isinstance(other, dict):
            other, direction = link.get("inwardIssue"), "inward"
        if not isinstance(other, dict) or not other.get("key"):
            continue  # a link whose other end we cannot name is not actionable
        of = other.get("fields") or {}
        out.append({
            "relation": t.get(direction) or t.get("name"),
            "type": t.get("name"),
            "direction": direction,
            "issue_key": other["key"],
            "summary": of.get("summary"),
            "status": (of.get("status") or {}).get("name"),
        })
    return out[:MAX_LINKS], max(0, len(out) - MAX_LINKS)


# --- custom fields (per-instance; discovered, never hard-coded) ----------------


def _shape_of(schema: dict | None) -> str | None:
    """The write shape for a field's `schema.custom`, or None if we can't write
    it safely. The suffix after the last ':' is the customfield TYPE KEY."""
    custom = (schema or {}).get("custom")
    if not isinstance(custom, str) or ":" not in custom:
        return None
    return CUSTOM_WRITE_SHAPES.get(custom.rsplit(":", 1)[1])


async def _custom_fields() -> list[dict]:
    """Every custom field this instance declares — {id, name, type, writable}.

    Cached for CUSTOM_FIELDS_TTL because every issue read consults it; the TTL
    (not a permanent cache) is what makes a newly created field visible without
    a restart.
    """
    global _custom_fields_cache
    now = time.monotonic()
    if _custom_fields_cache and now - _custom_fields_cache[0] < CUSTOM_FIELDS_TTL:
        return _custom_fields_cache[1]
    resp = await _call("GET", "/rest/api/3/field")
    _check(resp, "listFields")
    fields = [
        {"id": f["id"], "name": f.get("name"),
         "type": (f.get("schema") or {}).get("custom", "").rsplit(":", 1)[-1] or None,
         "writable": _shape_of(f.get("schema")) is not None}
        for f in resp.json()
        if isinstance(f, dict) and f.get("custom") and f.get("id")
    ]
    _custom_fields_cache = (now, fields)
    return fields


def _clip_custom(text: str | None) -> str | None:
    if not text:
        return None
    if len(text) <= MAX_CUSTOM_CHARS:
        return text
    # Say it was cut. A clipped value that reads whole is how an agent invents
    # a fact about a field it only half saw.
    return text[:MAX_CUSTOM_CHARS] + f"… (clipped at {MAX_CUSTOM_CHARS} chars)"


def _custom_value(raw):
    """A custom field's provider shape → something an agent can reason over.

    Tolerant like `_adf_to_text`: an unknown shape degrades to its readable
    content, never to '[object Object]'. Empty is None, so callers can tell
    "unset" from "set to something".
    """
    if raw is None or raw == "" or raw == []:
        return None
    if isinstance(raw, dict):
        if raw.get("type") == "doc":            # rich text (textarea) — the
            return _clip_custom(               # cost/benefit fields' shape
                re.sub(r"\n{3,}", "\n\n", _adf_to_text(raw)).strip() or None)
        for k in ("value", "displayName", "name", "text"):
            if isinstance(raw.get(k), str) and raw[k]:
                return raw[k]
        return _clip_custom(str(raw))
    if isinstance(raw, list):
        vals = [v for v in (_custom_value(v) for v in raw) if v is not None]
        return vals or None
    if isinstance(raw, (bool, int, float)):
        return raw
    return _clip_custom(str(raw))


def _custom_values(fields: dict, declared: list[dict], *, only_set: bool) -> dict:
    """Rendered custom fields keyed by their HUMAN name — the name is what the
    operator says out loud ("Accomplishment Benefit"), and the id is meaningless
    outside this instance."""
    out = {}
    for f in declared:
        if f["id"] not in fields:
            continue    # not requested, or not on this issue's screen
        value = _custom_value(fields.get(f["id"]))
        if value is None and only_set:
            continue
        out[f["name"]] = value
    return out


async def _declared_or_note(op: str) -> tuple[list[dict], str | None]:
    """Discovery is best-effort, but its failure is REPORTED, never silent —
    silently omitting custom fields is the exact bug this code exists to fix."""
    try:
        return await _custom_fields(), None
    except Exception as e:  # noqa: BLE001
        return [], f"{op} — custom fields could not be listed ({type(e).__name__}: {e})"


# --- reads --------------------------------------------------------------------


@_or_facade('get_issue')
async def get_issue(issue_key: str) -> dict:
    key = _key(issue_key)
    declared, note = await _declared_or_note("getIssue")
    asked = FIELDS + "".join("," + f["id"] for f in declared)
    resp = await _call("GET", f"/rest/api/3/issue/{key}?fields={asked}")
    _check(resp, "getIssue", key)
    raw = resp.json()
    f = raw.get("fields") or {}
    desc = f.get("description")
    if isinstance(desc, dict):
        desc = re.sub(r"\n{3,}", "\n\n", _adf_to_text(desc)).strip() or None
    elif isinstance(desc, str):
        desc = desc.strip() or None
    links, dropped = _links(f)
    issue = {
        "issue_key": raw.get("key"),
        "labels": f.get("labels") if isinstance(f.get("labels"), list) else None,
        "summary": f.get("summary"),
        "description": desc,
        "priority": (f.get("priority") or {}).get("name"),
        "status": (f.get("status") or {}).get("name"),
        "issue_type": (f.get("issuetype") or {}).get("name"),
        "due_date": f.get("duedate"),
        "created": f.get("created"),
        "updated": f.get("updated"),
        "assignee": _assignee(f),   # None == unassigned, not "unknown"
        "parent": _parent(f),
        "links": links,
        "url": settings.jira_base_url.rstrip("/") + "/browse/" + str(raw.get("key")),
    }
    if dropped:
        issue["links_omitted"] = dropped
    # Every declared custom field, INCLUDING the unset ones (null) — on a single
    # issue "the field exists and is empty" is the answer to a real question,
    # and omitting it is indistinguishable from the field not existing.
    issue["custom_fields"] = _custom_values(f, declared, only_set=False)
    if note:
        issue["custom_fields_error"] = note
    return {"ok": True, "kind": "jira", "operation": "getIssue", "issue": issue}


@_or_facade('search_issues')
async def search_issues(jql: str, limit: int | None = None) -> dict:
    if not isinstance(jql, str) or not jql.strip() or len(jql) > 2000:
        raise JiraError("searchIssues — jql must be a non-empty string under 2000 chars")
    bound = min(100, limit) if limit else 50
    all_declared, note = await _declared_or_note("searchIssues")
    # Sweep rows carry only the custom fields a PROPOSAL could act on. The
    # excluded ones are exactly the Jira/plugin-managed internals — `Rank`'s
    # lexorank string rode every single row before this line existed, which is
    # noise an agent cannot act on and might try to reason about. The full
    # picture, internals included, is one getIssue away.
    declared = [f for f in all_declared if f["writable"]]
    asked = SEARCH_FIELDS + [f["id"] for f in declared]
    issues, token = [], None
    for _ in range(10):  # page cap — never an unbounded crawl
        body = {"jql": jql, "maxResults": bound, "fields": asked}
        if token:
            body["nextPageToken"] = token
        resp = await _call("POST", "/rest/api/3/search/jql", body)
        _check(resp, "searchIssues")
        page = resp.json()
        for it in page.get("issues") or []:
            if not it.get("key"):
                continue
            f = it.get("fields") or {}
            row_links, row_dropped = _links(f)
            # Unlike getIssue, a search row carries only the custom fields that
            # are actually SET — a board-wide sweep must not pay for one null
            # per declared field per row. Absent here means empty, and
            # jira.list_fields is where "what fields exist" is answered.
            row_custom = _custom_values(f, declared, only_set=True)
            issues.append({
                "issue_key": it["key"],
                "summary": f.get("summary"),
                "status": (f.get("status") or {}).get("name"),
                "issue_type": (f.get("issuetype") or {}).get("name"),
                "labels": f.get("labels") if isinstance(f.get("labels"), list) else [],
                "due_date": f.get("duedate"),
                "updated": f.get("updated"),
                "assignee": _assignee(f),   # None == unassigned
                "parent": (_parent(f) or {}).get("issue_key"),
                # Count only, by design (see SEARCH_FIELDS): enough to spot
                # orphans and hubs, then getIssue for the actual dependencies.
                "link_count": len(row_links) + row_dropped,
                "url": settings.jira_base_url.rstrip("/") + "/browse/" + it["key"],
                **({"custom_fields": row_custom} if row_custom else {}),
            })
        token = page.get("nextPageToken")
        # 100 is Jira's per-page maxResults cap, NOT a result ceiling: page until
        # `limit` is satisfied. This used to break on the first page whenever a
        # limit was passed — and jira_search_issues always passes one — so
        # limit=500 silently returned 100 with no marker, the exact "empty tail
        # reads as no-more-rows" failure `_clip` exists to prevent.
        if page.get("isLast") or not token or (limit is not None and len(issues) >= limit):
            break
    out = issues[:limit] if limit else issues
    return {"ok": True, "kind": "jira", "operation": "searchIssues",
            "issues": out, "count": len(out),
            **({"custom_fields_error": note} if note else {}),
            # More rows the 10-page cap (or `limit`) did not return. Say so —
            # a reader that cannot tell a full result from a cut one invents facts.
            "truncated": bool(token) or len(issues) > len(out)}


@_or_facade('get_transitions')
async def get_transitions(issue_key: str) -> dict:
    key = _key(issue_key)
    resp = await _call("GET", f"/rest/api/3/issue/{key}/transitions")
    _check(resp, "getTransitions", key)
    transitions = [
        {"id": t.get("id"), "name": t.get("name"),
         "to_status": (t.get("to") or {}).get("name")}
        for t in resp.json().get("transitions") or []
        if t.get("id") and t.get("name")
    ]
    return {"ok": True, "kind": "jira", "operation": "getTransitions",
            "issue": {"issue_key": key}, "transitions": transitions,
            "count": len(transitions)}


# --- writes (Executor only, post-approval) ------------------------------------


@_or_facade('set_due_date')
async def set_due_date(issue_key: str, due_date: str) -> dict:
    key = _key(issue_key)
    d = _date(due_date, "setDueDate", key)
    resp = await _call("PUT", f"/rest/api/3/issue/{key}", {"fields": {"duedate": d}})
    _check(resp, "setDueDate", key)
    return {"ok": True, "kind": "jira", "operation": "setDueDate",
            "issue": {"issue_key": key, "due_date": d}}


@_or_facade('create_issue')
async def create_issue(
    project_key: str, summary: str, description: str | None = None,
    issue_type: str = "Task", due_date: str | None = None,
    labels: list[str] | None = None, parent: str | None = None,
    custom_fields: dict | None = None,
) -> dict:
    """Create a new issue — the capability the 2026-07-20 charter-v2 incident
    demanded (the coached charter told the agent to propose it before it
    existed). Model-controlled strings validated here, like every write.

    `parent` expresses hierarchy (a Story/Task under an Epic names the Epic;
    a Subtask REQUIRES it) — distinct from `link_issues`, which expresses
    dependency, not structure.

    `custom_fields` ({name: value}, resolved and shape-checked exactly like
    set_fields) rides the create POST — added 2026-08-18 because the executor
    passes no values between a proposal's actions, so a follow-up set_fields
    can never name the key this create produces; one action makes the complete
    issue one approval."""
    if not isinstance(project_key, str) or not PROJECT_RE.match(project_key or ""):
        raise JiraError(f"createIssue — bad project_key {project_key!r} — must match [A-Z][A-Z0-9_]*")
    if not isinstance(summary, str) or not summary.strip() or len(summary) > 255:
        raise JiraError("createIssue — summary must be a non-empty string under 255 chars")
    if issue_type not in ISSUE_TYPES:
        raise JiraError(
            f"createIssue — bad issue_type {issue_type!r} (must be one of {', '.join(ISSUE_TYPES)})"
        )
    if due_date is not None:
        due_date = _date(due_date, "createIssue", project_key)
    if labels is not None and (
        not isinstance(labels, list) or not all(isinstance(x, str) for x in labels)
    ):
        raise JiraError("createIssue — labels must be a list of strings")
    if parent is not None:
        parent = _key(parent, "parent")
    if issue_type in ("Subtask", "Sub-task") and not parent:
        raise JiraError(f"createIssue — issue_type {issue_type!r} requires a parent issue key")
    custom_payload, custom_applied = {}, {}
    if custom_fields is not None:
        custom_payload, custom_applied = await _resolve_custom_writes(
            custom_fields, "createIssue", project_key
        )

    fields: dict = {
        "project": {"key": project_key},
        "summary": summary.strip(),
        "issuetype": {"name": issue_type},
    }
    if description and description.strip():
        fields["description"] = _adf(description.strip())
    if due_date:
        fields["duedate"] = due_date
    if labels:
        fields["labels"] = labels
    if parent:
        fields["parent"] = {"key": parent}
    fields.update(custom_payload)
    resp = await _call("POST", "/rest/api/3/issue", {"fields": fields})
    _check(resp, "createIssue", project_key)
    key = resp.json().get("key")
    return {"ok": True, "kind": "jira", "operation": "createIssue", "issue": {
        "issue_key": key,
        "summary": summary.strip(),
        "issue_type": issue_type,
        "due_date": due_date,
        "labels": labels or [],
        "parent": parent,
        "url": settings.jira_base_url.rstrip("/") + "/browse/" + str(key),
        **({"custom_fields": custom_applied} if custom_applied else {}),
    }}


@_or_facade('add_comment')
async def add_comment(issue_key: str, body: str) -> dict:
    key = _key(issue_key)
    if not isinstance(body, str) or not body.strip():
        raise JiraError(f"addComment for {key} — body must be a non-empty string")
    resp = await _call("POST", f"/rest/api/3/issue/{key}/comment", {"body": _adf(body)})
    _check(resp, "addComment", key)
    return {"ok": True, "kind": "jira", "operation": "addComment",
            "issue": {"issue_key": key},
            "comment": {"id": resp.json().get("id")}}


@_or_facade('update_attributes')
async def update_attributes(
    issue_key: str, priority: str, due_date: str | None, labels: list[str]
) -> dict:
    key = _key(issue_key)
    if due_date is not None:
        due_date = _date(due_date, "updateAttributes", key)
    if not isinstance(labels, list):
        raise JiraError(f"updateAttributes for {key} — labels must be the full replacement list")
    resp = await _call("PUT", f"/rest/api/3/issue/{key}", {"fields": {
        "priority": {"name": priority}, "duedate": due_date, "labels": labels}})
    _check(resp, "updateAttributes", key)
    return {"ok": True, "kind": "jira", "operation": "updateAttributes",
            "issue": {"issue_key": key, "priority": priority,
                      "due_date": due_date, "labels": labels}}


@_or_facade('link_issues')
async def link_issues(from_key: str, to_key: str, link_type: str) -> dict:
    """Typed link; from_key is the OUTWARD side (for Blocks: from BLOCKS to)."""
    fk, tk = _key(from_key, "from_key"), _key(to_key, "to_key")
    if fk == tk:
        raise JiraError(f"linkIssues — from_key and to_key are the same issue {fk!r}")
    if link_type not in LINK_TYPES:
        raise JiraError(
            f"linkIssues — bad link_type {link_type!r} (must be one of {', '.join(LINK_TYPES)})"
        )
    resp = await _call("POST", "/rest/api/3/issueLink", {
        "type": {"name": link_type},
        "outwardIssue": {"key": fk}, "inwardIssue": {"key": tk}})
    _check(resp, "linkIssues", fk)
    return {"ok": True, "kind": "jira", "operation": "linkIssues",
            "link": {"from_key": fk, "to_key": tk, "link_type": link_type}}


@_or_facade('transition_issue')
async def transition_issue(issue_key: str, transition: str) -> dict:
    """Move an issue by transition NAME, resolved against what is actually
    available from its current status — unavailable names fail loudly with the
    available set, never a guess."""
    key = _key(issue_key)
    if not isinstance(transition, str) or not transition.strip():
        raise JiraError(f"transitionIssue for {key} — transition must be a non-empty name")
    avail = (await get_transitions(key))["transitions"]
    match = next(
        (t for t in avail if t["name"].lower() == transition.strip().lower()), None
    )
    if match is None:
        names = ", ".join(t["name"] for t in avail) or "(none)"
        raise JiraError(
            f"transitionIssue for {key} — transition {transition!r} is not available "
            f"from the issue's current status (available: {names})"
        )
    resp = await _call("POST", f"/rest/api/3/issue/{key}/transitions",
                       {"transition": {"id": match["id"]}})
    _check(resp, "transitionIssue", key)
    return {"ok": True, "kind": "jira", "operation": "transitionIssue",
            "issue": {"issue_key": key, "transition": match["name"]}}


# --- custom fields, filters, dashboards & gadgets (native only) ---------------
# lib-jira never grew these operations, so there is no `_or_facade` fallback:
# they simply refuse until native credentials exist.


def _require_native(op: str) -> None:
    if not configured():
        raise JiraError(f"{op} requires native Jira credentials (CC_JIRA_EMAIL/CC_JIRA_API_TOKEN)")


async def list_fields() -> dict:
    """Every custom field this instance declares, by name — the answer to "does
    Jira have a field called X, and can I write it?"."""
    _require_native("listFields")
    fields = await _custom_fields()
    return {"ok": True, "kind": "jira", "operation": "listFields",
            "fields": fields, "count": len(fields)}


def _coerce_custom(name: str, shape: str, value, op: str = "setFields"):
    """A field's declared write shape + the agent's value → the provider shape.

    Refuses rather than guesses: a wrong shape is a 400 at best, and at worst a
    200 that binds nothing (the gadget-prefs failure mode, 2026-08-10).
    """
    bad = f"{op} — {name!r} "
    if value is None:
        return None                      # explicit clear
    if shape == "doc":
        if not isinstance(value, str):
            raise JiraError(bad + "is a rich-text field — value must be a string")
        return _adf(value)
    if shape == "text":
        if not isinstance(value, str):
            raise JiraError(bad + "is a text field — value must be a string")
        return value
    if shape == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise JiraError(bad + "is a number field — value must be a number")
        return value
    if shape == "date":
        return _date(value, op, name)
    if shape == "option":
        if not isinstance(value, str):
            raise JiraError(bad + "is a single-select — value must be one option name")
        return {"value": value}
    if shape == "options":
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise JiraError(bad + "is a multi-select — value must be a list of option names")
        return [{"value": v} for v in value]
    if shape == "labels":
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise JiraError(bad + "is a labels field — value must be a list of strings")
        return value
    raise JiraError(bad + f"has an unsupported write shape {shape!r}")   # unreachable


async def _resolve_custom_writes(fields: dict, op: str, ref: str) -> tuple[dict, dict]:
    """{name: value} → ({customfield_id: provider_value}, {name: value} applied).

    The shared seam under set_fields and create_issue's `custom_fields`: names
    resolved against what the instance declares, unknown/unwritable refused
    with the real list, values shape-checked — never sent for Jira to silently
    ignore."""
    if not isinstance(fields, dict) or not fields:
        raise JiraError(f"{op} for {ref} — fields must be a non-empty {{name: value}} object")
    declared = await _custom_fields()
    by_name = {f["name"].strip().lower(): f for f in declared if f.get("name")}
    payload, applied = {}, {}
    for name, value in fields.items():
        f = by_name.get(name.strip().lower()) if isinstance(name, str) else None
        if f is None:
            known = ", ".join(sorted(d["name"] for d in declared if d.get("name"))) or "(none)"
            raise JiraError(
                f"{op} for {ref} — no custom field named {name!r}. This "
                f"instance declares: {known}"
            )
        if not f["writable"]:
            raise JiraError(
                f"{op} for {ref} — {f['name']!r} is a {f['type']!r} field, "
                "which this capability cannot write safely (it is managed by "
                "Jira or a plugin). Change it in the Jira UI."
            )
        shape = CUSTOM_WRITE_SHAPES[f["type"]]
        payload[f["id"]] = _coerce_custom(f["name"], shape, value, op)
        applied[f["name"]] = value
    return payload, applied


async def set_fields(issue_key: str, fields: dict) -> dict:
    """Set custom fields on an issue BY NAME.

    Names, not ids: `customfield_10075` is meaningless outside this instance,
    and resolving here is what stops a proposal naming an id that belongs to a
    different field (or to nothing). An unknown name is refused with the list of
    real ones rather than sent to Jira, which would accept the write and bind
    NOTHING — a 200 with no effect is the failure this whole path exists to
    avoid. Values are validated against each field's declared shape.
    """
    _require_native("setFields")
    key = _key(issue_key)
    payload, applied = await _resolve_custom_writes(fields, "setFields", key)
    resp = await _call("PUT", f"/rest/api/3/issue/{key}", {"fields": payload})
    _check(resp, "setFields", key)
    return {"ok": True, "kind": "jira", "operation": "setFields",
            "issue": {"issue_key": key, "fields": applied}}


async def list_filters(query: str | None = None) -> dict:
    _require_native("listFilters")
    path = "/rest/api/3/filter/search?expand=jql,description,owner"
    if query:
        from urllib.parse import quote
        path += f"&filterName={quote(query)}"
    resp = await _call("GET", path)
    _check(resp, "listFilters")
    # First page only, by design — proven envelope carries maxResults/startAt/
    # total/isLast, but a bounded read is enough for "does this already exist".
    values = resp.json().get("values") or []
    filters = [
        {"id": f.get("id"), "name": f.get("name"), "jql": f.get("jql"),
         "description": f.get("description"), "owner": (f.get("owner") or {}).get("displayName")}
        for f in values
    ]
    return {"ok": True, "kind": "jira", "operation": "listFilters",
            "filters": filters, "count": len(filters)}


async def list_dashboards(query: str | None = None) -> dict:
    _require_native("listDashboards")
    path = "/rest/api/3/dashboard/search?expand=description"
    if query:
        from urllib.parse import quote
        path += f"&dashboardName={quote(query)}"
    resp = await _call("GET", path)
    _check(resp, "listDashboards")
    values = resp.json().get("values") or []
    dashboards = [
        {"id": d.get("id"), "name": d.get("name"), "description": d.get("description"),
         "view": d.get("view")}
        for d in values
    ]
    return {"ok": True, "kind": "jira", "operation": "listDashboards",
            "dashboards": dashboards, "count": len(dashboards)}


async def list_gadgets() -> dict:
    _require_native("listGadgets")
    resp = await _call("GET", "/rest/api/3/dashboard/gadgets")
    _check(resp, "listGadgets")
    raw = resp.json().get("gadgets") or []
    gadgets = [
        {"title": g.get("title"), "uri": g.get("uri"), "module_key": g.get("moduleKey")}
        for g in raw
    ]
    return {"ok": True, "kind": "jira", "operation": "listGadgets",
            "gadgets": gadgets, "count": len(gadgets)}


async def list_projects() -> dict:
    """Every project this instance holds — key, name, type, id.

    The one read that had no tool until 2026-08-15, which is why nine proposals
    failed on invented keys (PERSONAL, SUPPORT, HOME, TSC, CS, SUPP): agents had
    a read tool for fields, filters, dashboards, gadgets and transitions — every
    "never guess this" surface — but no way to see the project list every
    `create_issue` requires, so they guessed a plausible one. `jira-expert`
    guessed too, and a consult about it could only answer "I can only confirm
    TASKS exists", inferred from issue searches.

    Unbounded on purpose (`isLast`, not a fixed page): under-reporting projects
    is not a smaller version of the same answer — a project missing from this
    list reads as "no project fits, create one", which is a one-way door.
    """
    _require_native("listProjects")
    projects, start = [], 0
    while True:
        resp = await _call(
            "GET", f"/rest/api/3/project/search?maxResults=100&startAt={start}"
        )
        _check(resp, "listProjects")
        body = resp.json()
        values = body.get("values") or []
        projects += [
            {"key": p.get("key"), "name": p.get("name"),
             "id": p.get("id"), "type": p.get("projectTypeKey")}
            for p in values
        ]
        if body.get("isLast", True) or not values:
            break
        start += len(values)
    return {"ok": True, "kind": "jira", "operation": "listProjects",
            "projects": projects, "count": len(projects)}


def _filter_or_dashboard_name(name: str, op: str) -> str:
    if not isinstance(name, str) or not name.strip() or len(name) > 255:
        raise JiraError(f"{op} — name must be a non-empty string under 255 chars")
    return name.strip()


async def create_filter(name: str, jql: str, description: str | None = None) -> dict:
    _require_native("createFilter")
    name = _filter_or_dashboard_name(name, "createFilter")
    if not isinstance(jql, str) or not jql.strip() or len(jql) > 2000:
        raise JiraError("createFilter — jql must be a non-empty string under 2000 chars")
    body = {"name": name, "jql": jql}
    if description is not None:
        body["description"] = description
    resp = await _call("POST", "/rest/api/3/filter", body)
    _check(resp, "createFilter")
    out = resp.json()
    fid = out.get("id")
    return {"ok": True, "kind": "jira", "operation": "createFilter", "filter": {
        "id": fid, "name": out.get("name", name), "jql": jql,
        "url": settings.jira_base_url.rstrip("/") + "/issues/?filter=" + str(fid),
    }}


def _gadget_ref(spec: dict) -> tuple[str, str]:
    """Validate exactly one of uri/module_key is given; return (field, value)."""
    uri, module_key = spec.get("uri"), spec.get("module_key")
    if bool(uri) == bool(module_key):
        raise JiraError(
            "createDashboard — each gadget needs EXACTLY ONE of 'uri' or "
            "'module_key' (from jira_list_gadgets)"
        )
    return ("uri", uri) if uri else ("moduleKey", module_key)


FILTER_ID_RE = re.compile(r"^filter-\d+$")
USERPREF_RE = re.compile(r'<UserPref\s+name="([^"]+)"')
FILTER_KEYS = ("filterId", "projectOrFilterId", "filterName")


def _filter_binding(spec: dict) -> tuple[str, str] | None:
    """The filter a gadget config names, as `('id'|'name', value)` — popped out
    of the config so the caller's key choice cannot survive into the write.

    A proposal is drafted before its filter exists, so a composite that
    creates a filter and then a dashboard cannot know the filter's id — the
    2026-08-08 live run shipped the literal placeholder `filter-<filter_id>`
    and an empty dashboard. Naming the filter instead makes the binding
    resolvable at execution time. Pure on purpose: a bad shape must cost no
    requests at all."""
    config = spec.get("config")
    if not isinstance(config, dict):
        return None
    given = [k for k in FILTER_KEYS if config.get(k) is not None]
    if len(given) > 1:
        raise JiraError(
            f"createDashboard — a gadget config carries {given}; give exactly one "
            "(filterName when the filter is created in this same proposal)"
        )
    if not given:
        return None
    value = config.pop(given[0])
    if given[0] == "filterName":
        return ("name", value)
    if not FILTER_ID_RE.match(str(value)):
        raise JiraError(
            f"createDashboard — filterId {value!r} is not a real filter id "
            "(must be 'filter-<number>'). If the filter is created in this "
            "same proposal or you only know its name, use 'filterName' "
            "instead and it is resolved at execution time."
        )
    return ("id", value)


async def _gadget_prefs(spec: dict) -> frozenset[str] | None:
    """The prefs a gadget actually declares, read from its own XML. None means
    there was nothing to read (a `module_key` gadget), and nothing below it
    then guesses.

    The gadget XML is the source of truth and cannot drift the way a hardcoded
    per-gadget table would — and fetching it is also the URI check: a real
    gadget's declaration always resolves, so one that does not is not a gadget.
    That matters because Jira accepts the dashboard and rejects each bogus
    gadget one at a time, leaving a dashboard with NOTHING on it (2026-08-10:
    an agent invented four query-string URIs like
    `/rest/gadgets/1.0/dashboards/10000/gadget/pie-chart?title=…`; all four
    came back 400 "The URI of this gadget is not present in the directory" and
    the empty dashboard stood)."""
    uri = spec.get("uri")
    if not uri:
        return None
    resp = await _call("GET", "/" + str(uri).lstrip("/"))
    if resp.status_code != 200:
        raise JiraError(
            f"createDashboard — gadget uri {uri!r} does not resolve to a gadget "
            f"declaration ({resp.status_code}); nothing was created. Gadget URIs "
            "come from jira_list_gadgets verbatim — they are never constructed, "
            "and they carry no query string."
        )
    return frozenset(USERPREF_RE.findall(resp.text)) or None


async def _prepare_gadget_configs(gadgets: list[dict]) -> None:
    """Make every gadget config something the gadget will actually READ — IN
    PLACE, before the dashboard exists.

    Three things went wrong silently on the 2026-08-10 dashboard whose four
    gadgets ALL rendered "This gadget hasn't been configured yet":

    * Every classic gadget declares `isConfigured` with `default_value="false"`,
      and that pref IS the unconfigured placeholder. Nothing ever set it, so a
      perfectly-bound gadget still rendered as unconfigured.
    * Only Filter Results declares `filterId`. Pie Chart, Created vs Resolved
      and Stats bind under `projectOrFilterId` — and a gadget SILENTLY IGNORES
      any pref it does not declare, so the wrong key is a 200 with no filter.
    * The same silence swallows any other misnamed pref (`num` sent to Created
      vs Resolved, whose row count is `daysprevious`).

    Resolution happens BEFORE the dashboard is created, so an unresolvable
    binding fails the whole write rather than leaving another empty dashboard
    behind."""
    bindings = [_filter_binding(g) for g in gadgets]  # pure — costs no requests
    for spec, binding in zip(gadgets, bindings):
        # Every uri is fetched, config or not: the fetch IS the uri check, and
        # a made-up uri must fail the whole write rather than add one more
        # gadget-shaped 400 to a dashboard that ends up empty.
        prefs = await _gadget_prefs(spec)
        config = spec.get("config")
        if not isinstance(config, dict):
            continue
        if binding:
            kind, value = binding
            if kind == "name":
                found = (await list_filters(value))["filters"]
                matches = [f for f in found if f.get("name") == value]
                if len(matches) != 1:
                    raise JiraError(
                        f"createDashboard — filterName {value!r} matched "
                        f"{len(matches)} filters (need exactly 1); nothing was created"
                    )
                value = f"filter-{matches[0]['id']}"
            config["projectOrFilterId" if prefs and "projectOrFilterId" in prefs
                   else "filterId"] = value
        if not prefs:
            continue
        unknown = sorted(set(config) - prefs)
        if unknown:
            raise JiraError(
                f"createDashboard — gadget {spec.get('title') or spec.get('uri')!r} "
                f"does not declare {unknown}. It accepts {sorted(prefs)}. A gadget "
                "silently ignores a pref it does not declare, so this would have "
                "rendered an unconfigured gadget with no error at all."
            )
        if "isConfigured" in prefs:
            config.setdefault("isConfigured", "true")


async def create_dashboard(
    name: str, description: str | None = None, gadgets: list[dict] | None = None
) -> dict:
    _require_native("createDashboard")
    name = _filter_or_dashboard_name(name, "createDashboard")
    if gadgets is not None:
        if not isinstance(gadgets, list) or not all(isinstance(g, dict) for g in gadgets):
            raise JiraError("createDashboard — gadgets must be a list of dicts")
        if len(gadgets) > 10:
            raise JiraError(
                f"createDashboard — {len(gadgets)} gadgets given, max 10 (no silent caps)"
            )
        for g in gadgets:
            _gadget_ref(g)  # validate every spec before any request is made
        # Resolve filter names, bind them under the pref the gadget actually
        # declares, and mark the gadget configured — all BEFORE the dashboard
        # exists, so a bad binding fails the whole write instead of producing
        # another dashboard of "hasn't been configured yet" gadgets.
        await _prepare_gadget_configs(gadgets)

    body = {"name": name, "sharePermissions": [], "editPermissions": []}
    if description is not None:
        body["description"] = description
    resp = await _call("POST", "/rest/api/3/dashboard", body)
    _check(resp, "createDashboard")
    dash = resp.json()
    dash_id = dash.get("id")

    outcomes = []
    for i, spec in enumerate(gadgets or []):
        field, value = _gadget_ref(spec)
        gadget_body = {
            field: value,
            "title": spec.get("title"),
            "color": spec.get("color", "blue"),
            "position": spec.get("position", {"row": i, "column": 0}),
        }
        try:
            gresp = await _call(
                "POST", f"/rest/api/3/dashboard/{dash_id}/gadget", gadget_body
            )
            _check(gresp, "createDashboard.gadget")
            gadget_id = gresp.json().get("id")
            if spec.get("config"):
                cresp = await _call(
                    "PUT",
                    f"/rest/api/3/dashboard/{dash_id}/items/{gadget_id}/properties/config",
                    spec["config"],
                )
                _check(cresp, "createDashboard.gadget.config")
            outcomes.append({"title": spec.get("title"), "ok": True, "gadget_id": gadget_id})
        except JiraError as e:
            # One gadget's failure must not hide earlier successes or block the
            # rest — the dashboard itself already exists and stands.
            outcomes.append({"title": spec.get("title"), "ok": False, "error": str(e)})

    return {"ok": True, "kind": "jira", "operation": "createDashboard", "dashboard": {
        "id": dash_id, "name": dash.get("name", name),
        "url": settings.jira_base_url.rstrip("/") + "/jira/dashboards/" + str(dash_id),
        "gadgets": outcomes,
    }}


# --- project creation (jira-expert only; see gateway/capabilities.py) ---------

# A NEW project's key is stricter than PROJECT_RE (which also matches any
# EXISTING key create_issue might target): Jira's own rule for freshly-created
# keys is 2-10 chars, uppercase letters/digits, starting with a letter.
NEW_PROJECT_KEY_RE = re.compile(r"^[A-Z][A-Z0-9]{1,9}$")


def _new_project_key(value: str) -> str:
    if not isinstance(value, str) or not NEW_PROJECT_KEY_RE.match(value or ""):
        raise JiraError(
            f"createProject — bad key {value!r} — must be 2-10 chars, "
            "uppercase letters/digits only, starting with a letter"
        )
    return value


async def create_project(key: str, name: str, project_type_key: str = "software") -> dict:
    """Create a new Jira project — native-only, no façade counterpart (like
    create_filter/create_dashboard). The lead is always the CREDENTIAL's own
    account, fetched fresh via /myself rather than hardcoded — this instance's
    only holder is jira-expert, and the credential is the operator himself."""
    _require_native("createProject")
    key = _new_project_key(key)
    if not isinstance(name, str) or not name.strip() or len(name) > 255:
        raise JiraError("createProject — name must be a non-empty string under 255 chars")
    me = await _call("GET", "/rest/api/3/myself")
    _check(me, "createProject — myself")
    account_id = me.json().get("accountId")
    if not account_id:
        raise JiraError("createProject — could not resolve the credential's accountId")
    body = {
        "key": key, "name": name.strip(), "projectTypeKey": project_type_key,
        "leadAccountId": account_id,
    }
    resp = await _call("POST", "/rest/api/3/project", body)
    _check(resp, "createProject")
    out = resp.json()
    project_key = out.get("key", key)
    return {"ok": True, "kind": "jira", "operation": "createProject", "project": {
        "key": project_key, "id": out.get("id"), "name": name.strip(),
        "url": settings.jira_base_url.rstrip("/") + "/projects/" + str(project_key),
    }}
