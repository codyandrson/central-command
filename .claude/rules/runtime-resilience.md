---
paths:
  - "central_command/runtime/**"
  - "central_command/api/**"
  - "central_command/heartbeat/**"
  - "central_command/events/**"
  - "central_command/db/repo.py"
---

# Runtime, API and heartbeat resilience bite marks

Rules below exist because a real failure produced them. Trust the rule even
where the story is gone. Moved verbatim from the root instructions; they load
when a matching file is read.

- **An attachment reaches the agent as content, or the SEND fails — there is
  no third state** (`api/attachments.py`). Attachments are extracted to text
  at the GATEWAY (model-agnostic); nothing sends native multimodal blocks,
  because a text-only model silently drops a `document` block and answers
  anyway. **The empty-output check is the load-bearing one:** markitdown
  converts an image-only PDF *without raising* and returns zero characters —
  check the OUTPUT, never the exception. Scanned/image-only PDFs go through
  `_ocr_scanned_pdf` (vision model, capped at `MAX_OCR_PAGES`) and the
  empty check guards the OCR result. Validate BEFORE the turn exists: a
  refusal must leave no transcript row and no lifecycle frame owed to the
  spinner. Capability is DECLARED, not guessed — `None` means "nobody said",
  never "yes". `settings.attachment_max_bytes` only refuses cleanly because uvicorn's
  `--ws-max-size` sits above its base64-inflated frame.
- **A `failed=True` session must record WHY** (`land_session(...,
  reason=…)`). Guarded by a source walk
  (`tests/test_session_failure_reason.py`), NOT a runtime raise: every call
  site is already handling an exception, and raising would mask it.
- **A failure landing that lives in ONE wrapper is not a landing.** The
  guard that resolves a dead task FAILED (or retry-parks it) lives in
  `_run_assigned_task`; the raw body is `_run_assigned_task_inner`, so a new
  caller gets the guard by default. The one deliberate `_inner` caller is
  `orchestration.retry_parked_task`, whose own handler is attempt-aware.
  **When handling lives in a wrapper, test the entry, not the wrapper.**
