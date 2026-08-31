# Central Command — Design Compendium

The system was designed as a set of documents before code. Each is also a published
artifact (links below, owned by the operator, fetchable). **This file captures the substance
in-repo** so the design stands on its own here. Read top to bottom for the full picture.

| # | Doc | Artifact |
|---|-----|----------|
| — | Research & recommendation | https://claude.ai/code/artifact/38b6eec4-ff13-42a3-841e-e592d3ad7701 |
| 1 | OV1 — Operational concept | https://claude.ai/code/artifact/0c783e87-bef9-4cdb-ae27-6734c046431a |
| 2 | Design contracts | https://claude.ai/code/artifact/68f3504e-af25-463c-8a88-1efcb4f84b36 |
| 3 | Memory architecture | https://claude.ai/code/artifact/c006dc9f-8557-4049-a674-b2feabe2f882 |
| 4 | Ingestion & safe iteration | https://claude.ai/code/artifact/97fecaf2-efe3-4bda-9d99-a874e5fdd03c |
| 5 | Trust & reliability | https://claude.ai/code/artifact/a13a2d07-fa05-43c1-a34a-45ff560606e8 |
| 6 | Architecture | https://claude.ai/code/artifact/e051266c-7e04-4109-add1-fdd2dc86f7b1 |
| 7 | Phase-1 plan | https://claude.ai/code/artifact/7c6c4252-05fc-4bfd-82e5-768af24752cc |

> **⚠️ This compendium is frozen at 2026-07-19** — it is the *founding* design and
> has not been edited since the initial commit. It is still accurate about the
> load-bearing internals (the trust boundary, the proposal contract, memory
> layering, the gating model); it says nothing about anything designed after
> Phase 1. The design records that landed later are indexed immediately below.
> Read them alongside this file, not instead of it.

## Design records since the compendium

Written under `docs/superpowers/` as specs (the design decision) and plans (the
task-by-task implementation). Listed newest first. **Status here is the fact of
what shipped**, checked against the code on 2026-07-29 — a spec's own header
says what was true when it was written, which for the withdrawn ones is no
longer the same thing.

