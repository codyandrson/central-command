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
  callable on their own and `check` composes them); the phases and
  their steps are declared ONCE, in `steps.tsv`, and the operator's
  `CHECKLIST.md` is rendered from it by `scripts/render_checklist.py` — edit
  the manifest, never a prose copy; the
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
  the key. The `*-base` rows reach the builds through the same keys, and
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
  Nine dry sections (`./setup.sh check --list`), one table, the same protocol
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
  inputs, not builds. "Executes nothing" means CHANGES nothing — the `llm` and
  `integrations` sections make READ-ONLY round trips (a completion, and
  `scripts/atlassian_probe.py` against Jira/Confluence), because a credential's
  only proof is a round trip; the guard test's list is what "mutating" means.
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
- **A new `.env` writer is a ROW, not a line of bash, and a phase's `probe`
  is what "done" MEANS** (v2.55.0, design record
  `docs/superpowers/specs/2026-10-01-setup-ledger-selfcheck-design.md` D1).
  `deploy/single/steps.tsv` declares every step of the install once — phase,
  step, kind (`run`/`gate`/`human`), `requires`, the `.env` keys it `reads`
  and `writes`, its `probe`, a doc sentence — and `tests/test_single_steps_schema.py`
  fails the suite when a `set_kv`/`set_kv_if_unset` target in
  `deploy/single/*.sh` is not exactly one row's `writes`. The phase functions
  still run exactly as before; what changed is that a phase is DONE only when
  every one of its rows' probes passes AFTER the function returns — a phase
  that `return 0`s with a row's probe false is a FAIL naming the row, which is
  what turns "the step after the one that failed never ran" from invisible
  into a line. `<state>/ledger.tsv` is the one record of what completed (one
  row per step: status, version, timestamp, a fingerprint over the `reads`
  keys' values, the last FAIL/USERACTION reason), and `./setup.sh` with no
  argument is the only resume command — it reads the ledger and continues
  from wherever the last run stopped. `./setup.sh <phase>` is a DEVELOPER
  form, never an operator one: it REFUSES (exit 1) when that phase's
  `requires` are not recorded `done`, with no `--force`; the bypass,
  `CC_SETUP_UNLEDGERED=1`, is documented ONLY here and is itself refused when
  `.env` carries `CC_EXECUTOR_MODE=live`.
- **One exit-code rule, everywhere** (v2.55.0, D5). `cc_exit_code <fails>
  <warns> <actions>` in `deploy/env-lib.sh`, precedence `FAIL > USERACTION >
  WARN` — `run_phase`, the `machine` subcommand and both `update.sh` entry
  points call it, so a FAIL is never reported as "stopped for your action."
  `phase_fetch` returns 1 on a FAIL now; `USERACTION` is reserved for a seam
  only the operator can fill.
- **The tree is pristine, or the driver refuses — there is no flag past it**
  (v2.55.0, D10). A `check` row, `tree-pristine`, repeated at the top of
  every mutating phase and of `update.sh apply`: the tracked tree is clean
  and `HEAD` is an ancestor of `upstream` when that ref exists. A difference
  is a FAIL naming the files; no git is a USERACTION naming `./update.sh
  init`; `CC_SETUP_UNLEDGERED=1` does NOT cover it. `update.sh apply` is
  fast-forward only now — the three-way merge of local commits and the
  advice to commit local tweaks to `local` are retired. `update.sh init`'s
  baseline `git add -A` on a freshly unzipped tree with no history STAYS —
  that snapshot is what makes the FIRST `apply` a clean fast-forward instead
  of a local patch.
- **`./setup.sh report`, never a freehand bundle.** One file,
  `<state>/report-<stamp>.txt` — the ledger, every failed row's `reason`
  and its `reads` keys' NAMES, the whole last run's log, `.env` key NAMES
  only, tool versions, the image manifest — built to paste into a development
  session. `diagnose` is an alias of it now.
  `tests/test_single_report_redacts.py` feeds it marker secret values and
  greps the output; never weaken the redaction to make a report "more
  useful."
- **The hook holds the agent to `.env`, not the prompt** (v2.55.0, D10).
  `.claude/settings.json`'s `PreToolUse` hooks run
  `.claude/hooks/guard-install-tree.sh` on `Edit`/`Write`/`NotebookEdit` and
  `Bash`. It is ACTIVE only on a DEPLOYMENT tree — one whose state dir
  already holds `ledger.tsv`, which every `./setup.sh`/`configure`/`check`
  run creates — and denies an edit to a tracked file or a shell command that
  writes into the tree, naming D10 and `./setup.sh report` in its own
  denial. A DEVELOPMENT session (this checkout, a worktree, a feature
  branch) sets `CC_DEV_SESSION=1` and the hook stands aside — set it in `~/.claude/settings.json`'s `env` so every dev session
  carries it, not per-command. `tests/test_install_tree_hook.py` is the
  allow/deny table. **A developer who runs `./setup.sh` in a dev checkout
  gets a ledger there too** (the hook keys off `ledger.tsv`'s presence, not
  off "is this the real deployment") and must set `CC_DEV_SESSION=1` before
  the next edit to that tree.
- **A step that printed FAIL is never `done`, and a phase that began reads
  `started`** (v2.56.0, design record D2/D11). `ledger_record` used to mark a
  row `done` whenever its probe passed — so a `verify-live` that FAILED with
  the stack still up was recorded done and the next `./setup.sh` SKIPPED
  verify. The phase's own word now outranks the probe: `fail` and
  `useraction` record what was said per check-name (`STEP_SAID`), a FAIL is
  `failed`, a USERACTION is `gate`, whatever the probe reads. And every row
  of a phase is written `started` (one atomic rewrite,
  `cc_ledger_mark_started`) BEFORE the phase function runs, so a killed run
  leaves "started, not finished" (dpkg's half-configured) instead of rows
  that look untried. `started` blocks like any other not-done status. Never
  let a probe upgrade a row the phase itself said failed.
- **One run at a time, and `./setup.sh` is what clears a stale lock**
  (v2.56.0, D11). `<state>/run.lock/` (a `mkdir` — atomic on NTFS, where
  `flock` does not exist) holds `pid`, `command`, `started`; the functions
  are `cc_lock_*` in `deploy/env-lib.sh`. Taken by every command that writes
  the ledger or changes the host and by `update.sh` (not by `status`,
  `report`, `check --list`, `machine --dry-run`, `update.sh plan`). A lock
  held by a LIVE run is `FAIL run-lock:` naming pid, command and start, and
  nothing else happens. A lock whose holder is provably gone (`kill -0`
  fails, or the pid is alive but `/proc/<pid>/cmdline` is not one of our
  scripts) is RECLAIMED with a `WARN run-lock:` naming the dead run — there
  is deliberately NO `unlock` command: the agent's contract is three verbs
  and the recovery is one command, and a logon-time run after a power cut
  must not sit behind a lock nothing holds. The WARN is printed and logged
  but not COUNTED, or a headless run would stop at the check gate on it. A
  child of the holder (`update.sh` → `setup.sh <phase>`) recognises its
  parent through `CC_RUN_LOCK_PID` and neither waits nor releases; `setup.sh`
  un-exports it before `boot` starts the API, or a cockpit-driven update
  would run "nested" beside a live install.
- **Every run prints its PLAN first, and the plan and the loop are ONE
  decision** (v2.56.0, D11 — `terraform plan`). `PLAN <phase>: WILL RUN|WILL
  SKIP — <why>` per phase, before anything executes: never run, failed
  (quoting the reason), interrupted, waiting on the operator, version
  changed, inputs changed (the row's `reads` key NAMES — a fingerprint
  cannot say which one), effect absent (drift). `cc_row_decide` /
  `cc_phase_decide` in `ledger-lib.sh` are that decision, and
  `phase_is_done` calls the same functions, so the plan cannot disagree with
  what the loop then does. A probe is evaluated only for a row that is
  otherwise skippable, so a fresh install's plan costs none. It is a
  PREDICTION and says so. `PLAN` lines are not protocol lines and move no
  counter.
- **`verify/selfcheck` is the application proving itself, and it is
  READINESS, never liveness** (v2.56.0, D4/D11). After `verify.sh` (the
  deployment, under the ADMIN key) the `verify` phase runs `python -m
  central_command.selfcheck --pre-boot` from the install's venv — the app's
  own `Settings`, the same `.env`, the credential the app holds, a completion
  through `resolve_model` — and re-emits its `selfcheck-<name>` lines through
  the driver's own `pass`/`warn`/`fail`. `boot/boot-api` and `demo/demo-feed`
  REQUIRE the row, which is how an empty `CC_LLM_API_KEY` stops reaching
  `boot`. `--pre-boot` skips only what `boot` starts (cockpit, sandbox
  runner); `./setup.sh status` runs the whole table and so spends two small
  model requests (plus the reranker's probe when one is configured) — which is why the API CACHES (`GET /api/selfcheck` spends
  nothing; a run is at API start or on `POST /api/selfcheck/run`). Nothing
  may ever restart, stop or reconfigure anything because a check failed, and
  never put it on a timer.
- **The spine key's scope is `cc_required_aliases`, never a second list**
  (v2.56.0). The mint used a hard-coded three-alias list that had already
  drifted from the ONE list. `mint_spine_key` builds the scope from
  `cc_required_aliases`; a key that is already set is never re-minted and
  never narrowed — a non-empty scope missing a required alias gains exactly
  the missing ones through `/key/update` (an EMPTY list is LiteLLM's "all
  models" and is left alone), and a proxy that cannot be asked is a WARN.
  Both keys travel in a curl config on STDIN (`curl -K -`), never an argv.
  Since graphiti-core runs in the API (design record 2026-10-04, D2) the
  scope is load-bearing for the graph: extraction (`graphiti-llm`) and its
  embedder (`cc-embedding`) are called with THIS key — the curation writer
  still embeds with the admin key — and so is the reranker, which joins the
  scope through `cc_scope_aliases` only once `CC_GRAPH_RERANK_ALIAS` names it
  (v2.62.0; `cc-rerank` is OPTIONAL, `cc_optional_aliases`, and never pauses
  the run). The k3s profile's equivalent is `mint-keys.sh` (`--scope-only`
  from its updater); its list always carries `cc-rerank`.
- **The reranker's KIND is probed, a set kind is a pin, and the extraction
  model is never mapped for the operator** (v2.62.0, design record
  2026-10-04 D3 as rebuilt). `llm/probe-rerank` asks `cc-rerank` LiteLLM's
  `/rerank` first, then the chat shape (`discover-llm.sh rerank` /
  `rerank-chat` — one-token True/False with logprobs; a thinking model
  answers a reasoning token and fails), and writes `CC_GRAPH_RERANK_ALIAS` /
  `CC_GRAPH_RERANK_KIND` only where `.env` has none. A kind that is set is
  PROVEN on every run, never re-detected — a failure is a USERACTION,
  because a configured reranker that fails makes fact search ERROR; the same
  goes for an alias the operator set. Unmapped (absent, or its skeleton) is
  a PASS saying rank fusion; mapped but answering neither shape and named by
  nothing is a WARN that leaves the alias UNUSED (its probe reads done, so
  setting `CC_GRAPH_RERANK_ALIAS` — a `reads` key — is what makes the next
  run probe again). Never auto-map `cc-rerank` to the `graphiti-llm` model:
  a chat reranker costs up to 2 × the search limit one-token calls on it per
  search; the question's help text offers it as a one-line answer instead.
  `gpt-4.1-nano` (graphiti-core's default reranker, built and never called)
  left the required list; an existing row and its `.env` key are harmless.
- **The report redacts as it collects, from `redact.tsv`, and the scan
  stays** (v2.56.0, D11 — Replicated's redactors). `deploy/single/redact.tsv`
  declares `key` rows (globs over `.env` key NAMES whose VALUES become
  `[REDACTED:<KEY>]`) and `shape` rows (URL userinfo, `Bearer`, `sk-…`);
  every section goes through `report_redact`, with known values replaced by
  bash substitution so a value never reaches an argv. `report_secret_values`
  is the one list both the filter and the final leak scan read. The scan is
  still the last line: a value that survives refuses the report, and a
  missing or key-less `redact.tsv` refuses it outright. A new credential
  shape is a ROW; never weaken the scan to get a report out.
- **`boot` starts three host processes, `stop` stops three, and the PORT is
  the proof** (v2.57.0, design record D6). The API, the sandbox runner (when
  `CC_ENABLE_SANDBOX=1`) and the cockpit server each have a pid file and a
  log in the state dir. On Linux with a `systemd --user` instance they run
  THROUGH units — `cc-<install-id>-{api,sandbox,cockpit}.service`, rendered
  by the pure functions in `deploy/single/supervise-lib.sh` into
  `<state>/systemd/`, enabled, and started with `systemctl --user restart` —
  one supervisor, never a `nohup` beside an enabled unit, and `stop` goes
  through systemd too or `Restart=on-failure` revives what it killed. With
  no user manager the start is detached and a WARN says nothing restarts
  them. `loginctl enable-linger` off is a **FAIL** (`check/linger`): without
  it the user's units die with the login session. The detached start must
  record the SERVER's pid: `( cd X && nohup cmd & echo $! )` backgrounds the
  whole list, so `$!` was a wrapper shell — `stop` signalled the shell, the
  server kept its port, and that is the 2026-09-18 and 2026-09-25 "stop left
  uvicorn listening" finding. `stop` proves each port has NO listener (not
  "no HTTP 200": a token-protected runner answers 401 to everything) and
  FAILS when it cannot load `.env` — it never reports green on ports it could
  not read. The API unit alone is `KillMode=process`: the API spawns the
  detached updater, which stops the API mid-update, and the default kill
  mode would take the updater with it.
- **The Windows logon entry runs `./setup.sh`, in a retry loop — never
  `boot` alone** (v2.57.0, D6). `cc-boot.cmd` hands off to
  `<state>/boot-at-logon.sh`, which runs `./setup.sh --accept-warnings` up to
  ten times a minute apart: with no terminal a WARN-only `check` stops the
  run without that flag, and at logon the podman machine and its containers
  may not be up yet. Exit 0/2 ends it, exit 3 ends it (it is waiting on the
  operator; retrying cannot help), exit 1 retries. So a reboot and an
  install that never finished are ONE code path, and either leaves ledger
  rows in `boot-at-logon.log` instead of silence. The run lock's stale
  reclaim is what makes this safe after a power cut.
- **The sandbox runner's token is generated, and the runner and the API read
  the same one** (v2.57.0, D6). `make-secrets.sh` generates
  `CC_SANDBOX_RUNNER_TOKEN` with the other credentials (never overwriting a
  set one); before this the shipped posture was an unauthenticated runner.
  The token reaches the runner only through `.env` (`EnvironmentFile=` or
  the sourced environment) — never a unit file, never an argv.
- **Importing the bundled skills is a step, and it is CREATE-ONLY** (v2.57.0,
  D7). `boot/skills-imported`, after the roster: each `skills/<id>/` is
  `POST`ed to `/api/skills/import` only when `GET /api/skills` does not
  already hold that id (retired ones included — a skill the operator retired
  is never resurrected), and a skill the library holds is never overwritten
  even when the release changed the bundled copy. The row's `reads` carries
  `@skills`: a TREE input — a content hash of that directory, CR-insensitive,
  no git — so the row re-runs when a bundled folder changes or is added.
  `@<dir>` in `reads` is the only non-`.env` input the manifest knows.
- **The updater ACQUIRES before it merges, from a staged copy of the new
  release** (v2.57.0, D5). `update.sh apply` checks `upstream` out SPARSELY
  (`STAGE_PATHS` — never `docs/vendor`: 47k files and MAX_PATH on Windows)
  into `<state>/stage/tree` and runs THAT release's `setup.sh acquire` with
  `CC_STAGED_FOR` naming the deployment: its fetch phase plus a probe that
  the running catalog answers the aliases it requires. A staged run reads
  the deployment's `.env` and state dir, nests under the run lock, writes NO
  ledger row and resolves image refs into COPIES — the install's `.env` and
  `installed.manifest` do not move before the merge — and builds the local
  images under an ASIDE tag (`<ref>-staged`, `cc_staged_image_ref`), because
  building under the live tag would hand the running deployment's next
  sandbox session the new release's image before anything was merged. A stop there is exit 1
  or 3 with the tree, branch, database and containers untouched. After the
  fast-forward the order is schema → fetch → llm → stack (n8n's façade
  workflows are its `n8n-workflows` row) → app → verify, each a ledgered phase, and an exit 3 from any of them STOPS there (the old code
  `return 0`ed past `app` and `verify`). `acquire` + `CC_STAGED_FOR` + the
  output protocol are an INTERFACE BETWEEN RELEASES — the installed
  `update.sh` runs the next release's `setup.sh` — so they do not change
  shape. `update-run.sh` rolls back only when HEAD actually moved: a stop
  before the merge used to "roll back" to an EARLIER update's tag, which was
  a downgrade.
- **A deployment that predates the ledger is ADOPTED by `./setup.sh`, and an
  update never fails it into a rollback** (v2.57.0). Its ledger is empty, so
  the first post-merge phase would be refused. `update.sh` asks
  `cc_ledger_blocked` before each phase and stops with ONE USERACTION
  (`ledger-adopt`, exit 3): run `./setup.sh` once — every phase is
  idempotent, so it walks the running deployment and records it. For an
  OLDER updater driving the new `setup.sh`, `phase_ledger_gate` gives the
  same answer when `CC_UPDATE_DRIVEN=1` and the ledger has no rows; by hand
  it stays the FAIL that names `./setup.sh`.
- **The suite runs once per release, by the ledger** (v2.57.0, D5).
  `phase_test` no longer skips itself when the API is up; a `done`
  `test/test` row at this version is what skips it in the full run. A `step`
  whose body is `A || B; C` hides A's failure behind C's exit status — split
  it (the cockpit's `npm ci` then `npm run build` was one), and a `bash -c`
  pipe needs its own `set -o pipefail`.
- **A locally built image carries the hash of what it was built from, and
  "the tag exists" is not "done"** (v2.57.0). The local tags are fixed
  (`cc-sandbox:1`, `cc-crawler:1`; the Graphiti server's until it left), `fetch_local`
  skipped the build whenever the tag existed, and so a release that changed
  a Dockerfile never rebuilt on update. Each build script computes
  `cc_build_inputs_hash` — the Dockerfile, every file of the build context,
  the build args that change the result, the staged CA's content; CR-stripped
  so a CRLF checkout hashes the same; never a timestamp or a temp path — and
  stamps it as the label `cc.build-inputs`. `--inputs-hash` prints it and
  touches nothing. `local_image_state` compares; `fetch_local` builds when the
  image is absent, unlabelled or stale; the image probes and `stack`'s
  `need_image` are true only when the label matches. A new build input goes
  INTO the hash in the build script, or the image silently stops tracking it.
- **A container must be running the image its ref resolves to NOW** (v2.57.0).
  podman-compose 1.6.0 recreates a container only when the service's config
  hash changes, and that hash holds the image REF, not its ID — so a rebuilt
  or re-pulled tag left the old container running, and `update.sh` never ran
  `stack` at all: a release that changed `compose.yaml` or an image was
  reported applied over the old containers. `catch_up_images` (after `compose
  up` in `llm` and `stack`) recreates exactly the services whose container's
  image ID differs from what the ref resolves to, and `p_up_stack` /
  `p_up_litellm` read false on that drift. A STATEFUL service is recreated
  only when `compose.yaml` still shows its data path on a NAMED volume
  (`stack_data_on_volume`); otherwise it is a FAIL naming the path, never a
  recreate. A new stateful service joins `STACK_STATEFUL` with its data path.
- **The resolver carries a row forward whenever it did not resolve an image**
  (v2.57.0). `installed.manifest` is how `resolve-images.sh` recognises its
  own earlier write; a `.env` value with no matching row reads as an operator
  PIN. It used to rewrite the manifest from this run's successes only, so an
  image whose registry failed once — or whose component was switched off and
  later back on — came back as a "pin" stuck at the old tag with only a WARN.
  A failed or skipped image keeps its previous row.
- **One file per phase, and the tests read the installer through ONE helper**
  (v2.58.0, design record D11 — Sentry's installer). `setup.sh` is the
  orchestrator: the protocol, `load_env`, `configure`/`status`/`stop`/
  `report`, the ledger, the plan, the run lock, `acquire`, `main`, and the
  helpers and probes more than one phase uses. Each manifest phase has
  exactly one `deploy/single/phases/<phase>.sh` holding `phase_<name>`, the
  helpers only it uses and its rows' probes — sourced, never executed, safe
  to source twice, no top-level side effects. `tests/installer_source.py` is
  the one definition of "the installer's source" (`setup.sh` plus the phase
  files): a source-reading test that globs `setup.sh` alone has silently
  stopped looking at most of the code. `tests/test_single_phase_files.py`
  pins the layout — the files equal the manifest's phases, no function
  defined twice, sourced in manifest order before `main`.
- **The operator's procedure is GENERATED, and documents link to it**
  (v2.58.0, D8). `deploy/single/CHECKLIST.md` is rendered from `steps.tsv`
  (and the prose in `scripts/checklist_template.md`) by
  `scripts/render_checklist.py`; `tests/test_single_checklist.py` fails when
  the committed file differs from a fresh render, and requires a WHERE for
  every `human`/`gate` row. The READMEs, `AIRGAP.md`, `ARCHITECTURE.md` and
  the `/setup` skill link to it and carry no phase list of their own
  (`tests/test_setup_phase_docs.py`). Change the process by changing the
  manifest and re-rendering — never by editing a prose copy.
- **No operator-facing line names a phase to run** (v2.58.0, D3). The
  contract is one command, and `./setup.sh <phase>` is a developer form that
  is refused out of order — so a FAIL that says `run: ./setup.sh app` sends
  an agent off the contract and an operator into a refusal. Every message
  says `./setup.sh` ("it resumes at <phase>" where that helps) and `report`,
  never `diagnose`. `tests/test_single_no_phase_hints.py` walks the
  installer's messages and fails on a new one.
- **A `human` row is a gate that says WHERE, and it asks for everything the
  next step will demand** (v2.58.0, D1). `check/ca-bundle` (the PEM file
  `CC_CA_BUNDLE` names must be on disk — `check` stops there rather than
  failing every later section against a missing trust file) and
  `stack/n8n-credential` (the n8n credentials the façade workflows bind by
  name), each exit 3 with a USERACTION naming the place. `stack/n8n-workflows`
  then applies the façade workflows through `deploy/n8n/apply-workflows.sh`
  — a fresh install used to have none until its first update, because only
  `update.sh` ran that script.
- **A row the phase never reached is `pending`, and a pause is `gate`**
  (v2.58.2, measured on the 2026-10-02 laptop run). `ledger_record` used to
  judge every row of a phase by its probe, so after `FAIL mint-key` the later
  `app` rows read `done` (the configure-born `.env` already had the value) or
  `failed` with no reason, and after the llm pause `catalog-filled` read
  `done` while `gate` appeared nowhere. The rule now, in manifest order: a
  row that printed FAIL is `failed`, USERACTION is `gate`, PASS/WARN is
  `done` if its probe holds; a row that printed NOTHING after an earlier row
  of this pass stopped is `pending` — never probed, no reason. Every
  `fail`/`useraction`/`step` in a phase file names a ROW of that phase
  (`tests/test_single_reporter_rows.py`; `llm_gate` printed `llm-models`,
  which was no row, and the gate was never marked). `catalog-filled`'s probe
  is `p_catalog_filled` — a required alias is unfilled while any of its rows
  is still a PLACEHOLDER skeleton (`/model/info`, the same call
  `register-models.py` judges by); `catalog`'s probe stays "listed", because
  creating the skeletons IS that step's effect.
- **`update.sh apply` with nothing to apply does nothing** (v2.58.2). With
  `local` already containing `upstream`, apply resumes the post-merge phases
  only when the tree moved under the ledger (a row carries another version)
  and a post-merge row is not `done` at the tree's version, or the ledger is
  empty (the adoption pause). Otherwise one PASS and exit 0 — it used to
  redeploy the installed release by name for eight minutes after a failed
  import, and ask the operator to stop the API first.
- **The import unpacks with Python, never `unzip`** (v2.58.2). Git for
  Windows' UnZip 6.00 does not let `*` cross `/`, so `unzip -x
  <prefix>docs/vendor/*` excluded 3 of 49,809 entries, the whole vendor tree
  was extracted, and its four symlink members made EVERY release zip fail to
  import on Windows — GitHub's own included. `deploy/single/release-zip.py`
  (run by `cc_resolve_py`, the ONE interpreter resolution `setup.sh` and
  `update.sh` share) decides every entry before writing one: `docs/vendor/`
  is a path-prefix skip, modes come from `external_attr`, an escaping name
  is a refusal naming the entry, and a symlink is never written to disk — it
  is recorded into the import commit's index as a 120000 entry, so
  `upstream` carries the release's links on every host.
- **The cockpit rebuilds only when its inputs changed** (v2.58.2). Every
  re-run spent ~70 s on `npm ci` and ~66 s on `npm run build` with nothing in
  `web/` changed. `<state>/cockpit.npm-inputs` (the lockfile + node's major)
  and `<state>/cockpit.build-inputs` (the files and trees `npm run build`
  reads, CR-stripped) are written after success and deleted before the
  command runs; `cc_cockpit_current` is the one question the two steps and
  their probes ask. A staged run never reads or writes the deployment's
  records.
- **The mail self-check applies when something in the app READS mail — never
  on the n8n flag or the façade token** (v2.58.2, finding F4 of the
  2026-10-02 laptop run). `make-secrets.sh` generates `CC_EMAIL_FACADE_TOKEN`
  on every install, and the k3s profile's `.env` says `CC_ENABLE_N8N=0` while
  its façade is in daily use, so neither can say whether mail is configured.
  `check_mail` asks what the app does: Exchange configured → checked (a
  half-configured Exchange is a FAIL); else `CC_FEED_ENABLED=1` or an enabled
  heartbeat `feed.poll` row → the façade is checked; else not applicable.
  Before this every default install FAILed `verify/selfcheck` and `boot` was
  unreachable.
- **A tag the mirror lacks is the operator's seam: USERACTION, exit 3 — a
  registry that is unreachable, a pin that does not exist, a build that
  fails: FAIL** (v2.58.2). D5's two sentences both hold because the resolver
  prints no FAIL for the seam case; `cc_exit_code` then ranks correctly
  everywhere, and the logon runner stops on exit 3 instead of retrying ten
  times.
- **The driver's hot paths start no program** (v2.58.2, finding F10). On
  Windows every `./setup.sh` invocation spent ~21 s before its first line and
  ~60 s on the plan, because a fork under MSYS costs 10–50 ms and the ledger
  code forked `cut` per manifest field, `sha256sum` per row, `date` per log
  line and re-read `.env` per key — a thousand processes to print a refusal.
  Now: rows are split by parameter expansion (`cc__tsv_split`), `.env` is
  read through a self-checking per-run cache (`cc_get_kv_cached`), fingerprints
  are hashed in one batch (`cc_fingerprint_prime`) with their VALUES
  unchanged, the ledger readers work in-process, and a probe's verdict is
  memoized for the run (`cc_probe_memo`, dropped after any phase but `check`
  runs). `tests/test_single_driver_forks.py` holds each helper against the
  old code and runs the hot paths with an EMPTY `PATH`. New code in the
  ledger, plan, lock or logging path does not use `$(cut …)`, `$(sed …)`,
  `$(date …)` or `$(cat …)` — a helper that forks there is a regression the
  empty-PATH tests catch.
- **`stop` kills by port only what it has a record of** (v2.58.2). The
  Windows fallback — `taskkill` the listener `netstat` names, because a
  Git Bash `kill` can report a success it never delivered — runs only when a
  unit or a pid file says this install started something on that port. With
  no record the listener is somebody else's and is reported, never shot: a
  `stop` in a second tree on the default ports would otherwise have killed a
  live API.
- **A probe runs BEFORE `.env` is exported, and Windows Python ends its lines
  with `\r`** (v2.58.2, second laptop pass). Two defects with one symptom —
  `llm` re-ran on every `./setup.sh`. The plan asks the probes before
  `load_env` has sourced `.env`, and `cc_required_aliases` read
  `CC_ENABLE_SPEECH` from the environment only (default ON), so with speech
  off the plan demanded `cc-tts`/`cc-stt` and read the catalog as unfilled; a
  probe reads a flag through the probes' own reader (process value, else
  `.env` — `catalog_required_aliases`, `p_flag`), never straight from the
  environment. And a MULTI-line capture of `$PY` output carries a `\r` on
  every line but the last under Git Bash (`$(...)` strips only the final
  newline; `sed`/`grep` read past a `\r`, bash `read` and `[[ == ]]` do not):
  strip it where the lines are compared (`skills_library_ids`, the
  resolver's tag list, `catalog_unfilled`). A third trap of the same family:
  on Git Bash 5.3 a `$'\r'` written INSIDE the text of a `$( … )` becomes
  empty, so a CR-strip coded there silently does nothing — put it in a
  function (`cc__tree_payload`); `tests/test_single_ledger_lib.py` scans for
  the pattern.
- **A reachability probe asks; it does not download the index** (v2.58.2).
  `check`'s host section fetched the whole PyPI simple index — 46.7 MB —
  under a 10 s limit and WARNed "unreachable" on a slow evening while the
  index answered. `index_reachable` asks with HEAD and falls back to GET only
  when HEAD is refused, and a warning names only the index that failed.
- **A logon-time run is HEADLESS by definition, and "done" is what the driver
  logged — never an exit code alone** (v2.58.2, second laptop pass). The
  logon entry runs in an interactive console, so `./setup.sh`'s stdin was a
  terminal and `boot/operator-name` prompted — and waited forever in a locked
  session on every install whose operator gave their name in the cockpit
  rather than in `.env`. `cc_render_logon_retry` runs the driver with
  `</dev/null`; every prompt in the driver is keyed on `[[ -t 0 ]]`, so each
  takes its headless branch. And a `setup.sh` killed from outside can return
  exit 0 on Windows: an attempt counts as finished only when the state dir's
  log gained the driver's own `run end: ./setup.sh all -> exit <rc>` line
  during it; otherwise the loop retries. Exactly ONE logon entry exists after
  any sequence of elevated and non-elevated runs (`cc_logon_entry_plan`: an
  existing `cc-boot` task is kept, a Startup entry beside a task is removed,
  and the Startup entry is a one-line `call` into the state dir's wrapper,
  never a copy).
- **`./setup.sh` runs the INSTALLED release while an imported update waits**
  (v2.58.2). An update that is imported but not merged leaves the tree
  exactly the installed release; refusing to run it (`existing-install … use
  ./update.sh plan`) protected nothing and kept the install DOWN after an
  acquisition that stopped, because the update flow had already stopped the
  API. It prints one note naming `./update.sh apply` and goes on. What is
  refused is a merge left half-way (`MERGE_HEAD`).
- **graphiti-core's carried fixes are a ROW after `app/install`, and its
  probe is the applier's `--check`** (design record 2026-10-04, D7). The graph
  client runs inside the API, and `uv pip install` puts back a pristine
  package on every install, so `app/graphiti-patches` (requires
  `app/install`) runs `scripts/apply_graphiti_patches.py` with the venv's own
  Python every time the app phase runs — a fresh install, an update's
  post-merge `app`, a rollback's. No `patch` binary is assumed; the applier
  writes temp-then-`os.replace` because uv HARDLINKS installed files from its
  cache (an in-place write would patch the cache and every venv sharing it,
  so `UV_LINK_MODE` needs no change); a hunk matching neither the pristine
  nor the patched file FAILS (the version is pinned — that is a defect, not
  drift). `verify/selfcheck`'s `graph-patches` line is the running system's
  own check, and the ingest worker refuses extraction without them.
- **`compose up` never removes a container whose SERVICE left
  `compose.yaml`** — so a release that drops a service retires its container
  BY NAME in the `stack` phase (`retire_graphiti_container`, the Graphiti
  server's, 2026-10-04), and `p_up_stack` reads false while it exists. Never
  `--remove-orphans`: whether podman-compose counts a disabled PROFILE's
  containers as orphans is not something this profile may assume, and
  `restart: always` would otherwise bring the dead service back at every
  reboot, holding its port. Its image is left in storage for a rollback.
- **`boot` starts the sandbox runner BEFORE the API** (v2.58.2). The API runs
  its self-check once at start; with the runner started after it, the cached
  result showed the runner's link failing until someone pressed Run. The
  runner depends on nothing, so it goes first (manifest order, `phase_boot`,
  and `After=` on the API unit).
