# Air-gapped install: one answer file, a check that executes nothing, a setup that cannot surprise

> **Status:** implemented — every phase shipped (v2.42.0–v2.45.0)
> **As-built:** P1 — `deploy/env-lib.sh`, `.env.example`, `deploy/single/setup.sh`, `deploy/single/update.sh`, `deploy/single/resolve-images.sh`, `deploy/single/make-secrets.sh`, `deploy/discover.sh`, `central_command/config.py`, `central_command/api/update.py`, `tests/test_single_no_tree_writes.py`. P2 (v2.43.0, D2 + D4) — the same five scripts plus `deploy/single/machine-lib.sh` (new), `deploy/single/build-graphiti-image.sh`, `deploy/single/build-sandbox-image.sh`, `deploy/single/build-crawler-image.sh`, `deploy/single/verify.sh`, `deploy/single/discover-llm.sh`, `deploy/single/compose.yaml`, `deploy/single/images.txt`, `deploy/pi/graphiti/Dockerfile`, `deploy/k3s/sandbox.Dockerfile`, `central_command/crawler/Dockerfile`, `central_command/sandbox/runner.py`, `deploy/AIRGAP.md`, `deploy/single/README.md`, `.claude/rules/deploy-single.md`, `.claude/skills/setup/SKILL.md`, `tests/test_single_airgap_seams.py`, `tests/test_single_machine_lib.py` (new).
> P3 (v2.44.0, D5 + D3) — `deploy/single/setup.sh` (the `check` command, the
> phase split it composes, the gate in the `all` driver),
> `deploy/env-lib.sh` (`cc_alias_env_key`, `cc_required_aliases`),
> `deploy/pi/litellm/register-models.py` (real rows from `.env`, the
> placeholder-only update path, the loopback rewrite, key redaction),
> `.env.example`, `deploy/AIRGAP.md`, `deploy/single/README.md`, `README.md`,
> `.claude/rules/deploy-single.md`, `.claude/skills/setup/SKILL.md`,
> `tests/test_single_check_is_dry.py` (new),
> `tests/test_register_models_upstream.py` (new),
> `tests/test_single_airgap_seams.py`, `tests/test_setup_phase_docs.py`.
> P4+P5 (v2.45.0, D6 + the docs and the skill) — `deploy/single/questions.tsv`
> (new), `deploy/single/questions-lib.sh` (new), `deploy/single/setup.sh`
> (`configure`, the schema reader, check's answers section, the usage/driver
> text), `deploy/single/make-secrets.sh`, `deploy/single/resolve-images.sh`,
> `deploy/AIRGAP.md`, `deploy/single/README.md`, `README.md`,
> `.claude/rules/deploy-single.md`, `.claude/skills/setup/SKILL.md`,
> `.claude/skills/discover/SKILL.md`, `docs/decisions/deploy.md`,
> `tests/test_single_questions_schema.py` (new),
> `tests/test_single_configure_preseed.py` (new),
> `tests/test_single_airgap_seams.py`.
>
> **P3 deltas from the plan, decided while building.** The section order is the
> record's, but three verdicts were softened because a pre-deployment gate that
> refuses a FIRST install is worse than no gate: the credentials
> `make-secrets.sh` generates are a WARN (check never generates a secret, and
> P4's `configure` will fill them BEFORE check runs), a compose variable that is
> one of those credentials is a WARN rather than a FAIL, and a port held by one
> of this install's own containers (or by the host process `boot` started, per
> its pid file in the state dir) is a PASS rather than a conflict. The upstream
> values are CREATE material, never INVARIANTS: `plan()` gained an `invariants`
> argument so a row the operator filled in with their own model id is not "drift"
> because `.env` names another one — the declaration's own patterns (the plain
> `openai/` prefix, `mode`, the timeouts) stay the invariants. `check` resolves
> the Python graph against the install's `.venv` when it exists and otherwise
> against a THROWAWAY venv in the state directory: a resolve needs a target
> environment, and creating the install's own venv is the `fetch` phase's job.
> `CC_API_PORT` and `CC_COCKPIT_PORT` joined the validated port list (they are
> host ports like any other). `cc_required_aliases` gates the speech pair on
> `CC_ENABLE_SPEECH`, which is a deliberate change from today's behaviour where
> all six aliases are required unconditionally: the flag's whole meaning is
> "those two point at engines of your own", which is a UI job, so they are not
> required IN `.env` — `register-models.py` still creates all six skeletons and
> the `llm` phase still probes all six through the proxy.
>
> **Scope:** the single-node profile (`deploy/single/`) on Windows Podman
> Desktop (WSL2 podman machine) behind an enterprise mirror. The k3s
> profile is explicitly OUT of scope for this record; D4 of
> `2026-09-03-deploy-refactor-design.md` still owns it.
> **Builds on:** `2026-08-25-deterministic-setup.md` (output protocol, exit
> taxonomy, "script is the spine, agent is the exception handler") and
> `2026-09-03-deploy-refactor-design.md` D2 (constraint / lock / resolve).
> **Retires:** the "never disable TLS verification anywhere" rule in
> `deploy/AIRGAP.md`; the discovery output tree as an input to setup; the
> LiteLLM-UI pause in the `llm` phase as the ONLY way to enter a provider.
>
> **P2 deltas from the plan, decided while building:** the base-image variables
> are the resolver's own PATH-derived names (`CC_IMG_ZEPAI_KNOWLEDGE_GRAPH_MCP`,
> `CC_IMG_PYTHON`, `CC_IMG_PLAYWRIGHT_PYTHON`), not a second `*_BASE` naming
> scheme, so one image has one variable everywhere. `installed.manifest` gained
> a leading variable column: the pin/own-write comparison has to be keyed to the
> `images.txt` ROW, which a renamed path would otherwise break. The machine
> phase's dry run reports WARN, never USERACTION — `preflight` calls it, and
> exit 3 there would abort the full run before the phase that fixes it. The CA
> for LiteLLM travels in `SSL_CERT_FILE` only, with `SSL_VERIFY` carrying just
> `True`/`False`: LiteLLM's documented precedence puts `SSL_VERIFY` ABOVE
> `SSL_CERT_FILE`, and a path coerced to a bool would read as False and silently
> disable verification, where this way an unread CA is a loud certificate error.
> `deploy/discover.sh` keeps its explicit `--cacert`/`-k` flags rather than the
> exported fan-out (it probes AS the tools would, so flags are the point) and
> only adopts the shared WARN wording.

