# Activity coverage — every run and every event has a surface (2026-08-11)

**Status when written:** approved by the operator 2026-08-11, in the session that
produced the audit below. Started from a narrow UX question — "the *show closed
sessions* toggle dumps 600 rows into the chat sidebar, what's the established
pattern?" — and the audit turned it into a coverage question: *where are those
sessions supposed to live?* the operator's framing is the requirement: **"I need to be
able to view/review these — we need to ensure we can view *all* activity in at
least one UI."**

## The audit (measured 2026-08-11 against the live `cc-postgres`)

Six cockpit screens exist: `chat`, `kanban`, `inbox` (decisions), `agents`,
`skills`, `graph` (`web/src/features/command-palette/commands.ts`). Against
601 sessions in the database:

| provenance | rows | visible today |
|---|---|---|
| `conversation` mode | 36 | chat sidebar ✅ |
| `oneshot` + linked task | 92 | **nowhere** — Kanban shows the task, but `TaskDetailDrawer` carries no session reference at all |
| `inbox-triage` oneshot, no task | 423 | **nowhere** — these drain `work_item` rows and there is no ledger screen |
| other oneshot, no task (`jira-expert` 16, `knowledge-steward` 13, `coach` 7, `ea` 7, `litellm-manager` 4, `orchestrator` 1) | 48 | **nowhere**, except the heartbeat-fired subset via the cron dialog buried in Workspace |

So the sidebar's history toggle is the **only** place 565 of 601 sessions
appear. It is load-bearing by accident, which is why hiding closed rows felt
wrong even though the list is unnavigable. **Do not filter the sidebar until
there is somewhere else to look.** That ordering is the whole point of this
record.

Beyond sessions, the backend REST API is far richer than the cockpit. Endpoints
that exist and nothing calls:

- **`GET /events` + `/events/kinds`** — the durable append-only log, 8,000+
  rows, including 477 `audit.verdict` and 344 `heartbeat.fired`. The cockpit's
  EVENTS and AGENT LOG panels are **live-only, in-memory, capped at 100 and
  wiped on reload** (`SessionContext.tsx:403`). The governance record that
  CLAUDE.md calls append-only has no UI at all.
- **`GET /dispatch`** — already returns a complete queue dashboard payload
  (`ingest/dispatcher.py:538`): `running`, `may_dispatch`, `reason`,
  `in_flight`, `awaiting_human`, `tokens_spent`, `valves{}`, `ledger{}`.
  Nothing calls it. Backpressure is invisible.
- **`/emails` + `/ledger`** — 577 `work_item` rows. Ledger state today:
  572 PROCESSED, 4 FAILED, 1 FOLDED, **0 UNPROCESSED**. The queue is empty and
  healthy, which is precisely why the absence went unnoticed; the 4 FAILED are
  the only rows wanting action and they are invisible.
- **`/audit` + `/audit/agreement`**, **`/heartbeat/schedules/{id}/runs`** —
  the latter is wired, but only through the Workspace cron dialog.

Work items *do* reach the cockpit in one shape: `EmailCard` in
`DecisionsView.tsx` renders them as **source evidence attached to a proposal**.
Never as a queue. An email that failed, or is waiting, appears nowhere.

## What the research said (live web, 2026-08-11)

- **Date bucketing** (Today / Yesterday / Previous 7 days / …) is the universal
  chat-history default — ChatGPT, Claude, LibreChat, Open WebUI, and PatternFly
  codifies it as the reference chatbot spec. It does not scale on its own: 212
  of our sessions were created on 2026-08-05 alone.
- **Archive-out-of-list-but-in-search** (Gmail, ChatGPT archive): "hidden from
  the list" and "findable" are separate concerns. Archived threads still
  surface in search. Nothing needs to stay in the sidebar for nothing to be
  lost.
- **NN/g:** search and navigation are complements, not substitutes; faceted
  filtering beats keyword-only (25–50% faster task completion).
- **Polaris:** paginate past 50 items. **Smashing / LogRocket:** infinite
  scroll is for discovery feeds and is actively harmful for task-driven
  refinding — a "Load more" button plus lazy loading tested best. Our case is
  refinding.
- **Temporal Web UI / GitHub Actions** — the closest analogue to our `oneshot`
  runs — filter executions by type, status and time range rather than listing
  them.
