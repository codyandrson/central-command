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
  (Jira) are allowed, and whether the operator will restart a unit for the
  resilience phase.
- **A session never restarts a live `cc-*` unit.** The operator deploys and
  restarts through the cockpit updater (release discipline: a bare unit
  restart skips the checkpoint, the dumps and the rollback). The sweep
  observes; where a phase needs a restart (resilience, a temp cockpit
  password) it asks the operator to do it, or skips that phase and leans on
  the pytest coverage — it never runs one itself, and no sub-agent brief
  contains a restart command.
- **NEVER approve a proposal that sends email.** Reject/dismiss those as part
  of verdict testing instead.
- **At most ONE external-write approval per run** (Jira create), and only
  with summary/description `CC TESTRUN <date> - delete me` +
  label `cc-test`. Record the issue key in CLEANUP.md immediately.
  Delete it in cleanup via `jira._call("DELETE", jira._api("/issue/<KEY>"))`
  (`_api()` picks the REST version the configured flavor speaks).
- **Mutate only artifacts you create, prefixed `testrun-`.** Real roster
  agents may be TASKED (tiny, "TESTRUN"-labeled tasks) but never
  charter-saved, coached, granted/revoked, retired, or model-changed.
  Any pre-existing test-ish agents the roster already holds are also
  off-limits — only clean up what THIS run created.
- **Only decide proposals/operator-items your run created** — capture each
  id at creation. Never probe (even 422 probes) against real pending items.
- **Any config you change: GET/save the original FIRST, restore after,
  restore-on-failure too.** Applies to MCP tool gates, heartbeat engine
  state, cockpit configs, and the auth hash (below).
- **Don't** start dispatch/feed continuous loops, provision a sandbox, or
  touch `/api/gateway/restart` at all.
- **Models:** every sub-agent gets an explicit model — haiku for mechanical
  sweeps (GET sweep, inventory, snapshots), sonnet for judgment lanes.
  Never let a lane inherit the orchestrator model.
- **Playwright has ONE owner at a time** — serialize all browser agents.

## Environment facts (verified 2026-08-20 — re-verify if stale)

- FastAPI :8080, routes under `/api` EXCEPT health at `/health`.
  Cockpit (Hono) :3080; its health also `/health`. The cockpit's public
  routes are five (`PUBLIC_ROUTES` in `web/server/middleware/auth.ts`):
  `/health`, `/api/version`, `/api/auth/status`, `/api/auth/login` and
  `/api/auth/logout`.
- Postgres DSN `postgresql://central_command:central_command@localhost:5442/central_command`.
- Restarts are the operator's, never the session's (see Ground rules): a
  backend or cockpit change goes live through the updater.
- Direct-task body field is `instruction` (singular). Hire: omit `packs`
  or pass a non-empty list.
- **Cockpit auth cannot be minted**: `NERVE_SESSION_SECRET` is ephemeral
  (in-memory). To test authed surfaces, ASK THE OPERATOR for the password,
  or skip the authed lanes. A temp `NERVE_PASSWORD_HASH` needs the cockpit
  unit restarted to take effect, and again to undo it, so it is only ever
  done by the operator themselves (they back up `web/.env`, set a scrypt
  hash `salthex:keyhex`, restart, and restore and confirm the temp password
  401s afterwards) — never by the session.

## Phases

Create `drafts/test-run-<date>/` with `REPORT.md` (findings, ranked),
`CLEANUP.md` (started at phase 0, updated the moment any artifact is
created), `findings/`, `baseline/`, `verify/`.

0. **Baseline** — full `pytest -q` (it needs the dev Postgres, and its live graph reads skip when the backend is down); snapshot roster/grants/charters/
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
3b. **Sandbox reach (operator-run, once per release apply)** — the live half
   of the sandbox-isolation guarantee: a throwaway curl pod in `cc-sandbox`
   must NOT connect to the API's port, the cockpit's port or the cockpit's
   tailnet-published URL. The commands are the "Sandbox reach drill" in
   `deploy/k3s/README.md` (§9, placeholders only); run them with the
   operator's OK, never from pytest, and delete the pod afterwards. Egress in
   general is open on purpose — this checks these three listeners only.
4. **Resilience (operator-run)** — the session never restarts the unit: it
   pre-captures state, attaches an SSE client and (ideally) starts a RUNNING
   session, then asks the operator to restart the backend through their usual
   path and says when it is ready; afterwards verify parked/awaiting state is
   byte-identical across the restart, shutdown is graceful (~11s, no
   SIGKILL), nothing stuck RUNNING after.
5. **Cleanup + proof** — work through CLEANUP.md; retire `testrun-` agents
   (skills need a DB purge — no delete endpoint: skill_chunk via doc_id →
   skill_doc → agent_skill_grant → skill); delete the Jira issue; confirm with
   the operator that any temp cockpit password was undone; then DIFF live roster/grants vs `baseline/` and record: N real
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
- **The approve endpoint's `approver` stamp does not distinguish operator
  clicks from API calls** — `gateway.gateway._operator_actor()` derives it
  from `CC_OPERATOR_NAME` (fixed 2026-08-21, rehearsal finding F11; it used
  to hardcode a literal operator name), so any caller that reaches the
  endpoint gets the same stamp. Provenance proves the endpoint was called,
  not that the operator clicked — don't cite it as proof of operator action.
- **The operator skills-import API is deliberately ungated** (operator
  decision 2026-08-20) — not a finding, don't re-file it.
- Assert on rows your test created, never global counts; pin
  `dispatch_approval_limit` and `test_model` where the existing test rules
  say so (see `.claude/rules/tests.md`).
