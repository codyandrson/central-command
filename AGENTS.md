# AGENTS.md — Central Command agent instructions

> Covers only what applies to ANY Central Command deployment. Keep facts about
> YOUR deployment (hostnames, live state, operational history) out of this
> tree — put them in a private instance repo or a gitignored `CLAUDE.local.md`.
> Subtree-specific bite marks live in `.claude/rules/` and load by path.

<!-- bmad:context -->
<!-- Verified 2026-09-19 against ba173438. Managed by bmad-project-context; edits inside this block are replaced on refresh. Keep anything you want preserved outside the markers. -->

## Central Command

A human-supervised agentic-team framework: a control plane where the operator tasks Claude agents and approves every action that changes the world. Python 3.12 (FastAPI, Pydantic AI) with a React/Hono cockpit in `web/`, Postgres as the spine, Graphiti/Neo4j, LiteLLM, n8n. Single-operator and self-hosted, with an air-gapped deployment target; the risk model is error, not malice. Full design: `docs/DESIGN.md`; what changed: `CHANGELOG.md`; design records: `docs/superpowers/`.

## Policy

- Nothing changes the world without passing an approval gate; the tier rules are under Architecture below.
- Secrets live only in `.env` (gitignored). Never commit or print them, and keep anything sensitive or personal out of tracked files: no real operator identity, no real mail/issue content, no deployment facts, nothing that lowers the cost of attacking an instance. Prose says "the operator"; examples use the Doe/Rivers cast and `example.com`; instance facts go in a private repo or gitignored `CLAUDE.local.md`.
- Never weaken `scripts/prepush-scan.sh` (the pre-push guard) to get a push through; move the content to a private repo instead.
- `master` is the only branch — no `main`. A feature branch is a deliberate choice to raise with the operator, not a default.
- A release is three things: a CHANGELOG entry, a `VERSION` bump, a tag. The updaters read `VERSION` as the installed version — not git, not the tag; `tests/test_version_file.py` pins it to the newest CHANGELOG heading.
- Never change `LITELLM_SALT_KEY` or `N8N_ENCRYPTION_KEY`; they encrypt the stored LiteLLM virtual keys and the Gmail OAuth credential, and a restore under a different key leaves the rows present but undecryptable.
- Never edit `docs/vendor/` (refetch via `scripts/vendor_docs_fetch.sh`) or hand-create anything in `servers/` (it arrives only through an approved `mcp.sync_source`). Regenerating `requirements.lock` is a deliberate act.

## Where things are

- `web/` is a vendored fork of openclaw-nerve; ours are `web/server/routes/cc-*.ts`, `web/server/lib/gateway-*.ts` and `web/src/features/`. Ignore `web/vendor-unused/`, `web/docs/` and `web/.github/` — upstream leftovers; see `web/VENDORED.md`.
- Touching `deploy/`? `deploy/k3s/README.md` is the multi-node runbook, `deploy/single/` the Compose profile, and read `deploy/AIRGAP.md` FIRST for any mirrored or no-egress install.
- Queue operations (enrolling the fixture backlog, stepping the dispatcher, reading the audit trail): `docs/QUEUE.md`.
- Subtree bite marks load from `.claude/rules/` when a matching file is read: `graph`, `deploy-k3s`, `deploy-single`, `models`, `cockpit`, `integrations`, `runtime-resilience`, `tests`. Working in one of those areas without having opened its files? Read the rule file first.

## Running and verifying

- Python `>=3.12`, Node `>=22` (`web/.nvmrc`). Install with `pip install -e ".[dev,runtime]"`; build the cockpit with `(cd web && npm install && npm run build)`; serve with `uvicorn central_command.api.app:app --port 8080`.
- There is no CI — run the suite yourself before pushing. `pytest -q`, sequential, is the gate (~4 min): it is the run that catches a test leaking state into its neighbour, which xdist workers hide. `-n 4` (~2 min, one database per worker) is only the quick pass. Cockpit tests are separate: `(cd web && npm test)`.
- Dev database: `docker compose up -d postgres` → `cc-postgres` on 127.0.0.1:5442 with the schema auto-loaded. 5442, never 5432 — the port is baked into `.env`, `.env.example`, the compose files, the k3s manifests and the `config.py` default.
- A fresh git worktree has no `.env`; copy it in before running the suite.
- `python scripts/m5_acceptance.py` is the live acceptance run: it needs a key and a Postgres, and spends money.

<!-- /bmad:context -->

