# Archived decision-log entries

Entries moved out of `docs/decisions/` on 2026-11-22 because they were BOTH
enforced by discipline only AND cited nowhere outside the decision log (not in
`AGENTS.md`, a rule file, code, tests, another document or the changelog), and
no current `AGENTS.md` or rule-file bullet states their rule. Each keeps its id
and a one-line stub in its area file, so ids stay findable and ascending; the
index in `docs/decisions/README.md` lists them as `archived`.

This file is a record, not part of the live log: an entry here is not read by
anything and is not current guidance. An entry's `Archived:` line gives its
status at the time. To bring one back, move its body back over its stub and
restore that status.

<a id="dl-007"></a>

### DL-007 — srt stays dormant in the sandbox image; bwrap-fs-only ships instead

- **Status:** archived
- **Archived:** 2026-11-22, from `docs/decisions/trust-gate.md`; status before: superseded. Enforced by discipline only and cited nowhere outside the decision log.
- **Date:** 2026-08-03
- **Rule:** (recorded here) The sandbox image shipped gVisor plus a deny-all
  NetworkPolicy and `bwrap --share-net` (filesystem-only isolation) instead
  of `srt`: `srt`'s network-namespace removal was tested and does not work
  under gVisor, while `bwrap --share-net` does. `srt` stays dormant as a
  residual capability until bwrap-fs-only wrapping is proven viable.
- **Why:** `docs/superpowers/plans/2026-08-03-sandbox-slice1.md` records the
  direct test result: "srt works under gVisor... TESTED 2026-08-03 — DOES
  NOT... the incompatibility is srt's netns removal specifically." That
  finding stands. Superseded 2026-10-21 (review ruling GATE-011): the deny-all
  egress half was replaced on 2026-08-04 by DL-005 (containment, not
  restriction). Live posture today: gVisor is the pod's `runtimeClassName`;
  the namespace NetworkPolicy denies INGRESS only and allows all egress
  (`deploy/k3s/60-sandbox.yaml`); `srt` and `bubblewrap` are installed in the
  image and dormant; nothing in the code or manifests wraps a command in
  `bwrap --share-net`, so the "bwrap-fs-only ships" half was never wired.
- **Enforced:** discipline only — a recorded residual risk, no test
- **Source:** docs/superpowers/plans/2026-08-03-sandbox-slice1.md; code comment `deploy/k3s/sandbox.Dockerfile:3-10`
- **Superseded-by:** DL-005

<a id="dl-011"></a>

### DL-011 — ARG_SPECS stays partial by design; added only after a live multi-round rejection

- **Status:** archived
- **Archived:** 2026-11-22, from `docs/decisions/trust-gate.md`; status before: active. Enforced by discipline only and cited nowhere outside the decision log.
- **Date:** 2026-09-20
- **Rule:** (recorded here) `contract.ARG_SPECS` deliberately covers only
  SOME gated-write capabilities: a spec is added when a capability family
  produces a multi-round rejection in practice, never pre-emptively, because
  completeness-by-default would contradict that policy. The counts
  drift with every release, so they are dated, not current. As first measured
  (v2.38.2): 16 `ARG_SPECS` entries, 89 registered capabilities, 58
  gated-write capabilities, 42 gated writes without a spec. Re-measured
  2026-10-21 (v2.67.2, by running `central_command.gateway.capabilities.gated_write_names()`
  and `len(central_command.contract.args.ARG_SPECS)`): 58 `ARG_SPECS` entries,
  97 registered capabilities (`gateway.capabilities.REGISTRY`), 65 gated-write
  capabilities, so 7 gated writes carry no arg spec. Partial-by-design still
  holds; the spec set grew by incident, as the rule says.
- **Why:** `central_command/contract/args.py` — "Only SHAPE belongs here...
  Found live 2026-08-29: one `graph.add_episode` intent took FOUR approvals."
  The operator confirmed 2026-09-20 that partial-by-design is the intended
  shape, not a gap to close.
- **Enforced:** discipline only — deliberately no completeness check
- **Source:** code comment `central_command/contract/args.py:9-17`
- **Related:** DL-010

<a id="dl-019"></a>

### DL-019 — EA holds read-only mail-read, narrower than the drafting slice it was weighed against

- **Status:** archived
- **Archived:** 2026-11-22, from `docs/decisions/trust-gate.md`; status before: active. Enforced by discipline only and cited nowhere outside the decision log.
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

<a id="dl-020"></a>

### DL-020 — Build the spot-check before graph.verify_invalidation graduates

- **Status:** archived
- **Archived:** 2026-11-22, from `docs/decisions/trust-gate.md`; status before: active. Enforced by discipline only and cited nowhere outside the decision log.
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

<a id="dl-068"></a>

