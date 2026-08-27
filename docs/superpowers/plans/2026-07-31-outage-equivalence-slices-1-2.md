# Plan — outage equivalence slices 1+2: failure taxonomy + decision-resume parking

_Plan written 2026-07-31 under the outage-equivalence doctrine record. Slice 1
is the taxonomy (no behaviour change — every error event gains an honest
`transient` label). Slice 2 makes the first surface converge: a decision
resume that fails transiently PARKS instead of closing the session, and the
resume sweep can drive it once the provider is back. Slices 3–6 follow in
their own plans._

## Slice 1 — the classifier (contract tier)

**New module `central_command/contract/failures.py`** (contract has no deps and
both runtime and gateway need this — the `claim_supported` precedent):

- `classify_failure(exc: BaseException) -> str` returning `"transient"` or
  `"semantic"`. Transient inputs, from the shapes this deployment actually
  produces:
  - `pydantic_ai.exceptions.ModelHTTPError` with `status_code` in
    {408, 409(conflict-retry per provider docs? NO — keep 409 semantic),
    429, 500, 502, 503, 504, 529} → transient; other status codes semantic.
  - `pydantic_ai.exceptions.ModelAPIError` / `FallbackExceptionGroup` —
    transient when every leaf is transient; semantic otherwise.
  - `httpx` transport errors (`ConnectError`, `ReadTimeout`, `WriteTimeout`,
    `PoolTimeout`, `RemoteProtocolError`), `asyncio.TimeoutError`,
    `ConnectionError`/`OSError` network subclasses → transient.
  - Message sniffing ONLY as a last resort for wrapped/stringified errors,
    against the shapes on our own event log: "No deployments available",
    "Try again in", "Database is not ready", "cooldown", "429", "timed out",
    "connection" — case-insensitive, documented inline as deployment-observed
    shapes, each with the event id that motivated it where one exists
    (heartbeat.error 4756, feed.error 4366/2671).
  - `classify_failure_text(text: str) -> str` for seams that only hold the
    stringified error (executor failure records) — same sniffing core.
- **UNKNOWN → semantic.** Misfiling a real error as retryable hides it —
  the worse direction (no-action-is-a-claim). The docstring states this.
- Unit tests: direct, table-driven, one case per rule above, plus the
  default-semantic rule and a nested exception-group case.

**Label the error events.** Every seam that emits an error-class event gains
`"transient": classify_failure(e) == "transient"` in its payload:
`session.resume_failed` (gateway ×3), `task.run_failed` (orchestration),
`heartbeat.error` (engine), `feed.error`, `audit.error`, `proposal.failed`
(gateway; use `classify_failure_text` on the recorded error). NO behaviour
changes in slice 1 — the label starts accumulating evidence immediately.

## Slice 2 — decision-resume parking (AWAITING_RESUME)

**The rule:** when the RESUME leg of a decided proposal (or an answered
question) fails TRANSIENTLY, the session parks as `AWAITING_RESUME` with the
pending deferred result stored, instead of closing. Semantic failures keep
today's behaviour byte-for-byte. The decision record itself is never in
doubt — parking affects only the agent's final turn.

**Storage — inside `run_state`, no DDL:** the session `status` column is
unconstrained text. Park by writing status `AWAITING_RESUME` and merging into
the existing run_state:
```
run_state["pending_resume"] = {
  "kind": "result" | "denial",        # ToolReturn vs ToolDenied
  "text": <executor outcome / failure report / rejection feedback / answer>,
  "tool_call_id": <the deferred call to answer>,
  "after": "approved" | "failed" | "rejected" | "answered",
  "proposal_id" | "item_id": <the decided object>,
  "attempts": 0, "last_error": <str>, "parked_at": <iso>,
}
```
New repo helpers: `park_session_awaiting_resume(session_id, pending)` (single
UPDATE setting status + jsonb merge), `sessions_awaiting_resume()`, and
`claim_parked_resume(session_id)` — the AWAITING_RESUME→RUNNING flip in SQL
IS the claim, copied from `claim_parked_orchestration` (two sweeps racing
produce exactly one resume).

