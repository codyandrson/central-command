# CLAUDE.md — Central Command orientation

> Read this first. It orients a new session; **`docs/DESIGN.md`** is the full
> design; **`CHANGELOG.md`** is the public what-changed record. The live
> operational state (**STATUS.md**) and the development journal (**JOURNAL.md**)
> are instance documents in the operator's PRIVATE instance repo (checkout:
> `~/CentralCommand-instance/docs/`), not in this public tree. Read JOURNAL for
> *why* a rule exists, never for *what to do*, and never trust an operational
> recipe or a count from it.

## What Central Command is

A **human-supervised agentic-team framework**: a control plane over a team of
Claude agents where the operator (The operator) is team lead — tasking agents, approving
their actions, answering their questions, and coaching behavior over time.
**Nothing changes the world without passing an approval gate.** Single-operator,
self-hosted on a Raspberry Pi in the operator's homelab (Docker CE); the work deployment
target is air-gapped. Primary risk model is **error, not malice** (hallucination
/ misreads), so the guards are reliability guards that also happen to stop
misuse.

## Status: Phases 1–3 ✅ built & human-verified (M0–M16) · Phase 4 well underway

The full spine works end-to-end: **agent proposes → durable pause (survives a full
restart) → Decisions Inbox → operator approves → gateway → Executor performs the
real write → provenance stamped → agent resumes.** It runs on real Claude against
The operator's actual Gmail and a real Jira, and in **demo mode** (no API key, dry-run
writes) for safe dev.

Phase 4 is not a plan any more — the auditor is **graduated** (active on
`dismissal.confirm`), the coaching loop produces real charter versions, the D7
orchestrator runs multi-agent projects, the heartbeat schedules recurring team
work as governed data, capability packs and the roster are data rather than code,
and a skills library delivers curated subject knowledge on demand.

**For the precise live state — roster, charter versions, capability counts,
what's in the queue and what's waiting on the operator — read the instance repo's
`STATUS.md` (`~/CentralCommand-instance/docs/STATUS.md`).** It is short and
verified; this file deliberately does not duplicate it, because two copies of
the same numbers is how they drift. The story of how each piece landed,
including the incidents that produced the rules below, is the instance repo's
`JOURNAL.md`.

## Architecture — runtime is Pydantic AI 2.0 + Claude

Modular monolith, 4 tiers:
1. **Control Plane UI** — React (`web/`)
2. **Control Plane API & Services** — FastAPI (`central_command/api`, `central_command/gateway`)
3. **Agent Runtime** — Pydantic AI + Claude (`central_command/runtime`) — can **only propose + read**
4. **Stores** — Postgres (spine), Graphiti/Neo4j (knowledge graph, via MCP), n8n
   (email façade holding the Gmail OAuth; **D23**: Jira is a native client in
   `integrations/jira.py` — n8n only where it already solved a hard integration
   problem)

**The trust boundary is the import graph:** `runtime/` must never import
`gateway`/`executor`. Agents hold only `propose_*` tools; the credentialed Executor
performs writes *after* approval. Keep it that way.

## Where it runs — a two-node k3s cluster (2026-07-31)

**The deployment is `deploy/k3s/`, across `raspberrypi` (100.74.236.97, arm64,
k3s control plane) and `chromebox` (100.113.118.28, amd64, 8 cores / 15 GB,
agent), run FROM THIS CHECKOUT — `/home/codyslab/central-command` (the units,
`.venv`, root `.env`, `web/.env` and `deploy/pi/.env` all live here;
clean-installed 2026-08-29 by `./deploy/k3s/setup.sh`). The former checkout
(retired 2026-08-29 under a `.retired-20260829` suffix) — never run,
edit or install from it.** The Docker Compose stack in `deploy/pi/` is SUPERSEDED — kept for its
comments and as the rollback path, not run. `cc-uvicorn`/`cc-nerve` still run as
systemd units on the Pi, outside the cluster.

**Why the move happened — one measured cause, three symptoms.** LiteLLM's
bundled Prisma query engine leaks GBs per minute **on arm64** (1.47 → 4.82 GB in
24s). Uncapped it reached 10.96 GiB of the Pi's 15.6 GiB and took the host down
three times, k3s control plane included. The 6 GB `mem_limit` was a blast-radius
guard, never a fix. Running LiteLLM on the x86_64 chromebox takes it off the
architecture where the leak was measured.

