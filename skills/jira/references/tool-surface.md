# The Jira tool surface, as Central Command exposes it

Source of truth: `central_command/integrations/jira.py`. Every response is the same
envelope: `{ok: true, kind: "jira", operation: "<name>", ...}`.

## Reads (ungated — call them freely, before every proposal)

### `jira_get_issue(issue_key)` → `GET /rest/api/3/issue/{key}`

Requests these fields:
`summary, description, priority, status, issuetype, labels, duedate, created,
updated, assignee, parent, issuelinks` — **plus every custom field the instance
declares** (see `jira_list_fields`).

Returns one flat `issue` object:

| key | meaning |
|---|---|
| `issue_key`, `summary`, `description` | description is ADF flattened to plain text (`None` if empty) |
| `priority`, `status`, `issue_type` | the `.name` of each, never the raw object |
| `labels` | list, or `None` if the field was absent |
| `due_date`, `created`, `updated` | `duedate` is `YYYY-MM-DD`; `created`/`updated` are ISO timestamps |
| `assignee` | display name, or **`None` = genuinely unassigned** |
| `parent` | `{issue_key, summary}` or `None` — this is the HIERARCHY |
| `links` | list of `{relation, type, direction, issue_key, summary, status}` — this is the DEPENDENCY graph |
| `links_omitted` | present only when more than 25 links existed; the overflow is reported, never hidden |
| `custom_fields` | **every** custom field, keyed by its human name — `null` means declared but empty ON THIS ISSUE, never "no such field". Rich text arrives flattened to plain text, clipped at 500 chars with a marker |
| `custom_fields_error` | present only when the field list could not be read; the reads still work, but `custom_fields` is then incomplete and says so |
| `url` | browse URL |

`relation` is Jira's own directional phrasing ("blocks" vs "is blocked by"), so
read the dependency the way `relation` states it. `type` + `direction` are what
a `jira.link_issues` proposal needs.

### `jira_search_issues(jql, limit=None)` → `POST /rest/api/3/search/jql`

Per-issue rows are deliberately **cheaper** than `get_issue`: `issue_key`,
`summary`, `status`, `issue_type`, `labels`, `due_date`, `updated`, `assignee`,
`parent` (**key only**), `link_count`, `url`.

- **There is no `links` list in search results — only `link_count`.** A non-zero
  count means "open this issue with `get_issue` to see the actual links". A full
  link list per row would be truncated by the token budget, and a truncated list
  reads as "no more links", which is worse than an honest count.
- A row also carries `custom_fields`, but only the **writable** ones and only
  those actually **SET** on that issue — a sweep must not pay for one `null` per
  declared field per row, nor carry Jira-managed internals like `Rank` on every
  row. Absent here means empty, never "no such field". Which fields *exist* is
  `jira_list_fields`'s question, and `get_issue` shows all of them, empties and
  internals included.
- Default page size 50, capped at 100 (`limit` above 100 is clamped down).
- Paginates with `nextPageToken` up to **10 pages**, then stops. Passing an
  explicit `limit` stops after the first page.
- `jql` must be a non-empty string **under 2000 characters**.

### `jira_get_transitions(issue_key)` → `GET /rest/api/3/issue/{key}/transitions`

Returns `[{id, name, to_status}]` — **only the transitions available from the
issue's CURRENT status**, for the calling account. This is the only honest
source for what a transition proposal may name.

### `jira_list_fields()` → `GET /rest/api/3/field`

Every **custom** field this instance declares: `{id, name, type, writable}`.
`type` is the customfield type key (`textarea`, `datepicker`, `select`, …), and
`writable` says whether a `jira.set_fields` proposal can set it — fields Jira or
a plugin owns (`Rank`, `Development`, `Team`, `Agent Sessions`, …) come back
`writable: false` and are refused before any request goes out.

Custom fields are **per-instance**: `customfield_10075` means nothing on another
Jira, which is why you address them **by name** everywhere — in this list, in
`custom_fields` on a read, and in a `jira.set_fields` proposal. You never need a
`customfield_NNNNN` id, and **asking the operator for one is a wrong answer**:
the issue reads already carry these fields by name.

The list is cached for 5 minutes, so a field the operator creates in the UI
appears within that window with nothing restarted.

### `jira_list_projects()` → `GET /rest/api/3/project`

Every Jira project — key, name, type. Read this **before** proposing any
`jira.create_issue`: the project key is the one argument you cannot infer
from an email, and a key that does not exist here fails the write. Never
invent a plausible-sounding key — the real list is short and this tool is the
only thing that knows it.

### `jira_list_filters(query="")` → `GET /rest/api/3/filter/search`

Saved filters: `{id, name, jql, description, owner}`. First page only. Call
before proposing `jira.create_filter` — a duplicate filter is a rejected
proposal. `query` narrows by filter name.

### `jira_list_dashboards(query="")` → `GET /rest/api/3/dashboard/search`

Dashboards: `{id, name, description, view}`. First page only. Call before
proposing `jira.create_dashboard`.

### `jira_list_gadgets()` → `GET /rest/api/3/dashboard/gadgets`

Every gadget a dashboard can host: `{title, uri, module_key}` — each gadget
carries **exactly one** of `uri`/`module_key` (built-ins like Filter Results
and Pie Chart carry `uri`). A `jira.create_dashboard` proposal may only name
an identifier that appears in this list. Never guess one.

