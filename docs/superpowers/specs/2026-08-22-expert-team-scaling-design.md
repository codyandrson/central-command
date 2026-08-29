# Expert team scaling

_Spec written 2026-08-22, out of the expert-agents design discussion with the operator
(this record's decisions were each explicitly ratified there). This is a
**doctrine record with an implementation slice list**: it fixes how the roster
scales into a team of domain experts, and the four mechanisms landed alongside
it. It extends the 2026-07-29 teaming-consultation doctrine and supersedes two
of its lines (named below); everything else in that record stands._

## Why this record exists

The jira-expert/confluence-expert pattern works: a practice charter, domain
packs, `consultable`+`taskable`, and dual consultation/direct-task modes. The operator
wants that pattern to generalize — experts per work system, each stewarding
deep domain knowledge that generalist agents tap by consulting rather than
guessing (the anti-Dunning-Kruger goal). The blockers were discovery (only the
orchestrator saw a live roster; everyone else knew teammates through
hand-written charter prose, so every hire meant recoaching), the depth-1
consult construction, and an undecided graph-visibility model for expert
knowledge.

## Decision 1 — Experts are the jira-expert pattern, hired by the operator on evidence

A new expert is a template instantiation, not new architecture: practice
charter, domain read/propose packs, consultable, taskable, dual-mode. Hiring
originates with the operator only — there is no autonomous hire path, so this
record deliberately codifies **no hiring criteria and no roster-composition
audit**. Agents that flounder in a domain surface it through the existing
capability-gap channel ("a hire" is already a nameable remedy); the operator judges.

**Superseded:** the 2026-07-29 record's "target roster stays ~9–10 agents"
line is deleted, not revised. It was a proxy for discovery cost (retired by
Decision 2) and for operator attention (which no written number manages). The
posture that replaces it: the roster grows with the work, in response to
observed failure, never speculatively — and that posture is advice to the
operator, not doctrine binding any agent.

## Decision 2 — Discovery is a generated index, not a directory agent

Every fresh run's system prompt carries a generated **YOUR TEAM** section:
one line per active roster agent (id, role one-liner, consultable/taskable
flags), excluding the agent itself — built from the live roster at run start
exactly as the capabilities section is built from grants, and for the same
reason: prose that mirrors data drifts; generated sections can't. A new hire
is self-announcing; **no agent is ever recoached because the roster changed.**
Hand-written charter mentions of specific experts remain as coached emphasis
about *when* to consult, never as the discovery mechanism.

Deliberately thin: the index answers "who exists, roughly for what." All
detail stays lazy — the first consult message *is* the detail fetch, because
the specialist brings its own charter, packs, and graph to the sub-run.
The `role` column is therefore load-bearing as a routing one-liner and is
guarded against paragraph-creep at hire time.

No directory agent: "who exists" is a lookup, and the 2026-07-29 record
already ruled consults are for judgment, not lookups. The judgment form of
the question ("who covers this?") is the orchestrator's job; it already gets
generated agent cards.

## Decision 3 — Consults go to depth 2, cycle-refused at runtime

The 2026-07-29 record specified a depth cap of 2; what shipped was depth-1
**by construction** (advisory toolsets stripped `consult_agent`). This lands
the written doctrine: a consulted specialist keeps `consult_agent` and may
open at most one consult of its own, with the consult chain threaded through
deps and refused mechanically at depth >2, on cycles (anyone already in the
chain), and on self-consult. The by-construction guarantee is replaced by an
explicit runtime check plus guard tests. The operator owns the revert to depth-1 if
it misbehaves in practice.

## Decision 4 — Consults stay single-shot; continuity is caller-carried

Researched 2026-08-22 (internal machinery map + external evidence sweep)
before deciding. Every production/protocol source found converges on bounded
request-response as the primitive with the *caller* threading context forward
(Anthropic's research system: fresh subagent contexts, "careful handoffs";
Google A2A: bounded tasks linked by `contextId`; our own orchestrator: its
session is long-lived, its children are always fresh). The dialogue-shaped
failure classes in MAST (derailment, conversation reset, history loss,
premature termination) are length-dependent and structurally impossible in a
single-shot exchange, and the multi-turn degradation literature measured an
average 39% quality drop moving the same tasks from single- to multi-turn.
The operator-conversation precedent does not transfer: those lanes work
because a human arbiter holds one end; an agent↔agent dialogue puts a
degrading model on both ends.

So: `consult_agent` stays one bounded round per call. Breadth and repetition
are unlimited (an agent may consult several experts, or the same expert
repeatedly, carrying the thread in its questions) — only chaining is bounded
(Decision 3).

**Designed next step, not built:** threaded consults in the A2A shape — an
optional follow-up handle resuming the specialist's persisted session, each
exchange still one bounded round, closed by the originator, hard round cap.
Build trigger: transcripts showing callers straining to re-carry long threads,
or specialists redundantly re-retrieving across a burst of related consults.
(Mechanically cheap when triggered: `durable.py` is mode-agnostic; a third
session mode `'consult'` resting like `AWAITING_OPERATOR` does it.)

## Decision 5 — Consult vs. handoff, and park-on-task-result

The coached distinction: **consult when you need an answer to continue your
own judgment; hand off a task when the work belongs in the other agent's
hands** (their domain to draft in, sizable, or needs their full loadout
rather than advisory mode). Handoffs stay gated through `task-propose` —
never peer-to-peer direct assignment, which would dissolve the star into a
mesh.

New mechanism: a task proposal may declare **await-result**. The proposer's
session parks `AWAITING_AGENTS` (the same durable wait/resume-sweep machinery
consult waits use) and resumes with the task's terminal outcome. This is also
the depth-chain escape hatch made real: an expert *assigned* work runs with
its full kit — including `consult` — so cross-domain chains continue one
governed hop at a time instead of deepening the consult stack.

**Superseded:** the 2026-07-29 "async agent-to-agent messaging — struck"
line is narrowed, not reversed: free-form peer messaging stays struck;
awaiting a peer's *gated, operator-approved task* across runs is now a real
story and is built.

## Decision 6 — Expert knowledge is stewarded, not private

Expert domain knowledge is world facts, so it lives in **per-domain graph
groups inside the shared read set** (`agent.steward_group`): every agent can
read it; the steward expert is the domain's primary writer (its ungrouped
approved writes default there). Truly private groups stay reserved for an
agent's own operating rules — unchanged.

