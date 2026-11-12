# What belongs in the graph

## The layer doctrine — four layers, not competitors

Decided 2026-07-29 (`docs/superpowers/specs/2026-07-29-knowledge-layer-routing-design.md`).
Before proposing an episode, check the fact is not really something else:

| Layer | Question it answers | Rule |
|---|---|---|
| **Capability packs + grants** | what may this agent *do* | Permissions only. **Never knowledge.** |
| **Skills library** | *how* — procedural, stable, curated | Versioned how-to and judgment: conventions, runbooks, operator-defined standards that change rarely. |
| **Knowledge graph + KB** | *what is true* — factual, **evolving**, shared | Steward-maintained; every agent reads the same store. Narrative artifacts (notes, reports, minutes) → the KB/Confluence; **extracted facts → the graph**. |
| **MCP / native clients** | live-system access | Transport. Not a place to store anything. |

Skills teach *how*; retrieval answers *what*; MCP reaches what changes between
invocations. An operator-defined **standard** is a skill if stable, a graph
fact if evolving.

So: a stable procedure is **not** a graph episode. A long narrative document is
**not** a graph episode. A permission is **never** a graph episode.

## What IS worth an episode

Durable, checkable facts about the team's world:

- **Decisions, and who made them.**
- **Ownership** — who owns a system, an issue, a workstream.
- **Dependencies** — that A blocks B, stated outright by the source.
- **Dates and date changes.** A due-date change **always** qualifies: it
  supersedes the team's previous understanding of the schedule.
- **Systems and their status** — introduced, decommissioned, degraded.
- **New tracked work items.**
- **What the operator's world is made of** — who a person is to them; that
  an account, a policy, a membership, a subscription, a vehicle, a property or
  a provider relationship exists; and its stable attributes (the institution,
  the kind, an identifier, when it started and ended, how it is paid).
- **Changes of status** — opened, closed, switched, traded in, cancelled,
  disputed.

The test: *a teammate reading this months from now would act differently for
knowing it.*

## What is NOT

- Narrative, restatement, pleasantries.
- Anything the source merely **speculates** about.
- Newsletters, FYIs, trivia.
- Facts you are not sure actually happened.
- Anything you would have to paste source text to express.

**A document with no extractable fact is a normal outcome, not a failure.**
Answer in plain text and the operator confirms. Never manufacture a fact to
have something to propose.

## The source is not the fact

An episode records what is now known about a **thing**: a person, an account,
a vehicle, a policy, a project, a relationship. The message that taught you is
its **source**. It goes in `source_description` and never in the body, because
every sentence in the body becomes a fact in the graph: a sentence about a
receipt becomes a receipt the whole team retrieves forever.

Draft in this order.

1. **Name the thing** the source teaches about.
2. **Read what the graph already holds about it.** Search for the thing itself
   (the institution and kind of account, the person, the vehicle), not only the
   identifier in front of you: an account number the graph has never seen can
   belong to an account it knows well. The episode is the **delta**, what is
   known now that was not known before, and it names the thing as the graph
   already names it. Nothing new means no episode.
3. **Judge the event separately.** The transfer, payment, statement, alert or
   shipment that revealed the thing is its own question, answered by the same
   test: would a teammate months from now act differently for knowing this
   event happened? A change of status passes and is its own episode. A routine
   occurrence fails and leaves no trace in the body.

A figure or a date belongs in the body when it describes the thing (a plan's
monthly price, a policy's premium, when an account was opened) and not when it
describes one occurrence of it (this month's bill, one payment, a balance on
one day). An occurrence is never needed in the body as **proof** that the
thing exists: the evidence you cite and `source_description` carry the proof.

People are things too. A relative's letter evidences who they are to the
operator, when that is new. The news in the letter (a renovation, a trip, a
greeting, a meeting invite) is that letter's occurrence, not a fact to keep.

## The shape of a good episode

**One episode per thing.** A source that teaches about two things yields two
episodes in the same proposal, each judged on its own. Never fold a second
fact into the first one's body: two clear episodes beat one jumbled one.

- `name` — a short title.
- `episode_body` — **1–3 self-contained sentences**, naming the people, issue
  keys and dates involved. Self-contained means it still means something to a
  reader who never sees the source. Never pasted source text.
- `source_description` — where it came from (e.g. `email <message-id>`, the
  document id, or the operator).
- `reference_time` — **required**, the ISO-8601 instant the source material is
  from (the mail's sent date, the event's timestamp, the moment the operator
  said it), read from the material and never "now": the graph anchors every
  present-tense fact to it and the Executor refuses a proposal without one.
- `scope` — **required**, exactly `shared` or `private`; nothing defaults,
  because the partition is part of what the operator approves. `shared` is
  the team graph (a domain steward's write lands in its domain group, still
  inside the shared read set); `private` is your own partition, or another
  active roster agent's when you add `for_agent: '<agent id>'`. One
  teammate's operating doctrine is that teammate's private episode via
  `for_agent`, never a shared one.

Proposal envelope: `target_ref = {'system': 'graphiti', 'id': 'central_command'}`, `reversibility = 'reversible'`.

An episode can ride **alongside other actions in one proposal** (a Jira change
plus the episode that records what it means) or **stand alone** when the source
is knowledge-only.

### Good

> "TASKS-12's due date moved from 2026-08-06 to 2026-08-07 after Dana
> Rivers superseded her own morning message on 2026-07-02; the vendor invoice
> INV-4471 payment date is unchanged."

Names the issue, both dates, the person, and the supersession.

### Bad

> "Dana Rivers sent an email about the deadline."

Not a fact anyone acts on. Names no date. Restates that a message existed.

> "It seems the team may be under schedule pressure this quarter."

Speculation, unnamed entities, unfalsifiable.

### The receipt and the account

A bank confirms a $2,500 transfer to an account ending 7731, and the graph
already holds Jane Doe's checking account at Example Credit Union.

Bad:

> "Jane Doe holds a checking account at Example Credit Union ending in 7731.
> As of 2026-08-07, she transferred $2,500 from her other checking account to
> this account."

The first sentence restates an account the graph already knows. The second
records a receipt: nobody acts differently next spring for knowing it.

Good:

> "Jane Doe's checking account at Example Credit Union ends in 7731."

The one thing the mail added, said about the thing it belongs to. The transfer
confirmation is named in `source_description`.

## Keeping the graph in sync is half the job

For a triage agent this is explicit: **before you propose, answer one question —
does this source change what the team believes about some thing in the
world, and which thing?** If yes, the
proposal should include the `graph.add_episode` action alongside the other
change, or stand alone when the source is knowledge-only. But the graph's
failure mode is over-inclusion, so the retention test applies strictly and
**when in doubt, do not propose the episode**: skip it for trivia, newsletters,
sender dispositions ("mail from Y is expected no-action mail" is triage policy,
not world knowledge), process state (where a run got to, what is still open),
and facts you are unsure actually happened — an uncertain fact goes to the
operator as a question, not to the graph.
