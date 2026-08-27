"""The D7 orchestrator — a dedicated agent that routes unassigned tasks.

the operator's shape call (2026-07-21): dedicated agent, per the design lean — on the
roster, governed charter, coachable, uniform management. Rollout follows the
house rule (shadow-first, graduated, revocable): the orchestrator RECOMMENDS
an agent for a NEW task; the recommendation rides the task card with its
rationale, and **assignment stays the operator's click**. Auto-assign is a
future graduation, decided on the operator's override record — never before.

The orchestrator holds no propose_* tools and no write path: routing is the
whole job, and a recommendation changes nothing in the world. A down or
erroring orchestrator degrades to exactly the old behaviour — the task parks
NEW for the operator, `task.route_error` on the log.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel

from central_command.db import repo
from central_command.runtime.deps import TriageDeps
from central_command.runtime.orchestrator_profile import CHARTER
from central_command.runtime.roster import roster
from central_command.runtime.templates import render_v0

AGENT_ID = "orchestrator"


class RouteRecommendation(BaseModel):
    """The structured verdict: which agent should take the task, or None when
    no roster agent fits (an honest roster-gap report, never a forced match)."""

    agent_id: str | None
    rationale: str


def build_orchestrator(model, charter: str | None = None):
    from pydantic_ai import Agent

    return Agent(
        model,
        name=AGENT_ID,
        output_type=RouteRecommendation,
        system_prompt=charter or render_v0(CHARTER),
        deps_type=TriageDeps,
    )


async def build_project_orchestrator(
    model=None, charter: str | None = None, packs=(),
    capabilities=None, skills_catalog: str = "", team_section: str = "",
):
    """The ORCHESTRATION-mode agent (D7 phase 2): governed charter (or the
    built-in) + the generated pack section, the deferred assign/ask tools from
    the `orchestrate` pack, plain-text final report. Routing mode above keeps
    its structured RouteRecommendation output untouched — same agent identity,
    two run shapes, one governed charter covering both.

    `capabilities`/`skills_catalog` give the orchestrator the same granted-
    skills lane every other roster agent gets — uniform agent management
    means no agent is the deliberate exception without a recorded reason,
    and this one isn't one."""
    from pydantic_ai import Agent, DeferredToolRequests

    from central_command.runtime import packs as packs_mod
    from central_command.runtime.agent import build_charter
    from central_command.runtime.tools import current_time, declare_gap

    return Agent(
        await _resolve_model(model),
        name=AGENT_ID,
        system_prompt=build_charter(
            charter or render_v0(CHARTER), packs, team_section=team_section
        ) + (
            "\n\n" + skills_catalog if skills_catalog else ""
        ),
        deps_type=TriageDeps,
        output_type=[str, DeferredToolRequests],
        # declare_gap is agent PROCESS, not a grantable capability — same
        # reasoning as build_agent/build_hired_agent in runtime/agent.py.
        tools=[declare_gap, current_time],
        toolsets=[packs_mod.toolset_for(packs)],
        capabilities=list(capabilities or []),
    )


async def _resolve_model(model=None):
    if model is not None:
        return model
    from central_command.config import settings

    if settings.demo_mode:
        from central_command.runtime.spike_model import make_orchestrator_model

        return make_orchestrator_model()
    from central_command.runtime.models import resolve_model

    return await resolve_model(agent_id="orchestrator")


async def ensure_registered() -> None:
    await repo.upsert_agent(
        AGENT_ID, "Orchestrator", "",
        "routes unassigned tasks to the right agent; recommends, never assigns",
    )


def _briefing(task: dict, candidates) -> str:
    lines = [
        "An UNASSIGNED task needs a routing recommendation.",
        "",
        f"TASK (from the team lead, trusted): {task['instructions']}",
        "",
        "CANDIDATE AGENTS (recommend one of these ids, or null if none fits):",
    ]
    for a in candidates:
        lines.append(f"- {a.id}: {a.role}")
    return "\n".join(lines)


