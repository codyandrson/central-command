# Central Command — Design Compendium

> **FROZEN — this is the founding record.** It is the design as it stood when
> the first phase was built (founding text dated 2026-07-19), frozen on
> 2026-10-21. Nothing below is edited except superseded notes and pointers, and
> where it disagrees with the code the code wins. Each layer of the
> documentation owns its own content:
>
> - the vision, who it is for, and what it must do: [`PRD.md`](PRD.md);
> - how the system is built, as built: [`ARCHITECTURE.md`](ARCHITECTURE.md);
> - why each rule exists: [`decisions/`](decisions/README.md).
>
> A note headed **Superseded** marks a founding position that later changed and
> names the requirement or decision-log entry that replaced it; the founding
> wording stays as written above it. The dated corrections this file once
> carried inline (2026-08-01, 2026-09-20, 2026-10-04) now live in the decision
> log and are pointed to from the place they were made.

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

> The compendium holds the load-bearing internals in outline (the trust
> boundary, the proposal contract, memory layering, the gating model) and says
> nothing about anything designed after Phase 1; the notes below mark where a
> founding position has since changed. The design records that landed later
> carry their own status (see immediately below) rather than being indexed in
> this file. Read them alongside this file, not instead of it.

## Design records since the compendium

The hand-maintained table that once lived in this section was replaced by
generated, per-record status headers (decision log DL-183).

Each design record under `docs/superpowers/{specs,plans,research}/` now
carries its own `> **Status:**` / `> **As-built:**` header, right after its
title — implemented, diverged, partial, superseded, or open, with a pointer
to what actually shipped. `tests/test_design_record_headers.py` guards that
every record has one, that every As-built path exists, and that the
generated index hasn't drifted. That index is
[`docs/superpowers/README.md`](superpowers/README.md), built by
`scripts/gen_records_index.py` from those headers — regenerate it after
changing one.

For the living, current picture of the system (as opposed to the history of
how it got there), see [`ARCHITECTURE.md`](ARCHITECTURE.md); the vision and the
requirements are in [`PRD.md`](PRD.md); individual decisions are recorded under
[`decisions/`](decisions/README.md).

The experiment scripts behind the routing evidence are
[`docs/experiments/`](experiments/) — kept so a claim in those specs can be
re-run rather than taken on trust.

---

## 0. The vision & the build/adopt decision

**Vision:** lives in [`PRD.md`](PRD.md) §1 (why, for whom) and §4 (what it must do),
and nowhere else. The founding statement is not repeated here.

> **Superseded 2026-10-21.** The founding vision promised "full control over a
> self-hosted system". Where models run is now the operator's choice, expressed
> in aliases and per-agent pins (PRD FR-36; decision log DL-165).

**Research verdict (build vs adopt)** (the reasons are DL-157, DL-158 and DL-161): *Build the control plane; adopt the runtime;
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

> **Superseded 2026-10-21.** The supervised star shape behind D7 (only the
> operator delegates; the orchestrator routes) was reversed: any agent may
> create a task and assign it to any agent without approval, bounded by depth,
> cycle and fan-out backstops (PRD FR-19; DL-170). The orchestrator stays a
> role, built (`ARCHITECTURE.md` §7, D7).

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

> **Superseded 2026-10-21 (rule 4).** The `read_version` stale bounce at propose
> time is retired; the Executor's world-state checks at execution replace it
> (DL-142).

**Domain model & lifecycles** (the durability contract): entities Agent,
GuidanceVersion, Session (tree), Task, Proposal, Skill, Policy, KnowledgeEpisode.
State machines — **Proposal:** DRAFT→PROPOSED→UNDER_AUDIT→AWAITING_HUMAN→APPROVED→
EXECUTING→EXECUTED (branches AUTO_APPROVED / REJECTED / STALE / FAILED / EXPIRED);
**Session:** SPAWNED→RUNNING⇄PAUSED_FOR_APPROVAL/PAUSED_FOR_INPUT/YIELDED→DONE;
**Task:** INBOX→ASSIGNED→IN_PROGRESS⇄BLOCKED→REVIEW→DONE; **Guidance:**
PROPOSED→CURRENT→SUPERSEDED (revertible). `AWAITING_HUMAN` must survive restarts.

> **Superseded 2026-10-21.** The state names above are the founding sketch, not
> the machines that were built. Proposal: DRAFT, UNDER_AUDIT, AUTO_APPROVED,
> STALE and EXPIRED were never built. Session: the real states differ (nine,
> none of SPAWNED, PAUSED_FOR_* or YIELDED). Task: INBOX became NEW and BLOCKED
> was never built. Guidance: only CURRENT and SUPERSEDED exist; a proposed edit
> is a proposal row. Policy: there is no policy entity (DL-143). The as-built
> sets are in `ARCHITECTURE.md` §3 (Proposal, State sets).

