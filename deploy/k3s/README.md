# deploy/k3s — clean-install runbook

> **The install is driven by `./deploy/k3s/setup.sh` (2026-08-27,
> the setup-update contract):** `validate → preflight → llm → stack → app →
> verify`, each phase re-runnable alone, `PASS|WARN|FAIL|USERACTION` output,
> exit 3 = stopped for your action, `diagnose` for a support bundle. It
> mechanizes §1–§6 and §9 below by CALLING the same scripts they describe.
> This file remains the reference — the *why* behind each step, the placement
> table, §8's instance-data decisions, and §10's rollback path.

From bare cluster to a running Central Command. Every command is
copy-pasteable and every `apply`/`make-*` step is idempotent, so a re-run
reconciles rather than breaks.

Context for *why* the two-node split looks like this:
`.claude/rules/deploy-k3s.md` → "Placement splits by STATE". Live state
(counts, roster, what's in the queue) belongs to your private instance repo.
This file does not repeat either.

---

## 0. Upgrading an existing v1.0.x install to v2.0.0

v2.0.0 renamed every identifier (package, `CC_*` prefix, `cc-*` pods/units/
images, the namespaces, the databases and role, the Graphiti group ids, the
LiteLLM alias, the n8n webhook paths). Kubernetes cannot rename a namespace
and the cockpit updater cannot rename its own units mid-run, so **this one
release was applied by hand**, with downtime, by a one-shot script, kept in
git history at v2.75.0, where its header is the runbook. The cockpit updater
refuses a v1.x install (`min_upgrade_from` in `VERSION`). Fresh installs follow
§1 onward as before.

## 0a. The update that removes the Graphiti server

v2.60.0 moved graphiti-core into the control plane (design record
`docs/superpowers/specs/2026-10-04-graphiti-library-migration-design.md`, D10)
and deletes `cc-graphiti` and its `graphiti` Service. **Update from v2.59.1
or later** — v2.60.0's `min_upgrade_from=2.59.1`, and the installed updater
refuses an older install's jump at resolve, before anything changes. Apply
v2.59.1 first (the cockpit offers only the newest release, so take v2.59.1
while it is the newest, or name it as the update's target).

The update INTO v2.60.0 is run by v2.59.1's `cc-update.sh` (systemd runs the
installed copy), and that bridge release is what makes it hands-free: after
the dependency install it applies graphiti-core's carried fixes, and before
the services start it widens the app key's scope (`mint-keys.sh
--scope-only`, the new release's script) and adds `CC_GRAPH_RERANK_ALIAS=
cc-rerank` to the app's `.env` when the key is absent. Nothing to run by hand.

The tree was built to survive that updater's in-memory rows: the old image
row's inputs (`deploy/pi/graphiti/`, `deploy/k3s/build-graphiti-image.sh`) were
left byte-identical through v2.60.0, so it neither rebuilt the image nor
rollout-restarted the Deployment its `removed.txt` pass deletes. v2.61.0,
applied by v2.60.0's updater (which has no such rows), deleted those files and
tombstoned the `cc-graphiti-config` configmap and `cc-graphiti` Secret — one
release LATER than the Deployment, so v2.59.1's rollback (which re-applies the
v2.59.1 manifests) still found what the old Deployment mounts. v2.61.0's
`min_upgrade_from` is v2.60.0: an install still on v2.59.1 takes v2.60.0 first.

Once no rollback to v2.59.x is wanted: revoke the three retired key aliases
(`graphiti-llm`, `graphiti-embeddings`, `graphiti-reranker`) in the LiteLLM
UI, and remove the `cc-graphiti:1.0.2-anthropic` image from both nodes'
containerd. If the update's journal shows a WARNING from the patch step or
the scope check, the matching command is
`.venv/bin/python scripts/apply_graphiti_patches.py` or
`./deploy/k3s/mint-keys.sh --scope-only` — until then extraction is held
(jobs stay queued) or graph calls get a 403 the worker retries; nothing is
lost.

## 0b. The update that moves the containers' env

v2.66.0 is the first half of the `deploy/pi/` split (two releases, the shape
of §0a). The live set moved: `deploy/pi/litellm/` → `deploy/shared/litellm/`
(both deployment substrates read it), `deploy/pi/.env` → `deploy/k3s/.env`
(template `deploy/k3s/.env.example`), `deploy/pi/cc-nerve.service` →
`deploy/k3s/cc-nerve.service`. Every k3s script finds the env through ONE
function, `cc_k3s_env_file` in `deploy/k3s/env-path.sh`: the new path when it
exists, else the old one. **v2.66.0 deletes nothing** — the old copies stay
byte-identical for one release because the update INTO v2.66.0 is run by the
installed v2.65.x updater, whose configmap row, units glob and policy-apply
still name `deploy/pi/...` against the merged tree. v2.67.0 deletes
`deploy/pi/` whole (duplicates and the retired Compose stack) once v2.66.0 is
validated, and sets `min_upgrade_from=2.66.0`.

The env file is untracked, so no merge moves it and no rollback restores it.
The move is `cc_k3s_env_migrate` (same file): `deploy/pi/.env` a regular file
and `deploy/k3s/.env` absent → `mv`, mode kept, and a relative symlink left at
the old path so any release that still reads it finds the same bytes; both
present as regular files → refused, nothing touched. Two callers, same
function: **after applying v2.66.0, run `./deploy/k3s/init-env.sh`** — it
migrates first, then does its usual never-overwrite fill — and v2.66.0's own
updater runs it right after the merge of the NEXT release (WARN-only; an
unmoved file only means every reader keeps using the old path). Check with
`ls -la deploy/k3s/.env deploy/pi/.env`: the first a `-rw-------` file, the
second a symlink to `../k3s/.env`. The two keys inside never change; compare
`sha256sum` of the file before and after if in doubt. Rollback matrix: a
failed v2.66.0 apply lands on v2.65.x with nothing moved; a failed v2.67.0
apply lands on v2.66.0, which reads the new path first. v2.67.0 deleted the
tracked files only, so the untracked symlink and the `__pycache__` survive on
an install that took the update (a fresh clone has neither, and `ls deploy/pi`
there fails — nothing to do). The symlink is
removed by hand after v2.67.0 is accepted (`rm -r deploy/pi/litellm/__pycache__
&& rmdir deploy/pi/litellm && rm deploy/pi/.env && rmdir deploy/pi`); nothing
automated needs it then. Run that only when `ls -la deploy/pi/.env` shows a
symlink (`-> ../k3s/.env`). A regular file there means the migrate step
refused — both paths were regular files — and the two must be settled first
(the one the containers were built from is `deploy/k3s/.env`; compare before
removing anything), or the `rm` deletes the only copy of the keys.

## 1. Prerequisites & assumptions

Assumed already true before phase 2:

- **k3s v1.34+** installed: an arm64 **anchor** node (control plane) and an
  amd64 **compute** node (agent). `sudo k3s kubectl get nodes` shows both
  `Ready`. The anchor/compute roles are node labels (§3a), never hostnames.
- **A private network** (a tailnet, for instance) between the two; the anchor
  can `ssh <user>@<compute-address>` without a password prompt (every build
  script shells over that). Set the target as `CC_COMPUTE_SSH` in
  `deploy/k3s/.env`; the real value belongs to your private instance docs.
- Repo cloned at **`<checkout>`** (any path; the units and scripts below are
  run from it).
- **`deploy/k3s/.env`** populated — run **`./deploy/k3s/init-env.sh`**: it copies
  the template, generates every **GENERATED** variable that is empty, and
  prints the **ELICITED** ones you must supply yourself. It
  never overwrites a value that is already set, so it is safe to re-run and safe
  on a restore. It is the single source of truth for container secrets.
  `LITELLM_SALT_KEY` /
  `N8N_ENCRYPTION_KEY` must carry the **existing** values whenever the matching
  database is being restored — a restore under a different key leaves the rows
  present and undecryptable. Generate them only on a fully-clean deployment that
  restores neither.
- **Repo-root `.env`** populated (see `.env.example`). Different audience: the
  app's own `CC_*` settings. Secrets live only in gitignored environment files:
  these two plus `web/.env` (§6) — all three are gitignored.
- **uv-managed CPython 3.12** and **NodeSource 22** on the anchor node.
- **`socat`** on the anchor node (`sudo apt install socat`) — the loopback relay
  `cc-graph-bolt` runs on it (§6).
- **No arm64 image builder.** Both locally built images (sandbox, crawler)
  are compute-node-only and built there with podman. Docker on the anchor was
  the arm64 half of the retired Graphiti image and is no longer required.
- **gVisor (`runsc`) on the compute node** — installed by
  `./deploy/k3s/install-gvisor.sh` (idempotent: reports-and-exits when already
  present). Without it, sandbox Jobs never start — the `gvisor` RuntimeClass
  applies fine and the failure only shows at first use, which is why verify.sh
  asserts it too. Run the script again after phase 4 for its pod-level proof.
- **The k3s server drop-in** on the anchor node (v2.39.2; moved under `host/` in
  v2.39.3 — `apply -f deploy/k3s/` reads every top-level yaml as a manifest)
  — `sudo cp deploy/k3s/host/10-central-command.yaml
  /etc/rancher/k3s/config.yaml.d/` then `sudo systemctl restart k3s`. It sets `terminated-pod-gc-threshold`, which
  Kubernetes defaults to 12500: every anchor-node reboot otherwise leaves a generation
  of Succeeded/Failed pod records behind forever (and a LiteLLM failover pod
  killed by a reboot reads "Error" in every pod listing). Hand-installed
  because a k3s restart is not the updater's to perform; `setup.sh preflight`
  warns and `verify.sh` fails while it is missing.
- **The LiteLLM trio** (`cc-litellm`, `cc-litellm-db`, `cc-litellm-redis`) is
  either already running or absent, and both are fine.
  - **Already running:** leave it. Every step below is safe against it —
    `make-secrets.sh` rewrites their Secret with the same values, and
    re-applying `30-litellm.yaml` is a no-op when nothing changed.
  - **Absent (a fully-clean deployment):** phase 4 brings it up fresh, on an
    empty `cc-litellm-pgdata`. That means two rebuilds, in this order.

    **1. An empty model catalog.** `store_model_in_db: true`, so every model
    lives in that database and NOT in `deploy/shared/litellm/config.yaml` — and
    since 2026-08-30 the catalog is the operator's, managed in the proxy UI.
    `register-models.py` is create-only: it creates every alias
    `model-preferences.yaml` declares as a SKELETON (name, invariants, and
    `PLACEHOLDER` where the model id and `api_base` go; no key) and exits 3
    for you to fill them in at `http://127.0.0.1:4000/ui` — model ids,
    endpoints, and the Anthropic credential under Endpoints (the pod carries
    no `ANTHROPIC_API_KEY` on purpose). Re-run and it checks the rows, then
    the policy goes on:

    ```bash
    python3 deploy/shared/litellm/register-models.py             # exit 3 = go fill in the UI
    python3 deploy/shared/litellm/register-models.py             # exit 0 = every row filled and consistent
    python3 deploy/shared/litellm/policy.py --apply              # routing policy
    sudo k3s kubectl -n central-command rollout restart deploy/cc-litellm
    sudo k3s kubectl -n central-command rollout status deploy/cc-litellm --timeout=120s
    python3 deploy/shared/litellm/policy.py --check              # proves parity
    ```

    The restart is required, not optional: the adaptive router reads member
    preferences only when constructed (`policy.py --apply` prints this same
    warning), so skipping it leaves `/adaptive_router/state` stale even though
    `--check` may still pass.

    `model-preferences.yaml` declares every alias — the `cc-*` ROLE aliases
    the app addresses (cc-default, cc-embedding, cc-rerank) AND the
    real-model rows they point at — plus the two invariants most easily got
    wrong: `graphiti-llm` must be a PLAIN `openai/<model>`, same prefix as
    `cc-default` (the app's in-process graphiti-core client,
    `OpenAIGenericClient`, speaks chat-completions, and the old
    `openai/chat_completions/` Responses→chat bridge prefix 404s), and a
    dedicated rerank row's (`qwen3-rerank-local`, and `cc-rerank` when it is
    one) **`api_base` must end in `/v1/rerank`** (the `cohere/` client POSTs
    the base verbatim and appends nothing). `cc-rerank` is graph search's
    reranker ROLE and may instead be a chat model with logprobs and thinking
    OFF; it is `judged_by_probe`, so its row is not held to the skeleton's
    patterns — the llm phase probes `/rerank`, then the chat shape, and writes
    the result to `CC_GRAPH_RERANK_KIND` (only when that key is empty; a set
    kind is proven, never re-detected). `cc-rerank` and `qwen3-rerank-local`
    are `optional: true` (v2.62.1): with **no reranker**, leave both skeletons
    unfilled — the catalog step does not pause for them, and the probe
    PASSes "no reranker" and writes `CC_GRAPH_RERANK_ALIAS=` (empty, an
    explicit off) to the app's `.env`. To turn it on later, fill `cc-rerank`,
    set `CC_GRAPH_RERANK_ALIAS=cc-rerank` there and re-run the llm phase. An
    unfilled row the app's `.env` names is a gate whose message gives that
    exact line. Tiers, the measured numbers and the benchmark:
    `deploy/single/README.md`, Reranking. Nothing
    you enter in the UI is ever overwritten by the script. Timeouts have
    exactly one home per model, which is what makes `policy.py --check` a
    real gate rather than a coin flip. `policy.py --apply` still only
    *annotates* models that already exist — register first, always. The
    setup driver (`./deploy/k3s/setup.sh llm`) runs all of this, gate
    included, and then probes `cc-default`, `graphiti-llm` (structured),
    `cc-rerank` (its kind) and the embedder through the proxy.

    **2. A fresh master key**, so every downstream virtual key must be re-minted
    against it before it will authenticate:

    ```bash
    ./deploy/k3s/mint-keys.sh    # CC_LLM_API_KEY (+ web/.env's cockpit key),
                                 # then re-runs make-secrets.sh
    ```

    Mint **after** registering — each key is scoped to model groups that have to
    exist. The app's key reaches `cc-default`, `cc-tts`/`cc-stt` and the graph's
    aliases (`graphiti-llm`, `cc-embedding`, `cc-rerank` — graphiti-core runs
    inside the app). Idempotent: a value that is set is kept (`FORCE=1` to
    override), and a kept app key whose model list lacks one of those aliases
    GAINS it in place (`/key/update`, a union; an empty list — "all models" —
    is left alone). `--scope-only` does just that widening; `cc-update.sh`
    runs it on every update.
