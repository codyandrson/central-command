# Deploy refactor: standard substrates, flexible pins, symmetric rollback

**Date:** 2026-09-03 · **Status:** approved direction (operator decision) ·
**Supersedes the orchestration half of** `2026-08-25-deterministic-setup.md`
(its elicitation/protocol half stands).

## Why

The operator's verdict on installation: "miserable every single time — error
prone, fragile." The evidence agrees:

- `deploy/` is ~8,300 lines of bash across 90 files — a second product.
- Two near-identical 1000+-line drivers (`deploy/single/setup.sh`,
  `deploy/k3s/setup.sh`) share helpers by copy-paste; every fix lands twice
  or drifts.
- 100+ env vars across 3–4 `.env` files with facts hand-copied between them.
- The air-gapped target (single-node Windows, Podman Desktop, comprehensive
  enterprise mirrors) has **never** deployed successfully.
- The bite-mark comments (pipefail/grep inversions, NTFS chmod, `localhost`
  vs `::1`, stray-checkout Gmail polling) are each a real incident that a
  standard orchestrator makes structurally impossible.

A survey of how the field ships comparable multi-service self-hosted systems
(Sentry, Supabase, Immich; GitLab's chart; Zarf/Hauler for air gap —
research pass 2026-09-03) found the settled answers: **one Compose file +
one `.env` + a thin wrapper** for a single node; **a Helm chart with
`values.yaml` as the only config surface** for Kubernetes. Zarf was
evaluated and **rejected**: it solves the no-infrastructure sneakernet air
gap and deploys only into Kubernetes; our air-gapped site is a mirrored
enterprise network with a podman box — a configuration problem, not a
transport problem.

## What survives (validated, not sentiment)

- The **output protocol** (`PASS|WARN|FAIL|USERACTION`, exit 0/1/2/3) — the
  right contract for an agent-conducted install; shrinks into the thin
  wrapper.
- The **LLM-catalog exit-3 pause** with live per-alias probes (chat,
  structured, rerank, TTS→STT round trip, embedding-dimension measurement).
  No mature project scripts away operator credentials; ours *proves* them.
- The **embed-dimension permanence guard** (refuse to change `CC_EMBED_DIM`).
- **`update.sh`'s discipline**: version gate, DB-backup-before-apply,
  additive-only schema, plan→confirm→apply, and its manual `rollback` verb.
- **`discover.sh`** for mirrored (not fully dark) networks — it feeds mirror
  config; it stops being an outer loop around a bespoke fetch phase.
- **Digest/tag pinning as a record** — demoted from requirement to
  preference (see below).
- Idempotency by probing reality, no state file; resume is re-run.
- The boot/demo phases (operator name, human-approved dry-run demo) — the
  gate IS the product.

## Decisions

### D1 — Single-node substrate: Compose

`deploy/single/compose.yaml` (Compose spec; runs under `podman compose` on
the targets, validated with `docker compose config` in dev) replaces
`render.sh` + envsubst templates + `podman kube play` choreography +
`wait_http` polling:

- **Optional components become Compose profiles** (`n8n`, `crawler`,
  `sandbox`, `speech`) — `CC_ENABLE_*` flags map to `COMPOSE_PROFILES`.
- **Readiness becomes `healthcheck` + `depends_on: condition:
  service_healthy`** — the dependency graph lives in the file, not in phase
  ordering.
- **`.env` is read natively by Compose** — the render step disappears;
  service DNS names replace the pod-prefix convention.
- Ports stay loopback-published (`127.0.0.1:port:port`) — same exposure
  contract as today. 5442 stays 5442.
- Named volumes replace kube-play volumes; `make-secrets.sh` stays (secrets
  as env, generated once, written back to `.env`).
- The Windows/Podman-Desktop target is a **design input, not a port**: the
  air-gapped production box is Windows. `127.0.0.1` addressing, no reliance
  on POSIX file modes for secrets (warn, as today), no `/proc` tricks.

