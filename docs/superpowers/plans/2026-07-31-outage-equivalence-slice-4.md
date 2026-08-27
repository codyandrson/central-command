# Plan — outage equivalence slice 4: dispatch + task requeue on transient failure

_Plan written 2026-07-31. Depends on slice 1's classifier. The ledger half is
mostly refinement — the dispatcher ALREADY releases-and-retries with an
attempts cap; what it lacks is the transient/semantic distinction and the
exhaustion PROMOTION. The task half is new: a task whose run dies transiently
must return to a re-runnable state instead of FAILED._

## Ledger half (dispatcher) — refine, don't rebuild

`ingest/dispatcher.process_claimed`'s except-branch today: any exception →
release (up to `dispatch_max_attempts`) → then park FAILED. Changes:

1. Classify. **Transient** failures release WITHOUT consuming an attempt
   (`repo.release_work_item` gains a `count_attempt: bool` — a provider
   outage burning three attempts in three poll cycles would park items
   FAILED for a reason that was never the item's fault). The release event
   gains `transient: true`. To avoid hot-looping while the provider is down,
   a transiently-released item also gets `not_before = now + backoff`
   (new nullable `work_item.not_before timestamptz`; the claim query skips
   rows whose `not_before` is in the future — one added predicate).
   Backoff: `min(2^releases * 60s, 30min)` — store the transient-release
   count in the existing attempts-adjacent way (a `transient_releases int
   default 0` column beside `attempts`).
2. **Semantic** failures keep today's behaviour byte-for-byte (attempts++,
   park FAILED at the cap).
3. Exhaustion promotion for the SEMANTIC path stays as-is (FAILED is
   correct there). For transient there is no exhaustion — the item waits,
   backed off, as long as the outage lasts; the OUTAGE itself is already
   loud (labelled error events, heartbeat.error). This is deliberate: an
   email queued during a week-long outage should still process afterwards —
   that is the doctrine's whole point. State it in the docstring.

## Task half — a re-runnable state

Task-FAILED-on-run-exception sites: `api/routes.py:387` (the direct
task-run wrapper), `api/orchestration.py:700` and `:848` (project-child and
question-resume wrappers; the `:848` site was partially handled by slice 2 —
its parked-answer branch — the remaining raise path is this slice's),
`orchestration._fail` (`:172`, orchestrator child failure reporting).

1. At each site: classify. Semantic → today's FAILED. Transient → the task
   returns to **`ASSIGNED`** (its pre-run status — it is still assigned to
   the same agent with the same instructions; a fresh run IS the unit-level
   replay the doctrine promises) with retry bookkeeping in a new nullable
   `task.retry_state jsonb` ({attempts, last_error, parked_at, kind:
   "run"}), and `task.run_parked` emitted (transient: true, attempts). The
   run's dead session stays FAILED with its partial transcript — sessions
   are never rewritten; the TASK is what retries, with a fresh session,
   exactly like a first run.
2. Retry driver `orchestration.retry_parked_task(task_id, model=None)`:
   claim = SQL flip of retry_state-marked ASSIGNED task to IN_PROGRESS
   (returning row; the idiom); re-run through the SAME entry the original
   used (`run_task` for plain tasks; for an orchestrator child, the parent's
   existing child-failure handling already covers re-dispatch — scope
   children OUT of v1 and state it: `_fail` keeps today's behaviour, because
   the orchestrator loop has its own failure protocol the parent reasons
   about, and silently retrying under it would race that protocol).
   So v1 sites: routes.py:387 (direct task runs) and the residual raise in
   `_resume_task_question` (whose parked-answer case slice 2 owns).
   Transient again → attempts+1 re-park with backoff timestamp
   (`retry_state.not_before`); `attempts >= CC_TASK_RETRY_LIMIT` (config,
   default 5) → promote: task FAILED + operator_item + `task.run_exhausted`.
3. `resume_sweep` iterates retry-parked tasks too (skipping not_before in
   the future). Slice 5 gives it cadence.

## Cockpit honesty

- A retry-parked task is status ASSIGNED — already renders as assigned; add
  the "queued for retry (provider outage)" hint only if the task detail
  already renders outcome-ish text (check first; if a new wire field is
  needed: backend wire-shape test, per the bite-mark).
- FAILED items view (`routes.py:1073` include_payload) is unchanged —
  transient releases never land there.

## Tests

1. Transient dispatch failure: item released, attempts NOT consumed,
   `transient_releases` incremented, `not_before` set in the future, claim
   query skips it until due (pin the clock or set not_before manually).
2. Semantic dispatch failure: attempts consumed, parks FAILED at cap —
   regression, byte-for-byte.
3. Direct task run raising transiently: task back to ASSIGNED with
   retry_state, session FAILED with partial transcript, `task.run_parked`
   emitted.
4. Same, semantic: task FAILED — regression.
5. `retry_parked_task` re-runs: fresh session, task lands DONE (or parks a
   proposal — the run is a normal run).
6. Exhaustion: attempts cap → FAILED + operator item + exhausted event.
7. Claim race on retry_parked_task: one winner.
8. `resume_sweep` picks up a due retry-parked task and skips a not-yet-due
   one.
Standing pins throughout; tests that drain pin `dispatch_approval_limit`.

## Migration & deployment (hand-apply live, idempotent)

```sql
alter table work_item add column if not exists not_before timestamptz;
alter table work_item add column if not exists transient_releases int not null default 0;
alter table task add column if not exists retry_state jsonb;
```
`CC_TASK_RETRY_LIMIT` in config.py (default 5). Restart `cc-uvicorn`.

## Acceptance

Full pytest green; web at baseline; STATUS slice-4 claims + JOURNAL;
commit + push master.