### DL-068 — The router-mode serving unit was never built; model swapping lives outside this repo

- **Status:** archived
- **Archived:** 2026-11-22, from `docs/decisions/models.md`; status before: active. Enforced by discipline only and cited nowhere outside the decision log.
- **Date:** 2026-07-26
- **Rule:** (recorded here) Unit 1 of the local-serving spec (llama-server
  router mode) is not built and is not planned. Serving one local model at a
  time is a deployment concern solved in front of llama.cpp by a
  model-swapping proxy; this repository only addresses `cc-*` aliases through
  LiteLLM (DL-073) and must not grow a dependency on either mechanism.
- **Why:** The spec reasoned for router mode over a swapping proxy; practice
  went the other way, and because every consumer addresses an alias the choice
  never touched this codebase. Recorded so nobody "finishes" Unit 1.
- **Enforced:** discipline only
- **Source:** `docs/superpowers/specs/2026-07-26-local-serving-and-embedding-migration-design.md` (see its Status header)

<a id="dl-101"></a>

### DL-101 — One name everywhere: the historical identifiers are gone

- **Status:** archived
- **Archived:** 2026-11-22, from `docs/decisions/process.md`; status before: active. Enforced by discipline only and cited nowhere outside the decision log.
- **Date:** 2026-08-29
- **Rule:** (recorded here) The system uses one consistent product/brand
  identifier throughout code, docs, and deployment artifacts; the earlier,
  historical identifiers were removed in a single breaking, by-hand release.
- **Why:** CHANGELOG `2026-08-29 — v2.0.0: one name everywhere — the
  historical identifiers are gone`: "This release is applied BY HAND, not by
  the cockpit updater" because of its breaking scope.
- **Enforced:** discipline only — a naming convention, not independently guarded
- **Source:** CHANGELOG v2.0.0

<a id="dl-102"></a>

### DL-102 — The great scrub: a whole-repo audit is itself a periodic decision

- **Status:** archived
- **Archived:** 2026-11-22, from `docs/decisions/process.md`; status before: active. Enforced by discipline only and cited nowhere outside the decision log.
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

<a id="dl-151"></a>

### DL-151 — Graphiti communities, sagas, triplet writes and bulk ingest are not adopted; no fork of graphiti-core

- **Status:** archived
- **Archived:** 2026-11-22, from `docs/decisions/process.md`; status before: active. Enforced by discipline only and cited nowhere outside the decision log.
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

<a id="dl-156"></a>

### DL-156 — Out-of-cockpit notification (email, push) is out of scope

- **Status:** archived
- **Archived:** 2026-11-22, from `docs/decisions/process.md`; status before: active. Enforced by discipline only and cited nowhere outside the decision log.
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

<a id="dl-168"></a>

### DL-168 — A discussion stall is typed by who spoke last; nothing auto-concludes a discussion

- **Status:** archived
- **Archived:** 2026-11-22, from `docs/decisions/process.md`; status before: active. Enforced by discipline only and cited nowhere outside the decision log.
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

<a id="dl-169"></a>

### DL-169 — Knowledge layers: packs permit, skills instruct, the graph holds what is true, MCP is transport

- **Status:** archived
- **Archived:** 2026-11-22, from `docs/decisions/process.md`; status before: active. Enforced by discipline only and cited nowhere outside the decision log.
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

<a id="dl-174"></a>

### DL-174 — Graph inspection, activity coverage and bulk dismissal: read-only browser, session as the unit, pinned sets

- **Status:** archived
- **Archived:** 2026-11-22, from `docs/decisions/process.md`; status before: active. Enforced by discipline only and cited nowhere outside the decision log.
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

<a id="dl-175"></a>

### DL-175 — The graph auditor is read-only and judges from the raw rows; remediation is a separate agent and never re-ingests

- **Status:** archived
- **Archived:** 2026-11-22, from `docs/decisions/process.md`; status before: active. Enforced by discipline only and cited nowhere outside the decision log.
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

<a id="dl-178"></a>

### DL-178 — The sources catalog is a Postgres table; claims carry versioned evidence pointers; staleness is a deterministic comparison

- **Status:** archived
- **Archived:** 2026-11-22, from `docs/decisions/process.md`; status before: active. Enforced by discipline only and cited nowhere outside the decision log.
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

<a id="dl-180"></a>

### DL-180 — One tool installs and updates; a running process never replaces its own code; backups track reversibility

- **Status:** archived
- **Archived:** 2026-11-22, from `docs/decisions/process.md`; status before: active. Enforced by discipline only and cited nowhere outside the decision log.
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
- **Source:** docs/superpowers/specs/2026-08-27-setup-update-contract.md; docs/archive/superpowers/specs/2026-08-25-deterministic-setup.md
