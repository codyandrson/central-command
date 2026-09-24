# Decision log

This is the INDEX and the WHY behind Central Command's durable rules. It does
not restate rule text: `AGENTS.md` and `.claude/rules/*.md` remain the
operative source — every entry here links to the exact bullet it explains and
adds the reason (incident, design tradeoff, or operator decision) that rule
text alone doesn't carry. A handful of decisions have no rule-file home at
all (an as-built deviation, a residual risk, a scope call); for those, this
log IS the record — see the `(recorded here)` convention below.

## Why this exists

A bite mark tells you WHAT not to do. It rarely says WHY, because the rule
files are meant to be read under time pressure. When the same "why" gets
asked twice — in review, in onboarding, in a postmortem — it belongs here
once, linked from both sides.

## How to add an entry

1. Pick the one area file the decision belongs to (see the areas below); a
   decision belongs to exactly one area.
2. Give it the next `DL-NNN` id in that area's sequence — ids are assigned
   ascending in this README's index order and are never reused, even if an
   entry is later retired.
3. Write the entry in the exact format below (the guard test,
   `tests/test_decision_log.py`, parses it structurally).
4. Add a row to the index table below, in `DL-NNN` order.
5. Run `pytest -q tests/test_decision_log.py` before committing.

## Areas

| area file | covers |
|-----------|--------|
| [trust-gate.md](trust-gate.md) | the approval gate, the proposal contract, agent identity/provenance, the sandbox, the auditor's graduation ladder |
| [runtime.md](runtime.md) | agent construction/resume, session lifecycle, heartbeat/retry resilience, context/window handling |
| [events-data.md](events-data.md) | the append-only event log, its publish path, the heartbeat materiality allowlist, the Postgres spine |
| [ingest-integrations.md](ingest-integrations.md) | the mail/ledger pipeline, the n8n workflow façade, the Jira client |
| [graph.md](graph.md) | Graphiti/Neo4j identity, embedding, live-write gating, temporal facts, ontology |
| [models.md](models.md) | LiteLLM registration, capability/price sourcing, alias naming, autodiscovery memory, fleet architecture |
| [deploy.md](deploy.md) | the k3s multi-node deployment, the single-node Compose profile, the release/update surface |
| [cockpit.md](cockpit.md) | the React/Hono cockpit: container queries, the RPC wire contract, live-state UI |
| [process.md](process.md) | repo/release/branch policy, schema hygiene, test-writing conventions |

## Entry format

Every entry is a level-3 heading `### DL-NNN — <short imperative title>`
followed by exactly these six bullets (two optional ones may follow):

```
### DL-017 — <short imperative title>

- **Status:** active | superseded | retired
- **Date:** YYYY-MM-DD | undated
- **Rule:** [AGENTS.md](../../AGENTS.md) — "<exact bold lead phrase of the bullet, copied verbatim>"
- **Why:** 1–3 sentences of the reason/incident, from in-repo evidence only.
- **Enforced:** test: `tests/<file>::<test_name>` | script: `<path>` | sql: `central_command/db/schema.sql` | code structure only (`<path>`) | discipline only
- **Source:** CHANGELOG vX.Y.Z / `docs/superpowers/...` record / code comment `path` — at least one
- **Supersedes:** DL-nnn   (optional)
- **Superseded-by:** DL-nnn   (optional)
```

For a rules-file link, use
`[.claude/rules/<name>.md](../../.claude/rules/<name>.md) — "<verbatim lead phrase>"`.
For a decision with NO rule text anywhere in the tree, write
`- **Rule:** (recorded here) <one or two sentence statement of the rule>` —
the log itself is that decision's only durable home.

The quoted `Rule:` phrase must be copied verbatim (whitespace/newlines
aside) from the target file — the guard test string-matches it. `Why` must
come from in-repo evidence (the rule text's own story, the CHANGELOG, a code
comment, or a design record); if the repo gives no reason, write "Not
recorded in-repo." rather than inventing one.

## Status vocabulary

- **active** — the rule is in force today.
- **superseded** — replaced by a later decision; the newer entry's
  `Supersedes:` bullet should point back here, and this entry should carry
  `Superseded-by:`.
