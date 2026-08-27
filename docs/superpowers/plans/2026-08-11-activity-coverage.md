# Plan — Activity coverage (2026-08-11)

_Implements [the Activity coverage spec](../specs/2026-08-11-activity-coverage-design.md).
Five slices, each independently shippable. The ordering is load-bearing: the
sidebar filter is LAST, because it is only safe once Runs exists._

**One revision to the spec's shape, decided while planning:** Dashboard and
Activity are **one** top-level view with three tabs (Overview · Runs · Events),
not two nav entries. TopBar already carries six chips and is tight at
`max-[371px]`; the two screens are the same subject (current state vs history)
and the spec already gave Activity tabs. This changes nothing about the build
order — Overview is still first.

**What the audit found already built and unconsumed** — most of this plan is
assembly, not new backend:

- `GET /dispatch` returns the whole Overview payload (`dispatcher.status()`).
- `GET /events` already documents `order=desc` + `before` as "the audit-trail
  browser paging backwards through history". Zero backend for the Events tab.
- `GET /events/kinds` serves the facet vocabulary.
- `GET /ledger?state=FAILED` returns failures *with payload*, because
  `routes.py:1477` says "the Failures strip needs `failure_ack`" — a strip that
  was never built. `POST /work/{id}/requeue` and `POST /work/{id}/ack_failure`
  are its unbuilt actions.
- `repo.list_work_items`, `ledger_counts`, `count_awaiting_human`,
  `count_in_flight`, `count_dismiss_pending`, `list_heartbeat_schedules` all
  exist.

## Slice 1 — backend: session filters + one Overview payload

**`repo.list_sessions`** takes only `limit` today. Add optional `status`,
`mode`, `agent_id`, `before` (created_at cursor). Keep the existing
`token_estimate` sub-select: its measured cost is +85ms over 231 rows, so ~18ms
over a 50-row page, and a run browser is the one place that number earns its
keep. One function, extra params — not a second near-duplicate query.

**`GET /sessions`** — pass the new params through. Return `{sessions, agents}`
where `agents` is the distinct agent list, so the facet dropdown does not need
a second fetch.

**`GET /activity/overview`** — assemble in ONE payload, for the reason
`_decisions_list` already documents: the operator must never see a dispatch
number from one instant beside a ledger number from another.

```
dispatch: dispatcher.status()          # running, may_dispatch, reason,
                                       # in_flight, awaiting_human,
                                       # tokens_spent, valves, ledger
oldest_unprocessed: min(enrolled_at) where state='UNPROCESSED'
failures: list_work_items('FAILED', include_payload=True)  # minus acked
dismiss_pending: count_dismiss_pending()
schedules: list_heartbeat_schedules()  # id, name, enabled, last_fired_at
heartbeat_running: engine state
```

Tests: filter combinations against the test DB; the overview payload shape.

## Slice 2 — Hono proxy `web/server/routes/cc-activity.ts`

Follows `cc-crons.ts` exactly (`cc()` / `gvError()` helpers, `rateLimitGeneral`).

| route | proxies |
|---|---|
| `GET /api/activity/overview` | `/activity/overview` |
| `GET /api/activity/runs` | `/sessions` + query params |
| `GET /api/activity/events` | `/events` (order=desc, before cursor) |
| `GET /api/activity/event-kinds` | `/events/kinds` |
| `POST /api/activity/work/:id/requeue` | `/work/{id}/requeue` |
| `POST /api/activity/work/:id/ack` | `/work/{id}/ack_failure` |

No shape mapping — these are our own endpoints, not a vendored Nerve contract.

## Slice 3 — the Activity view

`web/src/features/activity/ActivityView.tsx` + `useActivity.ts`, joining the
existing `AgentLog.tsx` / `EventLog.tsx` in that directory (those stay: they are
the LIVE panels, this is the durable one).

- **Overview** — tiles (dispatch health, ledger counts, decisions pressure,
  heartbeat liveness) then a **Needs attention** list: FAILED work items with
  `last_error` / `attempts`, each with Requeue and Acknowledge.
- **Runs** — facet bar (agent · status · mode · date range) over a row list;
  server-paged with **Load more**, never infinite scroll (refinding, per the
  research). Each row: label, agent, status, when, and its source —
  `task_title`, or the work-item subject via `session_id`. Click opens the
  transcript.
- **Events** — kind facet from `/event-kinds`, `order=desc` + `before` cursor,
  Load more.

Register: `ViewMode` in `commands.ts`, a TopBar chip, the `viewMode === 'activity'`
branch in `App.tsx`, and a command-palette entry.

## Slice 4 — cross-links

- `TaskDetailDrawer` → its session. **Cheaper than planned:** `sessionKey` was
  already on the kanban wire and already rendered — as a *copyable code span*.
  92 sessions were reachable only by pasting it somewhere. Making it a button
  is the whole fix, so no new wire field and no wire test is owed.
- Decision → the session that drafted it. Also already on the wire
  (`ProposalDetail extends ProposalSummary`, which carries `session_id`); the
  key is built from `agent_id`, the DRAFTER, per the CLAUDE.md rule.
- ~~Cron run → its session~~ — **substituted, deliberately.** Only the
  `task.create` heartbeat action records a `session_id` in its result payload,
  and reaching that list means threading a navigate callback through the
  Workspace panel into the cron dialog — a lot of plumbing for one action kind
  on a surface the spec already calls buried. Instead the **Events tab links
  any row whose log entry carries both a session id and an agent id** (1,099 of
  the 1,252 session-referencing events). One rule, in one place, covering
  heartbeat runs and every other kind at once. Rows missing either half render
  no link rather than a guessed key that would 404 — an absent transcript link
  is honest, a broken one reads as "the run is gone".

## Slice 5 — the sidebar filter, last

Filter the history branch of `visibleSessions` in `SessionList.tsx` to
`mode === 'conversation'`; the tooltip count follows. The currently-viewed pin
stays exactly as it is.

**Guard test** — the coverage claim is the point, so it gets a test rather than
a checklist: assert every `(mode, status)` combination present in the sessions
table is reachable through the Runs query with default facets. A new session
producer that invents a mode fails the suite in the commit that adds it.

## Not in this plan

Browsable ledger screen, full-text transcript search, saved views,
virtualization — all recorded in the spec with the condition that would justify
each.
