# Lane brief templates

Battle-tested sub-agent briefs from the 2026-08-20 run. Substitute `<date>`
and the run dir. Every brief inherits the SKILL.md ground rules — repeat the
relevant HARD RULES inside each prompt verbatim; sub-agents don't see the
skill. Every lane writes to `drafts/test-run-<date>/findings/<lane>.md` and
returns: test count, findings one-liners with severity guesses, and the list
of artifacts it created (for CLEANUP.md).

Universal brief additions (paste into every lane):
- "Before filing any finding against a route, confirm the route exists in
  SOURCE (the decorator in central_command/api/routes.py or web/server/routes/)
  and quote file:line. Do not trust any generated inventory."
- "Read the handler before judging a response wrong — if the code
  intentionally does it, record 'intentional but questionable', not FINDING."
- "curl --max-time 20 on every call. If you're tempted to wait on background
  work, poll once, record the state, finish your report."

## Phase 0 — baseline snapshot (haiku)

Snapshot live state so cleanup can be verified. Capture as JSON via the API
(discover GET endpoints by grepping routes.py — do not guess URLs): roster,
skills, charters per agent, grants, schedules, decisions, dispatch. Ground
truth from Postgres (DSN in SKILL.md): agent, agent_grant, guidance_version
as CSVs into baseline/db/. Write baseline/NOTES.md listing files + row counts.

## Phase 0 — route inventory (haiku)

For each FastAPI + Hono route: method, path, handler name, one-line purpose,
body fields from the pydantic model. MARK each row with the file:line of the
decorator — a row without a source citation is not allowed in the table.
List cockpit pages from web/src App/router. Output: route-inventory.md.

## Phase 1 — GET sweep (haiku, read-only)

Hit EVERY GET route on both servers. IDs from list endpoints. Skip SSE bodies
(headers only, -m 5), the WebSocket, /api/gateway/restart. Record status,
time, one-line shape check. Flag: 500s, timeouts, HTML-where-JSON,
error-in-200, suspicious emptiness. NO mutations of any kind.

## Phase 1 — negative/wrong-state (sonnet)

For every mutating route reachable within the safety rules: nonexistent ids
(expect 404 — record 409s and 500s), malformed/empty/wrong-type bodies
(expect 422), wrong-state transitions using read-only-discovered REAL data
(approve an already-DECIDED proposal, resume a non-STOPPED session, ...),
boundary junk (long strings on nonexistent-id paths, negative limits, bad
query params). HARD: never decide a PENDING proposal, never mutate a real
agent, never start loops, no email.

## Phase 1 — graph CRUD (sonnet; only if operator confirmed graph disposable)

Prefix everything `TESTRUN-`. Node/edge CRUD verifying BOTH identity halves
(labels + n.labels property; relationship + uuid properties) and semantic
findability (proves name_embedding written). Type-allowlist refusal. Merge
preserving edges. Negative: nonexistent deletes, self-merge, empty/huge
names. Self-clean to exact baseline counts even on failure paths.

## Phase 1/2 — cockpit direct routes (sonnet, needs auth per SKILL.md)

Login, then: workspace/kanban/transcribe/tts/language config GET→PUT→verify→
RESTORE roundtrips (restore mandatory even on failure); files API in a
`testrun-<date>/` subdir only, full write/rename/move/trash/restore cycle;
path-traversal probes on files/read + resolve (`../../etc/hostname`,
url-encoded, absolute — MUST be refused, report exactly what happens);
memories gating; sessions/:id/model with real + fake ids. No /api/keys PUT,
no gateway restart. Clean your subdir.

## Phase 2 — Playwright UI walk (sonnet, SOLE browser owner)

Login via the real gate. READ/RENDER pass: every view, both side panels,
every dialog (open then cancel). Per view: snapshot + console messages +
self-initiated failed network requests. Resize 800px and 1600px hunting the
container-query blank-panel class. Click a few non-writing buttons per view
to catch dead controls; note spinners still alive after 10s. Screenshot only
on suspicion. FORBIDDEN: any approve/send/save/delete.

