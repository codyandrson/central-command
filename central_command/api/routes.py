"""Control-plane API routes for the M4 dashboard."""

from __future__ import annotations

import io
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from central_command import events
from central_command.contract import is_transient
from central_command.db import repo
from central_command.gateway import gateway
from central_command.ingest import bulk_dismiss, ledger
from central_command.ingest.dispatcher import dispatch_item, dispatch_once, dispatcher
from central_command.ingest.feed import feed, sweeper
from central_command.integrations import neo4j_reader, neo4j_writer
from central_command.runtime.models import resolve_model, resolve_session_model
from central_command.skills.ids import SKILL_ID_HELP, SKILL_ID_RE

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


class EmailIn(BaseModel):
    text: str


class RejectIn(BaseModel):
    feedback: str


class DismissIn(BaseModel):
    # Optional on purpose: an operator waving off noise ("Close, don't ask
    # again") should not have to write an essay to do it.
    reason: str = ""


class BacklogIn(BaseModel):
    dir: str = "fixtures/emails"


class CharterIn(BaseModel):
    content: str
    rationale: str | None = None


# --- governed charters (Phase 3, M11) -----------------------------------------
# Two paths write versions: operator-authored here, and coach-PROPOSED edits
# (Phase 4, stage 5a) through the same approval gate as every other mutation.


@router.get("/agents")
async def list_agents() -> dict:
    """The roster (D24: the agent rows ARE the roster) — role, lifecycle
    status, taskability/consultability, each recorded deviation from the
    standard management processes (The operator's uniform-management rule), and the
    active capability-pack grants (D25)."""
    agents = []
    for a in await repo.list_agents():
        grants = await repo.list_grants(a["id"])
        agents.append({
            **a,
            # '' is the UNSET sentinel in the column; the wire says null, so the
            # cockpit renders "default" rather than an agent pinned to "".
            "model": a["model"] or None,
            "thinking": a["thinking"] or None,
            "packs": [g["pack"] for g in grants if g["revoked_at"] is None],
        })
    return {"agents": agents}


class AgentModelIn(BaseModel):
    model: str | None = None      # '' / null clears back to the default
    thinking: str | None = None   # a THINKING_LEVELS value; '' / null clears


@router.post("/agents/{agent_id}/model")
async def set_agent_model(agent_id: str, body: AgentModelIn) -> dict:
    """The operator's per-agent model + thinking choice — a DIRECT operator
    action with an event record (D24), not a proposal: the gate exists for what
    AGENTS want to do.

    Both halves are written together; clearing either sends '' and the run falls
    back through CC_MODEL_<AGENT_ID> to the global default. The model id is NOT
    checked against the proxy catalog here on purpose — that failure is loud
    (the run 400s and the task lands FAILED), unlike a bad thinking level, which
    500s every turn of the chat template.
    """
    from central_command.runtime.models import THINKING_LEVELS

    thinking = (body.thinking or "").strip()
    if thinking and thinking not in THINKING_LEVELS:
        raise HTTPException(
            422, f"thinking must be one of {THINKING_LEVELS} (got {thinking!r})")
    model = (body.model or "").strip()
    row = await repo.set_agent_model(agent_id, model, thinking)
    if row is None:
        raise HTTPException(404, f"agent {agent_id!r} does not exist")
    await events.emit(
        "agent.model_set", ref_id=agent_id,
        payload={"model": model or None, "thinking": thinking or None},
        actor="operator",
    )
    return {"ok": True, "agent": {**row, "model": row["model"] or None,
                                  "thinking": row["thinking"] or None}}


@router.get("/packs")
async def list_packs() -> dict:
    """The capability-pack catalog (D25) — the grantable tool surface, with
    the gated capabilities each pack lets an agent propose."""
    from central_command.runtime.packs import PACKS

    return {"packs": [
        {
            "name": p.name,
            "description": p.description,
            "tools": list(p.tool_names),
            "capabilities": [c.name for c in p.capabilities],
        }
        for p in PACKS.values()
    ]}


class HireIn(BaseModel):
    name: str                      # display name; the id is derived from it
    mission: str                   # what this agent exists to do (fills the template)
    template: str = "general"
    packs: list[str] | None = None  # override the template's default grants


@router.post("/agents/hire")
async def hire_agent(body: HireIn) -> dict:
    """Hire an agent from a template — a starting capability loadout, never an
    agent type (D24) — a DIRECT operator action with an
    event-log record (The operator's decision, 2026-07-22: internal state, not a world
    write, so no proposal gate). One transaction enforces the uniform-
    management invariant at creation: no agent row without its charter v1 +
    capability grants + ACTIVE status."""
    from central_command.runtime.packs import PACKS
    from central_command.runtime.templates import TEMPLATES, agent_id_for

    tpl = TEMPLATES.get(body.template)
    if tpl is None:
        raise HTTPException(422, f"unknown template {body.template!r} "
                                 f"(have: {', '.join(TEMPLATES)})")
    if not body.name.strip():
        raise HTTPException(422, "the agent needs a name")
    if not body.mission.strip():
        raise HTTPException(422, "the agent needs a mission — what is it for?")
    packs = body.packs if body.packs else list(tpl.default_packs)
    unknown = [p for p in packs if p not in PACKS]
    if unknown:
        raise HTTPException(422, f"unknown capability pack(s): {unknown}")
    if not packs:
        raise HTTPException(422, "an agent cannot be hired without capability grants")

    agent_id = agent_id_for(body.name)
    charter = tpl.render(name=body.name.strip(), mission=body.mission.strip(),
                         packs=packs)
    try:
        row = await repo.hire_agent(
            agent_id, body.name.strip(), tpl.role_for(body.mission.strip()),
            charter, packs, template=tpl.id,
            taskable=tpl.taskable, conversational=tpl.conversational,
        )
    except ValueError as e:
        raise HTTPException(409, str(e))
    await events.emit(
        "agent.hired",
        ref_id=agent_id,
        payload={"name": body.name.strip(), "template": tpl.id,
                 "mission": body.mission.strip()[:300], "packs": packs},
        actor="operator",
    )
    return {"ok": True, "agent": row}


class RetireIn(BaseModel):
    reason: str


@router.post("/agents/{agent_id}/retire")
async def retire_agent(agent_id: str, body: RetireIn) -> dict:
    """Retire = a status flip with a recorded reason (D24). History — sessions,
    charters, grants, events — stays keyed by agent_id forever."""
    if not body.reason.strip():
        raise HTTPException(422, "retirement requires a recorded reason")
    if not await repo.retire_agent(agent_id, body.reason.strip()):
        raise HTTPException(409, f"agent {agent_id!r} is not ACTIVE (or does not exist)")
    await events.emit(
        "agent.retired", ref_id=agent_id,
        payload={"reason": body.reason.strip()}, actor="operator",
    )
    return {"ok": True}


@router.post("/agents/{agent_id}/reactivate")
async def reactivate_agent(agent_id: str) -> dict:
    if not await repo.reactivate_agent(agent_id):
        raise HTTPException(409, f"agent {agent_id!r} is not RETIRED")
    await events.emit("agent.reactivated", ref_id=agent_id, payload={}, actor="operator")
    return {"ok": True}


class GrantIn(BaseModel):
    pack: str


@router.get("/agents/{agent_id}/grants")
async def list_agent_grants(agent_id: str) -> dict:
    return {"grants": await repo.list_grants(agent_id)}


@router.post("/agents/{agent_id}/grants")
async def grant_pack(agent_id: str, body: GrantIn) -> dict:
    """Grant a capability pack (D25) — direct operator action, on the record.
    The agent's next run assembles its toolsets from the updated grants."""
    from central_command.runtime.packs import PACKS

    # `mcp:<server_id>` is a DYNAMIC pack name (sandbox slice 5) — its
    # validity depends on a DB row, not the static PACKS dict, so it is
    # checked by repo.grant_pack itself (ValueError -> 422 below).
    if body.pack not in PACKS and not body.pack.startswith("mcp:"):
        raise HTTPException(422, f"unknown capability pack {body.pack!r}")
    if await repo.get_agent(agent_id) is None:
        raise HTTPException(404, f"unknown agent {agent_id!r}")
    try:
        await repo.grant_pack(agent_id, body.pack)
    except ValueError as e:
        raise HTTPException(422, str(e))
    await events.emit(
        "agent.pack_granted", ref_id=agent_id,
        payload={"pack": body.pack}, actor="operator",
    )
    return {"grants": await repo.list_grants(agent_id)}


class RevokeIn(BaseModel):
    reason: str | None = None


@router.post("/agents/{agent_id}/grants/{pack}/revoke")
async def revoke_pack(agent_id: str, pack: str, body: RevokeIn) -> dict:
    """Revoke a pack — the row keeps its history (revoked_at + reason), so a
    revocation can never be silently undone by an idempotent seed."""
    if not await repo.revoke_pack(agent_id, pack, (body.reason or "").strip() or None):
        raise HTTPException(409, f"{agent_id!r} holds no active grant for {pack!r}")
    await events.emit(
        "agent.pack_revoked", ref_id=agent_id,
        payload={"pack": pack, "reason": (body.reason or "").strip() or None},
        actor="operator",
    )
    return {"grants": await repo.list_grants(agent_id)}


# --- MCP tool grants (D-sandbox slice 5) --------------------------------------
# Direct operator actions, same style as hire/retire/grant_pack above: no
# propose path writes an `agent_mcp_gate_override` row — its existence IS the
# explicit buy-in the governance doctrine requires. Every write here emits an
# event AFTER the write (bookkeeping, not authorization — the write itself is
# the operator's own direct action, gated by nothing).

_MCP_GATES = {"human approval", "ungated"}


@router.get("/mcp/servers")
async def list_mcp_servers_and_tools() -> dict:
    """Every known mcp_server row, with its discovered tools and default
    gates — the operator's view for deciding what to widen."""
    tools = await repo.list_mcp_tools()
    by_server: dict[str, list[dict]] = {}
    for t in tools:
        by_server.setdefault(t["server_id"], []).append(t)
    return {"servers": [
        {**row, "tools": by_server.get(row["id"], [])}
        for row in await repo.list_mcp_servers()
    ]}


class MCPToolGateIn(BaseModel):
    default_gate: str


@router.patch("/mcp/servers/{server_id}/tools/{tool_name}")
async def set_mcp_tool_gate(server_id: str, tool_name: str, body: MCPToolGateIn) -> dict:
    """Change a tool's DEFAULT gate — the fallback every agent gets unless it
    holds its own override."""
    if body.default_gate not in _MCP_GATES:
        raise HTTPException(422, f"default_gate must be one of {sorted(_MCP_GATES)}")
    if not await repo.set_mcp_tool_default_gate(server_id, tool_name, body.default_gate):
        raise HTTPException(404, f"no mcp_tool {server_id!r}/{tool_name!r}")
    await events.emit(
        "mcp.gate_changed", ref_id=server_id,
        payload={"server_id": server_id, "tool_name": tool_name,
                 "default_gate": body.default_gate},
        actor="operator",
    )
    return {"server_id": server_id, "tool_name": tool_name, "default_gate": body.default_gate}


class MCPOverrideIn(BaseModel):
    server_id: str
    tool_name: str
    gate: str


@router.put("/agents/{agent_id}/mcp-overrides")
async def set_mcp_override(agent_id: str, body: MCPOverrideIn) -> dict:
    """The operator's per-agent widen/narrow — writing this row IS the
    explicit buy-in; re-granting after a revoke reactivates it."""
    if body.gate not in _MCP_GATES:
        raise HTTPException(422, f"gate must be one of {sorted(_MCP_GATES)}")
    if await repo.get_agent(agent_id) is None:
        raise HTTPException(404, f"unknown agent {agent_id!r}")
    if await repo.get_mcp_server(body.server_id) is None:
        raise HTTPException(404, f"unknown mcp_server {body.server_id!r}")
    await repo.set_agent_mcp_gate_override(agent_id, body.server_id, body.tool_name, body.gate)
    await events.emit(
        "mcp.override_set", ref_id=agent_id,
        payload={"agent_id": agent_id, "server_id": body.server_id,
                 "tool_name": body.tool_name, "gate": body.gate},
        actor="operator",
    )
    return {"overrides": await repo.list_agent_mcp_overrides(agent_id)}


@router.delete("/agents/{agent_id}/mcp-overrides")
async def revoke_mcp_override(agent_id: str, server_id: str, tool_name: str) -> dict:
    if not await repo.revoke_agent_mcp_gate_override(agent_id, server_id, tool_name):
        raise HTTPException(
            409, f"{agent_id!r} holds no active override for {server_id!r}/{tool_name!r}"
        )
    await events.emit(
        "mcp.override_revoked", ref_id=agent_id,
        payload={"agent_id": agent_id, "server_id": server_id, "tool_name": tool_name},
        actor="operator",
    )
    return {"overrides": await repo.list_agent_mcp_overrides(agent_id)}


@router.get("/agents/{agent_id}/charter")
async def get_charter(agent_id: str) -> dict:
    from central_command.runtime.agent import builtin_charter

    current = await repo.current_charter(agent_id)
    if current is None:
        # No governed version yet — the agent runs on its built-in code
        # charter. Per-agent lookup: this used to always show the triage
        # charter, which mislabeled every other agent's v0.
        try:
            content = builtin_charter(agent_id)
        except ValueError:
            raise HTTPException(404, f"unknown agent {agent_id!r}")
        current = {"version": 0, "content": content, "created_by": "built-in",
                   "rationale": "hardcoded default", "created_at": None}
    return {"current": current, "versions": await repo.list_charter_versions(agent_id)}


@router.post("/agents/{agent_id}/charter")
async def save_charter(agent_id: str, body: CharterIn) -> dict:
    from central_command.gateway import policy

    if not body.content.strip():
        raise HTTPException(422, "charter content must not be empty")
    if await repo.get_agent(agent_id) is None:
        raise HTTPException(404, f"unknown agent {agent_id!r}")
    # Advisory, never blocking (operator input is trusted) — but a charter
    # quoting a capability the registry doesn't have deserves a warning at
    # save time, not a failed proposal weeks later.
    warnings = policy.check_charter_content(body.content)
    row = await repo.save_charter_version(agent_id, body.content, body.rationale)
    await events.emit(
        "charter.updated",
        ref_id=agent_id,
        payload={"version": row["version"], "rationale": body.rationale,
                 **({"policy_flags": warnings} if warnings else {})},
        actor="operator",
    )
    return {"ok": True, "version": row["version"], "warnings": warnings}


