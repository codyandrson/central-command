# CLAUDE.md — Central Command orientation

> Read this first. It orients a new session; **`docs/DESIGN.md`** is the full
> design; **`CHANGELOG.md`** is the public what-changed record. This file
> covers only what applies to ANY Central Command deployment. Keep facts about
> YOUR deployment (hostnames, live state, operational history) out of this
> tree — put them in a private instance repo or a gitignored `CLAUDE.local.md`
> alongside this file.

## What Central Command is

A **human-supervised agentic-team framework**: a control plane over a team of
Claude agents where the operator is team lead — tasking agents, approving
their actions, answering their questions, and coaching behavior over time.
**Nothing changes the world without passing an approval gate.**
Single-operator, self-hosted; designed to run in a homelab and to support an
air-gapped deployment target. Primary risk model is **error, not malice**
(hallucination / misreads), so the guards are reliability guards that also
happen to stop misuse.

The full spine works end-to-end: **agent proposes → durable pause (survives a
full restart) → Decisions Inbox → operator approves → gateway → Executor
performs the real write → provenance stamped → agent resumes.** It runs on
real Claude against real Gmail and Jira, and in **demo mode** (no API key,
dry-run writes) for safe dev. Beyond the spine: a graduated auditor, a
coaching loop that produces charter versions, a multi-agent orchestrator, a
heartbeat that schedules recurring team work as governed data, capability
packs and a roster held as data rather than code, and a skills library.

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

## Deployment profiles

- **`deploy/k3s/`** — the multi-node reference deployment (two-node k3s:
  an arm64 "anchor" node and an amd64 "compute" node). `README.md` there is
  the clean-install runbook the /setup skill conducts phase-by-phase;
  `setup.sh` is the driver, `verify.sh` the assertion suite, `cc-update.sh`
  the one-click updater covering the full release surface (code, schema,
  manifests, unit files, locally-built images, with rollback).
- **`deploy/single/`** — the single-node `podman kube play` profile.
  `setup.sh` is a deterministic driver (validate / preflight / fetch / llm /
  stack / app / verify / test / boot / demo, PASS/WARN/FAIL/USERACTION,
  exit 0/1/2/3, `diagnose` support bundle); the
  /setup skill's job is elicitation and diagnosis only. `images.txt` pins
  every pulled image by digest; `bundle.sh` exports/imports the
  checksum-verified air-gap bundle.
- **`deploy/AIRGAP.md`** — the map of every external source and its seam
  (mirrors primary, bundle explicit). Read it FIRST for any mirrored or
  no-egress install.

k3s facts that generalize to any multi-node install:

- **Placement splits by STATE, not by service.** Stateless compute floats
  (preferred nodeAffinity, short NoExecute tolerations for ~1-min failover);
  stateful services are pinned, because `local-path` stamps a *required*
  hostname nodeAffinity on every PV — and shared storage is no way out
  (Neo4j forbids NFS: no file locking → store corruption). Placement resolves
  via the `cc-role/anchor` / `cc-role/compute` node labels, never hostnames;
  `setup.sh` preflight requires exactly one node per label.
- **Exposure is ServiceLB (k3s Klipper), not NodePort** — the real port binds
  on every node and follows the pod, so every `CC_*` endpoint in `.env` must
  resolve to LOOPBACK; a hard-coded node address works today and silently
  breaks failover (`verify.sh` guards this). Pinned services use `hostPort`
  on `hostIP: 127.0.0.1`.
- **A floating pod needs its image on BOTH nodes**, and locally-built images
  need `imagePullPolicy: IfNotPresent` (`Always` CrashLoops on a tag no
  registry serves). A missing image is invisible until the pod actually moves.
- **A release is THREE things: a CHANGELOG entry, a `VERSION` bump, a tag.**
  The updaters read `VERSION` as the installed version — not git, not the
  tag. `tests/test_version_file.py` pins VERSION to the newest CHANGELOG
  heading.
- **Never change `LITELLM_SALT_KEY` or `N8N_ENCRYPTION_KEY`.** They encrypt
  the stored LiteLLM virtual keys and the Gmail OAuth credential; a restore
  under a different key leaves the rows present but undecryptable.

## Run it

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,runtime]"
cp .env.example .env                 # then fill CC_LLM_API_KEY for live mode
(cd web && npm install && npm run build)
uvicorn central_command.api.app:app --port 8080   # run modes: CC_DEMO_MODE / CC_EXECUTOR_MODE, see .env.example

