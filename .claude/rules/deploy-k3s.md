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
- **In a unit's `ExecStart=/bin/sh -c`, `A && B & exec C` backgrounds A and B
  together.** `&` binds the whole `&&` chain into one background subshell,
  so a variable set in A is empty in C (the bolt relay opened to "" for 13
  hours, 2026-09-18). Terminate the lookup with `;` and let only the relay
  that should background carry the `&`; `tests/test_graph_bolt_unit.py`
  runs the line under stubs and asserts both relays get the address.
- **The file-built configmaps are refreshed by the updater, per file.**
  `make-secrets.sh` builds `cc-graphiti-config`, `cc-litellm-config` and
  `cc-schema-sql` from files; `kubectl apply -f deploy/k3s/` never touches
  them. `cc-update.sh` re-applies the ones a release changed — add a new
  file-built configmap to that list or it silently never deploys.
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
