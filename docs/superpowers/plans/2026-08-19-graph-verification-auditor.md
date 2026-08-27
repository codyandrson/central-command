# Graph verification auditor — implementation plan

_Plan for [the 2026-08-19 spec](../specs/2026-08-19-graph-verification-auditor-design.md).
Seven tasks, in dependency order. Task 1 resolves the one empirical unknown
(invalidation attribution); everything else is assembly of proven patterns:
the dismissal auditor's verdict/gating/pairing shape (`gateway/auditor.py`),
the heartbeat action registry, and the bespoke operator-item precedent
(`DISMISS_PENDING`)._

Bite marks this plan must honour (checked against the code 2026-08-19):

- `schema.sql` is auto-loaded on a FRESH database only — apply the new table
  to the live `cc-postgres` by hand in the same change (`create table if not
  exists`, idempotent).
- Heartbeat schedules are born disabled; only `retry-sweep` has a recorded
  exception. `graph-verify-sweep` seeds disabled.
- `material` predicates may quiet only non-authorising, self-recording
  actions; errors are always material.
- `events.emit()` is the only publish path; emit a verdict BEFORE the state
  change it authorises (the M8 ordering rule, honoured by `audit.verdict`).
- The suite may not write to the live graph (`no_live_graph_writes`, both MCP
  and bolt); live validation opts in with `CC_LIVE_GRAPH_TESTS=1`, uses a
  scratch group, and cleans up (one leftover scratch entity broke an
  unrelated live read test on 2026-08-15).
- Cockpit fields need a BACKEND wire-shape test — a frontend test building
  its own payload proves nothing.
- Dry-run short-circuits in `_execute_action` before any handler runs, so a
  verification row created inside `_graph_add_episode` can never refer to a
  write that didn't happen.
- The auditor-registration precedent is `upsert_agent`, NOT `ensure_hired` —
  auditors stay off the taskable/consultable roster for gate independence
  (recorded in `ea_profile.DEVIATIONS`); the coaching loop reaches the
  charter via `repo.current_charter(AGENT_ID)` fallback.

## Task 1 — delta read-back + empirical invalidation attribution

`integrations/neo4j_reader.py` gains two read-only functions:

- `episode_by_marker(marker, group_id)` — the Episodic node whose
  `source_description` CONTAINS the marker, in the group. Returns
  `{uuid, name, created_at, group_id}` or None. Absence past the settle
  window IS the silent-drop finding.
- `episode_delta(episode_uuid)` — `{entities, edges, invalidated}`:
  entities via `(:Episodic {uuid})-[:MENTIONS]->(n)` with
  `has_embedding: n.name_embedding IS NOT NULL` and labels; edges via
  `RELATES_TO` where `$uuid IN r.episodes` (fact, valid_at, invalid_at,
  expired_at, endpoint names); invalidated = the subset/superset of expired
  edges attributable to this episode.

**The empirical question**: when Graphiti invalidates an edge during an
episode's ingestion, does that episode's uuid land in the edge's `episodes`
property, or is the only attribution `expired_at` falling inside the
ingestion window? Answered by an opt-in live test
(`CC_LIVE_GRAPH_TESTS=1`): write a scratch episode establishing a fact into
a dedicated scratch group, a second contradicting it, read back attribution,
delete the scratch group's nodes via `neo4j_writer`. The winning method
becomes `episode_delta`'s primary; the window is the fallback either way
(episodes are queued serially per group, volume is human-approved-low, so
window fuzziness is acceptable).

## Task 2 — worklist table + executor stamping

- `schema.sql`: `graph_verification` — `id`, `proposal_id`, `episode_name`,
  `group_id`, `scope`, `marker` (the stamped `proposal=<id>` token),
  `status` (`PENDING` / `AWAITING_OPERATOR` / `VERIFIED` / `PROBLEM`),
  `episode_uuid`, `mechanical` jsonb, `delta` jsonb, `verdict`,
  `verdict_rationale`, `has_invalidations` bool, `created_at`, `closed_at`,
  `closed_by`. Applied to live cc-postgres by hand in the same change.
- `repo.py`: `create_graph_verification`, `due_graph_verifications(settle)`,
  `update_graph_verification`, `list_graph_verifications`,
  `close_graph_verification` (status-guarded like `confirm_dismissal`).
- `executor._graph_add_episode`: append `| proposal=<id>` (from
  `_current_proposal_id`) to `source_description`; insert the PENDING row
  after the ack. Handler-local, so dry-run never creates a row.

