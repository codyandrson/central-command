"""Graph verification — mechanical read-back plus the graph-auditor's judgment.

Closes the loop the 2026-08-19 spec names: the approval gate reviews the
EPISODE, Graphiti's queued extraction runs after it, unreviewed, and the ack
is not graph state. The Executor leaves a `graph_verification` row per
approved episode (stamped with a `proposal=<id>` marker); the
`graph.verify_sweep` heartbeat action drives `verify_sweep()` here over rows
past a settle delay.

The design rules honoured, in the dismissal auditor's mold (gateway/auditor.py):

- **Mechanical before judgment.** Code asserts what code can check — the
  episode LANDED, in the intended group, produced something, and its entities
  carry embeddings. A mechanical failure parks for the operator directly and
  is never a question put to the judgment agent, so a code bug can't be
  misdiagnosed as a judgment failure or vice versa.
- **Independent derivation.** The judgment compares the APPROVED text (from
  the proposal row — the control plane's own record) against the delta read
  back from Neo4j itself, never the extractor's account of what it did.
- **Shadow-first, per action class, revocable.** `graph.verify` is
  addition-only deltas; `graph.verify_invalidation` is any delta that retired
  an existing fact — it ALWAYS parks, even in active mode, until it earns its
  own flag on its own record. Active mode auto-closes only aligned +
  mechanically-clean + addition-only. Every flag, error, or absent verdict
  degrades to the human gate.
- **The auditor is READ-ONLY.** Its graduated authority is closing a
  verification row; remediation stays on the gated curation path
  (integrations/neo4j_writer, operator-driven). A wrong ALIGNED can only fail
  to surface a problem — which is exactly what the agreement record measures
  before the operator ever flips the class.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel
from pydantic_ai import Agent

from central_command import events
from central_command.config import settings
from central_command.contract import is_transient
from central_command.db import repo
from central_command.integrations import neo4j_reader

# The two action classes, graduated separately (spec decision 6). A wrong
# auto-close on an addition costs one flawed new fact; on an invalidation it
# silently retires a fact the whole team reads — the grandfather/aunt class.
GRAPH_AUDIT_ACTION_CLASS = "graph.verify"
GRAPH_AUDIT_INVALIDATION_CLASS = "graph.verify_invalidation"

log = logging.getLogger(__name__)


class GraphAuditVerdict(BaseModel):
    """aligned = the delta faithfully represents the approved claim;
    flag = something materially diverges and deserves a human."""

    aligned: bool
    rationale: str


# The graph auditor is a TEAM MEMBER: its built-in charter lives in the
# runtime tier so the coaching loop can fall back to it, and the CURRENT
# governed version wins at audit time — the operator coaches it like any
# other agent. Registered by upsert, never hired (the auditor precedent:
# off the taskable/consultable roster for gate independence).
from central_command.runtime.graph_auditor_profile import (  # noqa: E402
    AGENT_ID,
    CHARTER as GRAPH_AUDITOR_CHARTER,
)
from central_command.runtime.templates import render_v0  # noqa: E402


async def ensure_registered() -> None:
    await repo.upsert_agent(
        AGENT_ID, "Graph Auditor", "",
        "verify approved graph episodes landed as approved",
    )


def build_graph_auditor(model, charter: str | None = None) -> Agent:
    return Agent(
        model,
        name="graph-auditor",
        output_type=GraphAuditVerdict,
        system_prompt=charter or render_v0(GRAPH_AUDITOR_CHARTER),
    )


async def _resolve_model(model=None):
    if model is not None:
        return model
    if settings.demo_mode:
        from central_command.runtime.spike_model import make_graph_auditor_model

        return make_graph_auditor_model()
    from central_command.runtime.models import resolve_model

    return await resolve_model(agent_id=AGENT_ID)


def render_delta(delta: dict, include_uuids: bool = False) -> str:
    """The delta as reviewable text — the same rendering goes into the
    judgment prompt and (via the row's `delta` jsonb) to the operator, so
    the auditor and the human are always looking at the same picture.
    `include_uuids` is the remediation-task variant: the curator needs the
    uuids to target its surgical edits."""
    lines: list[str] = []
    entities = delta.get("entities") or []
    edges = delta.get("edges") or []
    invalidated = delta.get("invalidated") or []
    def _newness(x: dict) -> str:
        # `new` may be absent on deltas audited before 2026-08-20 — absent is
        # "not checked", never a bucket.
        if x.get("new") is True:
            return "[NEW] "
        if x.get("new") is False:
            return "[EXISTING] "
        return ""

    def _validity(e: dict) -> str:
        parts = []
        if e.get("valid_at"):
            parts.append(f"valid from {e['valid_at']}")
        if e.get("invalid_at"):
            parts.append(f"until {e['invalid_at']}")
        return f"  ({', '.join(parts)})" if parts else ""

    def _uuid(x: dict) -> str:
        return f" [uuid {x.get('uuid')}]" if include_uuids and x.get("uuid") else ""

    lines.append(f"ENTITIES ({len(entities)}):")
    for n in entities:
        labels = [l for l in (n.get("labels") or []) if l != "Entity"]
        embedded = "" if n.get("has_embedding") else "  [NO EMBEDDING — invisible to semantic recall]"
        lines.append(
            f"  - {_newness(n)}{n.get('name')} ({', '.join(labels) or 'Entity'})"
            f"{_uuid(n)}{embedded}"
        )
    lines.append(f"RELATIONSHIPS ({len(edges)}):")
    for e in edges:
        lines.append(
            f"  - {_newness(e)}{e.get('source')} —[{e.get('name')}]→ "
            f"{e.get('target')}: {e.get('fact')}{_uuid(e)}{_validity(e)}"
        )
    lines.append(f"INVALIDATED EXISTING FACTS ({len(invalidated)}):")
    for e in invalidated:
        lines.append(
            f"  - RETIRED: {e.get('source')} —[{e.get('name')}]→ {e.get('target')}: "
            f"{e.get('fact')}{_uuid(e)} (attributed_by={e.get('attributed_by')})"
        )
    return "\n".join(lines)


async def remediation_instructions(row: dict, note: str) -> str:
    """The graph-curator's task brief for one PROBLEM verdict — everything on
    the record, uuids included so the proposed edits can be targeted."""
    approved = await _approved_episode_body(row) or "(no approved text on record)"
    delta = render_delta(row.get("delta") or {}, include_uuids=True)
    return (
        "A knowledge-graph write diverged from what the operator approved. "
        "Propose the MINIMAL gated curation edits that make the graph say what "
        "the approved text says.\n\n"
        f"verification_id (carry this in every action's arguments): {row['id']}\n"
        f"Graph group: {row['group_id']} (scope {row['scope']})\n\n"
        f"APPROVED EPISODE TEXT:\n---\n{approved}\n---\n\n"
        f"WHAT EXTRACTION PRODUCED (as audited; re-read the live graph before "
        f"proposing — it may have moved):\n{delta}\n\n"
        f"AUDITOR VERDICT: {row.get('verdict') or 'none'}"
        f" — {row.get('verdict_rationale') or 'no rationale recorded'}\n\n"
        f"THE OPERATOR'S PROBLEM NOTE (the defect to fix):\n{note}"
    )


async def _approved_episode_body(row: dict) -> str | None:
    """The claim the operator actually approved, from the proposal row — the
    control plane's record, deliberately not the graph's copy of it.

    A remediation RE-CHECK row's own proposal is the curation proposal (no
    episode on it) — follow `remediation_of` back to the row that carries the
    original `graph.add_episode` proposal. Bounded walk: a fix of a fix still
    ends at the one original episode."""
    for _ in range(10):
        proposal = await repo.load_proposal(row["proposal_id"])
        for action in (proposal or {}).get("actions") or []:
            capability = str(action.get("capability", "")).split("@", 1)[0]
            if capability == "graph.add_episode":
                return (action.get("arguments") or {}).get("episode_body")
        if not row.get("remediation_of"):
            return None
        row = await repo.get_graph_verification(row["remediation_of"]) or {}
        if not row:
            return None
    return None


async def _judge(
    row: dict, episode_body: str, delta: dict, model=None
) -> GraphAuditVerdict | None:
    """One judgment run. None = the auditor errored — the row degrades to the
    human gate with no verdict, exactly like a disabled auditor."""
    prompt = (
        "APPROVED EPISODE (the claim the operator reviewed and approved):\n"
        f"---\n{episode_body}\n---\n"
        f"Scope: {row['scope']} (graph group {row['group_id']})\n\n"
        "WHAT EXTRACTION ACTUALLY PRODUCED (read back from the graph):\n"
        f"{render_delta(delta)}\n\n"
        "Does the graph change faithfully represent the approved claim, and is "
        "every invalidated fact genuinely contradicted by it?"
    )
    try:
        await ensure_registered()
        governed = await repo.current_charter(AGENT_ID)
        run = await build_graph_auditor(
            await _resolve_model(model), charter=governed["content"] if governed else None
        ).run(prompt)
        return run.output
    except Exception as e:  # noqa: BLE001 — a down auditor degrades to the human gate
        await events.emit(
            "graph.audit.error", ref_id=row["id"],
            payload={"error": str(e), "transient": is_transient(e)},
            actor="graph-auditor",
        )
        return None


async def verify_one(row: dict, model=None, missing_after_minutes: int = 360) -> str:
    """Audit one PENDING verification row and land it. Returns the outcome
    bucket for the sweep's tally:
    'waiting' | 'missing' | 'auto_verified' | 'awaiting'."""
    episode = await neo4j_reader.episode_by_marker(row["marker"], row["group_id"])
    if episode is None:
        # Absence is only evidence once the row is STALE. Ingestion is a
        # serial queue on the local model (~2-3 min/episode), so a BATCH of
        # approvals lands 50-70 minutes after execute — measured 2026-08-20,
        # when a 10-minute settle false-parked nine healthy episodes as
        # "missing". Inside the deadline the row just stays PENDING for the
        # next tick; past it, absence IS the silent-drop finding (the class
        # that lost 25 acked episodes on 2026-08-15).
        from datetime import datetime, timezone

        created = row["created_at"]
        age_minutes = (datetime.now(timezone.utc) - created).total_seconds() / 60
        if age_minutes < missing_after_minutes:
            return "waiting"
        mechanical = {"missing": True}
        await repo.finish_graph_verification(
            row["id"], status="AWAITING_OPERATOR", mechanical=mechanical,
        )
        await events.emit(
            "graph.verification.parked", ref_id=row["id"],
            payload={"proposal_id": row["proposal_id"], "episode_name": row["episode_name"],
                     "mechanical": mechanical, "verdict": None},
            actor="graph-auditor",
        )
        return "missing"

    delta = await neo4j_reader.episode_delta(episode["uuid"])
    unembedded = [
        n.get("name") for n in delta.get("entities") or [] if not n.get("has_embedding")
    ]
    mechanical = {
        "missing": False,
        "empty_delta": not (delta.get("entities") or delta.get("edges")),
        "unembedded": unembedded,
    }
    mechanical_clean = not mechanical["empty_delta"] and not unembedded
    has_invalidations = bool(delta.get("invalidated"))
    action_class = (
        GRAPH_AUDIT_INVALIDATION_CLASS if has_invalidations else GRAPH_AUDIT_ACTION_CLASS
    )

    verdict: GraphAuditVerdict | None = None
    if settings.graph_auditor_enabled and mechanical_clean:
        episode_body = await _approved_episode_body(row)
        if episode_body:
            verdict = await _judge(row, episode_body, delta, model=model)
        else:
            # An unattributed row (no proposal on record) has no INTENDED text
            # to compare against — that absence is itself operator-worthy.
            mechanical["no_approved_text"] = True
            mechanical_clean = False

    mode = settings.graph_auditor_mode if settings.graph_auditor_mode == "active" else "shadow"

    # The verdict goes on the log BEFORE the close it can authorise (the M8
    # ordering rule) — an auto-close must never appear before its reason.
    if verdict is not None:
        await events.emit(
            "graph.audit.verdict", ref_id=row["id"],
            payload={
                "action_class": action_class,
                "verdict": "aligned" if verdict.aligned else "flag",
                "rationale": verdict.rationale,
                "mode": mode,
                "proposal_id": row["proposal_id"],
                "has_invalidations": has_invalidations,
            },
            actor="graph-auditor",
        )

    auto_close = (
        mode == "active"
        and verdict is not None
        and verdict.aligned
        and mechanical_clean
        and not has_invalidations
    )
    await repo.finish_graph_verification(
        row["id"],
        status="VERIFIED" if auto_close else "AWAITING_OPERATOR",
        episode_uuid=episode["uuid"],
        mechanical=mechanical,
        delta=delta,
        verdict=None if verdict is None else ("aligned" if verdict.aligned else "flag"),
        verdict_rationale=None if verdict is None else verdict.rationale,
        has_invalidations=has_invalidations,
        closed_by="graph-auditor" if auto_close else None,
    )
    if auto_close:
        await events.emit(
            "graph.verification.confirmed", ref_id=row["id"],
            payload={"by": "graph-auditor", "action_class": action_class,
                     "rationale": verdict.rationale},
            actor="graph-auditor",
        )
        return "auto_verified"

    await events.emit(
        "graph.verification.parked", ref_id=row["id"],
        payload={
            "proposal_id": row["proposal_id"], "episode_name": row["episode_name"],
            "mechanical": mechanical,
            "verdict": None if verdict is None else ("aligned" if verdict.aligned else "flag"),
            "has_invalidations": has_invalidations,
        },
        actor="graph-auditor",
    )
    return "awaiting"


async def verify_sweep(
    settle_minutes: int = 10, model=None, missing_after_minutes: int = 360
) -> dict:
    """The heartbeat entry: audit every PENDING row past the settle delay.
    A row that errors (or whose episode is still queued) stays PENDING and is
    retried next tick — the sweep can never wedge, and never silently drops a
    row."""
    rows = await repo.due_graph_verifications(settle_minutes)
    summary = {"checked": 0, "auto_verified": 0, "awaiting": 0, "missing": 0,
               "waiting": 0, "errors": []}
    for row in rows:
        summary["checked"] += 1
        try:
            outcome = await verify_one(
                row, model=model, missing_after_minutes=missing_after_minutes
            )
            summary[outcome] += 1
        except Exception as e:  # noqa: BLE001 — one bad row must not starve the rest
            log.exception("graph verification failed for %s", row["id"])
            summary["errors"].append({"id": row["id"], "error": str(e)})
    return summary


def pair_graph_verdicts(rows: list[dict], action_class: str) -> dict:
    """Pure pairing over event rows (ordered by id ascending), scoped to ONE
    action class — mixing them would let the chatty addition class stand in
    for the quiet invalidation class (the steward_agreement rule).

    A verdict pairs with the NEXT operator outcome for its row. Auto-closes
    (active mode, actor graph-auditor) carry no operator signal and are
    tallied separately, as are verdicts still awaiting an outcome.
    """
    pending: dict[str, dict] = {}
    pairs: list[dict] = []
    auto_confirmed = 0

    def close(ref: str, outcome: str, outcome_event: int, note: str | None = None) -> None:
        verdict = pending.pop(ref, None)
        if verdict is not None:
            pairs.append({"verification_id": ref, "outcome": outcome,
                          "outcome_event": outcome_event, "note": note, **verdict})

    for e in rows:
        kind, ref, payload = e["kind"], e["ref_id"], e.get("payload") or {}
        if kind == "graph.audit.verdict":
            if payload.get("action_class") != action_class:
                continue
            pending[ref] = {
                "verdict": payload.get("verdict"),
                "rationale": payload.get("rationale", ""),
                "mode": payload.get("mode"),
                "proposal_id": payload.get("proposal_id"),
                "verdict_event": e["id"],
            }
        elif kind == "graph.verification.confirmed":
            if e.get("actor") == "graph-auditor":
                auto_confirmed += 1
                pending.pop(ref, None)
            else:
                close(ref, "confirmed", e["id"])
        elif kind == "graph.verification.problem":
            close(ref, "problem", e["id"], note=payload.get("note"))

    q = {"aligned_confirmed": 0, "flag_problem": 0,
         "flag_confirmed": 0, "aligned_problem": 0}
    for p in pairs:
        q[f"{p['verdict']}_{p['outcome']}"] += 1

    agreed = q["aligned_confirmed"] + q["flag_problem"]
    return {
        "action_class": action_class,
        "pairs": pairs,
        "quadrants": q,
        "agreed": agreed,
        "total_paired": len(pairs),
        "agreement_rate": round(agreed / len(pairs), 3) if pairs else None,
        # The DANGEROUS quadrant: an aligned verdict the operator overruled is
        # exactly what active mode would have silently let through.
        "auditor_misses": [p for p in pairs
                           if p["verdict"] == "aligned" and p["outcome"] == "problem"],
        "over_cautious": [p for p in pairs
                          if p["verdict"] == "flag" and p["outcome"] == "confirmed"],
        "auto_confirmed": auto_confirmed,
        "awaiting_outcome": len(pending),
    }


def signals_from_graph_report(report: dict, names: dict[str, str] | None = None) -> list[dict]:
    """The graph auditor's coaching signals ARE the disagreement quadrants,
    citable by verdict event — same shape as auditor.signals_from_report."""
    names = names or {}
    signals: list[dict] = []
    for p in report.get("over_cautious", []):
        name = names.get(p["verification_id"]) or p.get("episode_name") or p["verification_id"]
        signals.append({
            "kind": "graph-audit-overcautious",
            "event_id": p["verdict_event"],
            "feedback": (
                f"on episode “{name}” you flagged with “{p['rationale']}” — "
                "the team lead confirmed the graph change was faithful"
            ),
        })
    for p in report.get("auditor_misses", []):
        name = names.get(p["verification_id"]) or p.get("episode_name") or p["verification_id"]
        note = f" — their note: “{p['note']}”" if p.get("note") else ""
        signals.append({
            "kind": "graph-audit-miss",
            "event_id": p["verdict_event"],
            "feedback": (
                f"on episode “{name}” you judged the change ALIGNED with "
                f"“{p['rationale']}” — the team lead found a real problem{note}"
            ),
        })
    return signals


async def agreement_report() -> dict:
    """The shadow-vs-operator record per action class, computed fresh from the
    event log every time — the log is the truth; nothing cached or restated."""
    rows = await repo.list_events_of_kinds(
        ["graph.audit.verdict", "graph.verification.confirmed",
         "graph.verification.problem"]
    )
    classes = {}
    for action_class in (GRAPH_AUDIT_ACTION_CLASS, GRAPH_AUDIT_INVALIDATION_CLASS):
        report = pair_graph_verdicts(rows, action_class)
        # Episode names for the disagreement lists — what the operator reviews
        # when deciding graduation.
        for p in report["auditor_misses"] + report["over_cautious"]:
            row = await repo.get_graph_verification(p["verification_id"])
            p["episode_name"] = row["episode_name"] if row else None
        classes[action_class] = report
    return {"classes": classes}
