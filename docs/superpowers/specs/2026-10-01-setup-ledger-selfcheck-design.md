# The install remembers what it did, proves it as the app, and has one definition

> **Status:** implemented — P1 v2.55.0 (D1, D2, D3, D5's exit-code rule and
> `fetch` fix, D10); P2 v2.56.0 (D4, and from D11 the `started` status, the
> run lock, the plan, in-process redaction, the readiness-only rule); P3
> v2.57.0 (D6, D7, the rest of D5); P4 v2.58.0 (D8, the remaining `human`
> rows, D11's driver split); P5 v2.58.2 — D9's acceptance run on a Windows
> laptop, three passes, the last one clean. What is NOT proven is listed under
> "What the acceptance run proved, and what it did not".
> Decisions D1–D10 taken with the operator on 2026-10-01; D11 (precedents,
> seven adoptions) approved 2026-10-02.
> **As-built:** P1 — `deploy/single/steps.tsv` (new), `deploy/single/ledger-lib.sh`
> (new), `deploy/single/setup.sh`, `deploy/single/update.sh`, `deploy/env-lib.sh`,
> `.claude/settings.json` (new), `.claude/hooks/guard-install-tree.sh` (new),
> `tests/test_single_steps_schema.py`, `tests/test_single_ledger_lib.py`,
> `tests/test_single_driver_ledger.py`, `tests/test_single_report_redacts.py`,
> `tests/test_install_tree_hook.py` (all new). P2 — `central_command/selfcheck.py`,
> `central_command/api/selfcheck.py`, `deploy/single/redact.tsv`,
> `tests/test_selfcheck.py`, `tests/test_selfcheck_api.py`,
> `tests/test_single_selfcheck_row.py`, `tests/test_single_run_lock.py`,
> `tests/test_single_mint_key_scope.py` (all new); `deploy/single/setup.sh`,
> `deploy/single/ledger-lib.sh`, `deploy/single/steps.tsv`,
> `deploy/single/update.sh`, `deploy/env-lib.sh`,
> `web/src/features/systems/SystemsView.tsx`, `web/server/routes/cc-systems.ts`.
> P3 — `deploy/single/supervise-lib.sh`, `tests/test_single_boot_supervision.py`,
> `tests/test_single_update_acquire.py` (new); `deploy/single/setup.sh`,
> `deploy/single/update.sh`, `deploy/single/update-run.sh`,
> `deploy/single/steps.tsv`, `deploy/single/ledger-lib.sh`,
> `deploy/single/make-secrets.sh`, `deploy/single/resolve-images.sh`.
> P4 — `deploy/single/phases/<phase>.sh` (ten, new), `deploy/single/CHECKLIST.md`,
> `scripts/render_checklist.py`, `scripts/checklist_template.md`,
> `tests/installer_source.py`, `tests/test_single_phase_files.py`,
> `tests/test_single_checklist.py`, `tests/test_single_human_rows.py`,
> `tests/test_single_n8n_workflows_row.py`, `tests/test_single_no_phase_hints.py`
> (all new); `deploy/single/setup.sh`, `.claude/skills/setup/SKILL.md`, the READMEs.
> **Scope:** the single-node profile (`deploy/single/`), Linux and Windows
> Podman Desktop. The k3s profile is OUT of scope for this record and gets a
> follow-on record once P1–P4 are proven; nothing here changes
> `deploy/k3s/`.
> **Builds on:** `2026-08-25-deterministic-setup.md` (output protocol, exit
> taxonomy, "the script is the spine, the agent is the exception handler"),
> `2026-09-23-airgap-check-configure-setup-design.md` (one answer file, the
> question schema, the dry gate, the trust knobs). Keeps all of them.
> **Retires:** `./setup.sh <phase>` as an OPERATOR command; `phase_test`'s
> "skipped — the API is up" branch; the Windows logon task's `boot`-only
> wrapper; the `tail -40` diagnose window; the three exit-code precedence
> rules; `update.sh`'s "your committed changes preserved by three-way merge"
> allowance (D10 — a deployment carries no local patches). `update.sh init`'s
> `git add -A`, which snapshots a freshly unzipped tree with no history as
> its baseline, STAYS — that snapshot is what makes the first `apply` a
> clean fast-forward instead of a local patch.

## The problem, measured

On 2026-10-01 the operator asked why every deployment "comes off the rails,
some step was missed, something wasn't done in order", and why it is not
deterministic. The investigation (recorded in the private instance repo's
journal and memory) read the installer in full and catalogued 49 journal
incidents, 68 testbed-ledger findings and 52 install-related releases since
2026-08-31. The classes that recur are not exotic:

| class | incidents |
|---|---|
| platform difference (Windows, MSYS, podman machine, CRLF) | 22 |
| a phase passed green but had not done its job | 11 |
| `.env` drift (blank, not derived, wrong file, CRLF) | 9 |
| missing checklist or doc | 5 |
| steps out of order / a step skipped and never re-run | 7 |

Three absences explain nearly all of the non-platform rows, and the
platform rows are what the absences let through:

1. **No record of what completed.** A phase is one linear bash function; a
   mid-function `return 1` abandons every later step in it and nothing
   writes that down. `phase_app` has 14 steps; its mint-key step (5) returns
   on failure and skips nine `.env` writes and the cockpit build. The state
   directory holds image provenance, pid files and one stamp file. "Resume
   is re-run" is implemented by three reality probes, by `.env` placeholder
   tests (a present value stands in for "done", an absent one for "not
   done"), and otherwise by assumption. `./setup.sh boot` after a failed
   `app` is accepted, which is exactly the 2026-10-01 work-site state: an
   empty `CC_LLM_API_KEY`, eight blank Systems links, a green `verify`.
2. **Nothing in the pipeline acts as the application.** `verify.sh` proves
   the deployment with `CC_LLM_PROXY_ADMIN_KEY`. It never reads the spine
   key, `CC_DEFAULT_MODEL`, `CC_LLM_BASE_URL`, `CC_DATABASE_URL`, the sandbox
   token or any link; it never touches the API's own port or the cockpit.
   The first exercise of the app's credential is `demo`, the last phase,
   gated on a human click and skipped forever once one decided proposal
   exists. The spine key has gone missing three times by three mechanisms
   (2026-09-18 a CRLF template read as "set"; 2026-09-26 the k3s mint script
   wrote an empty key and the step reported green; 2026-10-01 the single-node
   `app` phase aborted first). Each fix was local to its mechanism.
3. **The process is defined in code once and described in prose six times.**
   The phase list lives in `main()`'s loop; `README.md`,
   `deploy/single/README.md`, `deploy/AIRGAP.md`, `.claude/rules/deploy-single.md`,
   `.claude/skills/setup/SKILL.md` and `docs/ARCHITECTURE.md` each restate
   it, one of them wrongly. One regression test pins four of the copies. No
   operator-facing document is a numbered checklist, and fourteen required
   steps (prerequisites, the LiteLLM catalog, naming the operator, the n8n
   credential, importing the seven skill folders, starting the sandbox
   runner, …) are commands in no sequence at all.

Two protocol defects compound them: `phase_fetch` can never return 1 (its
only FAIL path is followed by a USERACTION, and the phase runner ranks
USERACTION above FAIL), and `update.sh apply` merges the new code BEFORE
`fetch` runs, so a fetch pause leaves a merged tree with the old venv and
`.env`, reported as exit 3. The exit-code precedence differs between the
phase runner, the `machine` subcommand and `update.sh`.

The operator's standard, verbatim: "a robust and repeatable setup process
that will never ever break … the only thing the agent should be doing is
modifying the config or `.env`, or flagging issues". What this record can
honestly promise is narrower and stated at the end: every recorded
process-class failure becomes impossible or loud, and every other failure
has one deterministic recovery, which is "fix `.env` and run the one command
again".

## Decisions

### D1 — The process is one data file: `deploy/single/steps.tsv`

The precedent is `questions.tsv` (2026-09-23 D6): a seam is a row, and two
commands read the same rows. `steps.tsv` declares every step of the install
once, in run order. Columns, tab-separated, `-` where empty:

```
phase     check | machine | fetch | llm | stack | app | verify | boot | demo
step      the step's name — the PASS/FAIL check-name it prints
kind      run    = the driver executes it
          gate   = the driver STOPS here until a human has acted (exit 3);
                   the row's `probe` is how it knows they have
          human  = never executed by the driver; a thing the operator does
                   in a UI, recorded in the ledger when its probe passes
requires  comma-separated steps that must be DONE first (same or earlier phase)
reads     the .env keys whose VALUES are this step's inputs (fingerprinted, D2)
writes    the .env keys and state-dir artifacts this step is the ONLY writer of
probe     a bash function in setup.sh: 0 = the step's effect is present now
doc       one plain sentence for the generated checklist
```

Rules the schema carries, each guarded by `tests/test_single_steps_schema.py`:

* every `set_kv` / `set_kv_if_unset` target in `deploy/single/*.sh` appears in
  exactly one row's `writes` (a key written outside the manifest is the
  v2.52.0 "eight blank lines nobody types" bug, and v2.48.0's missing
  `CC_LLM_BASE_URL` before it);
* every `reads` and `writes` key has a line in `.env.example`;
* every `probe` names a function that exists and that
  `test_single_check_is_dry.py`'s walker classifies as non-mutating;
* `requires` is acyclic and never points forward;
* the `human` rows cover the fourteen non-command steps the investigation
  listed — the LiteLLM catalog (`llm/catalog-filled`, probe: the required
  aliases answer), the operator's name (`boot/operator-named`), the demo
  approval (`demo/demo-approved`), the n8n `Gmail account` credential when
  `CC_ENABLE_N8N=1`, the corporate CA placed on disk when `CC_CA_BUNDLE` is
  set. Prerequisites the host must already have (podman-compose ≥ 1.6.0,
  CPython 3.12, Git Bash) stay `check` rows: they are probed, not performed.

The phase functions stay. P1 does not rewrite 3,200 lines of bash into a
step interpreter; it makes the EXISTING phases accountable to the manifest.
Each phase declares its rows, the driver runs the phase function as today,
and afterwards runs every row's `probe`. A phase is DONE only when every one
of its rows probes true. A phase that returns 0 with a row probing false is a
FAIL naming the row — which is what turns "the step after the one that
failed never ran" from invisible into a line.

### D2 — The ledger: `<state>/ledger.tsv`, and resume reads it

One row per step, rewritten atomically after every step:

```
step  status  version  at  fingerprint  reason
```

`status` is `done | failed | pending | gate`, and since P2 `started` (D11):
every row of a phase is written `started` before the phase function runs, so
a run that is killed leaves "started, not finished" behind instead of rows
that look as if nothing had been tried. `version` is `VERSION` at the
time. `fingerprint` is sha256 over the step's `reads` keys' VALUES (the
operator's answers that shaped it) and, for `fetch` rows, the resolved
image digests. `reason` is the last FAIL/USERACTION message, verbatim.

The driver's rule for every row, in manifest order:

1. `requires` not all `done` → STOP: `FAIL <step>: requires <x>, which is
   <status>` (exit 1). Never run ahead.
2. `done` at this `version` with an equal `fingerprint` AND `probe` true →
   skip, print `PASS <step>: done (<at>)`.
3. otherwise run it (or, for `gate`/`human`, probe it) and record the result.

So: an `.env` edit re-runs exactly the steps whose inputs it changed; a
release bump re-runs every step (the existing inner idempotency —
`have_image`, `api_up`, `set_kv_if_unset` — keeps that cheap); a
mid-function abort leaves `failed` rows that block everything downstream;
and the resume command is always the same command (D3). The ledger is the
diagnose bundle's first section (D8) and `./setup.sh status`'s whole output.

A `human` row with a false probe is a `gate`: the run stops with exit 3 and a
USERACTION naming what to do and where (the LiteLLM UI URL, the cockpit
URL). That is the existing `llm` pause (2026-09-23 D3, "a deliberate
exception to 'a full run does not stop'"), generalised and recorded.

### D3 — One command, and phases are not operator commands

`./setup.sh` is the command. It runs `check` (the dry gate, unchanged),
then walks `steps.tsv` under D2's rule, and ends by printing the ledger
table. `./setup.sh status` prints the ledger and the self-check (D4) and
changes nothing. `./setup.sh diagnose` and `./setup.sh stop` stay.

`./setup.sh <phase>` remains for development and is REFUSED with exit 1
when the phase's `requires` are not `done`. There is no `--force`; the
operator decided (2026-10-01) that the escape hatch is the defect. A
developer bypass is the environment variable `CC_SETUP_UNLEDGERED=1`,
documented only in `.claude/rules/deploy-single.md`, never in an operator
document, and refused when `.env` carries `CC_EXECUTOR_MODE=live`.

The `/setup` skill's contract becomes three sentences: run `./setup.sh`;
read the ledger it prints; either change a `.env` key the FAIL line names
and run `./setup.sh` again, or report the ledger row as a repository defect.
The skill never names a phase.

### D4 — A self-check that is the application: `central_command/selfcheck.py`

A module in the app, run two ways with one implementation:

* `python -m central_command.selfcheck` from the install's `.venv`, with
  the repo root as cwd (the API's own `Settings` load, the same `.env`),
  printing the protocol lines; the `verify` phase runs it AFTER `verify.sh`
  as the row `verify/selfcheck`, and `boot` and `demo` `require` it;
* `GET /api/selfcheck` in the running API, the same checks from inside the
  process that serves the agents, rendered on the Systems page beside the
  reachability dots and readable by the agent.

What it asserts, each with the setting the app actually uses and the
credential the app actually holds:

| check | how |
|---|---|
| spine | `CC_DATABASE_URL` connects; the six schema tables exist; the roster is non-empty |
| proxy-as-app | `GET {CC_LLM_BASE_URL}/v1/models` with `CC_LLM_API_KEY`; `CC_DEFAULT_MODEL`'s name is in the list |
| completion-as-app | one `max_tokens=1` request through `runtime.models.resolve_model(CC_DEFAULT_MODEL)` — the seam a real run takes, not a curl |
| embedding-as-app | one embedding of a fixed string via `CC_EMBED_ALIAS` with the key the graph writer uses; dimension equals `CC_EMBED_DIM` |
| graph | Graphiti status via `CC_GRAPHITI_MCP_URL`; Neo4j reachable behind it |
| sandbox | `CC_SANDBOX_RUNNER_URL` answers with `CC_SANDBOX_RUNNER_TOKEN`, when `CC_ENABLE_SANDBOX=1` |
| crawler | `{CC_CRAWLER_URL}/healthz`, when `CC_ENABLE_CRAWLER=1` |
| mail | the configured provider lists one ref — Exchange when `exchange.configured()`, else the n8n façade — and ONLY when something in the app reads mail: `CC_FEED_ENABLED=1`, or an enabled heartbeat `feed.poll` schedule, or Exchange configured. (As built in v2.58.2; the first version keyed on `CC_ENABLE_N8N`/the façade token and FAILed every default install, because `make-secrets.sh` always generates the token and the k3s profile does not set the flag.) |
| integrations | `scripts/atlassian_probe.py`'s checks, when Jira/Confluence are configured |
| cockpit | `http://127.0.0.1:{CC_COCKPIT_PORT}/` answers (CLI mode only) |
| links | every `CC_*_UI_URL` / `_DOCS_URL` the Systems page would show is either blank by flag or answers |

Every FAIL names the `.env` key or the phase to re-run. The completion and
embedding checks spend tokens; they are two requests, and they are the only
two in the whole install that prove what the agents will experience.
`verify.sh` stays as the deployment's own proof under the admin key; the two
are different questions and both are asked.

Known consequence, MEASURED in P2 (2026-10-02) and not what this paragraph
first guessed: the minted spine key was scoped to `cc-default`, `cc-tts`,
`cc-stt` (`setup.sh` mint-key), and the graph writer embeds through
`CC_EMBED_ALIAS` — but it does so with `CC_LLM_PROXY_ADMIN_KEY`
(`integrations/neo4j_writer.py`'s `embed()`), not the spine key, so
`embedding-as-app` asks with the admin key because that is the credential
the writer holds, and the narrow scope never broke embedding. The scope fix
ships in P2 anyway, for the reason that survives the measurement: the
hard-coded three-alias list was a second copy of `cc_required_aliases` that
had already drifted from it. The key is minted over that ONE list, and an
existing key gains the aliases it lacks in place — never re-minted, and
never narrowed.

Two things the operator decided on 2026-10-02 that the table above does not
say. **The API's result is CACHED**: `GET /api/selfcheck` returns the last
run and spends nothing; a run happens once at API start and when someone
asks for one (`POST /api/selfcheck/run`, the Systems page's button). The
completion and embedding checks each spend a model request and, on a
single-slot local backend, queue behind running sessions — a check that runs
on every page view would make looking at the Systems page a cost.
**The installer's run is `--pre-boot`**: `verify` comes before `boot`, so the
two checks whose subject `boot` starts (the cockpit, the sandbox runner)
report "not checked before boot" there and are asserted by `./setup.sh
status`, which runs the whole table.

### D5 — One exit-code rule, no swallowed failure, acquire before merge

* ONE function, `cc_exit_code <fails> <warns> <actions>`, in `env-lib.sh`,
  precedence `FAIL > USERACTION > WARN` everywhere — a FAIL is never
  reported as "stopped for your action". `run_phase`, the `machine`
  branch, `update.sh main` and `update-run.sh` all call it. The one case
  where a USERACTION should win — the `llm` catalog gate — prints no FAIL,
  so nothing is lost.
* `phase_fetch` returns 1 on a FAIL; `useraction` is reserved for the seam
  the operator must fill (a mirror that lacks a tag). A failed local build
  is a FAIL.
* `update.sh apply`: `fetch` and `llm`'s catalog probe run against the
  STAGED tree BEFORE `git merge`, so the comment "acquire before mutating"
  becomes true; a fetch stop leaves the tree untouched. After the merge the
  order is schema → llm → app → verify → selfcheck, each a ledger row, and
  an exit 3 from any of them stops the run with the ledger showing it — it
  never `return 0`s past `app` and `verify`.
* Every `step` whose body is `A || B; C` is split so a failing `A || B` is a
  FAIL; `cmd_stop` no longer silences `load_env`; `machine_sh`'s stderr is
  kept in the log.
* `phase_test` no longer skips when the API is up. Its ledger row is
  `done` per version: the suite runs once per release, which the ledger
  expresses without the `api_up` proxy.

**As built (P3, v2.57.0).** The acquisition runs from a SPARSE `git worktree`
of `upstream` under the state dir, through a new `setup.sh acquire` that only
a staged run may call (`CC_STAGED_FOR` names the deployment): the new
release's fetch phase plus the catalog probe. It writes no ledger row and
resolves image refs into copies, so the install's `.env` and
`installed.manifest` do not move before the merge — which is why the
post-merge order gained a `fetch` the bullet above does not list, and a
`stack` (schema → fetch → llm → stack → app → n8n → verify; that fetch is
fast, everything being present, and is the one that writes the real refs and
rows). `stack` was never part of an update before and this record did not
ask for it; it is there because the build exposed that an update deployed
nothing it acquired — podman-compose recreates a container only when the
service's config hash changes, the hash holds the image ref and not its ID,
and the local images' tags are fixed, so a release that changed a Dockerfile
neither rebuilt (the tag existed) nor restarted anything. Local images now
carry a `cc.build-inputs` label and rebuild when it differs, `stack` and
`llm` recreate a service whose container is not running the image its ref
resolves to (a stateful one only while its data is on a named volume), and a
staged build is tagged aside so that the acquisition moves no live tag. (P4 moved the n8n façade workflows
out of the updater's own sequence and into the `stack` phase as a manifest
row, so the order reads schema → fetch → llm → stack → app → verify.) `acquire`,
`CC_STAGED_FOR` and the output protocol are now an interface BETWEEN releases.
Three things this record did not foresee were decided in the build. A
deployment that predates the ledger has no rows, so the first post-merge
phase would be refused and the cockpit's runner would roll back to a tree
that can never write one: the updater stops instead with one USERACTION —
run `./setup.sh` once, which ADOPTS the running deployment — and the ledger
gate gives an older updater the same answer. `update-run.sh` rolled back on
any exit 1, including a stop before the merge, to an EARLIER update's tag (a
downgrade): it now rolls back only when HEAD moved. And an exit 2 from a
post-merge phase is a WARN, not the FAIL that used to roll an update back
over a node-version warning.

Open after P3, by this record's own design: an update records `fetch`,
`llm`, `stack`, `app` and `verify` at the new version and nothing else, so
the next `./setup.sh` — the logon run included — re-runs `machine` and the
suite before `boot`. The suite is once per release either way; the question
left to the operator is whether it should run at update time (adding `test`
to the post-merge order) rather than at the next boot. Also open: every
`podman` call the image catch-up makes has run against a stub and, read-only,
against a Linux podman 4.9 — its first real run is P5's; and a rebuild leaves
the previous image untagged in podman's storage, which nothing prunes.

### D6 — Everything the install starts, it supervises

`boot` starts, and `stop` stops, exactly three host processes: the API, the
cockpit server and the sandbox runner. Each has a pid file and a log in the
state dir, and `stop` proves the PORT is free rather than trusting the pid
(the 2026-09-18 and 2026-09-25 false-green `stop` findings).

* **Linux:** `boot` writes three `systemd --user` units into
  `<state>/systemd/` and enables them (`loginctl enable-linger` is a `check`
  row; its absence is a FAIL naming the command). The units run the same
  commands `boot` runs by hand.
* **Windows:** the logon wrapper runs `./setup.sh` — the resume command —
  not `boot`. After a reboot that means "start whatever is not running", and
  on a box whose install never completed it means a `failed` ledger row in
  `boot-at-logon.log` instead of silence.

The runner's `CC_SANDBOX_RUNNER_TOKEN` is generated by `make-secrets.sh`
like the other eight credentials; today it is never generated and the
shipped posture is an unauthenticated runner.

**As built (P3, v2.57.0).** Unit names are `cc-<install-id>-{api,sandbox,
cockpit}.service`, rendered by pure functions in `supervise-lib.sh`, and
`boot` starts the processes THROUGH them (`systemctl --user restart`); a host
with no user manager falls back to a detached start with a WARN. The Linux
path is proven by stub-driven execution tests and `systemd-analyze --user
verify` on the rendered units — the operator's decision, 2026-10-02, there
being no Linux single-node host — so a real Linux boot is UNPROVEN and says
so here. The logon wrapper does run `./setup.sh`, and two things this section
did not spell out were added: `--accept-warnings` (a headless WARN-only
`check` otherwise stops the run) and a retry loop, ten attempts a minute
apart, because at logon the podman machine may not be up yet. The cost is
that a logon now runs the dry `check` before anything starts; whether that is
acceptable on a slow or flaky site is one of the things P5 measures. Found
while building: the detached start recorded a wrapper shell's pid rather than
the server's, which is what the two false-green `stop` findings were.

### D7 — Importing the bundled skills is a step

`boot/skills-imported`, after the roster: for each `skills/*/SKILL.md`,
`POST /api/skills/import` when `GET /api/skills` does not already hold that
`skill_id`. Create-only, like `register-models.py`; a release that changes a
bundled skill re-imports it from `update.sh` under the same rule, because
the ledger row's fingerprint includes the folder's tree hash.

**As built (P3, v2.57.0).** The route is the Python API's own
`POST /api/skills/import` (`{"path", "skill_id"}`), so the step needs neither
node nor the cockpit. CREATE-ONLY is the whole rule: a release that changes a
bundled skill makes the row RE-RUN (its `reads` carries `@skills`, a content
hash of the directory — the one non-`.env` input the manifest knows), and
running it imports only ids the library does not hold. A skill that exists is
never overwritten and a retired one is never resurrected.

### D8 — The checklist is generated, and the diagnose bundle shows the ledger

`scripts/render_checklist.py` renders `deploy/single/CHECKLIST.md` from
`steps.tsv`: the prerequisites, then every row in order with its `doc`
sentence, the `human` rows set in bold with WHERE to do them. It is
committed, and `tests/test_single_steps_schema.py` fails when the committed
file differs from a fresh render. `README.md` and `deploy/single/README.md`
link to it as THE install procedure and stop restating the phase list;
`tests/test_setup_phase_docs.py` extends to pin `SKILL.md` and both READMEs
to the manifest's phase order. `docs/ARCHITECTURE.md`'s stale phase list is
corrected in the same change.

`./setup.sh diagnose` leads with the ledger, then the self-check output,
then the ENTIRE log of the last run (runs are delimited by their `run start`
line, so no `tail -40`), then the tails of `uvicorn.log`, `cockpit.log` and
`sandbox.log`, then everything it captures today.

**As built (P4, v2.58.0).** The renderer and the stale-file test live in
`scripts/render_checklist.py` and `tests/test_single_checklist.py`; the static
prose around the steps is one template, `scripts/checklist_template.md`. The
doc pin was INVERTED rather than extended: `tests/test_setup_phase_docs.py`
used to require four documents to carry the phase list and now requires that
they do not — they link to the checklist, and any phase sequence a document
still names must be in manifest order. The skill is rewritten around the
three verbs (D10.4's paragraph pinned verbatim) and gives `configure`,
`update.sh` and `stop` to the operator. Two things this section did not list
shipped with it because the checklist made them visible. The messages
themselves still named phases to run (`run: ./setup.sh app`) and `diagnose`;
they say `./setup.sh` and `report` now, and a guard test walks them. And the
n8n façade workflows were applied only by `update.sh`, so a fresh install
with `CC_ENABLE_N8N=1` had none: `stack/n8n-workflows` is a manifest row
after the credential gate, and the gate asks for every credential the import
binds by name.

### D9 — Proof: execution tests on Linux, a laptop run before the tag

* `tests/test_single_driver_ledger.py` runs the REAL `./setup.sh` against a
  stub `PATH` (stub `podman`, `curl`, `uv`, `npm` — the pattern
  `test_update_runner.py` already uses) and asserts: an injected failure in
  `app`'s mint-key leaves `app/mint-key failed` and every later `app` row
  `pending`; `./setup.sh boot` then exits 1 naming `app/mint-key`; after
  the stub is fixed, `./setup.sh` resumes at `app/mint-key`, skips every
  `done` row, and ends with the ledger all `done`; changing `CC_LITELLM_PORT`
  invalidates exactly the rows that read it.
* `tests/test_selfcheck.py` runs the module against mocked endpoints and
  asserts every row in D4's table can FAIL by name.
* The acceptance run is a fresh install on the rebuilt garage laptop
  (operator decision, 2026-10-01), driven by the session, reviewed by the
  operator from the ledger alone: (1) kill LiteLLM during `app`, confirm
  the refusal and the resume; (2) reboot, confirm `status` all green
  including the sandbox; (3) `update.sh apply` with the in-machine mirror
  missing one tag, confirm the tree is untouched; (4) the demo proposal
  reached through `CHECKLIST.md`'s steps and nothing else.

**The first pass (2026-10-02, v2.58.0 on a Windows 10 laptop, by the
checklist).** It stopped on three findings that no Linux test could have
produced, which is what the run is for. The self-check's `mail` row FAILed on
every default install (it read the façade token `make-secrets.sh` always
generates as "configured"), so `boot` was unreachable. No release zip could
be imported on Windows (Git for Windows' UnZip does not let `*` cross `/`, so
the vendor skip excluded 3 of 49,809 entries and the tree's symlinks failed
the unpack). And the suite could not finish (pytest-timeout on Windows ends
the whole session when one test passes the ceiling). What DID hold, on a real
host: an `app` interrupted at `mint-key` was refused at `boot` and resumed by
`./setup.sh` alone; a killed run left `started` rows, the next reclaimed the
stale lock, and a concurrent run was refused naming the pid; an update that
could not be acquired left the tree, `.env`, the ledger, the manifest and
every container's image ID untouched; and a deployment with an emptied ledger
and a narrow spine key was adopted in seven minutes with no container
recreated and the key widened in place, its value unchanged. The run also
measured what the ledger costs under MSYS — ~21 s before any command's first
line — and that the ledger recorded a stop wrongly (rows the phase never
reached were judged by their probes). v2.58.2 is those fixes; the reboot, the
demo and a clean update were not reached and are the repeat run's.

**The second and third passes (2026-10-03, release candidates of v2.58.2).**
The second ran every scenario and confirmed the first pass's fixes on the
box, and found what only a real reboot and a real update could: the logon
entry runs in a console, so the resume run prompted for the operator's name
and waited forever in a locked session whenever the name had been given in
the cockpit; `./setup.sh` refused to start the installed release while an
imported update waited, which kept the install down after an acquisition
that stopped; the API's start-up self-check ran before `boot` had started the
sandbox runner; and `llm` re-ran on every run with the speech engine off,
because the plan asks its probes before `.env` is exported. The fixes for the
Windows-only failures were first made blind and failed again on the box; the
round that worked validated each one there, in a scratch clone beside the
deployment. The third pass, on the tree that is tagged, was clean.

### What the acceptance run proved, and what it did not

Proven on a Windows 10 laptop (Git Bash, a podman machine), by the checklist
alone, on the tagged tree: a fresh install to every ledger row `done`; an
`app` interrupted at `mint-key`, refused at `boot` and resumed by `./setup.sh`
to the end; a run killed mid-phase (`started` rows, the stale lock reclaimed,
a concurrent run refused naming the pid); the demo approved in the cockpit,
the seven bundled skills imported, the self-check passing with the sandbox
runner; a reboot with the operator's name absent from `.env` — one logon
attempt, three listeners 268 s after SSH returned, `status` all green, one
logon entry; an update whose acquisition could not complete, from a real
release zip, leaving the tree, `.env`, the ledger, the manifest and every
container's image ID as they were, and `./setup.sh` then running the
installed release; a clean update (271 s, nothing recreated) through to all
rows `done` at the new version; `update.sh rollback`; and the adoption of a
deployment with an empty ledger and a narrow spine key (190 s, no container
recreated, the key widened on the real proxy with its value unchanged).

Measured there: the first output line of any command at about one second
(21 before the fork reduction); the suite at 44 minutes through the `test`
phase — it runs once per release, and after an update that is before `boot`.

NOT proven, and stated so nobody assumes it: a real Linux single-node host
(the `systemd --user` units are stub-tested and pass `systemd-analyze`; under
systemd `After=` orders the runner before the API but does not wait for it);
n8n on this profile (the credential gate and the workflow import ran against
stubs only); a corporate CA, a proxy or a mirror on this release (the
2026-09-24/25 runs covered those seams on earlier releases); the recreate
path of the image catch-up (no update in the run changed an image). Left
open from the run: after `update.sh rollback` the release rolled back from is
still imported on `upstream`, so the next run reports it as waiting and
`apply` would install it again — neither the rollback's output nor the
checklist says so, and its last line still reads "update applied"; the
Systems page shows n8n as Down when n8n is not deployed.

### D10 — A deployment carries no local patches; a defect travels back as a report

The operator's rule, 2026-10-01: an install configures through the
established mechanisms — `.env` and the environment — and never rewrites
any part of Central Command; anything else it needs is a finding, carried
back to a development session, fixed properly, and released. Today that
rule is prose in the skill ("never a script, a Dockerfile, `images.txt`"),
and the 2026-09-24 work-site session regenerated the npm lock, hand-edited
`images.txt` and commented out lock pins anyway — each a defect later
blamed on something else. The rule becomes four mechanisms:

1. **The tree is pristine, or the driver refuses.** A `check` row,
   `check/tree-pristine`, and the same test at the top of EVERY
   `./setup.sh` and `update.sh` run: the working tree and `HEAD` carry no
   difference from the installed release (`upstream` on an updated install,
   the import commit on a fresh one) outside gitignored paths. A difference
   is a FAIL that lists the paths and exits 1. There is no flag past it;
   `CC_SETUP_UNLEDGERED=1` (D3) does not cover it. The one tracked file an
   operator may differ on is none: `.env` is gitignored, and everything
   generated lives in the state dir (2026-09-23 D7). `update.sh` loses its
   three-way-merge allowance: `apply` fast-forwards `local` to `upstream`,
   and a tree that cannot fast-forward is the same FAIL. (The `local` branch
   stays as the name the updater moves.) `update.sh init`'s `git add -A` —
   its baseline snapshot of a freshly unzipped tree with no history — STAYS:
   that commit is what gives a fresh install a pristine starting point to
   fast-forward from, not a local patch to retire.
2. **The agent is held to `.env` by a hook, not a sentence.** The repo
   ships `.claude/settings.json` with two PreToolUse hooks under
   `.claude/hooks/`, so they apply to any Claude Code session opened in a
   checkout, the work site included: `guard-install-tree.sh` denies `Edit`,
   `Write` and `MultiEdit` whose path is a tracked file (anything but the
   repo-root `.env` and paths under the state dir), and denies a `Bash`
   command at command position that writes into the tree — `sed -i`,
   `tee`, `>`/`>>` redirections, `git checkout --`/`git restore`/`git
   stash`/`git apply`, `npm install` without `ci`, `uv lock`, `pip-compile`
   — with the same exit-2 contract and command-position matching as the
   operator's existing `guard-live-deploy.sh`. The hook names D10 and the
   report command (3) in its denial text, so the session's next move is the
   right one. A session that must edit the tree is a DEVELOPMENT session,
   which sets `CC_DEV_SESSION=1` in its environment; the hook allows
   everything then and says so once. Whether a project-level
   `.claude/settings.json` hook applies without the operator opting in is
   verified against the Claude Code docs in P1, not assumed; if it needs an
   opt-in, `CHECKLIST.md` carries the one-time step.
3. **`./setup.sh report` is how a defect travels.** One file,
   `<state>/report-<stamp>.txt`, built to be pasted into a development
   session: the ledger; every `failed` row with its `reason` and the
   `reads` keys' NAMES; the self-check lines; the whole log of the last
   run; `.env` key names with `(set)`/`(empty)`; tool versions; the image
   manifest; the tails of the three process logs. Never a secret value —
   the same redaction `diagnose` has, and `tests/test_single_report_redacts.py`
   feeds it an `.env` of marker values and greps the output. `diagnose`
   becomes an alias. The skill's instruction on a repository defect is one
   line: run `./setup.sh report`, hand the operator the path, end the turn.
4. **The skill says it in the same words.** "You may change `.env`. You may
   run `./setup.sh`, `./setup.sh status` and `./setup.sh report`. Everything
   else is a finding." The three verbs are the whole operator-side agent
   contract, and `tests/test_setup_phase_docs.py` pins that paragraph.

What this removes on purpose: an operator's ability to carry a local
patch across releases. The updater's three-way merge existed for that; the
operator decided (2026-10-01) that a deployment with a patch is a
deployment that has deviated, and the patch belongs in the repository or
nowhere.

### D11 — Precedents: the names of the wheels, and seven rules adopted from them

Asked on 2026-10-01 whether this record reinvents the wheel, two research
passes (sources fetched that day; the private instance memory keeps the
list) mapped every piece to an established pattern. The record now says
which wheel each piece is, because a design that hides its precedents makes
the next reader rederive them:

| piece | established pattern | rule adopted |
|---|---|---|
| `probe` + "done only if the probe holds" (D1) | Microsoft DSC v3's Test/Set contract ("verify desired state before enforcing"); Terraform's refresh before diff | the probe IS the refresh: a `done` row whose probe fails is drift and runs again, never a skip |
| the ledger (D2) | Terraform state; dpkg's package states, where `half-configured` is a state of its own and `dpkg --configure -a` is the resume | a `started` status, written BEFORE a phase runs, so an interrupted run reads "started, not finished" rather than "pending" |
| the resume driver (D2, D3) | a level-triggered reconciler over an ordered step list (Kubernetes controllers) | the name, in the driver's header |
| `questions.tsv` + `.env` (2026-09-23 D6) | debconf's question database and preseeding | nothing new now; debconf's priority dial is noted as the shape a future "only stop me where there is no safe default" would take |
| `check` (2026-09-23 D5) | Replicated's preflight; Sentry's minimum-requirements step | a PLAN, printed at the start of every run: which rows will run, which will skip and why (Terraform `plan`, Ansible `--diff`) |
| PASS/WARN/FAIL/USERACTION with a remedy (2026-08-25) | the "doctor" convention (brew, flutter, npm — recalled, not fetched) | unchanged; a FAIL line names the key or the command, which is already the rule |
| the self-check (D4) | goss, "serve the checks as a health endpoint"; Kubernetes readiness vs liveness | the self-check is READINESS-shaped: it gates use. It is never liveness-shaped: it restarts nothing, and the Kubernetes docs' warning about conflating the two is quoted in its docstring |
| `report` (D10) | Replicated troubleshoot.sh: collectors, redactors, analyzers, redaction in process with a default set | redaction happens in process as each section is collected, from a declared default list; the marker test stays as the guard nobody else has |
| the pristine tree (D10) | Sentry's commit check (HEAD equals upstream, opt-out); GitLab, Discourse, Coolify regenerate artifacts on every converge | nothing: ours is stricter than any found, and justified because our tree is code, not generated output |
| one 3,800-line driver | Sentry's installer: a thin orchestrator sourcing step files in order | the driver splits into `deploy/single/phases/<phase>.sh`, one file per manifest phase, when P4 lines the docs up with the manifest |
| one run at a time | Kamal's atomic lock directory for the duration of a deploy | a run lock in the state dir, taken by every mutating command and by `update.sh`; a lock held by a live run is a FAIL naming its pid and command. As built (P2), a STALE lock — its holder provably gone — is reclaimed by the next run with a WARN naming the dead pid and what it was running, rather than waiting on a release command as Kamal's does: the agent's contract is three verbs (D10.4) and the recovery is one command (D3), so the command that releases a stale lock has to be `./setup.sh` itself, and a logon-time run after a power cut must not sit behind a lock nothing holds |

Two pieces have no precedent in self-hosted installers and are kept on a
stated justification: the per-step resume ledger (the products examined
either reconverge because a full rerun is cheap, or fail hard; ours is not
cheap — image builds, paid model round trips, a ten-minute suite and human
gates) and the agent-conducted loop with hooks (uncharted, not under-built).
Adopting any of the tools stays rejected for the 2026-09-23 reason: every
real convergence runtime assumes an interpreter an air-gapped Windows box
with Git Bash does not guarantee.

## Phasing (each a release; P5 gates the tag of the whole)

1. **P1 — manifest, ledger, one command, pristine tree.** D1, D2, D3,
   D5's exit-code function and `phase_fetch` fix, D10's tree check, hook
   and `report` command (the hook and the check are small and are the
   operator's first ask). Acceptance: the ledger test above passes on
   Linux; `./setup.sh boot` on a tree with no `app` row refuses; a tree
   with one tracked file modified refuses every command and names the
   file; the hook denies an `Edit` of `deploy/single/images.txt` and
   allows one of `.env`.
2. **P2 — the self-check, and the ledger's precedents.** D4, the
   `verify/selfcheck` row, `/api/selfcheck`, the Systems page column, the
   spine-key scope fix; from D11: the `started` ledger status, the run lock,
   the plan printed at the start of every run, in-process report redaction
   from a declared default list, the readiness-only rule in the self-check's
   docstring. Acceptance: an `.env` with an empty `CC_LLM_API_KEY` cannot
   reach `boot`; a phase killed mid-run leaves its rows `started`; a second
   `./setup.sh` while one runs refuses and names the lock.
3. **P3 — supervision and skills.** D6, D7, the runner token, `update.sh`'s
   acquire-before-merge (rest of D5). Acceptance: on Linux, `stop` then
   `boot` leaves three listeners and a non-empty skills library.
4. **P4 — one definition.** D8: `CHECKLIST.md` (carrying the one-time
   workspace-trust dialog and, for a zip install, `./update.sh init` as the
   baseline step), the doc pins, the skill rewrite, the diagnose bundle, the
   `human` rows D1 names that P1 left out (the n8n credential, the CA on
   disk); from D11: the driver split into `deploy/single/phases/<phase>.sh`.
   Acceptance: `test_setup_phase_docs.py` passes with the READMEs no longer
   carrying a phase list of their own; every manifest phase has exactly one
   phase file.
5. **P5 — the laptop.** D9's acceptance run. Any Windows defect it finds is
   fixed and the run repeated before the tag.

## What this does and does not promise

Prevented, because the mechanism makes them impossible or loud: a step
skipped and nobody told (D1, D2); a phase run before its prerequisite (D2,
D3); an app-facing setting blank or wrong at `boot` (D4); a FAIL reported as
"your action" (D5); a merge ahead of a failed fetch (D5); a process the
install started but nothing restarts (D6); a checklist that exists only in
the agent's instructions (D8); a deployment that quietly differs from the
release it claims to be, and a session that "fixes" the tree on site (D10).

Not prevented: the next platform difference nobody has met. Twenty-two of
the recorded incidents are that class, and a data file cannot know what
MSYS will rewrite next. What changes is that such a failure now lands in a
named ledger row with its inputs recorded, and the recovery is always the
same two moves: change the `.env` key the line names, or report the row.

## Open items (do not block P1)

- Whether the k3s driver adopts `steps.tsv` as-is or needs its own file: it
  has a second answer file (`deploy/pi/.env`) and its own `is_placeholder`
  (`PENDING`). Decide after P2 proves the shape.
- `demo` is skipped once one decided proposal exists; with the ledger that
  rule can go (the row is `done`), but a re-run on an installed box must not
  enrol a second fixture — keep the event-log probe as the row's `probe`.
- `phase_test` once per version is ~10 minutes on Linux and 44 minutes on
  the Windows laptop (measured, P5); after an update it runs before `boot`
  on the next `./setup.sh`. Whether it should run at update time instead is
  the operator's decision and is open.
- With n8n enabled the credential gate asks for the Google Calendar
  credential too, because `make-secrets.sh` always generates the calendar
  façade token; making the calendar façade opt-in needs a flag and is open.
- The diagnose bundle's size with a whole run's log: cap at the last run and
  say so in the first line.
