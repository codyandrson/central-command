---
title: "Central Command — Product Requirements Document"
status: final
created: 2026-10-18
updated: 2026-10-21
---

# PRD: Central Command

## 0. Document Purpose

This is the top layer of Central Command's master documentation: the why, the who, and the capabilities the product must have, as decided by the operator. It was produced on 2026-10-18/21 by extracting and code-checking every claim in the existing documentation and reviewing each discrepancy with the operator; the rulings are in `rulings/*.md` and `.memlog.md` beside this file.

It describes the product as it is **and** the requirements decided during that review (the AB and NEW marks are defined in §4). Architecture mechanics belong to the architecture document; the reasons behind individual rules belong to the decision log; operational procedure belongs to the runbooks. Where this document and the code disagree, the code is what runs and this document is what was intended; the discrepancy is a defect in one of them, never a licence.

## 1. Vision

Central Command exists so that one person can have a team of assistants that works with them, personally and professionally. The pain it answers is keeping up: email, calendar and prioritisation, Jira task upkeep, delegation and Confluence maintenance, at home and at work alike. Those jobs are so time-consuming that, unassisted, keeping up simply does not happen.

The product's identity, in one sentence: **human-supervised, with the supervision dial in the operator's hands.** Every write is gated by default, and the operator alone decides, per capability and per agent, where the gate relaxes. Agents can only propose and read; a credentialed Executor performs writes after approval. The risk model is error, not malice: the adversary is a hallucination or a misread, and the controls exist to contain mistakes, not to restrict reach. That stance rests on stated premises: one operator, who alone holds the credentials to the systems agents touch, and a work site with no path out. It holds while the product stays single-operator; a second user would reopen it. Sending mail is a gated step (FR-27); the gate, not the absence of a tool, contains the error.

The product's origin is the prior n8n-based team, which was disciplined but opaque to steer, could be stopped only by killing it, and had no durable guidance. The founding research verdict still stands: build the control plane, adopt the runtime, buy nothing. The supervision experience is the part the operator must own; the agent loop is not.

## 2. Target User

One operator: the product's author, who is also its only user. It is built for the operator alone, with no consideration for other users; its scope may grow later, and that is a later decision.

The operator runs two deployments of the same product:

- **Personal**: a two-node home cluster, real Gmail, a real Jira and Confluence, self-hosted models on a workstation.
- **Work**: a single-node profile on a locked-down, enterprise-managed Windows workstation, Exchange mail, a Data Center Jira and Confluence, hosted models through a corporate gateway. The network is air-gapped: there is never egress, and anything needed from outside is brought in by hand.

The single-node profile exists for the work deployment. That it could be handed to a stranger with only an API key is a consequence of building it cleanly, not a goal, and stranger-onboarding polish is not prioritised.

### 2.1 Jobs To Be Done

- Keep up with email: triage, dismiss, file, draft replies, unsubscribe (Gmail only, see §5), with nothing acted on unseen.
- Manage the calendar and prioritise the day and the week, through an executive assistant that may initiate contact within a budget.
- Keep Jira true to reality: every email and document that changes a task's state is reflected; delegation is effective; hygiene is maintained.
- Maintain Confluence: pages stay current, structure follows the real process, claims the team relies on are tracked for staleness.
- Remember: a knowledge graph that grows through gated, verified proposals and stays trustworthy.
- Supervise, not operate: approve, reject, dismiss, answer questions, coach behaviour over time, and relax the gate deliberately where the risk is understood.

### 2.2 Non-Users

- The operator's human teammates. They receive the output of agents (Jira updates, comments, pages) and will not distinguish agent writes from the operator's own; that is accepted. No watermark mechanism is designed and no additional write classes are added for them; an agent can be instructed per charter to leave a watermark.
- Anyone else operating a deployment. Nothing is designed for them.

### 2.3 Key User Journeys

Downscaled: a single operator role, so the journeys are the six interaction loops the product is built around, plus the operations loop that keeps it running.

