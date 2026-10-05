---
paths:
  - "deploy/k3s/**"
---

# k3s deployment bite marks

Rules below exist because a real failure produced them. Trust the rule even
where the story is gone. Moved verbatim from the root instructions; they load
when a matching file is read.

- **`deploy/k3s/`** — the multi-node reference deployment (two-node k3s:
  an arm64 "anchor" node and an amd64 "compute" node). `README.md` there is
  the clean-install runbook the /setup skill conducts phase-by-phase;
  `setup.sh` is the driver, `verify.sh` the assertion suite, `cc-update.sh`
  the one-click updater covering the full release surface (code, schema,
  manifests, unit files, locally-built images, with rollback).
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
  (No floating pod runs a local image today — the Graphiti server was the one,
  and it left; the rule stands for the next.)
- **In a unit's `ExecStart=/bin/sh -c`, `A && B & exec C` backgrounds A and B
  together.** `&` binds the whole `&&` chain into one background subshell,
  so a variable set in A is empty in C (the bolt relay opened to "" for 13
  hours, 2026-09-18). Terminate the lookup with `;` and let only the relay
  that should background carry the `&`; `tests/test_graph_bolt_unit.py`
  runs the line under stubs and asserts both relays get the address.
- **The file-built configmaps are refreshed by the updater, per file.**
  `make-secrets.sh` builds `cc-litellm-config` and `cc-schema-sql` from
  files; `kubectl apply -f deploy/k3s/` never touches them. `cc-update.sh`
  re-applies the ones a release changed — add a new file-built configmap to
  that list or it silently never deploys. Editing a refreshed file (even a
  comment) restarts its consumer during the update: `deploy/pi/litellm/
  config.yaml` restarts cc-litellm, so leave it alone unless the change is
  worth that.
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
- **Never `tailscale serve` a port a `type: LoadBalancer` Service owns.**
  ServiceLB's hostPort DNAT takes the connection ahead of tailscaled and
  answers the TLS handshake in plain http — a link that is dead from the
  day it is set, on every tailnet client. A ServiceLB service is linked as
  plain `http://<tailnet name>:<port>`; serve fronts only what is
  loopback-only on the host. `tests/test_systems_links_derived.py` pins it.
- **The update ACROSS a removal is run by the PREVIOUS release's
  `cc-update.sh`** (systemd runs the installed copy; the merge replaces the
  file, the running bash keeps the old one). Its IMAGES rows and configmap
  rows are in ITS memory, and they run against the NEW tree: a row whose
  input paths differ between the installed release and the target is
  prebuilt with the target's build script (missing script = `die prebuild`),
  its deployment is rollout-restarted AFTER the `removed.txt` deletes
  (deleted deployment = `die manifests` on a MERGED tree, no rollback), and a
  changed configmap file it cannot read is the same `die`. So when a release
  removes a locally built image or a file-built configmap: keep the row's
  input paths BYTE-IDENTICAL for one release (no stub — a changed stub is a
  changed input), tombstone the Deployment/Service now, and tombstone the
  make-secrets.sh objects it mounts (configmap, Secret) one release LATER —
  the old updater's rollback re-applies the old manifests, which recreate the
  Deployment but not what it mounts. The release after that, applied by an
  updater that no longer has the rows, deletes the frozen files, adds the
  configmap/Secret tombstones, and raises `min_upgrade_from` past the last
  release whose updater still had them (an older install would otherwise
  prebuild with a script that is gone). The Graphiti removal is the worked
  case, now finished across v2.60.0 (server out, files frozen) and v2.61.0
  (files deleted, configmap/Secret tombstoned): `tests/test_graphiti_server_boundary.py`,
  `deploy/k3s/README.md` §0a.
  The steps the NEW release needs an existing install to gain (here: the
  graphiti-core patches, the app key's wider scope, `CC_GRAPH_RERANK_ALIAS`)
  ship one release earlier in a BRIDGE (v2.59.1, bullet below), and the new
  release's `min_upgrade_from` names it — so no update needs the operator's
  hands. The same mechanism makes a COMMENT edit to a build script, a
  Dockerfile or a build context rebuild that image during an update — touch
  them only when the image should change.
- **What a release needs an EXISTING install to gain, the updater does — one
  release AHEAD** (v2.59.1, the bridge into the Graphiti-library release).
  The update INTO release N runs N-1's `cc-update.sh` (systemd runs the copy
  on disk; the merge replaces it only for the next run), so a step N needs
  ships in the updater of an earlier release and is keyed on what the MERGED
  tree contains, never on a version number: `rebuild()` runs
  `scripts/apply_graphiti_patches.py` after `uv pip install` when that script
  exists (absent = skip; that is also what a rollback onto an older release
  finds); `reconcile_app_config`, after the units and the n8n workflows and
  BEFORE `start_services` (the ingest worker starts with cc-uvicorn), appends
  each `APP_ENV_DEFAULTS` row only when `.env` lacks the key AND the merged
  `.env.example` declares it (a running release never gets a key it does not
  read), and runs the merged `mint-keys.sh --scope-only` only when that
  script has the flag. All of it WARN-only: a `die` there stops a MERGED tree
  with no rollback, while each gap only holds graph extraction or search.
  The release that needs them sets `min_upgrade_from` to the bridge, which
  the installed updater enforces at resolve. `tests/test_k3s_update_reconcile.py`.
  The patch step is the graphiti-core carried fixes (design record
  2026-10-04, D7): `setup.sh app` runs the same script after its install (a
  FAIL there) and `verify.sh` asserts `--check`. The scope widening is a
  union through `/key/update` (an empty list is "all models" and is left
  alone; the key's VALUE never changes, so nothing restarts); the app's key
  reaches every alias the app calls itself — graph extraction, embedding and
  rerank since graphiti-core moved in-process, and the three Graphiti-only
  keys are retired. `APP_ENV_DEFAULTS` and `setup.sh app`'s defaults loop are
  one table in two places — keep them in step.
- **A long-running host unit `Wants=k3s.service`, never `Requires=` it.**
  `Requires=` ties the unit's start job to k3s's FIRST start attempt: when
  that fails at boot, systemd cancels the dependent once ("Dependency
  failed"), k3s's own retry succeeds a minute later, and nothing re-queues
  the dependent — `Restart=always` never applies to a unit that has not run.
  `Wants=` + `After=` + `Restart=always` starts it either way and lets it
  retry. `tests/test_k3s_units_boot.py` pins it; a timer-fired one-shot
  (`cc-backup`) is the exception.