@router.post("/agents/{agent_id}/charter/{version}/activate")
async def revert_charter(agent_id: str, version: int) -> dict:
    """Revert = re-activate an old version's content as a NEW version, so the
    history stays append-only and the revert itself is on the record."""
    old = await repo.get_charter_version(agent_id, version)
    if old is None:
        raise HTTPException(404, "no such charter version")
    row = await repo.save_charter_version(
        agent_id, old["content"], f"revert to v{version}"
    )
    await events.emit(
        "charter.reverted",
        ref_id=agent_id,
        payload={"version": row["version"], "restored": version},
        actor="operator",
    )
    return {"ok": True, "version": row["version"], "restored": version}


class TaskIn(BaseModel):
    instruction: str


# --- first-class Tasks (Phase 4, SC-2 groundwork) ------------------------------
# Operator-created units of work, distinct from email work_items. An assigned
# task runs its agent immediately over the durable spine (valves skipped —
# operator-requested work is not agents outrunning review); any proposal parks
# in the Decisions Inbox and the task resolves when its session does (gateway).
# An UNASSIGNED task parks in NEW, visibly awaiting the orchestrator (D7 — not
# built; the slot exists so the board shows the gap honestly).


class TaskCreateIn(BaseModel):
    instructions: str
    title: str | None = None
    agent_id: str | None = None  # None = unassigned, awaiting orchestrator (D7)
    # True = the POST returns once the task exists and the run has been
    # launched; the run itself proceeds detached and lands its outcome on the
    # task record + event log (the "+"-dialog assign path uses this).
    background: bool = False
    # Documents only, same wire shape as chat's `attachments` — reuses
    # `attachments.prepare()`. Requires `agent_id`: an unassigned task has no
    # resolvable model to check delivery against.
    attachments: list[dict] | None = None


class TaskAssignIn(BaseModel):
    agent_id: str


async def _land_run_failure(task: dict, exc: BaseException) -> bool:
    """Put a dead run on its TASK's record. Returns True if the task was parked
    for retry instead of failed.

    THE landing for a FIRST run, shared by every fresh-run entry (the detached
    wrapper, the synchronous REST create, assign-and-run). It used to live
    inside `_run_assigned_task_detached` alone, which meant a run that raised on
    a SYNCHRONOUS entry never reached any handler at all: the exception escaped
    to the caller (`heartbeat.fire` logs `heartbeat.error` and moves on) and the
    task row stayed IN_PROGRESS forever. Three tasks sat stuck on the board that
    way — a 429 on 2026-07-31, a provider timeout on 2026-08-02 — both of them
    transient errors the retry machinery would have re-run had it been asked.

    NOT used by `orchestration.retry_parked_task`: a retry carries attempt
    bookkeeping and an exhaustion promotion this landing knows nothing about, so
    it keeps its own handler and calls `_run_assigned_task_inner` directly.
    Parking here from underneath it would reset `attempts` to 1 every round —
    a retry loop that never exhausts.
    """
    from central_command.api import orchestration

    task_id = task["id"]
    if await orchestration.park_task_retry(task_id, exc):
        return True
    await repo.resolve_task(task_id, "FAILED", outcome=f"run failed: {exc}")
    await events.emit(
        "task.run_failed",
        ref_id=task_id,
        payload={"error": str(exc), "transient": is_transient(exc)},
        actor=f"agent:{task['agent_id']}",
    )
    # A FAILED child still resumes its waiting orchestrator — the failure
    # IS the result the loop needs to decide on (D7 phase 2). A PARKED
    # child, by contrast, has simply not resolved yet, so the parent is
    # told nothing until it converges (or exhausts, which lands here).
    orchestration.spawn_child_resolved(task)
    return False


async def _run_assigned_task(task: dict, actor: str = "operator", *,
                             model_name: str | None = None,
                             thinking: str | None = None) -> dict:
    """Run an ASSIGNED task now, landing any run failure on the task record.

    THE fresh-run entry. The guard is here rather than at the three call sites
    so no caller can be the one that forgets it — which is exactly how the
    synchronous entries went unhandled while the detached one was correct. The
    exception still propagates: an HTTP caller gets its 500 and the heartbeat
    still records `heartbeat.error`. Only the task's record is new.
    """
    try:
        return await _run_assigned_task_inner(
            task, actor=actor, model_name=model_name, thinking=thinking)
    except Exception as e:  # noqa: BLE001 — any run error must reach the record
        await _land_run_failure(task, e)
        raise


_task_queue_locks: dict[tuple, "asyncio.Lock"] = {}


def _agent_task_lock(agent_id: str) -> "asyncio.Lock":
    """One asyncio.Lock per (running loop, agent_id) — the per-agent task-run
    queue (operator decision, 2026-08-21): a large backlog on the board drains
    incrementally per agent, never all at once. Scope is ASSIGNED-TASK RUNS
    ONLY — chats, consultations, resumes, coach runs, orchestrator
    recommendations are untouched; do not extend this lock to them without a
    new operator decision.

    BITE MARK: an asyncio.Lock created at module scope is LOOP-BOUND exactly
    like `neo4j_reader._get_driver`'s cached driver — it binds to the first
    running loop that acquires it and raises on every other one. Production
    has one loop and never sees it; pytest gives each test its own loop and
    hits it immediately. Keying the registry on the running loop (never
    reused across loops) is what avoids that, the same fix as the driver
    cache.

    Deadlock safety: no path ever nests two acquisitions for the same
    agent_id. `create_and_run_task` refuses an orchestrator-targeted child
    assignment (the star-shape guard above), so the orchestrator can never
    assign itself a task while already holding its own lock; a heartbeat
    action creates tasks from OUTSIDE any task run, never from within one
    this lock is held for.
    """
    import asyncio

    loop = asyncio.get_running_loop()
    key = (loop, agent_id)
    lock = _task_queue_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _task_queue_locks[key] = lock
    return lock


async def _run_assigned_task_inner(task: dict, actor: str = "operator", *,
                                   model_name: str | None = None,
                                   thinking: str | None = None) -> dict:
    """Run an ASSIGNED task now. `task.started` goes on the log BEFORE the run
    (emit before the thing it authorises); the run rides `run_task`, so a
    proposal parks behind the same gate as everything else.

    Unguarded on purpose — `_run_assigned_task` wraps it with the failure
    landing, and the retry driver supplies its own. Call one of those, not this.

    THE per-agent task queue lives here, at the top, so every entry point
    (sync create_and_run_task, the detached background wrapper, assign-and-run,
    and orchestration.retry_parked_task which calls this function directly)
    gets it for free — same lesson as the failure-landing bite mark above:
    when the gate lives in a wrapper, a caller that reaches the raw body skips
    it. Held for the WHOLE run, so a queued sibling waits out the entire turn,
    not just its start.
    """
    async with _agent_task_lock(task["agent_id"]):
        return await _run_one_assigned_task(task, actor=actor,
                                            model_name=model_name, thinking=thinking)


async def _run_one_assigned_task(task: dict, actor: str = "operator", *,
                                 model_name: str | None = None,
                                 thinking: str | None = None) -> dict:
    """The actual run body, moved out of `_run_assigned_task_inner` so the
    queue lock in that function wraps it without nesting the lock's own
    `async with` around a huge block. Not a new entry point — call
    `_run_assigned_task_inner` (or `_run_assigned_task`), never this."""
    from central_command.runtime.run import run_task

    task_id, agent_id = task["id"], task["agent_id"]
    await events.emit(
        "task.started",
        ref_id=task_id,
        payload={"agent_id": agent_id, "title": task["title"]},
        actor=actor,
    )
    await repo.start_task(task_id, None)  # IN_PROGRESS while the agent works
    if agent_id == "orchestrator":
        # D7 phase 2: a task assigned to the orchestrator is a PROJECT — it
        # runs the orchestration loop (plan review → assign → receive →
        # decide), parking on children/operator items instead of proposals.
        from central_command.api import orchestration
        from central_command.runtime.run import RunStopped

        try:
            return {**(await orchestration.run_project(task)), "task_id": task_id}
        except RunStopped as e:
            # The orchestrator loop has no stopped-outcome shape of its own —
            # the session is already parked STOPPED by `_run_live` and the
            # project task stays IN_PROGRESS, exactly as it does for a stall
            # park. Caught HERE rather than in each of the loop's four
            # `_run_live` call sites, because this is the one place both the
            # synchronous and the detached run come through, and letting it
            # escape would have `_run_assigned_task_detached` fail the project.
            return {"task_id": task_id, "stopped": True,
                    "session_id": e.session_id}
    result = await run_task(
        agent_id, task["instructions"],
        model=await resolve_model(agent_id=agent_id, model_name=model_name,
                                  thinking=thinking),
        parent_session_id=task.get("parent_session_id"),
        task_id=task_id,
    )
    if result.get("asked"):
        # The agent is blocked on an ambiguity and asked rather than guessing.
        # The task waits in REVIEW like any other operator-blocking outcome —
        # the question is in the Decisions Inbox, and answering resumes the run.
        await repo.park_task_review(task_id, result["session_id"])
        await events.emit(
            "task.blocked",
            ref_id=task_id,
            payload={
                "item_id": result["item_id"],
                "session_id": result["session_id"],
                "question": result["question"][:500],
            },
            actor=f"agent:{agent_id}",
        )
        return {**result, "task_id": task_id}
    if result.get("stopped"):
        # The operator stopped the run at a node boundary. The task stays
        # IN_PROGRESS with its session STOPPED — paused work, not finished
        # work — and resuming the session continues it in place. Checked BEFORE
        # the `deferred` branch: a stopped run is `deferred: False` and would
        # otherwise be resolved DONE with "stopped by the operator" as its
        # answer, which is the loudest possible lie about what happened.
        return {**result, "task_id": task_id}
    if result["deferred"]:
        await repo.park_task_review(task_id, result["session_id"])
        await events.emit(
            "task.review",
            ref_id=task_id,
            payload={
                "proposal_id": result["proposal_id"],
                "session_id": result["session_id"],
                "intent": result["intent"],
            },
            actor=f"agent:{agent_id}",
        )
    else:
        # A text answer completes the task on the spot: the operator asked,
        # the operator reads the answer — no dismissal review needed. (For an
        # orchestrated child this is the UNGATED path: nothing external
        # changed, so the answer flows back without approval — D7 phase 2.)
        # Link the run's transcript before resolving — text-answer tasks used
        # to lose their session link (only the REVIEW path set it).
        await repo.start_task(task_id, result["session_id"])
        await repo.resolve_task(task_id, "DONE", outcome=str(result["output"]))
        await events.emit(
            "task.completed",
            ref_id=task_id,
            payload={"outcome": str(result["output"])[:500]},
            actor=f"agent:{agent_id}",
        )
        from central_command.api import orchestration

        orchestration.spawn_child_resolved(task)
    return {**result, "task_id": task_id}


async def _attach_source_emails(tasks: list[dict]) -> list[dict]:
    """Resolve each task's `parent_session_id` (the session that proposed or
    fanned out the task) to the email that session was processing, if any —
    one batched `work_items_for_sessions` lookup for the whole page, never
    one query per task. Tasks with no parent session, or whose parent
    session wasn't processing a ledger email (an operator-created task, or a
    parent session mid-chat), get no `source_email` key."""
    parent_ids = {t["parent_session_id"] for t in tasks if t.get("parent_session_id")}
    if not parent_ids:
        return tasks
    work_items = await repo.work_items_for_sessions(list(parent_ids))
    for t in tasks:
        wi = work_items.get(t.get("parent_session_id") or "")
        if wi:
            payload = wi.get("payload") or {}
            t["source_email"] = {
                "subject": wi.get("subject"),
                "from": payload.get("from"),
                "text": payload.get("text"),
            }
    return tasks


@router.get("/tasks")
async def list_tasks(agent_id: str | None = None) -> dict:
    """`agent_id` scopes the task list to one agent (the workspace panel's
    selected agent). Counts stay board-wide — they are the whole queue's
    state, not the filtered view's."""
    from central_command.runtime.roster import taskable_agent_ids

    return {
        "tasks": await _attach_source_emails(await repo.list_tasks(agent_id=agent_id)),
        "counts": await repo.task_counts(),
        # The UI's agent picker comes from the roster rows' truth (D24), never
        # a hardcoded list.
        "taskable_agents": list(await taskable_agent_ids()),
    }


async def _run_assigned_task_detached(task: dict, actor: str, *,
                                      model_name: str | None = None,
                                      thinking: str | None = None) -> None:
    """Background wrapper for `_run_assigned_task`: nobody is holding an open
    request, so the exception the guard re-raises has nowhere to go but the log.
    The record is already carried by `_land_run_failure` — FAILED, or ASSIGNED
    with retry bookkeeping when the dependency was merely DOWN (outage
    equivalence, slice 4), because a provider outage must cost the operator's
    ask a delay, never the ask itself.
    """
    try:
        await _run_assigned_task(task, actor=actor, model_name=model_name,
                                 thinking=thinking)
    except Exception:  # noqa: BLE001 — already on the record; nobody to raise to
        log.exception("task %s: run failed", task["id"])


async def startup_task_sweep() -> None:
    """Re-launch tasks that were queued but never reached `task.started`
    before the process died — plain ASSIGNED, `retry_state is null`, an agent
    set (`repo.tasks_orphaned_at_startup`). STARTUP-ONLY, never periodic: the
    in-memory per-agent queue (`_agent_task_lock`) is empty by definition the
    moment a fresh process boots, so anything still sitting plain-ASSIGNED at
    that instant was genuinely orphaned — a periodic sweep run later could not
    tell "orphaned" apart from "queued in memory, its turn hasn't come yet"
    and would double-launch a task already in flight.

    Deliberate contrast with the orphan-SESSION sweep (`sweep_orphaned_sessions`,
    called just above in the lifespan): an interrupted RUN is FAILED, never
    re-run — the operator didn't re-issue it. A task that never got past
    ASSIGNED to `task.started` is different: it WAS issued and simply never
    began, so launching it now honours the original authorization rather than
    replaying unrequested work.

    Each relaunch goes through `_run_assigned_task_detached`, so it serializes
    on the same per-agent lock as everything else — a restart with many
    orphaned tasks for one agent drains them one at a time, not all at once.

    The sessionless-IN_PROGRESS landing runs FIRST, before any relaunch: a
    task the process died on between `start_task(id, None)` and `_run_live`'s
    session link is invisible to both the ASSIGNED relaunch and the
    session-orphan sweep (null session_id matches nothing), and was stuck on
    the board forever (found live 2026-08-25). Ordering matters — a relaunch
    passes through that same null-session window, so sweeping after the
    relaunches spawn could fail a task that just legitimately restarted.
    """
    for task in await repo.fail_sessionless_in_progress_tasks():
        await events.emit(
            "task.run_failed",
            ref_id=task["id"],
            payload={"error": "run died with its process before its session existed",
                     "transient": False, "swept": True},
            actor="system",
        )
    for task in await repo.tasks_orphaned_at_startup():
        import asyncio

        asyncio.create_task(_run_assigned_task_detached(task, "system"))


