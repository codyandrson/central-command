# Changelog

Public what-changed record for Central Command. One entry per release or
notable landing, newest first. The development journal behind these entries
(incidents, milestone write-ups) is a private instance document.

## 2026-08-30 — v2.3.0: the LiteLLM manager measures a model's capabilities instead of guessing them

- **New read tool `litellm_probe_model`** (`integrations/litellm.probe_model`,
  in the `litellm-read` pack): one small real request per capability through
  the proxy — chat, system message, forced `tool_choice`, tool calling and
  parallel calls, `json_schema` and `json_object` output, vision (a 1×1 PNG),
  reasoning (`reasoning_effort` accepted AND `reasoning_content` returned),
  the output-token ceiling, streaming; `measure_context=True` bisects the
  input ceiling (≤10 long prompts, opt-in). Returns `declared`, `observed`
  and `suggested_model_info` — the declared→observed diff a
  `litellm.update_model` proposal carries. "HTTP 200, empty content,
  `finish_reason: length`" is reported as INCONCLUSIVE, never as a
  capability finding: a thinking model spends a small budget on reasoning
  before it answers (the first cut scored every JSON/vision check on
  `cc-default` as "no" for exactly that reason). Nothing in LiteLLM does
  this — its `supports_*()` helpers are static cost-map lookups and
  `/health` only proves a model answers. Measured live against the whole
  local fleet; matches known truth (the judges' vision 500 is the mmproj
  incident the attachment gate was built around).
- **`litellm_list_models` no longer hides the capability fields.** Each
  entry carries a `capabilities` object (`mode`, `max_input_tokens`,
  `max_output_tokens`, the `supports_*` flags, `thinking_mechanism`,
  `thinking_levels`), None-preserving — the manager was told to declare
  `supports_vision` and could never see whether it had. Note the asymmetry
  this exposed: Central Command reads an undeclared flag as "nobody said";
  LiteLLM's own `/model_group/info` coerces it to **false** — `cc-default`
  advertised `supports_function_calling: false` while every agent called
  tools through it.
- **The local fleet is declared from measurement**
  (`deploy/pi/litellm/model-preferences.yaml`; `policy.py`'s
  `OPTIONAL_MODEL_INFO_KEYS` gains `supports_function_calling`,
  `supports_response_schema`, `mode`, `max_output_tokens`). **After
  updating, run `python3 deploy/pi/litellm/policy.py --apply`** — the
  updater does not apply policy, and `verify.sh` reports the new
  declarations as drift until it is run.
- **New skill `skills/model-evaluation/`** — the probe protocol, the
  registration recipes for private gateways (OpenAI vs Anthropic format
  detection, `base_model` for cost-map defaults, `additional_drop_params`,
  per-model health knobs), and the pre-staged public model library in
  `docs/vendor/model-library/` (LiteLLM's cost map, models.dev, LLMStats,
  the Aider polyglot leaderboard — all MIT/Apache-2.0/CC-BY, refetched by
  `scripts/model_library_fetch.sh`). Import it and grant it to
  `litellm-manager` from the Skills screen; the manager now also holds
  `skill-propose` (schema seed + `DEFAULT_PACKS`) so a probed model's
  measured profile becomes a versioned library document, not a session
  memory. The public card is a HYPOTHESIS for a private variant; the probe
  is the evidence.
- **Charter + autodiscovery brief:** a "testing a model" section (register
  with what is certain → probe → declare → record) and the add brief no
  longer says "plus a web read" — useless where the models live.
- **`scripts/run_evals.py --model A --model B [--out results.json]`** runs
  the same charter and cases against several aliases and prints a per-model
  table — a MODEL comparison on our own workload, the shape a comparison
  record in the library takes.

## 2026-08-30 — v2.2.0: the single-node setup acquires every dependency up front, from mirrors or an explicit bundle