## Phase 3 — ledger + verdicts (sonnet)

Hand-feed controlled TESTRUN emails; drive all three verdicts on proposals
THIS lane created: approve (only the one permitted Jira write), reject
(verify drafter resumes + redrafts), dismiss (verify withdrawn, folds
released, never resumed). Fold behavior with near-duplicates. Dispatch:
/api/dispatch/step only (never /start); verify approval-limit backpressure.
Idempotency: re-feed same Message-ID. Verify event ORDER (decided before
executed / before redraft's created) and provenance stamping.

## Phase 3 — agent lifecycle (sonnet)

Against a fresh `testrun-lifecycle` hire only: roster join with generated
capability list; grant/revoke (revoke must be timestamp, row kept); model
set; charter save (append-only) + revert (creates NEW version); skill
add/remove; coach run (if it drafts a charter proposal for YOUR agent,
verify claim_matches_source fired, then approve or dismiss); MCP override
PUT/DELETE; retire → reactivate → retire.

## Phase 3 — skills import (sonnet)

`testrun-` named skills only. import-doc / import-file / import-site /
consolidated import; verify content lands and (known accepted behavior)
that these are DIRECT operator writes, no proposal. Grant/ungrant a testrun
skill to a `testrun-skillholder` hire, never a real agent. Negative: missing
fields, unknown ids. Note for cleanup: skills have NO delete endpoint.

## Phase 3 — chat + attachments (sonnet)

Fresh `testrun-chat` conversational hire. Basic reply; two concurrent
sessions; close (status flip, transcript kept, idempotent re-end);
attachments: text doc (agent quotes planted content), image-only/scan PDF
(OCR fallback path — verify empty-output invariant, no silent ship), raw
image file (clean declared-capability refusal, no 500), oversized (clean
attachment_too_large refusal — if the socket dies 1009 instead, the ws cap
regressed below the app cap). Negative: nonexistent session, ended session,
empty attachment.

## Phase 3 — heartbeat (sonnet)

Record engine state FIRST, restore at end. `testrun-` schedules only, bound
to registry actions (prefer non-authorising like feed.poll). Run-once over
engine starts. Verify last_fired_at, quiet-fire writes no events,
self-recording actions write their own. Toggle/patch/delete + full negative
set (bad cron 422, unknown action 422, nonexistent id 404). Self-clean.

## Phase 3 — MCP governance + sync_source (sonnet)

Gate flip + RESTORE on one tool; per-agent override widen/narrow/DELETE on a
`testrun-mcp` hire only. Source-verify (never provision): _mcp_servers_root
parity across runtime/tools.py and gateway/executor.py, propose-time content
capture (executor writes args["files"], never re-reads), name translation.
Run the related pytest files. servers/ on disk matches API list.

## Phase 3 — orchestrator + auditor (sonnet)

Tiny TESTRUN-labeled tasks only. route → inspect recommendation → assign →
check /routing/agreement records overrides as disagreements. stop/resume/
cancel + negatives (see SKILL.md lesson: trivial tasks finish too fast to
catch RUNNING). orchestration/sweep idempotence. Auditor: read the
concurrence surfaces; drive dismissals/audit only against disposable backlog
items, else negatives only.

## Phase 4 — resilience (orchestrator does this directly, not a sub-agent)

Pre-capture proposal/session status buckets. Attach SSE client. Restart
cc-uvicorn. Verify: buckets byte-identical, graceful shutdown in journal
(no SIGKILL, no TimeoutStopSec hang), zero stuck RUNNING, SSE client exited
cleanly. Confirm PID actually changed — a failed wrapper can no-op the
restart and everything "passes".

## Phase 6 — verification (fresh-context sonnet, adversarial)

One verifier per findings cluster. For each finding: re-run the repro AND
independently re-read the cited source (assume the citation may be wrong —
find the real code yourself). Verdict CONFIRMED / REFUTED / PARTIAL with
one-line reason into verify/. The orchestrator separately audits every
approval/decision the run made and re-runs the baseline diff fresh.
