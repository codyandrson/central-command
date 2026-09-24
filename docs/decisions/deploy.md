# Deployment (k3s & single-node) — decisions

Governs the multi-node k3s reference deployment, the single-node Compose
profile, and the release/update surface. See `docs/decisions/README.md` for
the entry format and how to add one.

### DL-074 — Placement splits by state, not by service

- **Status:** active
- **Date:** undated
- **Rule:** [.claude/rules/deploy-k3s.md](../../.claude/rules/deploy-k3s.md) — "**Placement splits by STATE, not by service.**"
- **Why:** `local-path` stamps a required hostname nodeAffinity on every PV,
  and shared storage is no way out (Neo4j forbids NFS: no file locking means
  store corruption) — so stateless compute floats and stateful services are
  pinned, resolved via `cc-role/anchor`/`cc-role/compute` node labels, never
  hostnames.
- **Enforced:** script: `deploy/k3s/setup.sh` preflight phase (requires exactly one node per label)
- **Source:** .claude/rules/deploy-k3s.md

### DL-075 — Every CC_* endpoint must resolve to loopback

- **Status:** active
- **Date:** undated
- **Rule:** [.claude/rules/deploy-k3s.md](../../.claude/rules/deploy-k3s.md) — "**Exposure is ServiceLB (k3s Klipper), not NodePort**"
- **Why:** The real port binds on every node and follows the pod, so a
  hard-coded node address works today and silently breaks failover; pinned
  services use `hostPort` on `hostIP: 127.0.0.1`.
- **Enforced:** script: `deploy/k3s/verify.sh` (checks `127.0.0.1:5442/4000/8000` etc.)
- **Source:** .claude/rules/deploy-k3s.md

### DL-076 — A floating pod needs its image on both nodes

- **Status:** active
- **Date:** undated
- **Rule:** [.claude/rules/deploy-k3s.md](../../.claude/rules/deploy-k3s.md) — "**A floating pod needs its image on BOTH nodes**"
- **Why:** Not recorded beyond the stated mechanism: locally-built images
  need `imagePullPolicy: IfNotPresent` (`Always` CrashLoops on a tag no
  registry serves), and a missing image is invisible until the pod actually
  moves.
- **Enforced:** discipline only — no guard test located this pass
- **Source:** .claude/rules/deploy-k3s.md

### DL-077 — In a unit's ExecStart, "A && B & exec C" backgrounds A and B together

- **Status:** active
- **Date:** 2026-09-18
- **Rule:** [.claude/rules/deploy-k3s.md](../../.claude/rules/deploy-k3s.md) — "**In a unit's `ExecStart=/bin/sh -c`, `A && B & exec C` backgrounds A and B together.**"
- **Why:** `&` binds the whole `&&` chain into one background subshell, so a
  variable set in A is empty in C — the bolt relay opened to "" for 13 hours
  (2026-09-18).
- **Enforced:** test: `tests/test_graph_bolt_unit.py::test_both_relays_get_the_cluster_ip`
- **Source:** .claude/rules/deploy-k3s.md

### DL-078 — File-built configmaps are refreshed by the updater, per file

- **Status:** active
- **Date:** undated
- **Rule:** [.claude/rules/deploy-k3s.md](../../.claude/rules/deploy-k3s.md) — "**The file-built configmaps are refreshed by the updater, per file.**"
- **Why:** Not recorded beyond the stated mechanism: `kubectl apply -f
  deploy/k3s/` never touches file-built configmaps
  (`cc-graphiti-config`/`cc-litellm-config`/`cc-schema-sql`); `cc-update.sh`
  re-applies only the ones a release changed — a new file-built configmap
  must be added to that list or it silently never deploys.
- **Enforced:** script: `deploy/k3s/make-secrets.sh` / `deploy/k3s/cc-update.sh` file-map (script-level, no pytest)
- **Source:** .claude/rules/deploy-k3s.md

### DL-079 — Neo4j needs enableServiceLinks: false

- **Status:** active
- **Date:** undated
- **Rule:** [.claude/rules/deploy-k3s.md](../../.claude/rules/deploy-k3s.md) — "**Neo4j CrashLoops on its own Kubernetes Service name.**"
- **Why:** k8s injects service-discovery env vars and the Neo4j entrypoint
  turns every `NEO4J_`-prefixed var into a config setting; the fix is
  `enableServiceLinks: false` on the pod, NOT disabling strict validation
  (which silences the symptom and leaves junk settings parsed).
- **Enforced:** code structure only — `deploy/k3s/40-graph.yaml:102,192` (present twice); no automated guard against removal
- **Source:** .claude/rules/deploy-k3s.md

### DL-080 — Use the fully-qualified image ref; podman's localhost/ tag is invisible to Kubernetes

- **Status:** active
- **Date:** 2026-08-01
- **Rule:** [.claude/rules/deploy-k3s.md](../../.claude/rules/deploy-k3s.md) — "**podman tags local builds `localhost/<name>`; Kubernetes looks up `docker.io/library/<name>`.**"
- **Why:** A container image built locally with one tool gets tagged under a
  local-only prefix, but the cluster scheduler resolves an unqualified image
  name to a public-registry path instead — so a locally built image can be
  silently unreachable to the scheduler, a mismatch that would only surface
  during a failover, the worst possible moment to find it.
