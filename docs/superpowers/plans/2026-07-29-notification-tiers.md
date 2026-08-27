# Plan — notification tiers + agent-initiated conversation awareness (doctrine Decisions 4 & 5)

_Plan written 2026-07-29 (delivery item 2 of the teaming doctrine). Planning
finding that shapes the whole slice: the scheduled agent-initiated conversation
ALREADY WORKS end-to-end in the runtime — heartbeat `task.create` →
`run_task` → `ask_operator(needs_discussion=True)` → `_open_discussion`
(`runtime/questions.py:95`) seeds an answerable `AWAITING_OPERATOR` discussion
lane and emits `agent.discussion_opened`. Nothing in the runtime needs
building; the cockpit just doesn't notice. This slice is therefore: one wire
field + a frontend notification system._

## The gaps this closes

- **G1** nothing notifies the operator an agent-opened lane exists (no toast,
  no unread — `SessionContext`'s unread gate `isTopLevelAgentSessionKey`
  matches only `agent:<id>:main`, never real `agent:<id>:sess_*` keys, so
  per-session unread has NEVER fired on a real row).
- **G2** the lane takes up to 30s (poll) to even appear —
  `cc.agent.discussion_opened` is not among `refreshSessions` push triggers.
- **G3** the tab badge is equally late (`useAttention` has no `cc.agent.` leg).
- **G4** an agent-opened lane is indistinguishable from an operator-opened one.

## Steps (each independently shippable, suite green after each)

1. **Backend, the ONLY new wire field:** `sessions.list` rows gain
   `openedBy: 'agent'|'operator'` (+ `blockedTaskTitle`), DERIVED from
   `operator_item.discussion_session_id` — no schema change, so no live-DB
   ALTER (promote to a real column when a second producer of agent-initiated
   contact lands; the wire name survives). Mandatory backend wire-shape tests
   modelled on `test_composer_state_carries_closed_reason_to_the_cockpit`,
   plus a test that `_composer_state` on an agent-opened lane is enabled.
2. **Tier vocabulary as a pure frontend module**
   (`web/src/features/notifications/tiers.ts`): interrupt = proposals created,
   agent asks/discussion-opened, orchestration question/parked/stalled/gap,
   all `*.failed`/`*.error`/`dispatch.stalled`; badge = stall/review/step
   kinds; **unknown kinds default to `digest`** (a future chatty event must
   never become a toast by accident — tested).
3. **Interrupt tier — toast host.** Toasts derive from a PROJECTION DIFF
   (`decisions.list` + `sessions.list`) against a `localStorage` seen-set,
   never from the raw push — `_forward_events` has no replay, so a push is
   only a refresh hint; also refetch-and-diff on reconnect. Cold start seeds
   the seen-set and toasts nothing. Errors (no projection row) toast straight
   off the push, accepted as lossy. Click targets navigate. **No sound.**
4. **Badge tier — Decisions Inbox aging:** `AgeChip` (fresh/aging/stale at
   24h/72h client constants) beside the existing `RelativeTime`; section
   labels name the oldest item. Reuse the `session-stall-chip` visual
   language; assert no round-up (the stall chip's documented bug class).
5. **Badge tier — fix per-session unread + lane identity:** new
   `isCentralCommandSessionKey` predicate (do NOT widen
   `isTopLevelAgentSessionKey` — load-bearing elsewhere); add `cc.agent.*`
   legs to `SessionContext` refresh triggers and `useAttention`; "asked you"
   chip + waiting-age on `openedBy==='agent'` lanes.
6. **Deliberately NOT done:** no synthetic lifecycle frame pushed from the
   runtime (`questions.py` must not reach into the API tier; an ephemeral
   frame is the forbidden in-memory-only publish path — the durable
   `agent.discussion_opened` row already carries everything); no new event
   kinds (tiers are a projection of the existing vocabulary); **digest tier
   DEFERRED to the EA slice** — the doctrine names the digest as the EA's
   delivery channel; a panel the operator must remember to open is a log
   browser, not a digest.

## Acceptance

- `pytest` green (489 baseline + new backend tests); `cd web && npx vitest
  run` with the documented 22-failure baseline UNCHANGED (re-baseline before
  and after — `SessionContext` is the most tangled file in the tree, keep the
  diff to predicates and trigger lists).
- `npm run build`, restart `cc-nerve`, `verify.sh` 16/16.
- STATUS.md: item 2 marked delivered (interrupt + badge), digest recorded
  under "Deferred with reasons". JOURNAL entry.