The Dunning-Kruger failure ("I read something, so I understand it") is
addressed by visibility and coaching, never by read restriction (gates are
review, not ceiling): stewarded facts carry a **steward attribution stamp**
(`[domain: X — steward: <agent>]`) so every shallow read names the deeper
well, and the generated team section carries the boundary consideration —
*a graph read tells you what is recorded, not what it means* — phrased as a
fact for the agent's judgment, per the facts-not-directives rule. Failing to
consult across a domain boundary is thereby a coachable judgment error, not
an information gap.

## What this record does NOT change

- The star topology, the approval gate, the trust boundary, proposal
  provenance (`agent_id` is the drafter) — all unchanged.
- Consult economics: caller's usage accumulator, incremental per-consult
  request limit, distilled size-capped answers.
- The shared write group and per-agent private groups.
- Dismiss/reject/approve semantics.

## Addendum 2026-08-23 — chained consult waits

**Operator decision: the chain WAITS, nested, however long the operator
takes.** Depth 2 shipped with a hole one level deeper than the one it fixed.
When A consulted B and B consulted C, C drafting a proposal parked C's session
correctly — but the `consult_wait` deferral bubbling out of B's run hit the
outer `consult()`'s graceful-degrade branch. B's run died, everything B had
read was thrown away, A was told to "proceed without them", and C's proposal
sat in the Decisions Inbox with nobody left to receive the operator's decision.
Decision 2's whole premise ("it is not a collaboration if A carries on without
B's answer") was true one hop in and false two hops in.

The degrade path is REPLACED, not supplemented:

- `consult()` now recognises a `consult_wait` deferral from its sub-run and
  parks it as a durable MID-CHAIN session of the specialist's own — status
  `AWAITING_AGENTS`, `run_state.consult_wait` keyed on the inner specialist's
  session — then signals upward exactly like a drafted proposal does, so the
  asker parks on the mid-chain session through the machinery that already
  existed. No new status, no new table, no new sweep worklist.
- The chain unwinds inward-out on the decision: C lands → the wake hook
  (`repo.sessions_waiting_on`) resumes B with the distilled outcome fed into
  its deferred `consult_agent` call → B composes its advisory answer in TEXT →
  that text is persisted to `run_state.consult_answer` BEFORE the session lands
  → landing wakes A, which resumes with B's answer (clipped to
  `consult.ANSWER_CEILING`) as its own consult result.
- Rejection needs nothing new: C redrafts, C's session goes back to
  `AWAITING_HUMAN`, and the whole chain stays parked through the cycle —
  `_TERMINAL_SESSION` already means "finished", not "decided". Dismissal wakes
  through `close_session`, and B answers A with the no-action outcome.
- Degrading survives ONLY as the genuine fallback: if persisting the mid-chain
  session itself fails there is no durable record for anyone to wake, so the
  asker is honestly told to proceed without an answer.

**the operator explicitly accepts unbounded wait latency.** A consult chain may sit
parked for as long as the operator takes to decide, across any number of
restarts. That is the trade: a collapsed chain silently discards a
specialist's whole run and leaves a proposal nobody is waiting on, which is
strictly worse than a slow one.

**Correctness-critical side effect:** `TriageDeps.consult_chain` lived only in
memory. A resumed mid-chain specialist would have come back with `chain=()`
and sailed past `refusal_for`'s depth and cycle checks on anything it
consulted afterwards. The chain is persisted in the parked `run_state` and
restored into the deps every resume driver builds
(`orchestration._parked_chain`), guard-tested.

Unchanged: the approval gate, the trust boundary, proposal provenance (the
drafter owns the row), consult economics, and the depth-2 cap itself — which
means at most ONE mid-chain waiter exists today.
