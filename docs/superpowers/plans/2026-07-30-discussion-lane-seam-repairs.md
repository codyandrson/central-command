# Plan — discussion-lane seam repairs + declare_gap widening (2026-07-30)

_Plan written 2026-07-30, picking up the seams recorded by the teaming
delivery. Motivation for doing these now: two of the three are discussion-lane
hygiene, and the EA (whose whole delivery surface is agent-opened lanes) is
about to be enabled — a stale open lane or a silently dropped conclusion goes
from latent to daily the moment `ea-*` schedules turn on. The
`reject_with_feedback` sibling of this family was already repaired with
consult slice 2._

## Seam 1 — `answer_item` bypasses an open discussion lane

**Today:** `api/orchestration.answer_item` answers the operator_item and
resumes the parked task, but when the item carries a linked
`discussion_session_id` (tier 3 — `_open_discussion`), the lane stays
AWAITING_OPERATOR forever: an undead row in the sessions panel whose question
was already answered somewhere else.

**Repair:** in `answer_item`, after the item is answered and before the resume
is scheduled — when `item["discussion_session_id"]` is set and that session is
still open:

1. Append the operator's answer to the lane transcript (the mirror of
   `_open_discussion`'s seeding: a lane that closes without showing WHY reads
   as truncated; the answer is trusted operator input and lands verbatim as an
   operator turn).
2. Close through the SHARED close seam (`converse.close_session`) so the
   cockpit gets its push frames — never `mark_session_done` directly (the
   2026-07-23 lesson) and never `end_conversation` (its guards are for the
   operator racing a live turn; there is no live turn here).
3. `reason`: inspect how `closedReason` is rendered by the composer divider
   FIRST. If it is a free-text passthrough, use `"answered"`; if it is mapped,
   reuse the closest existing value. Either way the existing backend wire test
   (`test_composer_state_carries_closed_reason_to_the_cockpit`) family covers
   the field; extend it if a new value is introduced. **No new
   `why_not_sendable` code** — the session goes terminal, which the cockpit
   already renders.

The 1:1 item↔lane link stays 1:1; fold-into-open-lane remains the separately
recorded next slice.

## Seam 2 — an approved mid-conversation proposal drops a deferred `conclude_discussion`

**Today:** the approve path resumes the agent with the Executor's result; if
that resumed turn ends by deferring `conclude_discussion`,
`_continue_if_conversation` rests the session AWAITING_OPERATOR and the
deferral is silently dropped — the agent believes it concluded; the lane says
otherwise. This is why conclude-before-propose is EA charter doctrine; the
doctrine stays, but the reverse order must stop being lossy.

**Repair:** `_continue_if_conversation` is the one seam all three decision
paths share — repair it there, not per-path:

- Before resting the session, `extract_deferred(final)`; when
  `classify_deferred(call) == "conclude"` AND the session is a linked
  discussion lane (`repo.get_operator_item_by_discussion` non-None), drive
  `questions.conclude_discussion(session_id, summary, agent_id)` with the
  call's args — the lane closes concluded (shared close seam, frames pushed,
  summary re-checked by `claim_supported`, task resumed), and
  `_continue_if_conversation` returns True WITHOUT resting the session
  AWAITING_OPERATOR (it is terminal now).
- A `conclude` deferral on a conversation that is NOT a discussion lane, or
  any other deferral kind in that turn, keeps today's behaviour exactly. A
  deferred `ask` in the same position is a real but different seam — record
  it, don't widen scope.
- Failure containment: `conclude_discussion` raising must not orphan the
  decision (the executed/rejected record outranks the resume, same as every
  path) — on error, fall through to today's rest-at-AWAITING_OPERATOR.

## Seam 3 — `declare_gap` stale_knowledge rejects work-item doc ids

**Today:** a `stale_knowledge` gap validates `doc_id` against
`repo.get_skill_doc` only — so the steward, whose contradictions are found
against INGESTED DOCUMENTS (ledger `work_item.kind='document'`), cannot cite
the very document that contradicts the graph. This was the recorded reason
contradiction flags ride as graph proposals instead.

**Repair:** accept a `doc_id` that resolves EITHER as a skill doc OR as a
document-kind work item (whatever `POST /api/documents` mints as the item id —
verify the id shape from `ingest/ledger.py` rather than assuming). The refusal
message names both acceptable citations. Nothing else changes: the gap stays
ungated, the steward's EVIDENCE-mode confidence flow is untouched, and
`declare_gap(stale_knowledge)` remains an alternative to (not a replacement
for) a contradiction graph proposal.

## Explicitly NOT in this slice

- **`graph.add_episodes` batching** — no customer until the
  Confluence/OpenSearch adapters land (air-gapped work deployment); the
  `work_item.kind` seam is where crawl volume arrives. Stays on the record.
- **Fold-into-open-lane** (deferral UX) — separate recorded slice.
- **Deferred `ask` dropped on a decision resume** — new entry on the known-
  seams record, same family as seam 2, no story pressing on it yet.

## Tests (offline suite; standing pins apply)

1. Answering a discussion-linked question from the Inbox closes the lane:
   session terminal, `conversation.ended` emitted, transcript's last turn is
   the operator's answer, item ANSWERED, task resume still scheduled.
   A plain (unlinked) question item behaves exactly as before.
2. Answering must NOT close a lane that is mid-turn or already closed
   (idempotent, no exception).
3. Approve-with-deferred-conclude: FunctionModel that, on resume after
   approval, defers `conclude_discussion`; assert the lane goes terminal
   concluded, `agent.discussion_concluded` emitted with the summary check,
   session NOT left AWAITING_OPERATOR, and the proposal is EXECUTED regardless.
4. The same deferral on a non-discussion conversation keeps today's rest
   behaviour.
5. `declare_gap` stale_knowledge: accepted with a document work-item id;
   accepted with a skill doc id (regression); refused with an unknown id.
6. Wire: `closedReason` reaches the composer state for the inbox-answered
   close (extend the existing backend wire test only if a new reason value is
   introduced).
7. Standing pins: demo_mode where `resolve_model()` is reachable; own-rows
   assertions; `needs_pg` guards.

## Migration & deployment

No schema change expected (the `discussion_session_id` column and work-item
tables exist). If the implementer finds one is needed, stop and say so.
Deploy: restart `cc-uvicorn`; web rebuild only if any cockpit file changes.

## Acceptance

- Full `pytest` green (575 baseline + new tests), web suite at its 22-baseline.
- `verify.sh` 16/16 after restart.
- STATUS.md: the two lane seams struck from known-seams, the declare_gap
  deferred-option struck, the new deferred-`ask` seam recorded; JOURNAL entry
  appended; commit + push `master`.