- Air-gapped site only: copy `registries.yaml.example` to
  `/etc/rancher/k3s/registries.yaml` on **both** nodes first, fill `MIRROR_HOST`,
  then `systemctl restart k3s` (anchor) / `k3s-agent` (compute).

## 2. Secrets and ConfigMaps

```bash
cd <checkout>
./deploy/k3s/init-env.sh        # bootstraps deploy/k3s/.env (idempotent)
./deploy/k3s/make-secrets.sh
```

`init-env.sh` is the bootstrap; `make-secrets.sh` reads what it produced.

Creates the namespace, the Secrets (`cc-litellm`, `cc-neo4j`, `cc-n8n`, and
`cc-crawler-llm` if `CC_LLM_API_KEY` is set) and two ConfigMaps
(`cc-litellm-config`, `cc-schema-sql`) **from
`deploy/k3s/.env`**. Imperative on purpose: no base64'd secret ever lands in the
repo. Idempotent (`--dry-run=client | apply`).

Run this **before** phase 4 — pods that reference a missing Secret sit in
`CreateContainerConfigError`, not `Pending`, and it reads like a broken image.

## 3. Locally-built images

Two images are built here, not pulled. Everything else is multi-arch upstream.

```bash
./deploy/k3s/build-sandbox-image.sh     # compute node only
./deploy/k3s/build-crawler-image.sh     # compute node only — slow, installs Chromium
```

