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
- **Rule:** [.claude/rules/graph.md](../../.claude/rules/graph.md) — "**Graphiti's LLM client is upstream's, and `graphiti-llm` is a PLAIN `openai/<model>` alias.**"
- **Why:** v2.38.4's fix for the unbounded attribute replaced MCP 1.1.0's
  built-in entity-type models with field-less models built from config.yaml's
  one-line descriptions — and the extraction prompt's entity-types block is
  built from docstrings alone, so the per-type guidance vanished. With
  thinking off, the local model answered `{"extracted_entities": []}` for
  short episodes (0/4 with the one-liners vs 4/4 with the built-in
  docstrings, logprobs 2026-09-21). The Responses-client pin was circular (it
  preserved an alias that only existed for the old client); the stock chat
  client with a plain alias was verified through the proxy.
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