- **Enforced:** script: `grep -qx` assertion in the build script (script-level)
- **Source:** .claude/rules/deploy-k3s.md

### DL-081 — ctr images ls piped into grep -q inverts under pipefail

- **Status:** active
- **Date:** undated
- **Rule:** [.claude/rules/deploy-k3s.md](../../.claude/rules/deploy-k3s.md) — "**`ctr images ls | grep -q` inverts under `set -o pipefail`.**"
- **Why:** `grep -q` exits at the first match, the producer takes SIGPIPE,
  and the pipeline reports FAILURE on success — the fix captures into a
  variable first, then greps.
- **Enforced:** discipline only — script-level fix only, no pytest guard
- **Source:** .claude/rules/deploy-k3s.md

### DL-082 — setup.sh runs eleven deterministic phases with PASS/WARN/FAIL/USERACTION exit codes

- **Status:** active
- **Date:** undated
- **Rule:** [.claude/rules/deploy-single.md](../../.claude/rules/deploy-single.md) — "`setup.sh` is a deterministic driver (validate / preflight / machine / fetch / llm / stack / app / verify / test / boot / demo, PASS/WARN/FAIL/USERACTION, exit 0/1/2/3, `diagnose` support bundle)"
- **Why:** Not recorded beyond the stated mechanism: a deterministic,
  resumable driver protocol lets the /setup skill's job stay elicitation and
  diagnosis only, never freehand fixes.
- **Enforced:** script: `deploy/single/setup.sh` (`phase_validate...phase_demo`, `run_phase()` returns 0-3)
- **Source:** .claude/rules/deploy-single.md

### DL-083 — The single-node install acquires before it deploys, and never falls back on its own

- **Status:** active
- **Date:** undated
- **Rule:** [.claude/rules/deploy-single.md](../../.claude/rules/deploy-single.md) — "**The single-node install ACQUIRES before it deploys, and never falls back on its own.**"
- **Why:** Not recorded beyond the stated mechanism: `setup.sh fetch` is the
  one phase that touches the network; each failure names its `.env` seam and
  exits 3. Version flexibility is for third-party dependencies only — our
  own locally-built images stay exact, because the release is one tested
  unit.
- **Enforced:** test: `tests/test_single_airgap_seams.py::test_images_txt_is_well_formed`, `::test_every_pulled_image_is_pinned_in_images_txt`, `::test_fetch_phase_runs_before_anything_deploys`
- **Source:** .claude/rules/deploy-single.md

### DL-084 — CC_REGISTRY became three vars: CC_REGISTRY_DOCKERIO / _GHCR / _MCR

- **Status:** active
- **Date:** 2026-08-30
- **Rule:** (recorded here) The single-node profile answers registry
  mirroring through three explicit variables,
  `CC_REGISTRY_DOCKERIO`/`CC_REGISTRY_GHCR`/`CC_REGISTRY_MCR`, one per
  upstream registry, rather than one generic `CC_REGISTRY`.
- **Why:** CHANGELOG `2026-08-30 — v2.2.0: the single-node setup acquires
  every dependency up front, from mirrors or an explicit bundle` — "One
  answer file for every seam (`deploy/single/env.example`, whose keys v2.42.0
  merged into the repo-root `.env.example`'s deployment section):
  `CC_REGISTRY_{DOCKERIO,GHCR,MCR}`...".
- **Enforced:** test: `tests/test_single_airgap_seams.py::test_images_txt_is_well_formed` (validates `images.txt` format against the registry keys)
- **Source:** CHANGELOG v2.2.0

### DL-085 — An update waits for the agents to finish

- **Status:** active
- **Date:** 2026-09-13
- **Rule:** (recorded here) The cockpit updater's busy-gate holds, pauses, or
  parks running agent work before applying an update, rather than
  restarting underneath it.
- **Why:** CHANGELOG `2026-09-13 — v2.30.0: an update waits for the agents to
  finish`, building on v2.29.6's refusal-while-mid-turn behaviour.
- **Enforced:** test: `tests/test_update_hold.py::test_engage_waits_while_runs_are_live_then_triggers_unforced`, `::test_a_fresh_task_run_is_refused_while_held_and_the_task_stays_assigned`, `::test_the_hold_disables_the_cockpit_composer`
- **Source:** CHANGELOG v2.30.0

### DL-086 — Removing a k3s manifest means adding its tombstone

- **Status:** active
- **Date:** 2026-09-01
- **Rule:** (recorded here) `kubectl apply -f deploy/k3s/` creates and
  updates but never deletes. A manifest removed from the tree must be named
  in `deploy/k3s/removed.txt` (`<namespace> <kind> <name>`); the updater's
  manifests phase deletes each line with `--ignore-not-found`.
- **Why:** A replaced deployment kept its host port after its manifest left
  the tree, so its successor sat unschedulable from the moment it shipped.
  An explicit tombstone list was chosen over `--prune`, which deletes by
  label selection and can take more than was meant.
- **Enforced:** script: `deploy/k3s/cc-update.sh` (reads `deploy/k3s/removed.txt`); adding the tombstone is discipline
- **Source:** CHANGELOG v2.19.2
