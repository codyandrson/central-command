# Agent runtime — decisions

Governs agent construction/resume, session lifecycle, heartbeat/retry
resilience, and the runtime's context/window handling. See
`docs/decisions/README.md` for the entry format and how to add one.

### DL-021 — Two run modes: CC_DEMO_MODE and CC_EXECUTOR_MODE

- **Status:** active
- **Date:** 2026-07-19
- **Rule:** [AGENTS.md](../../AGENTS.md) — "**Two run modes** (config): `CC_DEMO_MODE`"
- **Why:** Early testing had used a scripted stand-in model to validate the
  system's plumbing, but that stand-in could not prove an agent's decisions
  were actually sound — only real inference could. The two concerns (which
  model answers, versus whether a write is actually performed) were kept as
  separate switches so neither could be mistaken for the other.
- **Enforced:** code structure only — `central_command/config.py`; exercised indirectly across most of `tests/` via `demo_mode=True` fixtures, no single dedicated test
- **Source:** AGENTS.md bite marks

### DL-022 — A fresh run must load the charter; build_agent_for does not

- **Status:** active
- **Date:** 2026-07-29
- **Rule:** [AGENTS.md](../../AGENTS.md) — "**A fresh run must load the charter; `build_agent_for` does not.**"
- **Why:** A helper function built for resuming an existing session (which
  correctly skips re-loading instructions) was reused for starting a brand-new
  one, silently running the new session under a different agent's leftover
  default instructions instead of its own charter.
- **Enforced:** test: `tests/test_agent_run_contract.py::test_every_fresh_run_path_loads_a_charter` (with `tests/test_agent_run_contract.py::test_the_resume_factory_is_still_the_only_charter_free_builder` pinning the one exemption)
- **Source:** AGENTS.md bite marks

### DL-023 — system_prompt= is load-bearing; never modernise it to instructions=

- **Status:** active
- **Date:** undated
- **Rule:** [AGENTS.md](../../AGENTS.md) — "**`system_prompt=` is load-bearing — never "modernise" it to `instructions=`.**"
- **Why:** A system prompt is PERSISTED into the message history, so a
  session paused at a deferral resumes under the charter it was proposed
  under; `instructions=` is re-applied from the live agent at every run, so a
  resume would silently execute under whatever the charter says NOW —
  rewriting the record of what an agent was told when it proposed is a
  governance change wearing an idiom cleanup's clothes.
- **Enforced:** test: `tests/test_agent_construction_guards.py::test_no_agent_is_built_with_instructions`
- **Source:** AGENTS.md bite marks

### DL-024 — Agents take deps

- **Status:** active
- **Date:** 2026-07-19
- **Rule:** [AGENTS.md](../../AGENTS.md) — "**Agents take `deps`** (`runtime/deps.py`)."
- **Why:** When a run pauses partway through a multi-step decision and later
  resumes, it needs fresh per-run state so it cannot accidentally redo or re-
  litigate a step that already took effect before the pause.
- **Enforced:** test: `tests/test_agent_run_contract.py::test_every_agent_run_passes_deps` (the three deps-free auditor/summarizer agents are named exemptions, re-checked by `tests/test_agent_run_contract.py::test_the_deps_exemptions_are_still_deps_free`)
- **Source:** AGENTS.md bite marks

### DL-025 — Sessions are never destroyed; agent:<id>:main means the current lane only

- **Status:** active
- **Date:** 2026-07-23
- **Rule:** [AGENTS.md](../../AGENTS.md) — "**Sessions are never destroyed.**"
- **Why:** A UI control implied it could permanently clear an agent's history,
  which was both untrue and not actually the workflow wanted; separately, the
  "current session" shortcut kept pointing at an agent's newest conversation
  even after that conversation was closed, making a closed lane appear to
  still be live.
- **Enforced:** code structure only — `central_command/api/nerve_gateway.py:341` (`_latest_conversation(open_only=True)`), called at lines 373/484/892/1394; no dedicated pytest guard found by name
- **Source:** AGENTS.md bite marks

