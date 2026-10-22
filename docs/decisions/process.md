# Repo, release and test policy — decisions

Governs secrets handling, branching, releases, vendored content, schema
hygiene, and how the test suite itself must be written. See
`docs/decisions/README.md` for the entry format and how to add one.

### DL-092 — Secrets live only in .env

- **Status:** active
- **Date:** undated
- **Rule:** [AGENTS.md](../../AGENTS.md) — "Secrets live only in `.env`"
- **Why:** Not recorded beyond the stated policy itself: nothing sensitive
  or personal belongs in tracked files — no real operator identity, no real
  mail/issue content, no deployment facts.
- **Enforced:** script: `scripts/prepush-scan.sh` (pre-push hook, confirmed present)
- **Source:** AGENTS.md Policy

### DL-093 — master is the only branch

- **Status:** active
- **Date:** undated
- **Rule:** [AGENTS.md](../../AGENTS.md) — "`master` is the only branch — no `main`"
- **Why:** Not recorded beyond the stated policy itself: a feature branch is
  a deliberate choice to raise with the operator, not a default.
- **Enforced:** discipline only
- **Source:** AGENTS.md Policy

### DL-094 — A release is three things: a CHANGELOG entry, a VERSION bump, and a tag

- **Status:** active
- **Date:** 2026-08-30
- **Rule:** [AGENTS.md](../../AGENTS.md) — "A release is three things: a CHANGELOG entry, a `VERSION` bump, a tag."
- **Why:** `tests/test_version_file.py` (module docstring) records the
  incident directly: v2.3.0 (2026-08-30) shipped a CHANGELOG entry and a tag
  but no VERSION bump, so every install reported 2.2.0 after a successful
  update and the cockpit re-offered 2.3.0 forever — four full
  stop/merge/restart cycles against an already-updated tree before anyone
  noticed. The updaters read `VERSION`, not git and not the tag.
- **Enforced:** test: `tests/test_version_file.py::test_version_file_matches_the_newest_changelog_release`
- **Source:** code comment `tests/test_version_file.py:1-8`

### DL-095 — Never change LITELLM_SALT_KEY or N8N_ENCRYPTION_KEY

- **Status:** active
- **Date:** undated
- **Rule:** [AGENTS.md](../../AGENTS.md) — "Never change `LITELLM_SALT_KEY` or `N8N_ENCRYPTION_KEY`"
- **Why:** They encrypt the stored LiteLLM virtual keys and the Gmail OAuth
  credential; a restore under a different key leaves the rows present but
  undecryptable.
- **Enforced:** discipline only
- **Source:** AGENTS.md Policy

### DL-096 — Never edit docs/vendor/ or hand-create anything in servers/

- **Status:** active
- **Date:** undated
- **Rule:** [AGENTS.md](../../AGENTS.md) — "Never edit `docs/vendor/`"
- **Why:** Not recorded beyond the stated policy itself: `docs/vendor/`
  refreshes only via `scripts/vendor_docs_fetch.sh`, and `servers/` arrives
  through an approved `mcp.sync_source` proposal (see DL-006). The one
  exception is `servers/echo-demo/`, hand-committed as the worked example
  (review ruling GATE-001, 2026-10-21); nothing else is hand-created there.
- **Enforced:** script: `scripts/vendor_docs_fetch.sh` for vendor docs; `servers/` is populated only via the `mcp.sync_source` executor path (code structure)
- **Source:** AGENTS.md Policy

### DL-097 — A fresh database must reproduce the roster

- **Status:** active
- **Date:** undated
- **Rule:** [AGENTS.md](../../AGENTS.md) — "**A fresh database must reproduce the roster.**"
- **Why:** Not recorded beyond the stated mechanism: `schema.sql`'s founding
  roster `UPDATE`s are guarded by `where … and role = ''`, so the seeding
  `INSERT` above them must NOT set `role` itself, or the guard becomes a
  no-op against an already-seeded value.
- **Enforced:** sql: `central_command/db/schema.sql:77,81,85,89,93,97,101,104` (`where id = '...' and role = ''` guards, confirmed present)
- **Source:** AGENTS.md bite marks

### DL-098 — schema.sql is auto-loaded on a fresh database only

- **Status:** active
- **Date:** undated
- **Rule:** [AGENTS.md](../../AGENTS.md) — "**`schema.sql` is applied on a FRESH database and by the updater at release — never at process start.**"
- **Why:** The test DB re-executes `schema.sql` on every run, so a new column
  passes the whole suite and then breaks a live deployment with an
  undefined-column error that reads like a code bug; additive columns must
  be applied to the running database (as `add column if not exists`) in the
  same change that adds them to the file.
- **Enforced:** code structure only — `tests/conftest.py` re-executes `central_command/db/schema.sql` every test run; no guard exists for the prod-apply gap this rule warns about
- **Source:** AGENTS.md bite marks

### DL-099 — Tests must pin demo_mode=True when they hit resolve_model()

- **Status:** active
- **Date:** undated
- **Rule:** [.claude/rules/tests.md](../../.claude/rules/tests.md) — "**Tests must pin `demo_mode=True` if they hit code that calls `resolve_model()` itself**"
- **Why:** With a real key in `.env` the "offline" suite would otherwise call
  the model for real and spend money.
- **Enforced:** discipline only — convention enforced by review, no meta-test
- **Source:** .claude/rules/tests.md

### DL-100 — Assert on rows your test created, never on global counts

- **Status:** active
- **Date:** undated
- **Rule:** [.claude/rules/tests.md](../../.claude/rules/tests.md) — "**Assert on rows your test created, never on global counts.**"
- **Why:** The dev DB may be shared; `tests/conftest.py` parks foreign
  `UNPROCESSED` rows so queue-order tests are deterministic, but count
  assertions still need scoping to rows the test itself created.
- **Enforced:** code structure only — enforced by `tests/conftest.py` itself, not a separate test
- **Source:** .claude/rules/tests.md

### DL-101 — One name everywhere: the historical identifiers are gone

- **Status:** active
- **Date:** 2026-08-29
- **Rule:** (recorded here) The system uses one consistent product/brand
  identifier throughout code, docs, and deployment artifacts; the earlier,
  historical identifiers were removed in a single breaking, by-hand release.
- **Why:** CHANGELOG `2026-08-29 — v2.0.0: one name everywhere — the
  historical identifiers are gone`: "This release is applied BY HAND, not by
  the cockpit updater" because of its breaking scope. MEMORY.md records the
  exhaustive removal (the "GV-rename lineage") as still ongoing for full
  identifier cleanup.
- **Enforced:** discipline only — a naming convention, not independently guarded
- **Source:** CHANGELOG v2.0.0

### DL-102 — The great scrub: a whole-repo audit is itself a periodic decision

- **Status:** active
- **Date:** 2026-08-31
- **Rule:** (recorded here) Periodically, the whole tree is read file-by-file
  in a dedicated audit release to catch drift and loose ends, rather than
  relying only on incremental review.
- **Why:** CHANGELOG `2026-08-31 — v2.17.0: the great scrub — every file
  read, every loose end pulled`: an exhaustive file-by-file review of the
  whole tree (~30 read-only audit tasks). This decision log is itself a
  direct descendant of that practice (the 2026-09 consistency audit that
  produced its seed data).
- **Enforced:** discipline only — a process practice, not a guarded invariant
- **Source:** CHANGELOG v2.17.0

### DL-103 — The runtime's use of credentialed integrations clients is read-only

- **Status:** active
- **Date:** 2026-09-20
- **Rule:** (recorded here) `runtime/` may call READ functions on the
  credentialed clients in `central_command/integrations/`, and nothing else.
  Every write method on a credentialed client is reachable only from
  `gateway/executor.py` (or other tier-2 code), after the approval gate.
  `integrations/sandbox_client.py` is exempt — the sandbox holds no
  credentials, so writing into it changes nothing in the world — and
  `integrations/neo4j_writer.py` is banned outright, being the operator's own
  ungated hand on the graph.
- **Why:** The import ban (DL-004) stops `runtime/` reaching `gateway/`, but
  says nothing about the clients the runtime legitimately imports for its
  reads — and every one of those modules exposes its reads and its writes side
  by side. `jira.get_issue` and `jira.transition_issue` are one import apart;
  so are `confluence.get_page`/`trash_page`, `graphiti.search_facts`/
  `add_episode`, `email_facade.get_message`/`report_spam`,
  `litellm.list_models`/`delete_model`. A `propose_*` tool "simplified" into
  the write it was drafting would pass the import ban (the module is already
  imported) and never reach the approval gate, leaving the operator clicking
  approve on something that had already happened.
  `docs/ARCHITECTURE.md` recorded the property as verified by call-site
  enumeration rather than by a guard test; this entry records the decision to
  freeze the enumeration as an allowlist instead.
- **Enforced:** test: `tests/test_runtime_integration_reads.py::test_runtime_reaches_only_read_functions_on_credentialed_clients`, with test: `tests/test_runtime_integration_reads.py::test_the_raw_litellm_transport_is_only_ever_asked_to_GET`, test: `tests/test_runtime_integration_reads.py::test_runtime_never_imports_a_write_only_integration` and test: `tests/test_runtime_integration_reads.py::test_a_write_function_imported_by_name_is_caught_too` closing the three bypasses
- **Source:** `docs/ARCHITECTURE.md`

### DL-104 — Graphiti's LLM client is upstream's; graphiti-llm is a plain alias and the built-in docstrings are the guidance

- **Status:** active
- **Date:** 2026-09-21
- **Rule:** [.claude/rules/graph.md](../../.claude/rules/graph.md) — "**The LLM client is graphiti-core's `OpenAIGenericClient`, built by us, and `graphiti-llm` is a PLAIN `openai/<model>` alias.**"
- **Why:** v2.38.4's fix for the unbounded attribute replaced MCP 1.1.0's
  built-in entity-type models with field-less models built from config.yaml's
  one-line descriptions — and the extraction prompt's entity-types block is
  built from docstrings alone, so the per-type guidance vanished. With
  thinking off, the local model answered `{"extracted_entities": []}` for
  short episodes (0/4 with the one-liners vs 4/4 with the built-in
  docstrings, logprobs 2026-09-21). The Responses-client pin was circular (it
  preserved an alias that only existed for the old client); the stock chat
  client with a plain alias was verified through the proxy. Since the
  library migration (2026-10-04) the client class is constructed by
  `integrations/graphiti_client.py` (DL-129) and the docstrings live in
  `integrations/graph_ontology.py` (DL-133); the rule — plain alias, built-in
  docstrings — is unchanged. The image patches and their pinning tests left
  with `deploy/pi/graphiti/` in v2.61.0, one release after the server did;
  the tests below carry the rule forward.
- **Enforced:** test: `tests/test_graph_ontology.py::test_each_docstring_is_byte_identical_to_the_upstream_builtin`, test: `tests/test_litellm_policy.py::test_graphiti_llm_is_a_plain_openai_alias`, test: `tests/test_graphiti_client.py::test_the_client_is_built_explicitly_and_issues_no_ddl` (the stock OpenAIGenericClient), test: `tests/test_single_models_declaration.py` (plain prefix), script: `deploy/single/discover-llm.sh` (structured probe)
- **Source:** CHANGELOG v2.39.0
- **Supersedes:** DL-055

### DL-105 — `setup.sh check` executes nothing, and it is the gate

