# deploy/k3s — clean-install runbook

From bare cluster to a running Central Command. Written for a human to follow top to
bottom, on the Pi, in a terminal. Every command is copy-pasteable and every
`apply`/`make-*` step is idempotent, so a re-run reconciles rather than breaks.

Context for *why* the two-node split looks like this: `CLAUDE.md` → "Where it
runs". Live state (counts, roster, what's in the queue): the instance repo's
`STATUS.md`. This file does not repeat either.

---

## 1. Prerequisites & assumptions

Assumed already true before phase 2:

- **k3s v1.34+** installed: `raspberrypi` (arm64, control plane) and `chromebox`
  (amd64, agent). `sudo k3s kubectl get nodes` shows both `Ready`.
- **Tailscale** up on both; the Pi can `ssh chromebox_admin@100.113.118.28`
  without a password prompt (every build script shells over that).
- Repo cloned at **`/home/codyslab/Central Command`**.
- **`deploy/pi/.env`** populated — run **`./deploy/k3s/init-env.sh`**: it copies
  the template, generates every **GENERATED** variable that is empty, writes
  `PENDING` into the three Graphiti virtual-key slots (they cannot exist before
  the proxy does), and prints the **ELICITED** ones you must supply yourself. It
  never overwrites a value that is already set, so it is safe to re-run and safe
  on a restore. It is the single source of truth for container secrets.
  `LITELLM_SALT_KEY` /
  `N8N_ENCRYPTION_KEY` must carry the **existing** values whenever the matching
  database is being restored — a restore under a different key leaves the rows
  present and undecryptable. Generate them only on a fully-clean deployment that
  restores neither.
- **Repo-root `.env`** populated (see `.env.example`). Different audience: the
  app's own `CC_*` settings. Both files are gitignored.
- **uv-managed CPython 3.12** and **NodeSource 22** on the Pi.
- **Docker CE** still installed on the Pi — `build-graphiti-image.sh` builds the
  arm64 half with `docker`. (The compose *stack* is superseded; the daemon is
  still the arm64 builder.)
- **gVisor (`runsc`) on the chromebox** — installed by
  `./deploy/k3s/install-gvisor.sh` (idempotent: reports-and-exits when already
  present). Without it, sandbox Jobs never start — the `gvisor` RuntimeClass
  applies fine and the failure only shows at first use, which is why verify.sh
  asserts it too. Run the script again after phase 4 for its pod-level proof.
- **The LiteLLM trio** (`cc-litellm`, `cc-litellm-db`, `cc-litellm-redis`) is
  either already running or absent, and both are fine.
  - **Already running:** leave it. Every step below is safe against it —
    `make-secrets.sh` rewrites their Secret with the same values, and
    re-applying `30-litellm.yaml` is a no-op when nothing changed.
  - **Absent (a fully-clean deployment):** phase 4 brings it up fresh, on an
    empty `cc-litellm-pgdata`. That means two rebuilds, in this order.

    **1. An empty model catalog.** `store_model_in_db: true`, so every model
    lives in that database and NOT in `deploy/pi/litellm/config.yaml`. Rebuild
    it from the tracked declaration — no hand-composed `POST /model/new`:

    ```bash
    python3 deploy/pi/litellm/register-models.py --dry-run   # read the plan
    python3 deploy/pi/litellm/register-models.py             # apply it
    python3 deploy/pi/litellm/policy.py --apply              # routing policy
    sudo k3s kubectl -n central-command rollout restart deploy/cc-litellm
    sudo k3s kubectl -n central-command rollout status deploy/cc-litellm --timeout=120s
    python3 deploy/pi/litellm/policy.py --check              # proves parity
    ```

    The restart is required, not optional: the adaptive router reads member
    preferences only when constructed (`policy.py --apply` prints this same
    warning), so skipping it leaves `/adaptive_router/state` stale even though
    `--check` may still pass.

    `model-preferences.yaml` is the authoritative list of all eleven, and it is
    now actually able to drive registration: each entry carries a
    `registration:` block with `litellm_params.model` (routing prefix included),
    `api_base` and, for the four `registration_only:` entries, `timeout`. The
    two facts most easily got wrong live in that file's comments — `graphiti-llm`
    needs the `openai/chat_completions/` Responses→chat bridge prefix, and
    `qwen3-rerank-local`'s **`api_base` must end in `/v1/rerank`** (the `cohere/`
    client POSTs the base verbatim and appends nothing). Timeouts have exactly
    one home per model, which is what makes `policy.py --check` a real gate
    rather than a coin flip. `policy.py --apply` still only *annotates* models
    that already exist — register first, always.

    **2. A fresh master key**, so every downstream virtual key must be re-minted
    against it before it will authenticate:

    ```bash
    ./deploy/k3s/mint-keys.sh    # 3 graphiti keys + CC_LLM_API_KEY, then
                                 # re-runs make-secrets.sh + restarts cc-graphiti
    ```

    Mint **after** registering — each key is scoped to model groups that have to
    exist. Idempotent: a value that is set and not `PENDING` is kept (`FORCE=1`
    to override).