### DL-026 — An attachment reaches the agent as content, or the send fails — no third state

- **Status:** active
- **Date:** undated
- **Rule:** [.claude/rules/runtime-resilience.md](../../.claude/rules/runtime-resilience.md) — "**An attachment reaches the agent as content, or the SEND fails — there is no third state**"
- **Why:** A model that cannot use an attached file may still return a normal-
  looking 200 response that silently ignored it, which is worse than an error
  — so the system now either delivers attachment content as real text the
  agent can act on, or refuses before the turn runs at all, rather than risk a
  silent, invisible drop.
- **Enforced:** discipline only — no guard test located
- **Source:** .claude/rules/runtime-resilience.md

### DL-027 — A failed=True session must record why

- **Status:** active
- **Date:** undated
- **Rule:** [.claude/rules/runtime-resilience.md](../../.claude/rules/runtime-resilience.md) — "**A `failed=True` session must record WHY**"
- **Why:** Not recorded beyond the stated invariant: guarded by a source
  walk rather than a runtime raise, because every call site is already
  handling an exception and raising would mask it.
- **Enforced:** test: `tests/test_session_failure_reason.py::test_every_failed_session_records_why`
- **Source:** .claude/rules/runtime-resilience.md

### DL-028 — A failure-landing guard that lives in one wrapper is not a landing

- **Status:** active
- **Date:** 2026-09-14
- **Rule:** [.claude/rules/runtime-resilience.md](../../.claude/rules/runtime-resilience.md) — "**A failure landing that lives in ONE wrapper is not a landing.**"
- **Why:** A failure that happened while resuming a previously paused piece of
  work escaped through a code path that assumed some other part of the system
  had already recorded the failure — leaving a task stuck with no visible
  outcome. The fix consolidated failure-landing into one shared function used
  by every entry path, not just the first-run path.
- **Enforced:** discipline only — no guard test located by name; rule states the principle, not a specific test
- **Source:** .claude/rules/runtime-resilience.md

### DL-029 — A shutdown hook in the FastAPI lifespan runs too late; release from SIGTERM instead

- **Status:** active
- **Date:** 2026-08-15
- **Rule:** [.claude/rules/runtime-resilience.md](../../.claude/rules/runtime-resilience.md) — "**A shutdown hook in the FastAPI lifespan runs too late to release a connection.**"
- **Why:** The server wound up waiting forever on its own always-open
  streaming connections before it would even attempt its shutdown logic,
  because the code that was supposed to release those connections ran in a
  hook that fires only after the wait it needed to unblock. Every deploy
  therefore hung until forcibly killed; the fix moved the release into the
  earliest hook available (the termination signal itself).
- **Enforced:** discipline only — no guard test located
- **Source:** .claude/rules/runtime-resilience.md

### DL-030 — A resume must arm its park record before it runs, not only in the except

- **Status:** active
- **Date:** undated
- **Rule:** [.claude/rules/runtime-resilience.md](../../.claude/rules/runtime-resilience.md) — "**A resume must ARM its park record BEFORE it runs, not only in the `except`.**"
- **Why:** A SIGKILL (or a `CancelledError`, which sails past `except
  Exception`) takes a local-variable park with it, leaving a session reading
  `AWAITING_HUMAN` over an already-decided item, permanently off anyone's
  worklist — hence `resume_park.arm()` at every decision site plus
  `resume_sweep`'s worklist.
- **Enforced:** code structure only — `resume_park.arm()` calls at `central_command/runtime/questions.py:410`, `central_command/gateway/gateway.py:677,778`; `tests/test_resume_parking.py` exercises resume-parking behaviour broadly, but no test is pinned to the exact "arm before run" assertion
- **Source:** .claude/rules/runtime-resilience.md

### DL-031 — The orphan sweep must land the task, not just the session