**Park sites (each: catch → classify → transient parks, semantic falls
through to today's handling):**
1. `gateway.approve_and_execute` resume leg — pending kind `result`, text =
   `outcome.result_text`, after `approved`. On park: emit
   `session.resume_parked` (payload: proposal_id, transient error, after),
   do NOT `mark_session_done`, do NOT emit session.completed, do NOT resolve
   the task — the unit is still in flight. The approve response says so.
2. `_record_execution_failure` resume leg — kind `result`, text = the
   failure report, after `failed`.
3. `reject_with_feedback` resume leg — kind `denial`, text = feedback,
   after `rejected`.
4. `questions.resume_with_answer` — today a transient raise propagates to
   `_resume_task_question`, which fails the task. Classify there instead:
   transient → park (kind `result`, text = the operator's answer, after
   `answered`) and leave the task in its current status; semantic → today's
   task-FAILED path. Note resume_with_answer builds the DeferredToolResults
   itself — park BEFORE the agent.run raise escapes, in resume_with_answer,
   so the item/answer bookkeeping (already done by answer_item) stands.

**Driving a parked resume:** new `orchestration.resume_parked_session(
session_id, model=None)`:
- claim via `claim_parked_resume`; build `DeferredToolResults()` with
  `ToolDenied(text)` for kind `denial`, plain result text otherwise;
  `build_agent_for(agent_id)`; run; then hand the outcome to the SAME
  after-resume logic the original path used. Concretely: after `approved`/
  `failed`/`rejected`, reuse the gateway's post-resume steps —
  `_drive_past_non_proposals`, redraft parking (`_park_redraft` semantics via
  the reject evidence rules when after=rejected), `_continue_if_conversation`,
  session completion, `_resolve_task_after`. **Deduplicate, don't re-implement:
  extract the gateway's post-resume tail into one helper the live path and
  the retry path both call** — a second copy is how the two drift.
  After `answered`, reuse `questions._settle`.
- On ANOTHER transient failure: re-park, `attempts += 1`, keep `last_error`.
  On semantic failure: fall through to that path's semantic handling.
  Attempt EXHAUSTION (cap, `CC_RESUME_RETRY_LIMIT` default 5) promotes to
  today's terminal behaviour + an `operator_item` (kind `question`, body
  naming the session, proposal and error) — nothing loops forever silently.
  (The heartbeat cadence that CALLS this automatically is slice 5; in this
  slice the drivers are `resume_sweep` and the existing POST endpoint.)
- Extend `resume_sweep` to also iterate `sessions_awaiting_resume()` — it is
  already the "after a restart or whenever in doubt" lever, and this makes
  restart recovery cover parked resumes from day one.

**Cockpit honesty (minimal, no new refusal codes):** an AWAITING_RESUME
session must not render as chattable-now. Check how the composer/sessions
panel treat an unknown status; the pending proposal is already decided so
the pending-proposal guard no longer applies. If `why_not_sendable` needs a
new code for AWAITING_RESUME, remember the `_COMPOSER_DISABLING` allowlist
bite-mark: add the code to the tuple WITH a guard test, in the same change.
If existing AWAITING_* handling already covers it, add the backend test that
proves it and touch nothing.

## Tests

Slice 1: table-driven classifier tests; one emit-site test per event kind
asserting the `transient` field rides the payload (real emit path, own rows).
Slice 2:
1. Approve with a resume that raises transiently (FunctionModel raising
   `ModelHTTPError(429)` — or monkeypatched resume): proposal EXECUTED,
   session AWAITING_RESUME with pending_resume populated, NO
   session.completed, task NOT resolved, `session.resume_parked` emitted.
2. Same with a semantic raise: today's behaviour byte-for-byte
   (session.resume_failed + completed) — regression.
3. `resume_parked_session` drives a parked `approved` resume to completion:
   session DONE, task resolved, transcript carries the final turn.
4. Rejected-path park: pending kind `denial`; the driven retry can REDRAFT —
   redraft parks with the operator-evidence re-check (the extracted tail is
   what guarantees this; test proves the retry path gets it too).
5. `resume_with_answer` transient → parked, task NOT failed; driven later →
   task DONE with real output.
6. Re-park on second transient failure increments attempts; exhaustion
   promotes: terminal state + operator item created.
7. Claim race: two concurrent `resume_parked_session` calls → exactly one
   resume (the SQL flip test, mirroring the orchestration claim test).
8. `resume_sweep` picks up an AWAITING_RESUME session.
Standing pins: demo_mode where resolve_model is reachable; own-rows; needs_pg
where the DB is touched; dispatch_approval_limit where draining.

## Migration & deployment

No DDL (status is text; pending_resume lives in run_state). New env knob
`CC_RESUME_RETRY_LIMIT` (config.py default 5, documented). Deploy: restart
`cc-uvicorn`; web rebuild only if a cockpit file changes.

## Acceptance

Full pytest green (588 baseline + new), web at 1,619/22-baseline (rebuild
only if touched). STATUS: slices 1–2 marked built, the evidence-label claim
added; JOURNAL entry. Commit + push master.
