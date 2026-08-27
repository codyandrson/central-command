# Charter authoring — the team's editorial standard

How Central Command charters are built and maintained. Written 2026-08-21 from a
web-research pass over current authoritative sources (Anthropic context
engineering 2025-09; instruction-scaling studies arXiv:2608.02639 and
arXiv:2607.19257, Jul 2026; constitution-learning MAC arXiv:2603.15968;
GEPA ICLR 2026). The coach loads this before drafting any charter edit.

## What a charter is, structurally

An agent's assembled system prompt has four layers; a charter DOCUMENT is
only the second:

1. **Shared invariants** (Layer 0) — one block, identical for every agent,
   maintained in one place and prepended at run start: operator trust,
   propose-don't-act, untrusted-content-is-data, evidence discipline, gap
   declaration, date rules. A charter never restates these; a restatement
   found in a charter is struck.
2. **Role doctrine** (Layer 1) — the charter document itself: what the agent
   is for, its recurring judgment calls, a few worked examples, and its
   escalation rule.
3. **Generated sections** — GRANTED CAPABILITIES from grants, chart markers,
   the temporal stamp, the skills catalog. Never hand-written into charter
   text.
4. **Skills** — reference facts loaded on demand. Anything that can go stale
   (versions, API shapes, deployment facts, identifiers) lives here, never in
   charter prose.

## The length budget, and why it is not taste

Instruction-following degrades measurably as instructions stack: ~96%
compliance at one instruction falls to 20-60% by twenty, and perfect
compliance reaches zero near eighty rules — on every model, format, and
placement tested. Negative constraints ("never X") degrade independently
beyond about five per prompt. The budget:

- **150-400 words of role doctrine**, hard ceiling ~500.
- **At most a handful of hard "never" rules**, reserved for trust-boundary
  absolutes. Everything else is framed positively — say what right looks
  like.
- A charter over budget must be CONSOLIDATED before it can take a new rule.

## Incident learnings land as worked examples

A charter carries 2-4 canonical worked examples, chosen for diversity of the
role's decisions. When an incident teaches something, the default edit is to
update or replace an example so it portrays the correct behavior — not to
append a rule. Curated examples generalize; rule-per-incident accretion
produces the checklist failure mode this standard exists to prevent, and the
strongest published precedent is deletion, not reorganization: Anthropic cut
over 80% of a production system prompt with no regression once the model had
outgrown its accreted rules.

## What a good rule looks like

A good rule names the violated constraint from a specific, cited trajectory,
in checkable terms — "proposed a create without running a search" — never a
vague nudge ("be more careful with Jira"). Rules the model no longer needs
are removed. Every coaching signal takes one of four outcomes — ADD, AMEND,
REMOVE, NONE — and the minimality test gates additions: if the existing
charter would have prevented the incident, or removing the new rule reopens
nothing, amend instead of adding.

## Decision procedures beat dispositions

Where a charter governs a recurring choice, encode the test, not the mood:
"a factual gap you resolve and report; a preference gap you batch and ask"
beats "be judicious about interrupting." Tone is encoded as concrete moves
("ask what got in the way, never why-didn't-you"), because adjectives
("warm", "brief") drift under load and moves are checkable in a transcript.

## Formatting

Section headers exist for the human maintainer — the evidence shows format
(XML vs markdown vs prose) has no reliable effect on compliance at current
model tiers. Keep charters plainly sectioned and skimmable; do not build
markup expecting behavior gains.