The driver shrinks to elicitation, the retained human gates, and calls to
`podman compose`. Target: setup.sh well under half its current size, with
`validate`'s port/flag arithmetic largely absorbed by `docker compose
config` and profile semantics.

### D2 — Version flexibility: constraint / lock / resolve

Rigid `@sha256` pinning broke real installs when enterprise mirrors lacked
the exact artifact. Replace with three tiers:

1. **Constraints** (`deploy/single/images.txt`, new columns): per image, the
   loosest true requirement — a tag *pattern* (`16`, `5.26`, `7-alpine`),
   i.e. floor-and-ceiling by series, never a bare floor (a new major is a
   breaking change until proven otherwise).
2. **Lock**: the tested tag + digest, recorded at release time (what
   `images.txt` is today).
3. **Resolve at install** (`resolve-images.sh`): query the (mirror)
   registry's `/v2/<name>/tags/list`; prefer the locked tag (PASS), else the
   newest tag satisfying the constraint (WARN, naming the substitution),
   else FAIL naming the constraint and what the mirror has. Resolved refs
   are written to `.env` as `CC_IMG_*` vars the compose file references.
4. **Record**: the resolved set (ref + tag + pulled digest) is written to
   `deploy/single/installed.manifest` — provenance, and the rollback record.

Digest verification still happens when the mirror serves the locked tag; a
substituted tag is trusted from the enterprise mirror (deliberate: the
digest pin defended against public-registry tag poisoning, a threat the
mirrored air gap does not carry). pip/npm keep their existing two tiers
(ranges + `requirements.lock` / `package-lock.json`); the airgap path
retries from constraints with a WARN instead of dying when the mirror lacks
a locked version.

**Boundary:** locally-built images and our own code stay exact — the release
is one tested unit; flexibility is for third-party dependencies only.

**Compensating control:** a WARN-level substitution plus green `verify` is a
supported install. Capability is proven by probes, not version strings —
when a release starts depending on a dependency feature, it adds a verify
probe for that feature (same principle as the model-capability probes).

### D3 — Update pipeline: plan → stage → pre-test → apply → verify → rollback

`update.sh` gains/reorders to:

- **plan**: existing merge preview **plus** constraint resolution against
  the mirror — a release whose new floors the mirror cannot satisfy fails
  *here*, before anything is touched, naming the dependency and the
  available versions.
- **stage**: pull/build every resolved artifact while the old stack runs.
  A failed stage leaves the deployment untouched.
- **pre-test**: offline suite against the new code (scratch DB), as today's
  setup `test` phase does.
- **apply**: unchanged discipline (backup default-on, additive-only schema,
  checkpoint tag) — now also snapshots `installed.manifest`.
- **verify**: live probes incl. new-feature capability probes.
- **rollback**: stays a first-class manual verb; now restores the previous
  `installed.manifest`'s image set (kept on disk until the release is
  accepted; retention: last two releases).

New release metadata: a release may declare dependency floors (the
constraints file travels with the release), and plan enforces them.

### D4 — k3s substrate: Helm (phase 3, separate release)

An umbrella chart; placement rules become `values.yaml`
(`nodeSelector`/`tolerations` per the anchor/compute split); `helm
rollback` supplies the missing manual rollback verb; `cc-update.sh` keeps
the backup/version-gate wrapper around `helm upgrade`. GitOps (Flux) was
assessed and deferred — auto-sync clashes with the approval-gate culture.
Not in scope for phase 1/2.

### D5 — Zarf/Hauler/KOTS: not adopted

Recorded so the question isn't reopened by default: Zarf is
Kubernetes-only and solves the no-mirror transport problem we don't have;
KOTS assumes a vendor/customer split; single-binary/embedded-DB appliances
conflict with the Postgres+Neo4j architecture. Revisit only if the
air-gapped target's shape changes.

## Phasing

1. **Phase 1 (this worktree):** Compose profile for single-node + D2
   resolution + slimmed driver. The old kube-play path is deleted in the
   same release (no second maintained path — the removed airgap bundle
   mechanism is the cautionary tale).
2. **Phase 2:** D3 update-pipeline rework.
3. **Phase 3:** Helm chart for k3s; retire `deploy/k3s/setup.sh`'s
   orchestration half the same way.
4. Windows/Podman-Desktop live validation gates each phase's release for
   the air-gapped target; the connected k3s deployment gates phase 3.

## Acceptance (phase 1)

- `docker compose config` clean; suite green; shellcheck clean on the new
  scripts.
- The ten-phase operator journey collapses to: edit `.env` → `./setup.sh`
  → LLM pause → demo approval. Same protocol, same gates, fewer moving
  parts.
- `verify.sh` assertions pass against the Compose-deployed stack — old and
  new judged by the same tests.
- No change to the app, schema, gateway, or trust boundary — this refactor
  is deployment-only.