pytest                               # offline tests (contract + durable)
python scripts/m5_acceptance.py      # live acceptance (needs key + a postgres)
```

For a standalone dev database, the root `docker-compose.yml` starts it:
`docker compose up -d postgres` — `cc-postgres` on **127.0.0.1:5442** with the
schema auto-loaded, matching the default `CC_DATABASE_URL`. Queue operations —
enrolling the fixture backlog, stepping the dispatcher, reading the audit
trail — are `docs/QUEUE.md`'s job.

**5442, never 5432.** The port is baked into `.env`, `.env.example`, the
compose files, the k3s manifests and the `config.py` default — changing it
buys nothing and breaks all five.

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
- **Secrets live only in `.env` (gitignored).** Never commit or print them.
  Nothing sensitive or personal reaches this repo's tracked files: no
  secrets, no real operator identity or real mail/issue content (prose says
  "the operator"; examples use the placeholder Doe/Rivers cast,
  `example.com`), nothing that lowers the cost of attacking an instance.
  `scripts/prepush-scan.sh` is the pre-push guard — never weaken it to get a
  push through; move the content to a private repo instead.
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
- **Models live in LiteLLM's DATABASE and belong to the operator; setup
  PAUSES for them.** `store_model_in_db: true`, no `model_list` in the config
  file, and `register-models.py` is CREATE-ONLY: an absent alias becomes a
  skeleton (`PLACEHOLDER` where the provider goes, never a key) and an
  existing row is never written to. Both `setup.sh` drivers exit 3 on a fresh
  catalog on purpose — the operator fills in providers and credentials in the
  proxy UI before anything else deploys — and the re-run validates each alias
  with a real request, including a Responses-API `structured` probe through
  `graphiti-llm`. Don't reintroduce provider values into a tracked
  declaration or into `.env`.
- **The single-node install ACQUIRES before it deploys, and never falls back
  on its own.** `setup.sh fetch` is the one phase that touches the network;
  each failure names its `.env` seam and the phase exits 3. Mirror is
  primary; `CC_SOURCE_<X>=bundle` is the operator's explicit per-artifact
  fallback (`bundle.sh`, checksum-verified). Adding an image means adding its
  digest line to `images.txt` — the seam test fails otherwise.
- **A model's capabilities are MEASURED, never guessed — and an undeclared
  flag is not neutral.** Central Command reads an absent `supports_*` as
  "nobody said" (fails closed); LiteLLM's aggregate `/model_group/info`
  coerces the same absence to **false**. `litellm_probe_model` sends one real
  request per capability and returns the declared→observed diff. Probe trap:
  a thinking model can spend a small `max_tokens` on reasoning and answer
  HTTP 200 with EMPTY content — that is "inconclusive", never "no". Declared
  fleet facts live in `model-preferences.yaml`; after a release that changes
  them, `policy.py --apply` — the updater does not.
- **LiteLLM forbids `-` in MCP server names; Kubernetes requires it.**
  `mcp_server.id` stays the k8s-valid canonical id and the proxy sees
  `_litellm_server_name()`'s underscored form — translated in exactly two
  places, the Executor and `runtime/packs.py`'s toolset URL (duplicated
  because runtime may never import gateway). The proxy's
  `/mcp-rest/tools/call` needs `server_id` in the BODY.
- **A proposal has THREE verdicts.** Approve executes; Reject means "fix it
  and try again" and RESUMES the drafter, which redrafts; **Dismiss** means
  no action needed — WITHDRAWN, folds released, session closed, and the agent
  is NEVER resumed (a test makes the agent factory raise if any path tries).
  Rejecting to make something go away is the trap Dismiss exists to end.
  Nothing feeds coaching automatically — the operator curates signals case by
  case.
- **A Graphiti entity and edge each store their identity TWICE, and a
  hand-made write must set both halves.** Types live in real Neo4j labels AND
  an `n.labels` property; edge endpoints live in the relationship AND in
  `source_node_uuid`/`target_node_uuid` properties — the cockpit draws from
  the PROPERTIES. Neo4j cannot repoint a relationship in place, so a repoint
  is copy-then-delete (`integrations/neo4j_writer.py`). And **a node with no
  `name_embedding` is invisible to the semantic half of hybrid search** while
  still turning up in keyword hits — every write re-embeds through the
  `cc-embedding` alias at exactly 1024 dimensions; a mis-sized vector is
  dropped rather than stored (nothing schema-side enforces the width). Neo4j
  5.x has no parameterized labels, hence the closed `ENTITY_TYPES` allowlist
  mirroring `graphiti/config.yaml`.
- **The suite may not write to the live graph** — over MCP
  (`no_live_graph_writes`) or over bolt (`neo4j_writer._write` refuses). Opt
  in with `CC_LIVE_GRAPH_TESTS=1` when you mean it: one leftover scratch
  entity is enough to fail an unrelated live read test. And **an async bolt
  driver cached at module scope is loop-bound**; `neo4j_reader._get_driver`
  keys its cache on the running loop, and the writer shares that driver
  because access mode is a property of the SESSION, never of the driver.
- **A Jira gadget's config keys are declared BY THE GADGET — read its XML,
  never a table.** A gadget silently ignores any pref it does not declare, so
  a wrong key is a 200 with no binding and no error.
  `_prepare_gadget_configs` fetches each gadget's own `<UserPref>` list and
  refuses undeclared keys; that same fetch is the URI check.
- **`@container` must sit on a PARENT of whatever uses `@3xl:`.** A container
  query resolves against an ANCESTOR container, never the element's own — so
  the variant silently never applies on the element that declares the
  container, while its children's variants work. jsdom has no layout engine,
  so no render test can catch this — `web/src/features/container-query-scope.test.ts`
  reads the SOURCE instead.
- **`tsc` does not delete removed sources from `server-dist`.** Delete a
  `server/` module and its compiled `.js` ships on after a rebuild — remove
  it by hand in the same change.
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
- **The orphan sweep must land the TASK, not just the session.**
  `repo.fail_stale_running_sessions` is the only thing that CAN land it — the
  park that would have recorded the death is written in an `except` block a
  dead process never reaches. FAILED, never retry-parked: a restart is not a
  dependency outage, and silently re-running whatever was in flight spends
  tokens on work the operator never re-issued.
- **The cockpit hand-declares its own interfaces over untyped RPC payloads,
  so a field the gateway never sends is invisible to `tsc` AND to every
  frontend test** — the feature silently never renders. The defence is a
  BACKEND test asserting the wire shape; a frontend test building its own
  payload object proves nothing.
- **`nerve_gateway._COMPOSER_DISABLING` is an ALLOWLIST, not a check.** A new
  `why_not_sendable` refusal code missing from that tuple leaves the cockpit
  rendering an ENABLED message box that `_chat_send` then 409s. Adding a
  refusal code means adding it there too, with a guard test.
- **Graphiti's extraction needs the BRIDGED LiteLLM alias.** graphiti_core
  drives extraction through the **Responses API** with no chat-completions
  fallback; an OpenAI-compatible backend without `/v1/responses` silently
  drops the `text.format` json_schema and every extraction fails. Registering
  the model as **`openai/chat_completions/<model>`** forces LiteLLM's
  Responses→chat bridge, which converts it into a real `response_format:
  json_schema`. That prefix is the whole fix.
- **Graphiti will retire a fact it merely RECOGNISES, and the guard is not
  the model.** Upstream's `resolve_extracted_edges` offers a whole-group
  semantic search as invalidation candidates (empty `SearchFilters()`); we
  carry upstream #1729 under `deploy/pi/graphiti/patches/` (with #1666,
  reasoning-first dedupe — drop both when they merge). Before blaming
  extraction quality on the model, check the invalidation scope, the sampling
  (unset temperature means the backend default, not deterministic), and the
  field ORDER of the schema (with schema-constrained decoding, index arrays
  before a `reasoning` field means the model answers before it thinks).
- **The ontology needs somewhere for every category to go.** Graphiti's
  upstream default entity types had no `Person`, so every person landed as a
  bare `Entity` while orgs typed correctly. Declare high-priority types FIRST
  so they beat the "use as last resort" types.
- **Neo4j CrashLoops on its own Kubernetes Service name.** k8s injects
  service-discovery env vars and the Neo4j entrypoint turns every
  `NEO4J_`-prefixed var into a config setting. The fix is
  **`enableServiceLinks: false`** on the pod — NOT disabling strict
  validation, which silences the symptom and leaves junk settings parsed.
- **podman tags local builds `localhost/<name>`; Kubernetes looks up
  `docker.io/library/<name>`.** An image imported under the `localhost/` ref
  is invisible to the kubelet — ImagePullBackOff on that node only,
  discovered at failover. Always build and verify with the fully-qualified
  ref, and assert with `grep -qx`, never a substring.
- **`ctr images ls | grep -q` inverts under `set -o pipefail`.** `grep -q`
  exits at the first match, the producer takes SIGPIPE, and the pipeline
  reports FAILURE on success. Capture into a variable first, then grep.
- **Ledger invariants live in SQL, not Python.** Idempotent enrollment is
  `unique (message_id)` + `on conflict do nothing`; the atomic claim is
  `for update skip locked`. Don't reimplement either in application code —
  and don't mock them in tests (`tests/test_ledger.py` skips without a real
  Postgres by design).
- **Header-less email still needs a stable key:** `parse_email()` synthesises
  a Message-ID from the content hash, so the hand-fed path stays idempotent.
- **Dispatch is opt-in.** `CC_DISPATCH_ENABLED` defaults false — the drain
  loop should never start by surprise and quietly burn tokens.
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
- **A fold is a claim of coverage, not an outcome.** Folded siblings sit in
  `FOLD_PENDING` and only go terminal (`FOLDED`) when the covering proposal
  is approved; rejection releases them to `UNPROCESSED`. Never mark a folded
  email terminal at fold time — that's how you silently drop mail.
- **Agents take `deps`** (`runtime/deps.py`). Every `agent.run()` needs
  `deps=TriageDeps(...)`; resume paths pass a fresh one on purpose.
- **Run the suite SEQUENTIALLY.** conftest.py was built for xdist's
  per-worker DBs but they are broken in practice (missing per-worker
  databases, cross-worker FK races). Plain `pytest -q` is the gate; a
  mysteriously red suite is worth checking for `-n` before diagnosing
  anything else.
- **Tests must pin `demo_mode=True` if they hit code that calls
  `resolve_model()` itself** (e.g. `routes.feed_email`) — with a real key in
  `.env` the "offline" suite will otherwise call the model for real and spend
  money.
- **Assert on rows your test created, never on global counts.** The dev DB
  may be shared; `tests/conftest.py` parks foreign `UNPROCESSED` rows so
  queue-order tests are deterministic, but count assertions still need
  scoping.
- **Every email takes one path:** `dispatcher.process_claimed()`. Queued work
  arrives via `dispatch_once`, hand-fed work via `dispatch_item` — both claim
  from the ledger first. Don't add a route that calls `ingest_and_propose()`
  directly.
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
- **The cockpit believes push frames, not hope.** The chat spinner clears
  only on `agent/lifecycle` end frames and the panel updates on
  `cc.conversation.ended` / `cc.session.*` events. If you add a new
  turn/close path, push the frames, or the UI lies.
- **`master` is the only branch** — no `main`. A feature branch is a
  deliberate choice to raise with the operator, not a default.

## Repo layout

```
central_command/
  contract/   Proposal/Action/Evidence models + enums — the shared vocabulary (no deps)
  db/         schema.sql (spine) + repo.py (async persistence)
  runtime/    agent, tools (propose_*), packs (capability packs), templates (hire),
              roster (DB-backed; SEED fallback), durable (pause/resume), models, run,
              proposals (THE park path: persist + announce)
  ingest/     ledger (parse + enroll emails), dispatcher (pull loop + backpressure valves)
  heartbeat/  scheduler: actions (closed registry of operator levers), engine (tick loop + lease)
  sandbox/    runner.py — the CREDENTIAL-FREE sandbox service (own systemd unit;
              imports nothing from gateway/runtime/db by design)
  crawler/    service.py + Dockerfile — cc-crawler, the Crawl4AI/Chromium crawl
              service for JS-rendered docs; standalone like sandbox/
  events/     log.py — the durable append-only event log + live fan-out (SSE source)
  gateway/    gateway (approve/reject) + executor (performs writes) — the trust boundary
  integrations/  jira, n8n_facade, graphiti (MCP client; reads ungated, writes
              Executor-only), neo4j_reader/writer (curation path)
  api/        FastAPI app, routes, SSE events
