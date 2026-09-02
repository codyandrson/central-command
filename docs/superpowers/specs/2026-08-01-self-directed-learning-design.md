# Self-directed learning & scoped agent memory — design record

_Design record written 2026-08-01, from operator direction plus two research
passes (agent-memory state of the art; self-reflection / routing / approval-at-
scale — evidence and sources in
[the research notes](../research/2026-08-01-agent-memory-research-notes.md)).
This fixes the decided shape for future implementation; nothing here is
built. It deliberately **revises D11** — see below — and that revision must not
land as a side effect of an unrelated change._

## Why this record exists

Today an agent learns durably through exactly one channel: the operator notices
something (rejects a proposal, reopens work), and the coach distills those
operator-curated signals into a charter version. Nothing fires when a session
closes; `gather_coaching_signals()` (`runtime/run.py`) reads only
human-generated events. The operator is the sole trigger for all durable
learning — which does not scale past a small roster, and makes the operator's
attention the bottleneck on the team getting better.

Separately, all graph knowledge lands in one shared partition:
`settings.graph_write_group` is the single constant `central_command`, and reads
span the same scope for every agent. Role-specific lessons (the
litellm-manager's "provider X flakes under load") have nowhere to go except
the team-wide graph, where they are noise to everyone else — or nowhere at
all. The substrate already supports partitions: `group_id` is a per-call
parameter throughout `integrations/graphiti.py`; Central Command just never passes
anything but the one constant. Zep — whose engine Graphiti is — defaults to
full per-entity isolation with sharing as the deliberate opt-in: the inverse
of our posture.

## The D11 revision

**Old stance [D11]:** no free-form private long-term agent memory — durable
learning goes to the shared graph via the gate.

**Revised stance [D11-r1]:** no *ungoverned* private agent memory. Agents get
a private graph partition (`central_command_<agent_id>`) alongside the shared
group, but every write to either scope remains a gated `graph.add_episode`
proposal through the same Proposal→audit→commit pipeline. What D11 always
protected — the poisoning boundary, distilled-claims-only, nothing writes
itself — is untouched. What it foreclosed by accident — a place for
role-specific knowledge that isn't team-wide noise — is opened, under the
same gate.

Reads: an agent's retrieval scope becomes `main, central_command,
central_command_<self>`. It never reads another agent's private partition; a fact
worth a teammate's attention belongs in the shared group (see routing, below).

*Revision 2026-09-01 (v2.20.0).* Two exceptions, both explicit: (1) a `private`
episode may name `for_agent` — a rule about how a TEAMMATE works lands in that
teammate's partition, visible on the proposal and validated against the active
roster (before this, "private" could only mean the proposer's own group, which
is how eight onboarding doctrines ended up in the EA's partition); (2) the
`graph-curate` pack's `list_graph_groups` / `list_graph_group_episodes` /
`search_graph_group` reads see EVERY partition — a curator that cannot see the
patient cannot operate. The exception is granted by holding the pack, never by
agent id, and `graph.rescope_episode` (episode = unit of scope; shared nodes
split, nothing deleted) is the gated fix.

## Decided shape

### 1. Scope is part of the proposal, never a silent decision

The agent chooses a target scope (private vs shared) per episode, by content:
role-specific lessons → its own partition; team-relevant facts ("system X
depends on system Y", "person A leads project X") → shared. But the choice is
a **visible field on the `graph.add_episode` proposal** the operator reviews —
defaulted by the agent's judgment, never baked in. Rationale (research pass
2): production memory systems (Zep, mem0) put the shared/private boundary at
the architecture layer, not inside per-fact model judgment, because
self-graded routing has no independent check. A mis-scoped write can be
entirely true and correctly approved — the routing judgment must be on the
record, not riding invisibly inside the fact.

### 2. Session-close reflection

`converse.close_session()` (`runtime/converse.py`) is the one seam — already
guarded against mid-turn races and double-close — where a closing session may
run a bounded reflection step: what did this session learn that is worth
keeping? Output is proposals, in two distinct vocabularies:

- **Graph episodes** — zero to N distilled facts, each its own
  `graph.add_episode` proposal with its scope field. Atomic on purpose: three
  facts are three proposals so the operator can approve two and reject one.
  N is a small structural bound (exact value an open question); overflow is
  *noted in the last episode's evidence*, never silently dropped.
- **Coaching self-nomination** — at most **one per closed session**, and it
  is a *signal*, not a charter edit: a new signal kind (`self_reflection`)
  appended into the same pool `gather_coaching_signals()` already serves,
  subject to the same `claim_supported()` verification (the agent supplies
  pointer + quote; citing a session it never had is flagged like a misquote).
  The coach distills across signals into a charter diff exactly as it does
  for rejections today — one session's self-assessment is *evidence for*
  coaching, never a charter change by itself. The one-per-session cap is
  structural, not charter prose, because it is what keeps the operator's
  queue bounded by session count rather than by agent enthusiasm — and the
  nominating agent has the strongest incentive in the system to rationalize
  its own episode into a durable rule. A nomination may carry multiple
  observations; "I was right to do X" is as valid as "I should stop doing Y"
  (coaching-on-failures-only drifts an agent toward timidity — existing
  doctrine).

Demo mode: reflection produces nothing (canned models can't glean lessons;
demo proves plumbing, never judgment).

### 3. Volume scales by trust-tier graduation, not by hope

The two scopes are **two distinct action classes** on the existing ladder
(2026-07-30 record): `graph.add_episode[scope=private]` and
`graph.add_episode[scope=shared]`. Private writes graduate first — smallest
blast radius (a wrong fact stuck in the writing agent's own silo, read by no
one else), same precedent as `dismissal.confirm`. Shared writes stay
human-gated until the agreement record shows the agent's private-vs-shared
calls consistently match what the operator would have chosen — pairing the
agent's proposed scope with the operator's approved scope is the natural
paired evidence the ladder requires. Graduation is per action class, flipped
by the operator, revocable — never per agent, never by code.

### 4. A scheduled cross-session review (later slice)

A heartbeat-scheduled process review — a governed recurring action in the D27
scheduler, seeded **disabled** like every other new schedule — that reads
recent closed sessions *across the roster* and proposes what no single
session can see: handoff friction between agents, repeated inefficiencies,
promotion of private facts that keep proving team-relevant. Its outputs use
the same proposal vocabulary as §2 (graph episodes with scope, coaching
signals), so it adds no new gate surface; per existing doctrine the gate
attaches to the proposed work, never to the schedule. This is the curator
pattern (research pass 2, option B) folded in as a complement rather than the
starting point: per-session reflection ships first because the reviewer
consumes the vocabulary reflection establishes.

### 5. Rejected: provisional / expiry-based memory

"Write now, operator can veto within N days" inverts the gate into "nothing
changes unless the operator notices in time" — the same failure mode the
"no action is a claim" incident already paid for once. Not adopted, not
deferred: rejected.

Also rejected: Letta-style agent self-editing of its own memory/state outside
the gate. Error-not-malice includes an agent memorializing something false or
overconfident; the gate is the mitigation the literature lacks.

## Where it attaches (for the implementation plan)

- `integrations/graphiti.py` — `group_id`/`group_ids` already per-call; add
  the per-agent scope convention (`central_command_<agent_id>`) and widen
  per-agent read scopes. Config: `graph_write_group` stays the shared
  default; scope arrives per proposal.
- `gateway/executor.py:_graph_add_episode` — honor the scope argument; the
  scope an agent writes into its own args is agent-authored, so the executor
  takes the *approved* scope from the proposal the operator saw.
- `runtime/converse.py:close_session()` — the reflection hook.
- `runtime/run.py:gather_coaching_signals()` — the `self_reflection` signal
  kind, same verification path.
- `runtime/proposals.park_proposal()` — reflection proposals take the one
  park path like every other proposal (the walker test in
  `tests/test_proposal_created.py` enforces this for free).
- Trust-tier ladder — two new action classes with paired scope evidence;
  agreement report before any graduation flip.
- Heartbeat (`heartbeat/actions.py`) — the cross-session reviewer as a new
  closed-registry action, seeded disabled. Its runs produce proposals
  (material by definition: they authorize gated work).
- Decisions Inbox — the scope field must be *visible* on episode proposals
  (cockpit change; backend wire-shape test per convention).

## Open questions (deliberately not decided here)

1. **N**, the per-session bound on graph-episode proposals (3? 5?). Pick from
   observed volume once reflection runs in shadow, not from taste.
2. **Digest UI** — whether the Inbox wants a "lessons" digest view once
   volume is real. UI sugar; reduces clicks, not judgment load.
3. **Reflection cost** — a reflection step is a model call per closed
   session. Whether it runs inline at close or as a deferred/idle-time pass
   (sleeptime pattern), and on which model tier.
4. **Cross-session reviewer's remit** — sessions only, or also the event
   log / task ledger? Scope creep risk; start with closed sessions.
5. **Private-partition retention** — whether private facts ever expire or
   get consolidated (the graph is disposable during build, so this can wait
   for real use).
