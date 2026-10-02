# The install remembers what it did, proves it as the app, and has one definition

> **Status:** partial — P1 shipped in v2.55.0 (D1, D2, D3, D5's exit-code rule and
> `fetch` fix, D10); P2–P5 (D4, D6, D7, D8, D9, the rest of D5) are open. Decisions
> D1–D10 taken with the operator on 2026-10-01.
> **As-built:** P1 — `deploy/single/steps.tsv` (new), `deploy/single/ledger-lib.sh`
> (new), `deploy/single/setup.sh`, `deploy/single/update.sh`, `deploy/env-lib.sh`,
> `.claude/settings.json` (new), `.claude/hooks/guard-install-tree.sh` (new),
> `tests/test_single_steps_schema.py`, `tests/test_single_ledger_lib.py`,
> `tests/test_single_driver_ledger.py`, `tests/test_single_report_redacts.py`,
> `tests/test_install_tree_hook.py` (all new).
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

`status` is `done | failed | pending | gate`. `version` is `VERSION` at the
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
| mail | the configured provider lists one ref (Exchange when `exchange.configured()`, else the n8n façade when `CC_ENABLE_N8N=1`) |
| integrations | `scripts/atlassian_probe.py`'s checks, when Jira/Confluence are configured |
| cockpit | `http://127.0.0.1:{CC_COCKPIT_PORT}/` answers (CLI mode only) |
| links | every `CC_*_UI_URL` / `_DOCS_URL` the Systems page would show is either blank by flag or answers |

Every FAIL names the `.env` key or the phase to re-run. The completion and
embedding checks spend tokens; they are two requests, and they are the only
two in the whole install that prove what the agents will experience.
`verify.sh` stays as the deployment's own proof under the admin key; the two
are different questions and both are asked.

Known consequence, to be measured in P2: the minted spine key is scoped to
`cc-default`, `cc-tts`, `cc-stt` (`setup.sh` mint-key), and the graph writer
embeds through `CC_EMBED_ALIAS` — if it does so with the spine key, the
self-check will FAIL `embedding-as-app` on every fresh install. That is a
real finding, not a false alarm; the fix (scope the minted key to the
aliases the app uses, from `cc_required_aliases`) ships in P2 with the check.

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

### D7 — Importing the bundled skills is a step

`boot/skills-imported`, after the roster: for each `skills/*/SKILL.md`,
`POST /api/skills/import` when `GET /api/skills` does not already hold that
`skill_id`. Create-only, like `register-models.py`; a release that changes a
bundled skill re-imports it from `update.sh` under the same rule, because
the ledger row's fingerprint includes the folder's tree hash.

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

## Phasing (each a release; P5 gates the tag of the whole)

1. **P1 — manifest, ledger, one command, pristine tree.** D1, D2, D3,
   D5's exit-code function and `phase_fetch` fix, D10's tree check, hook
   and `report` command (the hook and the check are small and are the
   operator's first ask). Acceptance: the ledger test above passes on
   Linux; `./setup.sh boot` on a tree with no `app` row refuses; a tree
   with one tracked file modified refuses every command and names the
   file; the hook denies an `Edit` of `deploy/single/images.txt` and
   allows one of `.env`.
2. **P2 — the self-check.** D4, the `verify/selfcheck` row, `/api/selfcheck`,
   the Systems page column, the spine-key scope fix. Acceptance: an `.env`
   with an empty `CC_LLM_API_KEY` cannot reach `boot`.
3. **P3 — supervision and skills.** D6, D7, the runner token, `update.sh`'s
   acquire-before-merge (rest of D5). Acceptance: on Linux, `stop` then
   `boot` leaves three listeners and a non-empty skills library.
4. **P4 — one definition.** D8: `CHECKLIST.md`, the doc pins, the skill
   rewrite, the diagnose bundle. Acceptance: `test_setup_phase_docs.py`
   passes with the READMEs no longer carrying a phase list of their own.
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
- `phase_test` once per version is ~10 minutes on Linux and unmeasured on
  Windows (F40); the laptop run measures it.
- The diagnose bundle's size with a whole run's log: cap at the last run and
  say so in the first line.
