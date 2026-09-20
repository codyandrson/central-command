# Central Command — Design Compendium

> **This is the FOUNDING design, frozen as of 2026-09-20.** A living
> `docs/ARCHITECTURE.md` is being written; where the two disagree, the code
> and `ARCHITECTURE.md` win.

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
> Phase 1. The design records that landed later carry their own status now
> (see immediately below) rather than being indexed in this file.
> Read them alongside this file, not instead of it.

## Design records since the compendium

The hand-maintained table that lived in this section through v2.38.2 was
replaced on 2026-09-20: it had drifted from the code it was meant to
summarize (two wrong cells, one missing row, found in that day's consistency
audit), because nothing tied its claims back to the 53 records or the code
they described.

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
how it got there), see `ARCHITECTURE.md`; individual decisions are recorded
under `decisions/`.

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
(`central_command_<agent_id>`) — "no ungoverned private memory" is what the stance
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
Neo4j/Graphiti = the graph; n8n = the email/calendar façade holding the Gmail
OAuth credential, Jira is a native client (`integrations/jira.py`) **(corrected
2026-09-20)**; a mail body is fetched from the provider ONCE, converted
(`ingest/mailtext.py`) and persisted in `work_item.payload["text"]` by
`ingest/ledger.py`'s `hydrate_work_item` — not re-fetched on every read, and
not left unstored as D13 originally said **(corrected 2026-09-20)**.

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
