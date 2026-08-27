# Outage equivalence — failures cost latency, never work

_Design record written 2026-07-31, from the operator's requirement stated after the
first live provider outage touched a decision flow: **"outages/failures can
cause interruptions/delays, but once the underlying issue has been resolved,
we expect the eventual performance to be identical as though no outage had
ever occurred."** This record fixes the decided shape; implementation slices
get their own plans. It applies to every dependency: the LLM provider chain,
the n8n façades, Graphiti/Neo4j, Jira, and the stores themselves._

## The property, named

**Outage equivalence:** for every unit of work the system accepts, a
dependency outage may delay the unit but must not (a) lose it, (b) degrade
its eventual result, or (c) require the operator to re-create it. When the
dependency returns, the system converges to the exact state a no-outage run
would have produced, by itself.

The evidence base for "this is not yet true" is 2026-07-31: a LiteLLM
cooldown 429 during a re-fired morning report was recorded loudly and lost
nothing (the heartbeat's windowing already has the property) — but the same
429 during a decision resume closes the drafter's session with its final
turn lost, during an Executor write turns an APPROVED proposal terminal
FAILED, and during a dispatch run fails the work item out of the queue. Same
transient cause, three different permanent outcomes.

## Where the system already has the property (keep these shapes)

- **Heartbeat contacts** — `ea.contact` windows from the newest
  `contact_delivered` event; an undelivered contact leaves the window open
  and the next fire covers the gap. Losslessness by WINDOWING, no retry
  queue needed. This is the house style: prefer designs where the next
  natural occurrence absorbs the gap over explicit retry machinery.
- **Parked proposals and questions** — the durable pause IS an outage-proof
  queue; a decision can arrive days later.
- **Ledger enrollment** — idempotent on message_id; re-feeding is safe.
- **Restart recovery** — `resume_sweep` re-drives parked sessions after a
  process death.
- **The LiteLLM fallback chain** — provider-level resilience, below the
  spine. (Its servability currently depends on the operator's token-bound
  choice; see "Interaction with fail-fast" below.)

## The missing primitive: a failure taxonomy

Every seam that touches a dependency must classify failures into exactly two
kinds, at the seam, from the error itself:

- **TRANSIENT** — the dependency was unavailable or overloaded: connection
  refused/reset, timeouts, HTTP 429/5xx, LiteLLM cooldown/no-deployments,
  Neo4j/Graphiti unreachable, n8n "database not ready". The request was
  never semantically judged. These may NEVER produce a terminal failure
  state; they produce a RETRYABLE one.
- **SEMANTIC** — the dependency answered and said no: validation errors,
  4xx application errors, a transition that does not exist, a malformed
  proposal. Exactly today's behaviour: loud, terminal, coachable.

One classifier, one module (contract tier or a leaf util — both runtime and
gateway need it; runtime may not import gateway), re-derived-from-source
tests in the style of `NON_ADVISORY_TOOLS`. When in doubt, classify
UNKNOWN → treat as semantic (loud) — misfiling a real error as retryable
hides it, which is the worse direction (no-action-is-a-claim).

## The retry spine (rungs, per surface)

1. **Retryable states, not terminal ones, for transient failures:**
   - Decision resume fails transiently → the session parks as
     **AWAITING_RESUME** with the pending deferred result (Executor outcome
     text / rejection feedback) stored ON the session row. The durable-pause
     machinery already stores everything else needed.
   - Executor action fails transiently → proposal **RETRY_PENDING** (new
     status), never FAILED; the decision record and any completed actions
     stand; re-execution resumes from the first incomplete action.
   - Dispatch/task run fails transiently → the work item returns to
     **UNPROCESSED** (the claim releases, exactly as a fold release works);
     the task returns to its pre-run status rather than FAILED.
   - Consult sub-run fails transiently → today's degradation text stands IF
     the caller can proceed; the caller's own run failing transiently is
     covered by the unit above it.
2. **One retry sweeper, on the heartbeat** (`retry.sweep`, a new closed-
   registry action): finds AWAITING_RESUME sessions, RETRY_PENDING
   proposals, and released-for-retry items; retries OLDEST-FIRST with
   per-unit attempt counts and exponential backoff caps. Behind a
   **health gate**: probe the dependency cheaply first (LiteLLM `/health`-
   class check, a Graphiti ping, an n8n HEAD) and skip the sweep while the
   dependency is still down — never burn attempt budget into a dead
   provider. Errors from the sweep are material; a successful no-op sweep
   writes nothing (the quiet-heartbeat rule).
3. **Attempt exhaustion is a promotion, not a loop:** after N failed
   retries the unit converts to today's terminal state (FAILED) plus an
   operator item — the operator learns the outage outlived the system's
   patience, with one click to re-arm. Nothing retries forever silently.
4. **Idempotency becomes load-bearing:** proposals already carry
   `idempotency_key`; retried Executor actions must dedupe on it end-to-end
   (the Jira façade and graph writes must tolerate a replay of a write that
   half-landed). Any action that cannot be made idempotent must record
   completion per-action (the Executor's `completed` list already exists)
   so a retry resumes from the first incomplete action, never from zero.

## Boundaries stated honestly

- **A live turn is not rewindable.** If a READ fails mid-turn, the v1
  answer stays: bounded in-call retry (short, e.g. 2 attempts), then honest
  degradation text — plus the new option of the AGENT declining to proceed
  ("the graph is unreachable; I would be guessing") and the UNIT then being
  requeued. Perfect mid-turn equivalence is within-run checkpointing — the
  DBOS line, still deferred, unchanged by this record.
- **Model-answer nondeterminism.** "Identical eventual performance" means
  identical OPPORTUNITY: the retried run gets the same inputs and full
  toolset a no-outage run would have had. It does not mean bit-identical
  output; nothing in the system ever promised that.
- **The operator's own actions never retry silently.** An approval whose
  execution parks RETRY_PENDING is visible in the Inbox as "approved,
  execution pending retry" — the decision stands; the world-change is
  what's queued. No re-approval, no double gate.

## Interaction with fail-fast (the 2026-07-30 token-bound decision)

The operator chose the full 262144 bound over a servable haiku fallback —
fail rather than degrade. Under outage equivalence that choice strengthens:
fail-fast becomes the CORRECT front half of the design, because a fast
transient failure lands the unit in a retryable state at full quality,
instead of a degraded success that can never be retried. The two decisions
compose: degrade never, delay freely, converge always.

## Delivery slices (each gets its own plan; decided order)

1. **Taxonomy + classifier + tests** — no behaviour change; every seam
   labels failures (events gain a `transient: bool`), so the record starts
   accumulating before anything retries.
2. **Decision-resume parking (AWAITING_RESUME)** — the smallest blast
   radius and the one that bit first; includes `resume_sweep` learning to
   drive it.
3. **Executor RETRY_PENDING + idempotent replay** — schema + status + Inbox
   rendering ("approved, pending retry"); the biggest honesty win.
   _Landed 2026-07-31. The replay is CURSOR-based, not key-based: the
   Executor's `completed` list is the cursor, so an action it saw succeed never
   re-runs. The residual, recorded rather than hidden — the single in-flight
   action whose call failed AFTER the façade may have applied it is retried and
   may double-apply for a non-idempotent capability. Rung 4 (façade-level
   idempotency keys on `proposal.idempotency_key`) is what closes it, and stays
   deferred until a façade can actually honour one._
4. **Dispatch/task requeue on transient** — claim release + task status
   restore.
5. **`retry.sweep` heartbeat action + health gates + exhaustion promotion**
   — turns the parked states from 2–4 into automatic convergence.
6. **Read-path bounded retries** — smallest value, last.

## What this record does NOT change

- The trust boundary and the gate: nothing retries its way past approval.
- The emit-before-authorise ordering; retry attempts are bookkeeping events.
- The append-only log: a retried unit's history shows the outage — the
  RECORD is never made to look as though no outage occurred; only the
  OUTCOME is.
- The heartbeat windowing designs that already deliver the property —
  no retry machinery replaces a working windowing shape.
