---
paths:
  - "deploy/single/**"
  - "deploy/discover.sh"
  - "deploy/env-lib.sh"
  - "deploy/AIRGAP.md"
  - "deploy/airgap.env.example"
---

# Single-node (Compose) deployment bite marks

Rules below exist because a real failure produced them. Trust the rule even
where the story is gone. Moved verbatim from the root instructions; they load
when a matching file is read.

- **`deploy/single/`** — the single-node **Compose** profile (`compose.yaml`,
  run under `podman compose`).
  `setup.sh` is a deterministic driver (check / machine / fetch /
  llm / stack / app / verify / test / boot / demo, PASS/WARN/FAIL/USERACTION,
  exit 0/1/2/3, `diagnose` support bundle — `validate` and `preflight` stay
  callable on their own and `check` composes them); the
  /setup skill's job is elicitation and diagnosis only. `compose.yaml` is the
  whole deployment (readiness is healthchecks + depends_on, optionals are
  profiles); `images.txt` holds a constraint, a locked tag and a locked digest
  per image, which `resolve-images.sh` turns into the refs this registry can
  actually serve. Restricted networks start with
  `deploy/discover.sh` (the /discover skill), which maps reachable mirrors
  into the `.env` seams.
- **ONE answer file, and NOTHING written inside the checkout** (v2.42.0,
  design record `docs/superpowers/specs/2026-09-23-airgap-check-configure-setup-design.md`
  D1 + D7 — the operator's failure mode was drift between files holding the
  same fact). The answer file is `$REPO_ROOT/.env`; `deploy/single/.env`,
  `deploy/single/env.example`, `web/.env` (on THIS profile) and
  `deploy/discovery.conf` are retired, and `load_env` migrates an existing
  install's into it, moving the old file to `<state>/migrated/`. So:
  * compose is ALWAYS `--env-file "$ENV_FILE"` before `-f` — there is no
    `.env` beside `compose.yaml` for it to find, and a hand-run
    `podman compose` without the flag renders empty credentials;
  * a duplicated fact gets ONE key and the **`CC_` app name wins**
    (`CC_LLM_PROXY_ADMIN_KEY`, `CC_LITELLM_SALT_KEY`, `CC_NEO4J_PASSWORD`);
    the CONTAINER-side variable names inside `compose.yaml`'s `environment:`
    blocks never change — they are the images' contract. Never re-add a copy
    step to keep two names in sync;
  * every generated file goes to `$CC_STATE_DIR` (`deploy/env-lib.sh`'s
    `cc_state_dir`, mirrored by `config.py`'s `resolved_state_dir()` — keep
    the two in step). `tests/test_single_no_tree_writes.py` walks the scripts
    and fails a new in-tree write; the `.gitignore` entries that used to hide
    the litter are deliberately gone, so a regression is a dirty checkout.
- **TWO trust knobs, fanned out from ONE function** (v2.43.0, design record D4
  — the operator DROPPED the "never disable verification" rule on 2026-09-23;
  the site assumes security through isolation). `CC_CA_BUNDLE` and
  `CC_TLS_INSECURE=0|1`, and no per-tool knobs — per-tool granularity is what
  drifted. `deploy/env-lib.sh`'s `cc_export_tls_env` is the ONE fan-out and
  every command in the profile calls it; a build gets the CA as
  `podman build --secret id=cc_ca` (never a build-arg — those are visible in
  `podman history`) and `--tls-verify=false`; the podman MACHINE gets it from
  the `machine` phase. Every command that sees `CC_TLS_INSECURE=1` prints
  exactly ONE `WARN tls-insecure:` line naming the consumers IT drives — never
  a PASS, never silent. The one consumer with no insecure option is Hugging
  Face (the speech engine); say so rather than pretending.
  `tests/test_single_airgap_seams.py` walks the fan-out table.
