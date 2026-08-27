# Trust-tier graduation — the ladder, generalized

_Design record written 2026-07-30, extracting the auditor's
shadow → evidence → operator-flips-a-class ladder into a named, reusable
mechanism. Like the two 2026-07-29 doctrine records, this fixes the decided
shape; each customer gets its own plan when its turn comes. The evidence base
is the three ladders already in the codebase — this record generalizes from
built, operator-tested mechanism, not from literature._

## Why this record exists

Three independent mechanisms grew the same shape without a name:

1. **The auditor (D8/D12, GRADUATED 2026-07-20)** — `gateway/auditor.py`:
   shadow verdicts on dismissals → `pair_verdicts` four-quadrant agreement
   report → `CC_AUDITOR_MODE=active` graduated exactly one action class
   (`dismissal.confirm`), auto-confirming only CONCUR.
2. **The steward's confidence (EVIDENCE mode, 2026-07-29)** —
   `gateway/steward_agreement.py`: a typed confidence on every proposal →
   `pair_confidence` (high+rejected = the dangerous quadrant) →
   `GET /api/steward/agreement` as the graduation record for a future
   bulk-approval threshold.
3. **D7 auto-assign (open)** — `runtime/orchestrator.routing_report`:
   `task.recommended` paired with `task.assigned` (accepted vs overridden) →
   `GET /api/routing/agreement`; the graduation call is deliberately still
   the operator's.

Each was designed by analogy with the previous one (steward_agreement's own
docstring says "modelled on gateway/auditor.py"), but the ladder itself was
never written down — so the next customer would re-derive it again, and
nothing stops a future mechanism from skipping a rung. The framework's core
bet — human-supervised autonomy where trust is EARNED per capability, not
granted per agent — deserves its load-bearing mechanism as doctrine.

## The ladder in one paragraph

**Autonomy is graduated per ACTION CLASS, on paired evidence, by an operator
flipping configuration — never by code, never by an agent, and never for an
agent as a whole.** A mechanism climbs three rungs: (0) it states its judgment
on the record while every gate stays human; (1) the control plane pairs each
stated judgment with the operator's eventual decision on the same object,
fresh from the event log, and names the disagreements case by case; (2) the
operator, reading that record, flips one named class to auto-handle only the
agreeing direction — instantly revocable, with every other case still
degrading to the human gate. Disagreements are never waste: they are the
coaching inputs for the judging agent.

## The rungs, precisely

### Rung 0 — judgment on the record (shadow / evidence mode)

The system states a typed judgment about an object the operator will decide
anyway. Rules, all three ladders already honour them:

- **Typed, not prose.** A verdict enum, a confidence level, a named agent —
  something a pairing function can bucket without interpretation
  (`AuditVerdict`, `confidence.level ∈ {high, medium, low}`, a roster id).
- **Before the operator's decision**, so the pairing is honest — a judgment
  stated after the outcome is worthless as evidence.
- **Independently derived.** The auditor re-derives from the RAW email, never
  the triage agent's summary. A judgment laundered from the actor's own
  account pairs the actor with itself.
- **On the event log** (`audit.verdict`, `proposal.confidence` on the row +
  `proposal.decided`, `task.recommended`) — the log is the record; nothing
  cached, nothing restated.
- **Absent is not low.** An unstated judgment is counted separately
  (`no_confidence_stated`, auditor-disabled items), never defaulted into a
  bucket.

### Rung 1 — the agreement record

A PURE pairing function over event rows (`pair_verdicts`, `pair_confidence`,
`pair_routes` — testable without a database), wrapped by a fresh-computed
report endpoint. Conventions:

- **Four quadrants, two of them named hazards.** Agreement in both
  directions; the noise quadrant (over-cautious challenge, under-confident
  low, an override the operator was happy to make) costs one extra human
  look; the DANGEROUS quadrant (auditor miss, overconfident high, a wrong
  auto-assignment) is what graduation would have silently let through. The
  report lists the dangerous cases INDIVIDUALLY with human-readable labels
  (subjects, intents, titles) — the operator reviews cases, not a rate.