- **AI UX Playground's conversation-search anti-patterns:** searching titles
  while ignoring bodies; jumping to a chat without highlighting the matched
  turn.

## The decision

**The session is the unit of activity, and it is a complete unit.** Every one of
the 601 rows carries `agent_id`, `mode`, `status`, `created_at`, an optional
`task_title`, and a transcript. A faceted session list therefore covers 100% of
agent activity **by construction** — a new session producer cannot escape it.
That is a provable property, worth a guard test rather than a checklist, and it
is why this is one screen and not a screen per producer (triage runs, heartbeat
runs, coach runs — each would cover a slice and leave the next producer
uncovered again).

`work_item.session_id` exists, so emails link to their run and a run can name
its source. The trace works from either end once the run is findable.

### 1. Activity screen, two tabs

- **Runs** — all sessions, faceted by agent / status / mode / date, server-paged
  with Load more (never infinite scroll — refinding, per the research). Show
  the source alongside the label: `task_title` for the 92, work-item `subject`
  via `session_id` for the 423.
- **Events** — the durable log. `/events` + `/events/kinds` already support
  `since` and `kind` faceting; this absorbs the audit verdicts and the
  heartbeat record.

Top-level view, not a Workspace tab: an audit surface you have to know to go
digging for is not much of an audit surface.

### 2. Dashboard

One screen — tiles above, needs-attention list below. Everything from data
already queryable, no new backend:

- **Dispatch health** — running / `may_dispatch` / the valve that is holding
  (`reason`), in-flight, awaiting-human, tokens spent vs budget.
- **Ledger counts by state** + oldest UNPROCESSED age (`enrolled_at`).
- **Needs attention** — FAILED work items with `last_error` and `attempts`, and
  rows with `not_before` set (transient-release parked). This is the only part
  of the ledger that wants action, and it closes that gap without a browse
  screen.
- **Heartbeat liveness** — `last_fired_at` per schedule. CLAUDE.md is explicit
  that cadence liveness lives in current state, not history, which makes it a
  tile by design rather than an event query.
- **Decisions pressure** — AWAITING_HUMAN proposals and the DISMISS_PENDING
  backlog. It is also the valve that stops dispatch, so it belongs beside it.
- **Stalled discussions** — `useStalledDiscussions` already computes this for
  the sidebar chips; the count belongs here too.

Not on it: token/cost charts, agent leaderboards, throughput graphs. Nothing is
asking those questions yet.

### 3. Three cross-links

`TaskDetailDrawer` → its session. Decision → the session that drafted it. Cron
run → its session.

### 4. Only then, the chat sidebar filter

With Activity live, filtering the sidebar to `mode === 'conversation'` stops
being "hide 565 sessions" and becomes Gmail archive semantics — out of the
list, never out of reach. 36 rows, navigable with no new UI. Date buckets on
the history section if it ever grows.

## Coverage claim

| | surface |
|---|---|
| 36 conversations | Chat (existing) |
| 92 runs w/ task · 423 triage runs · 48 orphan runs | Activity → Runs |
| 8,000+ durable events, incl. 477 audit verdicts, 344 heartbeat fires | Activity → Events |
| 577 work items — queue health, 4 failures | Dashboard |
| 130 proposals · 37 operator items | Decisions (existing) |
| Charter versions, coaching signals | Agents (existing) |
| Capability gaps | Skills (existing) |

Every row in every table has a surface.

## Deliberately not built

- **A browsable work-item ledger screen.** The dashboard surfaces the failures
  and `work_item.session_id` makes tracing work from the run side. Build it
  when "find *this specific email* among 577" becomes a real question; today
  that is a psql query run twice a year.
- **Full-text transcript search.** Add when facets stop finding things. The
  research says search and facets are complements, but facets are the cheaper
  half and we have never had either.
- **Saved views / named filter tabs** (the Polaris pattern). Single operator,
  no evidence of repeated filter combinations yet.
- **Virtualized rendering.** A performance answer, not a navigation answer.
  Server paging removes the row count that would motivate it.

## Sequencing

Dashboard first (near-zero backend, immediate value), then Activity → Runs,
then the cross-links, then Activity → Events, then the sidebar filter **last** —
it is safe only once Runs exists.