- **UJ-1 Tasking**: the operator (or the orchestrator, or another agent) puts work on an agent's board; the operator can stop or redirect a running session; the run ends in a briefing or a proposal, never a silent side effect.
- **UJ-2 Approval**: a proposal lands in the Decisions Inbox with intent, evidence, expected effect and the live state of every item it touches; the operator approves, rejects with feedback (the drafter redrafts) or dismisses (withdrawn, never resumed).
- **UJ-3 Question**: an agent asks a bounded question or opens a discussion; the operator's answer resumes the run under the charter it started with.
- **UJ-4 Supervision**: the operator reads sessions, activity and the board; long agent work becomes a task record, so no screen ever waits on it; outages show as banners with a count of waiting work.
- **UJ-5 Coaching**: a signal becomes a charter edit drafted by the coach, reviewed as a diff; versions are never overwritten.
- **UJ-6 Collaboration**: agents consult each other (advisory or drafting) and hand off tasks, visibly and within depth, cycle and fan-out bounds; never free-form messaging.
- **UJ-7 Operations**: the operator installs, updates, backs up and restores through one tool with a ledger of every step; a failed update rolls back on its own; nothing is applied without an explicit yes (FR-46, FR-48, FR-51).

## 3. Glossary

- **Proposal** — the single object an agent emits to change the world or memory: intent, actions, evidence, expected effect, optional confidence.
- **Capability** — a gated write, declared in the registry. "No undeclared surface": anything an agent can do is declared so the auditor has something to check.
- **Pack** — a bundle of tool grants. **Skill** — subject knowledge, delivered as database rows, never a permission.
- **Grant** — agent × capability (or pack), timestamped, never deleted. **Override** (new) — an operator-authored, scoped relaxation of a capability's default gate carried on a grant.
- **Executor** — the credentialed component that performs approved writes. **Gateway** — the one approval seam.
- **Auditor** — an independent judge that re-derives a decision from the raw source; never taskable or consultable; read-only.
- **Graduation** — the operator flipping one action class, for one mechanism, from human-gated to auditor-confirmed, on recorded agreement evidence; revocable; never retroactive.
- **Session** — the complete unit of agent activity; never destroyed. **Lane** — the current open conversation for an agent.
- **Episode** — a gated knowledge-graph write: one to three distilled sentences, with pedigree in its source description.
- **Steward / Curator** — the graph agents: the steward triages fact confidence; the curator remediates through gated curation, never by re-ingesting.
- **Work ledger** — one idempotent row per email or document, claimed by the dispatcher, processed by one path. (The *setup ledger* is unrelated: the installer's record of every step it took.)
- **World change / outward-facing** — a world change is any write outside Central Command's own stores (Jira, Confluence, mail, calendar; the graph counts as memory, not world). An outward-facing write is one other people receive: a sent mail, an invitation, a Jira comment or transition, a Confluence page.
- **Trust stamp** — the record on a fact or write of who authorised it: `human-approved`, `operator-direct`, an override's name, or (goal) `auditor-approved`.
- **Shadow period** — the first N runs of a newly relaxed gate (an override or a graduated class) during which writes still land as proposals for the operator; N is a setting.
- **Park** — a session, proposal or item waiting durably (on a decision, an agent, or a dependency that is DOWN) and resumed in place, never re-issued.
- **Backstop** — a limit (depth, cycles, fan-out, rounds) that exists to stop a runaway, never to pace work.
- **Fold** — a claim of coverage over related items that finishes only on approval; delays, never drops.
- **DOWN / NO** — the failure taxonomy: a dependency unavailable (retryable, never terminal) versus a dependency that answered no (loud, terminal, coachable). Unknown counts as NO.
- **Taskable / consultable** — roster flags: whether an agent accepts tasks, and whether other agents may consult it. The auditor is neither.
- **Employment models** — Executive Assistant, standing-guidance agents, direct-tasked agents.

## 4. Features

Requirements carry global ids. **AB** = as built, confirmed in review. **NEW** = decided in review, not yet built; the build list is §6.1.

### 4.1 The approval gate and trust boundary

- **FR-1 (AB)** Agents hold only propose and read tools; the credentialed Executor performs writes after approval. The boundary is the import graph: the runtime never imports the gateway, enforced by a test.
- **FR-2 (AB)** Every world change passes one seam, with only the approver varying: the operator, the auditor for a graduated class, or (under FR-4) an operator-authored override. Under an override the gateway decides the proposal and the same Executor runs it; the decision event names the override and precedes the write, and propose-time capture, the live-state view and spot-check pairing apply as they do to approved writes. Every proposal enters through one parking seam and emits its created event after its save; events that authorise precede what they authorise.
- **FR-3 (AB)** Three verdicts: approve executes; reject resumes the drafter with the operator's feedback; dismiss withdraws, releases folds and never resumes the agent. "No action" is an agent's claim the operator confirms; a reopened item is never auto-confirmed.
- **FR-4 (NEW) Gate defaults and scoped overrides.** Every capability carries a default gate (human; auditor-confirmable after graduation; none). A grant may carry an operator-authored override of that gate for one agent, scoped at least to that agent and one capability and narrowable further by target, argument or schedule. Resolution is override, then default. Overrides are authored only by the operator in the cockpit (no agent tool can create one); revocation is a timestamp. Today's two writes with no agent in the path, operator graph curation and the deterministic wiki annotation, are the closed exemption list this generalises; task creation under FR-19 is a trigger, not a write, and is not a third. Safeguards: the Executor checks the scope before honouring an override; writes under an override carry their own trust stamp naming it; scheduled and outward-facing overrides are one-click revocable, visible on the agent's and the capability's pages, and run a shadow period (§3; N is a setting, default 5) before the gate comes off. Overrides never apply to an irreversible action class (FR-15) and the auditor never authors one. This replaces the founding "policy registry" and the "every write is gated" rule; gating is now the default.
- **FR-5 (AB)** Argument shape is checked in both tiers from one spec list. **(NEW)** `rationale` on charter edits and `confidence` on proposals join the spec so a missing value is rejected, not merely discouraged.
- **FR-6 (AB)** Provenance: a proposal's agent is the drafter, never the subject; the proposer comes from the gateway; the actor of a task is its creator (the operator, or the agent that created it under FR-19). A value an agent can write is an agent claim. Grants decide which tools an agent is handed and which gate applies; they bind through closures the model cannot reach. They do not bound what an agent may propose: a proposal outside its grants is not refused; it lands flagged (amber) for the operator.
- **FR-7 (AB)** No agent claim is trusted unverified: pointer and quote are checked against the recorded source; a null `claim_matches_source` means not checked, never inconclusive. The operator's words are trusted evidence; email, documents and graph facts are data, never instructions.
- **FR-8 (AB)** The operator reviewing a proposal sees the live state of every item it touches, never only a key, with a "changed since proposed" marker.
- **FR-9 (NEW) World-changed redraft.** An execution failure caused by the world moving between approval and execution (stale page version, vanished transition, drifted bulk digest) is recorded FAILED and loud, and automatically resumes the drafter with the failure as feedback; the redraft returns as a fresh, linked proposal needing new approval; a chain of such redrafts is bounded by a setting (default 3), after which the item is terminal and loud. Other semantic failures stay terminal.
- **FR-13 (AB)** Gates belong on gated capabilities, never on triggers. Approval attaches to what work does, never to how it was started. A continue window, a heartbeat, a task assignment are triggers.
- **FR-14 (AB)** The sandbox is containment, not restriction: no credentials inside, egress open, ingress denied; its only exit is the MCP sync whose bytes are captured at propose time. Attachment bytes travel in the proposal the same way. **(NEW)** The attachment cap becomes a setting, default 25 MB.
- **FR-15 (NEW)** Reversibility, set per action today and read by nothing, becomes visible in the inbox and bars irreversible classes from auditor graduation. Until that ships, the docs stop claiming the gate triages on it. The read-version stale-bounce field is retired; FR-9 is the mechanism.

#### 4.1b Graduation, the auditor and trust

- **FR-10 (AB)** Graduation ladder: rung 0: the mechanism states a typed judgment on the record before the operator decides; rung 1: the system pairs judgments with decisions into four quadrants, per agent and per class, computed fresh, with the dangerous quadrant listed case by case; rung 2: the operator flips one class, revocable, never retroactive; auto-handled cases never count as agreement; nothing tunes a threshold on its own. Outward-facing writes (§3) never graduate to the auditor; only the operator may relax those gates, by override. Invalidations of graph facts always park for the operator. Post-graduation evidence starvation is a named blind spot: a graduated class stops producing operator decisions to pair against. **(NEW)** Operator-sampled spot checks are therefore required before any world-changing class graduates.
- **FR-11 (AB)** The auditor is read-only before and after graduation, judges from the approved text and the actual rows, never an extractor's summary; mechanical checks run in code first and a mechanical failure is an operator error item. Only an auditor CONCUR auto-executes; a CHALLENGE or an auditor error falls to the operator; auditor switches default to off or shadow.
- **FR-12 (NEW, design goal)** Facts carry a trust stamp (§3); `auditor-approved` arrives once a graduated class writes facts.

### 4.2 The team

- **FR-16 (AB)** Every hire is just an agent: a roster row (non-empty role, set only by seeds and the hire path), a governed charter that is its system prompt, a lifecycle, grants, and recorded reasons for any deviation. Templates are starting loadouts only; an agent's capability list is generated from its grants at run start, never written into charter text. Agents are hired and retired by the operator; no autonomous hire path.
- **FR-17 (AB)** Charters are versioned append-only; a revert is a new version; the charter is persisted into a session so a paused run resumes under the charter it was proposed under. An agent may run many sessions at once; a run wakes with an always-loaded core, a relevance pack and retrieval tools; a run that changes nothing answers in text; a FAILED session always records why; loop breakers (request counts, rounds) surface when hit rather than silently stopping. **(NEW)** A cockpit action "re-run under current charter" closes the old session, requeues the same inputs in a fresh session and links the two.
- **FR-18 (AB)** Coaching: triggered only by the operator; the coach reads the whole record (the operator chooses what to foreground; nothing is fenced off from the coach) and proposes the minimal change; no signal becomes a charter edit without the operator; changes are reviewed as diffs; self-coaching is ordinary; one self-nomination per closed session at most.
- **FR-19 (NEW, reverses the founding star shape, in which only the operator could delegate)** Any agent may create a task and assign it to any agent, including the orchestrator, without approval. Assignment is a trigger; the run it starts is governed like any other. Agent-created tasks carry their creator in provenance, appear on the board, and are bounded by the same depth, cycle and fan-out backstops as consults. `task.create` stops being a gated proposal. **Precondition:** the task backstops are built before the `task.create` gate comes off.
- **FR-20 (AB)** Consults are advisory (answer, propose nothing) or drafting (the specialist parks its own proposal), chosen by the asker; depth capped at two; cycles and self-consults refused; chains persisted across resume; every active agent is consultable except the auditor.
- **FR-21 (AB)** The orchestrator and the EA:
  - (a) The orchestrator orchestrates: a durable plan-assign-receive-decide loop with no propose tools of its own; it assigns work itself, as FR-19 allows any agent to; the operator's checkpoint is the plan review in (b), which is part of commissioning a project, not a gate on a capability (FR-13).
  - (b) Before round one of a multi-assignment project its plan (sub-tasks, assignees, order, what it will not do) parks for the operator to approve or revise, and every stall-triggered replan parks again.
  - (c) It may ask the operator a question that parks in the inbox, and the answer is trusted operator evidence.
  - (d) Stuck-ness, not time, is what it watches: stalls escalate to the operator, and round and fan-out limits are runaway backstops, never pacing.
  - (e) The operator never talks to agents raw: the EA is the agent built to be talked to, and it can give a team tour that records the operator's world as gated episodes; an over-budget ask degrades to a queued question and is never lost; only the EA is attention-budgeted, by design. The attention budget is a nice-to-have, not an MVP feature: if it fails to work or becomes cumbersome it is disabled, and nothing else depends on it.
- **FR-22 (AB)** Agents declare capability gaps (missing knowledge, tools or reference material, including reference material found stale against a capture-time stamp) as first-class answers and never guess past them; a "never guess X" rule needs a tool that can read X.
- **FR-23 (AB)** Skills are database rows delivered through capability loading, versioned (import is create-only), searchable only at the current version, granted per agent. Keeping agents away from files is not itself a requirement; the standing constraint is only that an agent never reaches a credential (FR-1, FR-14), so file access, if ever wanted, is given through the credential-free sandbox, never through the runtime process that holds secrets.

### 4.3 Work intake and integrations

- **FR-24 (AB)** The work ledger: one idempotent row per email or document keyed by message id (content hash when header-less), claimed by one dispatcher per database, processed by one path no route may bypass, through valves (concurrency, approval throttle, attempts). Live polling and the backlog share one queue; the backlog drains backwards from a fixed cutoff in resumable windows; dispatch is opt-in with a manual step and a visible queue status. Slow draining is a correctness requirement, not a cost one: each approval teaches the next proposal. **(NEW)** The token-budget valve is retired.
- **FR-25 (AB)** Dismissal is a decision: unconfirmed dismissals count toward the throttle; bulk dismissal is a pinned set with a digest, never a standing rule; operator-approved mail rules fold at zero model cost and are authored in the cockpit with a preview first. An unsubscribe target derives from the mailbox's own headers, never from the proposal; the Executor refuses any other URL.
- **FR-26 (AB)** Two mail providers behind one seam: the n8n façade holding Gmail OAuth, and the native Exchange client holding its own credentials, selected by configuration; the n8n façades also serve the calendar. Mail bodies reach a model only as text converted once and persisted. The Exchange client supports client certificates and a custom CA, deployment-time tenant and mailbox configuration, and search, reply, move, cancel and folder listing. The feed is text only today; inbound attachment and image handling is on the roadmap (§6.2).
- **FR-27 (NEW) Work-site safety defaults.** Mail is draft-first with send as its own gated step; deletes are recoverable; meetings are created without invitations, and sending them is a separate step; free/busy fails closed. Jira issue types are read from the instance, never a constant.
- **FR-28 (AB)** Jira is native; Confluence writes refuse a stale version; attachments and page bodies are captured at propose time; one flavour switch selects Cloud or Data Center and withholds what cannot work.
- **FR-29 (AB)** Sources are first-class governed rows; ingestion is deterministic inventory, then ledger judgment, then gated publication; the catalog is ours while Jira and Confluence remain systems of record for their own content.

### 4.4 Knowledge

- **FR-30 (AB)** The graph is written only through gated episode proposals (plus operator curation, ungated because the click is the author's); reads are in-process and ungated; an episode is a distilled claim, never pasted source; supersession lives in episode text; an episode is never edited in place. Deletes carry a propose-time preview that the Executor recomputes and refuses on mismatch; episode writes are atomic and audited; curation writes are a closed set. The ontology is fixed; search is hybrid (semantic, keyword, reranked when a reranker is configured); embeddings are never served from a cache.
- **FR-31 (AB)** Graph memory has a shared partition and gated per-agent private partitions; no provisional memory, no agent self-editing outside the gate. Facts carry when they were true, with retirement and attribution windows; the operator is never a subject of the graph.
- **FR-32 (AB)** Verification audits the mechanism, never the truth of a fact: that approved writes landed as approved and that sources still match. Truth is maintained by supersession through the gate. The graph's content is disposable during the current shake-out phase; once in long-term use, disposing becomes non-viable. (Contradiction detection is FR-50.)
- **FR-33 (NEW) Embedder integrity and change.**
  - (a) One metadata record in the graph itself declares its embedder (alias, dimensions, epoch, text recipe, three canary vectors); a graph with vectors and no record is a mismatch until the first re-embed writes one.
  - (b) A check at startup, at the ingest gate, in the curation writer, on agent reads and in the self-check fails closed on any mismatch, including a same-width model swap caught by the canaries (threshold a setting, tuned per model).
  - (c) An operator-run, command-line-only module changes the embedder in five steps (plan, stage, cutover, verify, commit or abort), resumable; each model gets its own alias; the plan reports vector and token counts for a hosted embedder; a fresh backup is required before cutover but never taken automatically; community nodes are included; commit reopens the gate, and the previous vectors are kept for seven days after commit so a rollback stays possible, then pruned.
  - (d) The first use is a one-time re-embed of the existing graph.
  - (e) A mismatch holds the graph as DOWN: reads and writes park, nothing fails open, no keyword-only fallback.
  - (f) The text recipe is name plus summary everywhere; the extractor-side fix ships in the same change as the first re-embed so no mixed-recipe vectors exist.
- **FR-34 (AB)** Embeddings never fall back to a different model; an embedder outage is a graph-write outage (under FR-40, writes park rather than fail open).
- **FR-50 (NEW, roadmap) Contradiction detection and scrub.** When an approved write adds a claim that contradicts or supersedes one the system already holds (System X on version 1.1, now 1.2), a check identifies the older claim as no longer valid and proposes the clean-up: one gated scrub proposal listing every place the stale claim still appears, across the graph, Confluence pages, tasks and catalogued sources. Detection runs on each approved write and on request; the scrub executes only through the gate. This is knowledge management, not truth-checking: the system finds the conflict and the operator adjudicates, as contradictions already must (they surface as gated proposals, never silent mutations).

### 4.5 Models

- **FR-35 (AB)** Every consumer addresses a role alias through LiteLLM; real models keep their own rows; routing and retries are LiteLLM's; Central Command builds no router; no model fallback, by decision. Capabilities and prices are measured, never guessed; absent is not zero. Limits are per model: the output ceiling is the model's own cap and admission is per backend and pool. A missing alias is an outage (DOWN), not a verdict. Speech (text-to-speech and transcription) and reranking are optional aliases; reranking is never auto-mapped to the extraction model and fails loud.
- **FR-36 (AB, reworded)** Model placement is the operator's choice expressed in aliases and per-agent pins; the personal deployment defaults to self-hosted; nothing in the product assumes where a model runs.
- **FR-37 (AB)** LiteLLM is the system of record for usage and spend; keys never leave the client; agents never create credentials, register models or propose budgets. The model catalog is operator-owned: setup pauses for the operator on a fresh catalog and registration never overwrites operator edits.

### 4.6 Resilience and operations

- **FR-38 (AB)** The event log is one durable append-only record; write order is read order; emit is the only publish path; the cockpit's streams are projections of the table; the Activity screen over the event log is the audit trail, everything emits, and WARNING-and-above log records are bridged into it. A turn or close path that does not push its events makes the UI lie, so every such path pushes them.
- **FR-39 (AB)** Every failure is DOWN or NO; unknown is NO. Transient failures never produce a terminal state.
- **FR-40 (NEW) Outage doctrine.** A DOWN failure on a live read (graph, calendar, Jira) parks the session like a DOWN model call; the agent never sees "proceed without it" for an outage. Sessions, proposals, tasks and work items all retry with exponential backoff (the cap is a setting, default one hour) for as long as the outage lasts; nothing terminal from DOWN, including restart orphans. A waiting approved proposal stays visible and dismissable by the operator, and re-checks the world before it finally executes (FR-9); the double-apply risk of a half-landed write is open until façade idempotency keys land (§6.2). One banner per down dependency with a count of waiting work, a retry-now button, and a health probe before each sweep.
- **FR-41 (AB)** The heartbeat is governed schedule rows; schedules that contact the operator or the world are born disabled; internal sweeps may seed enabled and are listed; a quiet action writes nothing and errors are always material; one heartbeat per database, missed fires skipped and recorded, never replayed.
- **FR-42 (NEW) Run modes.** The deterministic model is a test-only mode, renamed; a guard refuses it in any deployment env file. The dry-run executor mode is removed; a fresh install starts live.
- **FR-43 (AB)** A failed context compaction degrades to the clip, never the raw record (a current fallback violates this and must be fixed).
- **FR-51 (AB) Backup and restore.** Every store and both encryption keys are backed up nightly, outside the repository, with retention; the backup is the environment file's only copy; a restore carries the existing bytes and the keys they were written under. Host security (disk encryption, host hardening) is assumed, not solved by the product.

### 4.7 Cockpit

- **FR-44 (AB)** The cockpit is a vendored fork; our code is the cc routes, gateway libraries and features; two channels to the API. Vendored capabilities are quarantined, never deleted without a decision; unfinished ones stay visibly under construction; inert surfaces degrade honestly; upstream self-update and install tooling is never used. Updates can be staged from a file in the cockpit, with apply hidden until staged. The file browser stays behind a flag, rooted in a secret-free directory with a read-only guard. Onboarding happens in the cockpit; the first-run name prompt cannot be dismissed (a terminal install asks once; until answered, agents say "the operator").
- **FR-45 (AB)** Operator input is authoritative and never disappears; socket lifetime never owns a decision's lifetime; surfaces never hide the thing they ask the operator to judge. Notification tiers are in-app only (interrupt, badge, digest; see §5).

### 4.8 Deployment

- **FR-46 (AB)** The deployment profiles:
  - (a) The single-node profile is the reference capability set: one compose file, a deterministic driver with a ledger of every step, one answer file, two trust knobs, mirror seams for every upstream, an importable zip for air-gapped installs, one tool that installs and updates, rollback a first-class verb (code only, never the database).
  - (b) Podman is the container baseline and nothing assumes Linux.
  - (c) Discovery maps a restricted network before install; published ports bind to loopback only; insecure TLS is supported but always warned about; a redacted report is the only way a finding leaves the site.
  - (d) The driver is idempotent and re-runnable; preflight mutates nothing; every install or update ends in a read-only verify; the setup agent may change the answer file and run the driver, status and report, nothing else.
  - (e) The k3s profile is the operator's instance of the single-node profile, never a superset, and must survive losing its compute node: databases stay on the anchor node and stateless services reschedule.
- **FR-47 (AB)** Secrets live only in gitignored env files; the two encryption keys never change; the public tree carries no deployment specifics (**NEW**: ten public files are relocated; instance facts in skills load from a gitignored overlay, while field names and rules the operator ruled harmless stay public). Two repositories, routed by content: what another deployment would need is public, everything else is private. A pre-push guard refuses secret shapes and an identifier list kept outside the repo, and is never weakened to get a push through.
- **FR-48 (AB)** Release and update discipline:
  - (a) A release is a changelog entry, a version bump and a tag; the installed version is what the VERSION file says; a session ends at a pushed tag.
  - (b) Releases go live only through the updater; a session never restarts a live unit or moves the live branch.
  - (c) The schema is applied to a fresh database and by the updater at release, never at process start.
  - (d) An update is operator-run on an explicit yes, carries no local patches, builds before stopping anything, waits for in-flight agents, takes a backup and a rollback point first, and rolls back on failure.

### 4.9 Documentation (this product's own)

- **FR-49 (NEW)** Master documentation is a layered set and the single source of truth: this PRD (why, who, what), the architecture document (how, as built, kept true in the same change that changes the code), a concepts glossary (§3), short runbooks beside the code they operate, and the decision log (why behind each rule, stable ids). Every other document is revised or removed on its content: when the rewrite is done no stale claim remains in either repo, and a document that survives says only what is true or points to the layer that does. Dated history (the changelog, the journal, dated design records) is exempt because it is read as a record, cited and never revised. STATUS becomes a live-state table plus an owed list, under a hundred lines in all; instance facts live only in the private repo. Drift is prevented by rule and guard: a change that invalidates a documented claim fixes the claim in the same change.

## 5. Non-Goals (Explicit)

- Other users, multi-tenancy, or any design for a stranger's deployment.
- Notifying the operator outside the cockpit (email, push).
- Automated truth-checking of graph facts: the system never decides on its own which of two claims is true. Finding contradictions and proposing the clean-up is in scope (FR-50); adjudicating them is the operator's.
- Model routing, fallback chains or classifier-driven tier selection inside Central Command (fallback is off by operator decision; reversible only by the operator).
- A policy registry as a table; Flux GitOps (deferred: auto-sync clashes with approval culture); reviving the retired Compose stack of the personal deployment.
- Free-form agent-to-agent messaging, autonomous hiring, agent self-editing of memory.
- The surveyed-not-built Exchange feature set (forward, thread retrieval, rooms, tasks, folders, inbox rules, categories, directory, distribution lists, Graph/OAuth provider abstraction, shared and delegate mailboxes, archive handling, PowerShell adapter).
- Enterprise LiteLLM features.
- Unsubscribe for Exchange mail: the work mailbox has nothing to unsubscribe from, and no Exchange unsubscribe is ever built.
- Cockpit authentication: access is limited by the private network each deployment sits on, not by the product.
- Third-party installer and air-gap tooling (Zarf, Hauler, KOTS, Ansible and the like); the product's own installer and air-gap zip (FR-46) are the replacement, not the exclusion.

## 6. Scope

What is built next, in order, and what is kept for later.

### 6.1 In scope now (the build list from this review, in order)

1. The documentation rewrite (FR-49): new layered set, every other document revised or removed on content, STATUS rewrite, decision-log corrections, DESIGN.md superseded notes. First because every later epic is specified against it.
2. Task depth, cycle and fan-out backstops; then drop the `task.create` gate (FR-19).
3. Gate defaults and scoped overrides with the three safeguards (FR-4).
4. Outage doctrine: reads park, unlimited backoff, banner, retry-now, probe, restart orphans parked, embedder DOWN parks (FR-40, FR-34).
5. World-changed redraft (FR-9).
6. Embedder integrity check and change module; text-recipe fix shipped with the first re-embed; retire the old re-embed script (FR-33).
7. Work-site safety defaults and instance-read Jira issue types (FR-27).
8. Run modes: test-only model with guard; remove dry-run (FR-42).
9. Small fixes (each traced to a ruling id in the addendum §E):
   - `rationale` and `confidence` in the argument specs (FR-5)
   - compaction fallback clips, never sends raw (FR-43)
   - attachment cap as a setting (FR-14)
   - the insecure-TLS flag read in one place, the shared HTTP client
   - exit-code helper in the update runner
   - question rows for the four typed settings the installer resolves (netrc, client certificate and key, speech-model mirror), with a schema test
   - credential-free proxy URL (netrc holds credentials, the proxy setting never does)
   - new-ref (tag and branch) scan in the pre-push guard
   - guard test pinning the dash/underscore translation of setting names in both files that do it
   - token-budget valve removal (FR-24)
   - stall-hours knob verified as wired
   - plan-review parking before round one verified as wired, built if not (FR-21b)
   - the curation writer embeds with the scoped key, not the admin key
   - meta-test for the test-model pin (FR-42)
   - application self-check runs on the k3s profile too
   - a test proves the API's write routes are unreachable from the sandbox
   - file-browser read-only guard before any enable (FR-44)
10. Relocation of instance facts and the instance-skill overlay loader (FR-47).
11. Cockpit "re-run under current charter" (FR-17); reversibility in the inbox (FR-15); macro-inventory tool and operator runbook for Confluence macro discovery.

### 6.2 Roadmap (kept, not scheduled)

- Helm umbrella chart for k3s, with the k3s ledger follow-on and single-env-file consolidation tied to it.
- Per-agent LiteLLM keys (low priority).
- Façade idempotency keys (an open double-apply risk).
- Coaching stages 5b to 5d; the cross-session reviewer.
- Auditor-approved trust stamps once a graduated class writes facts (FR-12).
- Threaded consults; full-roster tour.
- A reranker tier and benchmark.
- A versioned reference-material registry so stale references are mechanically detectable (FR-22).
- Contradiction detection and the scrub proposal (FR-50).
- Inbound mail attachment and image handling (FR-26).
- Operator-sampled spot checks, built when the first world-changing class is ready to graduate (FR-10; owner: the operator; revisit when a class's agreement quadrant is clean).

## 7. Success

Success is the operator's own statement, not a dashboard rate. The three founding scenarios plus the EA's are the definition, and all are current:

1. **Email keeps Jira and the graph in sync.** A message that changes a task's state is reflected in Jira and remembered in the graph, with nothing acted on unseen.
2. **Tasked, long-running work completes.** A compliance job given to an agent runs over days with a Confluence tracker the team can read, surviving restarts and outages, with the operator approving only the gated writes.
3. **The graph grows richer while staying trustworthy.** Every fact arrived through the gate, carries its pedigree, and is superseded rather than silently rewritten.
4. **The EA keeps the calendar and the day's priorities.** It plans the day and the week, and contacts the operator within its budget, never outside it.

Beneath those, the operator reads trends from what the system already records, with no new instrumentation and no targets: whether the inbox drains at the pace the approval throttle sets rather than stalling; whether agent-proposed Jira and Confluence changes need less editing over time; whether the dangerous quadrant stays empty as classes graduate and overrides are set.

**Counter-metrics** (the ways the product can look successful and not be): approval fatigue, visible as time-to-decide collapsing; silent success, a write that reports done without landing; operator time spent repairing agent writes.

## 8. Open Questions

None open at finalization; deferred items carry an owner and a revisit condition in §6.2 and the memlog.

## 9. Assumptions Index

- [ASSUMPTION] The six interaction loops plus operations are the right frame for journeys; no standalone personas are needed for a single operator.
- [ASSUMPTION] Stranger-onboarding effort already spent (tour, checklist) stays as is; it is not removed, only not extended.
- [ASSUMPTION] The build order in §6.1 after item 1 reflects risk and dependency; the operator may reorder.
- [ASSUMPTION] The trend indicators in §7 are read from existing tables and event kinds; no new instrumentation is required.
