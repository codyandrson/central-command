# Stage 5 — Coaching experiences: hire the coach

_Spec written 2026-07-27. Supersedes the original "team-wide Performance &
Coaching screen" framing of stage 5 (see **Decision 3**)._

## What this is

Stage 5 of the agent-management split. the operator asked for **three coaching
experiences**, not a page:

1. **Coach on recent work** — from an agent's profile, see what it has recently
   done, select some of it (or none), add feedback, get a proposal to review.
2. **Coach by conversation** — work *with* the coach agent, discuss the changes
   you want based on your own observations, and have it draft the same kind of
   proposal.
3. **Thumbs up/down** — judge an individual action, proposal or decision where
   you meet it, get prompted for input, and have that feed the coach.

All three end in the same place the coaching loop already ends: a
`charter.update` proposal in the Decisions Inbox, reviewed as a diff, approved
or rejected by the operator, committed by the Executor with pedigree, one click
from revert.

This spec designs all three, and specifies **stage 5a — the spine** to build
depth. 5b/5c/5d are designed at the level needed to keep 5a's decisions
coherent, and each gets its own plan when its turn comes.

## Why the spine comes first

The three experiences differ only in **how a coaching signal is captured**.
They share:

- **the coach** — who reads the material and drafts the edit;
- **the proposal** — `charter.update`, diff review, gated approval;
- **the record** — what may be cited and how a citation is verified.

Build the shared part once, on behavior that already exists and is understood,
then add capture surfaces one at a time. This matches how the management split
itself was delivered: incremental, validated per stage, nothing moving until
the one before it is proven.

**5a ships no new feature.** It changes who drafts existing coach sessions and
puts them on a footing the other three can stand on. That is the cost of doing
it first, accepted deliberately.

## What exists today

- **`run_coach(agent_id, ...)`** (`runtime/run.py:281`) gathers the operator's
  curated signals, embeds the target's CURRENT charter, and asks for a minimal
  edit citing each signal verbatim.
- **The drafter is `Agent(name=f"{agent_id}-coach")`** (`run.py:323`) — an
  **ephemeral, unrostered** agent constructed per request. It has no roster row,
  no governed charter of its own, and cannot be coached. Measured against the
  uniform-agent-management rule (every agent gets roster + governed charter +
  coaching; deviations need a recorded reason), this is an **undocumented
  deviation** already in the tree.
- **Curation is authoritative:** `POST /api/agents/{id}/coach {signal_ids, note}`
  runs over exactly the selection; nothing feeds a session unchosen. The request
  is logged first so an operator note is citable as `event:<id>`.
- **Citations are re-checked:** `contract.claim_supported()` compares the quote
  against the curated signals. Citing a signal the session was never fed flags
  exactly like a misquote.
- **The Executor commits** an approved edit as a new governed version
  (`executor.py:82`), append-only, revert available.
- **Stage 3 already put the coach action on the Agents page.** The pre-pivot
  `Coaching.jsx` is otherwise re-homed: its approval-rate tiles are the stage-1
  roster; its curated-signal panel is the stage-3 dialog.

## Decisions

### Decision 1 — The coach is a rostered agent, hired

**Chosen.** Alternatives considered: keep the per-target ephemeral coach and
record the deviation; or roster a coach per target agent.

A rostered `coach` is the only option where "work with the coaching agent"
(experience 2) names something concrete, and where thumbs-down (experience 3)
has an addressee. It also converts the existing quiet deviation into a governed
team member.

**Consequence, stated plainly:** an agent that may propose changes to *other
agents' governing documents* is real authority, even behind the approval gate.
It is the second capability acting on a teammate, after `task.create` naming an
assignee — and unlike that one, it acts on how the teammate *thinks*, not on
what is on its board.

### Decision 2 — Full read access; selection is context, not a fence

**Chosen** (the operator, verbatim): the coach has full read access; a selection is
provided as part of its prompt/context but does **not** prevent it retrieving
more at its discretion — *"that behavior can be tuned by coaching the coach over
time."*

This deliberately relaxes "nothing feeds a session unchosen" from a hard rule to
a **foregrounding**. The rule was worth having — curation, not the model,
decided the outcome of the 2026-07-25 sessions — and the replacement control is
that discretion is visible (Decision 4) and correctable by coaching the coach.

### Decision 3 — No team-wide page

**Chosen.** The original stage 5 was a Performance & Coaching screen. All three
experiences are **contextual** — they attach coaching to a specific thing the
operator is looking at (an agent, a conversation, a decision). A page would be a
fourth place coaching lives, and a surface the operator did not ask for.

