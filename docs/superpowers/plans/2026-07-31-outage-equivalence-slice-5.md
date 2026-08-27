# Plan — outage equivalence slice 5: `retry.sweep` on the heartbeat, behind health gates

_Plan written 2026-07-31. Turns the parked states from slices 2–4 into
automatic convergence. Depends on all three landing first._

## What sweeps, and what deliberately doesn't

- **Sweeps:** AWAITING_RESUME sessions (slice 2), RETRY_PENDING proposals
  (slice 3), due retry-parked tasks (slice 4) — all driven through the
  EXISTING drivers (`resume_parked_session`, `retry_execution`,
  `retry_parked_task`); the sweep adds cadence, never a second driver.
- **Does NOT sweep:** transiently-released ledger items — they self-converge
  through the normal dispatch loop (`not_before` backoff + mail-poll/drain
  cadence). One convergence path per unit kind; two would race.

## The action

`retry.sweep` joins the heartbeat's closed action registry
(`heartbeat/actions.py`), kind-checked like `ea.contact`:

- Iterates the three worklists oldest-first, calling each driver; collects
  `{resumed, retried_executions, retried_tasks, skipped_gated, errors}`.
- **Material only when it DID something or errored.** A sweep that found
  nothing parked (the overwhelmingly common tick) writes NOTHING — the
  2026-07-25 heartbeat-noise rule. It is non-authorising (everything it
  drives was authorised before it parked) and self-recording (each driver
  emits its own retried/parked/exhausted events), so it qualifies for the
  material allowlist — add it there WITH the parity test extension, per the
  allowlist's own rule.

## Health gates — the part that protects the attempt budget

**The problem they solve:** attempt limits (5 per unit) exist to stop silent
infinite loops, but a long outage must not burn them — a 2-hour outage with
a 5-minute cadence would exhaust every parked unit in 25 minutes and promote
a storm of operator items, the opposite of "wait as long as the outage
lasts". So: **a gated skip consumes NO attempt.** Attempts are spent only
when the dependency LOOKED healthy and the real call still failed — genuine
flappiness, which is what the budget is for.

Probes, one per dependency, cached per tick (`heartbeat/probes.py`):

- **LLM chain** — a 1-token completion against `cc-default` via the
  configured base URL. This is the ONE probe that costs anything (fractions
  of a cent) and it runs only when at least one LLM-dependent unit is
  parked. `litellm_check_model_health` is explicitly NOT used — its own
  docstring says it makes real provider calls per model and is expensive.
- **n8n façade** — GET the n8n health endpoint (`/healthz`; verify the
  vendored n8n's actual path before coding).
- **Graphiti/Neo4j** — the MCP server's cheapest status endpoint (verify).
- Probe FAILURE (or a probe that itself raises) → every unit gated on that
  dependency is skipped this tick, attempts untouched, and the skip count
  rides the sweep result. Probe UNKNOWN (no probe defined for a
  dependency) → attempt anyway; the attempt is the probe, bounded by the
  budget.

Gating map: AWAITING_RESUME sessions and retry-parked tasks gate on the LLM
probe (their retry IS an agent run). RETRY_PENDING proposals gate on the
probe for the FAILED capability's system (**CORRECTED in review: `task.*`
gates on the LLM, not n8n — `create_and_run_task` is a local DB write plus
an agent run; the implementer caught the plan's error**) (`jira.*` →
n8n; `graph.*` → graphiti; `litellm.*` → litellm admin; unknown →
attempt), because the blocked call is the Executor's — and additionally on
the LLM probe only if you can do it without contortions (the post-execute
resume is LLM-work, but slice 2 already parks that leg independently, so
it is NOT worth double-gating; state this in a comment).

## The schedule

`retry-sweep` seeded **ENABLED**, `every_seconds: 300` — a deliberate
deviation from the seeded-disabled house default, with the reason recorded
in the seed comment: this schedule is the convergence half of outage
equivalence; disabled-by-default would mean every outage recovery waits for
an operator click, which contradicts the doctrine's "converges by itself".
Precedent: `mail-poll` is seeded enabled for the same
class of reason. It is also a strict no-op when nothing is parked
(cheap tick, no probes fire, nothing written). The operator can still
disable it in the crons tab like any schedule.

## Config

`CC_RETRY_SWEEP_*` knobs only if genuinely needed; prefer none — cadence is
the schedule row (governed data, D27), attempt limits already exist
per-unit-kind. The probe timeout is a constant (5s) unless a reason appears.

## Tests

1. Sweep with nothing parked: returns all-zeros, emits NOTHING (parity with
   the material-allowlist test).
2. Sweep with one of each parked kind and healthy probes (monkeypatched):
   each driver called once, results tallied, drivers' own events on the log.
3. Probe down: parked units skipped, attempts UNCHANGED (read the row back),
   `skipped_gated` counted, still no event when nothing else happened.
4. Probe down + one unit errored semantically anyway (mixed tick): material,
   errors carried.
5. Allowlist parity test extended (`retry.sweep` non-authorising +
   self-recording).
6. Registry: unknown-kind refusal unchanged; `retry.sweep` fires from the
   engine loop like any action (existing engine test pattern).
7. Gating map: a RETRY_PENDING proposal whose failed capability is `jira.*`
   gates on the n8n probe, not the LLM probe.
8. Seed: fresh schema seeds `retry-sweep` ENABLED at 300s (fresh-database
   rule — the test DB proves the seed).

## Migration & deployment

Seed row in `schema.sql` (idempotent insert, follows the existing heartbeat
seed pattern) AND the same insert hand-applied to the live `cc-postgres` in
the same change. Restart `cc-uvicorn`. No web change expected (the crons tab
is registry-driven).

## Acceptance

Full pytest green; web at baseline; STATUS (slice 5 + the enabled-by-default
deviation and its reason) + JOURNAL; commit + push master.