- **Scoped per agent and per action class.** A threshold graduates one
  agent's one class; mixing agents lets a chatty record stand in for a quiet
  one (steward_agreement's own rule).
- **Last decision wins** across redraft/reopen cycles; one pair per round.
- **Computed fresh every call.** A reliability claim beside a gate is never
  cached — the log is the truth.

### Rung 2 — the operator flips a class

- **Graduation is configuration, never code** (`CC_AUDITOR_MODE=active`), and
  the flip names ONE action class (`AUDIT_ACTION_CLASS = "dismissal.confirm"`).
  Anything else the mechanism ever covers gets its own class name, its own
  shadow period, its own revocable flag.
- **Smallest blast radius first.** dismissal.confirm graduated first because
  a wrong concur costs a missed email review, never a world change. The same
  test picks every future first class.
- **Auto-handle only the agreeing direction.** Active auditor confirms only
  CONCUR; a future steward threshold batches only HIGH-confidence; auto-assign
  would act only on a clean recommendation. Every challenge, every
  uncertainty, every error DEGRADES TO THE HUMAN GATE — the graduated path
  can never wedge the flow and can never widen its own scope.
- **Revocation is one config flip** and the human gate returns exactly as it
  was.

### The standing consequences (not a rung — always on)

- **Disagreements are coaching signals.** `signals_from_report` /
  `signals_from_routes` turn the two disagreement quadrants into citable
  coaching inputs (event-id-backed, `claim_supported`-checkable). The ladder
  and the coaching loop are one system: the operator's decisions teach the
  judge that wants their trust.
- **The record keeps accumulating after graduation** — but honestly:
  auto-handled cases carry NO operator signal and are tallied separately
  (`auto_confirmed`), never counted as agreement.

## The named blind spot: post-graduation evidence starvation

Once a class graduates, the agreeing direction stops generating pairs — the
auditor's `auto_confirmed` tally is exactly this. The record that justified
the flip goes quiet for the flipped class, so drift is invisible until a
disagreement happens to surface elsewhere. **Designed answer, deferred until
a class is actually starved:** operator-sampled spot checks — route a small
fixed fraction of graduated-class cases through the human gate anyway,
labelled as samples, so pairs keep trickling. Not built for the auditor
because the operator still sees every auto-confirm in the governance screens
and the class's blast radius is one missed review; build it before any class
whose blast radius is a world change.

## What graduation is NOT

- **Not per-agent trust.** No agent "earns autonomy" — a CLASS of its output
  earns a bypass, with the record to show for it. The uniform-agent-management
  rule holds: same ladder for every agent, deviations recorded.
- **Not delegation of the gate to another agent.** The auditor's concur is
  accepted only in the class the OPERATOR flipped; an agent's judgment never
  widens another agent's permissions.
- **Not a threshold the system tunes.** The approval-rate numbers inform the
  operator; nothing in the code moves a threshold on its own.
- **Not retroactive.** Graduation changes the handling of FUTURE cases;
  decided records are never reinterpreted (the no-backfill rule).

## Customers, in decided order

1. **D7 auto-assign** (nearest): rungs 0–1 are built
   (`/api/routing/agreement`); rung 2 needs a config flag + the assign path
   honouring it for clean recommendations only. Waits on the operator judging
   the override record sufficient — the call stays the operator's.
2. **Steward bulk approval**: rungs 0–1 built (`/api/steward/agreement`);
   rung 2 is a per-level threshold + the bulk-digest UX from the
   knowledge-layer record. First candidate for pre-graduation spot-check
   design, because its blast radius is a graph write.
3. **Document-dismissal auditing**: the auditor's second class, when document
   flow warrants it — new class name, new shadow period, per this record.
4. **EA digest quality** (speculative): if digest misses accumulate as
   coaching material, the same pairing shape applies; no rung-2 ambition —
   the EA's output is conversation, not a gate.

## What this record does NOT license as shared code

The three pairing functions pair DIFFERENT things and are ~100 pure lines
each; a generic "ladder framework" abstraction would trade three readable
functions for one configurable one — the wrong trade at n=3. What IS shared
is the ladder's conventions (this record) and, when a fourth report endpoint
appears, a common wire shape for the cockpit to render agreement records
uniformly — decide then, not now.