The cross-team record (all rejections, all reopen notes, trends) therefore has
no home. If it is later missed, it is its own small read-only stage.

### Decision 4 — Citations verify against the record, marked selected/discovered

**Chosen.** Once the coach reads freely it can cite something real that the
operator did not tick; today's check would call that a fabrication.

- The quote is verified against **the cited event in the record**, selected or
  not. No agent claim is trusted unverified — the guard holds, over a wider
  corpus.
- Each citation is marked **`selected`** (in what the operator ticked) or
  **`discovered`** (the coach went and found it), shown in the Inbox beside the
  quote.
- **`claim_matches_source: None` still means NOT CHECKED**, never "checked and
  inconclusive."

**A discovered citation is verified as real, not as representative.** The coach
may quote a genuine rejection from months ago that the operator considers
settled. The marker makes that visible at approval time; nothing prevents it.
That is the intended tuning surface.

### Decision 5 — Full uniformity: the governed charter IS the prompt

**Chosen.** Alternatives: hire the coach for identity but keep the bespoke
`Agent(...)` construction; or ship 5a with reads deferred.

The coach runs through the same `build_agent`/toolsets path as every other
agent, assembled from its grants, with its governed charter as its system
prompt. If coaching the coach cannot change how it runs, the charter is a
document nobody reads and Decision 1 bought nothing.

The `record-read` pack is **in 5a**, not deferred: a hired coach that cannot
look at anything is not the coach experiences 2 and 3 need, and deferring it
would ship the `discovered` marker for a path that cannot yet happen.

## Stage 5a — the spine

### The coach agent

- **Identity:** `coach`. Hired through the existing one-transaction path (roster
  row + charter v1 + pack grants) with an `agent.hired` event, **and** added to
  `schema.sql`'s founding-roster seeds.
  **Both, deliberately.** The live database gets it by hire; a fresh database
  must reproduce the roster or it boots a team missing a member. The seeding
  rule has drawn blood: the founding-roster `UPDATE`s are guarded on
  `role = ''`, so an INSERT that seeds `role` silently disables the guard — the
  whole roster came up untaskable on a clean database, invisible on the dev box
  until the Pi met it.
- **Charter v1:** the coaching job, with its **capability list generated from
  its grants** at run start (D25). Capability lists are never hand-written —
  hand-written ones drifted, which is what the 2026-07-22 challenged-dismissal
  incident cost.
- **Roster membership** is a non-empty `role`, set by `hire_agent` — never by a
  plain `upsert_agent`.

### Capabilities — two new packs

**`record-read` (ungated)** — reads over Central Command's own record: **any**
agent's charter and version history, its proposals and their decisions, its
sessions, its coaching signals, its grants. Not scoped to a designated target —
Decision 2 is full read access, and a coach that can only read the agent it was
pointed at cannot answer "is this a pattern across the team?". This is the first
read capability over Central Command's own record.

**`charter-propose` (gated)** — grants `charter.update`, which **already exists**
in the registry (`gateway/capabilities.py`, the 7th gated write, D5). This adds
**no new gated write**: the capability is the same, and what changes is who may
hold it and for whom.

Its registry entry gains the widening on the record — that the drafter may
differ from the target — the way `task.create`'s entry recorded that a proposal
may name its assignee. **Its `arguments` do not change**
(`agent_id`, `content`, `rationale`): `agent_id` is the target, and the drafter
is supplied server-side from the proposal row, never as an argument the agent
writes. The registry is executable truth — the Executor dispatches from it — and
both packs are parity-tested against it.

**Not granted:** Jira, graph, LiteLLM. The coach reads the team's record and
proposes charter edits. That is the whole job — and a grant an agent cannot use
is a lie on its profile.

**Self-coaching:** the coach is coachable like any other agent, and the drafter
of its own charter edit is the coach itself. A governed self-reference: it parks
in the Inbox, the operator reviews the diff, revert is one click. Preferred over
a second special-case coach-for-the-coach.

### The provenance fix (required, not optional)

`executor.execute(actions, approver, source_refs)` never receives the drafting
agent, which is why `_charter_update` reaches for `args["agent_id"]` — the
**target** — and stamps `created_by = f"agent:{args['agent_id']} (approved
{approver})"`. The moment a coach drafts for someone else, the version row and
the `charter.updated` event both claim the target proposed its own edit. That is
a false provenance record.

The fix follows stage 4's precedent: **the drafter must not be something the
agent writes into its own action args** — an actor an agent can name is an
agent-authored claim about provenance.