- **New `fetch` phase** (`deploy/single/setup.sh`, between `preflight` and
  `llm`): the only phase that touches the network, and it runs before
  anything is deployed. Images are pulled **by digest** from the new
  manifest `deploy/single/images.txt` and tagged locally (the templates'
  `name:tag` never triggers a pull; a mirror cannot serve a drifted or
  poisoned tag — the k3s docs' stated reason to prefer digests); the three
  local images are built with the operator's mirrors passed in as
  build-args; the Python graph is resolved (`uv pip install --dry-run`); the
  cockpit's `npm ci` runs. Every artifact that cannot be acquired is a
  `FAIL` naming the `.env` seam that governs it, and the phase ends in
  `USERACTION` (exit 3). `stack` now only ASSERTS the images are present.
  `update.sh apply` runs `fetch` before the schema or the code moves.
- **One answer file for every seam** (`deploy/single/env.example`, "Where
  every dependency comes from"): `CC_REGISTRY_{DOCKERIO,GHCR,MCR}`,
  `CC_APT_MIRROR` + `CC_APT_SECURITY_MIRROR`, `CC_PYPI_INDEX_URL` (fanned
  out to `PIP_INDEX_URL` AND `UV_DEFAULT_INDEX` — uv reads no `PIP_*`
  variable and `UV_INDEX_URL` is deprecated), `CC_PYTHON_MIRROR`,
  `CC_NPM_REGISTRY`, and per-artifact `CC_SOURCE_<X>=mirror|bundle` with
  `CC_BUNDLE_DIR`. Mirrors are the primary path; the bundle is the
  operator's explicit per-artifact fallback; **nothing falls back on its
  own** (the shape Zarf and k3s take: the artifact set is fixed in a
  manifest, fail-loud is deliberate).
- **Build containers get the mirrors as build-args.** The graphiti and
  sandbox Dockerfiles REWRITE `/etc/apt/sources.list.d/debian.sources` from
  `/etc/os-release` — the slim images ship only the deb822 file and delete
  `sources.list`, so a sed of the classic file silently does nothing, and
  the security archive is a separate path on every mirror needing its own
  stanza. Registry prefix on every `FROM`; `UV_DEFAULT_INDEX` /
  `PIP_INDEX_URL` / `NPM_CONFIG_REGISTRY` where each tool looks.
- **The crawler is rebased on Microsoft's Playwright image**
  (`mcr.microsoft.com/playwright/python:v1.62.0-noble`): browsers and OS
  libraries are baked in, so the build no longer needs a Debian mirror or
  the browser CDN (`crawl4ai-setup` runs in `CRAWL4AI_MODE=api`, which
  skips its `playwright install --with-deps`; the build launches Chromium
  once as its own check). `playwright==1.62.0` is pinned to the base tag;
  fastapi/uvicorn/httpx and the sandbox's `@anthropic-ai/sandbox-runtime`
  are pinned so a mirror rebuild produces the bytes a bundle holds.
- **`deploy/single/bundle.sh`** export/import: every image in `images.txt`
  under its public ref, the three local images under `localhost/cc-*`, a
  `pip download` wheelhouse of `requirements.lock`, the built cockpit
  (`dist`, `server-dist`, `bin-dist`, `node_modules`), a sha256 `MANIFEST`
  and a `RELEASE` marker; import refuses another release and verifies
  checksums before loading anything. With `CC_SOURCE_COCKPIT=bundle` the
  site needs no npm. `deploy/airgap-image-tarballs.sh` is k3s-only now —
  its `single` path loaded images under `docker.io/library/cc-*`, a name
  this profile's templates never referenced.
- `deploy/airgap.env.example` no longer offers the deprecated `UV_INDEX_URL`.
  `deploy/AIRGAP.md` rewritten as the map of every external source and its
  seam. `tests/test_single_airgap_seams.py` walks the five files that must
  agree (every pulled image pinned in `images.txt`, every seam declared in
  `env.example`, `fetch` ordered before `llm`, scripts parse).


- **Setup no longer knows your LLM provider.** Both drivers' `llm` phase
  bring the proxy up, create every required alias as a **skeleton** in
  LiteLLM's database — the name plus the facts that are not the operator's to
  choose (`graphiti-llm`'s `openai/chat_completions/` bridge prefix, the
  reranker's `mode: rerank` and `/v1/rerank` path, per-alias timeouts) with
  `PLACEHOLDER` where the model id and `api_base` go — and **stop (exit 3)
  on every fresh catalog** so the operator enters model ids, endpoints and
  keys/credentials in the LiteLLM UI before anything else is deployed.
  Re-running `setup.sh llm` checks each row (placeholder gone, invariants
  kept), then validates with real requests — chat through `cc-default`, a
  schema-constrained **Responses-API round trip through `graphiti-llm`**
  (the failure a chat probe cannot see: without the prefix the proxy
  returns prose with HTTP 200), an embedding with its dimension — and
  continues. Operator decision: models live in the LiteLLM database and
  are managed through its API/UI; the config file is the fallback for what
  the API cannot set.
- **`register-models.py` is create-only.** An alias that exists is never
  written to, so nothing entered or later changed in the UI is overwritten
  by a re-run or an update. Declared values are patterns
  (`openai/chat_completions/PLACEHOLDER`), `api_key` is never declared, and
  exit 3 means operator action. `model-preferences.yaml`'s registrations
  and the new `deploy/single/models.json` are skeletons; the k3s routing
  policy (`policy.py`) is unchanged.
- **Single-node `.env` drops `CC_LLM_BASE_URL` / `CC_LLM_API_KEY` /
  `CC_CHAT_MODEL` / `CC_EMBED_MODEL` and the split-embed pair.** The key is
  entered in the UI and stored encrypted under `LITELLM_SALT_KEY`; nothing
  renders or mounts it. `discover-llm.sh` gains `structured <alias>` and its
  direct mode takes the endpoint from the environment only. This closes the
  k3s gap where the three Anthropic rows registered with no credential and
  401'd on first use — the credential is created during the pause.

## 2026-08-30 — v2.0.3: the single-node profile keeps its models in LiteLLM's database, not the config file

- **`deploy/single` no longer declares models in `config.yaml`.** The profile
  had four entries in the ConfigMap's `model_list` *and* `store_model_in_db:
  true` — config-declared entries are static (the proxy UI cannot edit them,
  and a same-named DB row becomes a second deployment in the group) and they
  carried no `model_info`, so the app's thinking and vision declarations were
  silently absent on every gift install. The catalog is now seeded the way
  the k3s profile has always done it: `models.json.tmpl` renders to
  `models.json` from `.env`, and the `llm` phase applies it through
  `deploy/pi/litellm/register-models.py` — upsert by `model_name`, everything
  else on the proxy left alone, so operator edits made in the UI/API survive
  a re-run. The rule this lands: **models live in the LiteLLM database and
  are managed through its API/UI; the config file is the fallback for what
  the API cannot set, never the default.**
- `register-models.py` takes `--policy <file>`; a `.json` declaration is read
  with the stdlib so the pre-venv system python needs no PyYAML. The k3s
  default (`model-preferences.yaml`) is unchanged.
- `render.sh` renders and leak-checks `models.json` in both modes.

## 2026-08-30 — v2.0.2: a resume answers every deferred call in the parking response

- **A turn that defers two calls no longer loses the second.** A park records
  one `tool_call_id`, but a model may defer several calls in one response
  (two `propose_action`s; a propose and an ask). Every resume site answered
  the one id, and pydantic-ai refuses a resume that does not cover every
  deferred call in the parking response — so the run failed
  (`session.resume_failed`, non-transient) and the decision paths completed
  the session anyway, with the sibling draft never shown to the operator
  (live 2026-08-30: the onboarding tour's closing turn drafted two episodes).
  `durable.deferred_results` is now the one seam all six resume paths use:
  the decided call gets its result and each sibling a `ToolFailed` telling
  the model it was never seen and to raise it again; on the approved leg the
  existing follow-on branch parks the re-draft as its own proposal. Each
  bounce is recorded as `session.deferred_siblings_bounced`.
- Why `ToolFailed`: `ModelRetry` spends the tool's retry budget (one for
  `ask_operator`/consult), and `ToolDenied` is an approval-kind result that
  reaches the model as a serialised dict when placed in `.calls` (the reject
  path has carried that shape since it landed — unchanged here, noted for a
  deliberate follow-up). Measured, not assumed: `parallel_tool_calls=false`
  IS honoured by the LiteLLM → llama.cpp path (1 call vs 2), but forcing one
  call per turn fleet-wide is a behavioural change left out on purpose.

## 2026-08-30 — v2.0.1: a discussion lane runs under its charter, and cannot be closed out from under its question

- **A tier-3 discussion lane now carries the agent's charter.** `_open_discussion`
  seeded the lane with the agent's question as a `ModelResponse` and nothing
  else, and pydantic-ai adds system-prompt parts only when the history is
  EMPTY — so every turn of every seeded lane ran with the agent's tools and
  none of its charter: no pack guidance, no "conclude before you propose", no
  team section. Live 2026-08-29 the EA hosted the whole onboarding tour that
  way and, asked how to end it, said "there is nothing for me to conclude".
  The seed now leads with a `ModelRequest` of the same system-prompt parts a
  fresh chat would have persisted (`converse.lane_system_prompt`, built by the
  same builder the lane's turns use; a test pins the equality).
- **Closing a lane by hand is refused while its question is OPEN.**
  `end_conversation` was a third door with no resolution semantics: the lane
  went terminal, the item stayed OPEN, the task stayed blocked forever. It now
  refuses (409 in the cockpit) and points at the Decisions Inbox — answering
  the item closes the lane with the answer, as the agent's own
  `conclude_discussion` always did. Lanes opened before this release that were
  already closed this way still resolve by answering their item.
- `min_upgrade_from=2.0.0`: the v2.0.0 rename is applied by hand
  (`migrate-to-cc.sh`), so the cockpit updater carries only 2.x → 2.x.

## 2026-08-29 — v2.0.0: one name everywhere — the historical identifiers are gone

**Breaking. This release is applied BY HAND, not by the cockpit updater**
(`deploy/k3s/migrate-to-cc.sh`, see its header and `deploy/k3s/README.md`):
it renames the updater's own units, the Python package, the databases and
the Kubernetes namespaces, so the running v1.0.x updater cannot carry
itself across. `min_upgrade_from=1.0.7`.

- The product's historical code name is removed from every identifier, not
  just prose: Python package `central_command/` (`import central_command`),
  env prefix `CC_*` (every reader — `config.py`, the sandbox runner, the
  deploy scripts, `verify.sh`), pods/units/images/scripts `cc-*`, k8s
  namespaces `central-command` / `cc-mcp` / `cc-sandbox`, node labels
  `cc-role/*`, databases `central_command` / `central_command_test` and role
  `central_command`, Graphiti groups `central_command` / `central_command_<agent>`,
  LiteLLM alias `cc-default`, n8n webhook paths `webhook/cc-*-facade`,
  cockpit wire events `cc.*` and routes `cc-*.ts`, `CC_POD_PREFIX=cc-` in
  `deploy/single/`, `~/cc-backups`, `~/.cc-private-identifiers`.
- Data migration (`migrate-to-cc.sh`): renames the Postgres databases and
  role in place; rewrites the Graphiti `group_id` on every node and
  relationship (embeddings untouched); rewrites the synthesized
  `work_item.message_id` suffix and the stored prose in `audit_event.payload`,
  `session.run_state`, `proposal.*` and `operator_item.body` — the
  append-only log is rewritten in place, once, by operator decision; moves
  every local-path PV's data into the new namespace's claims; retags the
  three locally-built images on both nodes; installs the renamed units and
  removes the old ones; rewrites the gitignored `.env` files to the `CC_` prefix
  with a backup. Rollback reverses the recorded log.
- Cockpit persisted preferences are re-keyed (`cc-*` / `cc:*`) without a
  fallback to the old keys, so the "show history" toggles reset and
  already-seen notifications toast once more after the upgrade.
- `docs/vendor/{cva,dompurify,hono,hono-node-server}/README.md` were stale
  copies of this repo's own README, vendored in error on 2026-08-25;
  refetched for real from their tags (LICENSE files now included).
- Fixed: `web/src/components/TopBar.tsx` imported `LayoutGrid` twice (TS2300),
  which broke `npm run build` on v1.0.7 — the cockpit updater's rebuild
  phase failed on it.
- Git history is rewritten in the same release (every historical blob and
  commit message; all `v1.0.x` tags re-pointed), so an existing checkout must
  be re-pointed: `git fetch origin && git reset --hard origin/master`.

## 2026-08-29 — v1.0.7: Systems launchpad; private-only agent memory; LiteLLM UI login fields

- **Systems view** (top bar › Systems, or "Open Systems" in the palette): one
  launchpad listing every deployed or integrated system — cockpit, control
  plane API, LiteLLM, both Postgres stores, Graphiti, Neo4j, n8n,
  VictoriaLogs, sandbox runner, crawler, and Jira/Confluence when configured
  — with live up/down + latency from a concurrent health fan-out
  (`GET /api/systems`, `central_command/api/systems.py`), an **Open →** link
  where a browser URL is configured, and WHERE each credential lives (env
  var name / file) — never the value. New optional browser-URL settings on
  the `CC_LLM_PROXY_UI_URL` pattern: `CC_N8N_UI_URL`, `CC_VLOGS_UI_URL`,
  `CC_NEO4J_BROWSER_URL` (tailnet addresses; `deploy/k3s/verify.sh`
  exempts them from the loopback rule for the same reason).
- The per-agent **memory panel** on the chat page now shows only that
  agent's PRIVATE episodes by default (`/api/graph/episodes?scope=private`;
  `scope=all` restores the shared+private union). The graph view is the
  place for shared knowledge. It also never silently truncates: the response
  carries a real graph count (`total`) and `has_more`, the panel shows
  "Showing N of TOTAL" and a **Load more** button that doubles the page.
- **LiteLLM admin-UI login**: `UI_USERNAME` / `UI_PASSWORD` are now
  declared (commented out) in `deploy/pi/.env.example` and
  `deploy/single/env.example` and plumbed into the proxy's Secret and pod
  env, so uncommenting them replaces the master-key login. Unset keeps
  LiteLLM's default (`admin` + master key). On k3s the keys are only written
  when set (`optional: true` on the pod); on the podman profile the Secret
  carries LiteLLM's own defaults, because LiteLLM reads `UI_PASSWORD` with
  `os.getenv(..., None)` and an EMPTY string would become the real password.

## 2026-08-29 — v1.0.6: Settings › Updates with a manual "Check for updates"

- The cockpit's update check is now one shared state (`web/src/lib/
  version-check.ts`): checked on load, hourly, and whenever the tab regains
  focus. **Settings › Advanced › Updates** shows the running version, the
  latest published one, when it was last checked, a **Check for updates**
  button (`GET /api/version/check?force=1` bypasses the server's hourly
  cache) and, when one is available, the same apply dialog the status-bar
  badge opens. Nothing auto-applies; the root updater remains the only path.
- The status-bar and settings-footer version labels now read the product's
  `VERSION` (they showed the vendored Nerve cockpit's `package.json` version).

## 2026-08-29 — v1.0.5: updater uses `npm ci`; verify.sh exempts the proxy-UI link

- `cc-update.sh` (and both `setup.sh`s) build the cockpit with `npm ci`
  instead of `npm install`: install rewrote `web/package-lock.json` during
  the v1.0.4 update (one metadata line), leaving a dirty tracked file that
  would have made the next update refuse at its clean-tree check.
- `deploy/k3s/verify.sh` no longer flags `CC_LLM_PROXY_UI_URL` as a
  non-loopback endpoint: the app never calls it — it is the LiteLLM admin
  link the cockpit hands to the operator's browser, so a tailnet address is
  the correct value.

## 2026-08-29 — v1.0.4: argument-shape validation before the Inbox

- A proposal's action ARGUMENTS are now checked at propose time, not first at
  execution. `contract.ARG_SPECS` declares each capability's required keys
  and closed enums (the graph family to start: `graph.add_episode` and the
  seven curation actions); `contract.validate_action_args` runs in the
  runtime — a bad draft goes back to the model as a tool error listing EVERY
  problem, it redrafts in the same run, and only a well-formed proposal
  reaches the Decisions Inbox — and again in the Executor before any action
  runs, so an API-submitted proposal gets the same refusal with an empty
  completed-cursor instead of a half-applied one. Found live 2026-08-29: one
  `graph.add_episode` intent cost four approvals (`summary` for
  `episode_body`, then no `name`, then no `scope`), the Executor surfacing
  one problem per round, two of them as bare KeyErrors (`'name'`).
- Every in-run rejection is recorded as a `proposal.rejected_in_run` event
  (agent, capabilities, problems, intent): the operator is spared the
  re-approval, not the knowledge — a cluster of these is the coaching signal.
- Every `propose_*` tool now carries `max_retries=10` (was pydantic-ai's
  default of 1 for all but bulk-dismiss), so a second bad draft no longer
  fails the whole session; exhausting the budget still does, loudly.
- Two demo-model (`spike_model.py`) `graph.add_episode` drafts had carried no
  `scope` since the 2026-08-27 executor guard — never noticed because parked
  demo proposals never executed. The new propose-time check caught both.

## 2026-08-29 — cc-update covers the full release surface

- `deploy/k3s/cc-update.sh` now handles the parts of a release it used to
  silently skip: changed k8s manifests are `kubectl apply`ed (with rollout
  waits), changed systemd unit files are installed + `daemon-reload`ed, and
  the three locally-built images (`cc-graphiti`, `cc-sandbox`, `cc-crawler`)
  are rebuilt when their inputs changed — **before any service stops**, from
  a detached worktree of the target tag, natively per architecture on each
  node in parallel. The running image bytes are preserved first by a
  containerd retag (`:pre-update-<stamp>`), so automatic rollback now also
  restores images and re-applies the checkpoint's manifests. Downtime is
  unchanged; only total wall-clock grows when a release actually ships an
  image change (`TimeoutStartSec` raised to 5400 accordingly).

## 2026-08-29 — cc-update backup scope widened; update-docs audit

- `deploy/k3s/cc-update.sh`'s pre-update backup now dumps the LiteLLM and n8n
  databases alongside the spine, captures `N8N_ENCRYPTION_KEY` /
  `LITELLM_SALT_KEY` beside them (without the keys those dumps are
  undecryptable ciphertext), and validates every dump carries pg_dump's
  completion marker before proceeding — a truncated dump now blocks the
  update instead of posing as a backup. Neo4j stays deliberately out of scope
  (nightly `cc-backup.timer` covers it; dumping it would scale the graph
  stack down mid-update for no update-safety gain).
- Update-process documentation audit: exit-code corrections in
  `deploy/single/README.md` (the one-command path gates with exit 3, and a
  successful apply ends with a `USERACTION restart`, not a WARN), the root
  README now teaches the one-command `./update.sh <zip>` path first, the
  cockpit's update modal describes the widened backup scope, and this
  changelog was backfilled for v1.0.1–v1.0.3 (entries below).

## 2026-08-28 — v1.0.3: `update.sh <zip>`, the one-command update path

- `deploy/single/update.sh <path-to-zip>` chains
  init → import → plan → (explicit y/n, or an exit-3 gate if headless) → apply,
  and offers to stop a `./setup.sh boot`-started API by its own pid file
  before applying. The named subcommands (`init`/`import`/`plan`/`apply`/
  `rollback`) remain for granular or agent-conducted flows.
- Fixed a bug where `cmd_import`'s cleanup trap stayed armed across later
  function returns and could re-fire under the new one-command path with its
  temp directory already out of scope.

## 2026-08-27 — v1.0.2: one-command install-to-demo, gateway model catalog

- `deploy/single/setup.sh` gained `test` (pytest gate), `boot` (starts uvicorn
  detached, elicits `CC_OPERATOR_NAME`), and `demo` (feeds a fixture email,
  steps the dispatcher, waits for a real operator approval in the cockpit) —
  probing live state for idempotency rather than a state file.
- `/api/gateway/models` added to FastAPI, fixing a 404 on the single-node
  profile (the k3s Node server route it was missing didn't exist there).

## 2026-08-27 — v1.0.1: Windows clean-install fixes

- Windows-safe header piping (`curl -H @-` from stdin, not process
  substitution) and a doc fix for which URL the container itself dials.
- Two suite failures caught by the Windows clean-install pytest gate fixed
  (a stale fixture scope, a symlink-attack test skipped where Windows
  developer mode isn't available).

## 2026-08-27 — cc-update: one-click apply for the k3s deployment

- `deploy/k3s/cc-update.sh` + `cc-update.path`/`.service`/`-tmpfiles.conf`:
  the cockpit's "Apply update now" writes a trigger file; a systemd `.path`
  unit runs the updater as root. It resolves the latest (or requested) origin
  tag, version-gates it, tags a rollback checkpoint, dumps the spine DB,
  stops `cc-uvicorn`/`cc-sandbox-runner`, merges the tag, reapplies
  `schema.sql`, rebuilds, restarts, and automatically rolls back on a failed
  health check. Status is written to `/var/lib/cc-update/status.json` outside
  the repo tree so a rollback's `reset --hard` can't clobber it.
  Scoped to code/schema/systemd-managed processes only — it does not apply
  Kubernetes manifests or rebuild locally-built images.

## 2026-08-27 — Zip-based update path for air-gapped deployments

- `deploy/single/update.sh`: `init / import <zip> / plan / apply / rollback`.
  A zip-installed deployment becomes a two-branch git repo (`upstream` =
  pristine imports, `local` = the deployment); updates are compare-then-merge
  with local modifications preserved by three-way merge, schema applied
  before code goes live (additive-only, idempotent), deps/cockpit reconciled
  via `setup.sh app`, and a tagged rollback point per update.
- README rewritten as a getting-started walkthrough (get the code → 
  prerequisites → `/setup` → onboarding → updating);
  `deploy/single/README.md` gains the full update reference.

## 2026-08-27 — Initial public release

First public snapshot, fresh history. The system at this point:

- The full governed spine, built and human-verified (M0–M16): agent proposes →
  durable pause (survives restart) → Decisions Inbox → operator approval →
  gateway → credentialed Executor performs the write → provenance stamped →
  agent resumes. Demo mode (no API key, dry-run writes) included.
- Phase 4 in active development: graduated auditor, coaching loop with real
  charter versions, D7 multi-agent orchestrator, governed heartbeat scheduler,
  data-driven capability packs and roster, skills library.
- Deployments: two-node k3s (`deploy/k3s/`, the reference deployment) and the
  giftable single-node podman profile (`deploy/single/` + `/setup`).
- Runtime: Pydantic AI 2.0 + Claude; Postgres spine; Graphiti/Neo4j knowledge
  graph; LiteLLM proxy; forked MIT Nerve cockpit (`web/`).
