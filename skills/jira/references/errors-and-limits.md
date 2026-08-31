# Errors, permissions and limits

## The exact errors the client raises

All are `JiraError`. The message tells you which half of the system failed.

**Raised locally, before any HTTP request — the argument is wrong, not Jira:**

- `bad issue_key '<x>' — must match [A-Z][A-Z0-9_]*-<number>`
- `createIssue — bad project_key '<x>' — must match [A-Z][A-Z0-9_]*`
- `createIssue — summary must be a non-empty string under 255 chars`
- `createIssue — bad issue_type '<x>' (must be one of Task, Bug, Story, Epic, Subtask, Sub-task)`
- `<op> for <KEY> — due_date '<x>' must be strict YYYY-MM-DD`
- `<op> for <KEY> — due_date '<x>' is not a real calendar date`
- `linkIssues — bad link_type '<x>' (must be one of Blocks, Relates, Duplicate)`
- `linkIssues — from_key and to_key are the same issue '<KEY>'`
- `searchIssues — jql must be a non-empty string under 2000 chars`
- `transitionIssue for <KEY> — transition '<x>' is not available from the
  issue's current status (available: ...)` — **the available set is in the
  message; use it, do not guess again**

Retrying the same arguments cannot succeed. Fix the argument or declare a gap.

**Raised from the provider's status code:**

| status | message | what it means |
|---|---|---|
| 404 | `<op> — not found for <KEY>` | the issue does not exist **or you cannot see it** — Jira deliberately conflates the two so a 404 is never proof of non-existence |
| 401 / 403 | `<op> — auth failed (<code>) — check CC_JIRA_EMAIL/CC_JIRA_API_TOKEN` | 401 = credentials rejected; 403 = authenticated but **not permitted** for this issue or this operation |
| 429 | `<op> — rate limited — retry later` | back off; do not hammer |
| other | `<op> — provider rejected the request (<code>): <first 300 chars>` | read the body — Jira's `errorMessages`/`errors` usually names the exact field |

Every request has a **30-second timeout**.

## Recovering from a 400 naming a field

When the "other" status row above fires with a body naming a specific field
(a bad `issue_type`, a field not on the project's screen, a value shape the
field rejects), the field name is the lead: retry by varying **that field**
through its documented alternatives (a different issue type from
`tool-surface`'s list, the field's declared type from `jira_list_fields`, a
hierarchy level one step up or down) before abandoning the proposal.

Keep the retry and the conclusion separate. A retry that still fails is
evidence about THIS attempt, not about Jira's capabilities in general — do
not report "Jira doesn't support X" off the back of an error your own first
attempt introduced (a malformed value, an unchecked field name). Only state a
capability is missing after a clean attempt — correct arguments, checked
against `tool-surface` and `jira_list_fields` — still fails, or the
provider's own message says so explicitly.

## Permission gotchas

- **Read permission is per-project and per-issue-security-level.** JQL only ever
  returns what the calling account can see. A search returning zero rows means
  "nothing visible", not "nothing exists".
- **A 404 on `get_issue` may be a permission answer.** Report it as "not found
  or not visible", never as "the issue does not exist".
- **Field-level permission is real.** An account may read an issue and still be
  refused a write to one field — that surfaces as 403 or as a 400 naming the
  field, not as a silent no-op.
- **Transitions are permission- and condition-gated.** A transition missing from
  `get_transitions` may exist in the workflow and simply be unavailable to this
  account from this status. Do not report it as "no such transition".
- **`duedate` and `priority` can be absent from a project's field
  configuration.** A 400 naming the field on `setDueDate`/`updateAttributes`
  means the field is not on that project's screen — this is a configuration
  gap to declare, not something to work around.

## Rate limiting

Jira Cloud rate-limits per account and returns **429** with `Retry-After`. The
client surfaces it as `rate limited — retry later` and does not retry for you.

Cost control is mostly about not asking for too much:

- Prefer one `search_issues` with a precise JQL over N `get_issue` calls.
- Then use `get_issue` for the handful of issues you actually need links or
  description for. That two-step is why search rows carry `link_count` instead
  of full links.
- The search path crawls at most 10 pages; passing `limit` stops it at one.
- Nothing in this client polls. If you find yourself wanting to loop until a
  state changes, that is a gap to declare, not a loop to write.

## Consistency

`/search/jql` is **eventually consistent** — a just-executed write may not be
in the index yet. `GET /rest/api/3/issue/{key}` is not index-backed and reflects
the write immediately. So:

- Verify an executed change with `jira_get_issue`, never with a search.
- If a search disagrees with a `get_issue` you just made, believe `get_issue`.

## Reporting a failure honestly

Say which call failed, with which arguments, and what the message was. "The
Jira tools don't return X" is a legitimate and useful answer — it is exactly how
`assignee`, `parent` and `issuelinks` got added to this client in the first
place. Declare the gap; do not approximate around it.
