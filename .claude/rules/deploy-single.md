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
  `setup.sh` is a deterministic driver (configure, then check / machine / fetch /
  llm / stack / app / verify / test / boot / demo, PASS/WARN/FAIL/USERACTION,
  exit 0/1/2/3, `diagnose` support bundle — `validate` and `preflight` stay
  callable on their own and `check` composes them); the
  /setup skill's job is conducting the loop, elicitation and diagnosis only. `compose.yaml` is the
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
  every command in the profile calls it; a build gets the CA as **`cc-ca.crt`
  in a STAGED build context** (`cc_stage_build_context` →
  `<state>/build/<image>/`, regenerated every run) plus `--tls-verify=false`;
  the podman MACHINE gets it from the `machine` phase. **Never go back to
  `podman build --secret`**: on Windows podman joins a Windows separator into
  the machine's Linux temp path
  (`open /mnt/c/…/tmp.X\podman-build-secret-N`) and NO local image can build
  while `CC_CA_BUNDLE` is set (measured 2026-09-24). A CA is public material —
  the private key is what would be secret — so the secret bought nothing but
  that failure. The Dockerfiles take it with the optional-file glob
  `COPY cc-ca.cr[t] …` guarded by an `-s` test, and every script that builds
  them with PODMAN puts a cc-ca.crt in the context even with no CA (an EMPTY
  one): a zero-match glob is a no-op under BuildKit but an ERROR under buildah
  (containers/podman#25229). **`CC_CA_BUNDLE` REPLACES the trust store**, so a
  bundle carrying only the corporate root loses pypi/npm/deb with curl 60 —
  `check` WARNs on one certificate while a public source seam is blank.
  Every command that sees `CC_TLS_INSECURE=1` prints exactly ONE
  `WARN tls-insecure:` line naming the consumers IT drives — never a PASS,
  never silent. The one consumer with no insecure option is Hugging
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
- **A provider that ANSWERS can still be too old — podman-compose 1.6.0 is a
  FLOOR** (v2.47.0). The work site ran 1.5.0 and `check` said PASS, because
  `compose-provider` only ever asked whether something answered. Two things
  this profile depends on first shipped in 1.6.0 (2026-06-03): `up --wait` (the
  deploy phases wait on `compose.yaml`'s healthchecks instead of polling) and
  the config-hash change that made a second `up -d` idempotent — under 1.5.0
  every re-run of the install died with `container name … is already in use`.
  So there is a separate `compose-version` line, deciding through the pure
  `compose_version_floor_ok` (prints nothing, 0 ok / 1 too old / 2
  unparseable; `tests/test_single_compose_floor.py` lifts it out of setup.sh
  and runs it). Parse the PRODUCT, never `head -1`: `podman compose version`
  prints the external-provider banner first and `podman version 5.8.3` second.
  `docker compose` carries NO floor — do not invent one. The same run taught
  the other half: an air-gapped host with no CPython 3.12 and no
  `CC_PYTHON_MIRROR` is a **FAIL**, not a WARN — the interpreter download is
  known to be impossible there, and a warning only defers the failure to the
  `app` phase.
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
- **A new seam is a ROW, and the schema is the one list** (v2.45.0, design
  record D6). `deploy/single/questions.tsv` declares every question once — key,
  group, prompt, default, required, validator, `when` guard, secret — and TWO
  commands read it: `configure` ASKS from it and `check`'s answers section
  VALIDATES from it. So adding a dependency is **a row in `questions.tsv` + a
  line in `.env.example` + its consumer**, and
  `tests/test_single_questions_schema.py` fails the suite otherwise (an
  undeclared key, a validator that does not exist, a `when` the expression
  language cannot parse, a `CC_LLM_UPSTREAM_MODEL_*` row that does not match
  `models.json`'s aliases). The validators live in
  `deploy/single/questions-lib.sh`, print ONE line of reason, and may never
  write — `check` reaches them. `when` is `KEY=value` / `KEY!=value`,
  comma-separated ANDs, evaluated against the answers so far: a tiny expression
  language and NOT bash, because `eval`ing a data file makes every row a code
  path. Empty fields are written `-`: a genuinely empty TSV field COLLAPSES
  under IFS-splitting. **An answer containing a space is written QUOTED**
  (`q_quote`) — the answer file is SOURCED, so `CC_OPERATOR_NAME=Jane Doe`
  unquoted runs `Doe` as a command (it did, once).
- **`configure` FAILS CLOSED, and it is the only command that creates `.env`**
  (v2.45.0, rustup's rule). With no TTY, or with `--non-interactive`, it prompts
  for nothing: it lists every unanswered REQUIRED key as a USERACTION and exits
  3. It never defaults its way past a required answer, and it never writes a key
  it did not ask about — which is what keeps `.env` a PRESEED file (carried in
  from a connected machine, it asks nothing and stays byte-identical:
  `tests/test_single_configure_preseed.py`). REQUIRED means "setup cannot run
  without it", and as of v2.45.1 **NO question is required**: every one has a
  working default or a documented blank meaning, and the upstream LLM keys
  became optional when UI entry was confirmed as the primary method. The column
  and the fail-closed path stay — the next genuinely unanswerable dependency is
  a `y` in the schema rather than new code.
  `check` and the full run never create `.env`; they say "run configure".
- **The LLM catalog is ENTERED IN THE LiteLLM UI, and `.env` may declare it
  instead** (v2.44.0 design record D3, reworded v2.45.1). The catalog lives in
  LiteLLM's database, not in `.env`; UI entry is the PRIMARY method and the same
  one the k3s profile uses, because LiteLLM expresses provider nuance
  (credentials, per-provider parameters, routing) a flat answer file cannot.
  **So the `llm` phase's exit-3 pause is a DELIBERATE exception to "a full run
  does not stop", not a defect** — `check` reports a blank catalog as ONE PASS
  line naming the coming pause (never a USERACTION, so the gate lets it
  through), and the phase's `catalog-declared` line is a PASS either way: a WARN
  there made every successful UI-driven run finish at exit 2. The declaration is
  an OPTIONAL shortcut whose real value is proving the endpoint from the host
  BEFORE anything deploys: `CC_LLM_UPSTREAM_BASE_URL`,
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