async def create_and_run_task(
    instructions: str, title: str | None, agent_id: str | None,
    actor: str = "operator", background: bool = False,
    parent_session_id: str | None = None,
    attachments: list[dict] | None = None,
    model_name: str | None = None,
    thinking: str | None = None,
) -> dict:
    """THE task-create path — the Task Board REST route, the "+" dialog's
    assign flow, direct tasking sugar, the heartbeat's `task.create` action,
    and the orchestration driver's assign_work (D7 phase 2) all land here, so
    validation, events and the run can't diverge. `actor` records who
    initiated (operator | heartbeat:<schedule_id> | agent:orchestrator);
    `parent_session_id` links a child task to the orchestrator session it
    reports back to. `attachments` is the Task Board's create-dialog path only
    — every other caller passes none."""
    from central_command.runtime.roster import taskable_agent_ids

    instructions = instructions.strip()
    if not instructions:
        raise HTTPException(422, "instructions must not be empty")
    if thinking:
        # Validated at the ONE task-create path so the heartbeat's action_params
        # cannot smuggle a level the chat template rejects with a 500 per turn.
        from central_command.runtime.models import THINKING_LEVELS

        if thinking not in THINKING_LEVELS:
            raise HTTPException(
                422, f"thinking must be one of {THINKING_LEVELS} (got {thinking!r})")
    # ponytail: model_name/thinking ride the RUN, not the task row, so a
    # retry-parked task re-runs on the agent's own setting. Persist them on
    # `task` if a schedule's override needs to survive a provider outage.
    if agent_id is not None and agent_id not in await taskable_agent_ids():
        raise HTTPException(422, f"agent {agent_id!r} is not directly taskable")
    if parent_session_id and agent_id == "orchestrator":
        # Star shape, mechanically: the orchestrator never assigns to itself,
        # and no child chain can re-enter orchestration (depth-1).
        raise HTTPException(422, "the orchestrator cannot be the target of an "
                                 "orchestrated assignment")

    # Resolved and validated BEFORE `repo.create_task` — a refusal here must
    # leave no task row, same invariant as chat's `attachments.prepare()`
    # before the turn exists (see api/attachments.py).
    attachment_manifest: list[dict] | None = None
    if attachments:
        if agent_id is None:
            raise HTTPException(422, "attachments require an assignee — an "
                                     "unassigned task has no model to deliver them to")
        from central_command.api import attachments as attachments_mod

        for att in attachments:
            media_type = str((att or {}).get("mimeType") or (att or {}).get("media_type") or "").lower()
            if media_type.startswith(attachments_mod.IMAGE_PREFIX):
                raise HTTPException(422, "task attachments must be documents — "
                                         "images are not supported here")
        try:
            block, attachment_manifest = await attachments_mod.prepare(
                attachments,
                model_id=await attachments_mod.session_model_id(None, agent_id),
            )
        except attachments_mod.AttachmentRefused as exc:
            raise HTTPException(422, exc.message) from exc
        if block:
            instructions = f"{instructions}\n\n{block}"

    task_id = "task_" + uuid.uuid4().hex[:12]
    title = (title or "").strip() or instructions[:80]
    await repo.create_task(task_id, title, instructions, agent_id,
                           parent_session_id=parent_session_id)
    await events.emit(
        "task.created",
        ref_id=task_id,
        payload={
            "title": title,
            "agent_id": agent_id,
            "instructions": instructions[:500],
            **({"parent_session_id": parent_session_id}
               if parent_session_id else {}),
        },
        actor=actor,
    )
    if attachment_manifest:
        # Provenance mirrors `chat.attachment_delivered` — same payload shape,
        # never file bytes.
        await events.emit(
            "task.attachment_delivered",
            ref_id=task_id,
            payload={"agent_id": agent_id, "attachments": attachment_manifest},
            actor=actor,
        )
    if agent_id is None:
        # D7 (recommendation mode): the orchestrator looks at every unassigned
        # task in the background — the POST returns immediately, the
        # recommendation lands on the task card via the event stream, and a
        # routing failure degrades to exactly the old parked-NEW behaviour.
        import asyncio

        from central_command.runtime import orchestrator

        task_row = await repo.get_task(task_id)
        asyncio.create_task(orchestrator.recommend(task_row))
        return {"deferred": False, "task_id": task_id, "status": "NEW"}
    task_row = await repo.get_task(task_id)
    if background:
        # Return as soon as the run is launched: the board shows IN_PROGRESS,
        # the run's outcome (REVIEW / DONE / FAILED) lands on the record via
        # the same paths as a synchronous run.
        import asyncio

        asyncio.create_task(_run_assigned_task_detached(
            task_row, actor, model_name=model_name, thinking=thinking))
        return {
            "deferred": None, "task_id": task_id,
            "status": task_row["status"], "background": True,
        }
    return await _run_assigned_task(task_row, actor=actor,
                                    model_name=model_name, thinking=thinking)


@router.post("/tasks")
async def create_task(body: TaskCreateIn) -> dict:
    return await create_and_run_task(
        body.instructions, body.title, body.agent_id, background=body.background,
        attachments=body.attachments,
    )


# --- D7 phase 2: operator items (plan reviews + questions) + resume sweep ------


class OperatorAnswerIn(BaseModel):
    answer: str = ""
    verdict: str | None = None  # plan_review: 'approved' | 'revise'


@router.post("/operator_items/{item_id}/answer")
async def answer_operator_item(item_id: str, body: OperatorAnswerIn) -> dict:
    from central_command.api import orchestration

    try:
        return await orchestration.answer_item(item_id, body.answer, body.verdict)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@router.get("/routing/agreement")
async def routing_agreement() -> dict:
    """The D7 graduation record: every routing recommendation paired with the
    operator's assignment (accepted vs overridden), fresh from the event log.
    Auto-assign gets decided on this — never before."""
    from central_command.runtime.orchestrator import routing_report

    return await routing_report()


@router.post("/tasks/{task_id}/assign")
async def assign_task(task_id: str, body: TaskAssignIn) -> dict:
    from central_command.runtime.roster import taskable_agent_ids

    if body.agent_id not in await taskable_agent_ids():
        raise HTTPException(422, f"agent {body.agent_id!r} is not directly taskable")
    if not await repo.assign_task(task_id, body.agent_id):
        raise HTTPException(409, "task is not assignable (only NEW tasks are)")
    await events.emit(
        "task.assigned", ref_id=task_id, payload={"agent_id": body.agent_id},
        actor="operator",
    )
    return await _run_assigned_task(await repo.get_task(task_id))


@router.post("/tasks/{task_id}/stop")
async def stop_task(task_id: str) -> dict:
    """Stop this task's live run at its next node boundary.

    Cooperative and immediate to the caller: the flag is set and the turn
    finishes the node it is on, then parks STOPPED with its transcript intact
    (`session.stopped` is what tells the cockpit it landed). RUNNING-only —
    every parked state is cancel-for-good territory, and the 409 says so rather
    than arming a flag that would fire on some later resume.
    """
    from central_command.api import orchestration

    task = await repo.get_task(task_id)
    if task is None:
        raise HTTPException(404, "no such task")
    session_id = task.get("session_id")
    if not session_id:
        raise HTTPException(409, f"task {task_id} has no run to stop "
                                 f"(it is {task['status']})")
    if not await orchestration.request_stop(session_id):
        status = await repo.session_status(session_id)
        raise HTTPException(
            409,
            f"session {session_id} is {status}, not RUNNING — there is no live "
            "run to stop. A parked run resolves through its Inbox item, or "
            "cancel the task for good.",
        )
    return {"ok": True, "task_id": task_id, "session_id": session_id,
            "stop_requested": True}


@router.post("/sessions/{session_id}/resume")
async def resume_session(session_id: str) -> dict:
    """Resume a session the operator stopped — it re-enters on its persisted
    history and lands exactly as an uninterrupted run would."""
    from central_command.api import orchestration

    # model=None on purpose: `_continue_agent` resolves the AGENT's model (and
    # the orchestrator's builder resolves the project's), so naming one here
    # would override a per-session pin with the global default.
    out = await orchestration.resume_stopped_session(session_id)
    if out is None:
        status = await repo.session_status(session_id)
        raise HTTPException(
            409 if status else 404,
            f"session {session_id} is {status or 'unknown'} — only a STOPPED "
            "session can be resumed",
        )
    return out


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str) -> dict:
    """Cancel this task for good — the wind-down, not the pause.

    NEW/ASSIGNED is the original behaviour: nothing has run, so nothing has to
    be unwound. Beyond that, cancelling has to leave the record consistent, and
    the order below is the whole job:

      * every OPEN operator_item on the session is WITHDRAWN — the question is
        moot, and leaving it in the Inbox asks the operator about work they
        just cancelled;
      * every UNDECIDED proposal is WITHDRAWN and its folded siblings released
        through the shared seam, exactly as a rejection releases them (a fold is
        a claim of coverage, never an outcome). APPROVED / EXECUTING /
        RETRY_PENDING proposals are NOT touched: an approval the operator
        already gave executes, and its landing finds a terminal task and no-ops;
      * the session is CLOSED through the one close seam with a mapped reason —
        sessions are never destroyed, so this is a status flip with the
        transcript kept;
      * only then does the task go terminal.

    A task with a LIVE run is refused: stop it first. That is honest (the run is
    still executing a node and would keep going) and smaller than teaching the
    park path a second terminal outcome — and stop-then-cancel is two clicks on
    a lever the operator is already holding.
    """
    from central_command.gateway.gateway import release_folds_for
    from central_command.runtime.converse import close_session

    task = await repo.get_task(task_id)
    if task is None:
        raise HTTPException(404, "no such task")
    prior = task["status"]
    if prior in repo.TASK_TERMINAL:
        raise HTTPException(409, f"task {task_id} is already {prior}")

    session_id = task.get("session_id")
    if session_id and await repo.session_status(session_id) == "RUNNING":
        raise HTTPException(
            409,
            f"task {task_id} has a live run — stop it first, then cancel "
            "(cancelling out from under a running agent would leave its "
            "half-finished turn unaccounted for)",
        )

    withdrawn_items: list[str] = []
    withdrawn_proposals: list[str] = []
    if session_id:
        withdrawn_items = await repo.withdraw_operator_items_for_session(session_id)
        withdrawn_proposals = await repo.withdraw_proposals_for_session(session_id)
        for proposal_id in withdrawn_proposals:
            await release_folds_for(proposal_id, "covering proposal withdrawn")
        if await repo.session_status(session_id) not in ("DONE", "FAILED"):
            await close_session(
                session_id, agent_id=task["agent_id"] or "operator",
                reason="cancelled", actor="operator",
            )

    if not await repo.resolve_task(
        task_id, "CANCELLED", outcome="cancelled by operator"
    ):
        raise HTTPException(409, f"task {task_id} is already terminal")
    await events.emit(
        "task.cancelled", ref_id=task_id,
        payload={"prior_status": prior, "session_id": session_id,
                 "withdrawn_items": withdrawn_items,
                 "withdrawn_proposals": withdrawn_proposals},
        actor="operator",
    )
    return {"ok": True, "prior_status": prior,
            "withdrawn_items": withdrawn_items,
            "withdrawn_proposals": withdrawn_proposals}


@router.post("/agents/{agent_id}/task")
async def task_agent(agent_id: str, body: TaskIn) -> dict:
    """Direct operator tasking (Phase 4). Since SC-2 this is sugar for creating
    an ASSIGNED first-class Task, so Workspace-box taskings show on the Task
    Board like any other task. The `task.*` events supersede the earlier
    `agent.tasked` / `agent.task_answered` kinds (which remain in log history)."""
    from central_command.runtime.roster import taskable_agent_ids

    if not body.instruction.strip():
        raise HTTPException(422, "instruction must not be empty")
    if agent_id not in await taskable_agent_ids():  # checked BEFORE any record hits the log
        raise HTTPException(404, f"agent {agent_id!r} is not directly taskable")
    return await create_task(TaskCreateIn(instructions=body.instruction, agent_id=agent_id))


class CoachIn(BaseModel):
    # Operator-curated inputs: which recorded signals feed the session, and/or
    # the operator's own feedback in their own words.
    signal_ids: list[int] = []
    note: str | None = None


@router.get("/agents/{agent_id}/coaching_signals")
async def coaching_signals(agent_id: str) -> dict:
    """The candidate coaching inputs, for the operator to review and select
    BEFORE any coach run — nothing feeds a session unchosen. The auditor's
    signals are its disagreement record: over-cautious challenges and misses,
    each citing the verdict event."""
    if agent_id == "auditor":
        from central_command.gateway.auditor import auditor_signals

        return {"signals": await auditor_signals()}
    if agent_id == "orchestrator":
        from central_command.runtime.orchestrator import orchestrator_signals

        return {"signals": await orchestrator_signals()}
    from central_command.runtime.run import gather_coaching_signals

    return {"signals": await gather_coaching_signals(agent_id)}


@router.post("/agents/{agent_id}/coach")
async def coach_agent_route(agent_id: str, body: CoachIn) -> dict:
    return await coach_agent(agent_id, body)