- Air-gapped site only: copy `registries.yaml.example` to
  `/etc/rancher/k3s/registries.yaml` on **both** nodes first, fill `MIRROR_HOST`,
  then `systemctl restart k3s` (Pi) / `k3s-agent` (chromebox).

## 2. Secrets and ConfigMaps

```bash
cd /home/codyslab/Central Command
./deploy/k3s/init-env.sh        # bootstraps deploy/pi/.env (idempotent)
./deploy/k3s/make-secrets.sh
```

`init-env.sh` is the bootstrap; `make-secrets.sh` reads what it produced.
`PENDING` in the three Graphiti key slots is expected here and passes — those
are minted in §1's step 2, after the proxy is up.

Creates the namespace, five Secrets (`cc-litellm`, `cc-neo4j`, `cc-graphiti`,
`cc-n8n`, and `cc-crawler-llm` if `CC_LLM_API_KEY` is set) and three ConfigMaps
(`cc-litellm-config`, `cc-graphiti-config`, `cc-schema-sql`) **from
`deploy/pi/.env`**. Imperative on purpose: no base64'd secret ever lands in the
repo. Idempotent (`--dry-run=client | apply`).

Run this **before** phase 4 — pods that reference a missing Secret sit in
`CreateContainerConfigError`, not `Pending`, and it reads like a broken image.

## 3. Locally-built images

Three images are built here, not pulled. Everything else is multi-arch upstream.

```bash
./deploy/k3s/build-graphiti-image.sh    # BOTH nodes: arm64 (docker, Pi) + amd64 (podman, chromebox)
./deploy/k3s/build-sandbox-image.sh     # chromebox only
./deploy/k3s/build-crawler-image.sh     # chromebox only — slow, installs Chromium
```

- `cc-graphiti` **floats**, so it must exist on *both* nodes under the exact ref
  `docker.io/library/cc-graphiti:1.0.2-anthropic`. A missing copy is invisible
  until the day the pod actually moves.
- `cc-sandbox` and `cc-crawler` are chromebox-**required** (gVisor and Chromium
  live there), so they are single-arch by design.
- **The `localhost/` trap:** podman tags local builds `localhost/<name>`; the
  kubelet looks up `docker.io/library/<name>` and then tries to pull a tag no
  registry serves — `ImagePullBackOff` on that node only. The scripts tag with
  the fully-qualified ref and verify with `grep -qx` on a *captured* list; each
  one exits non-zero if the ref is missing. Believe that check.
- Builds are native — **no QEMU**. The graphiti build ships the context to the
  chromebox and builds there.
