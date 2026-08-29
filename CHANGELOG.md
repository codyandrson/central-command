# Changelog

Public what-changed record for Central Command. One entry per release or
notable landing, newest first. The development journal behind these entries
(incidents, milestone write-ups) is a private instance document.

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