**Eventing substrate ("nervous system"):** one durable append-only **event log** =
the spine for proactivity + collaboration + audit + observability. Agents wake by
*subscribing* to event patterns: EA on a heartbeat, standing agents on in-lane
triggers, tasked agents on `task.assigned`. A wake = a Session; one active run per
agent-lane; coalesce bursts; idempotent; unattended-run contract (a wake ends in a
briefing or a Proposal, never a silent side-effect). Agent-to-agent = `agent.request`
/`agent.response` events re-waking a YIELDED session.

> **Superseded 2026-10-21.** Event-subscription wakes and the
> `agent.request`/`agent.response` events were never built. An agent run starts
> from exactly three sources (heartbeat, ledger dispatcher, direct tasking) and
> agents collaborate by consult or task (DL-145; `ARCHITECTURE.md` §3, Wake
> sources). A wake is a session, but "one active run per agent-lane" is not
> a rule: multiple concurrent sessions per agent is the target architecture
> (`AGENTS.md`).

## 3. Memory architecture

Through-line: **reading is layered; writing is gated** — and **the graph never
ingests raw text, only distilled, verified claims.**

**Five layers** (hot/private → durable/shared): (1) Working memory (live session
context, compacted under pressure); (2) Episodic (durable per-session record = event
log projection); (3) Agent memory (the versioned charter — governed; the agent never
writes to itself); (4) Shared graph (Graphiti/Neo4j — the team's relational/temporal
understanding); (5) Shared docs (Confluence — human-readable record).
**Stance [D11]:** no free-form private long-term agent memory — durable learning goes
to the shared graph via the gate.

> **Superseded 2026-08-01 (D11-r1).** Agents gained a *gated* private partition;
> "no ungoverned private memory" is what the stance always protected, and the
> single-shared-group posture is what it dropped (DL-173; design record
> `superpowers/specs/2026-08-01-self-directed-learning-design.md`).

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

> **Superseded 2026-10-21.** (1) Trust levels: only `human-approved` and
> `operator-direct` are stamped today; `auditor-approved` is a design goal that
> arrives once a graduated class writes facts, and `proposed-unverified` is not
> a stamp (PRD §3, FR-12). (2) Hygiene: verification audits the mechanism, never
> the truth of a fact (FR-32), but contradiction detection and a gated scrub
> proposal are now in scope as roadmap; the system finds the conflict and the
> operator adjudicates (FR-50).

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
  > **Superseded 2026-10-21.** The guaranteed backlog trickle (D16) was never
  > built: priority is live-feed first, then newest first
  > (`ingest/dispatcher.py`), and bulk dismissal drains a backlog by pattern
  > (DL-152). The token-budget valve is likewise not adopted (DL-148).
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

> **Superseded 2026-10-21.** (1) Credentials: the runtime tier holds read
> credentials only and has no write path, enforced by a guard test; "no creds"
> is true of writes, not of reads (DL-103; `ARCHITECTURE.md` §1, What each tier
> holds). (2) The "no-exfiltration" premise is narrowed: mail send is a gated
> step (PRD FR-27), outward gates may be overridden by the operator (FR-4), and
> the sandbox's egress is open (FR-14); the gate, not the absence of a tool,
> contains the error (PRD §1; DL-005, DL-160).

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

> **Superseded 2026-10-21.** (1) "100% human approval" and "every change funnels
> through one gate" became a default: every capability has a default gate, a
> grant may carry an operator-authored scoped override, and an ungated write
> stamps an override instead of auto-approving (PRD FR-4; DL-002, DL-144).
> (2) The auditor graduates per action class and began with dismissals rather than
> graph writes (DL-002); capability-stripping by delegation depth has no
> counterpart in the as-built architecture.
> (3) The cut controls (D19-D21) stay cut as controls, but an agent sandbox was
> later built as containment, not restriction (DL-005, DL-160).

**The Approval Gateway** (the spine): every world/memory change funnels through one
gate. Stages: propose → policy/risk check → **audit** (independent re-verification;
MVP = you) → decide (triage, not yes/no; reversibility is first-class) → execute
(idempotent, provenance stamped). Auditor rollout = shadow mode → graduate per
action-class, revocable. The auditor is itself fallible → risk reduction, never the
sole guard for irreversible actions.

