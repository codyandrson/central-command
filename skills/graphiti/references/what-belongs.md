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

## The shape of a good episode

**One proposal, one episode.** If the source carries several unrelated facts,
choose the one that matters most and **say in your intent what you left out**.

- `name` — a short title.
- `episode_body` — **1–3 self-contained sentences**, naming the people, issue
  keys and dates involved. Self-contained means it still means something to a
  reader who never sees the source. Never pasted source text.
- `source_description` — where it came from (e.g. `email <message-id>`, the
  document id, or the operator).

Proposal envelope: `target_ref = {'system': 'graphiti', 'id': 'central_command',
'read_version': 'unknown'}`, `reversibility = 'reversible'`.

An episode can ride **alongside other actions in one proposal** (a Jira change
plus the episode that records what it means) or **stand alone** when the source
is knowledge-only.

### Good

> "TASKS-12's due date moved from 2026-08-06 to 2026-08-07 after Dana
> superseded her own morning message on 2026-07-02; the vendor invoice
> INV-4471 payment date is unchanged."

Names the issue, both dates, the person, and the supersession.

### Bad

> "Dana sent an email about the deadline."

Not a fact anyone acts on. Names no date. Restates that a message existed.

> "It seems the team may be under schedule pressure this quarter."

Speculation, unnamed entities, unfalsifiable.

## Keeping the graph in sync is half the job

For a triage agent this is explicit: **before you propose, answer one question —
does this source change what the team believes about the world?** If yes, the
proposal MUST include the `graph.add_episode` action alongside the other change.
It is not optional garnish. Only skip the episode for trivia, newsletters, or
facts you are unsure actually happened.
