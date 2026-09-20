# Event log & data spine — decisions

Governs the append-only event log, its publish path, the heartbeat
materiality allowlist, and the Postgres spine. See
`docs/decisions/README.md` for the entry format and how to add one.

### DL-036 — The event log is append-only, so write order is read order forever

- **Status:** active
- **Date:** undated
- **Rule:** [AGENTS.md](../../AGENTS.md) — "**The event log is append-only, so write order is read order forever.**"
- **Why:** `central_command/events/log.py` (module docstring) records the
  origin: "Before M8 there were two half-logs: an `audit_event` table
  nothing wrote to, and an in-process pub/sub whose events died with the
  process." `docs/DESIGN.md` states the architectural intent (one durable
  append-only event log as the spine for proactivity, collaboration, audit
  and observability). No calendar date is attached to M8 in-repo, and the
  CHANGELOG's tracked window (from 2026-08-27) has no "M8"/"event log"
  heading, so the milestone predates the CHANGELOG's coverage.
- **Enforced:** test: `tests/test_eventlog.py::test_decision_is_logged_before_execution`
- **Source:** code comment `central_command/events/log.py:1-18`; docs/DESIGN.md:139

### DL-037 — ActionSpec.material is an explicit, non-authorising and self-recording allowlist

- **Status:** active
- **Date:** undated
- **Rule:** [AGENTS.md](../../AGENTS.md) — "A heartbeat action may declare `ActionSpec.material` only if it is"
- **Why:** Not recorded beyond the stated invariant: a heartbeat action may
  declare itself material only if it is both non-authorising AND
  self-recording, so a cadence-liveness tick that found nothing writes
  nothing while an error is always material.
- **Enforced:** code structure only — `central_command/heartbeat/actions.py:29,48`; `tests/test_heartbeat.py` and `tests/test_retry_sweep.py` exercise heartbeat actions generally but no test asserts the allowlist's two-condition parity directly
- **Source:** AGENTS.md bite marks

### DL-038 — events.emit() is the only way to publish

- **Status:** active
- **Date:** undated
- **Rule:** [AGENTS.md](../../AGENTS.md) — "**`events.emit()` is the only way to publish.**"
- **Why:** Not recorded beyond the stated invariant: it writes to Postgres
  then fans out, so the SSE stream is a projection of the table, never a
  parallel channel; a proposal authorises nothing (its DECISION does), so
  `proposal.created` landing after the proposal's save is not a violation of
  the emit-before rule.
- **Enforced:** code structure only — `central_command/events/log.py`; no source-walk test found guarding against a second write path to Postgres/SSE
- **Source:** docs/DESIGN.md:139

### DL-039 — asyncio.Event at module scope is loop-bound; use a flag plus a queue sentinel

- **Status:** active
- **Date:** undated
- **Rule:** [.claude/rules/runtime-resilience.md](../../.claude/rules/runtime-resilience.md) — "**`asyncio.Event` at module scope is loop-bound and will bite in tests.**"
- **Why:** It binds to the first loop that awaits it and raises
  `RuntimeError` from every other one; pytest's per-test loops expose what
  one-process/one-loop production hides, and "the other future won" must
  never stand in for "no exception happened."
- **Enforced:** test: `tests/test_eventlog.py::test_the_close_flag_is_not_loop_bound`
- **Source:** .claude/rules/runtime-resilience.md

### DL-040 — The spine pools its database connections

- **Status:** active
- **Date:** 2026-09-13
- **Rule:** (recorded here) Postgres connections used by the spine are drawn
  from a pool rather than opened per call.
- **Why:** CHANGELOG `2026-09-13 — v2.31.0: the spine pools its database
  connections`: `pytest -q` took 16.5 minutes on the Pi, four-fifths of it
  connection overhead; pooling brought the sequential suite to ~4 minutes.
- **Enforced:** code structure only — the pooling architecture change itself; the regression it fixed is monitored by suite runtime, not a dedicated assertion
- **Source:** CHANGELOG v2.31.0