async def coach_agent(agent_id: str, body: CoachIn, *, background: bool = True) -> dict:
    """D5: ask the agent to propose its own charter edit from the inputs the
    operator selected (and/or wrote). The request is logged FIRST so the
    operator's note is citable evidence; the edit parks in the Decisions Inbox
    like any other proposal — nothing changes the charter without approval.

    A coach run is a real model run and takes minutes (109s measured
    2026-08-01), so by default it runs BEHIND A TASK RECORD, detached: this
    returns as soon as the task exists and the run is launched, the board shows
    it IN_PROGRESS, and the outcome lands on the record via the same events as
    any other task. Holding the request open instead is what made the cockpit
    report "Timeout" at 30s on a run that had in fact succeeded.

    `background=False` runs it inline and returns the run's own result. That is
    for tests and acceptance scripts — it is NOT reachable from the wire, on
    purpose: an operator surface must never depend on a multi-minute request."""
    import asyncio

    from central_command.runtime.roster import roster_ids
    from central_command.runtime.run import COACH_ID, run_coach

    if agent_id not in await roster_ids():  # every ACTIVE roster agent is coachable — the rule
        # Distinguish "no such agent" from "retired": the Agents page shows a
        # retired agent's full profile, so "unknown agent" would be a plain lie
        # about something the operator is looking at.
        if agent_id in await roster_ids(include_retired=True):
            raise HTTPException(
                409,
                f"agent {agent_id!r} is RETIRED — reactivate it before coaching; "
                "its charter and record stay readable either way",
            )
        raise HTTPException(404, f"unknown agent {agent_id!r}")
    if not body.signal_ids and not (body.note or "").strip():
        raise HTTPException(422, "select at least one signal or write a note")
    requested = await events.emit(
        "agent.coach_requested",
        ref_id=agent_id,
        payload={"signal_ids": body.signal_ids, "note": (body.note or "").strip() or None},
        actor="operator",
    )
    signals = None
    if agent_id == "auditor":
        from central_command.gateway.auditor import auditor_signals, ensure_registered

        await ensure_registered()
        signals = await auditor_signals()
    elif agent_id == "orchestrator":
        from central_command.runtime.orchestrator import ensure_registered, orchestrator_signals

        await ensure_registered()
        signals = await orchestrator_signals()
    # No explicit `model=` here: the coach DRAFTS, `agent_id` is the coaching
    # TARGET, and `resolve_model` uses whatever id it is given for BOTH the
    # per-agent model override and the per-agent proxy key (spend
    # attribution). An explicit model wins over `build_coach_agent`'s own
    # `resolve_model(..., agent_id="coach")` inside `run_coach` — passing one
    # resolved against the target billed and modelled the run as the target,
    # even though the session row, proposal and pedigree all say `coach`.
    # Let `run_coach` -> `build_coach_agent` resolve it against the coach.
    kwargs = {
        "signal_ids": body.signal_ids or [],
        "note": body.note,
        "note_event_id": requested["id"],
        "signals": signals,
    }
    if not background:
        return await run_coach(agent_id, **kwargs)

    # The task record IS the progress surface. Created here rather than through
    # `create_and_run_task` because a coach run is not a `run_task` run: it
    # carries the operator's curated signals and verifies the coach's citations
    # against them, neither of which a free-text task instruction can express.
    # (That is also why `coach` is not taskable — see roster.py.)
    task_id = "task_" + uuid.uuid4().hex[:12]
    title = f"Coach {agent_id}"
    instructions = (
        f"Draft a charter edit for {agent_id} from the operator's curated "
        f"coaching signals.\n\nSelected signals: "
        f"{', '.join(str(i) for i in body.signal_ids) or '(none)'}\n"
        f"Operator note: {(body.note or '').strip() or '(none)'}"
    )
    # The task belongs to the DRAFTER (`coach`), never the coaching target —
    # the same rule the proposal row follows. The target is in the title and
    # the instructions, where it is read, not claimed.
    await repo.create_task(task_id, title, instructions, COACH_ID)
    await events.emit(
        "task.created",
        ref_id=task_id,
        payload={"title": title, "agent_id": COACH_ID, "target": agent_id,
                 "instructions": instructions[:500]},
        actor="operator",
    )
    await events.emit(
        "task.started",
        ref_id=task_id,
        payload={"agent_id": COACH_ID, "title": title},
        actor="operator",
    )
    await repo.start_task(task_id, None)  # IN_PROGRESS while the coach works
    asyncio.create_task(_run_coach_detached(task_id, agent_id, kwargs))
    return {
        "deferred": None, "background": True, "task_id": task_id,
        "target": agent_id, "status": "IN_PROGRESS",
    }


async def _run_coach_detached(task_id: str, agent_id: str, kwargs: dict) -> None:
    """Background wrapper for a coach run: nobody is holding an open request to
    see a failure, so the record must carry it — the landing here is the same
    one `_run_assigned_task` performs for an ordinary task.

    No retry-on-outage (`park_task_retry`) on this path: the retry driver
    re-runs an ASSIGNED task through `run_task`, which for the coach would run
    it with NO curated signals and NO citation check. A failed coach run is
    FAILED, and the operator starts a new one with the signals still selected.
    """
    from central_command.runtime.run import COACH_ID, run_coach

    try:
        result = await run_coach(agent_id, task_id=task_id, **kwargs)
    except Exception as e:  # noqa: BLE001 — any run error must reach the record
        await repo.resolve_task(task_id, "FAILED", outcome=f"coach run failed: {e}")
        await events.emit(
            "task.run_failed",
            ref_id=task_id,
            payload={"error": str(e)[:500], "target": agent_id},
            actor=f"agent:{COACH_ID}",
        )
        return

    session_id = result.get("session_id")
    if result.get("asked"):
        # The coach hesitated rather than drafting from a vague ask. Same
        # landing as any other question: REVIEW behind the operator item.
        await repo.park_task_review(task_id, session_id)
        await events.emit(
            "task.blocked",
            ref_id=task_id,
            payload={"item_id": result.get("item_id"), "session_id": session_id,
                     "question": str(result.get("question") or "")[:500]},
            actor=f"agent:{COACH_ID}",
        )
        return
    if result.get("deferred"):
        # A drafted edit is a PROPOSAL. The task holds in REVIEW; deciding the
        # proposal resolves it (`gateway._resolve_task_after`, by session).
        await repo.park_task_review(task_id, session_id)
        await events.emit(
            "task.review",
            ref_id=task_id,
            payload={"proposal_id": result["proposal_id"],
                     "session_id": session_id, "intent": result["intent"]},
            actor=f"agent:{COACH_ID}",
        )
        return
    # No edit proposed — the coach answered in text, or nothing was selected.
    # That completes the task: the operator asked, the operator reads it.
    if session_id:
        await repo.start_task(task_id, session_id)
    outcome = str(result.get("output") or "")
    await repo.resolve_task(task_id, "DONE", outcome=outcome)
    await events.emit(
        "task.completed",
        ref_id=task_id,
        payload={"outcome": outcome[:500]},
        actor=f"agent:{COACH_ID}",
    )


@router.post("/emails")
async def feed_email(body: EmailIn) -> dict:
    """Hand-feed one email and process it immediately.

    Since M9 this goes *through the Work Ledger* rather than straight to the
    agent. It used to be a second, unthrottled path into the runtime that
    bypassed the ledger entirely — meaning hand-fed mail got no durability, no
    thread folding, and no ledger row. Now the only difference from queued work
    is that it skips the backpressure valves, which is the operator's call to
    make; everything else is the shared path.
    """
    enrolled = await ledger.enroll_email(
        body.text, feed="live", source="api", allow_repeat=True
    )
    await events.emit(
        "work.enrolled",
        ref_id=enrolled["message_id"],
        payload={"subject": enrolled.get("subject"), "feed": "live", "hand_fed": True},
        actor="operator",
    )
    if not enrolled["enrolled"]:
        # Real Message-ID we have already seen — don't silently reprocess it.
        return {"deferred": False, "duplicate": True, "message_id": enrolled["message_id"]}

    result = await dispatch_item(enrolled["item_id"], model=await resolve_model(agent_id="inbox-triage"))
    if result is None:
        raise HTTPException(409, "thread is busy — another message on it is in flight")
    return result


class DocumentIn(BaseModel):
    text: str
    title: str = ""
    # A stable id from the source system (a Confluence page id, once that
    # adapter exists). Absent, identity falls back to the content hash.
    doc_id: str | None = None


@router.post("/documents")
async def feed_document(body: DocumentIn) -> dict:
    """Hand-feed one document and process it immediately (the steward's path).

    The twin of `POST /emails`, deliberately: enroll into the Work Ledger, then
    `dispatch_item` — never a direct run. The ledger is what makes the work
    durable, claimable and idempotent, and a second door into the runtime is
    exactly the gap M9 closed on the email side.

    No `model=` is passed. `feed_email` passes the inbox-triage model because
    that is the agent it feeds; copying that here would run the steward on
    triage's demo model, whose proposal is a Jira due-date change.
    """
    if not body.text.strip():
        raise HTTPException(422, "a document needs text")
    enrolled = await ledger.enroll_document(
        body.title, body.text, doc_id=body.doc_id, feed="live", source="api"
    )
    await events.emit(
        "work.enrolled",
        ref_id=enrolled["message_id"],
        payload={"subject": enrolled.get("subject"), "feed": "live",
                 "kind": "document", "hand_fed": True},
        actor="operator",
    )
    if not enrolled["enrolled"]:
        # Same words (or the same doc_id) already enrolled — extracting it
        # twice would propose the same facts twice.
        return {"deferred": False, "duplicate": True,
                "message_id": enrolled["message_id"]}

    result = await dispatch_item(enrolled["item_id"])
    if result is None:
        raise HTTPException(409, "this document is already being processed")
    return result


# --- Ingestion: Work Ledger + dispatcher (Phase 2) ---------------------------
# `POST /emails` above is the Phase-1 direct path (runs the agent immediately).
# These routes are the durable path: enroll -> ledger -> dispatcher pulls.


@router.post("/ingest/document")
async def enroll_document(body: DocumentIn) -> dict:
    """Enroll one document on the live feed without running it — the durable
    twin of `POST /ingest/email`. Idempotent on the document's stable key."""
    if not body.text.strip():
        raise HTTPException(422, "a document needs text")
    result = await ledger.enroll_document(
        body.title, body.text, doc_id=body.doc_id, feed="live", source="api"
    )
    if result["enrolled"]:
        await events.emit(
            "work.enrolled",
            ref_id=result["message_id"],
            payload={"subject": result.get("subject"), "feed": "live",
                     "kind": "document"},
            actor="operator",
        )
    return result


@router.post("/ingest/backlog")
async def enroll_backlog(body: BacklogIn) -> dict:
    """Bulk-enroll a fixture corpus onto the backlog feed. Resumable."""
    try:
        result = await ledger.enroll_fixture_dir(body.dir)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    await events.emit("work.enrolled.bulk", ref_id=body.dir, payload=result, actor="operator")
    return result


class BacklogSweepIn(BaseModel):
    cutoff_date: str | None = None  # overrides CC_BACKLOG_CUTOFF_DATE


@router.get("/feed")
async def feed_status() -> dict:
    # After a restart the sweeper's in-memory state is empty even though the
    # durable cursor exists — hydrate it so the dashboard reads the truth.
    if sweeper.state is None:
        sweeper.state = await repo.get_feed_state("backlog_sweep")
    return {**feed.status(), "backlog": sweeper.status()}


@router.post("/feed/backlog/start")
async def backlog_start(body: BacklogSweepIn) -> dict:
    try:
        return await sweeper.start(cutoff_date=body.cutoff_date)
    except ValueError as e:
        raise HTTPException(422, str(e))


class ReopenIn(BaseModel):
    note: str | None = None


@router.get("/audit")
async def audit_status() -> dict:
    """The auditor's standing configuration — surfaced so the operator always
    knows which gate is human and which is graduated.
    Operator-diagnostic endpoint — no code consumer; curl it (see STATUS.md)."""
    from central_command.config import settings
    from central_command.gateway.auditor import AUDIT_ACTION_CLASS, BULK_AUDIT_ACTION_CLASS

    return {
        "enabled": settings.auditor_enabled,
        "mode": settings.auditor_mode if settings.auditor_enabled else None,
        "graduated_action_class": AUDIT_ACTION_CLASS,
        "graduated_action_classes": [AUDIT_ACTION_CLASS, BULK_AUDIT_ACTION_CLASS],
    }


@router.get("/audit/agreement")
async def audit_agreement() -> dict:
    """The shadow-vs-operator agreement record (D8 graduation evidence),
    computed fresh from the event log.
    Operator-diagnostic endpoint — no code consumer; curl it (see STATUS.md)."""
    from central_command.gateway.auditor import agreement_report

    return await agreement_report()


@router.get("/steward/agreement")
async def steward_agreement() -> dict:
    """The knowledge steward's confidence-vs-verdict record — the evidence a
    bulk-approval threshold would have to graduate on, computed fresh from the
    event log. Nothing here changes a gate; everything is still human-gated.
    Operator-diagnostic endpoint — no code consumer; curl it (see STATUS.md)."""
    from central_command.gateway.steward_agreement import steward_agreement_report

    return await steward_agreement_report()


@router.post("/dismissals/audit")
async def audit_parked_dismissals() -> dict:
    """Run the auditor over every parked dismissal that lacks a verdict — the
    operator's 'audit the backlog now' button, and how items parked before the
    auditor was enabled get their verdicts."""
    from central_command.config import settings
    from central_command.gateway import auditor

    if not settings.auditor_enabled:
        raise HTTPException(409, "auditor is disabled (CC_AUDITOR_ENABLED)")

    audited = confirmed = 0
    for it in await repo.list_work_items("DISMISS_PENDING", include_payload=True):
        payload = it.get("payload") or {}
        if payload.get("audit"):
            continue  # already has a verdict; the operator has seen or will see it
        record = await auditor.audit_dismissal(
            it["id"],
            it.get("subject"),
            payload.get("text") or "",
            payload.get("dismissal_rationale") or "no rationale given",
            allow_auto_confirm=not payload.get("operator_note"),
        )
        if record:
            audited += 1
            confirmed += 1 if record.get("auto_confirmed") else 0
    return {"audited": audited, "auto_confirmed": confirmed}


@router.post("/work/{item_id}/confirm")
async def confirm_dismissal(item_id: str) -> dict:
    if not await repo.confirm_dismissal(item_id):
        raise HTTPException(409, "item is not awaiting dismissal confirmation")
    await events.emit(
        "work.dismissal_confirmed", ref_id=item_id, payload={}, actor="operator"
    )
    return {"ok": True}


@router.post("/work/confirm_all")
async def confirm_all_dismissals() -> dict:
    ids = await repo.confirm_all_dismissals()
    if ids:
        await events.emit(
            "work.dismissal_confirmed",
            ref_id=None,
            payload={"count": len(ids), "item_ids": ids},
            actor="operator",
        )
    return {"ok": True, "confirmed": len(ids)}


class BulkDismissQueryIn(BaseModel):
    query: str


class BulkDismissExecuteIn(BaseModel):
    query: str
    match_digest: str


def _bulk_dismiss_query(raw: str) -> str:
    query = (raw or "").strip()
    if not query:
        raise HTTPException(400, "query must not be empty")
    if len(query) > 500:
        raise HTTPException(400, "query too long (max 500 chars)")
    return query


@router.post("/work/bulk_dismiss/preview")
async def bulk_dismiss_preview(body: BulkDismissQueryIn) -> dict:
    """Resolve a Gmail query + hydrate samples — the operator's dry run before
    committing (2026-08-17 bulk-dismissal design, slice 1)."""
    query = _bulk_dismiss_query(body.query)
    try:
        out = await bulk_dismiss.preview(query)
    except ValueError as e:
        raise HTTPException(400, str(e))
    # the digest stands in for the full set — no need to ship 2,500 ids to the UI
    out.pop("message_ids", None)
    return out


@router.post("/work/bulk_dismiss")
async def bulk_dismiss_commit(body: BulkDismissExecuteIn) -> dict:
    """Dismiss every UNPROCESSED row the query resolves to, ungated (operator
    path — the operator is the author). Refuses if the matched set drifted since
    preview's digest."""
    query = _bulk_dismiss_query(body.query)
    try:
        return await bulk_dismiss.execute(query, body.match_digest)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/work/{item_id}/requeue")