| date | record | status |
|---|---|---|
| 08-23 | **EA-hosted team tour — onboarding as meeting the team** — [spec](superpowers/specs/2026-08-23-ea-hosted-team-tour-design.md) | ✅ **built 2026-08-23, run live 2026-08-29/30** — the new-boss-meets-the-team onboarding phase: a re-runnable `ea.contact` kind (`onboarding_tour`, `runtime/tour.py` + `heartbeat/actions.py`) the setup skill creates as its last act; the EA brokers the arc but each agent introduces ITSELF in its own lane via the coached ask_operator(needs_discussion) pattern, generated from charter + grants + skills (a weak intro is a charter finding); calibration answers ride the gate as per-agent low-stakes proposals — deliberately, so the new operator learns propose→approve by repetition — with charter-worthy items routed to the existing coach loop; scope core five + offer the rest |
| 08-23 | **Sources, the catalog & incremental document processing** — [spec](superpowers/specs/2026-08-23-sources-catalog-design.md) | ✅ **slices 1–7 built (v2.9.0→v2.14.0, all 2026-08-30): catalog + extraction seam + filesystem & Confluence watchers, Sources panel (email feed as first row), deterministic dedupe/autofold tier, ledger enrollment on lineage thread keys, document auditing shadow-first in its own class, gated catalog.tag, and the Decision 9 claims runtime (capture at execution, computed staleness, wiki.freshness repair loop, ladder-shaped annotation; Decision 9 amended 2026-08-30 out of the OpenWiki discussion). Slice 8 (work adapters) waits for the work migration by design.** — designs the watcher layer the 2026-07-29 knowledge-layer record left unbuilt, plus the two layers around it: sources as governed data with an operator Sources panel (the email feed is its first row — retires the feed-visibility gap), a Postgres catalog as the library's system of record (Confluence pages are rendered views), extraction at watcher time with a deterministic dedupe tier (content-hash of extracted text), documents on the existing ledger with catalog lineage as the thread key, newest-first with NO depth cap (walk order is the governor), per-version diff work items carrying bi-temporal validity (stated dates win; supersession explicit in episode text, never auto-invalidation), auditor-agreement autonomy for catalog tags/dismissals, homelab-proves/work-gets-adapters (Cloud→DC, SMB→SharePoint) |
| 08-22 | **Expert-team scaling — experts, discovery, depth-2, stewardship** — [spec](superpowers/specs/2026-08-22-expert-team-scaling-design.md) | ◐ **decided + four slices built same day** — experts generalize the jira-expert pattern, operator-hired on evidence (no criteria codified, roster cap deleted); discovery = generated YOUR TEAM prompt section from the live roster (hires self-announce, no recoaching); consults to depth 2 with runtime cycle refusal; consults stay single-shot (researched — A2A-style threading is the named next step with a transcript-evidence trigger); task proposals can await-result (proposer parks `AWAITING_AGENTS`); expert knowledge stewarded in shared per-domain graph groups with steward attribution on reads |
| 08-21 | **Work-transition compatibility — Exchange via the internal automation toolset** — [spec](superpowers/specs/2026-08-21-work-transition-compatibility-design.md) · [reference](reference/work-environment-compatibility.md) | ○ **decided from the sanitized work profile** — corrects two assumptions: Exchange = adapter over the existing internal PowerShell toolset (draft-first defaults ≙ our gate; send is its own gated step; NOT an n8n/Graph façade), and work Atlassian is Server/DC (Jira 10.x, Confluence 9.x — not Cloud). Off-site buildables: Jira auth-mode seam, pluggable-trust httpx factory (`CC_CA_BUNDLE`/mTLS), `CC_REGISTRY` image prefix; toolset adapter + endpoint verification deferred on-site by design |
| 08-21 | **Setup & onboarding — zero to functioning from an API key and Claude Code** — [spec](superpowers/specs/2026-08-21-setup-onboarding-design.md), revised by [deterministic-setup](superpowers/specs/2026-08-25-deterministic-setup.md) and [the setup & update contract](superpowers/specs/2026-08-27-setup-update-contract.md) | ✅ **built and revised twice since** — core landed 2026-08-21 (`deploy/single/` podman-kube-play profile, `discover-llm.sh`, charter templates rendered once at hire, the six-phase `/setup` skill with the interview before first boot); 2026-08-25 rebuilt the flow around a deterministic driver script with the agent restricted to elicitation/diagnosis/interview only; 2026-08-27 generalized that into the setup-and-update contract (v1.0.1→v1.0.7: Windows fixes, one-command install-to-demo, `update.sh`, argument-shape validation, the Settings › Updates check, launchpad); the k3s substrate's `deploy/k3s/setup.sh` runs the identical contract |
| 08-21 | **Human-team staffing — humans are graph data, staffing is an agent** — [spec](superpowers/specs/2026-08-21-human-team-staffing-design.md) | ○ **doctrine decided, not scheduled** — human roster/competency = graph queries over `Person` entities, capacity = live Jira reads, assignment = one staffing agent proposing through the normal gate; cold start seeded by the onboarding interview; waits on real team traffic at the work transition |
| 08-20 | **Graph remediation loop — a PROBLEM verdict becomes a proposed fix** — [spec](superpowers/specs/2026-08-20-graph-remediation-loop-design.md) | ✅ **built & deployed 2026-08-20** — PROBLEM note tasks the hired `graph-curator`, whose seven gated curation capabilities (create/merge/update/delete node, create/update/delete edge — deterministic Neo4j edits, never re-ingestion) ride the normal Decisions gate; each applied fix queues a `remediation_of` re-check row back into the Verify tab |
| 08-19 | **Graph verification auditor — closing the loop after `graph.add_episode`** — [spec](superpowers/specs/2026-08-19-graph-verification-auditor-design.md) · [plan](superpowers/plans/2026-08-19-graph-verification-auditor.md) | ✅ **built & deployed 2026-08-19, dormant until enabled** — tasks 1–6 done (delta read-back with measured attribution finding, worklist + executor stamping, sweep, graph-auditor, agreement record, cockpit Verify tab); Task 7 live acceptance waits on the operator enabling the flag + schedule (born disabled) — approval gates the episode text; extraction (entities/edges/temporals/invalidation) runs post-approval unreviewed, and every graph incident on record acked "committed" while wrong. Mechanical read-back checks in code + a NEW hired `graph-auditor` (read-only, judgment layer only) on the trust-tier ladder; two classes graduated separately (`graph.verify` additions first, `graph.verify_invalidation` later, spot-check built before it flips) |
| 08-17 | **Bulk dismissal — chewing the 105k archive without 105k decisions** — [spec](superpowers/specs/2026-08-17-bulk-dismissal-design.md) | ✅ **shipped** — pattern-level dismissal for the refs-only Gmail backlog so bulk mail gets one decision per pattern instead of one per message |
| 08-11 | **Activity coverage — every run and every event has a surface** — [spec](superpowers/specs/2026-08-11-activity-coverage-design.md) · [plan](superpowers/plans/2026-08-11-activity-coverage.md) | ✅ **all five slices shipped** (`web/src/features/activity/`, `cc-activity.ts`, sidebar oneshot filter last) — audit found 565 of 601 sessions reachable only through the chat sidebar's history toggle, and the durable event log, the dispatch valves and the work-item failures with no UI at all. One Activity view (Overview · Runs · Events) makes the session the unit of activity, which covers every run *by construction* |
| 08-09 | **Graph inspection & curation (the rung ladder)** — [spec](superpowers/specs/2026-08-09-graph-inspection-design.md) | ◐ **rungs 1–3 built with the spec** (browse recipe, `graph_inspect.py`, cockpit Graph panel + Graphistry three-state seam); curation writes and Graphistry integration are recorded next increments, deferred on purpose |
| 08-01 | **Self-directed learning & scoped agent memory (revises D11)** — [spec](superpowers/specs/2026-08-01-self-directed-learning-design.md) · [research notes](superpowers/research/2026-08-01-agent-memory-research-notes.md) | ✅ **built, enabled by default** (`runtime/reflection.py`, `config.reflection_enabled=True`) — per-agent graph partitions (scope visible on every episode proposal), session-close reflection (bounded graph episodes + one coaching self-nomination per session), volume governed by trust-tier graduation per scope class; cross-session reviewer as a later slice; provisional/expiry writes rejected outright |
| 07-31 | **Outage equivalence (failures cost latency, never work)** — [spec](superpowers/specs/2026-07-31-outage-equivalence-design.md) · plans: slices [1-2](superpowers/plans/2026-07-31-outage-equivalence-slices-1-2.md) / [3](superpowers/plans/2026-07-31-outage-equivalence-slice-3.md) / [4](superpowers/plans/2026-07-31-outage-equivalence-slice-4.md) / [5](superpowers/plans/2026-07-31-outage-equivalence-slice-5.md) / [6](superpowers/plans/2026-07-31-outage-equivalence-slice-6.md) | ✅ **ALL SIX SLICES BUILT 2026-07-31** — taxonomy on every error event; AWAITING_RESUME / RETRY_PENDING / attempt-refunding ledger backoff / task requeue; health-gated `retry-sweep` (seeded enabled, 300s); bounded read retries. Residuals recorded in STATUS; the acceptance test is the next real outage |
| 07-30 | **Trust-tier graduation (the ladder, generalized)** — [spec](superpowers/specs/2026-07-30-trust-tier-graduation-design.md) | ✅ **doctrine decided** — shadow → agreement record → operator-flips-a-class, extracted from the auditor/steward/D7 ladders; names the post-graduation evidence-starvation blind spot; customers in decided order (D7 auto-assign nearest) |
| 07-30 | **Discussion-lane seam repairs** — [plan](superpowers/plans/2026-07-30-discussion-lane-seam-repairs.md) | ✅ **built** — `answer_item` closes its lane, decision resumes drive a deferred conclude, `declare_gap` cites ingested documents |
| 07-30 | **Consult slice 2: specialist drafts the proposal** — [plan](superpowers/plans/2026-07-30-consult-slice2-specialist-drafts.md) | ✅ **built** — advisory sub-run holds granted propose packs; parks under the specialist's own session; `origin` provenance on the wire; decision-resume deferral guard |
| 07-30 | **EA attention loop (slice 1)** — [plan](superpowers/plans/2026-07-30-ea-attention-loop.md) | ✅ **built** — hired `ea`, `ea.contact` heartbeat action, mechanical attention budget, deterministic snapshot; all schedules seeded disabled |
| 08-03 | **MCP server creation via sandbox** — [research](superpowers/research/2026-08-03-mcp-sandbox-creation-design.md) · [slice 1 plan](superpowers/plans/2026-08-03-sandbox-slice1.md) | ✅ **shipped, now at podman-backend parity** — `sandbox/runner.py` (credential-free, `CC_SANDBOX_BACKEND=kubectl`\|`podman`), slice 1's ungated exec/read/write/copy_in with nothing produced inside reaching anywhere durable, and slice 2's one gated exit (`mcp.sync_source` — content captured at propose time, never re-read from the sandbox) |
| 07-29 | **Ledger generalization + knowledge-steward (slice 1)** — [plan](superpowers/plans/2026-07-29-ledger-generalization-steward.md) | ✅ **built & live-proven** — `work_item.kind`, handler registry, hired steward, typed confidence, citation verification, agreement report |
| 07-29 | **Notification tiers + agent-initiated conversations** — [plan](superpowers/plans/2026-07-29-notification-tiers.md) | ✅ **built** — interrupt/badge tiers, projection-diff toasts, aging chips, `openedBy`, per-session unread fix; digest tier resolved into the EA |
| 07-29 | **consult_agent generalization (slice 1)** — [plan](superpowers/plans/2026-07-29-consult-generalization.md) | ✅ **built** — pack `consult`, generic advisory sub-run, depth-1 by construction, real-caller provenance |
| 07-29 | **Teaming & consultation doctrine** — [spec](superpowers/specs/2026-07-29-teaming-consultation-doctrine-design.md) · [research notes](superpowers/research/2026-07-29-teaming-research-notes.md) | ✅ **doctrine decided; all four delivery slices built 2026-07-29/30** — supervised star + bounded consults; specialist drafts the proposal (slice 2 built 07-30); agent-initiated contact on an attention budget; notification tiers. Strikes the unbuilt `agent.request`/`agent.response` design line |
| 07-29 | **Knowledge-layer routing & the steward** — [spec](superpowers/specs/2026-07-29-knowledge-layer-routing-design.md) · [research notes](superpowers/research/2026-07-29-teaming-research-notes.md) | ✅ **doctrine decided; steward slice 1 built; hybrid Confluence capture shipped** (see the sources-catalog record above, v2.9.0→v2.14.0) — packs=may, skills=how, graph/KB=what, MCP=transport; uncertainty-triggered review (EVIDENCE mode live); contradictions as gated proposals |
| 07-27 | **Tier-3 discussion stall escalation** — [spec](superpowers/specs/2026-07-27-discussion-stall-escalation-design.md) · [plan](superpowers/plans/2026-07-27-discussion-stall-escalation.md) | ✅ **built** — but the `discussion-sweep` schedule is seeded **disabled** and has never fired, so it is dormant until enabled in the crons tab |
| 07-27 | **The `cc-default` alias seam** — [spec](superpowers/specs/2026-07-27-default-alias-seam-design.md) | ✅ **shipped** — routing logic is deferred to LiteLLM; the spine addresses one alias. `cc-smart` was deleted (it never routed) |
| 07-27 | **Stage 5 coaching experiences: hire the coach** — [spec](superpowers/specs/2026-07-27-coaching-experiences-design.md) · [plan (5a)](superpowers/plans/2026-07-27-stage-5a-coach-spine.md) | ✅ **stage 5a shipped** — `coach` is a founding roster member with a governed charter. Stages 5b–5d are deliberately not built (see STATUS.md, "Deferred with reasons") |
| 07-26 | **The Skills Library** — [spec](superpowers/specs/2026-07-26-skills-library-design.md) · [plan](superpowers/plans/2026-07-26-skills-library.md) | ✅ **live** — all 12 tasks done; skills are DB rows delivered on demand |
| 07-26 | **Quality-router migration** — [spec](superpowers/specs/2026-07-26-quality-router-migration-design.md) | ⛔ **proposal withdrawn**, superseded by the `cc-default` alias seam. Its **evidence stands** and is the reference for why neither the adaptive router nor the complexity classifier is usable |
| 07-26 | **Adaptive routing that learns** — [spec](superpowers/specs/2026-07-26-adaptive-routing-that-learns-design.md) · [plan (Units 1–2)](superpowers/plans/2026-07-26-adaptive-routing-units-1-2.md) | ◐ **partly landed** — Units 1–2 (declared tiers, costs, fallback chains in `deploy/pi/litellm/model-preferences.yaml`) are applied; Unit 3 (the feedback loop) was withdrawn by the quality-router spec |
| 07-26 | **Local model serving + embedding migration** — [spec](superpowers/specs/2026-07-26-local-serving-and-embedding-migration-design.md) | ○ **approved, not implemented** — deliberately held; unaffected by the routing reversals |