- `execute()` gains a `proposer` argument, passed by the gateway from
  `proposals.agent_id` (server-side truth).
- `_charter_update` stamps drafter and target separately:
  `created_by = "agent:coach for <target> (approved human:lee)"`, and the
  `charter.updated` event carries `proposed_by` and `target` as distinct fields.
- **Existing rows are not rewritten.** A version row saying `agent:inbox-triage`
  is still true of the session that produced it. Backfilling would falsify the
  record.

### The citation change

`claim_supported(claim, source)` (`contract/models.py:39`) is a pure string
containment check and **does not change**. What changes is the *source* the
coach path hands it: the cited event fetched from the record, rather than a
string built from the curated selection. The `selected`/`discovered` marker is a
property of the pointer — was this event id in what the operator ticked — and
rides the proposal record into the Inbox.

`claim_supported()` stays in `contract/` because both tiers re-check quotes and
`runtime/` may never import `gateway/`.

### `run_coach`, rebuilt

The bespoke `Agent(...)` construction goes away. Coaching becomes: run the
rostered coach, assembled from its grants, with the target's material injected
as context and the operator's selection foregrounded. Every existing call site
keeps routing through the one path.

Unchanged: the coach request is logged **before** the run and the run is handed
that event id — the ordering that makes a coached charter's citation checkable.
That path already shipped once with its quote check silently not running.

### UI in 5a

The stage-3 Coach dialog on the Agents page keeps working and now states that
the coach drafts the edit. The Inbox renders the `selected`/`discovered` marker
beside each cited quote. No new screen.

### Tests

The two that carry the design:

1. **A coach-drafted charter version names the coach as drafter and the target
   as subject** — confirmed to **FAIL against the current executor** before
   being kept. A guard not proven to fail on the broken version is decoration.
2. **The coach's governed charter is its system prompt** — the test that makes
   Decision 5 real. If coaching the coach cannot change how it runs, Approach 2
   was built by accident.

Also:

- a `proposer` claimed in action args never overrides the proposal row;
- a discovered citation verifies against the record; a paraphrase still flags;
- `claim_matches_source: None` still reads as not checked;
- pack/registry parity for both new packs;
- a fresh database reproduces the roster including `coach`;
- coaching a RETIRED agent 409s with the real reason (404 stays for an id that
  does not exist).

Stubs where a real run would spend tokens, per the existing coach tests.

### Live validation (the operator)

A coach session against **`litellm-manager`** (4 signals, charter v0 — lowest
stakes), approve the diff, then confirm the new version row says the **coach**
drafted it and names the target. Existing behavior on new footing: the point of
doing 5a alone.

## Stages 5b–5d (designed, not specified)

### 5b — Coach on recent work (experience 1)

From an agent's profile: its recent work — proposals and their decisions,
sessions, dismissals — selectable, with a feedback box, feeding the same coach
run. This widens the signal vocabulary from *recorded feedback events* to *work
items*: today the only way to teach is to reject something.

Open for 5b: how a work item becomes a citable signal (it is not a feedback
event; the coach's citation must point at something with a stable id).

### 5c — Coach by conversation (experience 2)

A chat with the coach, which every active agent already supports. The operator
describes observations; the coach reads the record at its discretion
(Decision 2); the conversation concludes with a `charter.update` proposal.

Open for 5c: how the conversation's own content becomes evidence — the operator's
words in a chat are trusted operator input, but the coach's summary of them is an
agent claim, and `claim_supported()` needs a recorded source to check against.

### 5d — Thumbs up/down (experience 3)

A thumb on an action, proposal or decision where the operator meets it (Inbox,
Workspace, Board), which prompts for input and records a signal the coach can
draw on.

**This is the one that fills a real gap:** the coaching corpus today is entirely
negative — rejections and reopen notes. There is no way to record that an agent
did something *well*. Positive signal is not decoration; a charter coached only
on failures drifts toward timidity, which is the exact failure the 07-25 sessions
avoided by feeding the over-correction and the under-correction together.

Open for 5d: whether a thumb alone (no note) is a signal or only an index; and
which surfaces get thumbs first.

## Out of scope

- A team-wide Performance & Coaching page (Decision 3).
- Rewriting existing charter-version pedigree (the record is not falsified).
- Any change to how proposals are approved, diffed or reverted.
- Coaching triggered by anything other than the operator. The heartbeat may not
  start a coach session: approval attaches to what work does, never to its
  trigger, and a self-coaching team on a timer is exactly the shape that rule
  exists to prevent.