- (The Graphiti image and its build script left in v2.61.0 — see §0a.)
- `cc-sandbox` and `cc-crawler` are compute-node-**required** (gVisor and Chromium
  live there), so they are single-arch by design.
- **The `localhost/` trap:** podman tags local builds `localhost/<name>`; the
  kubelet looks up `docker.io/library/<name>` and then tries to pull a tag no
  registry serves — `ImagePullBackOff` on that node only. The scripts tag with
  the fully-qualified ref and verify with `grep -qx` on a *captured* list; each
  one exits non-zero if the ref is missing. Believe that check.
- Builds are native — **no QEMU**: both run on the compute node with podman.

## 3a. Label the nodes

Placement resolves entirely through `cc-role/anchor` / `cc-role/compute` node
labels, never hostnames (the 2026-08-27 contract) — `setup.sh` preflight
requires exactly one node carrying each label, and a manifest applied against
an unlabeled cluster leaves its pinned/preferred pods `Pending` with no event
naming why.

```bash
sudo k3s kubectl label node <anchor-node-name> cc-role/anchor=true
sudo k3s kubectl label node <compute-node-name> cc-role/compute=true
```

## 4. Apply the manifests

```bash
sudo k3s kubectl apply -f deploy/k3s/
sudo k3s kubectl -n central-command get pods -o wide -w
```

`apply -f <dir>` reads the `*.yaml` files only and is not recursive; the
scripts, units, `sandbox.Dockerfile`, `registries.yaml.example` and the
hand-installed files under `host/` are ignored. Every top-level yaml must
therefore be a Kubernetes manifest (a guard test pins it — v2.39.2 shipped a
k3s drop-in there and the updater died on it).

`apply` also never PRUNES: a resource whose manifest was deleted keeps
running. A removed manifest is therefore tombstoned in
`deploy/k3s/removed.txt` (`<namespace> <kind> <name>`), which `cc-update.sh`
deletes in its manifests phase. `setup.sh` is a first install and does not
read it, and a resource whose manifest was removed without a tombstone is
still there until someone deletes it by hand.