> **P4/P5 deltas from the plan, decided while building.** The schema's empty
> fields are written `-`, not left empty: tab is an IFS *whitespace* character,
> so bash's `read` collapses consecutive tabs and an empty column would shift
> every field after it. `CC_ENABLE_SANDBOX` joined the `features` group — the
> plan listed three flags, and the fourth is an install choice with the same
> shape (it is also the one whose containment trade the operator should be told
> about out loud). The port rows carry group `advanced`, which is the group
> `--all` unlocks, so "asked only with --all" is a property of the DATA rather
> than a special case in the code. One real defect surfaced and is fixed here:
> an answer containing a space must be written QUOTED, because the answer file is
> SOURCED — `CC_OPERATOR_NAME=Jane Doe` bare runs `Doe` as a command and leaves
> the variable unset (`cc_env_unquoted_keys` would then refuse the next run).
> `q_quote`/`q_unquote` own that, and `phase_boot`'s fallback name prompt — which
> had the same hazard since it was written — uses them too. The schema is read on
> fd 3 rather than stdin, because a `while read < <(…)` loop feeds `read -rp` the
> next SCHEMA ROW as the operator's answer (observed, once). `configure` skips
> `make-secrets.sh` on the fail-closed path: with nothing answered there is
> nothing to complete, and a run that reported "stopped for you" should not have
> generated six credentials on the way out.
>
**Date:** 2026-09-23 · **Research:** three-agent pass the same day — a repo
audit of every pinned artifact, a doc-verified pass over podman machine /
Registry v2 / per-tool TLS variables / LiteLLM / compose interpolation, and a
survey of installer and preflight frameworks. Facts below that came from
that pass are cited inline; anything not verified is marked.

## Why

The operator's air-gapped deployments did not fail on reachability. The
site has a container registry mirror, a PyPI mirror and an npm mirror, and
images pull. They failed on two things:

1. **A specific image tag is missing on the mirror** and a nearby tag has to
   be used.
