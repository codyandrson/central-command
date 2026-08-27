# Teaming & consultation doctrine

_Spec written 2026-07-29, out of the teaming-strategy review (see the journal
entry of the same date). This is a **doctrine record**: it fixes the decided
shape of how the team collaborates and how agents interact with the operator,
against the July-2026 evidence base. Implementation is deliberately not planned
here — each mechanism gets its own plan when its turn comes._

## Why this record exists

The founding design specified `agent.request`/`agent.response` events re-waking
a YIELDED session. That was never built; what was built instead — a depth-1
star around the orchestrator plus one hardcoded consult tool — was never
written down as a decision. Meanwhile the operator's five user stories (2026-07-29)
require real inter-agent collaboration. This record replaces the unbuilt design
line and makes the built-by-accident topology a chosen one, extended where the
stories need it.

## The doctrine in one paragraph

**The team is a supervised star with governed consultation.** Coordination
(decomposition, assignment, sequencing) flows through the orchestrator and the
operator. Expertise flows through **bounded peer consults**: any agent may ask
a consultable specialist a question; the specialist spends *its* context on the
retrieval and judgment and returns a distilled answer. An agent's context is a
scarce resource; specialists exist to spend theirs so others don't have to.
Free-form peer collaboration (open-ended agent-to-agent conversation with no
arbiter) is **not** part of the doctrine — not as a prohibition of principle,
but because the operator's approval gate, the event log, and bounded consults
already deliver the value it promises without its documented failure modes.

Evidence: production retrospectives consistently report free-form peer
collaboration failing while orchestrated + bounded shapes survive; the MAST
taxonomy (NeurIPS 2025) attributes ~37% of multi-agent failures to inter-agent
misalignment; uncoordinated meshes amplified errors ~17× vs ~4.4× under central
validation. Anthropic's first-party guidance: split work **by context required,
not by problem type**, with lookup subagents returning ~50–100-token
distillations.

## Decision 1 — Consultation is the agent-as-tool pattern, generalized

`consult_jira_expert` generalizes to **`consult_agent(agent_id, question)`**:
a synchronous sub-run consult. The caller keeps control; the specialist runs
with its own governed charter and read tools, returns text, and the consult is
recorded on the event log. Consultability stays a roster fact (the existing
`consultable` column) — making an agent consultable is a one-field roster
change, exactly as designed.

Bounds are runtime-enforced, not charter-suggested:

- **Depth cap** — a consult may not open another consult beyond depth 2.
- **Cycle refusal** — A→B→A within one run chain is refused mechanically
  (the canonical production failure in delegation frameworks).
- **Budget propagation** — the consult runs on the parent's usage accumulator
  and a per-consult request limit, as `consult_jira_expert` already does.
- **Distillation** — consult answers are size-capped; the specialist's job is
  compression, not relay.

The existing bug where `agent.consulted` hardcodes `actor="agent:inbox-triage"`
(`runtime/tools.py:335`) dies with the generalization: the actor is always the
real caller.

## Decision 2 — The specialist drafts the proposal

When a consult concludes that a gated action is warranted, **the specialist
drafts and parks the proposal itself**, with the consult chain recorded in
provenance (the triggering agent/session in the proposal record, alongside the
evidence refs the consult brief carried). The asker never transcribes the
expert's advice into a proposal of its own.

Why (decided on first principles — the literature demonstrably does not cover
this tradeoff, and cross-agent attribution is an acknowledged open problem):

1. Quality lives where the knowledge lives — the Jira expert owns naming,
   linking and hygiene conventions, so it should author Jira writes.
2. The spine already supports drafter ≠ subject cleanly: a proposal's
   `agent_id` is the DRAFTER (the coach has drafted for other agents since
   stage 5a), and the Executor takes the proposer from the gateway, never from
   args.