`deploy/pi/` is part LIVE, part retired — never delete it as a whole as
"the superseded compose stack." See `deploy/pi/README.md` for which files
`deploy/k3s/` still reads directly and which are rollback-only.

## Architecture — runtime is Pydantic AI 2.0 + Claude

Modular monolith, 4 tiers:
1. **Control Plane UI** — React (`web/`)
2. **Control Plane API & Services** — FastAPI (`central_command/api`, `central_command/gateway`)
3. **Agent Runtime** — Pydantic AI + Claude (`central_command/runtime`) — can **only propose + read**
4. **Stores** — Postgres (spine), Graphiti/Neo4j (knowledge graph, via MCP), n8n
   (email façade holding the Gmail OAuth; Jira is a native client in
   `integrations/jira.py` — n8n only where it already solved a hard integration
   problem)

**The trust boundary is the import graph:** `runtime/` must never import
`gateway`/`executor`. Agents hold only `propose_*` tools; the credentialed
Executor performs writes *after* approval. Keep it that way.

## Conventions & gotchas (bite marks)

Rules below exist because a real failure produced them. Trust the rule even
where the story is gone.

- **Two run modes** (config): `CC_DEMO_MODE` (deterministic FunctionModel vs
  real Claude) and `CC_EXECUTOR_MODE` (`dry_run` logs writes vs `live`
  performs them). The one model seam is `runtime/models.py:resolve_model()`.
- **Proposal arg shape:** real Claude *flattens* the `Proposal` fields at the
  top level of tool-call args (Pydantic AI hoists a single-model tool param);
  the demo model nests them under `proposal`. Always parse via
  `durable.proposal_from_call()` — it tolerates both.
- **A proposal's `agent_id` is the DRAFTER, never the subject.** The coach
  drafts charter edits for other agents, so the two differ: the proposal row
  (session, Inbox attribution, throttle count) belongs to the agent that
  generated it; the target lives in the action arguments where the Executor
  reads it. `executor.execute()` takes `proposer` from the gateway for
  exactly this reason — an actor an agent can write into its own args is an
  agent-authored claim about provenance.
- **A fresh run must load the charter; `build_agent_for` does not.** It is
  the RESUME factory and passes no charter (the persisted history carries the
  original prompt); `run_task` and `run_coach` load the real one. If you add
  a third fresh-run path, load the charter.
- **`system_prompt=` is load-bearing — never "modernise" it to
  `instructions=`.** A system prompt is PERSISTED into the message history,
  so a session paused at a deferral resumes under the charter it was proposed
  under; `instructions=` is re-applied from the live agent at every run — a
  resume would silently execute under whatever the charter says NOW.
  Rewriting the record of what an agent was told when it proposed is a
  governance change wearing an idiom cleanup's clothes.
- **Never swap the approval gate for pydantic-ai's `approval_required()`.**
  Its semantics are "pause, collect a yes, then run the tool IN THIS PROCESS"
  — which puts the write back in `runtime/`, the tier that may never hold
  credentials. `CallDeferred` + `propose_*` + the credentialed Executor is
  the shape on purpose: the runtime cannot perform the write even if it is
  approved by mistake.
- **A fresh database must reproduce the roster.** `schema.sql`'s
  founding-roster UPDATEs are guarded by `where … and role = ''`, so the
  INSERT above them must NOT seed `role`. If you add a seeded column, ask
  what a brand-new database does, not what the dev box does.
- **`schema.sql` is auto-loaded on a FRESH database only — nothing applies it
  at runtime.** The test DB re-executes it every run, so a new column passes
  the whole suite and then breaks a live deployment with an undefined-column
  error that reads like a code bug. Apply additive columns to the running
  database in the same change that adds them to the file (they are
  `add column if not exists`, so it is idempotent).
- **The agent sandbox is CONTAINMENT, not restriction.** It exists so an
  agent can work freely without risking the host — NOT to limit what it can
  reach. Egress is open; the gate is the CAPABILITY, never reachability —
  every tool that CHANGES a service is a gated `propose_*`. What constrains a
  sandbox is that it holds NO credentials; provisioning one converts reach
  into ungated writes, so that is an explicit operator decision.
- **The sandbox's only exit is `mcp.sync_source`, and content is captured at
  PROPOSE time.** The tool embeds the files in the proposal; the Executor
  writes exactly those bytes and never re-reads the sandbox — an agent that
  could swap content after approval would make review theater.
  `_mcp_servers_root()` must resolve to the SAME path in `runtime/tools.py`
  and `gateway/executor.py` (a guard test pins it).