## Writes (gated — you propose, the Executor performs on approval)

| capability | endpoint | notes |
|---|---|---|
| `jira.create_issue` | `POST /rest/api/3/issue` | `project_key` must match `[A-Z][A-Z0-9_]*`; `summary` non-empty and **< 255 chars**; `issue_type` is one of **Task, Bug, Story, Epic, Subtask** (`Sub-task` also accepted for company-managed projects — types are per-project, and Jira's own 400 is the final validator); optional `parent` (issue key — REQUIRED for Subtask, names the Epic for a Story/Task under one), `due_date`, `labels` |
| `jira.set_due_date` | `PUT /rest/api/3/issue/{key}` | sets `duedate` only |
| `jira.update_attributes` | `PUT /rest/api/3/issue/{key}` | sets `priority`, `duedate`, `labels` together — **`labels` is the FULL replacement list, not a merge**; `due_date=None` clears it |
| `jira.set_fields` | `PUT /rest/api/3/issue/{key}` | sets **custom** fields, given as `fields: {"<field name>": value}`. Names come from `jira_list_fields` or a read's `custom_fields` (matched case-insensitively) — **never a `customfield_NNNNN` id**, and never a name you have not seen there: an unknown name is refused here with the real list, because Jira answers an unknown field key with **200 and binds nothing**. The value shape follows the field's declared type: a string for text and rich text (wrapped into ADF for you), a number for numeric, `YYYY-MM-DD` for a date, the option name for a select, a list of names for a multi-select, `null` to clear. `writable: false` fields are refused. Priority, labels and due date are NOT here — those are `jira.update_attributes`. Native-only |
| `jira.add_comment` | `POST /rest/api/3/issue/{key}/comment` | plain text is converted to ADF for you; body must be non-empty |
| `jira.link_issues` | `POST /rest/api/3/issueLink` | `link_type` is one of **Blocks, Relates, Duplicate**; `from_key` is the **OUTWARD** side (for Blocks: from BLOCKS to); self-links are refused |
| `jira.transition_issue` | `POST /rest/api/3/issue/{key}/transitions` | takes a transition **NAME**, case-insensitive; the client resolves it against `get_transitions` first and fails with the available set if it does not match — it never guesses an id |
| `jira.create_filter` | `POST /rest/api/3/filter` | `name` non-empty < 255 chars; `jql` non-empty under 2000 chars; optional `description`. Native-only — no façade counterpart |
| `jira.create_dashboard` | `POST /rest/api/3/dashboard` + `POST .../gadget` per gadget | creates a **private** dashboard (empty share/edit permissions), then adds up to **10** gadgets. Each gadget spec: exactly one of `uri`/`module_key` (from `jira_list_gadgets`), optional `title`, `color`, `position {row, column}`, and `config`. A gadget's failure does not undo the dashboard or earlier gadgets — per-gadget outcomes come back in the result. To bind a saved filter to a gadget, set `config: {"filterId": "filter-<id>"}` — **string values, `filter-` prefix** — or `config: {"filterName": "<exact name>"}` when the filter is created in the same proposal (the name is resolved to the real id at execution time; exactly-one match required, and the whole write fails before anything is created if it cannot resolve). Give exactly ONE filter key. **You do not need to know which pref the gadget binds under** — the client reads the gadget's own XML and rewrites your binding to `filterId` or `projectOrFilterId`, whichever that gadget declares, and sets `isConfigured` for you. Every OTHER key in `config` must be a pref that gadget declares, or the whole write is refused with the accepted list (a gadget silently ignores prefs it does not declare, which is how four gadgets came back reading "hasn't been configured yet"). A placeholder filterId is refused outright. Native-only |
| `jira.create_project` | `POST /rest/api/3/project` | `key` must be 2–10 chars, uppercase letters/digits only, starting with a letter; `name` non-empty under 255 chars; optional `project_type_key` (defaults `software`). The lead is always the credential's own account (fetched fresh via `/myself`, never hardcoded). Native-only — no façade counterpart |

## Validation that happens before the provider ever sees your arguments

- **Issue keys** must match `^[A-Z][A-Z0-9_]{0,49}-[1-9][0-9]{0,9}$`. Lowercase
  keys, `TASKS-0`, and prose like "the invoice ticket" are rejected here.
- **Dates** must be strict `YYYY-MM-DD` *and* a real calendar date — `2026-02-30`
  is refused before any request goes out.
- These raise `JiraError` locally, which means the failure is yours, not Jira's.
  Fix the argument; do not retry the same shape.

## Description and comment text

Write **plain text**. `_adf()` wraps it into the minimal Atlassian Document
Format document REST v3 write endpoints require, one paragraph per newline. Do
not hand-author ADF JSON, and do not paste wiki markup expecting it to render.

Coming back the other way, `_adf_to_text()` flattens ADF tolerantly: unknown
node types degrade to their text content, mentions/status/emoji render as their
label, `inlineCard` renders as its URL. So a description you read may be a
lossy view of rich content — quote it as text, but do not claim formatting
fidelity.

## The cutover you may be running under

While `CC_JIRA_EMAIL` / `CC_JIRA_API_TOKEN` are unset, **every call marked
otherwise falls through to the n8n façade** (`_or_facade`) — the ones marked
native-only above simply refuse. The envelope is
identical by design, so you cannot tell from the response which path served it —
and you do not need to. Do not reason about "which backend answered".
