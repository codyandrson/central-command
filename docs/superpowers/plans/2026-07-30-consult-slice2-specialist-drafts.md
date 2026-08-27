# Plan — consult slice 2: the specialist drafts the proposal (doctrine Decision 2)

_Plan written 2026-07-30 under the teaming & consultation doctrine record.
Slice 1 (read-only consult) is built and live. This slice lets a consulted
specialist draft and park the gated proposal ITSELF, with the consult chain in
provenance, so operator rejections coach the drafter — the agent that owns the
conventions — instead of the asker that transcribed them._

## Why the shape below (evidence from the code, not recalled)

- Propose tools end a run by raising `CallDeferred`; the control plane parks
  the deferred call via the ONE seam `runtime/proposals.park_proposal()`,
  which persists the paused run state under a session row
  (`repo.save_paused_session` upserts the row, status `AWAITING_HUMAN`) and
  emits `proposal.created`.
- All three decision paths (`approve_and_execute`, `_record_execution_failure`,
  `reject_with_feedback`) resume via `build_agent_for(prop_row["agent_id"])` +
  `load_paused_session(session_id)`. **Drafter ≠ subject is already spine
  doctrine** (the coach precedent): a proposal's `agent_id` is the DRAFTER, and
  the Executor takes the proposer from the gateway, never from args.
- Therefore the specialist's proposal must park under a session row of the
  specialist's OWN — that is what makes the rejection→redraft loop resume the
  specialist and route coaching signals to it. Slice 1's "a consultation opens
  no session row" stays true for pure-advice consults; a consult that parks a
  proposal graduates to a real (specialist-owned) session.

## Target shape

### 1. The advisory sub-run gains the specialist's propose packs

- `packs.NON_ADVISORY_TOOLS` shrinks to the tools whose deferral needs a host
  the consult sub-run cannot be: `consult_agent` (depth-1/cycle-proofing —
  unchanged), `ask_operator`, `conclude_discussion`, `submit_plan`,
  `assign_work`. **`propose_jira_update` and `propose_litellm_change` leave
  the set** — the consult driver (below) now IS their control-plane driver.
- `packs.advisory_packs()` drops the `if pack.capabilities: continue` filter;
  the only filter left is NON_ADVISORY tool membership. Pack-whole-or-drop
  stays (guidance travels with tools). Consequence: whichever propose packs
  the specialist is GRANTED ride into its consults — uniform, no special
  cases; everything is operator-gated regardless.
- `consult.build_advisory_agent`: `output_type` becomes
  `[str, DeferredToolRequests]` when the advisory packs carry a deferral tool
  (str-only otherwise, preserving slice-1 behaviour for read-only loadouts).
- `_ADVISORY_FRAMING` rewritten: the specialist may and should draft the gated
  proposal itself when one is warranted — it owns the conventions; drafting
  ends the consultation and parks the proposal for operator review; the asker
  is told what was proposed. The framing stays the ONE place the sub-run rules
  live (never edit governed charters to say this).
- `consult_agent` tool docstring: "leave the proposing to them" wording dies;
  the caller is told the specialist may park a proposal and that the returned
  text will name it — the caller must NOT draft its own proposal for the same
  change.

### 2. `consult()` becomes the deferral driver

On a sub-run ending in a deferred call (`extract_deferred`):

- Mint a fresh `sess_<uuid12>` for the SPECIALIST and park through the one
  seam: `park_proposal(session_id=<new>, agent_id=<specialist>, result, call,
  actor=f"agent:{specialist}", context={"consulted_by": caller_id,
  "caller_session_id": caller_session_id}, origin=<same dict>)`. The
  `repo.save_paused_session` upsert creates the session row (mode stays
  `oneshot`; no new mode, no new `why_not_sendable` code — the pending
  proposal already disables the composer via existing codes, keeping us out of
  the `_COMPOSER_DISABLING` allowlist trap).
- Return synthesized TEXT to the caller (the consult tool contract stays
  `-> str`): the specialist drafted proposal `prop_…` — intent, one-line
  expected effect, "awaiting operator review; do not draft your own proposal
  for this change."
- `agent.consulted` event payload gains `proposal_id` (null for pure-advice
  consults).
- The usage accumulator still rides the caller's run for the sub-run itself;
  the post-decision resume of the specialist's session runs on its own usage,
  like every other resume.

### 3. Provenance: `origin` on the proposal row and the decisions wire