- **A proposal has THREE verdicts.** Approve executes; Reject means "fix it
  and try again" and RESUMES the drafter, which redrafts; **Dismiss** means
  no action needed — WITHDRAWN, folds released, session closed, and the agent
  is NEVER resumed (a test makes the agent factory raise if any path tries).
  Rejecting to make something go away is the trap Dismiss exists to end.
  Nothing feeds coaching automatically — the operator curates signals case by
  case.
- **A proposal's argument SHAPE is checked in BOTH tiers from ONE list —
  `contract.ARG_SPECS`.** The runtime hands a malformed draft back to the
  model (`tools._validate_proposal`, ModelRetry with every problem at once);
  `executor.execute()` runs the same `validate_action_args` over every action
  before the first runs. Adding a capability whose handler subscripts
  `args[...]` means adding its spec. Only SHAPE goes in the spec;
  world-state checks stay Executor-only because the world can change between
  propose and approve. Every `propose_*` tool carries `max_retries=10` —
  pydantic-ai's default of 1 fails the session on the second bad draft.
- **A "never guess X" docstring needs a tool that can READ X.** Agents will
  invent plausible values for any required argument nothing in the system can
  answer (hence `jira_list_projects`). Before coaching an agent for guessing
  — or for following bad documentation — check whether anything in the system
  could actually answer the question. And don't build a propose-time guard
  for a failure that is already LOUD (400 → FAILED → redraft); guards exist
  for failures that return 200 and bind nothing.
- **The event log is append-only, so write order is read order forever.**
  Emit an event *before* the thing it authorises — `proposal.decided` is
  written inside `gateway.approve_and_execute` before `executor.execute()`,
  never from an API route.
- **…but a record that authorises nothing needn't be written at all.** A
  heartbeat action may declare `ActionSpec.material` only if it is
  *non-authorising* AND *self-recording* — an explicit, parity-tested
  allowlist. Errors are always material; cadence liveness lives in
  `last_fired_at`, not in history. Don't quiet an action that authorises
  work.
- **Agents take `deps`** (`runtime/deps.py`). Every `agent.run()` needs
  `deps=TriageDeps(...)`; resume paths pass a fresh one on purpose.
- **Every proposal takes one path too:**
  `runtime/proposals.park_proposal()` — it pauses the run at its deferred
  call, persists the proposal, AND emits `proposal.created`.
  `tests/test_proposal_created.py` walks the source: a
  `repo.save_proposal(...)` anywhere but the seam fails the suite.
- **`events.emit()` is the only way to publish.** It writes to Postgres then
  fans out; the SSE stream is a projection of the table, never a parallel
  channel. **The emit-before rule is about *authorisation*, not
  bookkeeping:** a proposal authorises nothing — its *decision* does — so
  `proposal.created` lands after its save and that is not a violation.
- **No agent claim is trusted unverified — on every path that makes one.**
  `contract.claim_supported()` re-checks a quoted claim against the recorded
  source. It lives in `contract/`, not `gateway/`, because BOTH tiers need it
  and `runtime/` may never import `gateway/`. On the coach path the agent
  supplies the POINTER as well as the quote, so citing a signal the session
  was never fed is flagged exactly like a misquote. **`claim_matches_source:
  None` means NOT CHECKED** — never "checked and inconclusive". Don't fork
  the checker, and don't backfill the flag onto decided proposals — that
  would falsify the record.
- **Roster membership = a non-empty `role` column** (set only by schema seeds
  and `repo.hire_agent`). Plain `upsert_agent` registrations never join the
  roster — don't "fix" an agent's absence by upserting; hire it.
- **Grant revocation is a timestamp, never a delete.** `agent_grant` rows
  keep history so the idempotent schema seeds can't resurrect a revoked
  pack. An agent's charter capability list is GENERATED from grants at run
  start — never write capability lists into charter text.
- **Sessions are never destroyed.** Closing a conversation is a status flip
  (`converse.end_conversation`, guarded) with the transcript kept forever.
  The `agent:<id>:main` alias means "the CURRENT lane": the newest
  conversation IF it is open, and NONE when that newest lane is terminal —
  it deliberately does NOT fall back to an older still-open session
  (`nerve_gateway._latest_conversation(open_only=True)` is the one seam).
  Multiple concurrent sessions per agent is the TARGET architecture; never
  add code assuming one session per agent.