- **retired** — no longer in force and not replaced by anything (the
  capability or the constraint it governed is gone).

## Index

| id | title | status | date | enforced (short) | area file |
|----|-------|--------|------|-------------------|-----------|
| DL-001 | Every world-changing action passes one approval gate | active | undated | code structure only | [trust-gate.md](trust-gate.md) |
| DL-002 | The gate is singular, human by default; graduation is per-class and revocable | active | undated | code structure only | [trust-gate.md](trust-gate.md) |
| DL-003 | Some deterministic writes are gated by the operator's own click, not by propose/approve | active | undated | code structure only | [trust-gate.md](trust-gate.md) |
| DL-004 | The trust boundary is the import graph: runtime never imports gateway | active | undated | test: `tests/test_governance.py::test_runtime_never_imports_the_gateway_tier` | [trust-gate.md](trust-gate.md) |
| DL-005 | The agent sandbox is containment, not restriction | active | 2026-08-04 | discipline only | [trust-gate.md](trust-gate.md) |
| DL-006 | The sandbox's only exit is mcp.sync_source, and content is captured at propose time | active | undated | test: `tests/test_mcp_sync.py::test_servers_root_is_the_same_path_on_both_sides` | [trust-gate.md](trust-gate.md) |
| DL-007 | srt stays dormant in the sandbox image; bwrap-fs-only ships instead | active | 2026-08-03 | discipline only | [trust-gate.md](trust-gate.md) |
| DL-008 | A proposal's arguments arrive flattened from real Claude, nested from the demo model | active | undated | code structure only | [trust-gate.md](trust-gate.md) |
| DL-009 | A proposal's agent_id is the drafter, never the subject | active | 2026-07-29 | test: `tests/test_executor_provenance.py::test_the_handler_is_handed_the_gateways_proposer_not_the_agents` | [trust-gate.md](trust-gate.md) |
| DL-010 | A proposal's argument shape is checked in both tiers from one list | active | 2026-08-29 | code structure only | [trust-gate.md](trust-gate.md) |
| DL-011 | ARG_SPECS stays partial by design; added only after a live multi-round rejection | active | 2026-09-20 | discipline only | [trust-gate.md](trust-gate.md) |
| DL-012 | Never swap the approval gate for pydantic-ai's approval_required() | active | undated | test: `tests/test_agent_construction_guards.py::test_nothing_uses_pydantic_ais_approval_required` | [trust-gate.md](trust-gate.md) |
| DL-013 | Every propose_* tool carries max_retries=10 | active | 2026-09-20 | test: `tests/test_propose_max_retries.py::test_every_literal_toolset_wraps_propose_tools` | [trust-gate.md](trust-gate.md) |
| DL-014 | Every proposal takes one path: runtime.proposals.park_proposal() | active | undated | test: `tests/test_proposal_created.py::test_only_the_park_path_creates_proposals` | [trust-gate.md](trust-gate.md) |
| DL-015 | A proposal has three verdicts; Dismiss withdraws and never resumes the drafter | active | 2026-08-04 | test: `tests/test_dismiss.py::test_dismiss_withdraws_closes_session_and_never_resumes_the_agent` | [trust-gate.md](trust-gate.md) |
| DL-016 | No agent claim is trusted unverified; claim_matches_source: None means NOT CHECKED | active | undated | test: `tests/test_reflection.py::test_a_nomination_quote_is_always_checked` | [trust-gate.md](trust-gate.md) |
| DL-017 | Roster membership is a non-empty role column; hire it, don't upsert it | active | 2026-07-22 | test: `tests/test_hiring.py::test_ensure_hired_creates_a_full_roster_member_and_is_idempotent` | [trust-gate.md](trust-gate.md) |
| DL-018 | Grant revocation is a timestamp, never a delete | active | undated | test: `tests/test_mcp_grants.py::test_effective_mcp_gates_override_wins_revoke_restores_regrant_reactivates` | [trust-gate.md](trust-gate.md) |
| DL-019 | EA holds read-only mail-read, narrower than the drafting slice it was weighed against | active | 2026-09-02 | discipline only | [trust-gate.md](trust-gate.md) |
| DL-020 | Build the spot-check before graph.verify_invalidation graduates | active | undated | discipline only | [trust-gate.md](trust-gate.md) |
| DL-021 | Two run modes: CC_DEMO_MODE and CC_EXECUTOR_MODE | active | 2026-07-19 | code structure only | [runtime.md](runtime.md) |
| DL-022 | A fresh run must load the charter; build_agent_for does not | active | 2026-07-29 | test: `tests/test_agent_run_contract.py::test_every_fresh_run_path_loads_a_charter` | [runtime.md](runtime.md) |
| DL-023 | system_prompt= is load-bearing; never modernise it to instructions= | active | undated | test: `tests/test_agent_construction_guards.py::test_no_agent_is_built_with_instructions` | [runtime.md](runtime.md) |
| DL-024 | Agents take deps | active | 2026-07-19 | test: `tests/test_agent_run_contract.py::test_every_agent_run_passes_deps` | [runtime.md](runtime.md) |
| DL-025 | Sessions are never destroyed; agent:<id>:main means the current lane only | active | 2026-07-23 | code structure only | [runtime.md](runtime.md) |
| DL-026 | An attachment reaches the agent as content, or the send fails — no third state | active | undated | discipline only | [runtime.md](runtime.md) |
| DL-027 | A failed=True session must record why | active | undated | test: `tests/test_session_failure_reason.py::test_every_failed_session_records_why` | [runtime.md](runtime.md) |
| DL-028 | A failure-landing guard that lives in one wrapper is not a landing | active | 2026-09-14 | discipline only | [runtime.md](runtime.md) |
| DL-029 | A shutdown hook in the FastAPI lifespan runs too late; release from SIGTERM instead | active | 2026-08-15 | discipline only | [runtime.md](runtime.md) |
| DL-030 | A resume must arm its park record before it runs, not only in the except | active | undated | code structure only | [runtime.md](runtime.md) |
| DL-031 | The orphan sweep must land the task, not just the session | active | undated | test: `tests/test_outage_requeue.py::test_the_orphan_sweep_lands_the_task_not_just_the_session` | [runtime.md](runtime.md) |
| DL-032 | A tool result is bounded by the input window, not the output cap | active | 2026-09-16 | test: `tests/test_context_overflow.py::test_tool_result_ceiling_follows_the_smallest_discovered_window` | [runtime.md](runtime.md) |
| DL-033 | A failed compaction degrades to the clip, never to the raw record | active | 2026-09-18 | discipline only | [runtime.md](runtime.md) |
| DL-034 | A status-code sniff must match a token, not a substring | active | 2026-09-18 | discipline only | [runtime.md](runtime.md) |
| DL-035 | A heartbeat action never awaits agent work; the retry sweep dispatches detached | active | 2026-09-11 | test: `tests/test_retry_sweep.py::test_the_sweep_returns_while_an_agent_lane_is_busy` | [runtime.md](runtime.md) |
| DL-036 | The event log is append-only, so write order is read order forever | active | undated | test: `tests/test_eventlog.py::test_decision_is_logged_before_execution` | [events-data.md](events-data.md) |
| DL-037 | ActionSpec.material is an explicit, non-authorising and self-recording allowlist | active | undated | code structure only | [events-data.md](events-data.md) |
| DL-038 | events.emit() is the only way to publish | active | undated | code structure only | [events-data.md](events-data.md) |
| DL-039 | asyncio.Event at module scope is loop-bound; use a flag plus a queue sentinel | active | undated | test: `tests/test_eventlog.py::test_the_close_flag_is_not_loop_bound` | [events-data.md](events-data.md) |
| DL-040 | The spine pools its database connections | active | 2026-09-13 | code structure only | [events-data.md](events-data.md) |
| DL-041 | A Jira gadget's config keys are declared by the gadget, read from its XML | active | 2026-08-10 | code structure only | [ingest-integrations.md](ingest-integrations.md) |
| DL-042 | n8n import:workflow never activates outside queue mode; the canvas is not the source of truth | active | 2026-09-12 | script: `deploy/n8n/apply-workflows.sh` | [ingest-integrations.md](ingest-integrations.md) |
| DL-043 | A mail body reaches a model through ingest/mailtext.py, and nowhere else | active | 2026-09-19 | test: `tests/test_mailtext.py::test_nothing_reads_a_mail_body_except_through_the_converter` | [ingest-integrations.md](ingest-integrations.md) |
| DL-044 | A mail body is converted once at claim time and persisted, not re-fetched on every read | active | 2026-09-19 | test: `tests/test_mailtext.py::test_nothing_reads_a_mail_body_except_through_the_converter` | [ingest-integrations.md](ingest-integrations.md) |
| DL-045 | An unsubscribe URL is derived from the mailbox, never from the proposal | active | 2026-09-12 | code structure only | [ingest-integrations.md](ingest-integrations.md) |
| DL-046 | Ledger invariants live in SQL, not Python, and are never mocked in tests | active | 2026-07-18 | test: `tests/test_ledger.py` | [ingest-integrations.md](ingest-integrations.md) |
| DL-047 | Header-less email still needs a stable key, synthesised from the content hash | active | 2026-07-18 | test: `tests/test_ledger.py::test_bare_text_gets_a_stable_synthetic_message_id` | [ingest-integrations.md](ingest-integrations.md) |
| DL-048 | Dispatch is opt-in; the drain loop never starts by surprise | active | 2026-07-18 | test: `tests/test_dispatch_one_path.py::test_app_startup_starts_no_loop_that_was_not_asked_for` | [ingest-integrations.md](ingest-integrations.md) |
| DL-049 | A fold is a claim of coverage, not an outcome; it goes terminal only on covering-approval | active | undated | test: `tests/test_dismiss.py::test_dismissing_a_covering_proposal_releases_its_folds` | [ingest-integrations.md](ingest-integrations.md) |
| DL-050 | Every email takes one path: dispatcher.process_claimed() | active | 2026-07-20 | test: `tests/test_dispatch_one_path.py::test_nothing_outside_the_dispatcher_starts_a_work_item_run` | [ingest-integrations.md](ingest-integrations.md) |
| DL-051 | A Graphiti entity and edge each store their identity twice | active | undated | discipline only | [graph.md](graph.md) |
| DL-052 | Every write re-embeds at exactly 1024 dimensions; a mis-sized vector is dropped | active | 2026-08-21 | test: `tests/test_graph_embedding_width.py::test_a_mis_sized_vector_is_dropped_not_stored` | [graph.md](graph.md) |
| DL-053 | The suite may not write to the live graph without CC_LIVE_GRAPH_TESTS=1 | active | undated | test: `tests/conftest.py::no_live_graph_writes` | [graph.md](graph.md) |
| DL-054 | An async bolt driver cached at module scope is loop-bound | active | undated | discipline only | [graph.md](graph.md) |
| DL-055 | Graphiti's extraction needs the bridged LiteLLM alias | superseded | 2026-09-19 | discipline only | [graph.md](graph.md) |
| DL-056 | Graphiti will retire a fact it merely recognises; the guard is the invalidation scope, not the model | active | undated | test: `tests/test_graph_verification.py` | [graph.md](graph.md) |
| DL-057 | graph.add_episode requires reference_time; no defaults, anywhere | active | 2026-09-19 | test: `tests/test_proposal_args.py::test_executor_normalises_the_reference_time_and_refuses_words` | [graph.md](graph.md) |
| DL-058 | Episodes name the operator; "the operator" is not a graph subject | active | 2026-09-16 | test: `tests/test_context_overflow.py::test_graph_propose_notes_tell_the_agent_to_name_the_operator` | [graph.md](graph.md) |
| DL-059 | An edge with expired_at is not necessarily a retired fact | active | 2026-09-15 | discipline only | [graph.md](graph.md) |
| DL-060 | The ontology needs somewhere for every category; declare high-priority types first | active | 2026-08-15 | discipline only | [graph.md](graph.md) |
| DL-061 | The curator can see the patient: graph.add_episode takes for_agent | active | 2026-09-01 | code structure only | [graph.md](graph.md) |
| DL-062 | An embedding is never served from memory | active | 2026-09-01 | code structure only | [graph.md](graph.md) |
| DL-063 | Models live in LiteLLM's database and belong to the operator; setup pauses for them | active | undated | script: `register-models.py` | [models.md](models.md) |
| DL-064 | A model's capabilities are measured, never guessed; an undeclared flag is not neutral | active | undated | discipline only | [models.md](models.md) |
| DL-065 | LiteLLM forbids "-" in MCP server names; the translation is duplicated on purpose | active | undated | code structure only | [models.md](models.md) |
| DL-066 | Autodiscovery has memory; only new/changed/failed ids reach the agent | active | undated | test: `tests/test_heartbeat.py::test_reconcile_only_new_changed_or_failed_reach_the_agent` | [models.md](models.md) |
| DL-067 | Local→cloud model fallback is deliberately off | active | 2026-07-31 | code structure only | [models.md](models.md) |
| DL-068 | The router-mode serving unit was never built; model swapping lives outside this repo | active | 2026-07-26 | discipline only | [models.md](models.md) |
| DL-069 | A price is catalog data, never an agent's claim | active | 2026-09-14 | test: `tests/test_catalog_pricing.py::test_add_prices_from_the_catalog_and_drops_what_the_agent_wrote` | [models.md](models.md) |
| DL-070 | Model turns are admitted, not just submitted | active | 2026-09-13 | test: `tests/test_model_concurrency.py::test_aliases_in_one_pool_share_its_single_slot` | [models.md](models.md) |
| DL-071 | A missing model alias is an outage, not a verdict | active | 2026-09-12 | test: `tests/test_failure_taxonomy.py::test_semantic_shapes` | [models.md](models.md) |
| DL-072 | The output ceiling is the model's, not the deployment's | active | 2026-09-13 | test: `tests/test_models.py::test_the_ceiling_clears_the_largest_real_charter` | [models.md](models.md) |
| DL-073 | Every consumer addresses a cc-* role alias; real models keep their own rows | active | 2026-08-30 | code structure only | [models.md](models.md) |
| DL-074 | Placement splits by state, not by service | active | undated | script: `deploy/k3s/setup.sh` | [deploy.md](deploy.md) |
| DL-075 | Every CC_* endpoint must resolve to loopback | active | undated | script: `deploy/k3s/verify.sh` | [deploy.md](deploy.md) |
| DL-076 | A floating pod needs its image on both nodes | active | undated | discipline only | [deploy.md](deploy.md) |
| DL-077 | In a unit's ExecStart, "A && B & exec C" backgrounds A and B together | active | 2026-09-18 | test: `tests/test_graph_bolt_unit.py::test_both_relays_get_the_cluster_ip` | [deploy.md](deploy.md) |
| DL-078 | File-built configmaps are refreshed by the updater, per file | active | undated | script: `deploy/k3s/make-secrets.sh` | [deploy.md](deploy.md) |
| DL-079 | Neo4j needs enableServiceLinks: false | active | undated | code structure only | [deploy.md](deploy.md) |
| DL-080 | Use the fully-qualified image ref; podman's localhost/ tag is invisible to Kubernetes | active | 2026-08-01 | script: `grep -qx` | [deploy.md](deploy.md) |
| DL-081 | ctr images ls piped into grep -q inverts under pipefail | active | undated | discipline only | [deploy.md](deploy.md) |
| DL-082 | setup.sh runs a dry check and nine deterministic phases with PASS/WARN/FAIL/USERACTION exit codes | active | 2026-09-23 | script: `deploy/single/setup.sh` | [deploy.md](deploy.md) |
| DL-083 | The single-node install acquires before it deploys, and never falls back on its own | active | undated | test: `tests/test_single_airgap_seams.py::test_images_txt_is_well_formed` | [deploy.md](deploy.md) |
| DL-084 | CC_REGISTRY became three vars: CC_REGISTRY_DOCKERIO / _GHCR / _MCR | active | 2026-08-30 | test: `tests/test_single_airgap_seams.py::test_images_txt_is_well_formed` | [deploy.md](deploy.md) |
| DL-085 | An update waits for the agents to finish | active | 2026-09-13 | test: `tests/test_update_hold.py::test_engage_waits_while_runs_are_live_then_triggers_unforced` | [deploy.md](deploy.md) |
| DL-086 | Removing a k3s manifest means adding its tombstone | active | 2026-09-01 | script: `deploy/k3s/cc-update.sh` | [deploy.md](deploy.md) |
| DL-087 | @container must sit on a parent of whatever uses @3xl: | active | 2026-08-10 | test: `web/src/features/container-query-scope.test.ts` | [cockpit.md](cockpit.md) |
| DL-088 | tsc does not delete removed sources from server-dist | active | undated | discipline only | [cockpit.md](cockpit.md) |
| DL-089 | The cockpit hand-declares its own wire interfaces; the defence is a backend wire-shape test | active | undated | test: `tests/test_cc_routes_wire.py::test_tasks_list_carries_every_field_mapTask_reads` | [cockpit.md](cockpit.md) |
| DL-090 | _COMPOSER_DISABLING is an allowlist, not a check; a new refusal code needs its own guard | active | 2026-07-29 | test: `tests/test_composer_refusal_codes.py::test_every_refusal_code_is_classified` | [cockpit.md](cockpit.md) |
| DL-091 | The cockpit believes push frames, not hope | active | undated | UNVERIFIED | [cockpit.md](cockpit.md) |
| DL-092 | Secrets live only in .env | active | undated | script: `scripts/prepush-scan.sh` | [process.md](process.md) |
| DL-093 | master is the only branch | active | undated | discipline only | [process.md](process.md) |
| DL-094 | A release is three things: a CHANGELOG entry, a VERSION bump, and a tag | active | 2026-08-30 | test: `tests/test_version_file.py::test_version_file_matches_the_newest_changelog_release` | [process.md](process.md) |
| DL-095 | Never change LITELLM_SALT_KEY or N8N_ENCRYPTION_KEY | active | undated | discipline only | [process.md](process.md) |
| DL-096 | Never edit docs/vendor/ or hand-create anything in servers/ | active | undated | script: `scripts/vendor_docs_fetch.sh` | [process.md](process.md) |
| DL-097 | A fresh database must reproduce the roster | active | undated | sql: `central_command/db/schema.sql:77,81,85,89,93,97,101,104` | [process.md](process.md) |
| DL-098 | schema.sql is auto-loaded on a fresh database only | active | undated | code structure only | [process.md](process.md) |
| DL-099 | Tests must pin demo_mode=True when they hit resolve_model() | active | undated | discipline only | [process.md](process.md) |
| DL-100 | Assert on rows your test created, never on global counts | active | undated | code structure only | [process.md](process.md) |
| DL-101 | One name everywhere: the historical identifiers are gone | active | 2026-08-29 | discipline only | [process.md](process.md) |
| DL-102 | The great scrub: a whole-repo audit is itself a periodic decision | active | 2026-08-31 | discipline only | [process.md](process.md) |
| DL-103 | The runtime's use of credentialed integrations clients is read-only | active | 2026-09-20 | test: `tests/test_runtime_integration_reads.py::test_runtime_reaches_only_read_functions_on_credentialed_clients` | [process.md](process.md) |
| DL-104 | Graphiti's LLM client is upstream's; graphiti-llm is a plain alias and the built-in docstrings are the guidance | active | 2026-09-21 | test: `tests/test_graphiti_image_patches.py::test_the_responses_client_pin_is_retired` | [process.md](process.md) |
| DL-105 | `setup.sh check` executes nothing, and it is the gate | active | 2026-09-23 | test: `tests/test_single_check_is_dry.py::test_nothing_check_can_reach_mutates_anything` | [process.md](process.md) |
| DL-106 | The LLM catalog may be declared in `.env`; register-models.py stays create-only | active | 2026-09-23 | test: `tests/test_register_models_upstream.py::test_a_row_the_operator_edited_is_never_touched` | [process.md](process.md) |