- **A shutdown hook in the FastAPI lifespan runs too late to release a
  connection.** Uvicorn waits on open connections BEFORE running lifespan
  shutdown, and the SSE stream / cockpit WebSocket follow the event log
  forever by construction — so the close must fire from the SIGTERM handler
  (`api/app.py` wraps uvicorn's `handle_exit`); `--timeout-graceful-shutdown`
  is the backstop that should never fire.
- **`asyncio.Event` at module scope is loop-bound and will bite in tests.**
  It binds to the first loop that awaits it and raises `RuntimeError` from
  every other one; pytest's per-test loops expose what one-process/one-loop
  production hides. Prefer a plain flag plus a queue sentinel
  (`events/log.py`), and never let "the other future won" stand in for "no
  exception happened".
- **A resume must ARM its park record BEFORE it runs, not only in the
  `except`.** A SIGKILL (or a `CancelledError`, which sails past `except
  Exception`) takes a local-variable park with it, leaving a session reading
  `AWAITING_HUMAN` over an already-decided item — on nobody's worklist,
  permanent. Hence `resume_park.arm()` at every decision site plus
  `resume_sweep`'s worklist. **The `tool_call_id` match is load-bearing:**
  `run_state` is a key-level MERGE, so a resumed-then-re-parked session still
  carries the old marker, and driving it would hand the redraft the PREVIOUS
  decision's outcome.
- **The resume sweep drives every row under its own guard** (ruled
  2026-10-29). `resume_sweep` wraps each unit of each worklist in `guarded()`:
  a row that raises is logged, listed in the result's `errors`
  (`{unit, id, error}`) and, for a session, recorded as `session.resume_failed`
  with `after: "sweep"`; the sweep goes on, so one bad row cannot strand the
  rows behind it at every restart. A `CancelledError` is not swallowed. A parked
  orchestration whose state names no `task_id` is landed FAILED with that reason
  BEFORE the claim (the claim would flip it RUNNING and a lookup would
  `KeyError`), then raised into the guard. Guarded by
  `tests/test_resume_sweep_isolation.py`. **An answered no-progress stall makes
  the plan park again:** only the stall that fired on `orch_stall_limit`
  rounds without progress, once answered, withdraws plan approval
  (`maybe_resume`), so the orchestrator submits a plan and the operator reviews
  it before the next `assign_work`; the round and fan-out backstops and a
  `continue` window never do. Guarded by `tests/test_orchestration.py`.
- **A restart lands nothing FAILED: the startup landing re-parks what each
  orphan owes, and it lands the TASK, not just the session.** Only a sweep
  CAN land what a dead process left — the park that would have recorded the
  death is written in an `except` block a dead process never reaches — and
  only at STARTUP, age 0 (`orchestration.land_orphans_at_startup`, from the
  lifespan, before the resume sweep): a mid-life sweep cannot tell a dead run
  from a long one, so `Dispatcher.start` reclaims the ledger and touches no
  session. `repo.park_orphaned_sessions` lands each RUNNING session by what it
  owes: a killed RESUME (its `pending_resume` call still the record's
  unanswered tail — the `tool_call_id` match again) goes back to
  AWAITING_RESUME due now; a killed TASK RUN re-parks its task ASSIGNED with a
  `restart` retry_state, due now, re-run FRESH whatever its age (the operator
  issued it and can cancel it), and its transcript closes STOPPED naming the
  task — never a mid-turn resume, because a kill during tool execution leaves
  calls nothing answered and the tool may already have fired; a chat lane is
  landed by its record's tail (an unsent request parks a `turn`, due now);
  anything else closes STOPPED and its own next
  occurrence absorbs the gap. A restart-closed STOPPED run that cannot be
  re-entered is refused by the Resume lever and counts as finished for
  whoever waits on it. An EXECUTING proposal parks RETRY_PENDING
  `interrupted`, its retry_state MERGED (the known-complete prefix kept), and
  no sweep moves it — an INTERIM hold until reconciliation can ask the
  service whether the in-flight action landed.
- **A live read parks under a landing tail; it never degrades to "proceed
  without it".** A DOWN read raises `CallDeferred` (below) when its run's entry
  set `TriageDeps.park_reads`, and the session resumes in place when the
  dependency is back. The discipline that keeps it total is parity, not
  memory: every read tool has an entry in `contract/dependencies.TOOL_DEPENDENCY`
  and every Executor registry row has one in `CAPABILITY_DEPENDENCY`; the
  `@parkable_read` decorator looks the tool up when the module imports, so a
  read with no map entry FAILS AT IMPORT, and a parity test walks the packs, the
  registry, the handler table and the probe table. Why: a read that answered
  "unavailable, proceed without it" made the agent reason about an absence that
  was only an outage; PRD FR-40 retired that reading (the old RUNTIME-047
  "degrade, never wedge" position). A read
  the dependency ANSWERED with a no still returns honest text — that is a NO,
  not a DOWN — and `local` and `()` entries never park. **`propose_delete_episode`
  is the one propose tool that parks** (ruled 2026-10-29): it is a
  `@parkable_read` with `TOOL_DEPENDENCY` `graph`, because the deletion preview
  it embeds is a live read, so a graph outage parks the run instead of answering
  "nothing proposed", and on resume the proposal it drafts parks through the
  run's own tail (`orchestration._land_tail`: `run._land` for a task,
  `converse._land_turn` for a chat lane; an orchestrator round rejects it as a
  call it cannot drive; any other tail is refused loudly). A NO (the episode is
  not found) keeps its `ModelRetry`. By name alone a propose tool is never a
  read: the durable name-only checks use `READ_TOOLS - PROPOSE_TOOLS`, so a
  decided deletion proposal is not misread as a read to re-execute. Guarded by
  `tests/test_graph_delete_episode.py` and `tests/test_outage_doctrine.py`.
- **A tool pauses a run only through `CallDeferred`.** pydantic-ai lets any
  other exception cancel the sibling tools and fail the run, so a DOWN read that
  raised something else would turn an outage into a FAILED session. Anything
  that needs to wait raises `CallDeferred` with `kind: 'read'` metadata, and
  `classify_deferred` lets that metadata beat the tool-name classification (a
  consult is both a consult wait and, when its specialist is DOWN, a read).
- **A parked turn re-enters on an unsent request only.** `run._run_live` parks
  a DOWN model call (`pending_resume.kind = 'turn'`) only when the persisted
  record ENDS IN THE UNSENT REQUEST, the one shape `agent.iter(None,
  message_history=…)` re-sends without copying a word. A record ending in a
  response whose tool calls were executing cannot be re-entered without
  re-running them (a tool may already have fired), so that rare case keeps its
  FAILED landing with its reason. Why: the duplicated operator turn and the
  re-spent tool batch are exactly the evidence of an outage the standard forbids.
  `park_reads` therefore means "this entry has a landing tail", and
  `orchestration._drive_tail` is the one driver for read and turn resumes.
