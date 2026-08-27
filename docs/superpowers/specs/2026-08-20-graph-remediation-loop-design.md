# Graph remediation loop — a PROBLEM verdict becomes a proposed fix

_Design record written 2026-08-20, extending the graph verification auditor
(2026-08-19 spec) with the fix half, at the operator's direction: "provide the
feedback in such a way that it gets fixed and then I re-verify the new
result." Built the same day._

## The shape in one paragraph

When the operator marks a verification PROBLEM, their note (plus the approved
episode text, the audited delta **with uuids**, and the auditor's rationale)
becomes a task for a new hired agent, the **graph-curator**, which proposes
the MINIMAL gated curation edits — merge duplicate entities, delete invented
edges, repoint/re-date relationships, restore wrongly-retired facts. Those
proposals land in the **Decisions Inbox** like any other, with the normal
approve/reject/redraft loop. When an approved edit executes, the Executor
queues a fresh read-back of the same episode into the **Verify tab**
(`remediation_of` links it to the PROBLEM row it re-verifies), so the
operator re-checks the fixed state on the same surface they flagged it.

## Why this is NOT the proposal reject-resume pattern

Reject resumes the drafter because the draft was wrong. Here the draft — the
approved episode text — was fine; the EXTRACTION mangled it. Resuming the
original proposer would re-submit the same episode and create duplicates.
The remediation is a different artifact (surgical edits), so it is a
different agent's task.

## Why it never touches Graphiti's extraction

Every fix is a deterministic Neo4j edit through the operator-curation
primitives (`integrations/neo4j_writer`), which already carry the half-write
protections (identity in two places, re-embedding, endpoints as properties).
Re-ingesting a corrected episode would re-roll the same extraction dice that
caused the defect. The curator's charter forbids proposing a new episode as
a fix for this reason.

## Decisions

1. **A second agent, not a second duty.** The auditor stays read-only and
   independent — a remediation is work it might later judge (the re-check),
   so the curator is its own hire: `graph-curator`, an ordinary taskable
   roster member (uniform management, no deviations), hired on first use
   from `GRAPH_CURATOR_TEMPLATE` with packs `graph-read`, `graph-curate`,
   `ask-operator`.
2. **Seven gated capabilities** (`graph-curate` pack ↔ Executor handlers ↔
   capability registry, parity-guarded): `graph.merge_nodes`,
   `graph.update_node`, `graph.delete_node`, `graph.update_edge` (repoint,
   re-date, restore), `graph.delete_edge`, and (added the same day, after
   the first live test hit the dropped-content case) `graph.create_node` /
   `graph.create_edge` — a deterministic write of exactly the approved
   words, with the writer recording its own provenance episode, still never
   re-ingestion; `create_edge` takes `valid_at`/`invalid_at` so a missed
   fact lands with its real validity window. Restore clears `invalid_at` AND
   `expired_at` — the writer gained `clear_expired` because the verification
   sweep attributes retirements by `expired_at`, so a half-cleared restore
   still reads retired.
3. **The re-check rides the existing worklist.** Each executed curation
   action calls back with its `verification_id`; the FIRST action of a
   proposal creates one PENDING `graph_verification` row with the ORIGINAL
   marker (same episode, fresh read-back) and `remediation_of` set. The
   sweep audits it like any row; `_approved_episode_body` follows the
   `remediation_of` chain so the judgment still compares against the
   original approved text. `verification_id` is agent-supplied — worst case
   a wrong id re-checks the wrong episode, which the operator sees; the
   graph writes themselves are operator-approved cut by cut.
4. **Remediation is opt-out, not automatic**: the Problem dialog's "task the
   graph curator" checkbox defaults ON; unchecking records the problem
   without spawning work.
5. **Named, not built**: auto-linking the re-check verdict back to coaching
   signals for the curator (the standard agreement machinery covers the
   auditor; the curator is coached off proposal rejections like every
   agent). (`graph.create_*` was in this list for a few hours — promoted to
   decision 2 after the first live test hit the dropped-content case.)