- **Status:** active
- **Date:** 2026-09-23
- **Rule:** [.claude/rules/deploy-single.md](../../.claude/rules/deploy-single.md) — "**`check` EXECUTES nothing, and it is the GATE**"
- **Why:** The operator's air-gapped installs failed halfway through, on values
  nobody could check in advance (a tag the mirror lacked, a model id the
  endpoint did not serve). The asked-for experience was a pre-deployment check
  that prints what it checked and what failed, triage with the agent, re-run
  until green with nothing changed but `.env`, then install. That only works if
  the check is trustworthy about changing nothing — so dryness is a guard test,
  not an intention — and if it actually blocks (KOTS's hard preflight gate);
  a WARN-only run needs an explicit yes, and with no TTY it refuses to decide
  (rustup's fail-closed rule).
- **Enforced:** test: `tests/test_single_check_is_dry.py::test_nothing_check_can_reach_mutates_anything`, `::test_check_reaches_the_phases_it_composes`
- **Source:** CHANGELOG v2.44.0; docs/superpowers/specs/2026-09-23-airgap-check-configure-setup-design.md D5

### DL-106 — The LLM catalog is entered in the LiteLLM UI; `.env` may declare it; register-models.py stays create-only

- **Status:** active
- **Date:** 2026-09-24
- **Rule:** [.claude/rules/deploy-single.md](../../.claude/rules/deploy-single.md) — "**The LLM catalog is ENTERED IN THE LiteLLM UI, and `.env` may declare it
  instead**"
- **Why:** The catalog lives in LiteLLM's database, and the operator's practice
  on both profiles is to enter the provider in the proxy's own UI: LiteLLM
  expresses provider nuance — credentials, per-provider parameters, routing,
  fallbacks — that a flat answer file cannot, and one method across both
  profiles beats two that drift. **So the `llm` phase's exit-3 pause is a
  deliberate exception to "a full run does not stop", not a defect**, and a
  blank catalog is a PASS in `check` rather than a USERACTION the gate would
  refuse to pass. Declaring the upstream in `.env` stays as an OPTIONAL
  shortcut, and its real value is that the endpoint can then be probed from the
  HOST before a container exists — which on an air-gapped install is much
  earlier than the UI pause. Create-only survives because the row the
  operator filled in is the one they can see and change; `.env` may fill a
  skeleton, never overrule a decision. The key stays out of every log line and
  out of every comparison — LiteLLM masks it, so comparing it would report
  permanent drift.
- **Enforced:** test: `tests/test_register_models_upstream.py::test_a_row_the_operator_edited_is_never_touched`, `::test_declared_keys_create_real_rows`, `::test_no_keys_creates_placeholder_skeletons`
- **Source:** CHANGELOG v2.44.0; docs/superpowers/specs/2026-09-23-airgap-check-configure-setup-design.md D3

### DL-107 — Every `.env` writer is a row in steps.tsv, and a phase's probe is what "done" means

- **Status:** active
- **Date:** 2026-10-01
- **Rule:** [.claude/rules/deploy-single.md](../../.claude/rules/deploy-single.md) — "A new `.env` writer is a ROW, not a line of bash, and a phase's `probe` is what "done" MEANS"
- **Why:** `phase_app` has 14 steps; a mid-function `return 1` at its
  mint-key step silently skipped nine `.env` writes and the cockpit build,
  and nothing recorded that the phase had not finished — `./setup.sh boot`
  was accepted anyway, which is the 2026-10-01 work-site state the design
  record measures (empty `CC_LLM_API_KEY`, eight blank Systems links, a
  green `verify`).
- **Enforced:** test: `tests/test_single_steps_schema.py`; script: `deploy/single/steps.tsv`, `<state>/ledger.tsv`
- **Source:** CHANGELOG v2.55.0 / `docs/superpowers/specs/2026-10-01-setup-ledger-selfcheck-design.md`

### DL-108 — One exit-code rule, FAIL over USERACTION over WARN, everywhere

- **Status:** active
- **Date:** 2026-10-01
- **Rule:** [.claude/rules/deploy-single.md](../../.claude/rules/deploy-single.md) — "One exit-code rule, everywhere"
- **Why:** `phase_fetch` could never return 1 — its only FAIL path was
  always followed by a USERACTION, and the exit-code precedence ranked
  USERACTION above FAIL — so a hard failure in the one network phase
  reported as "stopped for your action" instead of "broken." The precedence
  also differed between the phase runner, the `machine` subcommand and
  `update.sh` before this release.
- **Enforced:** script: `deploy/env-lib.sh` (`cc_exit_code`)
- **Source:** CHANGELOG v2.55.0 / `docs/superpowers/specs/2026-10-01-setup-ledger-selfcheck-design.md`

### DL-109 — A deployment carries no local patches; a defect travels back as a report

- **Status:** active
- **Date:** 2026-10-01
- **Rule:** [.claude/rules/deploy-single.md](../../.claude/rules/deploy-single.md) — "The tree is pristine, or the driver refuses — there is no flag past it"
- **Why:** The operator's rule, 2026-10-01: an install configures through
  `.env` and the environment and never rewrites any part of Central
  Command; a prior work-site session regenerated the npm lock, hand-edited
  `images.txt` and commented out lock pins anyway, each later blamed on
  something else. `update.sh apply` is fast-forward only now — the
  three-way merge of local commits is retired — and `./setup.sh report`
  (`diagnose` is an alias) is the one way a finding travels back to a
  development session instead of being patched on the box.
- **Enforced:** test: `tests/test_single_report_redacts.py`; script: `deploy/single/update.sh`, `deploy/single/setup.sh` (`tree-pristine`)
- **Source:** CHANGELOG v2.55.0 / `docs/superpowers/specs/2026-10-01-setup-ledger-selfcheck-design.md`

### DL-110 — A PreToolUse hook, not a sentence, holds a session to `.env`

- **Status:** active
- **Date:** 2026-10-01
- **Rule:** [.claude/rules/deploy-single.md](../../.claude/rules/deploy-single.md) — "The hook holds the agent to `.env`, not the prompt"
- **Why:** The skill's "never a script, a Dockerfile, `images.txt`"
  instruction was prose a session could and did ignore. `.claude/settings.json`'s
  `PreToolUse` hooks make the same rule mechanical — active only on a
  deployment tree (one whose state dir already holds `ledger.tsv`) — and
  bypassed deliberately with `CC_DEV_SESSION=1` rather than accidentally.
- **Enforced:** test: `tests/test_install_tree_hook.py`; script: `.claude/hooks/guard-install-tree.sh`
- **Source:** CHANGELOG v2.55.0 / `docs/superpowers/specs/2026-10-01-setup-ledger-selfcheck-design.md`

### DL-111 — A step that printed FAIL is never recorded done, and a phase that began reads `started`

- **Status:** active
- **Date:** 2026-10-02
- **Rule:** [.claude/rules/deploy-single.md](../../.claude/rules/deploy-single.md) — "A step that printed FAIL is never `done`, and a phase that began reads `started`"
- **Why:** Found while building P2: the v2.55.0 ledger recorded a row `done`
  whenever its probe passed, so a `verify-live` that FAILED with the stack
  still up was written `done` and the next `./setup.sh` skipped the verify
  phase — the ledger reproducing the very class it exists to end ("a phase
  passed green but had not done its job"). And a run killed mid-phase left
  rows that read as never tried. dpkg's `half-configured` is the precedent
  for the second half (design record D11).
- **Enforced:** test: `tests/test_single_driver_ledger.py::test_a_step_that_failed_is_never_recorded_done_even_when_its_probe_holds`, `::test_a_phase_killed_mid_run_leaves_its_rows_started_and_the_next_run_resumes`
- **Source:** CHANGELOG v2.56.0 / `docs/superpowers/specs/2026-10-01-setup-ledger-selfcheck-design.md`

### DL-112 — One run at a time, and the resume command is what clears a stale lock

- **Status:** active
- **Date:** 2026-10-02
- **Rule:** [.claude/rules/deploy-single.md](../../.claude/rules/deploy-single.md) — "One run at a time, and `./setup.sh` is what clears a stale lock"
- **Why:** The ledger is read-modify-write, so two concurrent runs lose each
  other's rows; Kamal's lock directory is the precedent (design record D11).
  The record first said a stale lock "names the pid and the command that
  releases it". As built it is RECLAIMED by the next run instead, because a
  separate release command contradicts two earlier decisions — the agent's
  contract is three verbs (D10.4) and the recovery is one command (D3) — and
  because a logon-time `./setup.sh` after a power cut would otherwise sit
  behind a lock no process holds. A lock held by a live run is still a hard
  FAIL.
- **Enforced:** test: `tests/test_single_run_lock.py::test_a_live_holder_refuses_and_names_pid_command_and_start`, `::test_a_dead_holder_is_reclaimed_with_the_warning_text`, `::test_the_child_of_the_holder_proceeds_and_does_not_release`
- **Source:** CHANGELOG v2.56.0 / `docs/superpowers/specs/2026-10-01-setup-ledger-selfcheck-design.md`

### DL-113 — The self-check proves the install as the app; it is readiness, never liveness, and its result is cached

- **Status:** active
- **Date:** 2026-10-02
- **Rule:** [.claude/rules/deploy-single.md](../../.claude/rules/deploy-single.md) — "`verify/selfcheck` is the application proving itself, and it is READINESS, never liveness"
- **Why:** `verify.sh` proves the deployment under the admin key and never
  read the spine key, the default model, the app's base URL or database URL;
  the spine key went missing three times by three mechanisms behind a green
  verify. The self-check asks with the app's own settings and credential and
  `boot` requires it. It gates use and restarts nothing — the Kubernetes
  documentation's warning about conflating readiness with liveness is quoted
  in the module. The operator decided on 2026-10-02 that the API serves a
  CACHED result (a run at start and on request): two checks spend a model
  request each and queue behind running sessions on a single-slot backend.
- **Enforced:** test: `tests/test_single_selfcheck_row.py::test_an_empty_spine_key_cannot_reach_boot`, `::test_boot_and_demo_require_the_selfcheck_row`; test: `tests/test_selfcheck.py`; test: `tests/test_selfcheck_api.py`
- **Source:** CHANGELOG v2.56.0 / `docs/superpowers/specs/2026-10-01-setup-ledger-selfcheck-design.md`

### DL-114 — A report redacts as it collects, from a declared list, and the scan stays as the guard

- **Status:** active
- **Date:** 2026-10-02
- **Rule:** [.claude/rules/deploy-single.md](../../.claude/rules/deploy-single.md) — "The report redacts as it collects, from `redact.tsv`, and the scan stays"
- **Why:** v2.55.0's report scanned its finished output and REFUSED to write
  if a credential value appeared — safe, but one key echoed into a container
  log cost the operator the whole report, exactly when it was needed.
  Replicated's troubleshoot redactors are the precedent (design record D11):
  redact in process from a declared default list. The scan stays because it
  is the one check that does not trust the redactor.
- **Enforced:** test: `tests/test_single_report_redacts.py`; script: `deploy/single/redact.tsv`
- **Source:** CHANGELOG v2.56.0 / `docs/superpowers/specs/2026-10-01-setup-ledger-selfcheck-design.md`

### DL-115 — `boot` supervises three host processes, and a free port is the only proof of `stop`

- **Status:** active
- **Date:** 2026-10-02
- **Rule:** [.claude/rules/deploy-single.md](../../.claude/rules/deploy-single.md) — "`boot` starts three host processes, `stop` stops three, and the PORT is the proof"
- **Why:** The sandbox runner was a process the operator started by hand and
  nothing restarted; on Linux nothing brought the API or the cockpit back
  after a reboot either. And `stop` reported green twice (2026-09-18,
  2026-09-25) with the servers still listening — traced while building this
  to the detached start itself: `( cd X && nohup cmd & echo $! )` records a
  wrapper shell's pid, so `stop` signalled the shell. One supervisor per
  platform (systemd user units on Linux), the server's own pid, and a port
  with no listener as the proof.
- **Enforced:** test: `tests/test_single_boot_supervision.py::test_boot_writes_three_units_and_starts_them_through_systemd_then_stop_and_boot_again`, `::test_stop_fails_when_a_port_still_answers`, `::test_stop_fails_loudly_when_the_answer_file_cannot_be_loaded`, `::test_lingering_off_is_a_fail_naming_loginctl_enable_linger`
- **Source:** CHANGELOG v2.57.0 / `docs/superpowers/specs/2026-10-01-setup-ledger-selfcheck-design.md`

### DL-116 — The Windows logon entry runs the resume command, in a retry loop

- **Status:** active
- **Date:** 2026-10-02
- **Rule:** [.claude/rules/deploy-single.md](../../.claude/rules/deploy-single.md) — "The Windows logon entry runs `./setup.sh`, in a retry loop — never `boot` alone"
- **Why:** The logon task re-ran `boot` alone, so an install that never
  finished came back from a reboot half-built and silent. The record (D6)
  makes it the resume command. Two things the record did not spell out were
  decided while building: with no terminal a WARN-only `check` stops the run,
  so the wrapper passes `--accept-warnings`; and at logon the podman machine
  may not be up yet, so a failed attempt is retried (ten times, a minute
  apart) rather than left as the day's result.
- **Enforced:** test: `tests/test_single_boot_supervision.py::test_the_retry_script_runs_the_resume_command_until_it_can_stop`, `::test_the_windows_wrapper_is_tiny_and_hands_one_path_to_bash`
- **Source:** CHANGELOG v2.57.0 / `docs/superpowers/specs/2026-10-01-setup-ledger-selfcheck-design.md`

### DL-117 — The updater acquires before it merges, from a staged copy of the new release

- **Status:** active
- **Date:** 2026-10-02
- **Rule:** [.claude/rules/deploy-single.md](../../.claude/rules/deploy-single.md) — "The updater ACQUIRES before it merges, from a staged copy of the new release"
- **Why:** `update.sh apply` merged the new code and THEN ran `fetch`, under a
  comment that said "acquire BEFORE mutating": a mirror lacking one image
  left a merged tree over the old venv and containers, reported as exit 3,
  and a fetch or catalog pause `return 0`ed past `app` and `verify`. The new
  release's own fetch now runs from a sparse worktree before anything moves.
  Found alongside: the cockpit runner rolled back on ANY exit 1, including a
  stop before the merge, which reset the tree to an earlier update's tag.
- **Enforced:** test: `tests/test_single_update_acquire.py::test_a_mirror_missing_one_tag_stops_before_the_merge_with_nothing_changed`, `::test_a_pause_after_the_merge_stops_there_and_app_and_verify_do_not_run`, `::test_update_run_does_not_roll_back_a_stop_before_the_merge`
- **Source:** CHANGELOG v2.57.0 / `docs/superpowers/specs/2026-10-01-setup-ledger-selfcheck-design.md`

### DL-118 — A deployment that predates the ledger is adopted by `./setup.sh`, never failed into a rollback

- **Status:** active
- **Date:** 2026-10-02
- **Rule:** [.claude/rules/deploy-single.md](../../.claude/rules/deploy-single.md) — "A deployment that predates the ledger is ADOPTED by `./setup.sh`, and an update never fails it into a rollback"
- **Why:** Every deployment in the field was installed before v2.55.0 and has
  an empty ledger. The first ledgered phase an update calls would be refused,
  and the cockpit's runner answers a refusal with a rollback to a tree that
  can never write a ledger — such an install could never update. Adoption is
  the full run: every phase is idempotent, so `./setup.sh` walks the running
  deployment and records it.
- **Enforced:** test: `tests/test_single_update_acquire.py::test_a_pre_ledger_install_is_asked_to_run_setup_once`, `::test_update_run_does_not_roll_back_the_adoption_pause`; test: `tests/test_single_driver_ledger.py::test_an_older_updater_on_a_pre_ledger_install_gets_a_pause_not_a_failure`
- **Source:** CHANGELOG v2.57.0 / `docs/superpowers/specs/2026-10-01-setup-ledger-selfcheck-design.md`

### DL-119 — Importing the bundled skills is a create-only step

- **Status:** active
- **Date:** 2026-10-02
- **Rule:** [.claude/rules/deploy-single.md](../../.claude/rules/deploy-single.md) — "Importing the bundled skills is a step, and it is CREATE-ONLY"
- **Why:** The seven agent skills ship in `skills/` and nothing imported
  them: a fresh install's Skills page was empty by design and the operator
  was expected to know to import seven folders by hand (2026-10-01 work-site
  report). Create-only, like `register-models.py`: the library is the
  operator's and the coach's to edit, so a bundled copy never overwrites a
  skill that exists, and a retired one is never resurrected.
- **Enforced:** test: `tests/test_single_boot_supervision.py::test_skills_are_create_only_and_a_new_bundled_folder_is_imported`, `::test_a_tree_input_moves_the_fingerprint_only_when_the_tree_moves`
- **Source:** CHANGELOG v2.57.0 / `docs/superpowers/specs/2026-10-01-setup-ledger-selfcheck-design.md`

### DL-120 — An update deploys what it acquired: images carry their inputs' hash, and a container runs the image its ref resolves to

- **Status:** active
- **Date:** 2026-10-02
- **Rule:** [.claude/rules/deploy-single.md](../../.claude/rules/deploy-single.md) — "A container must be running the image its ref resolves to NOW"
- **Why:** Found while building acquire-before-merge: the local image tags
  are fixed and `fetch` skipped a build whenever the tag existed, so a
  release that changed a Dockerfile never rebuilt; and the updater never ran
  `stack`, while podman-compose 1.6.0 recreates a container only on a config
  hash that holds the image ref, not its ID — so even a rebuilt image would
  not have run. A release was "applied" and verified over the old
  containers. The same pass found the resolver turning a once-failed or
  switched-off image into a permanent "operator pin".
- **Enforced:** test: `tests/test_single_local_image_label.py`; test: `tests/test_single_stack_catch_up.py`; test: `tests/test_single_resolve_carry_forward.py`
- **Source:** CHANGELOG v2.57.0 / `docs/superpowers/specs/2026-10-01-setup-ledger-selfcheck-design.md`

### DL-121 — The operator's procedure is generated from the manifest, and documents link to it

- **Status:** active
- **Date:** 2026-10-02
- **Rule:** [.claude/rules/deploy-single.md](../../.claude/rules/deploy-single.md) — "The operator's procedure is GENERATED, and documents link to it"
- **Why:** The process was defined in code once and described in prose six
  times, one of them wrongly; no operator-facing document was a numbered
  checklist, and fourteen required steps were commands in no sequence at all
  (2026-10-01 investigation). The prose copies drifted again during this very
  record's build — the READMEs still said `fetch` ends in exit 3 and that the
  operator starts the sandbox runner by hand. One data file, one render, and
  a test that fails when they differ.
- **Enforced:** test: `tests/test_single_checklist.py`; test: `tests/test_setup_phase_docs.py`; script: `scripts/render_checklist.py`
- **Source:** CHANGELOG v2.58.0 / `docs/superpowers/specs/2026-10-01-setup-ledger-selfcheck-design.md`

### DL-122 — One file per manifest phase, and one helper that defines "the installer's source"

- **Status:** active
- **Date:** 2026-10-02
- **Rule:** [.claude/rules/deploy-single.md](../../.claude/rules/deploy-single.md) — "One file per phase, and the tests read the installer through ONE helper"
- **Why:** The driver had grown to 5,900 lines in one file. Sentry's
  self-hosted installer is the precedent (design record D11): a thin
  orchestrator sourcing step files in order. The risk in such a move is not
  the code but the guards — a dozen tests read `setup.sh`'s SOURCE, and one
  that keeps globbing the old file passes while checking nothing — so the
  split shipped with one shared definition of the source and a planted
  violation per guard.
- **Enforced:** test: `tests/test_single_phase_files.py`; script: `tests/installer_source.py`
- **Source:** CHANGELOG v2.58.0 / `docs/superpowers/specs/2026-10-01-setup-ledger-selfcheck-design.md`

### DL-123 — No operator-facing line names a phase to run

- **Status:** active
- **Date:** 2026-10-02
- **Rule:** [.claude/rules/deploy-single.md](../../.claude/rules/deploy-single.md) — "No operator-facing line names a phase to run"
- **Why:** The skill pins the agent to three verbs and the record promises
  one recovery command, but some twenty FAIL and USERACTION lines still said
  `run: ./setup.sh app` or `./setup.sh diagnose`. An agent follows the line
  it is given: the messages were the last place the old phase-by-phase
  procedure survived.
- **Enforced:** test: `tests/test_single_no_phase_hints.py`
- **Source:** CHANGELOG v2.58.0 / `docs/superpowers/specs/2026-10-01-setup-ledger-selfcheck-design.md`

### DL-124 — The self-check's mail row asks what the app does, not what the installer generated

- **Status:** active
- **Date:** 2026-10-02
- **Rule:** [.claude/rules/deploy-single.md](../../.claude/rules/deploy-single.md) — "The mail self-check applies when something in the app READS mail — never on the n8n flag or the façade token"
- **Why:** The first laptop acceptance run stopped here: with n8n off — the
  default — `verify/selfcheck` FAILed `mail` on every install, because the
  check read a set façade token as "configured" and `make-secrets.sh` always
  generates it; `boot` requires the row, so nothing after `verify` was
  reachable. The n8n flag could not be the signal either: the k3s profile
  leaves it at 0 while its façade is in use. What the app does with mail
  (the feed, the heartbeat poll, Exchange) is true on both profiles.
- **Enforced:** test: `tests/test_selfcheck.py`
- **Source:** CHANGELOG v2.58.2 / `docs/superpowers/specs/2026-10-01-setup-ledger-selfcheck-design.md`

### DL-125 — A row the phase never reached is pending, a pause is a gate, and every reporter line names a row

- **Status:** active
- **Date:** 2026-10-02
- **Rule:** [.claude/rules/deploy-single.md](../../.claude/rules/deploy-single.md) — "A row the phase never reached is `pending`, and a pause is `gate`"
- **Why:** The laptop run's ledger said two things at once: after `FAIL
  mint-key` some later rows read `done` and some `failed` with no reason, and
  after the llm pause the gate row read `done` and the status `gate` appeared
  nowhere — the probes were judging rows the phase had never reached, and the
  pause was printed under a name that was no row. The operator reviews an
  install from the ledger alone (D9), so the ledger has to say what happened.
- **Enforced:** test: `tests/test_single_driver_ledger.py::test_a_failed_mint_key_leaves_every_later_app_row_pending`, `::test_the_llm_catalog_pause_is_a_gate_and_the_rows_after_it_are_pending`; test: `tests/test_single_reporter_rows.py`
- **Source:** CHANGELOG v2.58.2 / `docs/superpowers/specs/2026-10-01-setup-ledger-selfcheck-design.md`

### DL-126 — The import unpacks with the install's own Python, never `unzip`

- **Status:** active
- **Date:** 2026-10-02
- **Rule:** [.claude/rules/deploy-single.md](../../.claude/rules/deploy-single.md) — "The import unpacks with Python, never `unzip`"
- **Why:** Git for Windows' UnZip 6.00 does not let `*` cross `/`: the
  `docs/vendor/*` skip excluded 3 of 49,809 entries, the vendor tree was
  unpacked anyway, and its four symlink members failed the import of every
  release zip on Windows, GitHub's own included. No update could be applied
  on the platform the work site runs. A tool whose wildcard semantics differ
  by build is not one a deterministic import can stand on.
- **Enforced:** test: `tests/test_single_release_zip.py`; test: `tests/test_update_runner.py::test_the_skip_covers_a_nested_vendor_tree_and_its_symlinks_with_no_unzip`
- **Source:** CHANGELOG v2.58.2 / `docs/superpowers/specs/2026-10-01-setup-ledger-selfcheck-design.md`

### DL-127 — The driver's hot paths start no program

- **Status:** active
- **Date:** 2026-10-02
- **Rule:** [.claude/rules/deploy-single.md](../../.claude/rules/deploy-single.md) — "The driver's hot paths start no program"
- **Why:** Measured on the laptop: ~21 s before any `./setup.sh` command
  printed a line, ~60 s for the plan, 64 minutes for a suite that takes ten
  on Linux. The cause was process creation — a thousand forks of `cut`,
  `sha256sum` and `date` per invocation, each 10–50 ms under MSYS. The logon
  entry runs the resume command at every boot, so that cost was paid before
  the API came back. 424 → 20 execs for `stop`, 571 → 26 for the plan, with
  every fingerprint value unchanged.
- **Enforced:** test: `tests/test_single_driver_forks.py`
- **Source:** CHANGELOG v2.58.2 / `docs/superpowers/specs/2026-10-01-setup-ledger-selfcheck-design.md`

### DL-128 — A test that drives the real installer has its own ceiling and cannot end the session

- **Status:** active
- **Date:** 2026-10-02
- **Rule:** [.claude/rules/tests.md](../../.claude/rules/tests.md) — "A test that runs the real installer is marked `@drives_installer` and calls `run_driver`"
- **Why:** On Windows pytest-timeout has only the thread method, which ends
  the whole session with no summary when ONE test passes the ceiling. The
  `test` phase is the on-site gate, and on the laptop it died at 79 % on a
  `setup.sh`-driving test — a gate that cannot finish is not a gate.
- **Enforced:** script: `tests/installer_source.py`; script: `tests/conftest.py`
- **Source:** CHANGELOG v2.58.2 / `docs/superpowers/specs/2026-10-01-setup-ledger-selfcheck-design.md`

### DL-129 — graphiti-core runs in the application process; its client is built explicitly and imported lazily

- **Status:** active
- **Date:** 2026-10-04
- **Rule:** [AGENTS.md](../../AGENTS.md) — "**graphiti-core is imported lazily, and only by the two `integrations/` modules that need it: `graphiti_client.py` and the read module `graphiti.py`.**"
- **Why:** The stock Graphiti MCP server acknowledged an episode before it was
  extracted and logged-and-dropped failures after the acknowledgement, offered
  no curation, and shipped as a locally built image per architecture; the
  library's `add_episode` is awaitable, raises, and returns what it wrote.
  Three constraints follow and are why the client is one module: a client left
  as `None` makes `Graphiti.__init__` quietly build an OpenAI client that wants
  an OpenAI key, so every client is passed in and a missing setting fails by
  name; importing the package reads `SEMAPHORE_LIMIT` and arms telemetry, so
  the environment is set before the first import and nothing loaded at
  application start may import it; and no `OPENAI_*` variable is ever exported
  into the process, so base URL and key are constructor arguments.
- **Enforced:** test: `tests/test_graphiti_client.py::test_missing_configuration_is_refused_by_name`, test: `tests/test_graphiti_client.py::test_the_client_is_built_explicitly_and_issues_no_ddl`, test: `tests/test_governance.py::test_importing_the_app_does_not_import_graphiti_core`, and test: `tests/test_graphiti_client.py::test_the_pyproject_pin_is_the_patches_version` (the exact pin)
- **Source:** `docs/superpowers/specs/2026-10-04-graphiti-library-migration-design.md` (D1, D2); code comment `central_command/integrations/graphiti_client.py`

### DL-130 — Graph ingestion is a durable queue in Postgres, strictly ordered per group, with no global cap

- **Status:** active
- **Date:** 2026-10-04
- **Rule:** [.claude/rules/graph.md](../../.claude/rules/graph.md) — "An episode that is not in the graph yet is a `graph_ingest_job` row, not an absence to infer"
- **Why:** The MCP server's queue lived in memory, exposed no depth and no
  completion signal, and lost a restart's worth of episodes; the verification
  sweep's absence deadline, queue-depth estimate and one-shot re-submission
  existed only to infer from outside what the server never reported. The
  `graph_ingest_job` table puts "afterwards" on the record: jobs in one group
  run strictly in order (upstream requires sequential adds per group, and the
  database refuses a second RUNNING job in a group), different groups run side
  by side, and a crash is recovered by the `proposal=<id>` marker on the
  Episodic node (an episode is written whole in one transaction, so the marker
  proves it landed). There is deliberately NO global concurrency cap and no
  setting for one: an extraction already issues several model calls at once,
  the model backend queues what it cannot serve, and a limit belongs to the
  model it protects, not to this queue. A transient failure re-queues with
  backoff and a permanent one fails the job and parks its Verify row — nothing
  is acknowledged and then dropped, and nothing is re-submitted.
- **Enforced:** test: `tests/test_graph_ingest.py::test_one_group_runs_in_order_and_groups_run_side_by_side`, test: `tests/test_graph_ingest.py::test_the_database_refuses_two_running_jobs_in_one_group`, test: `tests/test_graph_ingest.py::test_a_transient_failure_requeues_with_backoff`, test: `tests/test_graph_ingest.py::test_a_permanent_failure_fails_the_job_and_parks_its_row` and test: `tests/test_graph_ingest.py::test_recovery_marks_a_landed_orphan_done` and test: `tests/test_graph_ingest_no_cap.py::test_the_only_ingest_setting_is_the_on_off_switch` (no setting for a cap; the side-by-side test above proves one job per group is claimed in one tick)
- **Source:** `docs/superpowers/specs/2026-10-04-graphiti-library-migration-design.md` (D5); code comment `central_command/integrations/graphiti_ingest.py`

### DL-131 — Graphiti.search() is never called; every search copies its recipe

- **Status:** active
- **Date:** 2026-10-04
- **Rule:** [AGENTS.md](../../AGENTS.md) — "**Never call `Graphiti.search()`**"
- **Why:** `Graphiti.search()` assigns `limit` on a module-level recipe object
  that `add_episode` also reads for its dedupe and invalidation candidates, so
  one search call changes extraction for the life of the process. Every search
  therefore goes through `search_()` with a deep copy of the recipe and the
  limit set on the copy, which also fixes the retired server's own bug (it
  sliced a ten-result recipe, so asking for 25 facts returned ten).
- **Enforced:** test: `tests/test_graphiti_search_never_called.py::test_no_graphiti_search_call_anywhere_in_the_package` (a source walk: no `.search(` on the Graphiti client in `central_command/`) and test: `tests/test_graphiti_client.py::test_search_uses_a_deep_copy_with_the_requested_limit` (the module-level recipes are untouched and the requested limit is honoured)
- **Source:** `docs/superpowers/specs/2026-10-04-graphiti-library-migration-design.md` (D2, D6); code comment `central_command/integrations/graphiti.py`

### DL-132 — The two graphiti-core fixes are patch files applied to the installed package, and the worker refuses to extract without them

- **Status:** active
- **Date:** 2026-10-04
- **Rule:** [.claude/rules/graph.md](../../.claude/rules/graph.md) — "We carry upstream #1729 (invalidation scope) and #1666 (reasoning-first dedupe) as patch files in `deploy/graphiti-patches/`"
- **Why:** The invalidation scope (#1729) and the reasoning-first dedupe
  schema (#1666) are upstream bugs we hit and submitted, both still open. A
  fork was rejected because an air-gapped site cannot be assumed to reach it.
  The applier is stdlib-only (`patch` is not on every substrate), exact-context
  with no fuzz (the version is pinned, so a mismatch is a finding),
  idempotent, all-or-nothing, and writes through a temp file and `os.replace`
  because `uv` hardlinks installed files from its cache and an in-place write
  would patch the cache and every environment sharing it. A hand step can be
  forgotten, so the running system checks too: the ingest worker starts no
  extraction while the patch sentinels are absent from the installed package
  (jobs stay QUEUED; reads are unaffected) and the self-check's `graph-patches`
  row says why.
- **Enforced:** test: `tests/test_graphiti_patches.py::test_each_real_patch_applies_cleanly_or_is_already_applied_to_a_copy`, test: `tests/test_graphiti_patches.py::test_a_mismatch_fails_loudly_and_writes_nothing_anywhere`, test: `tests/test_graphiti_patches.py::test_write_is_atomic_hardlink_safe_and_keeps_the_mode`, test: `tests/test_graph_ingest.py::test_without_the_carried_patches_nothing_is_extracted`, and test: `tests/test_graphiti_server_boundary.py::test_the_patch_step_follows_every_install_and_tolerates_an_old_tree`; script: `scripts/apply_graphiti_patches.py`
- **Source:** `docs/superpowers/specs/2026-10-04-graphiti-library-migration-design.md` (D7); code comment `central_command/integrations/graphiti_patches.py`
- **Supersedes:** (the mechanism of DL-056 only — DL-056 stays active; its patches no longer ride in an image)

### DL-133 — The ontology is ten field-less models whose docstrings are pinned by hash

- **Status:** active
- **Date:** 2026-10-04
- **Rule:** [.claude/rules/graph.md](../../.claude/rules/graph.md) — "**The docstrings are the guidance**"
- **Why:** A required string attribute on an entity type is rewritten longer on
  every episode (the hub Person reached 4378 characters and generations of
  41-52k into the output cap), and the extraction prompt's type block is built
  from `__doc__` alone — replacing the docstrings with one-line descriptions
  made the local model extract nothing from short episodes (0/4 vs 4/4,
  2026-09-21). `integrations/graph_ontology.py` therefore defines the ten
  models with no fields and the stock built-ins' docstrings verbatim, assigned
  as explicit strings (so the bytes do not depend on the interpreter's
  docstring handling) and pinned by SHA-256. The same alias serves a second
  workload, so a change to one is a change to what the model is told:
  measure against both.
- **Enforced:** test: `tests/test_graph_ontology.py::test_each_docstring_is_byte_identical_to_the_upstream_builtin`, test: `tests/test_graph_ontology.py::test_every_model_is_field_less` and test: `tests/test_graph_ontology.py::test_the_writer_allowlist_names_the_same_types`
- **Source:** `docs/superpowers/specs/2026-10-04-graphiti-library-migration-design.md` (D4); code comment `central_command/integrations/graph_ontology.py`

### DL-134 — runtime/ may import the graph's reads and none of its write modules

- **Status:** active
- **Date:** 2026-10-04
- **Rule:** [AGENTS.md](../../AGENTS.md) — "`runtime/` may import `integrations/graphiti` (reads) and nothing that writes"
- **Why:** With graphiti-core in the application process, agents' graph reads
  run over bolt, so the older statement "the runtime tier holds no bolt" is
  retired and replaced by the rule that is actually enforced: the runtime tier
  holds no WRITE path. `graphiti_client` builds an object that carries
  `add_episode` and `remove_episode`, `graphiti_ingest` is the queue's worker,
  and `neo4j_writer` is the operator's ungated curation hand — none may be
  imported from `runtime/`. The read module reaches its client only through a
  function-local import and calls no write method, which keeps the ban
  checkable: importing it loads no write path.
- **Enforced:** test: `tests/test_governance.py::test_the_graph_write_modules_are_banned_in_every_import_spelling`, test: `tests/test_governance.py::test_runtime_never_imports_the_gateway_tier`, test: `tests/test_governance.py::test_the_graph_read_module_reaches_its_client_only_lazily` and test: `tests/test_runtime_integration_reads.py::test_runtime_never_imports_a_write_only_integration`
- **Source:** `docs/superpowers/specs/2026-10-04-graphiti-library-migration-design.md` (D6); code comment `central_command/integrations/neo4j_reader.py`
- **Supersedes:** (refines DL-103's list of banned modules; DL-103 stays active)

### DL-135 — Deleting an episode is upstream's pinned rule, in one transaction of ours, through the group's queue

- **Status:** active
- **Date:** 2026-10-05
- **Rule:** [.claude/rules/graph.md](../../.claude/rules/graph.md) — "**Deleting an episode is upstream's rule, in ONE transaction of ours, through the group's queue, against the set the operator approved**"
- **Why:** `Graphiti.remove_episode` runs three separate auto-commit deletes
  (facts, then entities in `CALL … IN TRANSACTIONS` batches, then the
  episode), so a crash between them leaves half a deletion whose re-run reads
  a different set; for a destructive operation atomicity is worth more than
  reusing upstream's code, and the hash pin on the installed source keeps the
  hand-written rule honest across upgrades. Running it as a `remove_episode`
  job on the episode's group queue means it can never race an extraction in
  that group, and a re-run of a committed deletion finishes DONE and repeats
  only the idempotent provenance cleanup (which upstream omits).
- **Enforced:** test: `tests/test_graph_delete_episode.py::test_our_deletion_rule_is_pinned_to_the_installed_remove_episode`, test: `tests/test_graph_delete_episode.py::test_the_deletion_removes_the_set_and_cleans_provenance`, test: `tests/test_graph_delete_episode.py::test_a_deletion_waits_behind_an_extraction_in_its_group` and test: `tests/test_graph_delete_episode.py::test_recovery_reruns_a_running_deletion_without_a_marker_lookup`
- **Source:** `docs/superpowers/specs/2026-10-04-graphiti-library-migration-design.md` (D9); CHANGELOG v2.61.0

### DL-136 — A deletion's preview is captured at propose time and compared, never executed from

- **Status:** active
- **Date:** 2026-10-05
- **Rule:** (recorded here) A `graph.delete_episode` proposal carries the
  preview of what it deletes, captured by `propose_delete_episode`; the
  Executor recomputes the preview and refuses (ExecutorError) when the set of
  episode, fact and entity uuids differs, refuses a proposal that carries no
  preview, and the deleting transaction compares a third time. The cockpit's
  direct path confirms the preview's digest and gets a 409 when it is stale.
- **Why:** The operator approves the LIST of what goes, not the agent's
  description of it, and the world can change between propose and approve —
  a world-state check, so it is Executor-only (the shape stays in
  `contract.ARG_SPECS`, DL-010). Executing from the embedded copy would let a
  graph that moved delete something nobody saw; deleting a smaller set
  silently would mis-report what the approval did.
- **Enforced:** test: `tests/test_graph_delete_episode.py::test_the_executor_refuses_when_the_set_changed`, test: `tests/test_graph_delete_episode.py::test_the_executor_refuses_a_proposal_that_carries_no_preview`, test: `tests/test_graph_delete_episode.py::test_a_changed_set_is_refused_and_nothing_is_deleted` and test: `tests/test_graph_delete_episode.py::test_a_stale_digest_is_refused_before_anything_is_queued`
- **Source:** `docs/superpowers/specs/2026-10-04-graphiti-library-migration-design.md` (D9); CHANGELOG v2.61.0

### DL-137 — Hand-made writes have the installed bulk path's shape, guarded against the package

- **Status:** active
- **Date:** 2026-10-05
- **Rule:** [.claude/rules/graph.md](../../.claude/rules/graph.md) — "**The bulk path's shape is canonical; a model's `save()` writes a different one.**"
- **Why:** Every extracted object in the graph has the bulk shape, and our
  curation Cypher has no upstream equivalent to lean on for update, merge or
  move; deriving upstream's property sets from the installed package at test
  time makes an upgrade that adds, renames or drops a property a failing test
  instead of a second shape in the graph. The same release stopped writing
  the `embedding_model`/`embedding_dimensions` stamp, which upstream's readers
  surfaced as an attribute in agents' fact results.
- **Enforced:** test: `tests/test_graph_write_shape.py::test_hand_made_writes_have_the_installed_bulk_shape`, test: `tests/test_graph_write_shape.py::test_vectors_are_stored_with_the_bulk_paths_procedures`, test: `tests/test_graph_write_shape.py::test_reads_take_the_relationship_as_truth` and test: `tests/test_graph_search_filters.py::test_fact_attributes_no_longer_carry_the_embedding_stamps`
- **Source:** `docs/superpowers/specs/2026-10-04-graphiti-library-migration-design.md` (D8); CHANGELOG v2.61.0

### DL-138 — A date filter carries at most one parameter-bearing OR group per field

- **Status:** active
- **Date:** 2026-10-05
- **Rule:** [.claude/rules/graph.md](../../.claude/rules/graph.md) — "**A Graphiti date filter takes at most ONE parameter-carrying OR group per field.**"
- **Why:** graphiti-core names a date filter's query parameter by its
  position inside its AND group only, so a second OR group with a compared
  date overwrites the first group's value and the search silently filters on
  the wrong instant. The parameterless `IS NULL` alternative is the only other
  group allowed, because an open validity bound is how a still-current fact
  is stored.
- **Enforced:** test: `tests/test_graph_search_filters.py::test_a_date_field_never_has_two_parameter_carrying_or_groups` and test: `tests/test_graph_search_filters.py::test_two_parameter_groups_would_collide_which_is_why_we_never_build_them`
- **Source:** `docs/superpowers/specs/2026-10-04-graphiti-library-migration-design.md` (D6); CHANGELOG v2.61.0

### DL-139 — Graph search reranks through one optional alias, the best kind the environment has, and the installers probe which

- **Status:** active
- **Date:** 2026-10-05
- **Rule:** [.claude/rules/models.md](../../.claude/rules/models.md) — "**Graph search's reranker is ONE optional alias with three tiers, best
  first — and a chat reranker needs logprobs and a NON-thinking alias; the
  probe is the judge**"
- **Why:** The post-cutover benchmark (2026-10-05, synthetic corpus, 72
  questions at 8 results) measured top-1 37.5% with no reranker, 66.7% with a
  chat model as reranker and 75.0% with a dedicated one — and found that the
  single-node profile had no reranking at all: it required, registered and
  probed a `gpt-4.1-nano` alias for a client search never called. The operator
  decided every deployment gets the best reranking its environment provides
  through one alias. A thinking model answers the one-token question with a
  reasoning token, which is why the alias must have thinking off and why the
  kind is decided by a probe rather than declared.
- **Enforced:** test: `tests/test_graphiti_client.py::test_search_uses_a_deep_copy_with_the_requested_limit`, test: `tests/test_graph_rerank.py::test_the_chat_question_is_upstreams_verbatim`, test: `tests/test_graph_rerank.py::test_the_kind_follows_the_setting_and_empty_means_rerank`, test: `tests/test_graph_rerank.py::test_a_reasoning_token_is_an_error_naming_the_causes_never_a_neutral_score` and test: `tests/test_single_rerank_probe.py::test_a_mapped_alias_is_probed_rerank_first_and_the_kind_written`
- **Source:** `docs/superpowers/specs/2026-10-04-graphiti-library-migration-design.md` (D3, rewritten); CHANGELOG v2.62.0

### DL-140 — A reranker that fails raises; there is no fallback order and no time budget

- **Status:** active
- **Date:** 2026-10-05
- **Rule:** [.claude/rules/graph.md](../../.claude/rules/graph.md) — "**A reranker that fails RAISES out of `search_()`; nothing is skipped and
  nothing waits on a budget**"
- **Why:** The operator's rule (2026-10-05): routing and retries belong to
  LiteLLM and the existing retry seams, and anything that fails should try
  again or throw an error rather than be silently abandoned. The release-1
  `RerankClient` returned the input order with placeholder scores on any
  error, so a reranker outage degraded ordering with nothing on record;
  raising lets the one failure taxonomy decide what the read path retries and
  puts the error in front of the agent and on the Systems row.
- **Enforced:** test: `tests/test_graph_rerank.py::test_an_http_failure_raises_and_is_classified_by_its_status`, test: `tests/test_graph_rerank.py::test_a_malformed_rerank_answer_is_a_semantic_error_not_placeholder_scores`, test: `tests/test_graph_rerank.py::test_a_transient_reranker_failure_is_retried_once_then_reported`, test: `tests/test_graph_rerank.py::test_no_reranker_refuses_to_be_called` and test: `tests/test_graphiti_client.py::test_rerank_raises_instead_of_falling_back_to_input_order`
- **Source:** `docs/superpowers/specs/2026-10-04-graphiti-library-migration-design.md` (D3, rewritten); CHANGELOG v2.62.0

### DL-141 — The deterministic model is test-only and the dry-run executor mode is removed

- **Status:** active
- **Date:** 2026-10-19
- **Rule:** (recorded here) AGENTS.md's "Two run modes" bite mark records `CC_DEMO_MODE` and
  `CC_EXECUTOR_MODE` (`dry_run` vs `live`) as it stood. The 2026-10 review
  amends it (PRD FR-42): the deterministic model becomes a test-only mode,
  renamed, with a guard that refuses it in any deployment env file; the
  dry-run executor mode is removed and a fresh install starts live. Tests
  use fakes. Not yet built; until it lands, DL-021 describes the code.
- **Why:** A dry-run executor that logs a write instead of performing it is a
  silent-success trap: the proposal reads EXECUTED and nothing happened.
  The deterministic stand-in model was only ever meant for plumbing tests,
  and a deployment that boots on it answers without judgment (the reason
  DL-021 kept the two switches separate). Rejected alternative recorded in
  the PRD addendum §B (dry-run executor mode).
- **Enforced:** discipline only — the guard and the meta-test are owed (PRD §6.1 item 8); until built, nothing enforces the amendment
- **Source:** docs/PRD.md FR-42; PRD addendum §B and §E (GATE-125..128); DL-021

### DL-142 — The read-version stale bounce at propose time is retired; world-state checks at execution replace it

- **Status:** active
- **Date:** 2026-10-19
- **Rule:** (recorded here) A proposal is not bounced at propose time for a stale read-version.
  World-state checks happen at execution, and a world that changed between
  propose and approve produces the world-changed redraft (PRD FR-5, FR-24).
- **Why:** The design contract's rule 4 had a proposal carry the version of what it
  read, to be compared at propose time. In practice the field was written
  "unknown" and nothing read it, while the checks that matter (the ticket is
  still in the state the draft assumed) can only run at execution because
  the world can change between propose and approve. Review ruling GATE-006
  retired the bounce and dropped the field from drafting guidance; the
  world-changed redraft (INGEST-045) is the mechanism that replaces it. This
  is also why `contract.ARG_SPECS` checks SHAPE only (AGENTS.md).
- **Enforced:** discipline only — a rejection or a design position; there is nothing to enforce
- **Source:** docs/PRD.md FR-5; PRD addendum §B (GATE-006, INGEST-045); docs/DESIGN.md §2 contract rule 4

### DL-143 — A policy registry as a governed table was never built

- **Status:** active
- **Date:** 2026-10-19
- **Rule:** (recorded here) There is no policy registry table. Advisory flags and charter text
  carry what a registry would; the concrete form of the idea is the scoped
  gate override on a grant (PRD FR-4).
- **Why:** The founding design imagined policy as governed data. It was never built
  because advisory flags and charter text sufficed in practice, and a table
  nothing consults would be exactly the unverified claim AGENTS.md warns
  against. Review ruling GATE-010 retired the claim and put the real need,
  per-grant overrides with an operator-authored scope, on the roadmap as
  FR-4.
- **Enforced:** discipline only — a rejection or a design position; there is nothing to enforce
- **Source:** docs/PRD.md FR-4; PRD addendum §B (GATE-010)

### DL-144 — Not every write produces a proposal: an ungated write stamps an override instead of auto-approving as system

- **Status:** active
- **Date:** 2026-10-19
- **Rule:** (recorded here) An ungated write (an operator-authored override, or the two
  deliberate exceptions of DL-003) creates no proposal and carries an
  override stamp. The design where every write produces a proposal and
  ungated ones auto-approve as "system" is rejected.
- **Why:** "Every write produces a proposal; ungated classes auto-approve as
  system" would give a gate-shaped record to writes that no gate decided,
  and it conflicts with DL-003 (ungated by design, no agent judgment in the
  path) and with the overrides requirement (FR-4), whose record is the
  stamp, not a fake approval. Review ruling GATE-060 retired the idea.
- **Enforced:** discipline only — a rejection or a design position; there is nothing to enforce
- **Source:** docs/PRD.md FR-2, FR-4; PRD addendum §B (GATE-060); DL-003

### DL-145 — Event-subscription agent wakes and agent.request/response events were never built

- **Status:** active
- **Date:** 2026-10-19
- **Rule:** (recorded here) Agents wake from three sources only: the heartbeat scheduler, the
  ledger dispatcher and direct tasking. There is no event-subscription wake
  and no agent.request/agent.response event; collaboration is consults (the
  asker parked awaiting agents) and tasks.
- **Why:** The founding eventing design had agents subscribing to log events.
  It was never built, and the three real wake sources are each traceable to
  a row or an operator click, which keeps "why did this agent run" an
  answerable question. Review rulings RUNTIME-003/004 confirmed the
  three-source doctrine.
- **Enforced:** discipline only — a rejection or a design position; there is nothing to enforce
- **Source:** docs/PRD.md FR-17; PRD addendum §A and §B (RUNTIME-003/004)

### DL-146 — DBOS was never adopted; durability is the deferred-tool pattern plus the persisted message history

- **Status:** active
- **Date:** 2026-10-19
- **Rule:** (recorded here) Durable pause and resume is pydantic-ai's deferred tool call plus the
  project's own persisted message history. DBOS (named in the founding
  runtime decision D22) was never adopted and is not a dependency.
- **Why:** D22 chose Pydantic AI 2.0 with DBOS for durable execution (a library
  over Postgres, no separate cluster, as against Temporal). The build found
  the deferred-tool primitive and the Postgres spine already gave the
  durability the design needed, so the extra layer never arrived. Review
  rulings RUNTIME-013/014 recorded the divergence; see DL-161 for the
  founding runtime comparison this narrows.
- **Enforced:** discipline only — a rejection or a design position; there is nothing to enforce
- **Source:** docs/PRD.md FR-18; PRD addendum §A and §B (RUNTIME-013/014)

### DL-147 — Classifier-driven model routing and the LiteLLM adaptive router are rejected; routing is the operator's, inside LiteLLM

- **Status:** active
- **Date:** 2026-07-26
- **Rule:** (recorded here) No routing logic (quality router, complexity classifier, Auto Router)
  is built into Central Command. Per-agent pinning (`CC_MODEL_<AGENT_ID>`)
  is the primary routing lever and keywords the only lexical override.
- **Why:** Measured, not assumed. On 283 real emails the complexity classifier sent
  75% to COMPLEX and its correlation with difficulty was inverted (dismissed
  mail 79% COMPLEX, acted-on mail 7%), so tier assignment cannot come from
  it. The adaptive bandit's only positive signal is a regex over human
  thanks, so agent runs decay every model, and in the deployed LiteLLM
  version the adaptive router is config-file-only (DB-created routers are
  absent from state and return HTTP 400). The default alias is a deployment
  row rather than a `model_group_alias`, so an Auto Router can later replace
  it with one API call if the operator chooses. Review rulings MODELS-013/015
  confirmed the rejection.
- **Enforced:** discipline only — a rejection or a design position; there is nothing to enforce
- **Source:** docs/superpowers/specs/2026-07-26-adaptive-routing-that-learns-design.md; docs/superpowers/specs/2026-07-26-quality-router-migration-design.md; docs/superpowers/specs/2026-07-27-default-alias-seam-design.md; PRD addendum §B (MODELS-013/015)

### DL-148 — The dispatch token-budget valve and the enterprise LiteLLM features are not adopted

- **Status:** active
- **Date:** 2026-10-19
- **Rule:** (recorded here) Dispatch carries no token-budget valve (the existing one is to be
  removed), and no enterprise-licensed LiteLLM feature is used.
- **Why:** The valve was judged unnecessary (review ruling MODELS-018, removal owed
  in PRD §6.1). The enterprise features are unlicensed here and, checked
  against LiteLLM's current documentation, give one operator nothing the
  open features do not (MODELS-024).
- **Enforced:** discipline only — a rejection or a design position; there is nothing to enforce
- **Source:** PRD addendum §B and §E (MODELS-018, MODELS-024)

### DL-149 — Embedding changes: per-vector stamps, a keyword-only freeze, a Postgres embedder record, a heartbeat re-embed, in-place rewrite and a second graph are rejected

- **Status:** active
- **Date:** 2026-10-19
- **Rule:** (recorded here) The embedding model changes only through the FR-33 migration, and an
  embedding never falls back to a model with different weights. Rejected,
  each with its reason: per-vector embedding stamps; a keyword-only read
  mode during a freeze; an embedder record in Postgres; re-embedding as a
  heartbeat action; an in-place vector rewrite; a second graph built and cut
  over.
- **Why:** From the embedding-model-change research (PRD addendum §B). Stamps
  leaked into agent results and extraction never wrote them, so they were a
  claim, not a measurement. A keyword-only mode is a fail-soft fallback,
  which a standing ruling forbids (see DL-140). The embedder record belongs
  with the graph, because separate stores restore separately and a graph
  restore would contradict a Postgres record. A heartbeat re-embed is a
  one-shot that needs its own state. An in-place rewrite leaves mixed
  vectors visible to live search for the whole run, and a second graph
  doubles storage for one operator. The local-serving record adds that
  vectors from different weights are incompatible, so a failed embedding
  write fails and retries; a second embedding host is legitimate only if it
  passes the same-weights checklist (model and revision, pooling, L2
  normalisation, output dimensions, instruction prefix, precision); the
  re-embed is a tracked, resumable script, and the graph write path accepting
  a workstation dependency was the operator's explicit availability trade.
- **Enforced:** discipline only — a rejection or a design position; there is nothing to enforce
- **Source:** docs/superpowers/specs/2026-07-26-local-serving-and-embedding-migration-design.md; docs/PRD.md FR-33, FR-34; PRD addendum §B (embedding rows)

### DL-150 — Provisional write-now-veto-later memory and Letta-style agent self-editing are rejected

- **Status:** active
- **Date:** 2026-08-01
- **Rule:** (recorded here) Every write to any memory scope is a gated `graph.add_episode`
  proposal. Rejected: provisional or expiry-based memory ("write now, veto
  within N days") and agent self-editing of memory outside the gate.
- **Why:** Revised D11 (D11-r1): agents get a private graph partition, but a lesson
  the operator never saw would shape later behaviour unreviewed, which is
  the failure the gate exists to prevent. Provisional writes would put
  unreviewed content in the memory every agent reads, and self-editing
  outside the gate removes the review altogether. The target scope is a
  visible field on each episode proposal and the Executor writes the
  operator-approved scope, never the agent's own arguments; session-close
  reflection yields at most one coaching self-nomination per closed session,
  as a signal, never a charter edit.
- **Enforced:** discipline only — a rejection or a design position; there is nothing to enforce
- **Source:** docs/superpowers/specs/2026-08-01-self-directed-learning-design.md; PRD addendum §B (GRAPH-028/029)

### DL-151 — Graphiti communities, sagas, triplet writes and bulk ingest are not adopted; no fork of graphiti-core

- **Status:** active
- **Date:** 2026-10-04
- **Rule:** (recorded here) One proposal is one episode. Not adopted: communities, sagas,
  `add_triplet`, message and JSON sources, bulk ingest. graphiti-core is not
  forked; two fixes ride as patches (DL-132).
- **Why:** Communities cost one LLM call per member with a long-open clustering bug
  and no evidence of value. Sagas' summaries were never exercised end to end
  and do not affect extraction. `add_triplet` creates bare nodes with no
  type, summary or provenance, and bulk ingest has the same shape problem.
  Per-episode extraction instructions and excluded entity types stay a
  deferred, benchmarked experiment.
- **Enforced:** discipline only — a rejection or a design position; there is nothing to enforce
- **Source:** docs/superpowers/specs/2026-10-04-graphiti-library-migration-design.md; PRD addendum §B (GRAPH-044..047)

### DL-152 — The D16 guaranteed backlog trickle was never built; bulk dismissal drains a backlog by pattern

- **Status:** active
- **Date:** 2026-10-19
- **Rule:** (recorded here) No guaranteed-trickle mechanism exists for a large backlog. A backlog
  is drained by bulk dismissal over a pinned set resolved at propose time.
- **Why:** The founding D16 trickle was never built. Bulk dismissal does the job
  with a human decision in the path: a pinned set resolved to ids at propose
  or claim time, never a standing rule, with a preview-then-execute step that
  refuses if the set drifted. Review ruling INGEST-003.
- **Enforced:** discipline only — a rejection or a design position; there is nothing to enforce
- **Source:** docs/superpowers/specs/2026-08-17-bulk-dismissal-design.md; PRD addendum §B (INGEST-003)

### DL-153 — The surveyed Exchange feature set and a provider abstraction are cut; Exchange is a native client at the seam

- **Status:** active
- **Date:** 2026-09-25
- **Rule:** (recorded here) The mail and calendar façades route to Exchange when configured, else
  to n8n, with signatures and callers unchanged. Not adopted: a provider
  interface class, autodiscover, Kerberos/SSPI, attachments, classification
  filtering before real fixtures exist. New gated writes `mail.send` and
  `mail.move` pin recipients and body at propose time.
- **Why:** The surveyed feature list was a wishlist never revised; the design cut
  the native client to the seam. The client uses `exchangelib` (synchronous,
  run through `asyncio.to_thread`, a core dependency) rather than hand-rolled
  SOAP, one query language per provider is translated at the seam, and
  `mail.unsubscribe` is withheld on Exchange until its DKIM verdict can be
  verified (and send and move on Gmail) using provider membership, the Jira
  flavour mechanism generalised. Data Center epic structure, rate limiting
  and `X-Atlassian-Token` stay out of scope until a probe reports.
- **Enforced:** discipline only — a rejection or a design position; there is nothing to enforce
- **Source:** docs/superpowers/specs/2026-09-25-exchange-native-client-design.md; docs/superpowers/specs/2026-09-23-jira-data-center-flavor-design.md; PRD addendum §B (INGEST-006..030)

### DL-154 — Ready-made installers and air-gap tools are not adopted; the per-step ledger and one answer file won

- **Status:** active
- **Date:** 2026-09-23
- **Rule:** (recorded here) The installer is a deterministic driver over a per-step ledger
  (`steps.tsv`) and one answer file. Not adopted: Zarf, Hauler, KOTS, goss,
  Replicated troubleshoot, Ansible, bats, regctl/crane/skopeo, gum/whiptail,
  bundles, a TUI or a Python installer.
- **Why:** None has an installer precedent for this shape, and each needs an
  interpreter, a Windows support level or a vendor/customer split the target
  site does not guarantee: Zarf and Hauler are k8s-only transport tools
  solving a problem the site lacks, and KOTS's embedded-database appliance
  conflicts with Postgres and Neo4j. The agent-conducted loop and per-step
  ledger are kept despite having no precedent (2026-09-23 design record,
  D8). Digest pinning is a preference, not a requirement, and a tag
  substituted from an enterprise mirror is trusted.
- **Enforced:** discipline only — a rejection or a design position; there is nothing to enforce
- **Source:** docs/superpowers/specs/2026-09-23-airgap-check-configure-setup-design.md; docs/superpowers/specs/2026-10-01-setup-ledger-selfcheck-design.md; PRD addendum §B (DEPLOY-SINGLE-029)

### DL-155 — Flux GitOps for k3s is not adopted; the deployment is numbered manifests with a trigger-file updater

- **Status:** active
- **Date:** 2026-09-03
- **Rule:** (recorded here) The k3s deployment is applied by the release updater, not by an
  auto-syncing GitOps controller.
- **Why:** Flux was assessed and deferred (DEPLOY-K3S-020): auto-sync clashes with
  the approval-gate culture, where nothing changes a running system without
  an operator deciding. The phase-3 design chose a Helm umbrella with
  `values.yaml` as the configuration surface instead.
- **Enforced:** discipline only — a rejection or a design position; there is nothing to enforce
- **Source:** docs/superpowers/specs/2026-09-03-deploy-refactor-design.md; PRD addendum §B (DEPLOY-K3S-020)

### DL-156 — Out-of-cockpit notification (email, push) is out of scope

- **Status:** active
- **Date:** 2026-10-19
- **Rule:** (recorded here) Notification tiers are in-app only: interrupt (approvals, errors,
  agent-initiated conversations), badge (unread and aging items) and digest
  (everything else, batched daily). Email and push are out of scope for the
  current version, with no plans.
- **Why:** The discussion-stall record and the teaming doctrine both chose to
  notify inside the cockpit and nowhere else; the review confirmed it as the
  current position (COCKPIT-010). Reversible only by a new operator
  decision.
- **Enforced:** discipline only — a rejection or a design position; there is nothing to enforce
- **Source:** docs/superpowers/specs/2026-07-27-discussion-stall-escalation-design.md; docs/superpowers/specs/2026-07-29-teaming-consultation-doctrine-design.md; PRD addendum §B (COCKPIT-010)

### DL-157 — Build the control plane, adopt the runtime, buy nothing: why not OpenClaw, LangGraph, CrewAI and the rest

- **Status:** active
- **Date:** 2026-07-18
- **Rule:** (recorded here) The control plane is built here; the agent runtime is adopted. Scored
  against five requirements (R1 per-agent supervision with mid-task
  redirect; R2 approval gates and agent-initiated questions; R3 a feedback
  loop that durably tunes standing guidance; R4 agent-to-agent delegation;
  R5 fully self-hosted, open source, modifiable), thirteen candidates were
  assessed and none was adopted as the platform.
- **Why:** "Owning it is the point": the supervision experience is the part no
  off-the-shelf tool gets right for a single self-hosting operator. Per
  candidate, as recorded at the time (first-party READMEs, none functionally
  tested): OpenClaw+Nerve is the UX reference only: a 2026 CVE record, an
  unfenced marketplace, approvals that lean post-hoc rather than blocking,
  no durable feedback-to-guidance loop, and a very large foreign codebase
  (Nerve is vendored under `web/` as the cockpit's base; OpenClaw was not
  adopted). LangGraph as a library is the documented fallback because its
  Studio/inbox server is not open source. CrewAI's open-source human input
  blocks on stdin, with async approval a paid product. AutoGen/AG2 is
  maintenance-mode and superseded. Microsoft Agent Framework's centre of
  gravity is Azure/OpenAI with a non-production dev UI. HumanLayer's SDK is
  deprecated with a closed successor (the `require_approval` idea was kept).
  Archon pivoted to a CI/CD harness for AI coding with no agent-to-agent
  delegation. FleetQ has all five requirements verified in source but is
  AGPL, Laravel and single-maintainer, so its design was mined, not its code.
  Mission Control is alpha (re-check later). Langfuse was noted for tracing.
  Patterns taken: control plane as proxy, a proposal inbox, a thinker/executor
  credential split, session trees, durable serialisable interrupts, and
  LLM-proposed human-approved guidance evolution.
- **Enforced:** discipline only — a rejection or a design position; there is nothing to enforce
- **Source:** docs/DESIGN.md §0 and §6; founding research and recommendation (2026-07-18, no longer in the repo; summarised in the PRD review record)

### DL-158 — The operator's earlier n8n project could not host the vision; n8n stays a façade, not the platform

- **Status:** active
- **Date:** 2026-07-18
- **Rule:** (recorded here) n8n holds the Gmail credential and the `lib-*` provider façades;
  agent-shaped work is not built in n8n.
- **Why:** The earlier n8n-based pipeline was finished and disciplined but opaque to
  steer: the AI-agent node is one opaque invocation whose only intervention
  is kill, prompts are string literals in workflow JSON, and session memory
  is unreliable in queue mode. "Pipeline-shaped workloads fit n8n;
  agent-shaped workloads don't." What carried over: Graphiti/Neo4j and
  Postgres, the facade idea (reads free, mutations only after approval,
  credentials staying in n8n) and the security patterns (credential split,
  idempotency, provenance, a poisoning boundary). The 60-node approval bridge
  was retired but its behaviour (quiet-period re-ping, free text routes to
  clarification and never a silent reject, outcome postback) was kept as
  requirements. The "lost" feeling traced to process-artifact weight and a
  fenced reactive-only assistant.
- **Enforced:** discipline only — a rejection or a design position; there is nothing to enforce
- **Source:** docs/DESIGN.md §0 and §6; founding research and recommendation (2026-07-18, no longer in the repo; summarised in the PRD review record)

### DL-159 — The nine-row failure-mode catalogue: assume the agent is wrong before assuming it is attacked

- **Status:** active
- **Date:** 2026-07-18
- **Rule:** (recorded here) Treat every agent conclusion as unverified until independently
  checked, and design so that a wrong agent cannot corrupt anything without
  the error being caught at the gate. Each failure mode below is mapped to
  the controls that catch it.
- **Why:** The founding trust record catalogued nine failure modes, ranked by
  expected frequency: (1) a hallucinated claim (the top concern, for example
  a quoted date the email does not contain), (2) misreading or
  misattribution, (3) a wrong-target mutation, (4) compounding memory
  corruption, (5) content misdirection taken as benign error (a forwarded
  thread's "please close the old tickets" is data, not a command), (6)
  duplicate or skipped work, (7) runaway cost, (8) a bad skill or bad update
  (curated, versioned skills; no auto-install; review on update; revert) and
  (9) the malicious cases, out of scope by construction. The framing wins
  because it contains the blast radius of error rather than trying to make
  the model infallible, which is impossible; the boundary also neutralising
  the malicious case is a bonus, not the driver. The honest trade in the
  first release was to lean on propose-only tools, the gateway chokepoint and
  total human approval so the auditor and isolation could wait, sound only
  while human approval stays meaningful; the named mitigations for approval
  fatigue are backpressure paced to review capacity, proposals that say what
  will happen, irreversible actions made visually distinct, and the auditor
  in shadow mode first. The non-negotiable minimum before real mail: curated
  skills only and internal services bound to loopback. Host security (disk
  encryption, hardening, backups of trusted stores) was assumed, not solved.
- **Enforced:** discipline only — a rejection or a design position; there is nothing to enforce
- **Source:** docs/DESIGN.md §0 and §6; founding research and recommendation (2026-07-18, no longer in the repo; summarised in the PRD review record); docs/PRD.md §1

### DL-160 — The threat model is error, not malice, for a single operator; sandboxes, egress allowlists, a vault and anomaly detection were cut

- **Status:** active
- **Date:** 2026-07-19
- **Rule:** (recorded here) The recalibration to error-not-malice was the operator's call, resting
  on a single operator, no exfiltration-capable tools, sole access to the
  work systems and an air-gapped work target. It stands only while the tool
  surface stays as designed or the system stays single-user. Sandboxing,
  egress allowlists, a secrets vault and anomaly detection (D19-D21) were
  cut, to be revisited if the tool surface gains exfiltration ability.
- **Why:** The prior draft's adversarial framing was judged overweight. D19 chose
  process isolation and a credential split over containers or OS-user
  separation, D20 no egress-allowlist infrastructure, D21 reuse of n8n's
  credential isolation through the façade with no vault. Several premises
  have since moved and the review recorded it: mail send is now a gated step
  (FR-27), outward gates can be overridden by the operator (FR-4), and sandbox
  egress is open by decision (DL-005); the founding "no exfiltration-capable
  tools" premise is NARROWED, not wrong: the gate, not tool absence,
  contains the error. The MVP defence in depth (propose-only tools, a gateway
  chokepoint, human approval of everything, data-not-commands, least-privilege
  grants, executor-held secrets, a memory write-gate with trust levels,
  budgets) stands; the later controls (an independent auditor starting with
  low-risk graph writes, capability stripping by delegation depth) arrived as
  DL-002 and the consult bounds.
- **Enforced:** discipline only — a rejection or a design position; there is nothing to enforce
- **Source:** docs/DESIGN.md §0 and §6; founding research and recommendation (2026-07-18, no longer in the repo; summarised in the PRD review record); docs/PRD.md §1; PRD addendum §C (founding premise NARROWED); review rulings GATE-017..019

### DL-161 — Pydantic AI plus Claude was chosen over the Claude Agent SDK, Temporal and microservices; the runtime stays swappable

- **Status:** active
- **Date:** 2026-07-19
- **Rule:** (recorded here) The runtime is Pydantic AI with Claude models behind one seam
  (`runtime/models.py:resolve_model()`); the system is a modular monolith.
- **Why:** D22 was framed as the one decision to make explicit before code. The
  Claude Agent SDK was the live alternative for Claude-nativeness at the cost
  of building durability by hand; Pydantic AI's deferred-tool protocol
  (typed, serialisable pauses that can park for days) met the two settling
  requirements, durability everywhere and proposals as typed objects.
  "Pydantic AI is not 'not Claude'". Temporal needs a cluster to run, so
  DBOS (a library over Postgres) was named, then never needed (DL-146).
  Microservices were rejected for a single homelab: module boundaries are
  splittable later. Python was chosen for one language across runtime,
  gateway, dispatcher and API (a TypeScript choice would reopen the runtime
  question). A thin runtime interface keeps the choice from being a one-way
  door. Phase 1 existed to prove the spine end to end: read an email, propose
  a Jira change with evidence, restart the server while it waits, approve,
  and see the ticket updated once with provenance.
- **Enforced:** discipline only — a rejection or a design position; there is nothing to enforce
- **Source:** docs/DESIGN.md §0 and §6; founding research and recommendation (2026-07-18, no longer in the repo; summarised in the PRD review record)

### DL-162 — Approval attaches to what work does, never to how it was started; scheduled work is born disabled

- **Status:** active
- **Date:** 2026-07-23
- **Rule:** (recorded here) The heartbeat neither bypasses a gate that exists nor adds one that
  does not. A schedule changes when work runs, not its permission level.
  Schedules are governed rows with `cron | every | at`; one heartbeat runs
  per database behind an advisory lock; missed fires are skipped and
  recorded, never replayed; run history is the event log, not a second
  table.
- **Why:** Everything proactive had been operator-initiated; recurring work should
  be visible data with an on/off switch, not shell scripts or hidden loops.
  Whether a scheduled agent ever touches approval is decided at creation by
  the packs it holds (a read-only analyst completes a nightly schedule with
  zero approvals; granting a propose pack makes its writes park). Six
  decision points were answered: in-process timers over external ones,
  `croniter` over `every`/`at` only, seeded schedules DISABLED so nothing
  quietly burns tokens (amended since: internal housekeeping such as
  `mail-poll` may seed enabled and is listed, PRD FR-41), skip rather than
  replay (a box that was off overnight must not burst stale sweeps at boot),
  keep the feed poller, and run history in the event log. The same principle
  shut the coach out of the heartbeat: coaching is triggered only by the
  operator.
- **Enforced:** test: `tests/test_heartbeat.py::test_action_registry_parity_with_the_lever_allowlist` pins the registry's levers
- **Source:** docs/PRD.md FR-41; heartbeat design proposal (founding artifact, 2026-07-23; D27); docs/ARCHITECTURE.md heartbeat section

### DL-163 — The orchestrator orchestrates: plan review on, stall detection not budgets, questions parked for the operator, capability gaps are answers

- **Status:** active
- **Date:** 2026-07-24
- **Rule:** (recorded here) The orchestrator is a role that runs a durable plan, assign, receive,
  decide loop; the gate applies only where the work changes the world.
  Decisions of 2026-07-24: D7-P plan review ON before round one; D7-B stall
  detection, not budgets; D7-Q `ask_operator` parks a question whose answer
  is trusted operator evidence; D7-E a cockpit entry point to assign a
  project. Capability gaps are first-class answers.
- **Why:** The operator's scope correction: v1 was a fire-and-visible fan-out; the
  orchestrator "must orchestrate", and an agent answering a question or
  reviewing makes no external change, so its response flows back ungated.
  Plan review parks the plan (sub-tasks, assignees, order, what it will NOT
  do) for approve or revise, and a stall-triggered replan re-parks. Time is
  not the constraint, stuck-ness is: two stalls park with `ask_operator` and
  the ledger attached, and the round and fan-out limits are runaway
  safety nets, not pacing. Agents may say they lack knowledge, tools or
  current reference material and request the remedy instead of guessing: a
  declared gap is reviewable, a guessed-past gap is buried until it surfaces
  as mistakes. Rejected: one long run calling children in-tool (dies with
  the process and blocks for days at a gate), handoff or mesh orchestration
  (dissolves the single accountable context the operator supervises),
  pydantic-graph (no self-persistence in v2) and Temporal/DBOS. Amended
  2026-10-19: FR-19 reverses the founding star shape so any agent may create
  and assign tasks; the orchestrator's plan-assign loop and the operator
  checkpoint stand.
- **Enforced:** discipline only — a rejection or a design position; there is nothing to enforce
- **Source:** D7 phase 2 design v2 (founding artifact, 2026-07-24); docs/PRD.md FR-19; PRD addendum §C (D7 REVERSED)

### DL-164 — Model facts live in one tracked file, applied by script; an absent cost is not zero; routing preferences and cost have fixed homes

- **Status:** active
- **Date:** 2026-07-26
- **Rule:** (recorded here) Declared model facts (tiers, costs, fallbacks) live in one tracked file
  applied idempotently by a script, with `verify.sh` failing if the live
  state drifts. Every model declares an explicit cost. Routing preferences
  live in `model_info` and cost in `litellm_params`. Policy writes go through
  `PATCH /model/{id}/update` with `model_info.id` explicit.
- **Why:** Putting either preference or cost in the wrong home fails silently (a
  coin flip with no error), and `POST /model/update` silently drops
  `model_info`. An undeclared cost read as zero is the same silent failure
  DL-064 records for capability flags. Retired models (`qwen3.6-35b`,
  `north-mini-code`) must not be re-registered. Background health checks stay
  off until the empty-messages probe is verified. The default-alias seam
  work was scoped to the seam only: the operator cut it with "I don't want
  to redesign anything at all".
- **Enforced:** discipline only — a rejection or a design position; there is nothing to enforce
- **Source:** docs/superpowers/specs/2026-07-26-adaptive-routing-that-learns-design.md; docs/superpowers/specs/2026-07-27-default-alias-seam-design.md

### DL-165 — The default alias is a deployment row; a verified fallback is not exercised; self-hosted-as-policy is retired

- **Status:** active
- **Date:** 2026-07-27
- **Rule:** (recorded here) No Central Command request may use a non-self-hosted model unless a
  fallback triggers (the spec's constraint 2; its enforcing test was
  explicitly rejected). A fallback verified present in configuration is
  deliberately not exercised with a dead-port test: the first real outage is
  the first test.
- **Why:** The seam spec recorded a `cc-default` to hosted-model fallback, which
  DL-067 later removed (2026-07-31); the spec still reads "implemented", so
  treat DL-067 as the in-force position. The related founding position that
  agents run self-hosted, including the graduated auditor, as standing
  policy is RETIRED (PRD FR-36): placement is the operator's choice.
- **Enforced:** discipline only — a rejection or a design position; there is nothing to enforce
- **Source:** docs/superpowers/specs/2026-07-27-default-alias-seam-design.md; docs/PRD.md FR-36; DL-067

### DL-166 — A skill is subject knowledge only; skills are database rows; every agent capability must be declared in the registry

- **Status:** active
- **Date:** 2026-07-26
- **Rule:** (recorded here) Vocabulary is fixed: a capability is a gated write, a pack is a bundle
  of tool grants, a skill is subject knowledge. Tool grants stay in packs.
  Skills are database rows delivered through the framework's capability
  loading, not a file-read tool. Anything an agent can do must be declared
  in the registry ("no undeclared surface", which is not "everything is
  gated").
- **Why:** The runtime tier has no filesystem access, because an agent that can
  read files can read `.env`. Only current skill versions are searchable;
  superseded versions stay in history and never reach an agent as fact.
  Reference search is Postgres full-text, not embeddings, because the target
  is air-gapped and no extra model dependency is wanted (an embeddings
  backend can sit behind the same seam). Freshness metadata (`describes`,
  `captured_at`, `source_url`) reaches the agent with every loaded skill;
  `stale_knowledge` is rejected unless it cites `doc_id`, `doc_says` and
  `observed`, replacing a substring match on "CAPABILITY GAP"; the catalog
  lists ungranted skills by title so a gap reads "needs access to X". Skill
  authoring and grants are direct operator actions that each emit an event,
  and a run that does not use a skill carries none of its guidance text.
- **Enforced:** discipline only — a rejection or a design position; there is nothing to enforce
- **Source:** docs/superpowers/specs/2026-07-26-skills-library-design.md

### DL-167 — The coach has full read access, runs through the normal build path, and is triggered only by the operator

- **Status:** active
- **Date:** 2026-07-27
- **Rule:** (recorded here) The coach's selection is foregrounded context, not a fence; a
  discovered citation is verified as real (not as representative) and
  marked `discovered`, and `claim_matches_source: None` still means not
  checked. The governed charter is the system prompt. The coach holds no
  Jira, graph or LiteLLM grant. There is no team-wide coaching page.
  Self-coaching is ordinary. The heartbeat may not start a coach session.
- **Why:** Approval attaches to what work does, not to its trigger (DL-162), so a
  scheduled coach would need no more approval than a typed one, yet
  coaching changes standing guidance and stays with the operator. Positive
  signal is needed to avoid a timid-drift failure, so thumbs must be able to
  record good work as well as failures (not built). This predates and
  narrows AGENTS.md's "Nothing feeds coaching automatically; the operator
  curates signals case by case" (see the spec's Decision 2).
- **Enforced:** discipline only — a rejection or a design position; there is nothing to enforce
- **Source:** docs/superpowers/specs/2026-07-27-coaching-experiences-design.md

### DL-168 — A discussion stall is typed by who spoke last; nothing auto-concludes a discussion

- **Status:** active
- **Date:** 2026-07-27
- **Rule:** (recorded here) S1 (the last turn is the agent's, so the operator is blocking) gets an
  amber flag; S2 (the last turn is the operator's, so the agent is blocking)
  gets a nudge. "Has the operator ever spoken" is not the test. The sweep is
  one idempotent heartbeat action (`stalled_at`, `nudged_at`); the threshold
  is `CC_DISCUSSION_STALL_HOURS` (default 24) from the session's
  `updated_at`.
- **Why:** Auto-concluding on the agent's behalf is out of scope: the summary is an
  agent claim, and synthesising one would fabricate the record. Every close
  path goes through one seam that marks the session done and emits
  `conversation.ended` with a stored `closed_reason`, and a concluded
  discussion is read-only. Seeded governed schedules are born disabled, and
  notifying outside the cockpit is out of scope (DL-156).
- **Enforced:** discipline only — a rejection or a design position; there is nothing to enforce
- **Source:** docs/superpowers/specs/2026-07-27-discussion-stall-escalation-design.md

### DL-169 — Knowledge layers: packs permit, skills instruct, the graph holds what is true, MCP is transport

- **Status:** active
- **Date:** 2026-07-29
- **Rule:** (recorded here) Capability packs say what an agent may do (permissions only, never
  knowledge); skills say how; the graph and knowledge base say what is true;
  MCP is transport. Operator standards are skills if stable and graph facts
  if they evolve. Every agent reads one shared store: there is no private
  per-agent memory.
- **Why:** The steward triages its own confidence: high-confidence routine facts
  batch into bulk-approval digests and only flagged items escalate.
  Contradictions surface as gated steward proposals, never silent mutations
  (the invalidation decision is itself untrusted). Confluence change capture
  is hybrid: webhooks accelerate and periodic CQL incremental crawls are
  ground truth. Ingestion is batched and finalised per run, relation
  extraction is held to a stricter confidence bar than entity extraction,
  direct graph reads stay ungated and LLM-free with judgment-heavy
  synthesis going through a librarian consult, and a Confluence supersede is
  a convention (status metadata plus a link) written as a gated action. Graph
  content stays disposable until the steward mechanism is judged sound; the
  mechanism is evaluated, not the corpus (amended: PRD FR-32). Note the
  private-memory ban was refined by DL-173 into governed private partitions.
- **Enforced:** discipline only — a rejection or a design position; there is nothing to enforce
- **Source:** docs/superpowers/specs/2026-07-29-knowledge-layer-routing-design.md; docs/PRD.md FR-32

### DL-170 — Teaming doctrine: consult bounds are enforced by the runtime, handoffs go through task proposals, the roster grows on observed failure

- **Status:** active
- **Date:** 2026-07-29
- **Rule:** (recorded here) Consult bounds are enforced by the runtime, not charter wording: depth
  cap of 2, cycle refusal, budget propagation and size-capped distillation.
  The specialist drafts and parks its own proposal with the consult chain in
  provenance. Consult when you need an answer; hand off a task when the work
  belongs to the other agent, through `task-propose`, never peer to peer. A
  task proposal may declare await-result, parking the proposer
  `AWAITING_AGENTS`. Consulted specialists run read-only by construction.
- **Why:** Free-form peer messaging stays struck, and the supervised star shape of
  the founding doctrine was reversed by FR-19 (any agent may now create and
  assign tasks), with the consult and await-result machinery kept. Agents
  may contact the operator on an attention budget (passive capture first,
  about two to three contacts a day, digest by default, the dial the
  operator's). The roster grows with the work: agents are hired only at the
  operator's origination on observed failure, there is no fixed target size,
  and a roster change never requires recoaching. Consults stay single-shot
  with continuity caller-carried; chained consult waits nest and persist in
  `run_state` (2026-08-23). Capability stripping for consults is deferred.
- **Enforced:** discipline only — a rejection or a design position; there is nothing to enforce
- **Source:** docs/superpowers/specs/2026-07-29-teaming-consultation-doctrine-design.md; docs/superpowers/specs/2026-08-22-expert-team-scaling-design.md; docs/PRD.md FR-19

### DL-171 — The graduation ladder: typed judgments, a pure pairing function, nothing tunes itself, and post-graduation starvation is a known blind spot

- **Status:** active
- **Date:** 2026-07-30
- **Rule:** (recorded here) Rung 0 judgments are typed, stated before the operator decides, and
  independently derived from the raw source; an absent judgment is counted
  separately and never defaulted to low. Rung 1 is a pure pairing function
  over event rows with four quadrants, per agent and per class, the last
  decision winning, reports computed fresh on every call. Disagreements are
  coaching inputs with event-id-backed citations. Nothing tunes a threshold
  on its own.
- **Why:** Auto-handled cases are tallied separately and never counted as
  agreement, and graduation is not retroactive. After graduation the
  evidence dries up (post-graduation starvation), so before any class whose
  blast radius is a world change graduates, sampled human spot-checks must
  exist (see DL-020; PRD FR-10). The three pairing functions are not
  generalised into a framework at n=3; only the ladder's conventions are
  shared. The dangerous quadrant is listed case by case, not as a rate.
- **Enforced:** discipline only — a rejection or a design position; there is nothing to enforce
- **Source:** docs/superpowers/specs/2026-07-30-trust-tier-graduation-design.md; DL-002; DL-020

### DL-172 — Outage equivalence: an outage delays but never loses, degrades or requires re-creation

- **Status:** active
- **Date:** 2026-07-31
- **Rule:** (recorded here) A transient failure never produces a terminal state: it produces a
  retryable one (`AWAITING_RESUME`, `RETRY_PENDING`, or `UNPROCESSED`).
  Retries are health-gated: the dependency is probed cheaply and the sweep is
  skipped while it is down, so the attempt budget is never spent against a
  dead provider. The operator's own approvals never retry silently: an
  approved-but-pending execution shows in the Inbox with no re-approval.
- **Why:** Fail fast beats degrade: the full-size context bound was chosen over a
  servable smaller-model fallback so the failure lands retryable at full
  quality ("degrade never, delay freely, converge always"; see DL-067).
  Executor replay is cursor-based (the `completed` list), so a half-landed
  non-idempotent write may be applied twice; the residual risk is recorded
  and façade idempotency keys are the owed fix. Heartbeat windowing is house
  style: prefer designs where the next natural occurrence absorbs a gap over
  explicit retry machinery. Attempt-exhaustion promotion to FAILED for a
  DOWN dependency was RETIRED by FR-40: nothing terminal comes from DOWN.
- **Enforced:** discipline only — a rejection or a design position; there is nothing to enforce
- **Source:** docs/superpowers/specs/2026-07-31-outage-equivalence-design.md; docs/PRD.md FR-40

### DL-173 — No ungoverned private agent memory: private graph partitions, every write gated, the scope visible on the proposal

- **Status:** active
- **Date:** 2026-08-01
- **Rule:** (recorded here) Revised D11 (D11-r1): agents get a private graph partition, but every
  write to any scope is a gated `graph.add_episode` proposal; a `private`
  episode may name `for_agent` (DL-061). Private and shared episode writes
  are separate action classes, private graduating first, the operator
  flipping each. Demo mode: reflection produces nothing.
- **Why:** Role-specific lessons should persist without the operator noticing every
  mistake, which does not scale, yet unreviewed memory is the compounding
  corruption failure (DL-159). So the target scope is a visible field on the
  proposal and the Executor writes the operator-approved scope. The
  rejected alternatives are DL-150. A later cross-session reviewer slice is
  a heartbeat action seeded disabled that emits the same proposal
  vocabulary.
- **Enforced:** discipline only — a rejection or a design position; there is nothing to enforce
- **Source:** docs/superpowers/specs/2026-08-01-self-directed-learning-design.md; DL-061

### DL-174 — Graph inspection, activity coverage and bulk dismissal: read-only browser, session as the unit, pinned sets

- **Status:** active
- **Date:** 2026-08-09
- **Rule:** (recorded here) The browser never talks to Neo4j; the API tier's bolt client is
  read-only with canned queries and no raw-Cypher endpoint. The session is
  the complete unit of activity, so a faceted session list covers all agent
  activity by construction, backed by a guard test. A bulk dismissal is a
  pinned set resolved to ids at propose or claim time, never a standing
  rule, and new arrivals are not covered.
- **Why:** Refinding uses server-side paging with "Load more", never infinite
  scroll, and the sidebar filter ships last. Deliberately not built until
  evidence: a work-item ledger screen, full-text transcript search, saved
  views, virtualized rendering. Bulk dismissal is two-step, preview then
  execute, and execute refuses if the resolved set drifted past a digest;
  the 2,500-reference cap is stated, never silently truncated. The graph
  visualiser seam has three states (disabled, unreachable, live); the UI
  believes the probe, not the flag, and the API never starts or stops the
  visualiser container. Operator curation writes are DL-003.
- **Enforced:** discipline only — a rejection or a design position; there is nothing to enforce
- **Source:** docs/superpowers/specs/2026-08-09-graph-inspection-design.md; docs/superpowers/specs/2026-08-11-activity-coverage-design.md; docs/superpowers/specs/2026-08-17-bulk-dismissal-design.md

### DL-175 — The graph auditor is read-only and judges from the raw rows; remediation is a separate agent and never re-ingests

- **Status:** active
- **Date:** 2026-08-19
- **Rule:** (recorded here) There is no auditor on the operator path (operator input is trusted
  evidence). Mechanical checks run in code before any judgment, and a
  mechanical failure is an ERROR item for the operator, never a question put
  to the auditor. The auditor judges from the approved episode text and the
  actual graph rows, never the extractor's summary, and is read-only before
  and after graduation. Remediation is a separate hired agent
  (graph-curator), opt-out by default-on checkbox; it never re-ingests an
  episode, and a re-check keeps the original marker with a `remediation_of`
  link.
- **Why:** A fix is never the auditor's job; it goes only through the operator's
  curation path. Every verdict, ALIGNED included, goes on the event log;
  auto-closed cases are tallied separately, never counted as agreement; and
  a sweep tick that finds nothing writes nothing. Rejected: auto-fix,
  two-phase staging ingestion, batching verifications, and re-ingestion as a
  fix because it re-rolls extraction. A verification row whose episode was
  deleted is parked as `episode_deleted` with history kept, and reworking an
  episode is a deletion plus a new proposal, never an edit in place.
- **Enforced:** discipline only — a rejection or a design position; there is nothing to enforce
- **Source:** docs/superpowers/specs/2026-08-19-graph-verification-auditor-design.md; docs/superpowers/specs/2026-08-20-graph-remediation-loop-design.md; docs/superpowers/specs/2026-10-04-graphiti-library-migration-design.md

### DL-176 — Staffing, onboarding and scaling: the human roster is graph data, capacity is read live, experts are hired only by the operator

- **Status:** active
- **Date:** 2026-08-21
- **Rule:** (recorded here) The human roster is graph data (`Person` entities, reports-to edges)
  with no parallel table; capacity is read live from Jira at decision time,
  never tracked as graph facts; assignment is one staffing agent proposing
  through the gate; the onboarding flow seeds the org chart and competency
  map as episodes. LiteLLM is mandatory (detect an existing proxy or
  provision one). The catalog is enumerated from the pack registry, never
  hard-coded. Draft-first defaults map to propose, the explicit send is its
  own gated capability, and deletions default soft.
- **Why:** Outlook COM is an optional adapter, never a dependency. Central Command is
  a client of forges, never a host (GitHub and GitLab as named instances with
  one token each). Experts are hired by the operator only, on observed
  evidence, and the "~9-10 agents" target was deleted; the YOUR TEAM
  discovery section is generated from the live roster. Expert knowledge
  lives in per-domain graph groups within the shared read set, carrying a
  steward attribution stamp and governed by visibility, not read
  restriction. The giftable target is the single-node profile; the two-node
  deployment is the operator's instance, not the profile (amended by PRD §2:
  giftable is a consequence, not a goal). The operator's name is asked in
  the cockpit, and the assistant-hosted tour records team and environment
  facts as gated episodes.
- **Enforced:** discipline only — a rejection or a design position; there is nothing to enforce
- **Source:** docs/superpowers/specs/2026-08-21-human-team-staffing-design.md; docs/superpowers/specs/2026-08-21-setup-onboarding-design.md; docs/superpowers/specs/2026-08-21-work-transition-compatibility-design.md; docs/superpowers/specs/2026-08-22-expert-team-scaling-design.md

### DL-177 — The onboarding tour is product machinery: each agent introduces itself through a real task and calibration goes through the gate

- **Status:** active
- **Date:** 2026-08-23
- **Rule:** (recorded here) The tour is an `ea.contact` kind `onboarding_tour`, re-runnable and
  independent of Claude Code. The assistant brokers the arc; each agent
  introduces itself in its own lane through a real intro task ending in
  `ask_operator`. Calibration is proposed by the intro agent and approved by
  the operator; charter-worthy items go to the coach loop and are never
  written by the intro agent. A guard test checks every toured agent holds
  the episode-propose capability before its prompt promises one.
- **Why:** Introductions are generated from charter, grants and skills, so a weak
  intro is a finding about the charter. The arc is the core five, then the
  rest on demand, and it is resumable because tour state is the set of intro
  tasks that exist. Three of the core five are non-taskable, so their intros
  route through the cockpit chat lane; making the arc purely task-based
  needs granting them `taskable`, an operator governance call. Deferred with
  triggers: a full-roster tour mode, re-tour diffing and tour analytics.
- **Enforced:** discipline only — a rejection or a design position; there is nothing to enforce
- **Source:** docs/superpowers/specs/2026-08-23-ea-hosted-team-tour-design.md

### DL-178 — The sources catalog is a Postgres table; claims carry versioned evidence pointers; staleness is a deterministic comparison

- **Status:** active
- **Date:** 2026-08-23
- **Rule:** (recorded here) The catalog is a Postgres table; Confluence is not the system of
  record. Sources are first-class governed rows (kind, scope, lookback,
  cursor, pacing, pause). Extraction happens at watcher time; the content
  hash is of the extracted text; an exact duplicate is a location link with
  no enrollment. Documents ride the existing ledger with one work item per
  version. Every proposal citation becomes a claim row with a versioned
  evidence pointer; staleness is a deterministic version comparison with no
  model call and no stored stale flag.
- **Why:** Three problems, three layers: inventory is deterministic (no LLM),
  judgment flows through the existing ledger, and publication renders views
  under the gate. Diffs carry temporality (document-stated dates win, the
  publish date is the fallback) and supersession lives in episode text,
  never Graphiti's automatic invalidation. Repair is scheduled by a
  `wiki.freshness` heartbeat and never event-triggered. Rescission is a
  catalog tag, never a deletion, and operator statements go stale only
  through a later superseding statement. Page annotations are a fixed
  template owned by the renderer and form their own action class. The
  core layers are never homelab-shaped: per-environment variance lives only
  in the watcher registry. The portability format (OKF) was deliberately
  skipped.
- **Enforced:** discipline only — a rejection or a design position; there is nothing to enforce
- **Source:** docs/superpowers/specs/2026-08-23-sources-catalog-design.md; DL-003

### DL-179 — No deployment profile ships less than another: single-node gets the sandbox and crawler, with disclosed weaker isolation

- **Status:** active
- **Date:** 2026-08-21
- **Rule:** (recorded here) No deployment profile ships less capability than another unless
  literally impossible; the single-node exclusion of sandbox and crawler was
  an undisclosed scope cut and ended. The single-node sandbox is rootless
  podman, not gVisor, and the weaker isolation is disclosed in the
  capability manifest and README. `CC_SANDBOX_BACKEND` selects kubectl
  (default) or podman, and the podman backend holds no kubeconfig.
- **Why:** Crawler parity: enabled by default on single node with a loopback host
  port, and the verifier skips it when disabled and fails when enabled and
  dead. The log console is deferred on single node (the log shipper's CRI
  input does not fit podman); `podman logs` plus the diagnose output
  substitute. The compose service for the removed Graphiti server and its
  base-image row are gone, and the stack phase removes a leftover container
  by name.
- **Enforced:** discipline only — a rejection or a design position; there is nothing to enforce
- **Source:** docs/superpowers/specs/2026-08-21-work-transition-compatibility-design.md; docs/superpowers/specs/2026-10-04-graphiti-library-migration-design.md

### DL-180 — One tool installs and updates; a running process never replaces its own code; backups track reversibility

- **Status:** active
- **Date:** 2026-08-27
- **Rule:** (recorded here) No install means fresh, same version means repair, older means upgrade,
  newer means refuse. Upgrade is stop, backup, replace, migrate, restart
  (automatic for daemons). An automatic checkpoint is taken on every update
  and a database dump before any apply, on by default, with an explicit skip
  (`CC_SKIP_DB_BACKUP`). The in-app update check is notify-only and the apply
  is performed by an external updater.
- **Why:** A running process never replaces its own code or migrates its own
  database. Non-goals kept dead: in-process self-update from the web UI, a
  wipe-and-recover update path, general multi-node topology elicitation and
  policy elicitation. The connected check is notify-only against releases,
  with an in-process one-hour cache added later (an accepted divergence).
  Rollback re-applies the previous manifests and the previous image is left
  on the nodes for that reason.
- **Enforced:** discipline only — a rejection or a design position; there is nothing to enforce
- **Source:** docs/superpowers/specs/2026-08-27-setup-update-contract.md; docs/superpowers/specs/2026-08-25-deterministic-setup.md

### DL-181 — Setup design details: the k3s driver shares the single-node phases, mirror mismatch is WARN or FAIL by host, `check` has an honest ceiling

- **Status:** active
- **Date:** 2026-09-23
- **Rule:** (recorded here) `deploy/k3s/setup.sh` runs the same phases and output protocol as
  single node, with targets and paths from the answer file, not inline
  literals. A locked-mirror digest mismatch is a WARN on a configured mirror
  and a FAIL on the public host (a mirror re-serialises manifests). The
  fetch phase returns 1 on FAIL and USERACTION is reserved for the seam the
  operator must fill. `diagnose` leads with the ledger and then the whole
  last-run log.
- **Why:** `check` cannot prove a local image build succeeds, and cannot measure the
  embedding dimension without the embedder, so the target is "no
  configuration failure", not "no failure". The Windows/Podman Desktop
  target is a design input, not a port: loopback addressing and no reliance
  on POSIX file modes for secrets. The operator name, install directory and
  feature flags are configure questions with the cockpit's first-run prompt
  as the fallback. Acceptance is execution tests on Linux and a real Windows
  laptop on the tagged tree before the tag. The original record kept k3s out
  of scope with a follow-on owed.
- **Enforced:** discipline only — a rejection or a design position; there is nothing to enforce
- **Source:** docs/superpowers/specs/2026-09-23-airgap-check-configure-setup-design.md; docs/superpowers/specs/2026-10-01-setup-ledger-selfcheck-design.md

### DL-182 — The graphiti-core migration: one LiteLLM key, failure classes without exhaustion, verification by recorded episode, benchmarked reranker

- **Status:** active
- **Date:** 2026-10-04
- **Rule:** (recorded here) The application uses one LiteLLM virtual key; the three Graphiti-only
  keys and their minting are retired. `add_episode` never calls the
  cross-encoder; only agent search does. Transient failures re-queue with
  backoff and no exhaustion; permanent failures become FAILED with the error
  parked for the operator; nothing is acknowledged and then dropped.
  Verification audits DONE jobs by their recorded episode uuid.
- **Why:** The absence deadline, the queue-depth estimate and resubmission are
  retired. The reranker decision is measured on the operator's own models
  with `scripts/graph_rerank_bench.py` because latency is one model's
  (DL-139). Release gating is the suite plus one live episode through
  approval, extraction, verification and search before the cutover tag, in
  a two-release sequence accepted by a prompt-parity test and a live episode
  on a non-production graph.
- **Enforced:** discipline only — a rejection or a design position; there is nothing to enforce
- **Source:** docs/superpowers/specs/2026-10-04-graphiti-library-migration-design.md; DL-129; DL-130
