"""D7 phase 2 — the orchestration runtime: plan review → assign → receive →
decide, parked on the SAME durable-pause machinery as approval waits.

What these tests lock in:
- the full loop offline (plan park → operator approval → child assignment →
  ungated text answer flows back WITHOUT approval → final report);
- the gated path (a child's proposal parks in the Decisions Inbox; the loop
  resumes only after the operator's decision);
- the mechanical guards: star shape / depth-1, plan-before-assign, stall
  escalation (the operator: pause when stuck, never budgets), batch rejection bounded;
- restart-proofness: resume works purely from persisted state (M2's
  two-process property, orchestration edition);
- the capability-gap principle: a child's declared gap becomes event-log data.
"""

from __future__ import annotations

import asyncio

import pytest

from central_command.api import orchestration, routes
from central_command.config import settings
from central_command.db import repo
from central_command.gateway import gateway
from central_command.runtime.spike_model import (
    make_bad_target_project_model,
    make_jira_task_model,
    make_stalling_project_model,
)
from tests.conftest import aresolve, needs_pg

pytestmark = needs_pg


def _text_model(text: str):
    from pydantic_ai.messages import ModelResponse, TextPart
    from pydantic_ai.models.function import FunctionModel

    return FunctionModel(
        lambda messages, info: ModelResponse(parts=[TextPart(content=text)])
    )


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "executor_mode", "dry_run")


async def _settle(cond, timeout: float = 5.0):
    """Wait for background child runs / resume hooks (instant demo models,
    but detached tasks need event-loop turns)."""
    for _ in range(int(timeout / 0.02)):
        if await cond():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not reached before timeout")


async def _start_project(instructions: str = "Demo project: review TASKS-12.") -> dict:
    return await routes.create_and_run_task(
        instructions, "Demo project", "orchestrator", actor="operator",
        background=False,
    )


async def _open_item(session_id: str, kind: str) -> dict:
    items = [i for i in await repo.list_operator_items("OPEN")
             if i["session_id"] == session_id and i["kind"] == kind]
    assert len(items) == 1, f"expected one OPEN {kind} item, got {len(items)}"
    return items[0]


async def _events_for(ref_id: str, kinds: list[str] | None = None) -> list[dict]:
    rows = await repo.list_events_of_kinds(kinds) if kinds else []
    return [e for e in rows if e["ref_id"] == ref_id]


# --- the full loop -------------------------------------------------------------


async def test_full_loop_ungated_answer_flows_without_approval(monkeypatch):
    # The child answers in TEXT — nothing external changes, so its result
    # returns to the orchestrator with NO approval anywhere (the operator's rule).
    monkeypatch.setattr(
        routes, "resolve_model",
        aresolve(lambda *a, **k: _text_model("Review done: TASKS-12 hygiene is fine.")),
    )
    r = await _start_project()
    assert r["parked"] is True
    sid = r["session_id"]

    # Round 1 parked on the plan review; the project task holds IN_PROGRESS.
    assert await repo.session_status(sid) == "AWAITING_AGENTS"
    task = await repo.get_task(r["task_id"])
    assert task["status"] == "IN_PROGRESS"
    item = await _open_item(sid, "plan_review")
    assert "jira-expert" in item["body"]

    # The operator approves the plan → the loop resumes, assigns the child,
    # the child's text answer resolves it DONE, and the loop finishes.
    await orchestration.answer_item(item["id"], "", "approved")

    async def done():
        t = await repo.get_task(r["task_id"])
        return t["status"] == "DONE"
    await _settle(done)

    project = await repo.get_task(r["task_id"])
    assert "PROJECT COMPLETE" in project["outcome"]

    # Lineage: exactly one child task, linked to the orchestrator session, and
    # the child's SESSION carries the parent link (the spawnedBy tree's data).
    children = [t for t in await repo.list_tasks()
                if t.get("parent_session_id") == sid]
    assert len(children) == 1
    child = children[0]
    assert child["agent_id"] == "jira-expert" and child["status"] == "DONE"
    child_session = await repo.get_session(child["session_id"])
    assert child_session["parent_session_id"] == sid

    # UNGATED: the child produced no proposal — nothing awaited a human.
    props = [p for p in await repo.list_proposals()
             if p["session_id"] == child["session_id"]]
    assert props == []

    # Event order: the round announcement precedes the child it authorises
    # (M8), and the loop's lifecycle is on the log.
    kinds = await repo.list_events_of_kinds(
        ["orchestration.round", "task.created", "orchestration.parked",
         "orchestration.resumed", "orchestration.completed"])
    mine = [e for e in kinds
            if e["ref_id"] == sid or e["ref_id"] == child["id"]]
    round2 = [e for e in mine if e["kind"] == "orchestration.round"
              and (e["payload"] or {}).get("assignments")]
    child_created = [e for e in mine if e["kind"] == "task.created"]
    assert round2 and child_created and round2[0]["id"] < child_created[0]["id"]
    assert any(e["kind"] == "orchestration.completed" for e in mine)