async def requeue_work_item(item_id: str) -> dict:
    """The operator's redo for a FAILED email: back to UNPROCESSED for a fresh
    run — e.g. after the capability its proposal needed gets built.

    Hand-fed items are re-parsed from their stored raw text first, so a payload
    written under a since-fixed parser bug is healed rather than replayed
    forever (gmail-fed items re-hydrate at claim anyway). Identity is never
    touched — message_id is immutable."""
    item = await repo.get_work_item(item_id)
    if item is None:
        raise HTTPException(404, "no such work item")
    fresh = None
    raw = (item.get("payload") or {}).get("raw")
    if raw:
        parsed = ledger.parse_email(raw)
        fresh = {**item["payload"], "text": parsed["body"],
                 "from": parsed["from"] or item["payload"].get("from")}
    if not await repo.requeue_failed_work_item(item_id, payload=fresh):
        raise HTTPException(409, "item is not FAILED")
    await events.emit(
        "work.requeued", ref_id=item_id,
        payload={"reparsed": fresh is not None}, actor="operator",
    )
    return {"ok": True, "reparsed": fresh is not None}


@router.post("/work/{item_id}/ack_failure")
async def acknowledge_failure(item_id: str) -> dict:
    """The operator closes a failure not worth retrying. The row stays FAILED —
    the record is the truth — but stops asking for action."""
    if not await repo.acknowledge_failed_work_item(item_id):
        raise HTTPException(409, "item is not FAILED (or already acknowledged)")
    await events.emit("work.failure_acknowledged", ref_id=item_id, payload={}, actor="operator")
    return {"ok": True}


@router.post("/work/{item_id}/reopen")
async def reopen_work_item(item_id: str, body: ReopenIn) -> dict:
    if not await repo.reopen_work_item(item_id, body.note):
        raise HTTPException(409, "item is not awaiting dismissal confirmation")
    await events.emit(
        "work.reopened", ref_id=item_id, payload={"note": body.note}, actor="operator"
    )
    return {"ok": True}


@router.get("/coaching")
async def coaching() -> dict:
    """The coaching surface: per-agent outcomes, the operator's rejection
    feedback verbatim, and reopen notes — every one a coaching moment already
    on the record. Composed fresh from the log and the proposal table."""
    counts = await repo.proposal_counts_by_agent()

    decided = await repo.list_events_of_kinds(["proposal.decided"])
    props = {p["id"]: p for p in await repo.list_proposals()}
    rejections = []
    for e in decided:
        payload = e.get("payload") or {}
        if payload.get("verdict") != "rejected":
            continue
        p = props.get(e["ref_id"]) or {}
        rejections.append({
            "proposal_id": e["ref_id"],
            "feedback": payload.get("feedback"),
            "at": e["created_at"],
            "intent": p.get("intent"),
            "agent_id": p.get("agent_id"),
        })

    reopen_events = await repo.list_events_of_kinds(["work.reopened"])
    subjects = await repo.work_item_subjects(
        [e["ref_id"] for e in reopen_events if e["ref_id"]]
    )
    reopens = [
        {"item_id": e["ref_id"], "note": (e.get("payload") or {}).get("note"),
         "at": e["created_at"], "subject": subjects.get(e["ref_id"])}
        for e in reopen_events
    ]

    return {
        "agents": await repo.list_agents(),
        "proposal_counts": counts,
        "rejections": rejections[::-1],  # newest first
        "reopens": reopens[::-1],
    }


@router.get("/activity/overview")
async def activity_overview() -> dict:
    """Current operational state, in ONE payload.

    Composed server-side for the reason `_decisions_list` already documents:
    the operator must never read a dispatch number from one instant beside a
    ledger number from another. Everything here is CURRENT STATE, never
    history — cadence liveness is `last_fired_at`, not a replay of
    `heartbeat.fired` (the 2026-07-25 quiet-actions rule).

    `failures` is the consumer the ledger has been missing: `routes.py`'s
    `/ledger` note has said "the Failures strip needs failure_ack" since the
    requeue/ack endpoints landed, and nothing ever rendered it. Acknowledged
    rows stay FAILED — the record is the truth — but drop out of here, because
    this list means "still wants a decision".
    """
    failures = [
        item for item in await repo.list_work_items("FAILED", include_payload=True)
        if not (item.get("payload") or {}).get("failure_ack")
    ]
    schedules = [
        {"id": s["id"], "name": s["name"], "enabled": s["enabled"],
         "action_kind": s["action_kind"], "last_fired_at": s.get("last_fired_at")}
        for s in await repo.list_heartbeat_schedules()
    ]
    return {
        "dispatch": await dispatcher.status(),
        "oldest_unprocessed_at": await repo.oldest_unprocessed_at(),
        "failures": failures,
        "dismiss_pending": await repo.count_dismiss_pending(),
        "heartbeat": _heartbeat_engine().status(),
        "schedules": schedules,
    }


@router.get("/dispatch")
async def dispatch_status() -> dict:
    return await dispatcher.status()


@router.post("/dispatch/step")
async def dispatch_step() -> dict:
    """Pull exactly one item, respecting the valves. The demo-friendly path:
    watch backpressure work one click at a time."""
    result = await dispatch_once(model=await resolve_model(agent_id="inbox-triage"))
    if result is None:
        return {"dispatched": False, **await dispatcher.status()}
    return {"dispatched": True, "result": result}


# --- lines of effort (2026-08-12) -----------------------------------------------
# The Goals screen's read, plus operator-direct creation. Creating a line of
# effort is the operator declaring their own goal — trusted input, no gate
# (the gate is on the AGENT's check-ins and recalibrations, which ride
# loe.record_checkin / loe.update through the Decisions Inbox).


class LoeIn(BaseModel):
    id: str
    name: str
    cadence: str = "weekly"
    agent_id: str
    semantic: dict
    presentation: dict = {}


async def _loe_view(row: dict, history_limit: int = 12) -> dict:
    """The wire shape the cockpit's `Loe` interface declares
    (web/src/features/loe/types.ts). Guarded by a BACKEND test — a hand-
    declared frontend interface cannot catch a field the backend never sends.
    """
    history = await repo.list_loe_checkins(row["id"], limit=history_limit)
    trimmed = [
        {"light": c["light"], "confidence": c["confidence"],
         "summary": c["summary"], "created_at": c["created_at"],
         "semantic_version": c["semantic_version"]}
        for c in history
    ]
    # Full semantic version history for the detail panel: the current version
    # (superseded_at=None) followed by the archived ones, newest first.
    semantic_history = [
        {"version": row["semantic_version"], "semantic": row["semantic"],
         "superseded_at": None},
        *await repo.list_loe_semantic_versions(row["id"]),
    ]
    return {
        "id": row["id"],
        "name": row["name"],
        "cadence": row["cadence"],
        "status": row["status"],
        "agent_id": row["agent_id"],
        "semantic": row["semantic"],
        "semantic_version": row["semantic_version"],
        "presentation": row["presentation"],
        "latest": trimmed[0] if trimmed else None,
        "history": trimmed,
        "semantic_history": semantic_history,
    }


@router.get("/loe")
async def list_loes(include_retired: bool = False, history: int = 12) -> dict:
    history = max(1, min(history, 200))
    rows = await repo.list_loes(include_retired=include_retired)
    return {"loes": [await _loe_view(r, history_limit=history) for r in rows]}


@router.post("/loe")
async def create_loe(body: LoeIn) -> dict:
    loe_id = body.id.strip()
    if not loe_id:
        raise HTTPException(422, "id is required")
    if await repo.get_loe(loe_id) is not None:
        raise HTTPException(409, f"line of effort {loe_id!r} already exists")
    try:
        row = await repo.create_loe(
            loe_id, body.name.strip(), body.cadence, body.agent_id,
            body.semantic, body.presentation,
        )
    except Exception as e:  # duplicate id fails loud — ids are operator-facing
        raise HTTPException(409, f"line of effort {loe_id!r} already exists") from e
    await events.emit(
        "loe.created",
        ref_id=loe_id,
        payload={"name": row["name"], "cadence": row["cadence"],
                 "agent_id": row["agent_id"]},
        actor="operator",
    )
    return await _loe_view(row)


# --- heartbeat (Phase 4, D27) ---------------------------------------------------
# Recurring team work as governed data. Approval attaches to what the fired
# work DOES, never to the trigger — see central_command/heartbeat/__init__.py.


class HeartbeatScheduleIn(BaseModel):
    name: str
    schedule_kind: str
    schedule: dict
    action_kind: str
    action_params: dict = {}
    id: str | None = None  # derived from name when omitted


class HeartbeatSchedulePatchIn(BaseModel):
    name: str | None = None
    schedule_kind: str | None = None
    schedule: dict | None = None
    action_kind: str | None = None
    action_params: dict | None = None


class HeartbeatToggleIn(BaseModel):
    enabled: bool


def _heartbeat_engine():
    from central_command.heartbeat import engine

    return engine


async def _validate_heartbeat_schedule(
    schedule_kind: str, schedule: dict, action_kind: str, action_params: dict
) -> None:
    from central_command.heartbeat import actions as hb_actions
    from central_command.heartbeat.engine import validate_schedule
    from central_command.runtime.roster import taskable_agent_ids

    try:
        validate_schedule(schedule_kind, schedule)
        hb_actions.validate_action(action_kind, action_params)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    # Create-time UX; fire time re-enforces via the one task-create path.
    if action_kind == "task.create":
        agent_id = action_params.get("agent_id")
        if agent_id not in await taskable_agent_ids():
            raise HTTPException(422, f"agent {agent_id!r} is not directly taskable")


async def _heartbeat_schedule_view(row: dict) -> dict:
    """A schedule row + what the crons UI needs: the next due time (computed
    statically, engine running or not) and the last run's outcome from the
    event log (run history has no second table)."""
    from datetime import datetime, timezone

    from central_command.heartbeat.engine import compute_next_due

    due = compute_next_due(
        row["schedule_kind"], row["schedule"], datetime.now(timezone.utc)
    )
    last = await repo.list_events(
        ref_id=row["id"], kind="heartbeat", newest_first=True, limit=10
    )
    outcome = next(
        (e for e in last if e["kind"] in ("heartbeat.completed", "heartbeat.error")),
        None,
    )
    status = (
        None if outcome is None
        else "error" if outcome["kind"] == "heartbeat.error" else "ok"
    )
    error = outcome["payload"].get("error") if outcome else None

    # A quiet firing (nothing to do) writes no outcome event on purpose, so a
    # recorded error must not outlive the recovery: `last_fired_at` newer than
    # the last outcome means every firing since then ran clean — an error would
    # have written one. Without this, one failure would read "error" forever.
    if status == "error":
        fired_at, outcome_at = row.get("last_fired_at"), outcome.get("created_at")
        if isinstance(fired_at, datetime) and isinstance(outcome_at, datetime):
            try:
                if fired_at > outcome_at:
                    status, error = "ok", None
            except TypeError:  # naive/aware mismatch — keep the recorded error
                pass

    return {
        **row,
        "next_due": due.isoformat() if due else None,
        "last_status": status,
        "last_error": error,
    }


@router.get("/heartbeat")
async def heartbeat_status() -> dict:
    return _heartbeat_engine().status()


@router.post("/heartbeat/start")
async def heartbeat_start() -> dict:
    engine = _heartbeat_engine()
    await engine.start()
    return engine.status()


@router.post("/heartbeat/stop")
async def heartbeat_stop() -> dict:
    engine = _heartbeat_engine()
    await engine.stop()
    return engine.status()


@router.get("/heartbeat/schedules")
async def list_heartbeat_schedules() -> dict:
    from central_command.heartbeat.actions import ACTIONS

    rows = await repo.list_heartbeat_schedules()
    return {
        "schedules": [await _heartbeat_schedule_view(r) for r in rows],
        "actions": {
            k: {"description": s.description, "params": s.params,
                "required": list(s.required)}
            for k, s in ACTIONS.items()
        },
    }


@router.post("/heartbeat/schedules")
async def create_heartbeat_schedule(body: HeartbeatScheduleIn) -> dict:
    from central_command.runtime.templates import agent_id_for

    await _validate_heartbeat_schedule(
        body.schedule_kind, body.schedule, body.action_kind, body.action_params
    )
    schedule_id = (body.id or "").strip() or agent_id_for(body.name)
    if not schedule_id:
        raise HTTPException(422, "name must yield a non-empty id")
    try:
        await repo.create_heartbeat_schedule(
            schedule_id, body.name.strip(), body.schedule_kind, body.schedule,
            body.action_kind, body.action_params,
        )
    except Exception as e:  # duplicate id fails loud — ids are operator-facing
        raise HTTPException(409, f"schedule {schedule_id!r} already exists") from e
    await events.emit(
        "heartbeat.schedule.created",
        ref_id=schedule_id,
        payload={"name": body.name, "schedule_kind": body.schedule_kind,
                 "schedule": body.schedule, "action_kind": body.action_kind},
        actor="operator",
    )
    row = await repo.get_heartbeat_schedule(schedule_id)
    return await _heartbeat_schedule_view(row)


@router.patch("/heartbeat/schedules/{schedule_id}")
async def patch_heartbeat_schedule(
    schedule_id: str, body: HeartbeatSchedulePatchIn
) -> dict:
    row = await repo.get_heartbeat_schedule(schedule_id)
    if row is None:
        raise HTTPException(404, "no such schedule")
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(422, "nothing to patch")
    merged = {**row, **fields}
    await _validate_heartbeat_schedule(
        merged["schedule_kind"], merged["schedule"],
        merged["action_kind"], merged["action_params"],
    )
    await repo.update_heartbeat_schedule(schedule_id, fields)
    await events.emit(
        "heartbeat.schedule.updated",
        ref_id=schedule_id,
        payload={"fields": sorted(fields)},
        actor="operator",
    )
    return await _heartbeat_schedule_view(await repo.get_heartbeat_schedule(schedule_id))


@router.delete("/heartbeat/schedules/{schedule_id}")
async def delete_heartbeat_schedule(schedule_id: str) -> dict:
    snapshot = await repo.delete_heartbeat_schedule(schedule_id)
    if snapshot is None:
        raise HTTPException(404, "no such schedule")
    snapshot.pop("created_at", None), snapshot.pop("updated_at", None)
    snapshot.pop("last_fired_at", None)
    await events.emit(
        "heartbeat.schedule.deleted",
        ref_id=schedule_id,
        payload={"snapshot": snapshot},  # the log keeps history; the table need not
        actor="operator",
    )
    return {"ok": True}


@router.post("/heartbeat/schedules/{schedule_id}/toggle")
async def toggle_heartbeat_schedule(schedule_id: str, body: HeartbeatToggleIn) -> dict:
    if not await repo.set_heartbeat_enabled(schedule_id, body.enabled):
        raise HTTPException(404, "no such schedule")
    await events.emit(
        "heartbeat.schedule.toggled",
        ref_id=schedule_id,
        payload={"enabled": body.enabled},
        actor="operator",
    )
    return await _heartbeat_schedule_view(await repo.get_heartbeat_schedule(schedule_id))