async def recommend(task: dict, model=None) -> dict | None:
    """Produce, persist and announce a routing recommendation for a NEW task.

    Returns the stored recommendation dict, or None when the orchestrator
    errored — the task simply stays NEW behind the operator, like every other
    degraded gate helper (auditor precedent)."""
    from central_command import events
    from central_command.runtime.run import _run_live

    candidates = [a for a in await roster() if a.taskable]
    try:
        await ensure_registered()
        governed = await repo.current_charter(AGENT_ID)
        agent = build_orchestrator(
            await _resolve_model(model), charter=governed["content"] if governed else None
        )
        session_id = "sess_" + uuid.uuid4().hex[:12]
        result = await _run_live(
            agent, _briefing(task, candidates), model=None,
            deps=TriageDeps(agent_id=AGENT_ID, session_id=session_id),
            agent_id=AGENT_ID, session_id=session_id,
        )
        from central_command.runtime.converse import land_session

        await land_session(session_id, agent_id=AGENT_ID)
        rec: RouteRecommendation = result.output
    except Exception as e:  # noqa: BLE001 — a down orchestrator degrades to the operator
        await events.emit(
            "task.route_error", ref_id=task["id"], payload={"error": str(e)},
            actor=f"agent:{AGENT_ID}",
        )
        return None

    # Mechanical check (house rule: no agent claim trusted unverified): a
    # recommended id must actually be a taskable roster agent. A hallucinated
    # id becomes an honest "no fit" with the fabrication on the record.
    sanitized = False
    agent_id = rec.agent_id
    if agent_id is not None and agent_id not in {a.id for a in candidates}:
        sanitized = True
        agent_id = None

    stored = {
        "agent_id": agent_id,
        "rationale": rec.rationale,
        "session_id": session_id,
        **({"sanitized_from": rec.agent_id} if sanitized else {}),
    }
    await repo.set_task_recommendation(task["id"], stored)
    await events.emit(
        "task.recommended",
        ref_id=task["id"],
        payload={"agent_id": agent_id, "rationale": rec.rationale[:300],
                 "session_id": session_id, "sanitized": sanitized},
        actor=f"agent:{AGENT_ID}",
    )
    return stored


# --- The routing agreement record (graduation evidence, auditor pattern) -------
# Every recommendation paired with the operator's outcome for the same task.
# Acceptances vs overrides are the D7 graduation record: auto-assign is decided
# on this, never before — and every override is a coaching signal.


def pair_routes(rows: list[dict]) -> dict:
    """Pure pairing over event rows (`task.recommended` / `task.assigned` /
    `task.cancelled`, ascending id order): the LATEST standing recommendation
    for a task pairs with the task's next outcome. Operator assignments made
    with no standing recommendation are tallied separately — they say nothing
    about the orchestrator's judgment."""
    standing: dict[str, dict] = {}   # task_id -> latest recommendation event
    accepted: list[dict] = []
    overridden: list[dict] = []
    cancelled_after_rec = 0
    unrouted_assignments = 0

    for e in sorted(rows, key=lambda r: r["id"]):
        task_id, payload = e.get("ref_id"), e.get("payload") or {}
        if e["kind"] == "task.recommended":
            standing[task_id] = e
            continue
        rec = standing.pop(task_id, None)
        if e["kind"] == "task.cancelled":
            if rec is not None:
                cancelled_after_rec += 1
            continue
        if e["kind"] == "task.assigned":
            if rec is None:
                unrouted_assignments += 1
                continue
            pair = {
                "task_id": task_id,
                "recommended": (rec.get("payload") or {}).get("agent_id"),
                "assigned": payload.get("agent_id"),
                "rationale": (rec.get("payload") or {}).get("rationale"),
                "recommended_event": rec["id"],
                "outcome_event": e["id"],
            }
            if pair["recommended"] is not None and pair["recommended"] == pair["assigned"]:
                accepted.append(pair)
            else:
                overridden.append(pair)

    total = len(accepted) + len(overridden)
    return {
        "accepted": len(accepted),
        "overridden": overridden,
        "cancelled_after_recommendation": cancelled_after_rec,
        "unrouted_assignments": unrouted_assignments,
        "awaiting_outcome": len(standing),
        "total_paired": total,
        "agreement_rate": (len(accepted) / total) if total else None,
    }


def signals_from_routes(report: dict, titles: dict[str, str] | None = None) -> list[dict]:
    """Every override is the operator's recorded verdict on a routing call —
    the orchestrator's coaching signals, citable by the assignment event."""
    titles = titles or {}
    signals: list[dict] = []
    for p in report.get("overridden", []):
        title = titles.get(p["task_id"]) or p.get("title") or p["task_id"]
        rec = p["recommended"] or "no one (no fit)"
        signals.append({
            "kind": "route-override",
            "event_id": p["outcome_event"],
            "feedback": (
                f"for task “{title}” you recommended {rec} "
                f"(“{p['rationale']}”) — the team lead assigned "
                f"{p['assigned']} instead"
            ),
        })
    return signals


async def routing_report() -> dict:
    """The recommendation-vs-operator record, computed fresh from the event
    log every call — the log is the truth, nothing cached or re-stated."""
    rows = await repo.list_events_of_kinds(
        ["task.recommended", "task.assigned", "task.cancelled"]
    )
    report = pair_routes(rows)
    ids = [p["task_id"] for p in report["overridden"]]
    titles = await repo.task_titles(ids) if ids else {}
    for p in report["overridden"]:
        p["title"] = titles.get(p["task_id"])
    return report


async def orchestrator_signals() -> list[dict]:
    """Candidate coaching inputs for the orchestrator, fresh from the log."""
    report = await routing_report()
    return signals_from_routes(report)
