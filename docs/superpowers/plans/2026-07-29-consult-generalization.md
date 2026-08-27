# Plan — generalize consultation: `consult_agent` (doctrine Decision 1, slice 1)

_Plan written 2026-07-29 under the teaming & consultation doctrine record.
Slice 1 is the READ-ONLY consult: any granted agent may ask any consultable
ACTIVE agent a question and get a distilled text answer. Specialist-drafts-
proposals (doctrine Decision 2) is deliberately the NEXT slice — it needs the
consult sub-run to hold propose tools and park under the specialist's
identity, and the read-only consult should be proven first._

## Target shape

- **Tool:** `consult_agent(agent_id: str, question: str)` replaces
  `consult_jira_expert`. Target resolution at call time: roster row must be
  ACTIVE and `consultable=True`, else a mechanical refusal naming the
  consultable roster (so the model can retarget rather than hallucinate).
  Self-consult refused.
- **Pack:** `jira-consult` is renamed to **`consult`** — same shape, generic
  tool, guidance rewritten ("ask a specialist so its context is spent instead
  of yours; the answer is advisory; gated work still goes through proposals").
  Pack count stays 12.
- **Specialist sub-run:** generalizes `jira_expert.advise()` — build the
  target from its GOVERNED charter (the fresh-run rule: load the charter;
  `build_agent_for` is the resume factory and must not be used) with its
  **read-only** toolset (its granted packs minus every gated/propose tool and
  minus `consult` itself — depth 1 by construction, cycles impossible), on the
  parent's usage accumulator with `UsageLimits(request_limit=10)`, answer
  size-capped to the existing deployment-ceiling text bound.
- **Event:** `agent.consulted` emitted with `actor="agent:<real caller>"`
  (kills the hardcoded `agent:inbox-triage` at `runtime/tools.py:335`),
  payload carrying target, question, answer length, and usage.
- **No session row** for the consult (unchanged from today); the event is the
  record.

## Migration (pack id changes; grants reference pack ids)

1. `runtime/packs.py`: rename pack, generalize tool; `DEFAULT_PACKS` fallback
   updated.
2. `db/schema.sql`: seed grants say `consult`; add an idempotent migration
   statement (update `agent_grant` rows `jira-consult` → `consult` where not
   revoked) so a FRESH database and the live one converge on the same truth.
3. Live Pi: apply the same UPDATE by hand against `cc-postgres` **in the same
   change** (the schema.sql-is-fresh-only bite-mark), then restart
   `cc-uvicorn`.
4. Charters/templates/guidance mentioning `consult_jira_expert` by name:
   generated capability lists cover the packs; grep for the literal name in
   profiles/templates and update wording (guidance-needs-a-mechanism rule —
   never promise a tool that doesn't exist).

## Tests (offline suite)

- Refusals: non-consultable target, RETIRED target, unknown target, self.
- Sub-run toolset: no `consult` tool, no propose/gated tools present.
- Event: emitted with the real caller as actor (regression for tools.py:335).
- Answer size cap enforced.
- Governance parity (`tests/test_governance.py`) still green — consult stays
  ungated (it authorises nothing; text advice only).
- Pin `demo_mode=True` anywhere `resolve_model()` is reachable; assert on rows
  the test created, never global counts.

## Acceptance

- Full `pytest` green (476+ baseline).
- On the Pi: grants migrated (`select agent_id, pack from agent_grant where
  revoked_at is null` shows `consult`, no live `jira-consult`), service
  restarted, `verify.sh` 16/16.
- STATUS.md pack facts updated in the same commit; JOURNAL entry appended.