@router.post("/heartbeat/schedules/{schedule_id}/run")
async def run_heartbeat_schedule(schedule_id: str) -> dict:
    """The operator's test button — fires the identical fired→action→outcome
    path, engine running or not, enabled or not."""
    try:
        return await _heartbeat_engine().run_now(schedule_id)
    except KeyError:
        raise HTTPException(404, "no such schedule") from None


@router.get("/heartbeat/schedules/{schedule_id}/runs")
async def heartbeat_schedule_runs(schedule_id: str, limit: int = 50) -> dict:
    """Run history straight from the event log."""
    return {
        "events": await repo.list_events(
            ref_id=schedule_id, kind="heartbeat",
            newest_first=True, limit=max(1, min(limit, 200)),
        )
    }


@router.get("/proposals/{proposal_id}")
async def get_proposal(proposal_id: str) -> dict:
    row = await repo.load_proposal(proposal_id)
    if row is None:
        raise HTTPException(404, "proposal not found")

    # A reviewer needs two things the proposal itself doesn't carry: the email it
    # came from (to check the agent read it right, rather than trusting its
    # summary) and any sibling emails approval would also mark resolved.
    row["source_emails"] = await repo.work_items_for_session(row["session_id"])
    row["folds"] = await repo.work_items_folded_into(proposal_id)

    # Deterministic policy flags, computed at review time only — a due date
    # that was fine when approved naturally falls into the past afterwards,
    # and history must not flag amber for it.
    if row["status"] in ("AWAITING_HUMAN", "PROPOSED"):
        from central_command.gateway import policy

        row["policy_flags"] = policy.check_actions(row["actions"])
        # D25: was the proposal within the agent's pack grants? Advisory —
        # surfaces grant/charter drift to the reviewer, never blocks.
        from central_command.runtime.packs import granted_capability_names, granted_packs

        try:
            granted = granted_capability_names(await granted_packs(row["agent_id"]))
        except ValueError:  # an unknown pack name in the DB — surface, don't hide
            granted = set()
        row["policy_flags"] += policy.check_grants(row["actions"], granted)
        # A charter edit's TEXT is checked too: guidance quoting a capability
        # the registry doesn't have programs the agent to propose impossible
        # actions (the charter-v2 incident).
        for a in row["actions"]:
            if a.get("capability", "").split("@")[0] == "charter.update":
                row["policy_flags"] += policy.check_charter_content(
                    str((a.get("arguments") or {}).get("content", ""))
                )

        # A charter edit is reviewed as a DIFF against the CURRENT charter —
        # the reviewer sees the change itself, never a wall of replacement
        # text (D5 rework, the operator's spec).
        import difflib

        for a in row["actions"]:
            if a.get("capability", "").split("@")[0] != "charter.update":
                continue
            args = a.get("arguments") or {}
            target = args.get("agent_id", "")
            current = await repo.current_charter(target)
            if current is None:
                from central_command.runtime.agent import builtin_charter

                old, label = builtin_charter(target), "built-in v0"
            else:
                old, label = current["content"], f"v{current['version']}"
            a["charter_diff"] = "\n".join(difflib.unified_diff(
                old.splitlines(), str(args.get("content", "")).splitlines(),
                fromfile=f"charter {label}", tofile="proposed", lineterm="",
            ))

        # skill.doc_add is reviewed as a diff against the skill's CURRENT doc
        # version too — same rationale, same D5 spec. skill.create has no
        # prior version to diff against (that's the whole point of "create").
        for a in row["actions"]:
            if a.get("capability", "").split("@")[0] != "skill.doc_add":
                continue
            args = a.get("arguments") or {}
            skill_id, doc_key = args.get("skill_id", ""), args.get("doc_key", "")
            history = (
                await repo.skill_doc_history(skill_id, doc_key)
                if skill_id and doc_key else []
            )
            if not history:
                continue
            current = history[0]  # ordered version desc — the current one
            a["skill_diff"] = "\n".join(difflib.unified_diff(
                (current["content"] or "").splitlines(),
                str(args.get("content", "")).splitlines(),
                fromfile=f"{doc_key} v{current['version']}", tofile="proposed", lineterm="",
            ))

        # mcp.sync_source embeds file bytes captured at PROPOSE time (the
        # sandbox's only exit — CLAUDE.md's mcp.sync_source doctrine): diff
        # each proposed file against what is CURRENTLY on disk, through the
        # exact path resolution the Executor writes through, so the diff the
        # reviewer sees is the diff the write will produce.
        from central_command.gateway.executor import _MCP_SERVER_ID_RE, _mcp_bad_path, _mcp_servers_root

        for a in row["actions"]:
            if a.get("capability", "").split("@")[0] != "mcp.sync_source":
                continue
            args = a.get("arguments") or {}
            server_id = args.get("server_id", "")
            root = _mcp_servers_root() / server_id if _MCP_SERVER_ID_RE.match(server_id or "") else None
            diffs = []
            for f in args.get("files") or []:
                path, proposed = f.get("path", ""), f.get("content", "")
                current = ""
                if root is not None and not _mcp_bad_path(path):
                    target = root / path
                    try:
                        if target.is_file():
                            current = target.read_text(encoding="utf-8")
                    except OSError:
                        pass  # unreadable current file reviews as if new
                diffs.append({
                    "path": path,
                    "diff": "\n".join(difflib.unified_diff(
                        current.splitlines(), str(proposed).splitlines(),
                        fromfile=f"{path} (current)", tofile="proposed", lineterm="",
                    )),
                })
            a["mcp_diffs"] = diffs

    # Same re-derivation for operator evidence: pull the recorded feedback fresh
    # from the event log so the reviewer compares the agent's claim against the
    # actual words — never the agent's copy of them.
    for e in row["evidence"]:
        ref = str(e.get("source_ref", ""))
        if e.get("kind") == "operator" and ref.startswith("event:"):
            # source_ref is AGENT-AUTHORED: an id with prose appended
            # ("event:75 (prop_…) and the operator's instruction…") must not
            # take down the whole review render (it did, 2026-08-26 — the
            # Inbox 500'd on exactly the proposal under review). Take the
            # leading digits if any; no digits → render unenriched, the raw
            # ref stays visible to the reviewer.
            m = re.match(r"\d+", ref[6:].strip())
            ev = await repo.get_event(int(m.group())) if m else None
            if ev:
                # Rejection feedback rides `feedback`; a reopen note and the
                # operator's own coaching note ride `note` — and those are most
                # of what a coach run cites, so reading only `feedback` left the
                # reviewer's side-by-side pane blank exactly where it was needed.
                payload = ev.get("payload") or {}
                e["source_feedback"] = payload.get("feedback") or payload.get("note")

    # The auditor's verdict on this proposal (bulk dismissals, graduated
    # 2026-08-17). It lives on the event log — the reviewer sees the same
    # record the dismissal cards show, and a challenge is exactly why this
    # proposal is still sitting in front of them.
    verdicts = await repo.list_events(
        kind="audit.verdict", ref_id=proposal_id, newest_first=True, limit=1
    )
    if verdicts:
        row["audit"] = verdicts[0]["payload"]
    return row


@router.post("/proposals/{proposal_id}/approve")
async def approve(proposal_id: str) -> dict:
    row = await repo.load_proposal(proposal_id)
    if row is None:
        raise HTTPException(404, "proposal not found")
    try:
        out = await gateway.approve_and_execute(
            row["session_id"], proposal_id,
            model=await resolve_session_model(row["session_id"], row["agent_id"]),
        )
    except gateway.GatewayError as e:
        raise HTTPException(409, str(e))
    # The decision/execution events are emitted by the gateway itself — it is the
    # trust chokepoint, and recording them here would order them after the fact.
    return out


@router.post("/proposals/{proposal_id}/reject")
async def reject(proposal_id: str, body: RejectIn) -> dict:
    row = await repo.load_proposal(proposal_id)
    if row is None:
        raise HTTPException(404, "proposal not found")
    try:
        out = await gateway.reject_with_feedback(
            row["session_id"], proposal_id, body.feedback,
            model=await resolve_session_model(row["session_id"], row["agent_id"]),
        )
    except gateway.GatewayError as e:
        raise HTTPException(409, str(e))
    return out  # gateway emits proposal.decided (see approve)


@router.post("/proposals/{proposal_id}/dismiss")
async def dismiss(proposal_id: str, body: DismissIn) -> dict:
    """No action needed — close it out without asking the agent to redraft.
    See `gateway.dismiss_proposal` for why this is neither approve nor reject."""
    row = await repo.load_proposal(proposal_id)
    if row is None:
        raise HTTPException(404, "proposal not found")
    try:
        return await gateway.dismiss_proposal(row["session_id"], proposal_id, body.reason)
    except gateway.GatewayError as e:
        raise HTTPException(409, str(e))


@router.get("/models")
async def list_model_catalog(session_id: str | None = None) -> dict:
    """The models Central Command exposes, straight from the LiteLLM proxy — the
    cockpit's model dropdown reads this.

    `default` is what a run resolves to with no override (the per-agent
    CC_MODEL_<AGENT_ID> when a session is named, else CC_DEFAULT_MODEL);
    `override` is that session's cockpit-set model, if any. A proxy we cannot
    reach returns `error` with an empty list — never a fake empty success, which
    would render as "this deployment has no models".

    Each model carries the thinking levels it DECLARES (`thinking_levels` in
    model_info). An empty list means no thinking control — undeclared is
    "nobody said", never "any level works", so the dropdown offers nothing
    rather than a lever that silently does nothing.
    """
    from central_command.config import settings
    from central_command.integrations.litellm import (
        LiteLLMError,
        exposed_model_ids,
        thinking_levels_by_model,
    )

    default = settings.default_model.split(":", 1)[-1]
    override = None
    if session_id:
        row = await repo.get_session(session_id)
        if row:
            override = row.get("model_override")
            default = (settings.agent_model(row["agent_id"]) or settings.default_model).split(":", 1)[-1]
    try:
        ids = await exposed_model_ids()
        # ONE proxy read for the whole catalog, not one per model.
        levels = await thinking_levels_by_model()
    except LiteLLMError as exc:
        return {"models": [], "default": default, "override": override, "error": str(exc)}
    return {
        "models": [{"id": m, "thinking_levels": levels.get(m, [])} for m in ids],
        "default": default, "override": override, "error": None,
    }


@router.get("/gateway/models")
async def gateway_model_catalog() -> dict:
    """The cockpit's `/api/gateway/models` — served here for the SINGLE-NODE
    profile, where uvicorn serves the cockpit and no Node server exists to
    provide it (2026-08-28: the model pickers showed "Gateway HTTP 404" on a
    fresh Windows install). On the k3s substrate cc-nerve's own route answers
    first and this one is never reached; both are thin translations of
    /api/models above, so the catalog has one source of truth.

    Wire shape is what web/src/hooks/useGatewayModelCatalog.ts declares —
    guarded by a backend test, since a hand-declared frontend interface cannot
    catch a field the backend never sends.
    """
    data = await list_model_catalog()
    if data.get("error"):
        return {"models": [], "error": data["error"], "source": "litellm"}
    primary = data.get("default") or None
    models = []
    for m in data["models"]:
        mid = m["id"]
        provider, _, rest = mid.partition("/")
        models.append({
            "id": mid,
            "label": rest or mid,
            "provider": provider if rest else "litellm",
            "configured": True,
            "role": "primary" if mid == primary else "allowed",
            "thinkingLevels": m.get("thinking_levels") or [],
        })
    if not models:
        return {"models": [], "error": "LiteLLM served no models.", "source": "litellm"}
    return {"models": models, "error": None, "source": "litellm"}


@router.get("/sessions")
async def list_sessions(
    limit: int = 50,
    status: str | None = None,
    mode: str | None = None,
    agent_id: str | None = None,
    before: datetime | None = None,
    before_id: str | None = None,
) -> dict:
    """The Agent Workspace roster: recent sessions, newest first.

    Unfiltered, this is the cockpit sidebar's read, unchanged. With facets and
    the `(before, before_id)` keyset cursor it is the Activity browser's Runs
    tab — the surface that makes every run visible somewhere, which is the
    whole point of the 2026-08-11 activity-coverage record.

    `agents` rides along so the facet dropdown can never disagree with the rows
    beside it (same reason `_decisions_list` composes one payload).
    """
    return {
        "sessions": await repo.list_sessions(
            max(1, min(limit, 200)), status=status, mode=mode, agent_id=agent_id,
            before=before, before_id=before_id,
        ),
        "agents": await repo.session_agent_ids(),
    }


@router.get("/events")
async def get_events(
    since: int = 0,
    kind: str | None = None,
    ref_id: str | None = None,
    limit: int = 200,
    actor: str | None = None,
    before: int | None = None,
    order: str = "asc",
) -> dict:
    """Query the durable log. `kind=work` matches the whole `work.*` namespace.

    `order=asc` (default) is the replay/cursor read the SSE stream and dashboard
    use; `order=desc` with a `before` cursor is the audit-trail browser paging
    backwards through history.
    """
    rows = await repo.list_events(
        since_id=since,
        kind=kind,
        ref_id=ref_id,
        limit=max(1, min(limit, 2000)),
        actor=actor,
        before_id=before,
        newest_first=order == "desc",
    )
    return {"events": rows, "latest_id": await repo.latest_event_id()}


@router.get("/events/kinds")
async def get_event_kinds() -> dict:
    """The log's own vocabulary: every kind ever recorded, with counts."""
    return {"kinds": await repo.event_kinds()}


@router.get("/usage")
async def usage() -> dict:
    """LiteLLM's spend/usage numbers, relayed for the cockpit. LiteLLM is the
    system of record for usage tracking and limits — this never duplicates it."""
    from central_command.integrations import litellm

    if not litellm.configured():
        raise HTTPException(503, "LiteLLM proxy is not configured (CC_LLM_PROXY_*)")
    try:
        return await litellm.usage_snapshot()
    except litellm.LiteLLMError as e:
        raise HTTPException(502, str(e))


@router.get("/graph/episodes")
async def graph_episodes(limit: int = 50, agent_id: str | None = None) -> dict:
    """Recent memory episodes (read-only; reads are ungated by design).
    Newest first — group merge order from the MCP server is not guaranteed.
    With `agent_id`, the response also spans that agent's private partition
    (D11-r1) and each episode is tagged `scope: "shared"|"private"` so the
    cockpit can label them; with no `agent_id` every episode is shared."""
    from central_command.integrations import graphiti

    episodes = await graphiti.get_episodes(last_n=max(1, min(limit, 500)), agent_id=agent_id)
    private_group = graphiti.private_group(agent_id) if agent_id else None
    for e in episodes:
        e["scope"] = "private" if private_group and e.get("group_id") == private_group else "shared"
    episodes.sort(key=lambda e: e.get("created_at") or "", reverse=True)
    return {"episodes": episodes}