## Task 3 — the sweep (mechanical layer)

`gateway/graph_auditor.py` (module named for its owner; same tier precedent
as `gateway/auditor.py`) — `verify_sweep()`:

- claims rows past the settle delay (param `settle_minutes`, default 10);
- `episode_by_marker` miss → status `AWAITING_OPERATOR`, mechanical
  `{missing: true}` — the silent-drop class, an ERROR for the operator,
  never a question for the judgment agent;
- found → `episode_delta`; mechanical checks recorded on the row: empty
  delta, entities without `name_embedding`. Mechanical failures park for
  the operator directly;
- clean mechanicals → Task 4's judgment; result drives the row's terminal
  handling per mode.

`heartbeat/actions.py`: `graph.verify_sweep` ActionSpec, levers naming the
sweep, `material=lambda r: bool(r.get("flagged") or r.get("awaiting") or
r.get("auto_verified") or r.get("errors"))` — a tick that found nothing
writes nothing. Schedule `graph-verify-sweep` seeded DISABLED, `every`/600s.

## Task 4 — the judgment agent + gating

- `runtime/graph_auditor_profile.py`: `AGENT_ID = "graph-auditor"`, NAME,
  ROLE, built-in v0 CHARTER — judgment layer only: given the approved
  episode text, the mechanical report and the rendered delta, decide
  `aligned | flag` on entities, relationships, temporals, scope, and the
  justification of each invalidation. DEVIATIONS text records the
  off-roster gate-independence exception (auditor precedent).
- `gateway/graph_auditor.py`: `GraphAuditVerdict(aligned: bool, rationale:
  str)`; `build_graph_auditor(model, charter)` mirroring `build_auditor`;
  model resolution demo→spike / live→`resolve_model(agent_id="graph-auditor")`;
  governed-charter fallback via `repo.current_charter`.
- Emit `graph.audit.verdict` (ref_id = verification id) BEFORE any close,
  payload `{action_class, verdict, rationale, mode, proposal_id,
  has_invalidations}`. `action_class` is `graph.verify` for addition-only
  deltas, `graph.verify_invalidation` when anything was retired.
- Config: `graph_auditor_enabled = False`, `graph_auditor_mode = "shadow"`.
  Sweep runs mechanicals regardless once its schedule is enabled; the
  judgment agent runs only when enabled. Shadow: every row →
  `AWAITING_OPERATOR` with the verdict attached. Active: auto-close
  (`VERIFIED`, closed_by `graph-auditor`, event
  `graph.verification.confirmed` actor `graph-auditor`) ONLY when verdict
  is aligned AND mechanicals are clean AND `has_invalidations` is false —
  the `graph.verify_invalidation` class keeps parking until it earns its
  own flag (built at ITS graduation, not now).

## Task 5 — operator routes + the agreement record

- `GET /graph-verifications` (awaiting + recent closed, with delta),
  `POST /graph-verifications/{id}/confirm` → `VERIFIED`, event
  `graph.verification.confirmed` actor `operator`;
  `POST /graph-verifications/{id}/problem` (note required) → `PROBLEM`,
  event `graph.verification.problem`. Remediation stays on the existing
  gated curation path — no fix capability here.
- `pair_graph_verdicts(rows)` — pure, over `graph.audit.verdict` +
  `graph.verification.confirmed` / `graph.verification.problem`; last
  decision wins; `auto_confirmed` tallied separately, never as agreement;
  scoped per action class. `GET /graph-audit/agreement` computed fresh;
  `signals_from_graph_report` for the two disagreement quadrants
  (`graph-audit-miss` = aligned verdict, operator found a problem).
- Backend wire-shape test asserting every field the cockpit renders.

## Task 6 — minimal cockpit surface

A Graph Verifications list (verdict chip, mechanical flags, rendered delta
— entities/edges/invalidations with the approved episode text alongside,
per source-material-inspectable), confirm / problem actions. Verified via
the backend wire-shape test and API; the operator drives the clicks.

## Task 7 — live acceptance

Enable `CC_GRAPH_AUDITOR_ENABLED` + the schedule on the Pi; approve one
scratch graph episode end-to-end; watch the row go PENDING → verdict →
AWAITING_OPERATOR (shadow); confirm it; check the agreement endpoint pairs
it. Then leave it in shadow accumulating the record — graduation is the operator's
call on the record, per the ladder.
