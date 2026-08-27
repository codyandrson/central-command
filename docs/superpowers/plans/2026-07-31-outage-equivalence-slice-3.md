# Plan — outage equivalence slice 3: Executor RETRY_PENDING + idempotent replay

_Plan written 2026-07-31 under the outage-equivalence doctrine. Depends on
slice 1's classifier (`contract/failures.py`) and composes with slice 2's
AWAITING_RESUME (a retried execution that succeeds hands off to the same
post-execution resume, which may itself park). An APPROVED proposal whose
execution hits a transient dependency failure must never go terminal FAILED —
the operator's decision stands; the world-change is what queues._

## Behaviour

**Gateway approve path (`approve_and_execute`)**: on `ExecutionFailed`,
classify the recorded error (`classify_failure_text`):

- **Semantic** → today's `_record_execution_failure` byte-for-byte.
- **Transient** → proposal status **`RETRY_PENDING`** (new status value):
  - Persist the replay cursor on the row: new nullable `retry_state jsonb` —
    `{completed_results: [...], next_action_index: len(completed),
    approver, attempts: 1, last_error, parked_at}`. The Executor's
    `ExecutionFailed.completed` list IS the cursor: actions are sequential,
    so the first incomplete action is `len(completed)`.
  - Emit `proposal.execution_parked` (ref proposal_id; payload after,
    failed_capability, error, transient: true, attempts). Folds are NOT
    released (contrast with FAILED: the covering claim is still expected to
    happen) and the agent is NOT resumed yet — its session simply stays
    AWAITING_HUMAN-paused exactly as while the decision was pending; the
    session was built to wait indefinitely and this is the same wait.
  - The approve API response carries `{"ok": true, "execution": "parked"}`
    -class truth, never a pretend success and never an error.

**Retry driver (`gateway.retry_execution(proposal_id, model=None)`)**:
- Claim in SQL: `RETRY_PENDING → EXECUTING` status flip returning the row
  (new repo helper, the claim idiom again). No row → someone else has it.
- Rebuild `actions[next_action_index:]` via `_reconstruct`, call
  `executor.execute` on the REMAINDER with the SAME approver from
  retry_state; on success: splice `completed_results + new results` into the
  outcome text, stamp provenance (executed_at = now — the record shows the
  outage; only the outcome converges), set EXECUTED, emit `proposal.executed`,
  commit folds, then the SHARED post-resume tail from slice 2 resumes the
  drafter (which may itself park AWAITING_RESUME — composition, not new code).
- Transient again → back to RETRY_PENDING, attempts += 1, update last_error.
  `attempts >= CC_EXECUTION_RETRY_LIMIT` (config, default 5) promotes to the
  semantic path: `_record_execution_failure` + an operator_item naming the
  proposal and the outage (nothing loops silently).
- Semantic on retry → `_record_execution_failure` with the FULL completed
  list (spliced), so the honest partial report names everything that landed
  across attempts.
- Wire into `resume_sweep` (iterate RETRY_PENDING proposals) — the manual
  "whenever in doubt" lever; the automatic cadence is slice 5.

**The idempotency residual, stated where it lives:** replay resumes from the
first INCOMPLETE action — completed actions never re-run. The residual is
the single in-flight action that timed out AFTER the façade may have applied
it (e.g. a `jira.add_comment` whose HTTP timed out post-write): v1 retries
it and MAY double-apply for non-idempotent capabilities. Record this
honestly in the executor docstring and the doctrine's slice notes; the
mitigation (façade-level idempotency keys — the proposal's `idempotency_key`
exists and is unused end-to-end) is deliberately deferred until a façade can
actually honour it. Do not fake it with client-side dedup guesses.

## Decisions Inbox / cockpit honesty

- `repo.list_proposals("AWAITING_HUMAN")` already excludes RETRY_PENDING —
  a parked execution must NOT reappear as decidable. Verify with a backend
  test rather than assuming.
- Wherever the cockpit renders a proposal STATUS chip (workspace/detail),
  RETRY_PENDING renders as "approved — execution queued (provider outage)".
  If that requires a new wire field, backend wire-shape test (the
  hand-declared-interface bite-mark); if it is a passthrough status string,
  a display-map entry + the existing status rendering test extended.
- No new `why_not_sendable` code expected (the drafter's session state is
  unchanged); if one turns out to be needed, the `_COMPOSER_DISABLING`
  allowlist + guard test, same change.

## Tests

1. Approve with a transiently-failing façade (monkeypatched action): status
   RETRY_PENDING, retry_state cursor correct, `proposal.execution_parked`
   emitted, folds NOT released, drafter session still paused, decision
   record (`proposal.decided`) untouched.
2. Approve with a semantic façade failure: today's FAILED path byte-for-byte
   (regression).
3. `retry_execution` completes a parked proposal: only the REMAINING actions
   run (spy on the façade — completed ones must not re-run), outcome text is
   the full splice, EXECUTED + provenance + folds committed + drafter
   resumed through the shared tail.
4. Retry hits transient again: attempts increments; at the limit it promotes
   (FAILED path + operator item).
5. Retry hits semantic: `_record_execution_failure` with the spliced
   completed list.
6. Claim race: two concurrent `retry_execution` → exactly one executes.
7. RETRY_PENDING absent from the decisions wire (backend test on the real
   payload).
8. Composition: retried execution succeeds but the resume leg raises
   transiently → session parks AWAITING_RESUME (slice 2), proposal EXECUTED.
Standing pins as always.

## Migration & deployment

DDL (hand-apply live in the same change, idempotent):
```sql
alter table proposal add column if not exists retry_state jsonb;
```
`CC_EXECUTION_RETRY_LIMIT` in config.py (default 5). Restart `cc-uvicorn`;
web rebuild only if cockpit files change.

## Acceptance

Full pytest green; web suite at baseline (rebuild if touched); STATUS slice-3
claims + JOURNAL; commit + push master.