- **Status:** active
- **Date:** undated
- **Rule:** [.claude/rules/runtime-resilience.md](../../.claude/rules/runtime-resilience.md) — "**The orphan sweep must land the TASK, not just the session.**"
- **Why:** `repo.fail_stale_running_sessions` is the only thing that CAN land
  it — the park that would have recorded the death is written in an `except`
  block a dead process never reaches. FAILED, never retry-parked: a restart
  is not a dependency outage, and silently re-running whatever was in flight
  spends tokens on work the operator never re-issued.
- **Enforced:** test: `tests/test_outage_requeue.py::test_the_orphan_sweep_lands_the_task_not_just_the_session`
- **Source:** .claude/rules/runtime-resilience.md

### DL-032 — A tool result is bounded by the input window, not the output cap

- **Status:** active
- **Date:** 2026-09-16
- **Rule:** [.claude/rules/runtime-resilience.md](../../.claude/rules/runtime-resilience.md) — "**A tool result is bounded by the INPUT window, and mail tokenises at ~2 chars/token.**"
- **Why:** CHANGELOG `2026-09-16 — v2.36.0: a tool result is sized by the
  input window, and the operator has a name` records the incident: ten
  inbox-triage sessions died in one day with the proxy's 400 (four
  `mail_read`s in one turn, ten dead sessions); `tools._clip` derives its
  ceiling from the smallest discovered input window
  (`context.tool_result_ceiling`), never from the output cap.
- **Enforced:** test: `tests/test_context_overflow.py::test_tool_result_ceiling_follows_the_smallest_discovered_window`, `::test_clip_default_is_the_input_window_share_not_the_output_cap`, `::test_clip_tool_results_keeps_the_head_of_every_result_and_the_pairing`
- **Source:** CHANGELOG v2.36.0

### DL-033 — A failed compaction degrades to the clip, never to the raw record

- **Status:** active
- **Date:** 2026-09-18
- **Rule:** [.claude/rules/runtime-resilience.md](../../.claude/rules/runtime-resilience.md) — "**A failed compaction degrades to the clip, never to the raw record.**"
- **Why:** CHANGELOG-adjacent incident (v2.37.2, per MEMORY.md): a
  compaction summary is itself a model call and can die like any other (a
  reasoning model spending its output cap thinking); sending raw history
  "rather than a dead run" IS the dead run once the record is bigger than
  the window (2026-09-18: 607k tokens against a 262k window).
- **Enforced:** discipline only — no guard test located this pass
- **Source:** .claude/rules/runtime-resilience.md

### DL-034 — A status-code sniff must match a token, not a substring

- **Status:** active
- **Date:** 2026-09-18
- **Rule:** [.claude/rules/runtime-resilience.md](../../.claude/rules/runtime-resilience.md) — "**A status-code sniff must match a TOKEN.**"
- **Why:** `"429" in text` fired inside a model uuid and retried a 404 as an
  outage five times (v2.37.2, per MEMORY.md).
- **Enforced:** discipline only — no dedicated guard test confirmed by name this pass
- **Source:** .claude/rules/runtime-resilience.md

### DL-035 — A heartbeat action never awaits agent work; the retry sweep dispatches detached

- **Status:** active
- **Date:** 2026-09-11
- **Rule:** [.claude/rules/runtime-resilience.md](../../.claude/rules/runtime-resilience.md) — "**A heartbeat action never AWAITS agent work.**"
- **Why:** Awaiting a task re-run's lane lock (`routes._agent_task_lock`) or a
  resume's full model turn inside the tick held every schedule hostage for
  seven hours behind one agent's backlog (2026-09-11); the retry sweep
  dispatches both detached (`orchestration._spawn_detached`) and the tick
  returns immediately.
- **Enforced:** test: `tests/test_retry_sweep.py::test_the_sweep_returns_while_an_agent_lane_is_busy`
- **Source:** .claude/rules/runtime-resilience.md
