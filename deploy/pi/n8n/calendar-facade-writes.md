# cc-calendar-facade — adding the three write modes (EA widening slice A)

**Status: APPLIED 2026-08-06** — both workflows now serve the four modes. Edit 1
was applied by the operator in the n8n UI; Edit 2 (and a cleanup of two stale
comments Edit 1 left behind) was applied by a supervised session **directly in
the n8n database**, since there is still no `N8N_API_KEY`.

How it was applied, for the next person:

- Backup of both rows (`workflow_entity` + every `workflow_history` row) at
  `/home/codyslab/cc-backups/n8n-calendar-workflows-2026-08-06/` (0600 in a 0700
  dir). Restore = write `nodes`/`connections` back and re-point `versionId` /
  `activeVersionId`, then restart `cc-n8n`.
- **n8n 2.29 does NOT execute `workflow_entity.nodes`.** Both the webhook path
  (`dist/webhooks/live-webhooks.js`) and the sub-workflow path
  (`dist/workflow-execute-additional-data.js`) load the **published** version —
  the `workflow_history` row named by `workflow_entity.activeVersionId`. Editing
  only `workflow_entity` changes what the canvas shows and nothing that runs.
  (`lib-google-calendar` was in exactly that state: its `activeVersionId` still
  pointed at a 2026-08-01 snapshot.) So the change inserts a new
  `workflow_history` row and sets `versionId` = `activeVersionId` = that row.
- Then `k3s kubectl -n central-command rollout restart deploy/cc-n8n`.

Smoke test (§Verifying), all green against the real calendar: `list` still
returns the normalised events; `create_event` → `ok:true` with an `event_id`;
`update_event` shifting `end` by 15 min → `ok:true`, `start` unchanged (PATCH
merged); `delete_event` → `{ok:true,deleted:…}`; **`delete_event` again →
`{ok:true,deleted:…}`** via the 410 rule. `{"mode":"purge"}` and a no-op
`update_event` are both refused.

Note: a refusal reaches the client as HTTP 500 `{"message":"Error in workflow"}`,
not as the `fail()` sentence — that is pre-existing webhook behaviour on the list
path too, and `CalendarFacadeError` still fires. The sentence is in the n8n
execution log.

Written 2026-08-06 by reading the live workflow JSON out of `cc-n8n-db`
(read-only: `select nodes::text from workflow_entity where id=…`). The n8n
public API is not reachable from the control plane — there is no `N8N_API_KEY`
in `deploy/pi/.env` — and an unattended build must not mutate the operator's
n8n regardless.

Delete is included on the operator's explicit override of the design doc's
"no delete in v1" (2026-08-06). It is the one mode whose blast radius is other
people's calendars, so it carries a required `reason` on the Central Command side
(reviewed in the Decisions Inbox; it is deliberately NOT sent to Google).

## What exists today

| workflow | id | role |
|---|---|---|
| `cc-calendar-facade` | `gMh57mcPQJ2gfLa7` | webhook + `x-cc-token` check + mode gate → calls the lib. No credential on this canvas. |
| `lib-google-calendar` | `GSyOmRlOCoyc9Nrh` | holds `Google Calendar account` (`googleCalendarOAuth2Api`, id `6i3SnyDioN2JlBC5`). One GET node. |

Both are `active`. The credential is full-scope by operator decision — it always
could write; `mode !== 'list'` in two places is what stopped it.

## Client contract the control plane now sends

`central_command/integrations/calendar_facade.py`, same POST + `x-cc-token` as
`list`. Called ONLY from `gateway/executor.py`, after an operator approval
(guard: `tests/test_ea_calendar.py::test_only_the_executor_calls_the_calendar_write_helpers`).