class GraphProblemIn(BaseModel):
    note: str
    # The remediation loop (2026-08-20): true = also task the graph-curator to
    # propose the fix; the proposals land in the Decisions Inbox as usual and
    # the applied fix comes back to the Verify tab as a re-check row.
    remediate: bool = True


@router.get("/graph/verifications")
async def graph_verifications() -> dict:
    """The verification worklist (2026-08-19 spec): rows parked for the
    operator plus recent closed ones, with the config state the cockpit needs
    to label the ladder rung. Remediation of a PROBLEM row goes through the
    curation path — this surface only decides whether the extraction was
    faithful."""
    from central_command.config import settings
    from central_command.gateway import graph_auditor

    awaiting = await repo.list_graph_verifications("AWAITING_OPERATOR")
    # The approved text rides along (source material inspectable inline): the
    # decision is "does the delta match THIS claim", so the claim must be on
    # the same screen, from the proposal row — the control plane's record.
    for row in awaiting:
        row["approved_text"] = await graph_auditor._approved_episode_body(row)
    recent = [
        r for r in await repo.list_graph_verifications(limit=50)
        if r["status"] in ("VERIFIED", "PROBLEM")
    ]
    return {
        "enabled": settings.graph_auditor_enabled,
        "mode": settings.graph_auditor_mode,
        "awaiting": awaiting,
        "recent_closed": recent,
    }


@router.post("/graph/verifications/{verification_id}/confirm")
async def confirm_graph_verification(verification_id: str) -> dict:
    """Operator: the graph change is faithful — close the loop. This is the
    outcome the agreement record pairs against the auditor's verdict."""
    if not await repo.close_graph_verification(
        verification_id, status="VERIFIED", closed_by="operator"
    ):
        raise HTTPException(409, "verification is not awaiting the operator")
    await events.emit(
        "graph.verification.confirmed", ref_id=verification_id,
        payload={"by": "operator"}, actor="operator",
    )
    return {"ok": True}


@router.post("/graph/verifications/{verification_id}/problem")
async def problem_graph_verification(verification_id: str, body: GraphProblemIn) -> dict:
    """Operator: the extraction diverged from the approved claim. The note is
    required — it is the coaching signal's content and the curation worklist's
    starting point."""
    note = body.note.strip()
    if not note:
        raise HTTPException(422, "a note describing the problem is required")
    row = await repo.get_graph_verification(verification_id)
    if not await repo.close_graph_verification(
        verification_id, status="PROBLEM", closed_by="operator", problem_note=note
    ):
        raise HTTPException(409, "verification is not awaiting the operator")
    await events.emit(
        "graph.verification.problem", ref_id=verification_id,
        payload={"note": note, "remediate": body.remediate}, actor="operator",
    )
    if not body.remediate:
        return {"ok": True}

    # The remediation loop: the note becomes the graph-curator's task brief;
    # its fixes are ordinary gated proposals (Decisions Inbox), and each
    # applied fix queues a re-check row back into the Verify tab.
    from central_command.gateway import graph_auditor
    from central_command.runtime import graph_curator_profile as gc
    from central_command.runtime.hiring import ensure_hired
    from central_command.runtime.templates import GRAPH_CURATOR_TEMPLATE

    await ensure_hired(
        agent_id=gc.AGENT_ID, name=gc.NAME, role=gc.ROLE, charter=gc.CHARTER,
        template=GRAPH_CURATOR_TEMPLATE, consultable=True,
        deviations=gc.DEVIATIONS, hired_by="system:graph-remediation",
    )
    result = await create_and_run_task(
        await graph_auditor.remediation_instructions(row, note),
        f"Fix graph extraction: {row['episode_name']}"[:120],
        gc.AGENT_ID,
        actor="operator",
        background=True,
    )
    return {"ok": True, "task_id": result.get("task_id")}


@router.get("/graph/audit/agreement")
async def graph_audit_agreement() -> dict:
    """The graph auditor's shadow-vs-operator record, per action class,
    computed fresh from the event log — the graduation evidence.
    Operator-diagnostic endpoint — no code consumer; curl it (see STATUS.md)."""
    from central_command.gateway import graph_auditor

    return await graph_auditor.agreement_report()


@router.get("/capabilities")
async def get_capabilities() -> dict:
    """The governed catalog: every capability and its gate, plus the policy
    register (enforced vs planned). Static by design — this is the registry the
    Executor dispatches from, so it cannot drift from behaviour."""
    from central_command.gateway.capabilities import registry_as_dict

    return registry_as_dict()


@router.get("/stream")
async def stream(last_event_id: str | None = Header(default=None)) -> StreamingResponse:
    """Live event stream, resumable. The browser's EventSource sends
    `Last-Event-ID` automatically on reconnect, so a dropped connection replays
    what it missed instead of silently losing updates.
    Raw SSE feed used for shutdown-drain verification (see the SIGTERM
    bite-mark in CLAUDE.md)."""
    try:
        since = int(last_event_id) if last_event_id else 0
    except ValueError:
        since = 0

    async def gen():
        yield "event: ping\ndata: {}\n\n"
        async for event in events.stream(last_id=since):
            yield f"id: {event['id']}\ndata: {json.dumps(event, default=str)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


# --- Skills Library ----------------------------------------------------------
# Authoring is a DIRECT operator action, like hire/retire and charter-save: this
# is internal state, not a world write, so it carries no approval gate. Agents
# do not write here at all in this slice — they declare gaps (see declare_gap).


class SkillIn(BaseModel):
    # Constrained because the id becomes part of a MODEL-VISIBLE tool name
    # (`search_<slug>_reference`). An unconstrained id can slug into another
    # skill's tool name and, when one agent holds both, pydantic-ai raises at
    # `agent.run()` — see central_command/skills/ids.py. FastAPI turns a pattern
    # violation into a 422, which is what the operator should see, not a 500.
    id: str = Field(pattern=SKILL_ID_RE.pattern, description=SKILL_ID_HELP)
    title: str
    summary: str


class SkillDocIn(BaseModel):
    doc_key: str
    kind: str                      # 'guidance' | 'reference'
    title: str
    content: str
    source_url: str | None = None
    describes: str | None = None   # what this documents: 'Jira Cloud REST v3'


class SkillGrantIn(BaseModel):
    skill_id: str


@router.get("/skills")
async def list_skills_route(include_retired: bool = True) -> dict:
    """The library catalog — each skill with its reference-doc and holder counts.

    Retired skills are INCLUDED by default, exactly as `list_agents` includes
    retired agents on the roster: this is the management surface, and a status
    you cannot see is a status you cannot undo. Until this parameter existed the
    route always called `repo.list_skills()` at its ACTIVE-only default, so a
    retired skill had no row to click and reactivation was unreachable.

    Note the runtime does NOT come through here — `runtime/skills.py`'s
    `catalog_line` calls `repo.list_skills()` directly and keeps the ACTIVE-only
    default, so a retired skill is never named to a model.
    """
    return {"skills": await repo.list_skills(include_retired=include_retired)}


@router.get("/skills/{skill_id}")
async def get_skill_route(skill_id: str) -> dict:
    """One skill: its current documents and which agents hold it."""
    skill = await repo.get_skill(skill_id)
    if skill is None:
        raise HTTPException(404, f"unknown skill {skill_id!r}")
    # Retired doc_keys ride along (flagged by `retired_at`) so the cockpit can
    # keep a click-target for their history — same reasoning as the roster and
    # the skills list including RETIRED rows. The RUNTIME never comes through
    # here; its delivery/search reads stay current-only.
    docs = await repo.list_skill_docs(skill_id, include_retired=True)
    holders = []
    for a in await repo.list_agents():
        if any(g["skill_id"] == skill_id for g in await repo.list_agent_skills(a["id"])):
            holders.append(a["id"])
    return {"skill": skill, "docs": docs, "agents": holders}


@router.post("/skills")
async def create_skill_route(body: SkillIn) -> dict:
    # `repo.create_skill` is an UPSERT and stays one — the importer legitimately
    # needs re-import to be the same action as first import. But an operator
    # POSTing an id that already exists means something different: they think
    # they are creating a new skill, and silently retitling someone else's is
    # how two vendors' documentation ends up interleaved under one id. Refuse
    # at the route, where the intent is "create".
    if await repo.get_skill(body.id) is not None:
        raise HTTPException(409, f"skill {body.id!r} already exists")
    await repo.create_skill(body.id, body.title, body.summary)
    await events.emit(
        "skill.created", ref_id=body.id,
        payload={"title": body.title, "summary": body.summary}, actor="operator",
    )
    return {"ok": True}


@router.post("/skills/{skill_id}/docs")
async def add_skill_doc_route(skill_id: str, body: SkillDocIn) -> dict:
    """Add a NEW version of one document. Never an in-place edit — the previous
    version stays readable forever, exactly like a charter version."""
    if await repo.get_skill(skill_id) is None:
        raise HTTPException(404, f"unknown skill {skill_id!r}")
    if body.kind not in ("guidance", "reference"):
        raise HTTPException(422, "kind must be 'guidance' or 'reference'")
    # `doc_key == 'guidance'` ⟺ `kind == 'guidance'` is an invariant the whole
    # delivery path relies on, and until now nothing enforced it:
    #   * doc_key='guidance' + kind='reference' supersedes the real guidance
    #     doc, so `capabilities_for` finds none and drops the skill from every
    #     holder while `catalog_line` still advertises it;
    #   * kind='guidance' + doc_key='about' leaves TWO current guidance docs,
    #     and `list_skill_docs` orders by (kind, doc_key) so 'about' wins —
    #     the agent silently loads the wrong guidance, and nothing surfaces it.
    # Reject both directions.
    if (body.doc_key == "guidance") != (body.kind == "guidance"):
        raise HTTPException(
            422,
            "the doc_key 'guidance' and the kind 'guidance' go together: use "
            "doc_key='guidance' with kind='guidance' for the skill's guidance "
            "document, and any other doc_key with kind='reference'",
        )
    doc = await repo.add_skill_doc(
        skill_id, body.doc_key, body.kind, body.title, body.content,
        source_url=body.source_url, describes=body.describes,
        captured_at=datetime.now(timezone.utc),
    )
    await events.emit(
        "skill.doc_added", ref_id=skill_id,
        payload={"doc_key": body.doc_key, "kind": body.kind,
                 "version": doc["version"], "describes": body.describes},
        actor="operator",
    )
    return doc


@router.get("/skills/{skill_id}/docs/{doc_key}/history")
async def skill_doc_history_route(skill_id: str, doc_key: str) -> dict:
    return {"versions": await repo.skill_doc_history(skill_id, doc_key)}


@router.post("/skills/{skill_id}/docs/{doc_key}/retire")
async def retire_skill_doc_route(skill_id: str, doc_key: str, body: RetireIn) -> dict:
    """End one reference doc: it leaves delivery and search for NEW sessions
    (in-flight ones already hold the text in their message history), while
    every version stays readable in the operator's history view.

    Guidance is refused: a skill whose guidance retires becomes a catalog
    entry that teaches nothing when loaded — `loadout` drops it with a
    warning. Supersede the guidance with a new version, or retire the SKILL.
    """
    if doc_key == "guidance":
        raise HTTPException(
            422,
            "the guidance document cannot be retired on its own — supersede it "
            "with a new version, or retire the whole skill",
        )
    row = await repo.retire_skill_doc(skill_id, doc_key, body.reason)
    if row is None:
        raise HTTPException(
            409, f"doc {doc_key!r} on skill {skill_id!r} is unknown or already retired",
        )
    await events.emit(
        "skill.doc_retired", ref_id=skill_id,
        payload={"doc_key": doc_key, "version": row["version"], "reason": body.reason},
        actor="operator",
    )
    return {"ok": True, "doc": row}


@router.post("/skills/{skill_id}/retire")
async def retire_skill_route(skill_id: str, body: RetireIn) -> dict:
    if not await repo.retire_skill(skill_id, body.reason):
        raise HTTPException(409, f"skill {skill_id!r} is unknown or already retired")
    await events.emit(
        "skill.retired", ref_id=skill_id,
        payload={"reason": body.reason}, actor="operator",
    )
    return {"ok": True}


@router.post("/skills/{skill_id}/reactivate")
async def reactivate_skill_route(skill_id: str) -> dict:
    """Bring a retired skill back into the library — the agent lifecycle's
    reactivation, applied to a skill. No reason is required: the retirement
    reason it clears is already on the record, and so is this event."""
    if not await repo.reactivate_skill(skill_id):
        raise HTTPException(409, f"skill {skill_id!r} is unknown or is not retired")
    await events.emit(
        "skill.reactivated", ref_id=skill_id, payload={}, actor="operator",
    )
    return {"ok": True}


@router.post("/agents/{agent_id}/skills")
async def grant_skill_route(agent_id: str, body: SkillGrantIn) -> dict:
    if await repo.get_agent(agent_id) is None:
        raise HTTPException(404, f"unknown agent {agent_id!r}")
    skill = await repo.get_skill(body.skill_id)
    if skill is None:
        raise HTTPException(404, f"unknown skill {body.skill_id!r}")
    # A RETIRED skill is filtered out of `list_agent_skills`
    # (`g.revoked_at is null and s.status = 'ACTIVE'`), so granting it here
    # would write a real row that delivers nothing — the agent would never
    # see it in its capability list, only the record would claim it did.
    if skill["status"] == "RETIRED":
        raise HTTPException(409, f"skill {body.skill_id!r} is retired")
    await repo.grant_skill(agent_id, body.skill_id)
    await events.emit(
        "skill.granted", ref_id=agent_id,
        payload={"skill_id": body.skill_id}, actor="operator",
    )
    return {"ok": True}


@router.delete("/agents/{agent_id}/skills/{skill_id}")
async def revoke_skill_route(agent_id: str, skill_id: str, body: RetireIn) -> dict:
    if not await repo.revoke_skill(agent_id, skill_id, body.reason):
        raise HTTPException(409, "no active grant to revoke")
    await events.emit(
        "skill.revoked", ref_id=agent_id,
        payload={"skill_id": skill_id, "reason": body.reason}, actor="operator",
    )
    return {"ok": True}


class SkillDocImportIn(BaseModel):
    skill_id: str
    doc_key: str
    title: str | None = None
    url: str | None = None
    content: str | None = None
    describes: str | None = None