web/          the forked Nerve cockpit (Vite + Hono). Central Command's own routes are
              the `cc-*` ones in server/routes; web/vendor-unused/ and web/docs/
              are upstream leftovers — see web/VENDORED.md before trusting either.
servers/      MCP servers AGENTS wrote — arrives only through an approved
              mcp.sync_source, never hand-edited into existence
fixtures/     emails/*.eml — the stand-in backlog corpus
scripts/      acceptance + inspection scripts; prepush-scan.sh (the pre-push
              sensitivity guard); vendor_docs_fetch.sh (builds docs/vendor/)
deploy/k3s/   the multi-node reference deployment: README.md runbook, manifests,
              setup.sh / verify.sh / backup.sh / cc-update.sh + systemd units
deploy/AIRGAP.md + airgap.env.example
              the map of every external source and its seam — read FIRST for
              any mirrored or no-egress install
deploy/single/ the single-node `podman kube play` profile: deterministic
              setup.sh driver, digest-pinned images.txt, bundle.sh (air-gap)
deploy/pi/    legacy compose stack — kept for its comments and as a rollback
              reference; home of graphiti/ + litellm/ configs and
              graphiti/patches/ (upstream #1729, #1666 — drop when merged)
tests/        contract, durable, jira_facade, ledger, eventlog, threading
docs/         DESIGN.md (design compendium + index of design records),
              QUEUE.md (operator's guide to the inbox queue),
              reference/ (sanitized work-environment compatibility profile),
              superpowers/ (the design-record trail: research/plans/specs),
              experiments/ (scripts reproducing findings the specs cite),
              vendor/ (offline doc mirrors for the air-gapped target — never
              edit; refetch via scripts/vendor_docs_fetch.sh)
requirements.lock
              the frozen, suite-tested Python resolution (air-gap
              reproducibility); regenerating it is a deliberate act
```