async def test_gated_child_parks_proposal_and_resumes_after_approval(monkeypatch):
    # The child PROPOSES a world change → it parks in the Decisions Inbox and
    # the orchestrator stays parked until the operator decides. The gate is
    # exactly where it always was — on the action class, not the delegation.
    monkeypatch.setattr(
        routes, "resolve_model", aresolve(lambda *a, **k: make_jira_task_model()),
    )
    r = await _start_project("Demo project: set the TASKS-12 due date properly.")
    sid = r["session_id"]
    item = await _open_item(sid, "plan_review")
    await orchestration.answer_item(item["id"], "", "approved")

    # The child runs in the background and parks REVIEW with a proposal.
    async def child_in_review():
        kids = [t for t in await repo.list_tasks()
                if t.get("parent_session_id") == sid]
        return bool(kids) and kids[0]["status"] == "REVIEW"
    await _settle(child_in_review)

    child = [t for t in await repo.list_tasks()
             if t.get("parent_session_id") == sid][0]
    props = [p for p in await repo.list_proposals()
             if p["session_id"] == child["session_id"]
             and p["status"] == "AWAITING_HUMAN"]
    assert len(props) == 1
    # The orchestrator is still parked — a pending gate is never skipped.
    assert await repo.session_status(sid) == "AWAITING_AGENTS"

    # Operator approves the child's proposal → child resolves → loop resumes
    # (the gateway's _resolve_task_after hook) → final report.
    await gateway.approve_and_execute(
        child["session_id"], props[0]["id"], approver="human:lee")

    async def project_done():
        t = await repo.get_task(r["task_id"])
        return t["status"] == "DONE"
    await _settle(project_done)
    assert "PROJECT COMPLETE" in (await repo.get_task(r["task_id"]))["outcome"]


# --- mechanical guards ---------------------------------------------------------


async def test_star_shape_self_assignment_is_rejected():
    r = await _start_project("Demo project: try to self-delegate.")
    # bad-target model: plan → (approve) → assigns to the orchestrator itself
    sid = r["session_id"]
    # swap the orchestrator model for this project's remaining rounds
    item = await _open_item(sid, "plan_review")

    async def run_with_bad_model():
        await repo.answer_operator_item(item["id"], "", "approved")
        return await orchestration.maybe_resume(
            sid, model=make_bad_target_project_model())
    out = await run_with_bad_model()

    # The batch was rejected (nothing created), and the model's honest text
    # surrender completed the task rather than looping forever.
    assert out is not None
    rejected = await repo.list_events_of_kinds(["orchestration.batch_rejected"])
    mine = [e for e in rejected if e["ref_id"] == sid]
    assert mine and any("not an ACTIVE taskable" in p
                        for p in mine[0]["payload"]["problems"])
    children = [t for t in await repo.list_tasks()
                if t.get("parent_session_id") == sid]
    assert children == []
    assert (await repo.get_task(r["task_id"]))["status"] == "DONE"
    assert "Giving up honestly" in (await repo.get_task(r["task_id"]))["outcome"]


async def test_assign_before_approved_plan_is_rejected(monkeypatch):
    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
    from pydantic_ai.models.function import FunctionModel

    def impatient(messages, info):
        for m in messages:
            for part in getattr(m, "parts", []):
                if isinstance(part, ToolReturnPart) and \
                        "BATCH REJECTED" in str(part.content):
                    return ModelResponse(parts=[TextPart(
                        content="Understood — plan first.")])
        return ModelResponse(parts=[ToolCallPart(
            tool_name="assign_work",
            args={"agent_id": "jira-expert", "title": "x",
                  "instructions": "skip the plan"})])

    task_id = "task_" + __import__("uuid").uuid4().hex[:12]
    await repo.create_task(task_id, "t", "Demo: no plan.", "orchestrator")
    task = await repo.get_task(task_id)
    out = await orchestration.run_project(task, model=FunctionModel(impatient))
    assert out["done"] is True
    rejected = await repo.list_events_of_kinds(["orchestration.batch_rejected"])
    mine = [e for e in rejected if e["ref_id"] == out["session_id"]]
    assert mine and any("APPROVED plan" in p for p in mine[0]["payload"]["problems"])


async def test_stall_escalates_to_operator_instead_of_looping(monkeypatch):
    monkeypatch.setattr(settings, "orch_stall_limit", 1)
    task_id = "task_" + __import__("uuid").uuid4().hex[:12]
    await repo.create_task(task_id, "t", "Demo: stall.", "orchestrator")
    task = await repo.get_task(task_id)
    out = await orchestration.run_project(
        task, model=make_stalling_project_model())

    # The requested calls were NOT executed; ONE stall item parks for the
    # operator with the ledger, and the loop waits.
    assert out["parked"] is True and out.get("stalled") is True
    sid = out["session_id"]
    item = await _open_item(sid, "stall")
    assert "PAUSED by the control plane" in item["body"]
    assert await repo.session_status(sid) == "AWAITING_AGENTS"
    assert [i for i in await repo.list_operator_items("OPEN")
            if i["session_id"] == sid and i["kind"] == "plan_review"] == []
    stalled = [e for e in await repo.list_events_of_kinds(["orchestration.stalled"])
               if e["ref_id"] == sid]
    assert stalled and "without progress" in stalled[0]["payload"]["reason"]


