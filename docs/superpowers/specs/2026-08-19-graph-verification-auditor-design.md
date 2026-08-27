# Graph verification auditor — closing the loop after `graph.add_episode`

_Design record written 2026-08-19, from an operator-raised gap: the approval
gate reviews the INPUT to graph ingestion, never the resulting graph change,
and nothing audits the change afterward. This record fixes the decided shape.
It is the next customer of the trust-tier graduation ladder
(2026-07-30-trust-tier-graduation-design.md) — a new agent on the same
shadow → agreement record → operator-flips-a-class rungs, per the operator's explicit
decision: **a similar pattern, a different agent** — not a second duty bolted
onto the dismissal auditor._

## Why this record exists

What the operator approves on a `graph.add_episode` proposal is the episode:
name, body text, scope, source description. The Executor submits it and
Graphiti **queues** ingestion server-side — `integrations/graphiti.py`'s own
docstring: "the return is its acknowledgement, not the finished graph state."
The dangerous transformation (entity extraction, typing, edge creation,
temporal validity, dedup, **invalidation of existing facts**) happens after
the gate, by the local model, unreviewed. Every graph incident on record is
exactly this gap wearing a clean "committed" ack:

- 25 approved episodes silently dropped (2026-08-15, bad `group_id` charset —
  queued failure after ack);
- true facts retired (the mother episode invalidating the grandfather and
  aunt — a destructive write to facts the approval never mentioned);
- nodes without `name_embedding`, present in the UI and invisible to semantic
  recall forever;
- every person typed bare `Entity` for weeks (no `Person` ontology type).

Each fix so far closed one observed mode; none added the outcome check. The
graph is the agents' accumulating brain — a flaw compounds, and finding it
later gets harder as the graph grows. The operator's end state, decided
2026-08-19: **approve the claim once; structural checks plus an aligned
audit verdict close the loop silently; the operator sees only flags,
disagreements, and (initially) invalidations.** Never a second review of
every write.

## The shape in one paragraph

At execute time the Executor records a verification row for the episode. A
recurring heartbeat sweep picks up rows past a settle delay, runs
**mechanical checks in code** (did the episode land, in the right group, did
it produce anything, is it embedded), reads back the **actual delta** from
Neo4j, and hands delta + approved episode + mechanical report to a new hired
roster agent, the **graph auditor**, for one typed judgment: *does the graph
change faithfully represent the approved claim, and are any invalidations
justified?* ALIGNED closes the row; FLAG parks an Inbox item for the
operator with the auditor's reason and the rendered delta. The auditor
climbs the standard ladder before its ALIGNED verdict closes anything.

## Decisions

1. **Mechanical before judgment — code checks what code can check.** The
   sweep asserts, without an LLM: the Episodic node EXISTS in the intended
   group (the silent-drop class); it produced ≥1 entity or edge (the
   extraction-failed-empty class); every new entity carries `name_embedding`
   (the invisible-to-recall class). A mechanical failure is an ERROR item for
   the operator directly — never a question put to the auditor, so a code bug
   is never misdiagnosed as a judgment failure or vice versa.

2. **Correlation is stamped, not guessed.** `add_memory`'s ack carries no
   episode uuid, so the Executor stamps `proposal=<id>` into
   `source_description` (which already carries `trust=` and `approver=`) and
   the sweep finds the Episodic node by that marker in the expected group. A
   row whose marker never appears after the settle window IS the silent-drop
   finding.

3. **The delta is read from Neo4j, not from Graphiti's account.** Entities
   via `(:Episodic)-[:MENTIONS]->(n)`; created/touched edges via
   `RELATES_TO.episodes` containing the episode uuid (`neo4j_reader.provenance`
   already walks the inverse). Invalidations — the destructive class — are
   attributed by `expired_at` set within the episode's ingestion window;
   whether the invalidating episode also lands in `edge.episodes` is verified
   empirically in the first build task, and the window method is the
   fallback. Fuzziness is acceptable here because episodes are queued
   serially per group and volume is human-approved-writes low.

4. **The graph auditor is a team member under uniform agent management** —
   hired to the roster, governed charter, coached through the existing loop.
   Its charter is ONLY the judgment layer: given the approved episode text,
   the mechanical report, and the rendered delta, state
   `aligned | flag` + rationale, judging entities, relationships, temporals,
   scope, and the justification of each invalidated fact. Independent
   derivation per the ladder: it judges from the approved proposal text and
   the actual graph rows — never the extractor's summary of what it did.

5. **The auditor is read-only, before and after graduation.** Its graduated
   authority is closing the verification item, nothing else. A fix is never
   the auditor's to make: wrong extraction still reaches the graph only
   through the operator's gated curation path (`neo4j_writer`). The worst a
   wrong ALIGNED does is fail to surface a problem — which is precisely what
   the agreement record measured before the class flipped.

6. **Two action classes, graduated separately.** `graph.verify` covers
   addition-only deltas — blast radius of a wrong ALIGNED is one flawed new
   fact. `graph.verify_invalidation` covers any delta that retires an
   existing fact — the grandfather/aunt class — and keeps surfacing to the
   operator even after additions graduate, flipping only on its own record.
   Per the ladder: own class name, own shadow period, own revocable flag,
   auto-handle only the agreeing direction, everything else degrades to the
   human gate.

7. **Supervised first — and it is a measurement, not a ceremony.** Rung 0:
   every verification still reaches the operator, with the auditor's verdict
   attached; rung 1: a pure pairing function + fresh-computed agreement
   endpoint, dangerous quadrant (auditor ALIGNED, operator found a problem)
   listed case by case; rung 2: config flip per class. The honest stake: the
   audit runs on `cc-default`, the same llama.cpp server whose extraction
   caused the incidents. Verification-given-both-artifacts is a much easier
   task than extraction, but whether the local model clears the bar is
   empirical — if agreement stays poor after coaching, the conclusion is
   "route this audit to a stronger alias" (a LiteLLM decision), not more
   charter rounds.

8. **Verdicts are on the event log; sweep ticks follow the material rule.**
   `graph.audit.verdict` is emitted for every judgment, ALIGNED included —
   rung 0 requires the record, volume is bounded by human-approved writes,
   and auto-closed cases are tallied separately (never counted as agreement,
   per the ladder's starvation honesty). The sweep tick itself is a
   non-authorising, self-recording heartbeat action: a tick that found
   nothing writes nothing.

9. **Build the spot check BEFORE `graph.verify_invalidation` graduates.**
   The ladder names post-graduation evidence starvation and defers the
   sampled spot check until a class's blast radius warrants it. A silently
   retired true fact is a compounding world-model change — this is the class
   the deferral was waiting for.

## What this record does NOT build

- **No staging-group two-phase ingestion** (extract → review delta →
  promote). Graphiti has no promote primitive; it would be a custom copy
  through `neo4j_writer` plus a review UI. Recorded as the escalation if the
  flag rate stays high after the extraction model improves — not before.
- **No auto-fix.** A flagged delta produces an operator item; remediation
  stays on the existing gated curation path.
- **No batching, no second write path.** One proposal = one episode = one
  verification row; the sweep is the only consumer.
- **No reuse of `gateway/auditor.py`'s code beyond its conventions.** The
  pairing functions pair different things (~100 pure lines each, per the
  ladder record's explicit no-framework rule); what is reused is the ladder
  itself and the verdict/agreement/coaching wire shapes the cockpit already
  renders.