2. **Finding where that tag — or a CA, a proxy, an index — is defined** meant
   chasing compose files, Dockerfiles, podman settings and several `.env`
   files, and editing code to change a value.

The experience the operator wants (the operator's words, condensed): run a
pre-deployment check that prints plainly what it is checking and what
failed; triage the failures with the agent; re-run; loop until 100% green
with **nothing changed but `.env`**; then run setup, which succeeds because
every input was already validated. Deploy = the release zip + one `.env`.

Two verdicts from the research that shape the design:

- **Nothing external is worth adopting as a dependency.** goss and
  Replicated's host preflight already express the PASS/WARN/FAIL/strict
  contract `setup.sh` has; both are Go binaries with weak or unconfirmed
  Windows support and neither has a "tag exists on mirror" check. skopeo has
  no Windows build; regctl and crane do only what `resolve-images.sh`
  already does with curl. gum and whiptail break under Git Bash's mintty
  pty (charmbracelet/gum#228). Ansible has no Windows control node.
- **Four patterns are worth copying:** debconf-preseed / Zarf's
  InteractiveVariable (declare each question ONCE with a default and a
  prompt-if-missing bit, so one schema drives both the interactive run and a
  prefilled answer file); KOTS's hard preflight gate between config and
  apply; Sentry's one-entrypoint-many-small-checks orchestration; rustup's
  fail-closed rule (an installer that cannot ask refuses to guess).

## What the audit found (the facts the decisions rest on)

- **Compose images are already one-place.** All eight pulled images flow
  through `deploy/single/images.txt` → `resolve-images.sh` → `CC_IMG_*` in
  `.env` → `${CC_IMG_X:-default}` in `compose.yaml`. The resolver's
  `fits()` already implements Renovate's docker rule (same suffix after the
  first hyphen, never cross a major silently).
- **The manifest does not reach the three local builds.** The Graphiti,
  sandbox and crawler Dockerfiles take the registry HOST as a build arg but
  carry their own base TAG (`1.1.0-standalone`, `3.12-slim-bookworm`,
  `v1.62.0-noble`). `images.txt` lists the same three tags in rows nothing
  reads — kept in sync by hand.
- **The sandbox image name is app code:** `central_command/sandbox/runner.py:55`
  (`localhost/cc-sandbox:1`), no override.
- **A path-renaming mirror has no seam.** Only the registry host varies;
  `env.example` says a re-namespaced path "needs the path edited in
  images.txt".
- **The LLM catalog is entirely outside `.env`.** Every alias is created as
  a `PLACEHOLDER` row in LiteLLM's database and the operator fills provider,
  model id and key in the LiteLLM web UI mid-setup (`llm_gate`, exit 3).
- **The env split is accidental.** Root `.env.example` has 116 assigned
  `CC_*` keys (the app; pydantic reads the root file), `deploy/single/env.example`
  has 35 (compose + acquisition seams); the real overlap is one key.
  `web/.env` is three lines written at boot. `deploy/discovery.conf` keys map
  one-to-one onto existing `CC_*` seams under a `DISCO_` prefix.
- **The tree currently gains generated files:** `installed.manifest`,
  `setup-log.txt`, `setup-diagnostics.txt`, `deploy/discovery.out/`.
- **Windows podman facts (doc-verified):** the Windows-side
  `~/.config/containers/registries.conf` is parsed but NOT honoured for a
  machine-backed connection (podman#16532); the file inside the WSL machine
  is authoritative. Per-registry `mirror` and `insecure = true` are
  `registries.conf` tables. A corporate CA is either
  `/etc/containers/certs.d/<host>/ca.crt` or the machine trust store; podman
  ≥ 6.0 (CORRECTED at P2 build time from the 5.9 recorded here: podman's
  RELEASE_NOTES lists the flag under 6.0.0) `podman machine set
  --import-native-ca` imports the HOST trust store
  into the machine at start (podman-machine-set docs; GA status of that flag
  in the Podman Desktop build at the site is UNVERIFIED). No proxy is passed
  through at `machine start`; proxy for pulls/builds is `containers.conf`
  `[engine] env` inside the machine. `podman machine ssh <name> <cmd>` is
  non-interactive. `podman compose` forwards `--env-file`; podman-compose
  implements `${VAR:-default}` / `${VAR:?err}` and a `config` subcommand.

## Decisions

### D1 — One operator-edited file: the repo-root `.env`

- `deploy/single/.env` is retired. Its 35 keys move into the root
  `.env` / `.env.example` under a clearly delimited `# --- Deployment
  (single-node) ---` section. `setup.sh`, `resolve-images.sh`, the three
  `build-*-image.sh`, `verify.sh` and `update.sh` read the root file; compose
  is invoked with `--env-file <repo>/.env`.
- `web/.env` is retired: the cockpit launcher exports `PORT`, `GATEWAY_URL`
  and `CC_UPDATE_BACKEND` from the root `.env` at start.
- `deploy/discovery.conf` is retired: `discover.sh` reads the same `CC_*`
  seams (`CC_CA_BUNDLE`, `CC_PROXY`, `CC_REGISTRY_*`, `CC_PYPI_INDEX_URL`,
  `CC_NPM_REGISTRY`, `CC_TLS_INSECURE`) from the root `.env`. Its report goes
  to stdout and the state directory (D7), not `deploy/discovery.out/`.
- **Shipped data files are not "extra artifacts".** `images.txt`,
  `compose.yaml`, `models.json`, the Dockerfiles are release content
  replaced wholesale by an update. The operator never edits them.
- The `.env` is the answer file in the preseed sense: filled on a connected
  machine, carried across with the zip, and `configure` (D6) only asks for
  what is missing.

**Why one file and not "one per concern":** the operator's stated failure
mode is drift between files holding the same fact. The overlap today is one
key, so the merge is plumbing, not a redesign.

### D2 — The image manifest reaches every image, and an operator pin wins

- `images.txt` remains the single list. Three changes:
  1. The three `*-base` rows are resolved like any other and passed to
     `podman build` as `--build-arg CC_IMG_<NAME>=<ref>`; each Dockerfile's
     `FROM` becomes `FROM ${CC_IMG_<NAME>}` (with the current ref as the
     `ARG` default so a bare `podman build` still works).
  2. The sandbox image name moves out of `runner.py` into `CC_SANDBOX_IMAGE`
     (default unchanged), read by the podman backend.
  3. **An operator-set `CC_IMG_*` is authoritative.** If the key is present
     in `.env` before resolution, the resolver verifies that exact ref exists
     (manifest HEAD) and never rewrites it. This is the seam for a
     path-renaming mirror and for "use this tag, I checked". A WARN names it
     as an operator pin so the record shows it.
- Substitution stays as D2 of the 2026-09-03 record defines it: locked tag →
  PASS; newest tag satisfying the constraint with the same flavour → WARN
  naming the substitution; nothing → FAIL naming the constraint, so the
  operator can pin. **Amended 2026-09-24 (v2.45.4):** on a CONFIGURED mirror
  a locked-tag digest mismatch is a WARN recorded as `locked-mirror`, not a
  FAIL — a mirror seeded by push re-serialises manifests, and the lock's
  digest guards public-registry poisoning, which the mirror does not carry.
  On the public host it stays a FAIL.
- `installed.manifest` moves to the state directory (D7).

### D3 — The LLM catalog is entered in the LiteLLM UI; `.env` may declare it

> **REWORDED 2026-09-24, on the Windows testbed run, by the operator.** The
> heading used to read "The LLM catalog is declared in `.env`; the UI becomes
> optional", and the phrasing had it backwards. **UI entry is the PRIMARY
> methodology, matching the k3s profile; the `.env` keys are an OPTIONAL
> convenience for a simple OpenAI-compatible upstream and are NOT required.**
> The operator's reasoning, in one sentence: LiteLLM handles provider nuance —
> credentials, per-provider parameters, routing, fallbacks — that a flat `.env`
> cannot, and running one method across both profiles beats maintaining two that
> drift. Consequences, all shipped in v2.45.1: the eight `CC_LLM_UPSTREAM_*`
> rows in `questions.tsv` are `required=n`; `check`'s `llm` section reports a
> blank catalog as ONE PASS line ("catalog will be entered in the LiteLLM UI —
> setup pauses at the llm phase (exit 3) until the aliases answer") and probes
> nothing, never a USERACTION, so the `all` gate lets it through; the `llm`
> phase's `catalog-declared` line is a PASS rather than a WARN, because a
> UI-driven install is not a degraded one and a WARN there made every successful
> UI-driven run finish at exit 2. **The `llm` phase's exit-3 pause is therefore a
> DELIBERATE exception to "a full run does not stop", not a defect** — the one
> place the deterministic driver hands the keyboard back on purpose.

- New keys: `CC_LLM_UPSTREAM_BASE_URL`, `CC_LLM_UPSTREAM_API_KEY`, and one
  `CC_LLM_UPSTREAM_MODEL_<ALIAS>` per required alias (`cc-default`,
  `graphiti-llm`, `cc-embedding`, `gpt-4.1-nano`, `cc-tts`, `cc-stt`). The
  site's enterprise endpoint is OpenAI-compatible, which LiteLLM expresses
  as `model: openai/<id>` + `api_base` + `api_key`.
- `register-models.py` stays create-only, but creates REAL rows from those
  keys instead of `PLACEHOLDER` rows when they are set. An alias left unset
  keeps today's behaviour (skeleton + UI pause), so nothing regresses for
  the connected install.
- `check` (D5) probes the upstream DIRECTLY from the host with curl
  (`/v1/models`, one chat completion, one embedding) before any container
  exists. That is what lets the LLM fail before setup rather than during it.
- LiteLLM's own outbound TLS follows the knob in D4 via `SSL_VERIFY` and
  `SSL_CERT_FILE` in the container environment (LiteLLM security-settings
  docs: precedence is parameter → `SSL_VERIFY` → `litellm.ssl_verify` →
  `SSL_CERT_FILE`).

### D4 — One trust knob, one insecure knob, fanned out everywhere; setup may write the podman machine

- `CC_CA_BUNDLE` (exists) and new `CC_TLS_INSECURE=0|1`. Exactly two keys.
  Per-tool knobs are not exposed; per-tool granularity is what drifts.
- Fan-out table (all names doc-verified 2026-09-23):

  | Consumer | CA | Insecure |
  |---|---|---|
  | curl (host) | `CURL_CA_BUNDLE` **plus a `cacert` line in the generated `.curlrc`** (CORRECTED on the 2026-09-24 Windows run: a Schannel curl — what Git for Windows ships — ignores the variable, and `--cacert` is what it honours; the CA does NOT have to be in the Windows Root store) | `-k` via the same `.curlrc` in the state dir, `CURL_HOME` pointed at it |
  | uv | `SSL_CERT_FILE` (`UV_NATIVE_TLS` is deprecated → `UV_SYSTEM_CERTS`) | `UV_INSECURE_HOST=<index host>` |
  | pip (builds) | `PIP_CERT` | `PIP_TRUSTED_HOST=<index host>` |
  | npm | `NPM_CONFIG_CAFILE` | `NPM_CONFIG_STRICT_SSL=false` |
  | node | `NODE_EXTRA_CA_CERTS` | `NODE_TLS_REJECT_UNAUTHORIZED=0` |
  | git | `GIT_SSL_CAINFO` | `GIT_SSL_NO_VERIFY=1` |
  | podman pulls (machine) | `certs.d/<registry>/ca.crt` or `--import-native-ca` | `insecure = true` per `[[registry]]` in the machine's `registries.conf` |
  | podman build | `--cert-dir` (WSL2 machines only) | `--tls-verify=false` |
  | apt (builds) | CA copied in via build context | `Acquire::https::Verify-Peer "false"` as a build arg |
  | LiteLLM container | `SSL_CERT_FILE` | `SSL_VERIFY=False` |
  | Hugging Face (speech) | `REQUESTS_CA_BUNDLE` (inherited) | **none exists** — CA route or pre-placed snapshots only; `check` says so |

- Every run with `CC_TLS_INSECURE=1` prints one WARN line naming the fact.
  It is never silent and never a PASS.
- **Setup writes the machine.** From `.env`, `setup.sh` (a new `machine`
  step inside `preflight`'s successor, see D5) applies over `podman machine
  ssh`: the CA into the trust store (or turns on `--import-native-ca` where
  the podman version supports it), the `[[registry]]` mirror / insecure
  tables, and the proxy in `containers.conf`. `check` reports the CURRENT
  machine state and the diff setup would apply; `setup` applies it. The
  operator authorised this on 2026-09-23. The Windows-host registries file
  is never written — it is not honoured.

### D5 — `check`: everything dry, one table, exit codes as today

A new top-level command. It runs every existing side-effect-free check plus
the gaps, and it **writes nothing inside the checkout**. Sections, in order:

1. **answers** — `.env` present; every REQUIRED key set (schema, D6); no
   `localhost` (loopback rule); ports valid, unique and FREE.
2. **host** — podman version and machine running; compose provider;
   tools (curl, git, uv, node, python); RAM / disk; Windows CA store.
3. **machine** — CA trusted INSIDE the machine (a curl through the mirror
   from `podman machine ssh`, not a settings read — Podman Desktop's CA
   propagation is a known rough edge, podman-desktop#3821); registries
   tables present; proxy present; diff vs `.env`.
4. **images** — `resolve-images.sh --dry-run` over all rows, including the
   three build bases; operator pins verified; substitutions listed.
5. **indexes** — PyPI simple index and npm registry answer; `uv pip install
   --dry-run` resolves (or the lock in `CC_AIRGAP=1`); apt mirror answers a
   `Release` file; Python-build-standalone mirror if uv must fetch a Python.
6. **llm** — upstream reachable, listed models include every
   `CC_LLM_UPSTREAM_MODEL_*`, one chat and one embedding round trip.
7. **compose** — `podman compose --env-file .env config` renders with no
   unresolved `${VAR:?}`.
8. **models** — HF endpoint (or pre-placed snapshots) for the speech pair;
   whisper GGUF source or dir for the cockpit.

Output is the existing protocol: one `PASS|WARN|FAIL|USERACTION <check>:
<message>` per line, secrets never printed, exit 0/1/2/3 with the existing
precedence. `setup` re-runs `check` first and refuses to proceed on FAIL
(the KOTS gate); WARN requires `--accept-warnings` or an interactive yes.

**Honest ceiling, recorded:** `check` cannot prove a local image BUILD
succeeds (the apt/pip/npm work happens inside the build), and it cannot
measure `CC_EMBED_DIM` (that needs the embedder answering through LiteLLM —
though with D3 the upstream embedding probe gives the dimension early, and
`check` may pre-fill it). The target is "no CONFIGURATION failure at
setup", not "no failure of any kind".

### D6 — `configure`: one question schema, plain prompts, fail-closed

- A question schema in the tree (data, not code): key, prompt text,
  default, required?, validator, and a `when` guard (e.g. HF keys only if
  `CC_ENABLE_SPEECH=1`). Read by `configure` to ASK and by `check` to
  VALIDATE, so a new dependency is a new row.
- Plain bash `read -rp` under Git Bash; no gum, no whiptail (both fail on
  mintty). When stdin is not a TTY, `configure` does not guess: it lists the
  unanswered required keys and exits 3 (rustup's rule).
- It only ever writes keys into `.env`, idempotently, and prints a diff of
  what it will write before writing. Secrets generated by `make-secrets.sh`
  are filled here too, so `.env` is complete before `check` runs.
- Operator name, install directory and feature flags (the profiles) are
  questions here. The cockpit's first-run name prompt stays as the fallback.

### D7 — Generated files live outside the checkout

- `CC_STATE_DIR` (default `~/.central-command/<install-id>/`; Windows:
  under `%USERPROFILE%`) holds `setup-log.txt`, `diagnostics.txt`,
  `installed.manifest`, the generated `.curlrc`, and discovery evidence.
  Nothing under `deploy/` is written by any command.
- stdout is the primary record; the log exists so the agent can triage
  after scrollback is gone. `./setup.sh diagnose` prints the state dir path.

### D8 — Not adopted, so the question stays closed

- goss, Replicated troubleshoot, Ansible, bats: no Windows fit or no
  benefit over the existing 30-line pass/warn/fail trio. Their CONTRACT is
  already ours.
- regctl / crane / skopeo: the resolver already speaks Registry v2 with
  curl; skopeo has no Windows build.
- gum / whiptail: broken under mintty.
- Zarf / Hauler / bundles: solve mirror seeding; the site has a mirror
  (`airgap-bundle-decision`, 2026-08-31, stands).
- A TUI or a Python/Node installer: the check must run before the venv and
  node_modules exist.

## Phasing (each a release; each verified on a Windows Podman Desktop testbed)

1. **P1 — One file (DONE, v2.42.0).** D1 + D7. Pure plumbing; the suite's env guard tests
   and `verify.sh` change with it. Acceptance: a fresh clone + one `.env`
   installs on Linux; `git status` clean after every command.
2. **P2 — Seams reach everything (DONE, v2.43.0).** D2 + D4 (fan-out and the machine
   writer). Acceptance on the laptop with a local registry standing in for
   the mirror: a base-tag substitution and a path-renamed image install
   with no edit outside `.env`; `CC_TLS_INSECURE=1` completes against a
   self-signed mirror with one WARN per run.
3. **P3 — `check` (DONE, v2.44.0).** D5, with D3's upstream probe. Acceptance: with the
   network cut and the mirror missing one tag, `check` exits 3 naming the
   tag and the pin key; after the pin, exits 0; `setup` then completes with
   no FAIL.
4. **P4 — `configure` (DONE, v2.45.0).** D6. Acceptance: an empty `.env.example`
   copy becomes a green `check` through prompts alone; the same run from a
   prefilled `.env` asks nothing. The second half is a test
   (`tests/test_single_configure_preseed.py`: empty, absent and prefilled `.env`,
   the last byte-identical); the first half is the Windows testbed's, because the
   prompts need a real TTY and the green `check` needs the mirror.
5. **P5 — Docs and skill (DONE, v2.45.0).** `AIRGAP.md` rewritten around the two
   knobs and the loop; the `/setup` skill conducts `configure → check → (triage)
   → check → setup` and never edits anything but `.env`.

P2–P4 need a Windows Podman Desktop testbed with a local registry that can
be made to lack a tag and to present a private CA; it is the acceptance
environment, not the developer's Linux box. What ONLY that testbed can settle
for P4: whether `read -rp` under mintty behaves (a non-TTY stdin there is the
`winpty` symptom `configure`'s summary line names, and it is the one branch a
Linux box cannot exercise), and whether `read -rsp` echoes nothing for the key.

## Open items (do not block P1)

- The LiteLLM and speech containers get the CA and the insecure flag (P2)
  but NOT `CC_PROXY`: the machine drop-in sets the ENGINE's proxy (pulls and
  builds), and containers.conf's `[engine] env` is documented as not reaching
  containers. If the site's LLM endpoint sits behind a proxy, add
  `HTTP(S)_PROXY`/`NO_PROXY` to those two services from `CC_PROXY` — decide
  on the testbed, where an empty-string proxy variable's effect on httpx can
  be observed rather than assumed.

- Podman version in the site's Podman Desktop build: decides whether
  `--import-native-ca` or the `certs.d` write is the primary CA path.
- Whether machine edits survive a Podman Desktop upgrade (unverified);
  `check` re-verifies each run either way.
- ~~`CC_EMBED_DIM` pre-fill from the upstream embedding probe~~ **DECIDED in
  P3 (v2.44.0): both, and the rule is untouched.** `check` MEASURES it against
  the upstream embedder from the host and writes it into `.env` only when the
  key is unset (that write is inside check's "may update `.env`" allowance, and
  its PASS line says so). The `llm` phase still measures it through the proxy
  ALIAS — the path production takes — and still FAILS on a mismatch with the
  recorded value rather than overwriting it. Nothing is ever declared: both
  numbers come from an endpoint returning a real vector.

- ~~**A WARN-only `check` stops a non-interactive `all` run** on a fresh `.env`,
  over two WARNs nobody can act on — the credentials `make-secrets.sh` has not
  generated yet~~ **CLOSED in P4 (v2.45.0):** `configure` runs `make-secrets.sh`
  itself, so the credentials exist before `check` ever runs. A run that SKIPPED
  `configure` still sees those two WARNs, which is now the correct reading of
  them — it is what an install that did not answer its questions looks like.

- `update.sh import` (Windows testbed, 2026-09-24): the unzip + tar-pipe tree
  sync took over an hour on NTFS with Defender for a tree `git clone` wrote in
  four minutes. A git-native checkout of the imported release is the lever; a
  design change to the importer, not a patch. Also: `unzip` prints a symlink
  error and exits 0, so an imported tree can silently lose a link.