3. **Coaching signals route correctly.** A rejected malformed Jira proposal
   must coach `jira-expert`, not the triage agent that asked the question. The
   recommendation-only alternative sends operator feedback to the agent least
   able to act on it.

## Decision 3 — The librarian is for judgment, not lookups

A librarian (knowledge) consult agent joins the roster when the knowledge
steward lands (see the knowledge-layer routing record). But **direct graph
reads stay ungated and direct**: Graphiti's hybrid search is LLM-free at read
time and fast; routing every lookup through a consult would add cost and
latency for nothing. The librarian earns its context spend on judgment-heavy
questions — multi-hop synthesis, "what do we know about X and is it current" —
where raw retrieval would flood the asker.

## Decision 4 — Agents may initiate contact with the operator, on a budget

Any agent may open a conversation with the operator — this becomes a first-class
mechanism, not a side effect of a run he triggered. Triggers start with the two
tame classes (**scheduled** and **event**) before any inferred-context
proactivity. What keeps it from becoming noise is an explicit **attention
doctrine**, decided 2026-07-29:

- **Passive capture first.** The EA work-log is built from what actually
  happened (Jira activity, calendar, approvals in this system) — the agent asks
  only about *gaps*, at natural breakpoints, roughly 2–3×/day, not hourly.
  Evidence: no production precedent for hourly polling of a human exists;
  standup tooling converged on daily cadence; CHI 2025 measured
  moderate-frequency proactivity winning (12–18% productivity, ~85%
  preference) and aggressive frequency collapsing (47%); the
  notification-budget literature puts total unsolicited agent contact at
  ~3–5/day before fatigue.
- **The dial is the operator's.** Cadence and budget are operator levers, tuned via
  agent behavior/coaching — the default errs low-frequency and he turns it up,
  never the reverse.
- **Digest is the default tier.** Anything not urgent batches into a daily
  digest; real-time interruption is reserved for the interrupt tier below.

## Decision 5 — Notification tiers in the cockpit

Three tiers, in-app only (the cockpit is open all day; no external channel,
and audible alerts are explicitly not worth effort — sound is usually off):

1. **Interrupt** — approval requests, errors, agent-initiated conversations:
   toast-class prominence.
2. **Badge** — unread-per-session badges and **aging indicators** on stale
   queue items. Queue-depth/aging alerts are load-bearing: an unreviewed queue
   silently growing is the documented death of async oversight.
3. **Digest** — everything else, batched daily.

The Decisions Inbox and operator-item queue are already the right substrate
("agent inbox" is now a named UX category and this is its shape); the tiers are
presentation over existing rows, not a new store.

## What this record does NOT change

- The trust boundary. No consult, no matter how expert, changes the world —
  writes pass the operator gate (or a graduated auditor class) regardless of
  which agent convinced which.
- The no-private-memory rule (D11). Consults share knowledge through answers
  and the shared graph, never through private channels.
- The event log as the only publication path. Every consult, initiation, and
  notification-worthy moment is an event first.
- Small-team discipline. Target roster stays ~9–10 agents; consultation does
  not license a cast of thousands. _[Superseded 2026-08-22 by the expert-team
  scaling record: the number is deleted — roster grows with the work, hired on
  observed failure, operator-originated only.]_

## Deferred, with reasons

- **Async agent-to-agent messaging** (the original `agent.request`/`agent.response`
  design line) — struck, not built. Revisit only if a real story needs an agent
  to wait on a peer across runs; bounded sync consults cover the stories today.
  _[Narrowed 2026-08-22: free-form peer messaging stays struck, but awaiting a
  peer's gated task across runs is now built (await-result task proposals) —
  see the expert-team scaling record.]_
- **Inferred-context proactivity** (the agent judging "the operator would want to know
  this now") — highest value, highest annoyance class; not until scheduled and
  event triggers have earned trust.
- **Consult-scoped capability stripping** (a consulted specialist running with
  reduced grants) — the specialist runs read-only in consults today by
  construction; revisit when a consult needs more.