- **A probe-gated skip spends nothing.** The retry sweep skips a unit whose
  recorded dependency (plus `llm`, for a model turn) probes down BEFORE calling
  its driver, so there is no path on which an outage can advance `attempts` or
  `not_before`. Attempts are spent only when the probe said healthy and the call
  still failed, and a unit that keeps doing that is reported once per
  failure EPISODE (`retry.failing_despite_probe`, keyed on the `episode` its
  park recorded, never a count: the sweep only sees units at or over the
  threshold) rather than failed: there is no attempt limit. A ledger freeze is
  not a failure and clears the item's streak.
  Why: with limits, a two-hour outage at a five-minute cadence exhausted every
  parked unit in 25 minutes and promoted a storm of operator items. One curve
  (`contract.retry_backoff_seconds`) and one cap setting now set the patience.
- **The waiting dismiss is one SQL-guarded transition, and it resolves the task
  FAILED.** Dismissing an approved proposal that waits on an outage
  (`RETRY_PENDING`) goes through `repo.withdraw_proposal_if_open`, a conditional
  update that returns the prior status, so it cannot race
  `claim_proposal_retry` into both withdrawing and executing. It withdraws the
  EXECUTION, not the decision: no second `proposal.decided`, a distinct
  `proposal.execution_dismissed` emitted before the task resolution it
  authorises, the drafter never resumed, `retry_state` kept. The task resolves
  FAILED with how many actions had landed, never DONE "no action needed"
  (the operator, 2026-10-27).
- **A tool result is bounded by the INPUT window, and mail tokenises at ~2
  chars/token.** `tools._clip` derives its ceiling from the smallest
  discovered input window (`context.tool_result_ceiling`), never from the
  output cap; the estimate starts at `CHARS_PER_TOKEN = 3` and switches to the density the proxy's own token count reveals (`record_density`, per model, from the serialized messages only — the proxy's count includes tool schemas, the character count does not, so the measured ratio already carries the schemas' overhead); and `prepare_window`
  clips a request that would not fit rather than sending it (2026-09-16:
  four `mail_read`s in one turn, ten dead sessions). A per-tool cap goes on
  the tool (`_MAIL_BODY_CEILING`), not on the constant.
- **A failed compaction degrades to the clip, never to the raw record.** The
  summary is a model call and can die like any other (a reasoning model
  spending its output cap thinking); `_prepare_window` guards that call on
  its own so the trimmed window and the overflow clip stay in play. Sending
  raw history "rather than a dead run" IS the dead run once the record is
  bigger than the window (2026-09-18: 607k tokens against 262k).
- **A status-code sniff must match a TOKEN.** `"429" in text` fired inside a
  model uuid and retried a 404 as an outage five times. And an operator item
  with no `tool_call_id` has nothing paused on it — answering it is an
  acknowledgement, not a resume.
- **A heartbeat action never AWAITS agent work.** A task re-run queues on
  its agent's lane lock (`routes._agent_task_lock`) and a resume is a full
  model turn; awaiting either inside the tick held every schedule hostage
  for seven hours behind one agent's backlog (2026-09-11). The retry sweep
  DISPATCHES both detached (`orchestration._spawn_detached`) and the tick
  returns; `sweep_settle()` is for tests and shutdown only. Guarded by
  `test_the_sweep_returns_while_an_agent_lane_is_busy`.
- **An integration error built from an HTTP response carries the status
  TYPED, never only in its message.** Subclass `contract.failures.ProviderStatusError`
  and pass `status_code=` at every raise site that holds a response;
  `classify_failure` reads it ahead of the text sniff. A Confluence or Jira 503
  once text-classified SEMANTIC and landed a proposal FAILED mid-outage. The
  sniff is the last resort, for errors that arrive already stringified.
- **The world moved is a NO: typed, never transient and never parked.** A
  handler that finds its target changed since the draft raises a
  `contract.failures.WorldMoved` subclass; `classify_failure` decides it by
  TYPE, ahead of the status branch and the text sniff, so a 409 or a
  `what_moved` that says "timeout" cannot turn it into an outage. A moved world
  replayed as-is overwrites what nobody reviewed, so it lands FAILED, resumes
  the drafter to read again and redraft, and the redraft parks. The chain is
  bounded by `CC_WORLD_MOVED_REDRAFT_LIMIT` (default 3); no other failure
  counts toward it. A parked or interrupted proposal re-checks the world
  before its first remaining action (`executor.WORLD_CHECKS`), so waiting never
  turns into a stale write. A `WORLD_CHECKS` row runs only when `_targets_of`
  names the action's target. A coached `charter.update` is compared against
  the version its drafter was SHOWN, stamped by `park_proposal` from
  `run_state.charter_shown`, never an agent argument; a Confluence page gone
  at the update's version check is world-moved, a Jira 404 is not. Guarded by `tests/test_world_moved.py` and
  `tests/test_world_moved_limit.py`.