async def test_depth1_agent_created_task_refused():
    task = {"id": "task_x", "instructions": "x", "title": "x",
            "agent_id": "orchestrator", "parent_session_id": "sess_parent"}
    with pytest.raises(ValueError, match="depth-1"):
        await orchestration.run_project(task)


async def test_create_path_refuses_orchestrator_as_child_target():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as e:
        await routes.create_and_run_task(
            "x", None, "orchestrator", actor="agent:orchestrator",
            parent_session_id="sess_parent")
    assert e.value.status_code == 422


async def test_orchestrate_pack_is_orchestrator_only():
    from central_command.runtime.packs import DEFAULT_PACKS
    from central_command.runtime.templates import TEMPLATES

    holders = [a for a, packs in DEFAULT_PACKS.items() if "orchestrate" in packs]
    assert holders == ["orchestrator"]
    for t in TEMPLATES.values():
        assert "orchestrate" not in t.default_packs, (
            "hire templates must not hand out the orchestrate pack — "
            "star shape is a structural property")


# --- restart-proofness + capability gaps ---------------------------------------


async def test_resume_works_purely_from_persisted_state(monkeypatch):
    # The M2 property, orchestration edition: after the park, NOTHING in
    # memory is needed — answer the item at the repo level (as a fresh
    # process would find it) and resume from the DB alone.
    monkeypatch.setattr(
        routes, "resolve_model",
        aresolve(lambda *a, **k: _text_model("Done.")),
    )
    r = await _start_project("Demo project: restart-proof.")
    sid = r["session_id"]
    item = await _open_item(sid, "plan_review")

    rs = await repo.get_session_run_state(sid)
    assert rs["orch"]["pending"] and rs["messages"], (
        "the park must persist messages + the pending map")

    await repo.answer_operator_item(item["id"], "", "approved")
    out = await orchestration.maybe_resume(sid)  # fresh-process entry point
    assert out is not None

    async def done():
        return (await repo.get_task(r["task_id"]))["status"] == "DONE"
    await _settle(done)


async def test_child_capability_gap_lands_on_the_event_log(monkeypatch):
    """A child that lacks the knowledge/tools/reference material a sub-task
    needs declares it through `declare_gap` (Task 8) against its OWN session —
    gaps are structured records now, not prose the parent greps for. This
    exercises `_collect`'s read path directly: a terminal child task whose
    session carries a declared gap must surface it in flags["gaps"]."""
    from central_command.runtime.tools import declare_gap

    class _ChildCtx:
        def __init__(self, session_id, agent_id):
            self.deps = type("D", (), {
                "session_id": session_id, "agent_id": agent_id,
            })()

    import uuid as _uuid

    task_id = "task_" + _uuid.uuid4().hex[:12]
    session_id = "sess_" + _uuid.uuid4().hex[:12]

    await repo.create_task(
        task_id, "Port the mainframe job", "Port TASKS-12 to the new stack",
        "jira-expert",
    )
    await repo.start_task(task_id, session_id)
    # Deliberately DIFFERENT from the task row's agent_id ("jira-expert"):
    # declare_gap's own payload carries whatever ctx.deps.agent_id was at
    # declaration time (here None, as a bare deps object with no agent_id
    # would produce) — the task row is the authoritative source for who was
    # actually assigned, and must win over the payload's copy.
    await declare_gap(
        _ChildCtx(session_id, None),
        kind="missing_agent", subject="cobol",
        need="nobody on the roster knows COBOL; a new agent or reference "
             "material before this can be done well",
    )
    await repo.resolve_task(task_id, "DONE", outcome="Review done.")

    ready, results, flags = await orchestration._collect(
        {"call_1": {"kind": "assign", "task_id": task_id}}
    )
    assert ready is True
    assert results  # the child's outcome still resolves the deferred call
    gaps = [g for g in flags["gaps"] if g["task_id"] == task_id]
    assert len(gaps) == 1
    # The task row is authoritative — must NOT be shadowed by the payload's
    # own (deps-sourced, here None) agent_id.
    assert gaps[0]["agent_id"] == "jira-expert"
    assert gaps[0]["kind"] == "missing_agent"
    assert "COBOL" in gaps[0]["need"]


# --- operator item guards ------------------------------------------------------


async def test_operator_item_answer_guards():
    r = await _start_project("Demo project: guards.")
    sid = r["session_id"]
    item = await _open_item(sid, "plan_review")

    with pytest.raises(ValueError, match="verdict"):
        await orchestration.answer_item(item["id"], "whatever", None)
    with pytest.raises(ValueError, match="revision requires a note"):
        await orchestration.answer_item(item["id"], "", "revise")

    await orchestration.answer_item(item["id"], "looks good", "approved")
    with pytest.raises(ValueError, match="already answered"):
        await orchestration.answer_item(item["id"], "again", "approved")

    # The answer is on the log BEFORE the resume it authorises.
    answered = [e for e in await repo.list_events_of_kinds(
        ["orchestration.item_answered"]) if e["ref_id"] == item["id"]]
    assert answered and answered[0]["payload"]["answer"] == "looks good"