- The `-anthropic` in the tag is a misnomer (Graphiti's LLM is the local Qwen).
  Keep it: renaming means rebuilding and re-importing on both nodes for nothing.

## 4. Apply the manifests

```bash
sudo k3s kubectl apply -f deploy/k3s/
sudo k3s kubectl -n central-command get pods -o wide -w
```

`apply -f <dir>` reads the `*.yaml` files only; the scripts, units,
`sandbox.Dockerfile` and `registries.yaml.example` are ignored.

Expected placement once settled:

| Workload | Node | Why | Host exposure |
|---|---|---|---|
| `cc-postgres` | raspberrypi (pinned) | stateful (`cc-pgdata`) | `hostPort` 127.0.0.1:**5442** |
| `cc-litellm-db` | raspberrypi (pinned) | stateful (`cc-litellm-pgdata`) | `hostPort` 127.0.0.1:5443 |
| `cc-litellm-redis` | raspberrypi (pinned) | stateful | none |
| `cc-n8n`, `cc-n8n-db` | raspberrypi (pinned) | stateful | `hostPort` 127.0.0.1:5678 (n8n) |
| `cc-neo4j` | chromebox (pinned) | stateful (`cc-neo4j-data`) | **ClusterIP only** |
| `cc-vlogs` | chromebox (pinned) | stateful (`cc-vlogs-data`) | ServiceLB :9428 |
| `cc-crawler` | chromebox (**required**) | Chromium stays off the Pi | ServiceLB :8091 |
| `cc-litellm` | **floats** (prefers chromebox) | stateless, the arm64 memory hog | ServiceLB :4000 |
| `cc-graphiti` | **floats** (prefers chromebox) | stateless | ServiceLB :8000 |
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

If the chromebox is freshly built (or you skipped the gVisor prerequisite):

```bash
./deploy/k3s/install-gvisor.sh   # idempotent; with the RuntimeClass now applied it also runs the pod-level proof
```

Put those four paths in the **repo-root `.env`** as `CC_MCP_DEPLOY_KUBECONFIG`,
`CC_LITELLM_KUBECONFIG`, `CC_LITELLM_LOG_KUBECONFIG` (the sandbox one is pinned
in the systemd unit). All 0600, all re-runnable — a token rotation is picked up
by re-running the script, never by editing the file.

## 5. Python and web builds

```bash
cd /home/codyslab/Central Command
python3 -m venv .venv && source .venv/bin/activate   # uv-managed CPython 3.12
pip install -e ".[dev,runtime]"
(cd web && npm install && npm run build)
```

## 6. systemd units

Install from `deploy/k3s/`, **not** `deploy/pi/`. The old
`deploy/pi/cc-uvicorn.service` has `ExecStartPre=docker compose up -d`, which
resurrects the Docker stack and fights k3s for 5442.

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
ALLOWED_ORIGINS=https://raspberrypi.tail2ae84b.ts.net
ENVEOF
chmod 600 web/.env
```

`ALLOWED_ORIGINS` is NOT optional if the cockpit is reached over the tailnet:
the WS upgrade checks the browser's Origin header against localhost plus this
list (`web/server/lib/origin-utils.ts`), so without it every tailnet browser
loads the page fine and then fails the WebSocket with a 403 you only see in
the server log (found 2026-08-26 — the old instance's web/.env had the line
and web/.env was never in the backup set; it is now three lines, not two).

```bash
sudo cp deploy/k3s/cc-uvicorn.service       /etc/systemd/system/
sudo cp deploy/k3s/cc-sandbox-runner.service /etc/systemd/system/
sudo cp deploy/k3s/cc-graph-bolt.service     /etc/systemd/system/
sudo cp deploy/k3s/cc-backup.service         /etc/systemd/system/
sudo cp deploy/k3s/cc-backup.timer           /etc/systemd/system/
sudo cp deploy/pi/cc-nerve.service           /etc/systemd/system/

sudo systemctl daemon-reload
sudo systemctl enable cc-uvicorn                     # enabled, NOT started — see below
sudo systemctl enable --now cc-nerve cc-sandbox-runner cc-graph-bolt
sudo systemctl enable --now cc-backup.timer
```

**`cc-uvicorn` is enabled here but started in §9**, after the onboarding
interview has written the instance's env — the API reads it once at start, so a
first boot before the interview runs the whole app under stale answers. Starting
`cc-nerve` used to start it anyway: `cc-nerve.service` carried
`Wants=cc-uvicorn.service`, and `Wants=` is a START dependency. That was dropped
2026-08-26 (it brought the API up 40 minutes early and hired an agent under the
rehearsal persona), so the hold now actually holds. `enable` without `--now`
still means the unit comes up on every subsequent boot.

- **cc-uvicorn** — `Requires=k3s.service`. Its `ExecStartPre` is a *readiness
  wait* on 127.0.0.1:5442 (120s ceiling), not a bring-up: Kubernetes is
  declarative, there is nothing to start, only something to wait for.
- **cc-nerve** — serves `web/server-dist` on 127.0.0.1:3080, proxying to the
  backend via `web/.env`'s `GATEWAY_URL` (above). Tailnet exposure is
  `tailscale serve --bg http://127.0.0.1:3080`, which persists in tailscaled
  state on its own.
- **cc-graph-bolt** — loopback-only `port-forward` of `svc/neo4j` 7687 for the
  cockpit's Graph panel. Keeps neo4j ClusterIP-only.
- **cc-sandbox-runner** — needs `make-sandbox-kubeconfig.sh` to have run first.
- **cc-backup** — `deploy/k3s/cc-backup.service` runs `deploy/k3s/backup.sh` as
  **root** (k3s's kubeconfig is root-owned 0600; under `User=codyslab` every
  dump fails silently while the timer still reads `active (waiting)`).
  Check it: `systemctl list-units --failed` and `journalctl -u cc-backup -n 50`.

**A merged change is not live until its process restarts:** backend →
`sudo systemctl restart cc-uvicorn`; cockpit → `(cd web && npm run build)` then
`sudo systemctl restart cc-nerve`.

## 7. Fresh-database facts

`cc-postgres` mounts the `cc-schema-sql` ConfigMap at
`/docker-entrypoint-initdb.d`. Postgres runs it **only when the data directory
is empty** — i.e. only on a fresh `cc-pgdata` PVC. Nothing applies `schema.sql`
at runtime, ever.

So a clean install boots with the **founding roster at v0 charters** (the seeds
in `central_command/db/schema.sql`, plus the founding capability grants and the
founding schedules, seeded DISABLED). That is the product baseline, not the operator's
instance.

Corollary for later: an additive column added to `schema.sql` must be applied by
hand to the running database in the same change — the test DB re-executes the
file every run, so the suite stays green while the live Pi throws an
undefined-column error that reads like a code bug.

## 8. INSTANCE DATA — what a clean install does NOT restore

Everything in phases 1–7 rebuilds the **system**. None of it restores **this
instance's** accumulated data. Decide each of these deliberately.

Backups live in `/home/codyslab/cc-backups` (0600 in a 0700 dir, outside the
repo). Full commands are in the **RESTORE section at the bottom of
`deploy/k3s/backup.sh`** — read it there, it is the authority.

1. **n8n workflows + the encrypted Gmail OAuth credential — REQUIRED for the
   email feed.** Nothing else holds the Gmail OAuth. Either restore
   `n8n_<stamp>.sql.gz` into `cc-n8n-db` (scale `cc-n8n` to 0 first; it holds
   workflow rows open), or reconfigure by hand in the n8n UI —
   `tailscale serve --bg --https=8443 http://127.0.0.1:5678`, then re-create the
   Gmail credential and re-activate `cc-email-facade`. The restore only works if
   `N8N_ENCRYPTION_KEY` in `deploy/pi/.env` is the value the dump was taken
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
   section (chromebox, offline load, `--overwrite-destination=true`). The
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
- The OTHER profile's litter in a shared checkout: `deploy/single/.env`,
  `deploy/single/{secrets,stack,stack-llm,optional-*}.yaml`,
  `deploy/single/setup-diagnostics.txt`. Back `deploy/single/.env` up to
  `~/cc-backups/` first — it may hold that profile's never-rotate keys.
- The instance env files (`.env`, `web/.env`, `deploy/pi/.env`) — usually
  already backed up and removed by the teardown, but verify rather than
  assume.

## 9. Verification

First boot of the API — §6 enabled `cc-uvicorn` without starting it, so the
onboarding interview's answers are already in the env it reads:

```bash
sudo systemctl start cc-uvicorn          # ExecStartPre waits on 127.0.0.1:5442
./deploy/k3s/verify.sh --clean-install   # the clean-install gate: 0 failures
```

Four sections: services answering, stores initialized, placement matching the
design, and every `CC_*` endpoint in the root `.env` resolving to loopback.
**0 failures is the gate — in `--clean-install` mode there is no expected red.**

The flag changes only section **B**: it asserts the stores are alive,
authenticated and schema-loaded (roster seeded, event log queryable, neo4j
answering cypher) instead of asserting *migrated instance data* (`audit_event >
100`, the n8n façade active, the Gmail credential decrypting, graph non-empty).
The two n8n instance checks are printed as `skip`. After the phase-8 restore
choices, run it again **without** the flag — that is the full 32-assertion
form and the steady-state nightly truth.

Quick smoke checks:

```bash
curl -fsS http://127.0.0.1:4000/health/liveliness   # litellm
curl -fsS http://127.0.0.1:8000/health              # graphiti MCP
curl -fsS http://127.0.0.1:8080/health              # control plane API
curl -fsS http://127.0.0.1:5678/healthz             # n8n
sudo k3s kubectl -n central-command get pods -o wide    # placement, all Running 1/1
```

Cockpit: https://raspberrypi.tail2ae84b.ts.net · logs:
https://raspberrypi.tail2ae84b.ts.net:9428/select/vmui/

## 10. Rollback

- **Data:** the RESTORE section at the bottom of `deploy/k3s/backup.sh`, against
  the **pre-clean-install** dump set in `/home/codyslab/cc-backups` (take one
  before wiping: `sudo /home/codyslab/Central Command/deploy/k3s/backup.sh`, and
  confirm the `keys_<stamp>.env` beside it — without those two keys the dumps
  are ciphertext).
- **Whole stack:** the Docker Compose stack in `deploy/pi/` is the documented
  fallback (`docker compose -f deploy/pi/docker-compose.yml up -d` plus the old
  `deploy/pi/cc-uvicorn.service`). It fights k3s for 5442 — stop the cluster
  workloads first, and only do this deliberately.
