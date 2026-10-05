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
  only through an approved `mcp.sync_source` proposal (see DL-006).
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
- **Rule:** [AGENTS.md](../../AGENTS.md) — "**`schema.sql` is auto-loaded on a FRESH database only — nothing applies it at runtime.**"
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
  docstrings — is unchanged. The image patches that the image-patch tests
  below pin are FROZEN with `deploy/pi/graphiti/` and are deleted by the next
  release, so `tests/test_graphiti_image_patches.py` is deliberately kept for
  exactly one more release and then retires with them; the tests that carry
  this rule forward are `tests/test_graph_ontology.py` and
  `tests/test_litellm_policy.py`.
- **Enforced:** test: `tests/test_graphiti_image_patches.py::test_the_entity_type_patch_keeps_the_builtin_docstring_and_drops_the_fields`, test: `tests/test_graphiti_image_patches.py::test_the_responses_client_pin_is_retired`, test: `tests/test_litellm_policy.py` and `tests/test_single_models_declaration.py` (plain prefix), script: `deploy/single/discover-llm.sh` (structured probe)
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
- **Rule:** [AGENTS.md](../../AGENTS.md) — "**graphiti-core is imported lazily, and only through `integrations/graphiti_client.py`.**"
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
