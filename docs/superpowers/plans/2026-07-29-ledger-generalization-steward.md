# Plan — ledger source-generalization + knowledge-steward, slice 1 (delivery item 3)

_Plan written 2026-07-29 under the knowledge-layer routing record. Scope set by
the architect: sources = email (existing) + hand-fed DOCUMENT only (Confluence
and OpenSearch are air-gapped at work and unreachable from the Pi — the source
seam is designed for their adapters, none built). Uncertainty triage ships in
EVIDENCE mode: confidence recorded on every proposal, everything still
human-gated; bulk approval graduates later on the accumulated
confidence-vs-verdict evidence, the auditor's ladder applied to knowledge
writes._

## Decisions (planning findings behind each are in the journal entry)

1. **`work_item.kind` (default `'email'`), never overloading `source`** —
   `source` means transport (`fixture|api|gmail`) and three code sites + a
   test cleanup depend on that meaning. Additive DDL, hand-applied to live
   `cc-postgres` in the same change.
2. **Routing is a handler registry in `dispatcher.process_claimed`** —
   `{"email": _handle_email, "document": _handle_document}`; one branch in the
   one path (M9 rule intact); unknown kind fails the item loudly, never
   defaults to triage. Dismissal/fold actors parameterised per handler.
3. **The steward is HIRED, not seeded**: a `knowledge-steward` HireTemplate +
   idempotent `ensure_registered()` (the `jira_expert`/`auditor` pattern) so a
   fresh database reproduces it without touching the founding-seed guards
   (the exact pattern that shipped a broken roster once). Roster:
   `taskable=False` (recorded deviation: work arrives via ledger claims, as
   inbox-triage's does), `consultable=False` for now. Packs: `graph-read`,
   `graph-propose`, `consult`, `ask-operator`. NO `task-propose` in slice 1.
4. **Confidence is a typed optional contract field** (`Proposal.confidence`:
   level high|medium|low + rationale), persisted in a new `proposal.confidence`
   jsonb column, rendered as an Inbox badge — with the mandatory backend
   wire-shape test. Never args-only: unvalidated free-dict confidence is how
   "pretty sure" becomes data. Charter states the spec's asymmetry: stricter
   on relations/edges than entities.
5. **Steward citations flow through `contract.claim_supported()`** against
   `work_item.payload["text"]` — the Inbox's unverified-quote badge works with
   zero cockpit code. `claim_matches_source: None` stays NOT-CHECKED for
   evidence kinds not verified.
6. **Contradictions are graph PROPOSALS carrying doc_id + doc_says + observed**
   in episode body and evidence — NOT `declare_gap(stale_knowledge)`, which
   validates `doc_id` against skill docs and would refuse every steward gap at
   the tool boundary (found in planning; widening `declare_gap` to work-item
   ids is the recorded deferred option).
7. **No auditor on document dismissals** — the auditor is graduated on the
   email `dismissal.confirm` class specifically; extending by omission would
   be an ungoverned graduation. Document dismissals still require the operator
   confirm.
8. **One proposal = one episode**; the batching seam (`graph.add_episodes`
   registry entry) is named for the Confluence-volume future, not built.
9. **Documents set `thread_id = message_id`** — thread lock still serialises
   re-feeds; fold machinery untouched. Document idempotence key:
   `<cc-doc-{sha256(title+NUL+text)[:24]}@central_command.local>`, or
   `<cc-doc-{doc_id}@…>` when the caller supplies a stable id (the future
   Confluence page-id key).
10. **Hand-feed API mirrors `feed_email`**: `POST /api/documents` (enroll →
    `dispatch_item`, never a direct run — M9) + `POST /api/ingest/document`
    (enroll-only). Demo mode gets purpose-built steward spike models (extract
    + no-extraction) so the offline suite walks the whole path.
11. **The agreement report ships** (`steward_agreement_report` modeled on the
    auditor's, confidence-vs-verdict, at `GET /api/steward/agreement`) — the
    plan's own risk register says confidence without it is decoration. It is
    the designated cut ONLY if the slice genuinely inflates, and the cut must
    be reported, not silent.
12. Reviewer pane: `kind` joins the work-item wire shape so the source pane
    can say "Source document" instead of "Source email" (backend-sends-first,
    then types.ts).

## Acceptance

- Full `pytest` green (493 baseline + new ledger/dispatcher-routing/steward/
  documents/confidence tests; ledger tests on real Postgres invariants, never
  mocked; every test near a model pin `demo_mode=True`; the source-walk guard
  in `test_proposal_created.py` must pass over the new module).
- Web suite: 22-failure baseline unchanged; `npm run build` green.
- Live Pi: both DDL statements hand-applied in the same change; steward hired
  (`ensure_registered` run once); `verify.sh` green; a hand-fed test document
  produces a gated `graph.add_episode` proposal with a confidence badge in
  the Inbox.
- STATUS.md roster/queue facts updated; JOURNAL entry; the `declare_gap`
  deferred option recorded.