- **THE PLACEMENT RULE — split by STATE, not by service.** Stateless compute
  FLOATS (preferred nodeAffinity → chromebox, 30s NoExecute tolerations so a
  dead node fails over in ~1 min rather than ~6): **`cc-litellm`, `graphiti`**.
  Stateful services are PINNED, because `local-path` stamps a *required*
  hostname nodeAffinity on every PV — a pod holding that PVC can never move, and
  shared storage is no way out ([Neo4j forbids NFS
  outright](https://neo4j.com/developer/kb/can-i-use-nfs-as-my-filesystem-or-datastore-storage/):
  no file locking → store corruption). Pi: `cc-postgres`, `cc-litellm-db`,
  `cc-litellm-redis`, `cc-n8n`, `cc-n8n-db`. Chromebox: `cc-neo4j`.
- **Databases on the Pi is what MAKES failover work.** LiteLLM is the memory hog
  *and* it is stateless — its state is in `cc-litellm-db` (182 MB) and redis.
  Float the hog, keep the small database on the Pi, and a chromebox outage
  reschedules LiteLLM onto the Pi where its database already is. Verified by
  drill 2026-07-31: cordon chromebox → pod moves to the Pi → `localhost:4000`
  serves real inference throughout.
- **Exposure: ServiceLB, not NodePort.** k3s's built-in Klipper runs a DaemonSet
  binding the REAL port on every node and forwarding to the pod wherever it is,
  so `localhost:4000` and `localhost:8000` work on the Pi unchanged. **`.env`
  needed no edits.** Pi-pinned services use `hostPort` on `hostIP: 127.0.0.1`
  (identical to the compose bindings, including **5442**). `cc-neo4j` is
  **ClusterIP only** — the app has zero direct Neo4j references and reaches the
  graph solely through Graphiti's MCP endpoint.
- **The Pi no longer stands alone — that premise is retired.** It was true of
  the compose stack; the cluster is deliberately two nodes now. What still holds
  is narrower: every `CC_*` endpoint in `.env` must resolve to LOOPBACK, because
  ServiceLB carries the floats. `deploy/k3s/verify.sh` guards exactly that — a
  hard-coded node address would work today and silently break failover.
- **systemd:** `deploy/k3s/cc-uvicorn.service` (`Requires=k3s.service`). Its
  `ExecStartPre` is a **readiness wait on 127.0.0.1:5442**, not `compose up -d`:
  Kubernetes is declarative and asynchronous, so there is nothing to "bring up",
  only something to wait for. **The old unit's `ExecStartPre=docker compose up
  -d` will resurrect the Docker stack and fight k3s for 5442** — install the
  k3s unit in the same change (bit us during the cutover, 2026-07-31).
- **Two locally-built architectures.** `cc-graphiti:1.0.2-anthropic` is built
  per-node by `deploy/k3s/build-graphiti-image.sh` — arm64 with docker on the
  Pi, amd64 **natively with podman** on the chromebox (no QEMU). Every other
  image is multi-arch upstream. See the image-reference gotcha below. **The
  `-anthropic` in that tag is a misnomer since 2026-08-01** — Graphiti's LLM is
  the local Qwen now. The tag is kept on purpose: renaming means rebuilding and
  re-importing on both nodes for zero gain, and a stale ref on one node is an
  ImagePullBackOff you only find at failover.
- **Cockpit:** https://raspberrypi.tail2ae84b.ts.net (tailscale serve → 3080);
  n8n UI at :8443 on the same host (tailnet-only, added 2026-08-01).
- **A merged change is not LIVE until its process restarts.** Backend (anything
  under `central_command/`): `sudo systemctl restart cc-uvicorn`. Cockpit (`web/`):
  `(cd web && npm run build)` then `sudo systemctl restart cc-nerve`. Bit us
  2026-08-01: a backend fix was "deployed" by rebuilding only the cockpit, and
  the operator saw stale behaviour until cc-uvicorn restarted.
- **Toolchain:** uv-managed CPython **3.12** (Debian 12 ships 3.11) and
  NodeSource **22** (Debian ships 18).
- **Scripts, all tracked:** `deploy/k3s/make-secrets.sh` (Secrets/ConfigMaps
  from `deploy/pi/.env` — imperative on purpose, so no base64'd secret ever
  lands in the repo), `build-graphiti-image.sh`, `migrate-from-docker.sh`
  (supports `DUMP_ONLY=1` to rehearse), `verify.sh` (32 assertions incl.
  placement, PVC binding, and both decryption keys), `cc-update.sh` +
  `cc-update.path`/`cc-update.service`/`cc-update-tmpfiles.conf` (the cockpit's
  one-click updater — see the update-scope note below).
- **`cc-update.sh` covers the full release surface since 2026-08-29** —
  code, schema, k8s manifests, unit files, and the three locally-built
  images. Images whose inputs changed are PREBUILT before anything stops
  (operator decision: builds are the long pole and need no downtime), each
  from a detached worktree of the target tag via the release's own
  `build-*-image.sh`, with the currently-running bytes preserved first by a
  containerd retag to `:pre-update-<stamp>` on every holding node — that
  retag is what makes image rollback possible on a stable tag. After the
  merge it applies changed `deploy/k3s/*.yaml` + installs changed unit
  files, and `rollout restart`s image-changed deployments (retag-in-place +
  `IfNotPresent` never repulls on its own). Rollback reverses exactly what
  the forward pass recorded: reset, retag back, re-apply checkpoint
  manifests, restart. Two known limits: `kubectl apply` does not PRUNE — a
  manifest the release deletes leaves its old resource running; and a
  changed `cc-update.service` only takes effect on the NEXT run. It remains
  a different design from `deploy/single/update.sh` (no `local`/`upstream`
  two-branch model; git-tag merge from origin, online-only on purpose).
- **Never change `LITELLM_SALT_KEY` or `N8N_ENCRYPTION_KEY`.** They encrypt the
  stored LiteLLM virtual keys and the Gmail OAuth credential respectively; a
  restore under a different key leaves the rows present but undecryptable.
- **Backups:** `deploy/k3s/backup.sh` nightly at 03:00 via `cc-backup.timer` —
  all four stores plus the decryption keys, to `/home/codyslab/cc-backups`
  (0600 in a 0700 dir, OUTSIDE the repo — never put dumps or keys in a tree
  that gets pushed). 14-day retention, pruned only for stores that succeeded.
  Neo4j's dump now runs in a helper pod ON THE CHROMEBOX (its PVC is RWO and
  node-pinned). Restore commands are at the bottom of the script — including
  the `chown -R 7474:7474` that is NOT optional after a root-owned load.

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

The guided demo quickstart that lived here was scrapped 2026-08-20 — it ended
at a "Feed test email" button that only existed in the pre-Nerve dashboard
(dead since the 2026-07-22 cockpit pivot). A comprehensive getting-started /
onboarding flow is planned future work. Queue operations — enrolling the
fixture backlog, stepping the dispatcher, reading the audit trail — are
`docs/QUEUE.md`'s job.

**On the deployment, `cc-postgres` is a pod pinned to the Pi**, publishing
`hostPort` on `127.0.0.1:5442`. Bring the whole stack up (or reconcile it)
with:

```bash
./deploy/k3s/make-secrets.sh                    # from deploy/pi/.env; idempotent
sudo k3s kubectl apply -f deploy/k3s/           # all manifests
sudo k3s kubectl -n central-command get pods -o wide
./deploy/k3s/verify.sh                          # 32 assertions incl. placement
```

For a standalone dev database elsewhere, the root `docker-compose.yml` still
starts the same container: `docker compose up -d postgres`. Either way it is
`cc-postgres` on **127.0.0.1:5442** with the schema auto-loaded, matching the
default `CC_DATABASE_URL`, so every recipe above applies unchanged.

**5442, never 5432.** The original reason (5432 was the desktop's
`nat-postgres`) is gone with that stack, but the port is baked into `.env`,
`.env.example`, the compose files, the k3s manifests and the `config.py`
default — changing it now buys nothing and breaks all five.

## Conventions & gotchas (bite marks)

- **Two run modes** (config): `CC_DEMO_MODE` (deterministic FunctionModel vs real
  Claude) and `CC_EXECUTOR_MODE` (`dry_run` logs writes vs `live` performs them).
  The one model seam is `runtime/models.py:resolve_model()`.
- **Proposal arg shape:** real Claude *flattens* the `Proposal` fields at the top
  level of tool-call args (Pydantic AI hoists a single-model tool param); the demo
  model nests them under `proposal`. Always parse via `durable.proposal_from_call()`
  — it tolerates both. (This bit us once in M5.)
- **A proposal's `agent_id` is the DRAFTER, never the subject.** Since stage 5a
  the coach drafts charter edits for other agents, so the two differ: the
  proposal row (and its session, its Inbox attribution, its throttle count)
  belongs to the agent that generated it, and the target lives in the action
  arguments where the Executor reads it. `executor.execute()` takes `proposer`
  from the gateway for exactly this reason — an actor an agent can write into
  its own args is an agent-authored claim about provenance. Existing
  charter-version rows were NOT rewritten: they are true of the sessions that
  produced them.
- **A fresh run must load the charter; `build_agent_for` does not.** It is the
  RESUME factory and passes no charter, so `build_hired_agent(charter="")`
  falls through `build_charter`'s `content or CHARTER` to the inbox-triage
  charter. Harmless on resume (the persisted history carries the original
  prompt) and wrong anywhere else — `run_task` and `run_coach` load the real
  one. If you add a third fresh-run path, load the charter.
- **`system_prompt=` is load-bearing — never "modernise" it to `instructions=`.**
  A system prompt is PERSISTED into the message history, so a session paused at
  a deferral resumes under the charter it was proposed under; `instructions=`
  is re-applied from the live agent at every run, so a resume would silently
  execute under whatever the charter says NOW — and on `build_agent_for` (the
  resume factory, which passes no charter) under the default inbox-triage
  charter. Rewriting the record of what an agent was told when it proposed is a
  governance change wearing an idiom cleanup's clothes.
- **Never swap the approval gate for pydantic-ai's `approval_required()`.** Its
  semantics are "pause, collect a yes, then run the tool IN THIS PROCESS" —
  which puts the write back in `runtime/`, the tier that may never hold
  credentials. `CallDeferred` + `propose_*` + the credentialed Executor is the
  shape on purpose: the runtime cannot perform the write even if it is approved
  by mistake. The framework feature is a UX affordance; ours is a trust
  boundary.
- **Secrets** live only in `.env` (gitignored): `CC_LLM_API_KEY`,
  `CC_ANTHROPIC_API_KEY`, `CC_JIRA_FACADE_TOKEN`. Never commit or print them.
- **Homelab reuse is OVER — the Pi owns its dependencies.** Central Command used to
  borrow the desktop's podman stacks (n8n / Graphiti / Neo4j under project
  `n8n-agentic-team`, plus a separate LiteLLM). Since the 2026-07-26 move it
  runs its own copies on the Pi, so it survives the homelab being off. The
  `lib-*` provider workflows and the two `cc-*` façades stay active inside the
  Pi's n8n; the old s0–s3 pipeline workflows came along dormant and stay that
  way. Don't reintroduce a dependency on a desktop hostname —
  `deploy/k3s/verify.sh` fails the build if one appears in the env files.
- **A fresh database must reproduce the roster.** `schema.sql`'s founding-roster
  UPDATEs are guarded by `where … and role = ''`, so the INSERT above them must
  NOT seed `role` — it did until 2026-07-26, which meant every fresh database
  booted with `taskable = consultable = false` for the whole roster (invisible
  on the desktop, whose rows predate the guard; 24 tests failed the first time
  the suite met a clean database, on the Pi). If you add a seeded column, ask
  what a brand-new database does, not what the dev box does.
- **`schema.sql` is auto-loaded on a FRESH database only — nothing applies it at
  runtime.** The test DB is fine (`tests/conftest.py` re-executes it into
  `central_command_test` every run), so a new column passes the whole suite and then
  breaks the live Pi with an undefined-column error that reads like a code bug.
  Apply additive columns to the running `cc-postgres` by hand in the same change
  that adds them to the file (they are `add column if not exists`, so it is
  idempotent). Found 2026-07-27 adding the tier-3 stall columns.
- **The agent sandbox is CONTAINMENT, not restriction** (The operator, 2026-08-04).
  It exists so an agent can work freely and unsupervised without risking his
  system — NOT to limit what it can reach. Egress is open: the internet AND
  his internal services, because a task may be "integrate with n8n". The gate
  is the CAPABILITY, never reachability — every tool that CHANGES a service is
  a gated `propose_*`. What actually constrains a sandbox is that it holds NO
  credentials; the corollary is that provisioning one converts reach into
  ungated writes, so that is an explicit operator decision. Proven by the
  inverse: with egress denied, the agent wrote against stale training data
  (mcp 1.x's `FastMCP`) and shipped a crash; with egress open it installed the
  library, introspected the real API, and tested the server before proposing.
- **The sandbox's only exit is `mcp.sync_source`, and content is captured at
  PROPOSE time.** The tool reads the files out of the sandbox and embeds them
  in the proposal; the Executor writes exactly those bytes and never re-reads
  the sandbox — an agent that could swap content after approval would make
  review theater. `_mcp_servers_root()` must resolve to the SAME path in
  `runtime/tools.py` and `gateway/executor.py` (it did not, 2026-08-04: the
  operator reviewed a diff against a directory the write never touched;
  `test_servers_root_is_the_same_path_on_both_sides` pins it).
- **LiteLLM forbids `-` in MCP server names; Kubernetes requires it.** The
  same server cannot use one name on both sides, so `mcp_server.id` stays the
  k8s-valid canonical id and the proxy sees `_litellm_server_name()`'s
  underscored form. Translated in exactly two places — the Executor
  (registration, discovery, tool calls) and `runtime/packs.py`'s toolset URL,
  duplicated because runtime may never import gateway. Also: the proxy's
  `/mcp-rest/tools/call` needs `server_id` in the BODY.
- **A proposal has THREE verdicts now.** Approve executes; Reject means "fix
  it and try again" and RESUMES the drafter, which redrafts; **Dismiss** means
  no action needed — WITHDRAWN, folds released, session closed, and the agent
  is NEVER resumed (a test makes the agent factory raise if any path tries).
  Rejecting to make something go away is the trap Dismiss exists to end
  (found live 2026-08-04: three "close this, don't ask again" rejections, three
  redrafts). Nothing feeds coaching automatically — everything on the record is
  citable and the operator curates signals case by case.
- **A Graphiti entity and edge each store their identity TWICE, and a hand-made
  write must set both halves.** An entity's types live in its real Neo4j labels
  AND in an `n.labels` property; an edge's endpoints live in the relationship
  AND in `source_node_uuid` / `target_node_uuid` properties — and the cockpit
  draws from the PROPERTIES, so a repoint done in Cypher alone leaves the panel
  rendering the old picture while Cypher reports the new one. Neo4j cannot move
  a relationship's endpoints in place either, so a repoint is copy-then-delete
  (`integrations/neo4j_writer.py`, the operator curation path). Third half-write
  in the same family: **a node with no `name_embedding` is invisible to the
  semantic half of hybrid search** while still turning up in keyword hits, so an
  operator's correction reads fine in the UI and no agent ever recalls it —
  every write re-embeds through `qwen3-embedding-local` at exactly 1024
  dimensions (a mis-sized vector corrupts the index instead of erroring, so the
  writer drops one rather than store it). Neo4j 5.26 has no parameterized
  labels, hence the closed `ENTITY_TYPES` allowlist mirroring
  `graphiti/config.yaml`.
- **The suite may not write to the live graph over bolt either.** The
  `no_live_graph_writes` guard was written for the MCP transport (`add_memory`)
  and the curation path does not use MCP at all, so it needed its own refusal
  at `neo4j_writer._write`. Opt in with `CC_LIVE_GRAPH_TESTS=1` when you mean
  it. This is not fussiness: one leftover scratch entity — no edges, its own
  group — was enough to push `search_facts(max_facts=3)` to four results and
  fail an unrelated live read test (2026-08-15). And **an async bolt driver
  cached at module scope is loop-bound** exactly like a module-scope
  `asyncio.Event`; `neo4j_reader._get_driver` keys its cache on the running
  loop, and the writer shares that one driver because access mode is a property
  of the SESSION, never of the driver.
- **A gadget's config keys are declared BY THE GADGET — read its XML, never a
  table.** Every classic Jira gadget declares `isConfigured` with
  `default_value="false"`, and that pref IS the "hasn't been configured yet"
  placeholder; nothing else sets it. Only Filter Results declares `filterId` —
  Pie Chart, Created vs Resolved and Stats bind under `projectOrFilterId`. A
  gadget **silently ignores any pref it does not declare**, so a wrong key is a
  200 with no binding and no error. `_prepare_gadget_configs` fetches each
  gadget's own `<UserPref>` list and refuses undeclared keys; that same fetch is
  the URI check, because a real gadget's declaration always resolves and an
  invented URI's does not (invented URIs 400 one at a time and leave the
  dashboard standing EMPTY, reading EXECUTED). Found 2026-08-10 — and the wrong
  key came from OUR OWN skill doc, so the agent had followed its documentation
  faithfully. Check what the docs say before coaching an agent for obeying them.
- **`@container` must sit on a PARENT of whatever uses `@3xl:`.** A container
  query resolves against an ANCESTOR container, never the element's own — so
  `@container … flex-col @3xl:flex-row` on ONE div silently never applies the
  row, while the CHILDREN's `@3xl:` variants apply normally. That asymmetry is
  the trap: the sidebar takes its `@3xl:w-[340px]` and stays STACKED, overflows
  the column, and `flex-1 min-h-0` squeezes the sibling pane to height 0 below
  the fold — a blank panel with no error anywhere. It degrades gracefully while
  the list is short, so it hides until the list grows (2026-08-10: all three of
  Agents/Skills/Decisions carried it; only Agents was long enough to show it).
  **jsdom has no layout engine, so no render test can catch this class** —
  `web/src/features/container-query-scope.test.ts` reads the SOURCE instead.
- **`tsc` does not delete removed sources from `server-dist`.** Delete a
  `server/` module and its compiled `.js` stays behind, so the old module ships
  on after a rebuild. Remove it by hand in the same change (2026-08-10:
  `gateway-client.js` survived its own deletion).
- **An attachment reaches the agent as content, or the SEND fails — there is
  no third state** (`api/attachments.py`, 2026-08-11). Measured against the
  real proxy on `cc-default` → the then-current qwen3-coder-next: an Anthropic `image` block
  returns HTTP 500 (`image input is not supported … you may need to provide
  the mmproj`), and a `document` block is **SILENTLY DROPPED** — 200 OK, and
  the model answers "I don't see any file attached". LiteLLM translates both
  faithfully, so the transport is fine; the text-only model discards the
  document and answers anyway. That is why nothing sends native multimodal
  blocks: attachments are extracted to text at the GATEWAY, which is
  model-agnostic. Two rules fall out. **The empty-output check is the load
  bearing one:** markitdown converts an image-only PDF — i.e. a SCAN —
  *without raising* and returns zero characters, so `try: convert() except:
  refuse` ships the exact silent failure the design exists to prevent; check
  the OUTPUT, never the exception and never the format registry (markitdown
  registers an `Image` converter that yields `''`). And **validate before the
  turn exists**: a refusal must leave no transcript row and no lifecycle frame
  owed to the spinner (see the 2026-07-23 incident), which is also what lets
  the operator keep their draft and fix the cause. Capability is DECLARED, not
  guessed — `litellm.model_accepts_images` reads `model_info.supports_vision`
  through the agents' own key, and `None` means "nobody said", never "yes".
  **Updated 2026-08-19/20:** `cc-default` now declares `supports_vision: true`,
  and a scan/image-only PDF no longer flat-refuses — `_ocr_scanned_pdf`
  (capped at `MAX_OCR_PAGES`) renders pages through the vision model and the
  empty-output check now guards the OCR RESULT (`if not ocr: raise`). The
  invariant is unchanged — content or refusal, no silent third state — only
  the path moved. Raw image FILES are still refused as attachments. And the
  size cap only refuses cleanly because the transport is wider than it:
  `MAX_ATTACHMENT_BYTES` (50 MB) needs uvicorn's `--ws-max-size` (96 MB in
  cc-uvicorn.service) above its base64-inflated frame, else the socket dies
  1009 before the check runs (found 2026-08-20).
- **A `failed=True` session must record WHY** (`land_session(..., reason=…)`).
  `session.failed` carried only `agent_id` until 2026-08-10, so an agent that
  died mid-turn left the operator nothing to diagnose from. Guarded by a source
  walk (`tests/test_session_failure_reason.py`), NOT a runtime raise: every one
  of those call sites is already handling an exception, so raising would mask
  the original error and leave the session row open — worse than the bug.
- **A failure landing that lives in ONE wrapper is not a landing.** The
  try/except that resolves a dead task FAILED (or retry-parks it) sat only in
  `routes._run_assigned_task_detached`, so the SYNCHRONOUS entries —
  `create_and_run_task(background=False)`, which is the heartbeat's `ea.contact`
  path, and assign-and-run — raised straight past the task record and left it
  IN_PROGRESS **forever**: a 429 (07-31) and a provider timeout (08-02) both
  stuck that way, transient errors the retry machinery would have re-run had
  anything asked it. The guard is in `_run_assigned_task` now and the raw body
  is `_run_assigned_task_inner`, so a new caller gets it by default. The one
  deliberate `_inner` caller is `orchestration.retry_parked_task`, whose own
  handler is attempt-AWARE — parking underneath it would reset `attempts` to 1
  each round and never reach the exhaustion promotion. Every pre-existing test
  in `test_outage_requeue.py` drove the DETACHED wrapper, which is exactly why
  the gap survived a suite written for this failure class: **when handling lives
  in a wrapper, test the entry, not the wrapper.**
- **A shutdown hook in the FastAPI lifespan runs too late to release a
  connection.** Uvicorn's order (`server.py`) is `wait_for(connections,
  timeout=timeout_graceful_shutdown)` and only THEN `lifespan.shutdown()` — so
  anything that ends a long-lived response must fire from the SIGTERM handler,
  not the lifespan. Our SSE stream and cockpit WebSocket follow the event log
  forever by construction and `timeout_graceful_shutdown` defaults to **None
  (wait forever)**, so every restart hung until systemd SIGKILLed it. Putting
  `events.log.close()` in the lifespan looked right and measured identically to
  having no fix at all (2026-08-15: 120s, then killed) — the close cannot
  release the connection that is blocking the wait for it. `api/app.py` wraps
  the existing handler (uvicorn installs `Server.handle_exit` via
  `signal.signal` before startup, so it is there to chain to);
  `--timeout-graceful-shutdown` is the backstop that should never fire. Verified
  by attaching a real SSE client across a restart: `Waiting for connections to
  close` stops appearing at all.
- **`asyncio.Event` at module scope is loop-bound and will bite in tests.** It
  carries `_LoopBoundMixin` — it binds to the first running loop that awaits it
  and raises `RuntimeError` from every other one. One process/one loop hides
  this in production and pytest's per-test loops expose it. The first cut of the
  event-log close raced `Event.wait()` against `queue.get()` and returned
  end-of-stream when the getter lost, which meant that `RuntimeError` was
  **silently served as a shutdown signal**. Prefer a plain flag plus a queue
  sentinel (`events/log.py`), and never let "the other future won" stand in for
  "no exception happened".
- **A resume must ARM its park record BEFORE it runs, not only in the `except`.**
  Every decision path (approve / fail / reject / answer) already builds a
  `pending_resume` for the transient-failure park and then holds it in a LOCAL
  VARIABLE — which a SIGKILL takes with it. What is left is a session reading
  `AWAITING_HUMAN` over an already-ANSWERED question or already-EXECUTED
  proposal: no OPEN Inbox item, no parked status, on nobody's worklist,
  permanent. Twelve triage sessions died that way on 2026-08-15 when
  `TimeoutStopSec=15` turned a shutdown into a SIGKILL mid-batch (and once
  before, 2026-08-13, via a cancelled RPC — `CancelledError` is a
  `BaseException` and sails past `except Exception` identically). The fix is
  `resume_park.arm()` at every site plus `resume_sweep`'s fifth worklist; a
  longer stop timeout only shrinks the window. **The `tool_call_id` match is
  load-bearing, not a detail:** `run_state` is a key-level MERGE, so a session
  that resumed and then RE-PARKED (a redraft) still carries the old marker, and
  driving it would hand the redraft's deferred call the PREVIOUS decision's
  outcome. Re-driving is correct here even though the sibling sweep FAILS what a
  restart interrupted — there the operator never re-issued the work; here they
  answered or approved, and the owed turn is theirs to lose.
- **A proposal's argument SHAPE is checked in BOTH tiers from ONE list —
  `contract.ARG_SPECS`.** The runtime hands a malformed draft back to the
  model (`tools._validate_proposal`, ModelRetry with every problem at once)
  so only a well-formed proposal reaches the Inbox, and `executor.execute()`
  runs the same `validate_action_args` over every action before the first
  runs. Found live 2026-08-29: one `graph.add_episode` intent cost the
  operator FOUR approvals because the Executor was the first thing to look
  at the args and found one per round. Adding a capability whose handler
  subscripts `args[...]` means adding its spec — a bare KeyError reaching the
  log as `'name'` is the smell. Only SHAPE goes in the spec (required keys,
  closed enums); world-state checks (a uuid exists, a project exists) stay
  Executor-only because the world can change between propose and approve.
  Every in-run rejection is a `proposal.rejected_in_run` event — the
  operator is spared the re-approval, never the knowledge. And every
  `propose_*` tool carries `max_retries=10`: pydantic-ai's default of 1 means
  the SECOND bad draft fails the session (bit us 2026-08-18 on bulk-dismiss).
- **A "never guess X" docstring needs a tool that can READ X.** Agents held read
  tools for fields, filters, dashboards, gadgets and transitions — and none for
  Jira PROJECTS, the one argument every `create_issue` requires, so they invented
  plausible keys (PERSONAL, SUPPORT, CS, HOME, TSC, SUPP) against an instance
  holding exactly T1 and TASKS. Ten failed proposals. Coaching did not help and
  could not: the agent escalated correctly to `jira-expert`, which had no list
  either and answered from issue searches ("I can only confirm TASKS"), missing
  T1. Before coaching an agent for guessing, check whether anything in the system
  can actually answer the question. `jira_list_projects` landed 2026-08-15 —
  with NO propose-time reject, deliberately: this failure is LOUD (400, FAILED,
  redraft), unlike the gadget-pref case whose guard exists because a wrong key
  returns 200 and binds nothing. Don't build a guard for a failure you have not
  observed under the fixed conditions.
- **The orphan sweep must land the TASK, not just the session.**
  `repo.fail_stale_running_sessions` flipped a dead process's RUNNING session to
  FAILED and left its task reading IN_PROGRESS against a FAILED transcript
  (08-01, a restart mid-run). It is the only thing that CAN land it — the park
  that would have recorded the death is written in an `except` block a dead
  process never reaches. FAILED, never retry-parked: a restart is not a
  dependency outage, and silently re-running whatever was in flight at shutdown
  spends tokens on work the operator never re-issued.
- **The cockpit hand-declares its own interfaces over untyped RPC payloads, so a
  field the gateway never sends is invisible to `tsc` AND to every frontend
  test.** The feature just silently never renders. Twice in one session
  (2026-07-27): a hook reading `stalled_at`/`task_title` off `decisions.list`,
  and a divider reading `closedReason` off the composer's session block, which
  carried id/mode/status/agentId and nothing else. The defence is a BACKEND test
  asserting the wire shape (`test_composer_state_carries_closed_reason_to_the_cockpit`)
  — a frontend test building its own payload object proves nothing.
- **`nerve_gateway._COMPOSER_DISABLING` is an ALLOWLIST, not a check.** A new
  `why_not_sendable` refusal code that is not in that tuple leaves the cockpit
  rendering an ENABLED message box that `_chat_send` then 409s — the exact
  composer/send drift `_send_target` was written to end. Adding a refusal code
  means adding it there too, with a guard test. (2026-07-27: a plan asserted the
  composer "needs no work because it reads `why_not_sendable`". It does not.)
- **Graphiti's extraction needs the BRIDGED LiteLLM alias, not `cc-default`.**
  graphiti_core's `OpenAIClient` drives extraction through the **Responses API**
  (`client.responses.parse`) whenever a `response_model` is present — always,
  for extraction — with no chat-completions fallback. llama.cpp does not
  implement `/v1/responses`, and LiteLLM treats `openai/…` as *natively*
  supporting it, so it passes the request through and the `text.format`
  json_schema is **silently dropped** — the model answers with prose and every
  extraction fails. Registering the model as
  **`openai/chat_completions/<model>`** forces LiteLLM's Responses→chat
  bridge, which converts `text.format` into a real `response_format:
  json_schema` that llama.cpp enforces via GBNF. That prefix is the whole fix —
  no image patch. (Upstream says the same: an OpenAI-compatible endpoint
  "does not support the /v1/responses endpoint that OpenAIClient uses".)
- **Graphiti will retire a fact it merely RECOGNISES, and the guard is not the
  model.** `resolve_extracted_edges` scopes its DUPLICATE search to edges
  between the same two nodes, then passes an **empty `SearchFilters()`** for
  invalidation candidates — a whole-group semantic search whose every hit is
  offered to the LLM as fair game to retire. On 2026-08-15 the episode "Jane
  Anne Doe is the mother of the operator" invalidated the operator's maternal
  grandfather AND maternal aunt along with it. Still unfiltered in upstream
  0.29.3, so **an upgrade does not fix it**; we carry upstream #1729 under
  `deploy/pi/graphiti/patches/`. Two more things stacked on it, and all three
  had to be true: **temperature was never pinned** (unset means the param is
  OMITTED, so llama.cpp's default temp 1.0 / top_k 40 applies — "unset" is not
  "deterministic", and the `anthropic_client.py` patch the old comment credited
  left this code path on 2026-08-01); and **`EdgeDuplicate` asked for its index
  arrays before any reasoning field**, which with schema-constrained decoding
  means the model answers before it thinks (upstream #1666 measured 7/15 vs
  14/15 with `reasoning` declared first — and `factories.py` hardcodes
  `small_model = config.model`, so the "cheap judge" path is our only model).
  Before blaming extraction quality on the model, check the scope, the
  sampling, and the field ORDER of the schema.
- **One llama.cpp server answers BOTH `cc-default` and `graphiti-llm`.** They
  are separate LiteLLM aliases onto the same `:8081` deployment, so a model
  chosen for one silently becomes the model for the other — which is how a
  CODING model came to run knowledge-graph entity resolution for three weeks.
  Changing it is a fleet-wide change; benchmark it against BOTH workloads.
  Two measured constraints when picking one: **context is set by the KV cache,
  not the model card** — and the per-token cost is an ARCHITECTURE fact, not a
  size fact: the old 131072 B/token figure here was all-64-layers math that was
  true of Qwen3.6 and is WRONG for 3.8, where only 16 of 64 layers are full
  attention (the 48 Gated-DeltaNet layers hold fixed-size state), so KV at
  q8_0 costs ~32 KB/token. Measured 2026-08-19, then RESHAPED the same day:
  with the embedder on the GPU, 49152 was the ceiling beside the reranker —
  but the embedder moved to CPU (100ms/query, trivial at its volume), freeing
  2.65 GB, so the 27B now runs at **81920** beside the resident GPU reranker
  (~1.9 GB free). The :8081 slot is **llama-swap** (`cc-swap` container on the
  workstation, config `~/QWEN/llama-swap.yaml`): one big model at a time,
  swapped on demand by the request's `model` field with requests QUEUED
  through the ~10s swap — `qwen3.8-27b` (the default), `gpt-oss-20b`
  (131072 ctx) and `gemma-4-31b` (65536 ctx, its KV ceiling) — the latter two
  are the graph-judgment second opinions (decorrelated lineages), ttl'd back
  out. FIFO with big timeouts is the operator-decided queue policy: a swap
  request waits behind the whole drain; never add quiesce choreography.
  `switch-model.sh` is the rollback path and refuses while cc-swap runs.
- **The ontology had no `Person`.** Graphiti's upstream default entity types
  cover Organization, Event, Document, Location and five more, and nothing for
  a human being — so every person landed as a bare `Entity` while orgs typed
  correctly. Added 2026-08-15, declared FIRST so it beats the "use as last
  resort" types. If extraction seems to be ignoring a whole category of thing,
  check whether the ontology gives it somewhere to go.
- **Neo4j CrashLoops on its own Kubernetes Service name.** k8s injects
  service-discovery env vars for every Service in the namespace
  (`NEO4J_PORT_7687_TCP_PORT`, …), and the Neo4j entrypoint turns EVERY
  `NEO4J_`-prefixed var into a config setting — producing `PORT.7687.TCP.PORT`
  and `Failed to read config: Unrecognized setting`. The fix is
  **`enableServiceLinks: false`** on the pod (it uses DNS and needs no links),
  NOT `server.config.strict_validation.enabled=false`, which silences the
  symptom and leaves junk settings parsed. Hit on the first cutover (2026-07-31).
- **podman tags local builds `localhost/<name>`; Kubernetes looks up
  `docker.io/library/<name>`.** A podman-built image imported into containerd
  under the `localhost/` ref is invisible to the kubelet, which then tries to
  pull a tag that exists in no registry — `ImagePullBackOff` on that node ONLY,
  i.e. discovered at failover rather than at deploy. Always build and verify
  with the fully-qualified ref, and assert with `grep -qx`, never a substring.
- **`ctr images ls | grep -q` inverts under `set -o pipefail`.** `grep -q` exits
  at the first match, `ctr` takes SIGPIPE, and the pipeline reports FAILURE on
  success. Capture into a variable first, then grep. This silently broke
  `migrate-from-docker.sh`'s own preflight with the image demonstrably present.
- **A floating pod needs its image on BOTH nodes.** `imagePullPolicy` must be
  `IfNotPresent` for locally-built images — `Always` CrashLoops on a tag no
  registry serves. `verify.sh` asserts presence on both nodes for this reason:
  a missing image is invisible until the day the pod actually moves.
- **Ledger invariants live in SQL, not Python.** Idempotent enrollment is
  `unique (message_id)` + `on conflict do nothing`; the atomic claim is
  `for update skip locked`. Don't reimplement either in application code — and
  don't mock them in tests, or you're testing nothing (`tests/test_ledger.py`
  skips without a real Postgres by design).
- **Header-less email still needs a stable key:** `parse_email()` synthesises a
  Message-ID from the content hash, so the hand-fed path stays idempotent too.
- **Dispatch is opt-in.** `CC_DISPATCH_ENABLED` defaults false — the drain loop
  should never start by surprise and quietly burn tokens.
- **The event log is append-only, so write order is read order forever.** Emit an
  event *before* the thing it authorises, not after — `proposal.decided` is
  written inside `gateway.approve_and_execute` before `executor.execute()`, never
  from an API route. (An earlier version logged execution before its own
  approval. `test_decision_is_logged_before_execution` guards it.)
- **…but a record that authorises nothing needn't be written at all.** A
  scheduled action that DID nothing writes nothing (2026-07-25): 5-minute mail
  polls were adding ~576 rows/day of "nothing happened" and drowning the
  governance screens. A heartbeat action may declare `ActionSpec.material` only
  if it is *non-authorising* (starts no gated work) AND *self-recording* (its
  effects emit their own events) — an explicit, parity-tested allowlist
  (`feed.poll`, `dispatch.window`). Errors are always material, everything else
  stays loud, and cadence liveness lives in `last_fired_at` (current state), not
  in history. Don't quiet an action that authorises work.
- **A fold is a claim of coverage, not an outcome.** Folded siblings sit in
  `FOLD_PENDING` and only go terminal (`FOLDED`) when the covering proposal is
  approved; rejection releases them to `UNPROCESSED`. Never mark a folded email
  terminal at fold time — that's how you silently drop mail.
- **Agents take `deps` now** (`runtime/deps.py`). Every `agent.run()` needs
  `deps=TriageDeps(...)`; resume paths pass a fresh one on purpose.
- **Run the suite SEQUENTIALLY — xdist's per-worker DBs are broken in
  practice.** conftest.py was built for `-n auto` (each worker gets
  `central_command_test_gwN`), but a 2026-08-21 run under xdist produced
  `database "central_command_test_gwN" does not exist` plus cross-worker FK races
  on a shared `skill` row — 10 failures that vanish single-worker. Plain
  `pytest -q` (~11 min on the Pi, measured 2026-08-23) is the gate; a
  mysteriously red suite is worth
  checking for `-n` before diagnosing anything else. Root-causing the xdist
  path is unowned work — do it deliberately or not at all.
  (monkeypatch), or they pass once and then fail as `AWAITING_HUMAN` proposals
  accumulate past the default of 3. This bit us in M9.
- **Tests must also pin `demo_mode=True` if they hit code that calls
  `resolve_model()` itself** (e.g. `routes.feed_email`). With a real key in
  `.env` the "offline" suite will otherwise call Claude for real and spend money.
- **Assert on rows your test created, never on global counts.** The dev DB is
  shared; `tests/conftest.py` parks foreign `UNPROCESSED` rows so queue-order
  tests are deterministic, but count assertions still need scoping.
- **Every email takes one path:** `dispatcher.process_claimed()`. Queued work
  arrives via `dispatch_once`, hand-fed work via `dispatch_item` — both claim
  from the ledger first. Don't add a route that calls `ingest_and_propose()`
  directly; that's the gap M9 closed.
- **Every proposal takes one path too:** `runtime/proposals.park_proposal()` —
  it pauses the run at its deferred call, persists the proposal, AND emits
  `proposal.created`. The emit used to live at the five call sites, so the two
  added later (a coach run, an operator task) forgot it and their proposals
  reached the Decisions Inbox with **no creation event at all** (2026-07-25).
  `tests/test_proposal_created.py` walks the source: a `repo.save_proposal(...)`
  anywhere but the seam fails the suite, in the same commit that adds it. Note
  this emit lands AFTER the save on purpose — see the next rule.
- **`events.emit()` is the only way to publish.** It writes to Postgres then fans
  out; the SSE stream is a projection of the table, never a parallel channel.
  Don't re-add an in-memory-only publish path — events would vanish on restart.
  **The emit-before rule is about *authorisation*, not bookkeeping:** emit
  before the thing an event authorises (`proposal.decided` before
  `executor.execute()`). A proposal authorises nothing — its *decision* does —
  so `proposal.created` is written after its save and that is not a violation.
- **No agent claim is trusted unverified — on every path that makes one.**
  `contract.claim_supported()` re-checks a quoted operator claim against the
  recorded source. It lives in `contract/`, not `gateway/`, because BOTH tiers
  need it and `runtime/` may never import `gateway/`: the gateway checks a
  redraft against the rejection feedback, the runtime checks a coached charter
  against the operator's curated signals. On the coach path the agent supplies
  the POINTER as well as the quote, so citing a signal the session was never
  fed is flagged exactly like a misquote. **`claim_matches_source: None` means
  NOT CHECKED** — never "checked and inconclusive"; it read `None` on every
  coach proposal until 2026-07-25 while the prompt promised a re-check. Don't
  fork the checker, and don't backfill the flag onto decided proposals — that
  would falsify the record.
- **Roster membership = a non-empty `role` column** (set only by schema seeds
  and `repo.hire_agent`). Plain `upsert_agent` registrations never join the
  roster — don't "fix" an agent's absence by upserting; hire it.
- **Grant revocation is a timestamp, never a delete.** `agent_grant` rows keep
  history so the idempotent schema seeds can't resurrect a revoked pack. And
  an agent's charter capability list is GENERATED from grants at run start —
  never write capability lists into charter text again.
- **Sessions are never destroyed.** No reset/clear/delete — closing a
  conversation is a status flip (`converse.end_conversation`, guarded: refuses
  with a pending proposal or mid-turn) with the transcript kept forever (M15).
  The cockpit's vendored reset/delete levers map to close-with-history; the
  panel hides terminal rows behind a History toggle (display only). The
  `agent:<id>:main` alias means "the CURRENT lane": the newest conversation
  IF it is open, and NONE when that newest lane is terminal — it deliberately
  does NOT fall back to an older still-open session — chat.history,
  chat.send routing and the close levers must stay consistent on that rule
  (`nerve_gateway._latest_conversation(open_only=True)` is the one seam).
  Multiple concurrent sessions per agent is the TARGET architecture
  (`sessions.new` opens parallel lanes); never add code assuming one session
  per agent.
- **The cockpit believes push frames, not hope.** The chat spinner clears only
  on `agent/lifecycle` end frames (the adapter pushes them around every turn —
  including on error), and the panel updates on `cc.conversation.ended` /
  `cc.session.*` events. If you add a new turn/close path, push the frames, or
  the UI lies ("still thinking" / stale rows — 2026-07-23, five rounds of it).
- **Two repos, routed by CONTENT.** Ask: *would another Central Command
  deployment need this?*
  - **Yes → PUBLIC, this repo:** `origin git@github.com:codyandrson/central-command`
    (SSH — the Pi's `id_ed25519` authenticates; there is no GitHub HTTPS
    credential on this host), branch `master`, no `main`. Core product work:
    features, fixes, drivers, manifests, unit files, docs that apply to any
    install. Never instance facts.
  - **No → PRIVATE, the instance repo:** `codyandrson/CentralCommand`,
    checkout `~/CentralCommand-instance`, its own commits and pushes. Everything
    specific to THIS deployment: `docs/STATUS.md` (live state),
    `docs/JOURNAL.md` (incidents and why), go-live and instance-data
    decisions, operator notes. A session that changes the deployment updates
    STATUS/JOURNAL there in the same session.
  **A change is not done until it is pushed**: `git push origin master`
  in the same session you commit it, and leave `git status -sb` reading
  `## master...origin/master` with no "ahead". STATUS.md carried "NOT yet
  committed to git" across three separate sessions — that is the failure mode
  this rule exists to stop, and a stale claim in the doc is worth checking
  against `git log` rather than trusting.
- **Never stage with `git add -A` or `git add .` — stage explicit paths.**
  Concurrent Claude sessions share this checkout, so a blanket add sweeps
  another session's in-progress files into your commit under your message
  (2026-08-17: an auditor commit, c8e15b9, silently absorbed most of the
  tool-visibility phase-2 work). Stage only the paths this session changed,
  and check `git status` for unexpected files before committing.
- **Nothing sensitive or personal EVER reaches the public repo — enforced,
  not hoped.** Three classes, all forbidden in tracked files: (1) SECRETS —
  API keys, tokens, passwords, private keys, kubeconfigs, virtual keys;
  they live only in gitignored `.env` files (`git check-ignore -v .env`).
  (2) THE OPERATOR — their name, handles, e-mail, Atlassian/Gmail account
  URLs, family, contacts, health, finances, real mail/Jira/Confluence
  content, and any interview or graph fact about them. Prose says "the
  operator"; examples use the placeholder cast (the Doe/Rivers cast,
  "Example Bank", "Initech", `example.com`). (3) SECURITY-RELEVANT internals
  beyond topology — anything that lowers the cost of attacking the instance.
  Homelab TOPOLOGY stays tracked by decision (tailnet-only IPs/hostnames,
  unix usernames, paths, the cockpit URL): setup/deploy reads it, and it is
  unreachable from outside the tailnet. `scripts/prepush-scan.sh` runs as
  the pre-push hook (`.git/hooks/pre-push`) and REFUSES a push whose added
  lines match secret shapes or the identifier list kept OUTSIDE the repo at
  `~/.cc-private-identifiers` — never weaken it to get a push through; move
  the content to the instance repo instead. Backlog scrubbed 2026-08-29
  (the operator's name and handle were tracked until then).
- **`master` is the only branch**, locally and on origin (the five historical
  `phase2-*` / feature branches were deleted 2026-07-25 — four fully merged,
  and `close-emails-gap` superseded: master's `feed_email` already routes
  through the ledger, that being what M9 landed independently). Work on
  `master`; if a branch is ever warranted, it is a deliberate choice to raise
  with the operator, not a default.

## Repo layout

```
central_command/
  contract/   Proposal/Action/Evidence models + enums — the shared vocabulary (no deps)
  db/         schema.sql (spine) + repo.py (async persistence)
  runtime/    agent, tools (propose_*), packs (D25 capability packs), templates (D24 hire),
              roster (DB-backed; SEED fallback), durable (pause/resume), models, run, spike_model,
              proposals (THE park path: persist + announce — every proposal goes through it)
  ingest/     ledger (parse + enroll emails), dispatcher (pull loop + backpressure valves)
  heartbeat/  D27 scheduler: actions (closed registry of operator levers), engine (tick loop + lease)
  sandbox/    runner.py — the CREDENTIAL-FREE sandbox service (its own systemd
              unit, drives k8s by shelling to kubectl with a namespace-scoped
              kubeconfig; imports nothing from gateway/runtime/db by design)
  crawler/    service.py + Dockerfile — cc-crawler, the Crawl4AI/Chromium crawl
              service (rung 2 for JS-rendered docs). Standalone like sandbox/:
              runs in a chromebox-pinned POD, imports nothing from central_command
  events/     log.py — the durable append-only event log + live fan-out (SSE source)
  gateway/    gateway (approve/reject) + executor (performs writes) — the trust boundary
  integrations/  n8n_facade (Jira via webhook), graphiti (MCP client; reads ungated, writes Executor-only)
  api/        FastAPI app, routes, SSE events
web/          the forked Nerve cockpit (Vite + Hono). Central Command's own routes are
              the `cc-*` ones in server/routes; web/vendor-unused/ and web/docs/
              are upstream leftovers — see web/VENDORED.md before trusting either.
servers/      MCP servers AGENTS wrote, one dir per server — arrives only through
              an approved mcp.sync_source, never hand-edited into existence
fixtures/     emails/*.eml — the stand-in backlog corpus (real provider slots in later)
scripts/      m2_spike (durable proof), m5_acceptance (live), onboard_episode.py
              (the setup interview's graph writes), graph_inspect.py,
              run_evals.py, vendor_docs_fetch.sh (builds docs/vendor/).
              oneoff/ is spent.
deploy/k3s/   THE deployment (2026-07-31): README.md is the CLEAN-INSTALL
              RUNBOOK the /setup skill conducts phase-by-phase; manifests
              (00-namespace, 20-postgres, 30/31-litellm(+rbac), 40-graph,
              50-n8n, 60-sandbox, 61-mcp, 70-crawler,
              80-vlogs — the log console: VictoriaLogs on the chromebox +
              a Fluent Bit DaemonSet shipping journald AND pod logs from both
              nodes; UI at :9428, tailnet via tailscale serve),
              scripts (init-env.sh, make-secrets.sh, mint-keys.sh,
              build-graphiti-image.sh, build-sandbox-image.sh, build-crawler-image.sh,
              make-sandbox/mcp/litellm-kubeconfig.sh, install-gvisor.sh,
              migrate-from-docker.sh, backup.sh, cc-update.sh, graph-browse.sh,
              verify.sh), registries.yaml.example (containerd mirror seam), and
              the units (cc-uvicorn, cc-sandbox-runner, cc-graph-bolt,
              cc-backup.service/.timer, cc-update.path/.service +
              cc-update-tmpfiles.conf — full release surface, see above).
deploy/AIRGAP.md + airgap.env.example
              the air-gap/mirror seam doc: pip/uv/npm/container-registry
              overrides, what still needs pre-staging. Read it FIRST for any
              mirrored or no-egress install; /setup Phase 0a points here.
deploy/single/ the GIFTABLE single-node `podman kube play` profile (2026-08-25
              deterministic-setup redesign): core spine as plain Pods (pod name =
              DNS name, no Services), user-supplied LLM endpoint, n8n optional,
              plus the sandbox (podman backend, `CC_SANDBOX_BACKEND`) and
              crawler now at parity with deploy/k3s/. `setup.sh` is the
              deterministic driver (validate/preflight/llm/stack/app/verify,
              PASS/WARN/FAIL, `diagnose` support bundle) the /setup skill
              conducts — the skill's job is elicitation and diagnosis only.
              Derived from deploy/k3s/, which stays the operator's deployment and is
              NOT this.
deploy/pi/    SUPERSEDED compose stack — kept for its comments and as the
              rollback path (docker volumes were never deleted). Still the home
              of .env, graphiti/ + litellm/ configs, and cc-nerve.service.
              graphiti/patches/ holds the graphiti_core patches the derived
              image applies (upstream #1729 invalidation scope, #1666
              reasoning-first dedupe) — both are OPEN upstream, so they are
              files to DROP when they merge, not edits to carry forever.
deploy/desktop-superseded/
              the pre-move DESKTOP units — history, not run (own README; two
              filenames collide with the Pi's, which is why they moved).
tests/        contract, durable, jira_facade, ledger, eventlog, threading
docs/         DESIGN.md (design compendium
              + the index of the design records that followed it),
              (STATUS.md and JOURNAL.md moved to the private instance repo,
              2026-08-27 — see the Git rule above),
              QUEUE.md (operator's guide to the inbox queue: reading it, tuning
              the valves, backlog depth, swapping the email provider),
              reference/ (the sanitized work-environment compatibility profile),
              superpowers/ (the ACTIVE design-record trail: research/plans/specs),
              experiments/ (scripts reproducing findings the specs cite),
              vendor/ (offline doc mirrors for the air-gapped target — the
              bulk of the repo by size; own README.md is the system doc.
              Nothing there is Central Command's own writing — never edit it,
              refetch it via scripts/vendor_docs_fetch.sh)
requirements.lock
              the frozen, suite-tested Python resolution (air-gap
              reproducibility — pyproject's >= bounds resolve differently
              against a mirror; regenerating it is a deliberate act, see its
              header)
```