> **Superseded 2026-10-22 (reversibility triage).** Reversibility is recorded
> per Action but nothing reads it: the inbox does not triage on it and it bars
> no class from auditor graduation. Surfacing it in the inbox and barring
> irreversible classes from graduation is PRD FR-15 (not yet built).

**Governed asset registries:** Agents; Skills (shared/versioned, each with a
gateway-enforced risk+approval flag); Policies (scope human/agent/both; **enforced**
[gateway checks] vs **advisory** [injected into guidance]); Knowledge (graph + docs).
**Systems of record:** Jira + Confluence stay authoritative; the framework reads +
proposes writes; internal store holds agent-operational state; deliverable tasks
mirror to Jira (gated). *[D9]*

> **Superseded 2026-10-22 (Skills).** A skill is subject knowledge delivered as
> database rows, never a permission; the risk and approval flag lives on the
> registry's capability row (DL-166; PRD §3).

> **Superseded 2026-10-21 (Policies).** The policy registry as a governed table
> was never built; policy flags in code are advisory and the retired claim is
> replaced by gate defaults with scoped overrides (DL-143; PRD FR-4).

## 6. Architecture & runtime decision

**Runtime = Pydantic AI 2.0 + Claude + DBOS *[D22, confirmed]*.** Two requirements
settled it: (1) durability is pervasive (agents park in AWAITING_HUMAN for days, must
survive restarts) → Pydantic AI native durable execution; (2) proposals *are* typed
structured objects → Pydantic AI's typed outputs + validation are home turf.
"Pydantic AI ≠ not Claude" — Anthropic is a first-class provider; it's the harness,
not a different brain. (Claude Agent SDK was the live alternative.)

> **Superseded 2026-10-21.** DBOS was never adopted, and durability was not
> delivered by Pydantic AI's native durable execution: it is the deferred-tool
> pattern plus the project's own persisted message history (DL-146;
> `ARCHITECTURE.md` §7, D22).

**Modular monolith, 4 tiers:** ① React UI → ② FastAPI control plane (Gateway,
Executor, Dispatcher, Event log, Projections) → ③ Agent Runtime (Pydantic AI + Claude:
sessions, `propose_*` toolset, retrieval, context assembly) → ④ Stores (Postgres,
Graphiti/Neo4j via MCP, n8n tool façade via MCP/webhook). **The trust boundary is a
code boundary:** tier ③ only ever gets propose + read, never the Executor.

> **Superseded 2026-10-04.** graphiti-core runs in the API process; the MCP
> server this line named is gone (DL-129).

**Storage:** Postgres = domain model + event log + work ledger (+ DBOS later);
Neo4j/Graphiti = the graph; n8n = mutation credentials + external tool calls; raw
email bodies NOT stored (referenced + re-fetched, D13).

> **Superseded 2026-09-20.** (1) Jira is a native client (DL-184); n8n stays the
> email/calendar façade holding the Gmail OAuth credential (DL-158). (2) D13 was
> reversed: a mail body is fetched once, converted, and persisted at claim time
> (DL-044). (3) "(+ DBOS later)" above is retired with D22 (DL-146).

**Build order:** Phase 1 walking skeleton → Phase 2 ingestion at scale → Phase 3 the
team (registry, charters, employment models, governance library, orchestrator; SC-2)
→ Phase 4 reduce review load (auditor shadow→graduate; coaching loop; proactive EA).

**Key finding (M2):** recorded inline here until 2026-10-21 and now DL-146 — the
days-long approval pause durability comes from the deferred-tool pattern and our
own message-history persistence, **not** DBOS; Phase 1 shipped without it.

## 7. Phase-1 plan → DONE

The walking skeleton (see `docs/STATUS.md` for the as-built state and the M2 crux:
`CallDeferred` → persist message-history → resume in a fresh process). Acceptance met
live: real Claude proposed a Jira due-date change with evidence, human approved, the
Executor performed the real write (TASKS-12), provenance stamped, agent resumed.

> **Superseded 2026-10-21.** `docs/STATUS.md` does not exist. The as-built
> picture is `ARCHITECTURE.md`; the operator's live status is kept in the
> operator's private records, not in this repository.

## Operational scenarios (the acceptance threads, for Phase 2+)

- **SC-1 (continuous):** every email → Jira & knowledge graph kept in sync; you
  approve until the auditor exists. Success = Jira reflects reality; fail = Jira stale.
- **SC-2 (tasked, long-running):** evaluate 100% of reports for compliance, build a
  Confluence tracker + report; ask when unsure; uses shared skills + the graph.
- **SC-3 (cross-cutting):** the knowledge graph grows via gated, verified agent
  proposals — richer over time while staying trustworthy.