A release that REMOVES a capability names it in
`deploy/k3s/removed-capabilities.txt` (one name per line; `task.create` since v2.70.0). Before
anything stops, `cc-update.sh` reads that file from the *target* tag and refuses
the apply while any proposal of a listed capability is still before a verdict
(`PROPOSED` or `AWAITING_HUMAN`, `contract.enums.PRE_VERDICT_PROPOSAL_STATUSES`)
or a session is parked on `task_wait` (`task.create`), listing the capability, the
proposal ids and the session ids (`scripts/removed_capability_check.py`, run with
the installed tree's venv). It decides nothing: clear the list through the normal
verdicts (approve, reject, dismiss) and apply again. "Update anyway" does not
bypass it, because a proposal stranded under a name no handler knows could never
be decided. Because the update *into* a release runs the installed tree's
updater, the check ships one release before the first entry.

Expected placement once settled:

| Workload | Node | Why | Host exposure |
|---|---|---|---|
| `cc-postgres` | anchor (pinned) | stateful (`cc-pgdata`) | `hostPort` 127.0.0.1:**5442** |
| `cc-litellm-db` | anchor (pinned) | stateful (`cc-litellm-pgdata`) | `hostPort` 127.0.0.1:5443 |
| `cc-litellm-redis` | anchor (pinned) | stateful | none |
| `cc-n8n`, `cc-n8n-db` | anchor (pinned) | stateful | `hostPort` 127.0.0.1:5678 (n8n) |
| `cc-neo4j` | compute (pinned) | stateful (`cc-neo4j-data`) | **ClusterIP only** |
| `cc-vlogs` | compute (pinned) | stateful (`cc-vlogs-data`) | ServiceLB :9428 |
| `cc-pgweb` | anchor (pinned) | stateless zero-login DB UI for the spine | `hostPort` 127.0.0.1:8092 |
| `cc-crawler` | compute (**required**) | Chromium stays off the anchor | ServiceLB :8091 |
| `cc-speech` | compute (**required**) | Speaches (Kokoro TTS + faster-whisper STT) behind the `cc-tts`/`cc-stt` aliases; model cache in `cc-speech-models` | ServiceLB :8093 |
| `cc-litellm` | **floats** (prefers compute) | stateless, the arm64 memory hog | ServiceLB :4000 |
| `cc-fluentbit` | DaemonSet, both nodes | log collection | none |

Pinned is not a preference: `local-path` stamps a *required* hostname
nodeAffinity on every PV, so a pod holding that PVC can never move. Floats carry
30s NoExecute tolerations so a dead node fails over in ~1 min.

Then mint the three namespace-scoped kubeconfigs — **after** the manifests, since
each reads a ServiceAccount token Secret the manifests create:

```bash
./deploy/k3s/make-sandbox-kubeconfig.sh    # -> ~/.cc-sandbox-runner.kubeconfig
./deploy/k3s/make-mcp-kubeconfig.sh        # -> ~/.cc-mcp-deployer.kubeconfig
./deploy/k3s/make-litellm-kubeconfig.sh    # -> ~/.cc-litellm-operator.kubeconfig + ~/.cc-litellm-logreader.kubeconfig
```

If the compute node is freshly built (or you skipped the gVisor prerequisite):

```bash
./deploy/k3s/install-gvisor.sh   # idempotent; with the RuntimeClass now applied it also runs the pod-level proof
```

Put those four paths in the **repo-root `.env`** as `CC_MCP_DEPLOY_KUBECONFIG`,
`CC_LITELLM_KUBECONFIG`, `CC_LITELLM_LOG_KUBECONFIG` (the sandbox one is pinned
in the systemd unit). All 0600, all re-runnable — a token rotation is picked up
by re-running the script, never by editing the file.

## 5. Python and web builds

```bash
cd <checkout>
python3 -m venv .venv && source .venv/bin/activate   # uv-managed CPython 3.12
pip install -e ".[dev,runtime]"
python scripts/apply_graphiti_patches.py             # AFTER every install
(cd web && npm install && npm run build)
```

The knowledge graph's extraction and search run inside the API through
graphiti-core (no Graphiti pod). Two upstream fixes we carry
(`deploy/graphiti-patches/`) are applied to the INSTALLED package by
`scripts/apply_graphiti_patches.py` — after every dependency install, because
an install puts back a pristine copy. It is idempotent and atomic (uv's
hardlinked cache is never written through), and `--check` reports without
changing anything. Without the fixes the API serves graph reads but its
ingest worker refuses extraction (approved episodes stay queued) and the
self-check's `graph-patches` row says why. `setup.sh app` and `cc-update.sh`
(its `rebuild()`, the rollback's included) run it.

## 6. systemd units

Install the units from `deploy/k3s/` only. The retired Compose stack's
`cc-uvicorn.service` (deleted in v2.67.0 with the rest of that stack) had
`ExecStartPre=docker compose up -d`, which resurrects the Docker stack and
fights k3s for 5442.

**`web/.env` must exist before `cc-nerve` starts**, or every cockpit panel
that proxies to the backend (kanban, memories, charter, graph, crons,
activity, skills, decisions, tokens, LOE) 502s with `fetch failed` — the
cc-nerve unit's own header comment says config lives there, but nothing
before this point in the runbook creates the file, and the code default
(`GATEWAY_URL=http://127.0.0.1:18789`, the vendored Nerve gateway's port) is
never Central Command's own API on **8080**. Minimal, at least these two lines:

```bash
cat > web/.env <<'ENVEOF'
PORT=3080
GATEWAY_URL=http://127.0.0.1:8080
ALLOWED_ORIGINS=https://cockpit.example.com
ENVEOF
chmod 600 web/.env
```

`ALLOWED_ORIGINS` is NOT optional if the cockpit is reached over the tailnet:
the WS upgrade checks the browser's Origin header against localhost plus this
list (`web/server/lib/origin-utils.ts`), so without it every tailnet browser
loads the page fine and then fails the WebSocket with a 403 you only see in
the server log (web/.env was once missing from the backup set; the line is
part of the minimum now, so it is three lines, not two).

The attachment limit is `CC_ATTACHMENT_MAX_BYTES` in the root `.env` (default
50 MiB, the cockpit's inline limit `NERVE_UPLOAD_INLINE_ATTACHMENT_MAX_MB` in
`web/.env` defaults to the same). The API refuses a value whose base64 size
would pass its websocket ceiling (`config.WS_MAX_SIZE_BYTES`, 96 MiB, so a cap
of at most 72 MiB), and `cc-uvicorn.service` carries the same number as
`--ws-max-size`. An operator who changes the cap sets the cockpit's limit too.

```bash
sudo cp deploy/k3s/cc-uvicorn.service       /etc/systemd/system/
sudo cp deploy/k3s/cc-sandbox-runner.service /etc/systemd/system/
sudo cp deploy/k3s/cc-graph-bolt.service     /etc/systemd/system/
sudo cp deploy/k3s/cc-backup.service         /etc/systemd/system/
sudo cp deploy/k3s/cc-backup.timer           /etc/systemd/system/
sudo cp deploy/k3s/cc-update.service         /etc/systemd/system/
sudo cp deploy/k3s/cc-update.path            /etc/systemd/system/
sudo cp deploy/k3s/cc-update-stage.service   /etc/systemd/system/
sudo cp deploy/k3s/cc-update-stage.path      /etc/systemd/system/
sudo cp deploy/k3s/cc-nerve.service           /etc/systemd/system/
sudo cp deploy/k3s/cc-update-tmpfiles.conf   /etc/tmpfiles.d/cc-update.conf
sudo systemd-tmpfiles --create /etc/tmpfiles.d/cc-update.conf

sudo systemctl daemon-reload
sudo systemctl enable --now cc-uvicorn cc-nerve cc-sandbox-runner cc-graph-bolt
sudo systemctl enable --now cc-backup.timer cc-update.path cc-update-stage.path
```

- **cc-update** (2026-08-28) — the cockpit's one-click updater. The badge's
  "Apply update now" writes `/run/cc-update/trigger`; `cc-update.path` starts
  the root one-shot `cc-update.service` → `deploy/k3s/cc-update.sh`, which
  version-gates, **prebuilds any locally-built image whose inputs changed
  (cc-sandbox / cc-crawler) while the system is still UP** — a
  failed build then costs no downtime at all — dumps the spine/litellm/n8n DBs
  plus their decryption keys (Neo4j is out of scope — nightly
  `cc-backup.timer` covers it), stops the services, merges the release
  tag, applies schema, rebuilds AS the checkout's owner (venv, cockpit — and, when the
  release ships `scripts/apply_graphiti_patches.py`, graphiti-core's carried
  fixes right after the install), **applies changed `deploy/k3s/`
  manifests and installs changed unit files (`daemon-reload`; a changed
  `cc-update.service` takes effect next run)**, then — before the restart —
  reconciles the app's configuration with the release (v2.59.1): appends each
  `APP_ENV_DEFAULTS` setting the release's `.env.example` declares and `.env`
  lacks (never one already present, an empty "off" included), and widens the
  app key's scope with the release's `mint-keys.sh --scope-only` when it has
  one; restarts, health-checks, and
  **rolls back automatically** if the new version is unhealthy — reset to the
  pre-update tag + rebuild, plus re-applying the checkpoint's manifests and
  retagging each node's preserved `…:pre-update-<stamp>` image back, for
  whatever the forward pass actually touched; the status then reads
  `rolled_back`, so an undone update never reads as succeeded. Status: `/var/lib/cc-update/status.json` (what
  the UI polls); full log: `journalctl -u cc-update`. No sudoers/polkit —
  the web process's only privileged act is writing a file in a tmpfs dir.
- **cc-update-stage** (v2.19.0) — the automatic PREPARE step. The moment a
  version check sees a newer release, the cockpit server writes
  `/run/cc-update/stage-trigger`; `cc-update-stage.path` starts the root
  one-shot stager → `cc-update.sh stage`, which resolves and version-gates
  the same target the apply would and runs every changed image build with
  `CC_BUILD_ONLY=1` — layer caches warm on both nodes, **nothing imported,
  no ref touched, no service stopped**. The cockpit keeps "Apply update now"
  hidden until `/var/lib/cc-update/stage.json` says the target is staged
  (a build failure surfaces there, with a retry button, before anyone
  commits to downtime); the apply then fast-forwards through the warm
  caches. Log: `journalctl -u cc-update-stage`.

**`cc-uvicorn` is enabled AND started here** (2026-09-18). The `app` phase no
longer holds first boot: the onboarding it used to wait for happens in the
cockpit now — a first-run prompt bar asks the operator's name — so there is
nothing to write into the env before the API reads it. (`cc-nerve.service`'s
`Wants=cc-uvicorn.service` was dropped 2026-08-26, when the hold still
mattered: `Wants=` is a START dependency and it brought the API up 40 minutes
early under the rehearsal persona. It stays dropped.)

- **cc-uvicorn** — `Wants=k3s.service` + `After=`. Its `ExecStartPre` is a *readiness
  wait* on 127.0.0.1:5442 (120s ceiling), not a bring-up: Kubernetes is
  declarative, there is nothing to start, only something to wait for.
  **`Wants=`, never `Requires=`, on the three long-running units**
  (`cc-uvicorn`, `cc-sandbox-runner`, `cc-graph-bolt`): with `Requires=`, a
  boot where k3s fails its FIRST start attempt (its network not up yet)
  cancels their start jobs with "Dependency failed", and nothing re-queues
  them when k3s's own retry succeeds a minute later — the cluster is healthy
  and the control plane is dead until someone runs `systemctl start`. Each
  unit retries on its own (`Restart=always`) until the cluster answers. The
  trade: an explicit `systemctl stop k3s` / `restart k3s` no longer stops
  them; they keep running and reconnect. `cc-backup.service` keeps
  `Requires=` — a timer-fired one-shot SHOULD fail when k3s is down.
- **cc-nerve** — serves `web/server-dist` on 127.0.0.1:3080, proxying to the
  backend via `web/.env`'s `GATEWAY_URL` (above). Tailnet exposure is
  `tailscale serve --bg http://127.0.0.1:3080`, which persists in tailscaled
  state on its own. **k3s's bundled Traefik captures port 443 on every node**
  (ServiceLB hostPort DNAT, ahead of tailscaled) — the symptom is a
  "404 page not found" under a `TRAEFIK DEFAULT CERT` on the cockpit URL while
  the other served ports work. Nothing here uses Traefik; disable it with the
  optional drop-in `deploy/k3s/host/20-disable-traefik.yaml` (instructions in
  the file; anchor-node k3s restart).
  The `app` phase reads `tailscale serve status --json` and the node's
  tailnet name to DERIVE the Systems page's "Open →" links (`CC_N8N_UI_URL`,
  `CC_NEO4J_BROWSER_URL`, `CC_SANDBOX_DOCS_URL`, `CC_DB_UI_URL`) into
  the app's `.env` when they are blank (v2.52.0) — add the serve entries
  first, or re-run `./deploy/k3s/setup.sh app` after adding one. A value you
  typed is never overwritten. **The three ServiceLB services need no serve
  entry and must not get one** — LiteLLM (4000), VictoriaLogs (9428) and the
  crawler (8091) are derived as plain `http://<tailnet name>:<port>`. Never
  `tailscale serve` a port a `type: LoadBalancer` Service owns: ServiceLB
  takes the connection first and answers the TLS handshake in plain http
  (`SSL wrong version number` in curl, a dead link in the browser) — the
  Traefik collision above, on another port. A link already set to
  `https://…:8091` or `https://…:9428` fails the self-check's `links` row;
  change it to `http://`, or blank it and re-run the `app` phase. `CC_LLAMA_SWAP_UI_URL` stays hand-typed: the
  compute host is not something this node can see.
- **cc-graph-bolt** — loopback-only `socat` relay of `svc/neo4j` 7687/7474
  (by ClusterIP) for the cockpit's Graph panel. Keeps neo4j ClusterIP-only.
  Needs `apt install socat` on the anchor node (preflight checks). Not
  `kubectl port-forward`: that pins one pod at start and goes stale, silently,
  every time the nightly backup replaces the Neo4j pod (v2.36.1).
- **cc-sandbox-runner** — needs `make-sandbox-kubeconfig.sh` to have run first.
- **cc-backup** — `deploy/k3s/cc-backup.service` runs `deploy/k3s/backup.sh` as
  **root** (k3s's kubeconfig is root-owned 0600; under a non-root `User=` every
  dump fails silently while the timer still reads `active (waiting)`).
  Check it: `systemctl list-units --failed` and `journalctl -u cc-backup -n 50`.

**Releases go live through the updater, never a hand restart.** The cockpit's
"Apply update now" (or `deploy/k3s/cc-update.sh`) fetches the tag, rebuilds the
venv and the cockpit, applies manifests and units, restarts and health-checks,
and rolls back on failure; a bare `systemctl restart` skips every one of
those. The cockpit is never updated by `git pull` plus `npm run build` on a
running checkout, and a coding agent's session never restarts a live `cc-*`
unit or moves the live checkout's branch — a merge or reset there is a
deployment, armed for the next restart. Restarting by hand is the operator's
act, for a change the updater does not carry, such as an edited `.env`:
`sudo systemctl restart cc-uvicorn` (backend) or `cc-nerve` (cockpit).

## 7. Fresh-database facts

**Skills are not in the schema — the `app` phase imports them** (v2.64.0).
Once the API answers, `setup.sh app` posts every `skills/<id>/SKILL.md` folder
this release ships to the API's own `POST /api/skills/import`. It is
create-only: a skill the library already holds, retired ones included, is
never re-imported, even when the release changed the bundled copy. Re-running
the phase on an existing install adds only the bundled skills the library
lacks. The updater does not run this step; after an update that adds a
bundled skill, run `./deploy/k3s/setup.sh app`.

Each skill's `default_agents` front-matter names the agents that hold it; the
import grants those CREATE-ONLY (a revoked grant is never restored; unknown
agent ids are reported, not fatal) and the step prints how many it applied.
Further grants are made in the cockpit's Agents view.

`cc-postgres` mounts the `cc-schema-sql` ConfigMap at
`/docker-entrypoint-initdb.d`. Postgres runs it **only when the data directory
is empty** — i.e. only on a fresh `cc-pgdata` PVC. After that the updater
applies `schema.sql` to the live database at each release (§6), before the new
code starts; the API never applies it at process start.

So a clean install boots with the **founding roster at v0 charters** (the seeds
in `central_command/db/schema.sql`, plus the founding capability grants and the
founding schedules, seeded DISABLED). That is the product baseline, not the operator's
instance.

Corollary for development: an additive column added to `schema.sql` reaches the
live database with the release that carries it. Code run against a live
database before that release must have the column applied by hand in the same
change — the test DB re-executes the file every run, so the suite stays green
while the live database throws an undefined-column error that reads like a
code bug.

## 8. INSTANCE DATA — what a clean install does NOT restore

Everything in §§1–7 rebuilds the **system**. None of it restores **this
instance's** accumulated data. Decide each of these deliberately.

Backups live in the backup directory (`CC_BACKUP_DIR` in `deploy/k3s/.env`,
by default `cc-backups` in the home of the account that owns the checkout;
0600 in a 0700 dir, outside the repo). Full commands are in the **RESTORE section at the bottom of
`deploy/k3s/backup.sh`** — read it there, it is the authority.

1. **n8n workflows + the encrypted Gmail OAuth credential — REQUIRED for the
   Gmail email feed.** Nothing else holds the Gmail OAuth. (With the native
   Exchange provider configured, the mailbox credentials live in the app's
   `.env` instead and n8n is not on the mail path; see `docs/QUEUE.md`.) Either restore
   `n8n_<stamp>.sql.gz` into `cc-n8n-db` (scale `cc-n8n` to 0 first; it holds
   workflow rows open), or set it up fresh: open the n8n UI
   (`tailscale serve --bg --https=8443 http://127.0.0.1:5678`), create the
   Gmail OAuth2 credential named exactly **`Gmail account`** (and **`Google
   Calendar account`** for the calendar façade), then apply the shipped façade
   workflows with `./deploy/n8n/apply-workflows.sh --k3s` (`deploy/n8n/README.md`;
   the `app` phase runs it once with freshly generated façade tokens, v2.51.0,
   and the updater re-applies them on every release that changes them). The restore only works if
   `N8N_ENCRYPTION_KEY` in `deploy/k3s/.env` is the value the dump was taken
   under; otherwise the rows are there and undecryptable — pair the dump with
   its **same-stamp `keys_<stamp>.env`**, not with whatever is in `.env` now.

   Two traps, both hit live 2026-08-26, both silent — the full block is in
   backup.sh's RESTORE section:
   - **The newest n8n dump may be an EMPTY one.** The nightly backup runs
     unconditionally, so a post-wipe instance gets dumped too. Count the
     workflow rows inside the dump before trusting it:
     `gunzip -c n8n_<stamp>.sql.gz | awk '/^COPY public.workflow_entity/,/^\.$/' | wc -l`
     (~2 means empty). Pick by content, not by date.
   - **The fresh instance's first boot poisons the PVC.** n8n stamps its own
     generated key into `/home/node/.n8n/config` on `cc-n8n-data`, and that file
     beats the env var, so restoring under the old key CrashLoops with
     "Mismatching encryption keys" until it is deleted — scale `cc-n8n` to 0 and
     `rm` it via a busybox helper pod mounting the PVC.
2. **The knowledge graph — optional.** A fresh instance starts with an empty
   graph by design and works fine. To carry it over, restore
   `neo4j_<stamp>.dump.gz` via the dumper-pod cycle in backup.sh's RESTORE
   section (compute node, offline load, `--overwrite-destination=true`). The
   `chown -R 7474:7474 /data` afterwards is **not optional** — the dumper runs
   as root, the neo4j pod as 7474, and root-owned store files fail to start with
   an error that reads like corruption.
3. **Coached charter versions, the email ledger, proposals, the event log —
   NOT part of a clean install.** They live in the `central_command` dump. Restoring
   it is a **full rollback to the previous instance**, not a step here; doing it
   discards the point of the clean install (v0 charters). Only do it if that is
   the decision.
4. **LiteLLM virtual keys and DB-registered models** ride in `cc-litellm-db`,
   which this procedure does not touch. If it is ever rebuilt, the
   `litellm_<stamp>.sql.gz` restore needs the matching `LITELLM_SALT_KEY`.

**Leftovers a fully-clean slate must also PURGE** (all gitignored or outside
the repo, so a manifest/env wipe misses every one — found the hard way,
2026-08-25, when a fresh /setup session tripped over another profile's stale
answers):

- The minted kubeconfigs: `~/.cc-litellm-logreader.kubeconfig`,
  `~/.cc-litellm-operator.kubeconfig`, `~/.cc-mcp-deployer.kubeconfig`,
  `~/.cc-sandbox-runner.kubeconfig`. Their tokens die with the namespaces but
  the files keep looking valid; §4 re-mints them.
- The OTHER profile's litter in a shared checkout — SHRINKING as of v2.42.0,
  which moved the single-node profile's answers into the repo-root `.env` and
  everything it generates into `$CC_STATE_DIR`, outside the tree. What may
  still be there from before that release: `deploy/single/.env`,
  `deploy/single/{secrets,stack,stack-llm,optional-*}.yaml`,
  `deploy/single/setup-diagnostics.txt`. Back `deploy/single/.env` up to
  the backup directory (above) first — it may hold that profile's never-rotate keys.
- The instance env files (`.env`, `web/.env`, `deploy/k3s/.env`) — usually
  already backed up and removed by the teardown, but verify rather than
  assume.
- **A leftover executor drop-in**: `/etc/systemd/system/cc-uvicorn.service.d/`
  (e.g. `executor-live.conf`). A drop-in may not name `CC_TEST_MODEL`,
  `CC_DEMO_MODE` or `CC_EXECUTOR_MODE`: the API refuses to start on one that
  does (whatever the value), and `verify.sh` fails it over the running
  process's environment. Such a drop-in lives outside the repo AND the cluster
  and survives every teardown, so check for it.

## 8a. Starting over on the same cluster — `setup.sh reset`

To test the install as a new operator would see it — the name prompt, the team
tour, the first mail, an empty graph — without rebuilding the cluster:

```bash
./deploy/k3s/setup.sh reset                  # prints the plan, changes nothing
./deploy/k3s/setup.sh reset --confirm-wipe   # does it
./deploy/k3s/setup.sh --clean-install        # installs on the empty stores
```

`reset` is destructive and is never part of the no-argument run. It is the
operator's command: an agent conducting a setup does not run it.

| | |
|---|---|
| **Deleted** | the spine database (`cc-pgdata`: roster, charters, grants, schedules, mail ledger, proposals, sessions, event log, the operator's name) and the knowledge graph (`cc-neo4j-data`). Both come back empty; the spine reloads `schema.sql` (§7). |
| **Removed** | what agents deployed: MCP servers in `cc-mcp`, sandbox jobs in `cc-sandbox`, and the untracked folders under `servers/` (moved into the keep folder, not deleted). |
| **Moved aside** | the app's `.env` and `web/.env`, so the next run starts from `.env.example`. `--keep-env` leaves them in place — that is a data-only reset, and it will not show you what the installer forgets to ask. |
| **Kept** | the cluster, node labels, images, gVisor; the LiteLLM database (models, virtual keys, spend); the n8n database (the Gmail and Calendar OAuth); `deploy/k3s/.env` (`LITELLM_SALT_KEY`, `N8N_ENCRYPTION_KEY`); the logs; the systemd units. |

The order is the safety: `backup.sh` runs first, and nothing is stopped or
deleted unless it exits 0 and leaves all four dumps, the key file and the three env-file copies (`env_`, `env-app_`, `env-web_`). That set
is hard-linked into `keep-pre-reset-<stamp>/` under the backup directory —
the nightly retention prunes top-level files only, so it never ages out — and
the moved env files land in its `env/` folder. Rolling back is §10 against
that folder. `make-secrets.sh` then rebuilds the ConfigMaps, so the empty spine
loads this checkout's `schema.sql` and not an older copy held in the cluster.
Then the API, the sandbox runner and the cockpit are stopped; each store is
scaled to 0, its dead pods are removed (a pod in a terminal phase still counts
as a user of the claim, and would hold its deletion forever), its volume is
deleted and recreated from the manifest; and the phase
asserts both stores are first-run ones (seeded roster, empty mail ledger, zero
graph nodes) before it moves the env files.

It leaves the app **stopped**. `./deploy/k3s/setup.sh --clean-install` starts
it: the `llm` phase finds the models already registered and re-mints the app's
key (the old one left with the old `.env`), and the `app` phase generates new
façade tokens, re-applies the n8n workflows and imports the bundled skills. To give answers the installer
does not ask for before first boot, create the file first — `cp .env.example
.env && chmod 600 .env`, then fill `CC_JIRA_*` / `CC_CONFLUENCE_*` and
`CC_BACKLOG_CUTOFF_DATE` (the old values are in the keep folder's
`env/root.env`).

What it does not reach:

- **The outside world.** Mail an approved proposal reported as spam stays out
  of the inbox, and issues created in Jira stay created. The mail feed decides
  "already seen" from the spine alone, so everything still in the inbox inside
  the backlog window is enrolled again.
- **A systemd drop-in** in `/etc/systemd/system/cc-uvicorn.service.d/` (§8). The
  phase names any it finds in a WARN; it does not remove them.
- **A second run.** Re-running `reset` after a partial failure is safe: a store
  the failed run left at 0 replicas is started again so the backup can dump
  it. If the failure came after a store was already emptied, that run's
  backup dumps the empty store — the dump set worth keeping is the FIRST
  `keep-pre-reset-*` folder, by content, not by date.

## 9. Verification

The API is already up — §6 both enabled and started `cc-uvicorn` (its
`ExecStartPre` waits on 127.0.0.1:5442), and there is no onboarding to land
before it reads its env. Onboarding happens in the cockpit, in two steps:
the first-run prompt bar asks the operator's name, then **Crons → "Team tour
(onboarding)" → Run now** — the schedule is seeded disabled by `schema.sql`
(v2.51.0), its first run hires the EA, and the EA hosts the introductions. So
this section is verification only:

```bash
./deploy/k3s/verify.sh --clean-install   # the clean-install gate: 0 failures
```

Sections: services answering, stores initialized, placement matching the
design, every `CC_*` endpoint in the root `.env` resolving to loopback, the
run-mode guard over the running API's environment, and, last (section F), the
**application self-check** — `python -m central_command.selfcheck` run from the
checkout's `.venv` under the minimal environment systemd gives the API, so it
proves the install as the application sees it. It spends two small model
requests and runs on EVERY `verify.sh` run; under `--clean-install` it is
skipped with a line saying why (the model catalog is not set up yet). A WARN
line passes with its text, a FAIL line fails the run. It is readiness, never
liveness: the updater's health poll and no timer run it.
**0 failures is the gate — in `--clean-install` mode there is no expected red.**

Section A also probes **Jira and Confluence** (v2.53.0) — the one off-box
dependency it asserts, because a wrong or expired Atlassian token is invisible
until an agent tries to work. It runs `scripts/atlassian_probe.py --quiet` with
the checkout's `.venv` against the root `.env`'s `CC_JIRA_*` / `CC_CONFLUENCE_*`
and reprints any FAIL line verbatim (the probe scrubs tokens and emails itself).
A blank `CC_JIRA_BASE_URL` is a **skip**, not a failure: no Jira is a valid
answer. `setup.sh validate` reports the same answers' SHAPE before anything is
deployed — a blank base URL is a `PASS` that states the consequence (no Jira
is a valid answer, and validate is the first phase of the `all` chain, so it
must never stop there), an incomplete credential set is a `FAIL`. The phase
mutates nothing; the live probe is verify's.

The flag changes only section **B**: it asserts the stores are alive,
authenticated and schema-loaded (roster seeded, event log queryable, neo4j
answering cypher) instead of asserting *migrated instance data* (`audit_event >
100`, the n8n façade active, the Gmail credential decrypting, graph non-empty).
The two n8n instance checks are printed as `skip`. After the §8 restore
choices, run it again **without** the flag — that is the full assertion
suite and the steady-state check (nothing runs it on a timer; run it after
any change).

Quick smoke checks:

```bash
curl -fsS http://127.0.0.1:4000/health/liveliness   # litellm
.venv/bin/python scripts/apply_graphiti_patches.py --check   # graph fixes (from the checkout)
curl -fsS http://127.0.0.1:8080/health              # control plane API
curl -fsS http://127.0.0.1:5678/healthz             # n8n
sudo k3s kubectl -n central-command get pods -o wide    # placement, all Running 1/1
```

Cockpit: `https://<cockpit-host>` (the `tailscale serve` front for port 3080) ·
logs: `http://<tailnet-name>:9428/select/vmui/` (plain http — a ServiceLB port,
§6). The real hosts are in your private instance docs.

### Sandbox reach drill

The sandbox is containment, not restriction: its egress is open on purpose
(`60-sandbox.yaml`), so what keeps an agent's code off the control plane is
that the API and the cockpit listen on loopback only and nothing publishes
them. `tests/test_sandbox_isolation.py` pins the shipped text that makes that
true (every bind is `127.0.0.1`, the Job manifest holds no credential and no
host network, no manifest exposes port 8080). It cannot prove the live
network, so this drill does. It is **never run by pytest**: the release story
runs it once after an apply, with the operator's OK, and the cc-test-sweep
skill points here. It changes nothing and leaves nothing behind.

Run it from the anchor node. Take `<anchor-address>` from the node's address
as the cluster reports it, and `<cockpit-tailnet-url>` from
`tailscale serve status` (the https front of port 3080 — it proxies `/api` to
the loopback API without authentication, so it is a target in its own right).
`<curl-image>` is any image a node already holds that carries `curl`.

```bash
sudo k3s kubectl -n cc-sandbox run reach-drill --image=<curl-image> \
  --overrides='{"spec":{"runtimeClassName":"gvisor","automountServiceAccountToken":false}}' \
  --command -- sleep 1800
sudo k3s kubectl -n cc-sandbox wait --for=condition=Ready pod/reach-drill --timeout=180s
```

Then try each of the three targets from inside the pod, one command each:

```bash
sudo k3s kubectl -n cc-sandbox exec reach-drill -- curl -sS -m 10 -o /dev/null -w '%{http_code}\n' http://<anchor-address>:8080/health
sudo k3s kubectl -n cc-sandbox exec reach-drill -- curl -sS -m 10 -o /dev/null -w '%{http_code}\n' http://<anchor-address>:3080/health
sudo k3s kubectl -n cc-sandbox exec reach-drill -- curl -sS -m 10 -o /dev/null -w '%{http_code}\n' <cockpit-tailnet-url>/api/heartbeat
```

**Expected: all three fail to connect** — `Connection refused`, a timeout, or
`Could not resolve host` (curl exits non-zero and prints no status). An HTTP
status from any of them is a finding: stop, leave the pod for inspection, and
report which target answered. Whatever the outcome, delete the pod:

```bash
sudo k3s kubectl -n cc-sandbox delete pod reach-drill
```

## 10. Rollback

- **Data:** the RESTORE section at the bottom of `deploy/k3s/backup.sh`, against
  the **pre-clean-install** dump set in `$CC_BACKUP_DIR` (take one
  before wiping: `sudo <checkout>/deploy/k3s/backup.sh`, and
  confirm the `keys_<stamp>.env` beside it — without those two keys the dumps
  are ciphertext). The same run leaves byte copies of the three env files
  beside the dumps (`env_`, `env-app_` and `env-web_<stamp>.env`, 0600, for
  `deploy/k3s/.env`, the root `.env` and `web/.env`); backup.sh's RESTORE
  section says where each goes back.
- **Whole stack:** the pre-k3s Docker Compose stack was deleted from the tree
  in v2.67.0. Reach it at v2.65.3 (the retired stack's bytes are identical at
  every tag from v2.65.3 through v2.66.2; a fallback should point at neither a
  bridge release nor a patch cut while the split was under way):
  `git show v2.65.3:deploy/pi/docker-compose.yml` reads the compose file, and `git archive v2.65.3 deploy/pi | tar -x` from the checkout root
  restores the whole directory, `cc-uvicorn.service` included — the compose
  file expects `litellm/config.yaml` and a `.env` beside it, and the unit
  hard-codes that path. The archive carries only `.env.example`, and the
  compose file `:?`-requires six variables from that `.env`, so copy
  `deploy/k3s/.env` to `deploy/pi/.env` before the first `docker compose up`
  — without it the fallback stops at its first command. (If §0b's symlink is
  still in place, `deploy/pi/.env` already resolves to `deploy/k3s/.env` and
  serves the same bytes; `cp` onto it only reports "same file" — leave it.)
  It had already drifted
  dead before its deletion: it runs postgres, LiteLLM, Neo4j and n8n only — no
  Graphiti, crawler, speech or reranker — so it restores a database host, not
  the product. It fights k3s for 5442 — stop the cluster workloads first, and
  only do this deliberately.
