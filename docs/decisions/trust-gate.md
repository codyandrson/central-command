# Trust boundary & approval gate — decisions

Governs the approval gate, the proposal contract, agent identity/provenance,
the sandbox, and the auditor's graduation ladder. See `docs/decisions/README.md`
for the entry format and how to add one.

### DL-001 — Every world-changing action passes one approval gate

- **Status:** active
- **Date:** undated
- **Rule:** [AGENTS.md](../../AGENTS.md) — "Nothing changes the world without passing an approval gate"
- **Why:** The risk model is error, not malice (AGENTS.md's own framing), so no
  world-changing capability is left to an unsupervised agent by default; the
  boundary is enforced architecturally (the import graph, DL-004) rather than
  by per-call discipline.
- **Enforced:** code structure only (`central_command/runtime/`, `central_command/gateway/`) — no single test asserts the aggregate rule; DL-004 pins the import boundary it depends on
- **Source:** AGENTS.md Policy

### DL-002 — The gate is singular, human by default; graduation is per-class and revocable

- **Status:** active
- **Date:** undated
- **Rule:** (recorded here) Every proposal that changes the world is decided
  by exactly one gate, `gateway.approve_and_execute`. The default decider is
  the human operator. An action CLASS may graduate to letting the
  independent auditor auto-confirm CONCUR verdicts only via its own named,
  revocable configuration flag — never as a side effect of another class's
  flag. Classes graduated today: `dismissal.confirm` and `work.bulk_dismiss`
  (`CC_AUDITOR_ENABLED` + `CC_AUDITOR_MODE=active`; bulk graduated
  2026-08-17), and `dismissal.confirm.document` (its own
  `CC_AUDITOR_DOCUMENT_MODE`). Graph verification additions graduate under
  `graph_auditor_mode=active`; graph invalidations are carved out and always
  park, graduating separately (DL-020). All flags default off/shadow; a
  CHALLENGE verdict or an auditor error always falls through to the human
  gate.
- **Why:** Approval fatigue erodes the human backstop, so the lowest-risk
  classes (a wrong concur costs a missed review, never a world change) can be
  handed to an independent auditor that re-derives the judgment from the raw
  source — but only on recorded agreement evidence, one class at a time, and
  revocably: "graduation is configuration, never code." A fresh install is
  fully human-gated. Doctrine: the trust-tier graduation spec.
- **Enforced:** code structure only — `central_command/gateway/auditor.py:1-50` (doctrine docstring and class constants) and `:285-340` (`approve_and_execute` call site, `approver="auditor"`); `central_command/config.py:368-420` (`auditor_enabled`/`auditor_mode`/`auditor_document_mode`/`graph_auditor_enabled`/`graph_auditor_mode`, all default off/"shadow"); test: `tests/test_graph_verification.py::test_invalidations_always_park_and_carry_their_own_class` pins the invalidation carve-out
- **Source:** code comment `central_command/gateway/auditor.py:1-50`; code comment `central_command/config.py:368-420`

### DL-003 — Some deterministic writes are gated by the operator's own click, not by propose/approve

- **Status:** active
- **Date:** undated
- **Rule:** (recorded here) Two write paths are ungated by design because
  they carry no agent judgment to review: the operator-driven graph-curation
  write-set (`central_command/api/routes.py` calls `neo4j_writer.create_node`,
  `update_node`, `delete_node`, `create_edge`, `update_edge`, `delete_edge`
  directly through a local `_curate()` helper — no proposal/Executor
  indirection, because the API tier call IS the operator's own click) and
  `wiki_annotation_mode="auto"` (a deterministic template writing a bounded
  stale-page panel through the credentialed Confluence client, with no model
  anywhere in that path).
- **Why:** The general propose-gate pattern exists to review agent JUDGMENT
  before it becomes a world change. A Cypher curation edit the operator typed
  directly, or a template with no model in the loop, has no judgment to
  review — gating it would review nothing.
  `docs/superpowers/specs/2026-08-09-graph-inspection-design.md` records this
  explicitly as a deliberate exception to the general approval-gate rule.
- **Enforced:** code structure only — `central_command/api/routes.py` (`_curate()` and its six call sites); `central_command/config.py:395-402` (`wiki_annotation_mode`, default `"off"`); `central_command/ingest/wiki_freshness.py:229`
- **Source:** `docs/superpowers/specs/2026-08-09-graph-inspection-design.md` §5-7,72,154; code comment `central_command/config.py:395-402`

### DL-004 — The trust boundary is the import graph: runtime never imports gateway

- **Status:** active
- **Date:** undated
- **Rule:** [AGENTS.md](../../AGENTS.md) — "**The trust boundary is the import graph:**"
- **Why:** Not recorded beyond the architectural framing itself: agents may
  only propose and read; only the credentialed Executor writes, so a runtime
  import of `gateway` would put write credentials in the tier an agent's own
  tool calls execute in.
- **Enforced:** test: `tests/test_governance.py::test_runtime_never_imports_the_gateway_tier`
- **Source:** docs/DESIGN.md (4-tier architecture)

### DL-005 — The agent sandbox is containment, not restriction

- **Status:** active
- **Date:** undated
- **Rule:** [AGENTS.md](../../AGENTS.md) — "**The agent sandbox is CONTAINMENT, not restriction.**"
- **Why:** Not recorded in-repo beyond the stated principle: egress is open by
  design; the real gate is CAPABILITY (every world-changing tool is a gated
  `propose_*`), never reachability, because the sandbox holds no credentials.
- **Enforced:** discipline only — no test asserts egress stays open or that a sandbox tool never becomes credentialed
- **Source:** AGENTS.md bite marks

### DL-006 — The sandbox's only exit is mcp.sync_source, and content is captured at propose time

- **Status:** active
- **Date:** undated
- **Rule:** [AGENTS.md](../../AGENTS.md) — "**The sandbox's only exit is `mcp.sync_source`, and content is captured at PROPOSE time.**"
- **Why:** An agent that could swap sandbox content after approval would make
  operator review theater — the Executor must write exactly the bytes that
  were reviewed and must never re-read the sandbox.
- **Enforced:** test: `tests/test_mcp_sync.py::test_servers_root_is_the_same_path_on_both_sides`
- **Source:** docs/superpowers/plans/2026-08-03-sandbox-slice1.md

### DL-007 — srt stays dormant in the sandbox image; bwrap-fs-only ships instead

- **Status:** active
- **Date:** 2026-08-03
- **Rule:** (recorded here) The sandbox image ships gVisor plus a deny-all
  NetworkPolicy and `bwrap --share-net` (filesystem-only isolation) instead
  of `srt`: `srt`'s network-namespace removal was tested and does not work
  under gVisor, while `bwrap --share-net` does. `srt` stays dormant as a
  residual capability until bwrap-fs-only wrapping is proven viable.
- **Why:** `docs/superpowers/plans/2026-08-03-sandbox-slice1.md` records the
  direct test result: "srt works under gVisor... TESTED 2026-08-03 — DOES
  NOT... the incompatibility is srt's netns removal specifically."
- **Enforced:** discipline only — a recorded residual risk, no test
- **Source:** docs/superpowers/plans/2026-08-03-sandbox-slice1.md

### DL-008 — A proposal's arguments arrive flattened from real Claude, nested from the demo model

- **Status:** active
- **Date:** undated
- **Rule:** [AGENTS.md](../../AGENTS.md) — "**Proposal arg shape:** real Claude *flattens*"
- **Why:** Not recorded beyond the stated mechanism: Pydantic AI hoists a
  single-model tool param for a real Claude call, while the deterministic
  demo `FunctionModel` nests the fields under `proposal` instead — always
  parse via the one tolerant seam.
- **Enforced:** code structure only — `central_command/runtime/durable.py` (`proposal_from_call()`); exercised indirectly by `tests/test_durable_spike.py::test_pause_serialize_restart_resume` and others, but no test asserts the flatten/nested duality itself
- **Source:** AGENTS.md bite marks

### DL-009 — A proposal's agent_id is the drafter, never the subject

- **Status:** active
- **Date:** undated
- **Rule:** [AGENTS.md](../../AGENTS.md) — "**A proposal's `agent_id` is the DRAFTER, never the subject.**"
- **Why:** Not recorded beyond the stated invariant: the coach drafts charter
  edits for OTHER agents, so the proposal row's attribution (session, Inbox,
  throttle count) must track who generated the draft, while the target of the
  edit lives in the action arguments the Executor reads — an actor an agent
  could write into its own arguments would be a self-authored provenance
  claim.
- **Enforced:** discipline only — no guard test located
- **Source:** AGENTS.md bite marks

### DL-010 — A proposal's argument shape is checked in both tiers from one list

- **Status:** active
- **Date:** 2026-08-29
- **Rule:** [AGENTS.md](../../AGENTS.md) — "**A proposal's argument SHAPE is checked in BOTH tiers from ONE list — `contract.ARG_SPECS`.**"
- **Why:** `central_command/contract/args.py` records the incident: "Found
  live 2026-08-29: one `graph.add_episode` intent took FOUR approvals"
  because the Executor validated one field per round (two as bare
  `KeyError`s) and nothing looked at arguments before approval.
- **Enforced:** code structure only — `central_command/contract/args.py` (`ARG_SPECS`), imported by both `runtime/tools._validate_proposal` and `gateway/executor.execute()`; test: `tests/test_proposal_args.py::test_bad_draft_is_handed_back_in_run_and_recorded` and `::test_executor_refuses_the_same_shape_before_any_action_runs` each exercise one tier, but no single test asserts cross-tier parity directly
- **Source:** code comment `central_command/contract/args.py:9-17`

### DL-011 — ARG_SPECS stays partial by design; added only after a live multi-round rejection

- **Status:** active
- **Date:** 2026-09-20
- **Rule:** (recorded here) `contract.ARG_SPECS` deliberately covers only
  SOME gated-write capabilities: a spec is added when a capability family
  produces a multi-round rejection in practice, never pre-emptively, because
  completeness-by-default would contradict that policy. As of v2.38.2
  (measured by running `central_command.gateway.capabilities.gated_write_names()`
  and `len(central_command.contract.args.ARG_SPECS)` against the worktree's
  interpreter): 16 `ARG_SPECS` entries, 89 total registered capabilities
  (`gateway.capabilities.REGISTRY`), 58 gated-write capabilities, so 42 gated
  writes currently carry no arg spec.
- **Why:** `central_command/contract/args.py` — "Only SHAPE belongs here...
  Found live 2026-08-29: one `graph.add_episode` intent took FOUR approvals."
  The operator confirmed 2026-09-20 that partial-by-design is the intended
  shape, not a gap to close.
- **Enforced:** discipline only — deliberately no completeness check
- **Source:** code comment `central_command/contract/args.py:9-17`
- **Related:** DL-010

### DL-012 — Never swap the approval gate for pydantic-ai's approval_required()

- **Status:** active
- **Date:** undated
- **Rule:** [AGENTS.md](../../AGENTS.md) — "**Never swap the approval gate for pydantic-ai's `approval_required()`.**"
- **Why:** `approval_required()`'s semantics run the tool IN THIS PROCESS once
  approved, which would put the write back in the runtime tier that may
  never hold credentials — `CallDeferred` + `propose_*` + the credentialed
  Executor keeps the write impossible from `runtime/` even if approved by
  mistake.
- **Enforced:** test: `tests/test_agent_construction_guards.py::test_nothing_uses_pydantic_ais_approval_required`
- **Source:** AGENTS.md bite marks

### DL-013 — Every propose_* tool carries max_retries=10

- **Status:** active
- **Date:** 2026-09-20
- **Rule:** [AGENTS.md](../../AGENTS.md) — "Every `propose_*` tool carries `max_retries=10`"
- **Why:** pydantic-ai's default of 1 retry fails the whole session on the
  second bad draft; the guard's own docstring records catching a live bug
  where `mcp_toolsets_for` attached `propose_mcp_tool_call` bare (original
  incident 2026-08-18; the guard itself was added 2026-09-20 in v2.38.2).
- **Enforced:** test: `tests/test_propose_max_retries.py::test_every_literal_toolset_wraps_propose_tools`; test: `tests/test_proposal_args.py::test_every_propose_tool_has_a_redraft_budget`
- **Source:** CHANGELOG v2.38.2

### DL-014 — Every proposal takes one path: runtime.proposals.park_proposal()

- **Status:** active
- **Date:** undated
- **Rule:** [AGENTS.md](../../AGENTS.md) — "**Every proposal takes one path too:**"
- **Why:** Not recorded beyond the stated invariant: a second creation path
  would let a proposal exist without pausing the run at its deferred call or
  emitting `proposal.created`.
- **Enforced:** test: `tests/test_proposal_created.py::test_only_the_park_path_creates_proposals`, `::test_no_other_module_emits_proposal_created`, `::test_every_known_creation_path_imports_the_seam`
- **Source:** AGENTS.md bite marks

### DL-015 — A proposal has three verdicts; Dismiss withdraws and never resumes the drafter

- **Status:** active
- **Date:** 2026-08-04
- **Rule:** [AGENTS.md](../../AGENTS.md) — "**A proposal has THREE verdicts.**"
- **Why:** `central_command/gateway/gateway.py:1134` (docstring of
  `dismiss_proposal`) records the origin: resuming on dismiss "turned
  'Close, don't ask again' into another proposal on the reject path"
  (2026-08-04 — predates the public CHANGELOG's 2026-08-27 start, so datable
  only from the code comment). A second, later fix at line 1141 made a
  dismissed proposal terminal rather than stuck ("three coach tasks stuck in
  REVIEW forever, 2026-08-16").
- **Enforced:** test: `tests/test_dismiss.py::test_dismiss_withdraws_closes_session_and_never_resumes_the_agent`
- **Source:** code comment `central_command/gateway/gateway.py:1134,1141`

### DL-016 — No agent claim is trusted unverified; claim_matches_source: None means NOT CHECKED

- **Status:** active
- **Date:** undated
- **Rule:** [AGENTS.md](../../AGENTS.md) — "**No agent claim is trusted unverified — on every path that makes one.**"
- **Why:** Not recorded beyond the stated invariant. The checker lives in
  `contract/`, not `gateway/`, because BOTH `runtime/` and `gateway/` need it
  and `runtime/` may never import `gateway/`.
- **Enforced:** test: `tests/test_reflection.py::test_a_nomination_quote_is_always_checked`
- **Source:** AGENTS.md bite marks

### DL-017 — Roster membership is a non-empty role column; hire it, don't upsert it

- **Status:** active
- **Date:** undated
- **Rule:** [AGENTS.md](../../AGENTS.md) — "**Roster membership = a non-empty `role` column**"
- **Why:** Not recorded beyond the stated invariant: a plain `upsert_agent`
  registration never joins the roster, so "fixing" an agent's absence by
  upserting silently leaves it off the roster.
- **Enforced:** test: `tests/test_hiring.py::test_ensure_hired_creates_a_full_roster_member_and_is_idempotent`
- **Source:** AGENTS.md bite marks

### DL-018 — Grant revocation is a timestamp, never a delete

- **Status:** active
- **Date:** undated
- **Rule:** [AGENTS.md](../../AGENTS.md) — "**Grant revocation is a timestamp, never a delete.**"
- **Why:** `agent_grant` rows keep history so the idempotent schema seeds
  cannot resurrect a revoked pack; a charter's capability list is generated
  from grants at run start, never written into charter text.
- **Enforced:** test: `tests/test_mcp_grants.py::test_effective_mcp_gates_override_wins_revoke_restores_regrant_reactivates`
- **Source:** AGENTS.md bite marks

### DL-019 — EA holds read-only mail-read, narrower than the drafting slice it was weighed against

- **Status:** active
- **Date:** 2026-09-02
- **Rule:** (recorded here) The EA's `mail-read` grant (shipped v2.21.1) is
  read-only ("the triager can read the mail it dismisses") and is a
  narrower, read-only step relative to the wider thread-reply-drafting
  capability considered in the same research thread — not an implementation
  of it.
- **Why:** CHANGELOG `2026-09-02 — v2.21.1: the triager can read the mail it
  dismisses`; the wider drafting option is recorded as still a "decision
  needed" in `docs/superpowers/research/2026-08-06-ea-widening-design.md`
  ("Slice C — thread reply drafting").
- **Enforced:** discipline only — no guard specific to the grant boundary
- **Source:** CHANGELOG v2.21.1; docs/superpowers/research/2026-08-06-ea-widening-design.md

### DL-020 — Build the spot-check before graph.verify_invalidation graduates

- **Status:** active
- **Date:** undated
- **Rule:** (recorded here) The graph-verification auditor's invalidation
  class (`graph.verify_invalidation`) must not graduate to auto-close until
  a sampled spot-check exists. List item 9 under the (unnumbered) "Decisions"
  section of `docs/superpowers/specs/2026-08-19-graph-verification-auditor-design.md`
  states the ladder's post-graduation evidence-starvation problem and defers
  the spot check "until a class's blast radius warrants it."
- **Why:** A silently-retired true fact is a compounding world-model change —
  the deferral is explicit, not an oversight; the class currently always
  parks for the operator regardless of mode (see DL-002).
- **Enforced:** discipline only — no `spot_check`/`spot-check` implementation found in `central_command/` or `tests/`; test: `tests/test_graph_verification.py::test_invalidations_always_park_and_carry_their_own_class` confirms the always-parks carve-out that stands in for the spot-check today
- **Source:** docs/superpowers/specs/2026-08-19-graph-verification-auditor-design.md (list item 9 under "## Decisions")