```jsonc
// mode: create_event
{ "mode": "create_event", "title": "1:1", "start": "2026-08-07T09:00:00-06:00",
  "end": "2026-08-07T09:30:00-06:00", "description": "", "attendees": ["a@example.com"],
  "calendar_id": "primary" }
// -> { "ok": true, "event": { "event_id": "...", "html_link": "...", "summary": "...",
//                             "start": "...", "end": "..." } }

// mode: update_event  — PATCH: only the keys present change
{ "mode": "update_event", "event_id": "abc123", "calendar_id": "primary",
  "start": "2026-08-07T10:00:00-06:00", "end": "2026-08-07T10:30:00-06:00" }
// -> { "ok": true, "event": { "event_id": "abc123", ... } }   (same event shape)

// mode: delete_event
{ "mode": "delete_event", "event_id": "abc123", "calendar_id": "primary" }
// -> { "ok": true, "deleted": "abc123" }
```

The client only reads `out["event"]` / `out["deleted"]` and requires `ok: true`
(anything else raises `CalendarFacadeError` with the façade's message, which is
what the operator sees on a failed execution). `event_id` is what makes a later
update or delete addressable, so **create and update must return it**.

## Edit 1 — `cc-calendar-facade` (`Auth + unwrap` code node)

Replace the single-mode check:

```js
if (body.mode !== 'list') { throw new Error('cc-calendar-facade - only mode list is served here, writes go through the approval gate'); }
```

with the allowlist (still an allowlist — never `!== ''`):

```js
const MODES = ['list', 'create_event', 'update_event', 'delete_event'];
if (!MODES.includes(body.mode)) { throw new Error('cc-calendar-facade - unsupported mode'); }
```

Nothing else on that canvas changes: same webhook, same token check, same
`Call lib-google-calendar` node — the lib routes by mode.

**Also revise `note_setup` on this canvas.** It currently says the façade
"serves `mode: 'list'` only … Calendar WRITES are not served here and never will
be". After this edit that is false, and a canvas whose sticky contradicts its
own code is worse than either. Replacement text: *this façade serves `list`
(ungated read) plus `create_event` / `update_event` / `delete_event`, which the
Central Command Executor calls ONLY after the operator approved the proposal that
named them. Nothing agent-side can reach this webhook.*

## Edit 2 — `lib-google-calendar`

### 2a. `Validate request`

- Accept the four modes; keep `fail()` and the `s()` sanitiser exactly as they
  are (the colon-swallowing gotcha documented in that node still applies).
- Keep the existing `calendar_id` charset check for every mode — it reaches a
  URL path segment.
- Per mode:
  - `create_event`: `title` a non-empty string ≤ 300 chars; `start`/`end` through
    the **existing `ISO` regex** (RFC3339 with explicit offset or `Z`) and
    `end > start`; `description` a string (default `''`); `attendees` an array of
    strings, each matching a conservative email shape, ≤ 50 entries.
  - `update_event`: `event_id` required, `/^[A-Za-z0-9_@.-]{1,1024}$/`; at least
    ONE of `title|start|end|description|attendees` present, else fail (a no-op
    write is a bug, not a success); validate whichever are present with the same
    rules; if both `start` and `end` are present require `end > start`.
  - `delete_event`: `event_id` only. (`reason` is not sent — it lives on the
    Central Command proposal.)
- Route with a `Switch` node on `$json.mode` (4 outputs), or an `If` chain —
  either is fine; the Switch keeps the canvas readable.

### 2b. Three provider nodes — `httpRequest` 4.2, like the existing GET

Same options as `List events (GCal REST)`: `authentication:
predefinedCredentialType`, `nodeCredentialType: googleCalendarOAuth2Api`,
`options.response = { fullResponse: true, neverError: true, responseFormat:
json }`, `timeout: 15000`, `onError: continueRegularOutput`. That combination is
what makes the status classification in `Return:` reachable — do not simplify it
away.

> Use raw `httpRequest`, NOT the `googleCalendar` node, for the same two reasons
> the sticky already records: its Calendar field validates the id against an
> email regex (so the literal `primary` is unaddressable) and it surfaces no HTTP
> status.

