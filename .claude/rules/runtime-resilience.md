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
  never "yes". `MAX_ATTACHMENT_BYTES` only refuses cleanly because uvicorn's
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
- **The orphan sweep must land the TASK, not just the session.**
  `repo.fail_stale_running_sessions` is the only thing that CAN land it — the
  park that would have recorded the death is written in an `except` block a
  dead process never reaches. FAILED, never retry-parked: a restart is not a
  dependency outage, and silently re-running whatever was in flight spends
  tokens on work the operator never re-issued.
- **A tool result is bounded by the INPUT window, and mail tokenises at ~2
  chars/token.** `tools._clip` derives its ceiling from the smallest
  discovered input window (`context.tool_result_ceiling`), never from the
  output cap; the estimate starts at `CHARS_PER_TOKEN = 3` and switches to the density the proxy's own token count reveals (`record_density`, per model, tool schemas included); and `prepare_window`
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
