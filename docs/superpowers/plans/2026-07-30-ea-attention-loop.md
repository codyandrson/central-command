# Plan — the executive assistant, slice 1: the attention loop (delivery item 4)

_Plan written 2026-07-30 under the teaming doctrine (Decisions 4 & 5). Slice 1
is the ATTENTION LOOP only: daily digest, scheduled check-ins, morning report,
and the enforced contact budget. No calendar/Gmail/Google integration (its own
later slice), no task-propose, and — the plan's biggest finding — NO frontend
change: the agent-opened conversation lane that item 2 made the cockpit notice
IS the delivery (toast + "asked you" chip), and a digest panel would be the
log browser STATUS already rejected._

## Decisions

1. **The EA is hired, not seeded** — `ea` roster agent, `taskable=True`
   (run_task refuses non-taskable and the heartbeat drives it through
   create_and_run_task), `conversational=True`, `consultable=False` with the
   recorded deviation (it distils the record FOR the operator; a consult
   returning "what everyone did" would flood the asker). The steward's
   `ensure_registered` generalizes into a shared `runtime/hiring.py:
   ensure_hired` (the race-loss handling is too subtle to hand-copy);
   steward re-pointed at it in the same change.
2. **Packs:** `record-read`, `graph-read`, `graph-propose`, `ask-operator`,
   `consult`, plus a NEW **`activity-read`** pack carrying one tool —
   `read_team_activity(since_hours)` over a FIXED event-kind list (the
   "LLM narrates, never improvises the queries" rule made mechanical). A new
   pack rather than growing `record-read` because pack guidance travels with
   its tools into every grantee's charter, and record-read's guidance teaches
   citation-over-a-record, not activity reads. No `task-propose`.
3. **One new heartbeat action, `ea.contact`**, params `{"kind": "digest" |
   "check_in" | "morning_report"}` with mechanical enum enforcement
   (`ActionSpec.choices`). `task.create` stays the operator's verbatim path —
   it has no templating seam, and `engine.fire` stamps the row BEFORE the
   action runs, so "since last run" must anchor elsewhere: `ea.contact`
   windows from the newest **`ea.contact_delivered`** event of its kind.
   LOUD (`material=None`) — it authorises an agent run. Three schedules
   seeded DISABLED: `ea-digest` (17:00), `ea-checkin` (11:00+15:00),
   `ea-morning-report` (07:30) — hand-applied to the live DB in the same
   change.
4. **Deterministic data, narrated:** `reports/ea_report.py:snapshot(since)`
   composes existing repo reads (ledger/task/proposal/operator-item counts,
   windowed events, open discussions — never `dispatcher.status()`, which
   mixes process-local state). The brief embeds the snapshot as a fenced
   JSON block (zero tool calls to produce the numbers); `read_team_activity`
   exists for FOLLOW-UPS in the lane. Both, with different jobs.
5. **The attention budget is mechanical, at the lane-opening seam.**
   `CC_EA_CONTACT_BUDGET_PER_DAY` (default 3, 0=unlimited) →
   `runtime/attention.py` (`budget_for` returns 0 for every other agent) →
   enforced INSIDE `questions._open_discussion`: over budget, no lane — the
   ask degrades to a plain queued question and `agent.contact_deferred` is
   emitted. A second cheap check inside `ea.contact` defers BEFORE spending
   a token. **Deferral, not folding**: the operator_item↔discussion link is
   1:1 in three places, and the delivered-anchor makes deferral lossless by
   construction (the next digest's window covers the gap). Folding into an
   open lane is the named next slice if deferral annoys.
6. **Delivery = the lane, for all three kinds.** The morning report is a
   digest with a different template (budget cost 1 — which is why the
   default is 3). A task outcome would land on the Task Board with no
   notification tier.
7. **Conclude-before-propose is charter doctrine, tested.** The check-in
   lane ends with `conclude_discussion(summary)`; the RESUMED task run parks
   the `graph.add_episode` work-log proposal. The reverse order silently
   drops the conclusion: `gateway._continue_if_conversation` rests an
   approved conversational session at AWAITING_OPERATOR and a deferred
   conclude in that turn goes nowhere (latent seam, named in STATUS).
8. **Charter carries the attention doctrine** (digest-first, the numeric
   budget and its enforcement, breakpoints not polling, data-block-is-
   ground-truth, conclude-before-propose, record-read citation discipline);
   capability list stays generated.

## Known follow-ups recorded, not built

- `answer_item` from the Inbox bypasses the lane (pre-existing asymmetry).
- Fold-into-open-lane (slice 2 candidate).
- **Enabling `discussion-sweep` should accompany turning the EA on** — it is
  the net under a hung lane and has never fired.

## Acceptance

- Full pytest green (533 baseline + hiring/attention/ea-report/heartbeat-
  action/end-to-end tests; the e2e walks fire → lane → operator turn →
  conclude → resume → gated work-log proposal, on demo models). Web baseline
  unchanged (no frontend change; wire-shape covered by existing
  `openedBy` tests — stated in the test file docstring).
- Live Pi: schedule seeds hand-applied (still disabled), `ea` hired,
  `verify.sh` green. The EA contacts nobody until the operator enables schedules.
- STATUS: roster 9 active, schedules 5 → 8, digest-tier deferred entry
  resolved to "delivered via the EA lane"; JOURNAL entry.