| node | method | URL | body |
|---|---|---|---|
| `Create event (GCal REST)` | POST | `https://www.googleapis.com/calendar/v3/calendars/{encodeURIComponent(calendar_id)}/events?sendUpdates=all` | `{ summary, description, start: { dateTime }, end: { dateTime }, attendees: [{ email }, …] }` |
| `Update event (GCal REST)` | PATCH | `…/calendars/{cid}/events/{encodeURIComponent(event_id)}?sendUpdates=all` | only the changed keys, same shape (PATCH merges — this is why the client omits untouched fields) |
| `Delete event (GCal REST)` | DELETE | `…/calendars/{cid}/events/{encodeURIComponent(event_id)}?sendUpdates=all` | none |

Notes that matter:

- **PATCH, not PUT.** PUT replaces the event and would silently clear anything
  omitted. The whole update contract is partial-merge.
- `start`/`end` go as `{ "dateTime": "<the RFC3339 string as given>" }`. Do not
  add a `timeZone` key: the strings carry an explicit offset, and a second,
  possibly-disagreeing source of truth for the instant is how a meeting lands an
  hour off.
- `sendUpdates=all` is deliberate: a create/move/cancel that silently fails to
  notify the attendees produces exactly the "why didn't anyone come" outcome the
  operator would blame the assistant for. If the operator prefers silence,
  change it in ONE place (all three URLs) and say so in the sticky.
- DELETE returns **204 with an empty body**, and deleting an already-deleted
  event returns **410 Gone**. Classify 410 as success-by-other-means
  (`{ ok: true, deleted: event_id }`) — the desired state holds, and failing an
  approved proposal because the outcome was already achieved teaches the
  operator to distrust real failures.

### 2c. `Return:` nodes — one per mode, cloned from `Return: list`

Keep the existing failure classification **verbatim** (it is the tested part):
`fail()`/`s()`, no-status → "outcome unknown, see the worker log", 401/403 →
"re-connect the credential", 404, 429, generic; provider text to `console.log`
only, never into the thrown message. Change only the operation word in the
messages (`create` / `update` / `delete` instead of `list`) and the success
return:

```js
// create / update
const ev = res.body || {};
return [{ json: { ok: true, event: {
  event_id: ev.id, html_link: ev.htmlLink || '', summary: ev.summary || '',
  start: (ev.start && (ev.start.dateTime || ev.start.date)) || null,
  end:   (ev.end   && (ev.end.dateTime   || ev.end.date))   || null,
} } }];

// delete (204 empty body, or 410 already gone)
if (status === 410) { /* already absent - desired state holds */ }
return [{ json: { ok: true, deleted: $('Validate request').first().json.event_id } }];
```

For delete, 410 must be handled **before** the generic non-2xx branch.

### 2d. Revise `note_contract`

It currently reads "no POST/PATCH/DELETE exists on this canvas … never add a
write op to this workflow. A write op here would be an ungated write on a
credentialed canvas". After this edit the canvas holds three. The replacement
must state the boundary as it now is, or the next reader trusts a stale
promise:

> Writes exist here (create/update/delete) and are NOT ungated. The only caller
> is `cc-calendar-facade`, whose only caller is Central Command's Executor, which
> runs a capability only after the operator approved the proposal naming it.
> Agents hold `propose_calendar_change` and nothing else; `runtime/` cannot even
> import the tier that calls this. What must never be added here is a mode that
> some *other* client could drive, or a second webhook onto this lib.

## Verifying, without an agent

`CC_CALENDAR_FACADE_TOKEN` from `deploy/pi/.env` (do not paste it into a doc or
a commit):

```bash
curl -s -X POST http://localhost:5678/webhook/cc-calendar-facade \
  -H "x-cc-token: $CC_CALENDAR_FACADE_TOKEN" -H 'content-type: application/json' \
  -d '{"mode":"create_event","title":"Central Command façade smoke test",
       "start":"2026-08-09T09:00:00-06:00","end":"2026-08-09T09:15:00-06:00",
       "attendees":[],"calendar_id":"primary"}'
# expect {"ok":true,"event":{"event_id":"…"}} — then delete_event that id.
```

Do the round trip with **no attendees** first: it changes nothing anyone else
sees. The real live proof of slice A is a `calendar.create_event` proposal from
the EA, approved in the Decisions Inbox, landing on the operator's actual calendar.