- `proposal` gains a nullable `origin jsonb` column
  (`{"consulted_by": …, "caller_session_id": …}`), written only by this path.
  `park_proposal` gains an `origin=None` param → `repo.save_proposal`.
- The decisions wire carries `origin`; the Inbox renders a
  "drafted by <specialist> · asked by <caller>" chip. **A BACKEND test asserts
  the wire shape** (the cockpit hand-declares its interfaces over untyped RPC
  — a field the gateway never sends is invisible to tsc and every frontend
  test; 2026-07-27 bite-mark ×2).
- `proposal.created`'s payload carries the same fields via `context` — event
  and row agree.

### 4. Seam repair folded in: `reject_with_feedback` non-proposal deferrals

Recorded 2026-07-30 as a known seam: the reject path hands ANY redraft
deferral to `proposal_from_call`, so a non-proposal tool reached mid-redraft
raises out of the gateway. This slice WIDENS exposure — a resumed specialist
runs under `build_agent_for` with its FULL toolset (incl. `ask_operator`), so
the seam is repaired here rather than left queued: the reject path gains the
same guard the `converse`/`run_task` paths got (a non-proposal deferral is
answered/declined mechanically with an explanatory tool result and the resume
continues, never an unhandled exception). Same fix applied to
`_record_execution_failure`'s redraft branch if inspection shows it shares the
gap.

## What does NOT change

- The trust boundary: `runtime/` still never imports `gateway/`; the consult
  driver calls only `park_proposal` (runtime tier) — parking authorises
  nothing, so no emit-ordering question arises (`proposal.created` after save
  is the established, documented order).
- Read-only consults: no session row, text answer, unchanged bounds
  (`ANSWER_CEILING`, `REQUEST_LIMIT=10`, depth-1 by toolset construction).
- Demo mode's advisor stays the text-answer model; deferral plumbing is
  tested with explicit FunctionModels.
- Governed charters: untouched (framing lives in `_ADVISORY_FRAMING`).
- Pack count and grants: unchanged — no migration of grants.

## Tests (offline suite; bite-marks apply)

1. `advisory_packs` for a jira-expert-shaped grant set now includes
   `jira-propose`; still excludes `consult`, `ask-operator`, `orchestrate`.
2. The re-derivation test (`test_non_advisory_tools_is_re_derived_from_the_tools_source`)
   updates its invariant: every CallDeferred-raising tool is either in
   `NON_ADVISORY_TOOLS` or in the explicit advisory-deferral allowlist, and an
   advisory agent whose packs carry an allowed deferral tool has
   `DeferredToolRequests` in its output type.
3. Consult-parks-proposal (FunctionModel emitting a propose call): proposal
   row exists with `agent_id=<specialist>`, a FRESH `AWAITING_HUMAN` session
   row owned by the specialist, `origin` populated, `proposal.created` emitted
   with the consult context, and the CALLER's `consult_agent` call returns
   text naming the proposal id without raising.
4. Rejection coaches the drafter: `reject_with_feedback` on a consult-parked
   proposal resumes the SPECIALIST; a redraft parks in the same session under
   the specialist (drafter ≠ asker end-to-end).
5. Wire shape: the decisions list carries `origin` (backend test, real
   payload, never a frontend-built object).
6. Seam repair: a non-proposal deferral surfacing mid-redraft on the reject
   path resolves mechanically instead of raising.
7. `test_proposal_created.py`'s source walk stays green (consult parks via the
   seam — no new `repo.save_proposal` call site).
8. Standing pins: `demo_mode=True` wherever `resolve_model()` is reachable;
   `dispatch_approval_limit` pinned open in any test that drains; assert on
   rows the test created, never global counts.

## Migration & deployment (schema.sql is fresh-only)

1. `db/schema.sql`: `alter table proposal add column if not exists origin jsonb;`
2. Hand-apply the same statement to the live `cc-postgres` **in the same
   change** (idempotent), then restart `cc-uvicorn`; rebuild web and restart
   `cc-nerve` for the Inbox chip.
3. `verify.sh` 16/16 after restart.

## Acceptance

- Full `pytest` green (569 baseline, plus this slice's tests, 0 failed).
- Web suite: 1,619+ passed, only the 22 documented vendored-baseline failures.
- STATUS.md: consult slice 2 moved from "queued" to built; the
  `reject_with_feedback` seam struck from the known-seams list; JOURNAL entry
  appended. Commit + push `master` in the same session.
- Live-Claude proof (a real consult that parks a real proposal) is a
  behaviour claim demo mode cannot validate — recorded as a validation click
  for the operator, not an acceptance gate.