@router.post("/skills/import-doc")
async def import_skill_doc_route(body: SkillDocImportIn) -> dict:
    """Add one document to an EXISTING skill from a URL or pasted text — the
    operator's fast path for "here's one more reference", short of a whole
    folder import. Creating the skill stays `POST /skills`'s job on purpose,
    so this route refuses an unknown skill_id rather than guessing whether the
    operator meant to create one.

    Exactly one of `url`/`content` is required: a URL is fetched server-side
    (same HTML→markdown conversion `fetch_url` uses, `central_command.integrations
    .webfetch`) and stamped with `source_url`/`captured_at` provenance; pasted
    content is written as-is with `added_by='operator'`.
    """
    if bool(body.url) == bool(body.content):
        raise HTTPException(422, "exactly one of url or content is required")
    if await repo.get_skill(body.skill_id) is None:
        raise HTTPException(404, f"unknown skill {body.skill_id!r}")

    if body.url:
        from central_command.integrations import webfetch

        result = await webfetch.fetch(body.url)
        if not result["ok"]:
            raise HTTPException(422, f"could not fetch {body.url!r}: {result['text']}")
        content = result["text"]
        source_url = body.url
    else:
        content = body.content or ""
        source_url = None

    kind = "guidance" if body.doc_key == "guidance" else "reference"
    title = body.title or body.doc_key
    doc = await repo.add_skill_doc(
        body.skill_id, body.doc_key, kind, title, content,
        source_url=source_url, describes=body.describes,
        captured_at=datetime.now(timezone.utc), added_by="operator",
    )
    await events.emit(
        "skill.doc_added", ref_id=body.skill_id,
        payload={"doc_key": body.doc_key, "kind": kind,
                 "version": doc["version"], "describes": body.describes,
                 "source_url": source_url},
        actor="operator",
    )
    return doc


# ponytail: 25 MB is a guess-free round number for an operator's one-off
# reference doc, not a measured limit — raise it if a real upload needs more.
MAX_IMPORT_FILE_BYTES = 50 * 1024 * 1024


@router.post("/skills/import-file")
async def import_skill_file_route(
    skill_id: str = Form(...),
    doc_key: str = Form(...),
    title: str | None = Form(None),
    describes: str | None = Form(None),
    file: UploadFile = File(...),
) -> dict:
    """Add one document to an EXISTING skill from an uploaded file (PDF,
    docx, pptx, xlsx, …) — `import-doc`'s URL/paste pair, plus a third
    source: something the operator has on disk. Conversion to markdown is
    server-side via `markitdown`; there is no live-write side effect to gate,
    same reasoning as `import-doc`.
    """
    if await repo.get_skill(skill_id) is None:
        raise HTTPException(404, f"unknown skill {skill_id!r}")

    data = await file.read()
    if len(data) > MAX_IMPORT_FILE_BYTES:
        raise HTTPException(
            422,
            f"file is {len(data)} bytes, over the {MAX_IMPORT_FILE_BYTES}-byte import limit",
        )

    from markitdown import MarkItDown
    from markitdown._exceptions import MarkItDownException

    filename = file.filename or "upload"
    suffix = Path(filename).suffix or None
    try:
        result = MarkItDown().convert_stream(io.BytesIO(data), file_extension=suffix)
    except MarkItDownException as e:
        raise HTTPException(422, f"could not convert {filename!r}: {e}") from e
    content = result.text_content

    kind = "guidance" if doc_key == "guidance" else "reference"
    title = title or Path(filename).stem
    doc = await repo.add_skill_doc(
        skill_id, doc_key, kind, title, content,
        source_url=None, describes=describes,
        captured_at=datetime.now(timezone.utc), added_by="operator",
    )
    await events.emit(
        "skill.doc_added", ref_id=skill_id,
        payload={"doc_key": doc_key, "kind": kind,
                 "version": doc["version"], "describes": describes,
                 "source_url": None, "filename": filename},
        actor="operator",
    )
    return doc


class SkillSiteImportIn(BaseModel):
    skill_id: str
    url: str
    doc_key_prefix: str | None = None
    max_pages: int = 100
    path_prefix: str | None = None
    describes: str | None = None
    render: bool = False
    # 'llm' asks cc-crawler for LLM-cleaned markdown through the cluster's
    # LiteLLM (render path only); 'pruning' is the free heuristic default.
    filter_mode: str = "pruning"


@router.post("/skills/import-site")
async def import_skill_site_route(body: SkillSiteImportIn) -> dict:
    """Import a whole documentation site into an EXISTING skill in one call —
    the ladder (`central_command.skills.site_import.import_site`) tries an
    `llms-full.txt` the site publishes for LLM consumers first, falls back to
    a sitemap walked with trafilatura, and only reaches for the separate
    `cc-crawler` browser-rendering service when the caller asks for it
    (`render=True`, for JS-only sites).

    Unlike `import-doc`, which emits one `skill.doc_added` event per document,
    this route emits ONE `skill.site_imported` summary event — a 60-doc
    llms-full.txt import should not produce 60 rows on the governance screens.
    """
    if await repo.get_skill(body.skill_id) is None:
        raise HTTPException(404, f"unknown skill {body.skill_id!r}")
    parsed = httpx.URL(body.url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(422, f"unsupported URL scheme {parsed.scheme!r} — only http/https")

    from central_command.skills.site_import import import_site

    result = await import_site(
        body.skill_id, body.url,
        doc_key_prefix=body.doc_key_prefix, max_pages=body.max_pages,
        path_prefix=body.path_prefix, describes=body.describes, render=body.render,
        filter_mode=body.filter_mode,
    )
    await events.emit(
        "skill.site_imported", ref_id=body.skill_id,
        payload={"url": body.url, "rung": result["rung"], "count": len(result["imported"])},
        actor="operator",
    )
    return result


class SkillImportIn(BaseModel):
    path: str                      # a SKILL.md + references/ folder on this host
    skill_id: str | None = None
    source_url: str | None = None
    describes: str | None = None


@router.post("/skills/import")
async def import_skill_route(body: SkillImportIn) -> dict:
    """Import a skill folder in one action. The operator's cheapest path in."""
    from central_command.skills.importer import import_skill_folder

    try:
        result = await import_skill_folder(
            body.path, skill_id=body.skill_id,
            source_url=body.source_url, describes=body.describes,
        )
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(422, str(e)) from e
    await events.emit(
        "skill.imported", ref_id=result["skill_id"],
        payload={"path": body.path, "references": len(result["references"])},
        actor="operator",
    )
    return result


@router.get("/gaps")
async def list_gaps_route(limit: int = 100) -> dict:
    """What the team says it is missing — the operator's queue. Newest first."""
    return {"gaps": await repo.recent_gaps(max(1, limit))}


# --- Graph panel (rung 3, 2026-08-09) ---------------------------------------
# Read-only bolt reads via integrations/neo4j_reader.py — no raw-Cypher
# endpoint, the cockpit never talks to Neo4j itself. See
# docs/superpowers/specs/2026-08-09-graph-inspection-design.md for the wire
# contract these routes pin.

_GRAPH_SETTINGS_KEY = "graph_settings"
_GRAPH_SETTINGS_DEFAULT = {"graphistry_enabled": False, "graphistry_url": ""}


class GraphSettingsIn(BaseModel):
    graphistry_enabled: bool | None = None
    graphistry_url: str | None = None


async def _graphistry_state(settings_row: dict) -> str:
    """Three states, not two (design spec): the toggle records INTENT only,
    the cockpit believes this probe, never the flag. enabled-but-unreachable
    is an expected state (workstation off / GPU busy), not an error."""
    if not settings_row["graphistry_enabled"]:
        return "disabled"
    url = settings_row["graphistry_url"]
    if not url:
        return "unreachable"
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(url)
        return "live" if resp.status_code < 400 else "unreachable"
    except Exception:
        # Includes httpx.InvalidURL — a garbage URL typed into settings is
        # just another way of being unreachable, never a 500 on status.
        return "unreachable"


@router.get("/graph/search")
async def graph_search(q: str, group_id: str | None = None, limit: int = 25) -> dict:
    try:
        nodes = await neo4j_reader.search_entities(q, group_id, max(1, limit))
    except Exception as e:
        raise HTTPException(503, f"graph unavailable: {e}") from e
    return {"nodes": nodes}


@router.get("/graph/all")
async def graph_all(group_id: str | None = None, limit: int = 2000, at: str | None = None) -> dict:
    """Everything at once, for curation rather than exploration. `limit` caps
    each half separately and the reply says whether it bit."""
    try:
        return await neo4j_reader.everything(group_id, max(1, min(limit, 5000)), at)
    except Exception as e:
        raise HTTPException(503, f"graph unavailable: {e}") from e


@router.get("/graph/neighborhood")
async def graph_neighborhood(uuid: str, at: str | None = None, limit: int = 50) -> dict:
    try:
        return await neo4j_reader.neighborhood(uuid, at, max(1, min(limit, 500)))
    except Exception as e:
        raise HTTPException(503, f"graph unavailable: {e}") from e


@router.get("/graph/provenance")
async def graph_provenance(uuid: str) -> dict:
    try:
        episodes = await neo4j_reader.provenance(uuid)
    except Exception as e:
        raise HTTPException(503, f"graph unavailable: {e}") from e
    return {"episodes": episodes}


@router.get("/graph/status")
async def graph_status() -> dict:
    settings_row = await repo.get_app_setting(_GRAPH_SETTINGS_KEY, _GRAPH_SETTINGS_DEFAULT)
    graphistry = await _graphistry_state(settings_row)
    available = await neo4j_reader.ping()
    if not available:
        return {
            "available": False, "node_count": 0, "edge_count": 0, "group_ids": [],
            "graphistry": graphistry,
            "graphistry_enabled": settings_row["graphistry_enabled"],
            "graphistry_url": settings_row["graphistry_url"],
        }
    counts = await neo4j_reader.counts()
    return {
        "available": True, **counts,
        "graphistry": graphistry,
        "graphistry_enabled": settings_row["graphistry_enabled"],
        "graphistry_url": settings_row["graphistry_url"],
    }


@router.put("/graph/settings")
async def graph_settings_put(body: GraphSettingsIn) -> dict:
    current = await repo.get_app_setting(_GRAPH_SETTINGS_KEY, _GRAPH_SETTINGS_DEFAULT)
    updated = {**current}
    if body.graphistry_enabled is not None:
        updated["graphistry_enabled"] = body.graphistry_enabled
    if body.graphistry_url is not None:
        updated["graphistry_url"] = body.graphistry_url
    await repo.set_app_setting(_GRAPH_SETTINGS_KEY, updated)
    return await graph_status()


# --- Graph curation (OPERATOR writes, 2026-08-15) ---------------------------
# The operator's own hand on the graph: delete a hallucinated node, repoint a
# wrong edge, add a fact the extractor missed, fold a duplicate identity.
#
# **Ungated on purpose, and that is not an exception to the approval rule.**
# Gates exist for AGENT-authored writes — an agent proposes, the operator approves. Here
# The operator IS the author, and asking him to approve his own click would be ceremony,
# not governance. What keeps it safe is the same thing that keeps the reader
# safe: a closed set of parameterized operations, no raw Cypher over the wire,
# and no path from `runtime/` to any of it (agents cannot reach these routes —
# they hold `propose_*` tools and no HTTP client).
#
# Every one of these emits nothing to the event log deliberately: the log records
# what the TEAM did and what was authorised. An operator correcting his own
# knowledge base is not an authorisation, and Graphiti already keeps the
# episodes that produced the original facts.


class GraphNodeIn(BaseModel):
    name: str
    labels: list[str] | None = None
    summary: str = ""
    group_id: str = "central_command"


class GraphNodePatch(BaseModel):
    uuid: str
    name: str | None = None
    summary: str | None = None
    labels: list[str] | None = None


class GraphEdgeIn(BaseModel):
    source_uuid: str
    target_uuid: str
    name: str
    fact: str
    valid_at: str | None = None


class GraphEdgePatch(BaseModel):
    uuid: str
    name: str | None = None
    fact: str | None = None
    source_uuid: str | None = None
    target_uuid: str | None = None
    valid_at: str | None = None
    invalid_at: str | None = None
    clear_invalid: bool = False
    clear_valid: bool = False


class GraphMergeIn(BaseModel):
    keep_uuid: str
    drop_uuid: str


async def _curate(coro, op: str, detail: dict):
    """One error contract for every curation write: a rejected edit is a 422
    the operator can act on ("unknown entity type Personn"), a broken bolt is
    the same 503 the read path returns.

    Every write that lands goes on the governance record. Emitted AFTER the
    write on purpose — this is bookkeeping of a completed operator action, not
    an authorisation (the operator IS the gate here), same reasoning as
    `proposal.created` landing after its save."""
    try:
        result = await coro
    except neo4j_writer.WriteError as e:
        raise HTTPException(422, str(e)) from e
    except Exception as e:
        raise HTTPException(503, f"graph unavailable: {e}") from e
    await events.emit(
        "graph.curated",
        ref_id=result.get("uuid"),
        payload={"op": op, "detail": detail, "result": result},
        actor="operator",
    )
    return result


@router.get("/graph/entity-types")
async def graph_entity_types() -> dict:
    """The ontology the operator may pick from — served rather than hard-coded
    in the cockpit, so the panel cannot drift from `graphiti/config.yaml`."""
    return {"entity_types": list(neo4j_writer.ENTITY_TYPES)}


@router.post("/graph/node")
async def graph_node_create(body: GraphNodeIn) -> dict:
    return await _curate(neo4j_writer.create_node(
        body.name, body.labels, body.summary, body.group_id),
        "node.create", body.model_dump(exclude_none=True))


@router.patch("/graph/node")
async def graph_node_update(body: GraphNodePatch) -> dict:
    return await _curate(neo4j_writer.update_node(
        body.uuid, body.name, body.summary, body.labels),
        "node.update", body.model_dump(exclude_none=True))


@router.delete("/graph/node")
async def graph_node_delete(uuid: str) -> dict:
    return await _curate(neo4j_writer.delete_node(uuid), "node.delete", {"uuid": uuid})


@router.post("/graph/edge")
async def graph_edge_create(body: GraphEdgeIn) -> dict:
    return await _curate(neo4j_writer.create_edge(
        body.source_uuid, body.target_uuid, body.name, body.fact, body.valid_at),
        "edge.create", body.model_dump(exclude_none=True))


@router.patch("/graph/edge")
async def graph_edge_update(body: GraphEdgePatch) -> dict:
    return await _curate(neo4j_writer.update_edge(
        body.uuid, body.name, body.fact, body.source_uuid, body.target_uuid,
        body.valid_at, body.invalid_at, body.clear_invalid, body.clear_valid),
        "edge.update", body.model_dump(exclude_none=True))


@router.delete("/graph/edge")
async def graph_edge_delete(uuid: str) -> dict:
    return await _curate(neo4j_writer.delete_edge(uuid), "edge.delete", {"uuid": uuid})


@router.post("/graph/merge")
async def graph_merge(body: GraphMergeIn) -> dict:
    return await _curate(neo4j_writer.merge_nodes(body.keep_uuid, body.drop_uuid),
        "merge", body.model_dump())