- **The `machine` phase WRITES another host, so it behaves like it.** Between
  `preflight` and `fetch`; a no-op where there is no podman machine; DROP-INS
  only (`registries.conf.d/`, `containers.conf.d/`) and never a main
  containers configuration file; the diff is printed BEFORE each write and a
  proxy VALUE is never printed; `--dry-run` reports and writes nothing (it is
  what `preflight` calls, so preflight must never turn the diff into a
  USERACTION — exit 3 there would abort the full run before the phase that
  fixes it). Its pure functions live in `deploy/single/machine-lib.sh` because
  a machine cannot exist on the developer's Linux box and the DECISIONS still
  have to be tested (`tests/test_single_machine_lib.py`).
- **An operator `CC_IMG_<NAME>` pin WINS, and is verified.** The resolver
  writes that key itself, so presence proves nothing — `installed.manifest`
  records what it wrote, and a value that differs from that record is the
  operator's. A pin is checked against the registry (HEAD by tag or digest,
  parsed from the PINNED ref so a re-namespaced PATH works), WARNed, recorded
  as `pinned`, and never rewritten; a pin that does not exist is a FAIL naming
  the key. The three `*-base` rows reach the builds through the same keys, and
  each Dockerfile's `ARG CC_IMG_*` default must equal its `images.txt` row.
- **`check` EXECUTES nothing, and it is the GATE** (v2.44.0, design record D5).
  Eight dry sections (`./setup.sh check --list`), one table, the same protocol
  and exit taxonomy as a phase; the full run is `check` then the nine phases
  that change something, and it refuses to continue past a FAIL or a
  USERACTION — a WARN-only check needs `--accept-warnings` or an interactive
  `y`, and with no TTY and no flag it STOPS rather than guessing (rustup's
  rule). It COMPOSES `validate`, `preflight` and `machine --dry-run` instead of
  copying their probes, writes nothing inside the checkout but `.env`
  (`CC_STATE_DIR` and `CC_EMBED_DIM`, only when unset), and never generates a
  secret — a blank credential is a WARN naming `make-secrets.sh`, because a
  gate that refuses a FIRST install is worse than no gate.
  `tests/test_single_check_is_dry.py` walks every function check can reach and
  fails the suite on a `podman pull|build|run`, a `compose … up`, an install
  or a `make-secrets` call. Its ceiling is PRINTED, not implied: check proves
  inputs, not builds.
- **The LLM catalog may be DECLARED in `.env`, and the UI is the fallback**
  (v2.44.0, design record D3). `CC_LLM_UPSTREAM_BASE_URL`,
  `CC_LLM_UPSTREAM_API_KEY` and one `CC_LLM_UPSTREAM_MODEL_<ALIAS>` per alias
  `cc_required_aliases` (in `deploy/env-lib.sh` — the ONE list) says this
  deployment needs. `register-models.py` stays CREATE-ONLY: real rows when the
  keys are set, today's PLACEHOLDER skeletons when they are not (the k3s
  profile depends on that), a PLACEHOLDER row UPDATED once the keys appear, and
  a row anyone filled in never written to — it WINS over `.env`, and the script
  says so. The key is never printed, never logged and never compared (LiteLLM
  masks it); a loopback base URL is rewritten to `host.containers.internal`
  for the row, with a WARN, because a CONTAINER dials it.
- **The single-node install ACQUIRES before it deploys, and never falls back
  on its own.** `setup.sh fetch` is the one phase that touches the network;
  each failure names its `.env` seam and the phase exits 3; `deploy/discover.sh`
  is how a restricted network learns which mirror to write into each seam.
  Adding an image means adding its constraint/lock line to `images.txt` — the
  seam test fails otherwise. **Version flexibility is for third-party
  dependencies only:** the locked digest is verified when the mirror serves
  the locked tag, a same-series substitution is a WARN the operator lives
  with, and our own (locally built) images stay exact — the release is one
  tested unit.