The experiment scripts behind the routing evidence are
[`docs/experiments/`](experiments/) — kept so a claim in those specs can be
re-run rather than taken on trust.

---

## 0. The vision & the build/adopt decision

**Vision:** the operator is the team lead over a team of AI agents with different
responsibilities — some under standing long-term guidance, some direct-tasked, plus
a proactive executive assistant. He supervises, steers, approves key decisions, and
coaches behavior over time, all with full control over a self-hosted system.

**Research verdict (build vs adopt):** *Build the control plane; adopt the runtime;
buy nothing.* The supervision experience is the part you must own; the agent loop is
not. **OpenClaw+Nerve** = strong UX reference but its engine has a bad security
record (don't adopt). **FleetQ** = only system with the feedback loop, but one
maintainer (mine the design). **Pydantic AI 2.0** and the **Claude Agent SDK** = the
two viable build foundations. Recurring patterns to steal (all backend-agnostic):
control-plane-as-proxy, approval inbox / proposal queue, thinker/executor credential
split, session trees, durable serializable interrupts, review-gated kanban, and
**LLM-proposed / human-approved guidance evolution** (the coaching loop).

## 1. OV1 — Operational concept

**You never talk to agents raw — a control plane sits between you and the team.**
Agents keep working when you close it; you re-attach to live work when you open it.

**Employment models (autonomy spectrum):** **Executive Assistant** (proactive chief
of staff), **standing-guidance** (a charter + triggers, acts within its lane),
**direct-tasked** (dormant until tasked). *[D1]*

**Orchestration is a ROLE, not the EA** — a dedicated Orchestrator (supervisor /
orchestrator-worker pattern) that decomposes multi-agent jobs and routes to the
best-fit agent, grounded in a capability registry, invoked per-job, and bypassable
by direct tasking. *[D7]*

**Six interaction loops:** tasking, approval, question, supervision
(steer/interrupt/follow-up — not just kill), coaching, collaboration.

**Feature areas:** roster, agent charter, tasking, live visibility, intervention,
decisions inbox, standing guidance & autonomy, feedback & coaching, memory,
collaboration, notifications, trust/audit.

**Seven wireframed screens:** Command Dashboard, Agent Workspace, Decisions Inbox,
Task Board, Agent Charter, Performance & Coaching, Governance Library.

## 2. Design contracts (the load-bearing internals)

**The Action / Proposal contract.** A `Proposal` is the single object an agent emits
to change the world/memory; agents hold only `propose_*` tools. Fields: `intent`,
`actions[]`, `evidence[]`, `expected_effect`, `status`, `decision`,
`idempotency_key`, `provenance`. `Action` = {`capability` (=Skill@ver), `arguments`,
`target_ref` (+read_version for stale-check), `reversibility`}. `Evidence` =
{`kind`, `source_ref` (pointer to the *actual* source), `locator` (re-fetch fn),
`claim`}. **Five rules:** (1) evidence is a *reference*, not the agent's summary —
the auditor re-fetches and compares (anti-hallucination); (2) the gateway owns risk,
not the agent; (3) actions are atomic; (4) stale proposals bounce (optimistic
concurrency); (5) rejection carries structured feedback back to the agent.

**Domain model & lifecycles** (the durability contract): entities Agent,
GuidanceVersion, Session (tree), Task, Proposal, Skill, Policy, KnowledgeEpisode.
State machines — **Proposal:** DRAFT→PROPOSED→UNDER_AUDIT→AWAITING_HUMAN→APPROVED→
EXECUTING→EXECUTED (branches AUTO_APPROVED / REJECTED / STALE / FAILED / EXPIRED);
**Session:** SPAWNED→RUNNING⇄PAUSED_FOR_APPROVAL/PAUSED_FOR_INPUT/YIELDED→DONE;
**Task:** INBOX→ASSIGNED→IN_PROGRESS⇄BLOCKED→REVIEW→DONE; **Guidance:**
PROPOSED→CURRENT→SUPERSEDED (revertible). `AWAITING_HUMAN` must survive restarts.

**Eventing substrate ("nervous system"):** one durable append-only **event log** =
the spine for proactivity + collaboration + audit + observability. Agents wake by
*subscribing* to event patterns: EA on a heartbeat, standing agents on in-lane
triggers, tasked agents on `task.assigned`. A wake = a Session; one active run per
agent-lane; coalesce bursts; idempotent; unattended-run contract (a wake ends in a
briefing or a Proposal, never a silent side-effect). Agent-to-agent = `agent.request`
/`agent.response` events re-waking a YIELDED session.

## 3. Memory architecture

Through-line: **reading is layered; writing is gated** — and **the graph never
ingests raw text, only distilled, verified claims.**

**Five layers** (hot/private → durable/shared): (1) Working memory (live session
context, compacted under pressure); (2) Episodic (durable per-session record = event
log projection); (3) Agent memory (the versioned charter — governed; the agent never
writes to itself); (4) Shared graph (Graphiti/Neo4j — the team's relational/temporal
understanding); (5) Shared docs (Confluence — human-readable record).
**Stance [D11]:** no free-form private long-term agent memory — durable learning goes
to the shared graph via the gate. **Revised 2026-08-01 [D11-r1]** (see the
self-directed-learning record): agents gain a *gated* private partition
(`central_command:<agent_id>`) — "no ungoverned private memory" is what the stance
always protected; the single-shared-group posture is what it drops.

**Context assembly** on wake: ALWAYS core (charter, applicable policies, task/trigger
context, skill index) + RETRIEVED relevance pack (scoped graph facts, doc chunks,
prior episodes) + ON-DEMAND retrieval tools.

**Read path:** reads are auto (only changes gated) — graph retrieval (relational +
temporal) and doc retrieval (semantic).

**Write path (the poisoning boundary):** a shared-memory write is a mutation → same
Proposal→audit→commit pipeline; the graph accepts only distilled `KnowledgeEpisode`s
whose claim was re-derived from a cited source. Facts carry a **trust level**
(human-approved / auditor-approved / proposed-unverified); low-trust can't justify
high-consequence actions. Hygiene: working-memory compaction; **temporal validity**
(supersede, don't overwrite); conflict → review. Scoping via `group_id` tenanting.

## 4. Ingestion at scale & safe iteration (Phase 2 territory)

**Principle:** draining the email backlog slowly is a *correctness* requirement, not
just cost — agents/tasks/knowledge evolve, so measured pace lets learning compound.
**Correction:** each email is a discrete work item (do NOT coalesce distinct emails).

- **Work Ledger:** durable per-email row (message-id key) = source of truth;
  UNPROCESSED→CLAIMED→PROCESSED/FOLDED; idempotent enrollment, atomic claim,
  terminal-on-committed-outcome; no skips, no dups, crash-safe.
- **Two feeds, one queue:** live (event) + backlog (resumable enrollment) → one
  dispatcher.
- **Backpressure dispatcher (PULL not push):** valves = concurrency (start=1, from
  real in-flight rows), **approval-coupled throttle** (in MVP the human is the
  bottleneck — don't outrun review), token budget, priority (live-first + guaranteed
  backlog trickle). *[D15–D16]*
- **Thread-aware batching:** claim → lock thread (one session per thread) → survey
  unprocessed siblings → agent decides fold or separate → mark terminal. *[D17]*
- **Generalizes:** the same work-ledger + dispatcher runs the SC-2 compliance sweep.

**Safe agent iteration** (deferred post-MVP; MVP keeps only guidance versioning +
revert): agent-version lifecycle DRAFT→SHADOW→CANARY→ACTIVE; **replay** past emails
against a new version and diff (regression test on real history). *[D18]*

## 5. Trust & reliability

**Principle: assume the agent is *wrong* before assuming it's *attacked*.** For a
single-operator, no-exfiltration, air-gapped-at-work profile, the primary adversary
is error (hallucination/misreads). Three zones — **unverified** inputs (email/web/
docs — data, never commands) → **fallible** LLM agents (no creds, no raw tools, can
only propose) → **trusted** control plane (gateway, auditor, executor holding creds).
**The trust boundary sits between the agent and the executor** — only a Proposal
crosses it, so unpreventable prompt-injection/hallucination is contained, not fatal.

**Defense in depth (MVP controls):** propose-only tools / credential split; the
gateway chokepoint; 100% human approval; data-not-commands boundary; least-privilege
grants; secrets in the executor only; memory write-gate + trust levels; budgets /
backpressure. **Later:** the independent auditor (re-derives claims from ground
truth; start on low-risk graph writes *[D12]*); capability-stripping by delegation
depth. **Cut** (recalibrated): sandboxing, egress allowlists, secrets vault, anomaly
detection *[D19–D21]* — revisit only if the tool surface gains exfiltration ability.
**Residual risk:** approval fatigue erodes the human backstop → the auditor is the
priority hardening; and the plausible-but-wrong proposal → evidence re-derivation.
**Standing rule:** if a send/exfiltration-capable skill is ever added, it enters at
risk-tier `never→ask` and the cut controls get re-evaluated for that class.

**The Approval Gateway** (the spine): every world/memory change funnels through one
gate. Stages: propose → policy/risk check → **audit** (independent re-verification;
MVP = you) → decide (triage, not yes/no; reversibility is first-class) → execute
(idempotent, provenance stamped). Auditor rollout = shadow mode → graduate per
action-class, revocable. The auditor is itself fallible → risk reduction, never the
sole guard for irreversible actions.

**Governed asset registries:** Agents; Skills (shared/versioned, each with a
gateway-enforced risk+approval flag); Policies (scope human/agent/both; **enforced**
[gateway checks] vs **advisory** [injected into guidance]); Knowledge (graph + docs).
**Systems of record:** Jira + Confluence stay authoritative; the framework reads +
proposes writes; internal store holds agent-operational state; deliverable tasks
mirror to Jira (gated). *[D9]*

## 6. Architecture & runtime decision

**Runtime = Pydantic AI 2.0 + Claude + DBOS *[D22, confirmed]*.** Two requirements
settled it: (1) durability is pervasive (agents park in AWAITING_HUMAN for days, must
survive restarts) → Pydantic AI native durable execution; (2) proposals *are* typed
structured objects → Pydantic AI's typed outputs + validation are home turf.
"Pydantic AI ≠ not Claude" — Anthropic is a first-class provider; it's the harness,
not a different brain. (Claude Agent SDK was the live alternative.)

**Modular monolith, 4 tiers:** ① React UI → ② FastAPI control plane (Gateway,
Executor, Dispatcher, Event log, Projections) → ③ Agent Runtime (Pydantic AI + Claude:
sessions, `propose_*` toolset, retrieval, context assembly) → ④ Stores (Postgres,
Graphiti/Neo4j via MCP, n8n tool façade via MCP/webhook). **The trust boundary is a
code boundary:** tier ③ only ever gets propose + read, never the Executor.

**Storage:** Postgres = domain model + event log + work ledger (+ DBOS later);
Neo4j/Graphiti = the graph; n8n = mutation credentials + external tool calls; raw
email bodies NOT stored (referenced + re-fetched, D13).

**Build order:** Phase 1 walking skeleton → Phase 2 ingestion at scale → Phase 3 the
team (registry, charters, employment models, governance library, orchestrator; SC-2)
→ Phase 4 reduce review load (auditor shadow→graduate; coaching loop; proactive EA).

**Key finding (M2):** the days-long approval pause durability comes from the
deferred-tool pattern + our own message-history persistence, **not** DBOS. DBOS adds
*within-run* crash recovery — deferred to when that matters. So Phase 1 shipped
without DBOS.

## 7. Phase-1 plan → DONE

The walking skeleton (see `docs/STATUS.md` for the as-built state and the M2 crux:
`CallDeferred` → persist message-history → resume in a fresh process). Acceptance met
live: real Claude proposed a Jira due-date change with evidence, human approved, the
Executor performed the real write (TASKS-12), provenance stamped, agent resumed.

## Operational scenarios (the acceptance threads, for Phase 2+)

- **SC-1 (continuous):** every email → Jira & knowledge graph kept in sync; you
  approve until the auditor exists. Success = Jira reflects reality; fail = Jira stale.
- **SC-2 (tasked, long-running):** evaluate 100% of reports for compliance, build a
  Confluence tracker + report; ask when unsure; uses shared skills + the graph.
- **SC-3 (cross-cutting):** the knowledge graph grows via gated, verified agent
  proposals — richer over time while staying trustworthy.
