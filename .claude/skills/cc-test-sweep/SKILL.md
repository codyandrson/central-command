---
name: cc-test-sweep
description: Run an exhaustive multi-agent test sweep of the live Central Command deployment — API surface, UI walk, end-to-end workflows, restart resilience — with disposable testrun-* artifacts, a mandatory fresh-context verification pass, and a cleanup phase that proves real state was untouched. Use when the operator asks to "test everything", run a regression sweep after big changes, or re-run the 2026-08-20-style audit.
---

# Central Command exhaustive test sweep

Distilled from the 2026-08-20 run. (Its raw report lived in
`drafts/test-run-2026-08-20/` and was deliberately discarded 2026-08-22 once
its findings were fixed and journaled — this skill IS the distillation.
Future run reports are working artifacts with the same lifecycle: discard
after the findings land, with the operator's sign-off.)
That run found 10 confirmed defects, retracted 2 false ones in verification,
and proved the spine's guarantees. Follow this structure; the per-lane
sub-agent briefs are in `references/lanes.md` — use them as templates, they
encode the safety rules and known API shapes.

## Ground rules (non-negotiable)

- **Report-only by default.** Findings go in the report; fixes are a separate,
  operator-approved step. Confirm scope with the operator before starting:
  what's disposable (graph/backlog/sessions?), whether external writes
  (Jira) are allowed, whether restarts are allowed.
- **NEVER approve a proposal that sends email.** Reject/dismiss those as part
  of verdict testing instead.
- **At most ONE external-write approval per run** (Jira create), and only
  with summary/description `CC TESTRUN <date> - delete me` +
  label `cc-test`. Record the issue key in CLEANUP.md immediately.
  Delete it in cleanup via `jira._call("DELETE", "/rest/api/3/issue/<KEY>")`.
- **Mutate only artifacts you create, prefixed `testrun-`.** Real roster
  agents may be TASKED (tiny, "TESTRUN"-labeled tasks) but never
  charter-saved, coached, granted/revoked, retired, or model-changed.
  Pre-existing test-ish agents (smoke-test-agent, test-intern-1,
  sandbox-tester or successors) are also off-limits — only clean up what
  THIS run created.
- **Only decide proposals/operator-items your run created** — capture each
  id at creation. Never probe (even 422 probes) against real pending items.
- **Any config you change: GET/save the original FIRST, restore after,
  restore-on-failure too.** Applies to MCP tool gates, heartbeat engine
  state, cockpit configs, and the auth hash (below).
- **Don't** start dispatch/feed continuous loops, provision a sandbox, or
  touch `/api/gateway/restart` outside the resilience phase.
- **Models:** every sub-agent gets an explicit model — haiku for mechanical
  sweeps (GET sweep, inventory, snapshots), sonnet for judgment lanes.
  Never let a lane inherit the orchestrator model.
- **Playwright has ONE owner at a time** — serialize all browser agents.

## Environment facts (verified 2026-08-20 — re-verify if stale)

- FastAPI :8080, routes under `/api` EXCEPT health at `/health`.
  Cockpit (Hono) :3080; its health also `/health`, plus `/api/version` and
  `/api/auth/status` are the only public routes.
- Postgres DSN `postgresql://central_command:central_command@localhost:5442/central_command`.
- Restarts: `sudo systemctl restart cc-uvicorn` (backend),
  `(cd web && npm run build) && sudo systemctl restart cc-nerve` (cockpit).
- Direct-task body field is `instruction` (singular). Hire: omit `packs`
  or pass a non-empty list.
- **Cockpit auth cannot be minted**: `NERVE_SESSION_SECRET` is ephemeral
  (in-memory). To test authed surfaces, ASK THE OPERATOR, then either get
  the password or (with approval) install a temp `NERVE_PASSWORD_HASH`:
  back up `web/.env` into the run dir first, generate a scrypt hash
  (`crypto.scrypt(pw, salt, 64)` → `salthex:keyhex`), restart cc-nerve.
  **Cleanup MUST restore the original `.env`, restart cc-nerve, and confirm
  the temp password now 401s.**

## Phases

Create `drafts/test-run-<date>/` with `REPORT.md` (findings, ranked),
`CLEANUP.md` (started at phase 0, updated the moment any artifact is
created), `findings/`, `baseline/`, `verify/`.

0. **Baseline** — full offline `pytest -q`; snapshot roster/grants/charters/
   skills/schedules/dispatch (API JSON + DB CSVs) into `baseline/`; build a
   route inventory from SOURCE (see Lessons). Haiku agents.
1. **API surface** — 4 parallel lanes: read-only GET sweep (haiku);
   negative/wrong-state (sonnet); graph CRUD with `TESTRUN-` entities,
   self-cleaning (sonnet); cockpit direct routes (sonnet, needs auth).
2. **UI walk** — one Playwright sonnet agent, render/interaction pass over
   every view at 800px and 1600px, console + network errors, no writes.
3. **E2E workflows** — parallel sonnet lanes: ledger→triage→all-3-verdicts→
   folds→valves; agent hire→grants→charter→coach→retire; skills import;
   chat+attachments; heartbeat schedules; MCP governance + sync_source
   (source-verify, don't provision); orchestrator+auditor.
4. **Resilience** — restart cc-uvicorn with an SSE client attached and
   (ideally) a RUNNING session in flight; verify parked/awaiting state is
   byte-identical across the restart, shutdown is graceful (~11s, no
   SIGKILL), nothing stuck RUNNING after.
5. **Cleanup + proof** — work through CLEANUP.md; retire `testrun-` agents
   (skills need a DB purge — no delete endpoint: skill_chunk via doc_id →
   skill_doc → agent_skill_grant → skill); delete the Jira issue; restore
   auth; then DIFF live roster/grants vs `baseline/` and record: N real
   agents unchanged, zero field drift, only `testrun-*` differ.
   **Graph writes leave TWO halves**: every hand-made node/edge ALSO writes
   an `operator manual entry` Episodic wrapper the entity delete does not
   cascade to — sweep those by CONTENT (`n.content contains 'TESTRUN'`),
   not by name prefix. Found 2026-08-27: five of them sat in the operator's
   Memory panel after a cleanup that had verified "zero TESTRUN residue".
6. **Verification pass (MANDATORY before anyone acts on findings)** —
   fresh-context sonnet verifiers re-run every actionable finding's repro
   AND re-read its cited source line, adversarial stance, verdict
   CONFIRMED/REFUTED/PARTIAL into `verify/`. Also audit the run's own
   judgment calls (every approval: was it in scope?). Annotate REPORT.md;
   strike retracted findings through in place.

## Lessons that came from real failures — do not relearn them

- **Verify a route exists in SOURCE before filing a finding against it.**
  The generated route inventory hallucinated a REST end-session route; two
  independent lanes then "empirically confirmed" a 500 against it (a 405,
  misread), citing a wrong source line. Caught only in verification. Lane
  briefs must say: confirm the decorator in source, quote file:line.
- **Two lanes agreeing is not confirmation** if both trusted the same wrong
  upstream artifact. Independence requires independent grounding.
- **Sub-agents that stop to "wait for background work"**: nudge them to
  poll ONCE, record current state as the result, and finish their report.
- **Fast local models finish trivial tasks in 5-9s** — you cannot catch a
  live RUNNING session with a trivial task; give restart-race subjects
  genuinely long work, or accept the pytest coverage.
- **The approve endpoint stamps `approver: human:lee` regardless of
  caller** (accepted risk, operator decision 2026-08-20) — provenance does
  not distinguish operator clicks from API calls; don't cite it as proof of
  operator action.
- **The operator skills-import API is deliberately ungated** (same
  decision) — not a finding, don't re-file it.
- Assert on rows your test created, never global counts; pin
  `dispatch_approval_limit` and `demo_mode` where the existing test rules
  say so (see CLAUDE.md).
