"""D7 phase 2 — the orchestration runtime: plan review → assign → receive →
decide, parked on the SAME durable-pause machinery as approval waits.

What these tests lock in:
- the full loop offline (plan park → operator approval → child assignment →
  ungated text answer flows back WITHOUT approval → final report);
- the gated path (a child's proposal parks in the Decisions Inbox; the loop
  resumes only after the operator's decision);
- the mechanical guards: the task backstops over the orchestrator's batch
  (an agent-created project runs; depth, cycle and fan-out refuse before any
  child exists), plan-before-assign, stall
  escalation (The operator: pause when stuck, never budgets), batch rejection bounded;
- restart-proofness: resume works purely from persisted state (M2's
  two-process property, orchestration edition);
- the capability-gap principle: a child's declared gap becomes event-log data.

A test that settles on one write and then asserts a second is racy against the
background resume (epic-small-fixes R31): `_settle` waits on every fact the
test goes on to assert.

epic-small-fixes R13 / D7-P (story 11.6, ruled 2026-10-29, Decision 3): a revise
answer parks a second plan review, an answered NO-PROGRESS stall withdraws
plan approval (the orchestrator must submit_plan again before assign_work),
an answered fan-out stall does not, and a continue window never does.
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
    monkeypatch.setattr(settings, "test_model", True)


async def _settle(cond, timeout: float = 5.0):
    """Wait for background child runs / resume hooks (instant test-model runs,
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
    # returns to the orchestrator with NO approval anywhere (The operator's rule).
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

    # The child runs in the background and parks REVIEW with a proposal, while
    # the orchestrator's own resume parks it AWAITING_AGENTS again: settle on
    # every fact asserted below, not just the first to land (R31).
    async def child_in_review():
        kids = [t for t in await repo.list_tasks()
                if t.get("parent_session_id") == sid]
        if not kids or kids[0]["status"] != "REVIEW":
            return False
        waiting = [p for p in await repo.list_proposals()
                   if p["session_id"] == kids[0]["session_id"]
                   and p["status"] == "AWAITING_HUMAN"]
        return bool(waiting) and await repo.session_status(sid) == "AWAITING_AGENTS"
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


async def test_orchestrator_self_assignment_is_refused_as_a_cycle():
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
    assert mine and any("is yourself" in p and "cycle" in p
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


async def test_project_exceeding_orch_fanout_limit_parks_an_operator_question(
        monkeypatch):
    """The orchestrator's own fan-out ceiling (not the task backstop's count,
    which lives at the create seam) pauses the loop for the operator: nothing
    is assigned and the question names the fan-out backstop."""
    monkeypatch.setattr(settings, "orch_fanout_limit", 0)
    r = await _start_project()
    sid = r["session_id"]
    plan = await _open_item(sid, "plan_review")

    await orchestration.answer_item(plan["id"], "", "approved")

    # `_park_stalled` writes the stall item (orchestration.py:296) BEFORE it
    # parks the session AWAITING_AGENTS (:308), and answer_item resumes in the
    # background: settle on BOTH facts the test asserts (R31).
    async def parked():
        return (any(i["session_id"] == sid and i["kind"] == "stall"
                    for i in await repo.list_operator_items("OPEN"))
                and await repo.session_status(sid) == "AWAITING_AGENTS")
    await _settle(parked)

    item = await _open_item(sid, "stall")
    assert "PAUSED by the control plane" in item["body"]
    assert "fan-out backstop" in item["body"]
    assert "were NOT executed" in item["body"]
    stalled = [e for e in await repo.list_events_of_kinds(["orchestration.stalled"])
               if e["ref_id"] == sid]
    assert stalled and "fan-out backstop" in stalled[0]["payload"]["reason"]
    # The batch did not run: no child task hangs off the orchestrator session.
    assert [t for t in await repo.list_tasks()
            if t.get("parent_session_id") == sid] == []
    assert await repo.session_status(sid) == "AWAITING_AGENTS"


# --- the revised plan and the answered stall (epic-small-fixes R13, 2026-10-29) --


def _replan_model(*, stall_with: str = "no_progress"):
    """plan -> (approved) -> a round the control plane PAUSES -> (stall answered)
    -> assign_work -> (rejected, plan not approved) -> submit_plan again ->
    (approved) -> assign_work -> text. `stall_with` picks the round the pause
    happens on: a round that reports no progress, or an ordinary one (the
    fan-out backstop is what pauses it)."""
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        TextPart,
        ToolCallPart,
        ToolReturnPart,
    )
    from pydantic_ai.models.function import FunctionModel

    def progress(made: bool, step: str):
        return ToolCallPart(tool_name="record_progress", args={
            "made_progress": made, "in_a_loop": not made, "next_step": step})

    def assign():
        return ToolCallPart(tool_name="assign_work", args={
            "agent_id": "jira-expert", "title": "job",
            "instructions": "Demo: job."})

    def respond(messages, info):
        latest = {p.tool_name: str(p.content)
                  for p in getattr(messages[-1], "parts", [])
                  if isinstance(p, ToolReturnPart)}
        approvals = sum(
            1 for m in messages if isinstance(m, ModelRequest)
            for p in m.parts if isinstance(p, ToolReturnPart)
            and p.tool_name == "submit_plan"
            and str(p.content).startswith("PLAN APPROVED"))
        if "assign_work" in latest:
            text = latest["assign_work"]
            if text.startswith("BATCH REJECTED"):
                return ModelResponse(parts=[
                    progress(True, "replan"),
                    ToolCallPart(tool_name="submit_plan",
                                 args={"plan": "PLAN 2: replanned."})])
            if text.startswith("OPERATOR ANSWER"):
                return ModelResponse(parts=[progress(True, "assign"), assign()])
            return ModelResponse(parts=[TextPart(content="Done.")])
        if "submit_plan" in latest:
            if latest["submit_plan"].startswith("PLAN NOT APPROVED"):
                return ModelResponse(parts=[
                    progress(True, "revise"),
                    ToolCallPart(tool_name="submit_plan",
                                 args={"plan": "PLAN 2: revised."})])
            first = approvals <= 1
            made = not (first and stall_with == "no_progress")
            return ModelResponse(parts=[progress(made, "go"), assign()])
        return ModelResponse(parts=[
            progress(True, "plan"),
            ToolCallPart(tool_name="submit_plan", args={"plan": "PLAN: go."})])

    return FunctionModel(respond)


async def _answer_and_resume(item: dict, answer: str, verdict, sid: str, model):
    await repo.answer_operator_item(item["id"], answer, verdict)
    return await orchestration.maybe_resume(sid, model=model)


async def _rejections(sid: str) -> list[dict]:
    return [e for e in await repo.list_events_of_kinds(["orchestration.batch_rejected"])
            if e["ref_id"] == sid]


async def test_a_revise_answer_parks_a_second_plan_review(monkeypatch):
    """A revise note withdraws approval and the orchestrator's revised plan
    parks as a SECOND plan review (R13: the revise round trip, as built)."""
    model = _replan_model()
    r = await _start_project()
    sid = r["session_id"]
    first = await _open_item(sid, "plan_review")

    await _answer_and_resume(first, "Split it in two.", "revise", sid, model)

    second = await _open_item(sid, "plan_review")
    assert second["id"] != first["id"]
    assert "PLAN 2: revised." in second["body"]
    rs = await repo.get_session_run_state(sid)
    assert rs["orch"]["plan_approved"] is False
    assert [t for t in await repo.list_tasks()
            if t.get("parent_session_id") == sid] == []


async def test_an_answered_no_progress_stall_withdraws_plan_approval(monkeypatch):
    """The ruling (2026-10-29, Decision 3): the operator's answer to a
    NO-PROGRESS pause is guidance, not approval of what comes next. The next
    assign_work is rejected until a NEW plan review is answered."""
    monkeypatch.setattr(settings, "orch_stall_limit", 1)
    monkeypatch.setattr(
        routes, "resolve_model",
        aresolve(lambda *a, **k: _text_model("Done.")))
    model = _replan_model(stall_with="no_progress")
    r = await _start_project()
    sid = r["session_id"]
    plan = await _open_item(sid, "plan_review")

    # Approved, then a round with no progress: the control plane pauses it.
    await _answer_and_resume(plan, "", "approved", sid, model)
    stall = await _open_item(sid, "stall")
    assert "withdraws plan approval" in stall["body"]
    rs = await repo.get_session_run_state(sid)
    assert rs["orch"]["plan_approved"] is True
    # The reason travels on the pending entries, so the resume can tell.
    assert {p["no_progress"] for p in rs["orch"]["pending"].values()} == {True}
    assert await _rejections(sid) == []

    # The operator answers. The orchestrator is told it is guidance only, its
    # next assign_work is REJECTED, and it re-plans: a second plan review parks.
    await _answer_and_resume(stall, "Try the other approach.", None, sid, model)
    (rej,) = await _rejections(sid)
    assert any("APPROVED plan" in p for p in rej["payload"]["problems"])
    assert [t for t in await repo.list_tasks()
            if t.get("parent_session_id") == sid] == []
    second = await _open_item(sid, "plan_review")
    assert "PLAN 2: replanned." in second["body"]
    rs = await repo.get_session_run_state(sid)
    assert rs["orch"]["plan_approved"] is False and rs["orch"]["stalls"] == 0
    answers = [str(p.get("content")) for m in rs["messages"]
               for p in m.get("parts", [])
               if p.get("part_kind") == "tool-return"
               and str(p.get("content")).startswith("OPERATOR ANSWER")]
    assert answers and all("submit_plan again before assign_work" in a
                           for a in answers)

    # Only a newly answered plan review lets assign_work through.
    await _answer_and_resume(second, "", "approved", sid, model)
    assert len(await _rejections(sid)) == 1
    (child,) = [t for t in await repo.list_tasks()
                if t.get("parent_session_id") == sid]
    assert child["parent_task_id"] == r["task_id"]


async def test_an_answered_fan_out_stall_leaves_plan_approval_alone(monkeypatch):
    """The round and fan-out backstops keep today's meaning (Decision 3): their
    counters are never reset by an answer, so withdrawing approval would send a
    lone submit_plan into the same backstop and no plan review could ever
    park. An answered fan-out stall leaves approval as it was."""
    monkeypatch.setattr(settings, "orch_fanout_limit", 0)
    model = _replan_model(stall_with="fan_out")
    r = await _start_project()
    sid = r["session_id"]
    plan = await _open_item(sid, "plan_review")

    await _answer_and_resume(plan, "", "approved", sid, model)
    stall = await _open_item(sid, "stall")
    assert "fan-out backstop" in stall["body"]
    assert "withdraws plan approval" not in stall["body"]
    rs = await repo.get_session_run_state(sid)
    assert {p["no_progress"] for p in rs["orch"]["pending"].values()} == {False}

    await _answer_and_resume(stall, "Carry on.", None, sid, model)

    # The orchestrator's next assign_work was NOT rejected for a missing plan:
    # it ran into the same backstop and parked a second stall.
    assert await _rejections(sid) == []
    again = await _open_item(sid, "stall")
    assert again["id"] != stall["id"]
    rs = await repo.get_session_run_state(sid)
    assert rs["orch"]["plan_approved"] is True
    assert [i for i in await repo.list_operator_items("OPEN")
            if i["session_id"] == sid and i["kind"] == "plan_review"] == []


async def test_a_continue_window_never_withdraws_plan_approval(monkeypatch):
    """A window grant is the operator saying 'keep going', not an answered
    stall: the approved plan stays approved across it (R13, Decision 3), so the
    next assign_work runs instead of being rejected for a missing plan."""
    from pydantic_ai.messages import ModelResponse, ToolCallPart
    from pydantic_ai.models.function import FunctionModel

    monkeypatch.setattr(settings, "run_request_limit", 3)
    monkeypatch.setattr(
        routes, "resolve_model",
        aresolve(lambda *a, **k: _text_model("Done.")))
    phase = {"now": "plan"}

    def progress():
        return ToolCallPart(tool_name="record_progress", args={
            "made_progress": True, "in_a_loop": False, "next_step": "go"})

    def respond(messages, info):
        if phase["now"] == "plan":
            return ModelResponse(parts=[progress(), ToolCallPart(
                tool_name="submit_plan", args={"plan": "PLAN: go."})])
        if phase["now"] == "loop":
            return ModelResponse(parts=[progress()])  # never ends the round
        return ModelResponse(parts=[progress(), ToolCallPart(
            tool_name="assign_work", args={
                "agent_id": "jira-expert", "title": "job",
                "instructions": "Demo: job."})])

    model = FunctionModel(respond)
    r = await _start_project()
    sid = r["session_id"]
    plan = await _open_item(sid, "plan_review")

    phase["now"] = "loop"
    await repo.answer_operator_item(plan["id"], "", "approved")
    parked = await orchestration.maybe_resume(sid, model=model)
    assert parked["window_exhausted"] is True
    assert await repo.session_status(sid) == "AWAITING_CONTINUE"
    marker = (await repo.get_session_run_state(sid))["pending_continue"]
    assert marker["state"]["plan_approved"] is True

    phase["now"] = "assign"
    monkeypatch.setattr(settings, "run_request_limit", 25)
    await orchestration.continue_session(parked["item_id"], model=model)

    assert await _rejections(sid) == []
    (child,) = [t for t in await repo.list_tasks()
                if t.get("parent_session_id") == sid]
    assert child["parent_task_id"] == r["task_id"]


async def test_assign_work_children_count_against_the_project_task():
    """The driver's assign_work lands on the create seam, so its children are
    counted by the project task, not a second check in the driver."""
    r = await _start_project()
    sid = r["session_id"]
    plan = await _open_item(sid, "plan_review")
    await orchestration.answer_item(plan["id"], "", "approved")

    async def assigned():
        return (any(t.get("parent_session_id") == sid
                    for t in await repo.list_tasks())
                and await repo.count_child_tasks(parent_task_id=r["task_id"]) == 1)
    await _settle(assigned)

    (child,) = [t for t in await repo.list_tasks()
                if t.get("parent_session_id") == sid]
    assert child["parent_task_id"] == r["task_id"]
    assert await repo.count_child_tasks(parent_task_id=r["task_id"]) == 1


def _assigning_model(*targets: str):
    """plan → (approved) → one assign_work per target in ONE round → text
    surrender once the batch comes back rejected, quoting the rejection."""
    from pydantic_ai.messages import (
        ModelResponse,
        TextPart,
        ToolCallPart,
        ToolReturnPart,
    )
    from pydantic_ai.models.function import FunctionModel

    def respond(messages, info):
        returns = {p.tool_name: str(p.content) for m in messages
                   for p in getattr(m, "parts", [])
                   if isinstance(p, ToolReturnPart)}
        if "BATCH REJECTED" in returns.get("assign_work", ""):
            return ModelResponse(parts=[TextPart(
                content="Rejected: " + returns["assign_work"])])
        if "submit_plan" in returns:
            return ModelResponse(parts=[
                ToolCallPart(tool_name="assign_work", args={
                    "agent_id": t, "title": f"job {i}",
                    "instructions": f"Demo: job {i}."})
                for i, t in enumerate(targets)])
        return ModelResponse(parts=[ToolCallPart(
            tool_name="submit_plan", args={"plan": "PLAN: delegate."})])

    return FunctionModel(respond)


async def _start_agent_project(creator: str = "jira-expert") -> tuple[dict, str]:
    """An agent-created project, the way an approved task.create makes one: the
    creating session is the parent. Returns (run result, creating session)."""
    creating = "sess_" + __import__("uuid").uuid4().hex[:12]
    await repo.create_running_session(creating, creator)
    r = await routes.create_and_run_task(
        "Demo project: review TASKS-12.", "Agent project", "orchestrator",
        actor="human:lee", background=False, parent_session_id=creating)
    return r, creating


async def _approve_plan(sid: str, model) -> None:
    item = await _open_item(sid, "plan_review")
    await repo.answer_operator_item(item["id"], "", "approved")
    await orchestration.maybe_resume(sid, model=model)


async def test_agent_created_project_reaches_its_plan_review_park():
    r, creating = await _start_agent_project()

    # The orchestrator is a valid assignee for an agent creator: it ran as a
    # project and parked on the operator's plan review before round one.
    assert r["parked"] is True
    assert await repo.session_status(r["session_id"]) == "AWAITING_AGENTS"
    await _open_item(r["session_id"], "plan_review")
    project = await repo.get_task(r["task_id"])
    assert project["status"] == "IN_PROGRESS"
    assert project["parent_session_id"] == creating
    assert project["created_by"] == "agent:jira-expert"
    assert project["creator_chain"] == ["jira-expert"]
    # Nothing was assigned before the plan was approved.
    assert [t for t in await repo.list_tasks()
            if t.get("parent_session_id") == r["session_id"]] == []


async def test_an_agent_projects_children_carry_the_orchestrator_in_their_chain(
        monkeypatch):
    monkeypatch.setattr(
        routes, "resolve_model",
        aresolve(lambda *a, **k: _text_model("Done.")))
    r, _ = await _start_agent_project()
    await _approve_plan(r["session_id"], _assigning_model("litellm-manager"))

    (child,) = [t for t in await repo.list_tasks()
                if t.get("parent_session_id") == r["session_id"]]
    assert child["parent_task_id"] == r["task_id"]
    assert child["creator_chain"] == ["jira-expert", "orchestrator"]
    assert child["created_by"] == "agent:orchestrator"


async def test_the_orchestrators_child_past_the_depth_cap_is_refused(monkeypatch):
    # operator-less chain: jira-expert -> project -> child makes two hops.
    monkeypatch.setattr(settings, "task_max_depth", 1)
    r, _ = await _start_agent_project()
    sid = r["session_id"]
    since = await repo.latest_event_id()
    await _approve_plan(sid, _assigning_model("litellm-manager"))

    assert [t for t in await repo.list_tasks()
            if t.get("parent_session_id") == sid] == []
    (rej,) = [e for e in await repo.list_events_of_kinds(
        ["orchestration.batch_rejected"]) if e["ref_id"] == sid]
    assert any("(depth)" in p for p in rej["payload"]["problems"])
    (refused,) = [e for e in await repo.list_events(since_id=since, kind="task")
                  if e["kind"] == "task.refused"]
    assert refused["ref_id"] == r["task_id"]
    assert refused["payload"]["reason"] == "depth"
    assert refused["payload"]["chain"] == ["jira-expert", "orchestrator"]


async def test_the_orchestrator_cannot_hand_work_back_to_its_creator():
    r, _ = await _start_agent_project("jira-expert")
    sid = r["session_id"]
    since = await repo.latest_event_id()
    await _approve_plan(sid, _assigning_model("jira-expert"))

    (rej,) = [e for e in await repo.list_events_of_kinds(
        ["orchestration.batch_rejected"]) if e["ref_id"] == sid]
    assert any("(cycle)" in p for p in rej["payload"]["problems"])
    (refused,) = [e for e in await repo.list_events(since_id=since, kind="task")
                  if e["kind"] == "task.refused"]
    assert refused["payload"]["reason"] == "cycle"


async def test_a_batch_whose_third_assignment_exceeds_the_cap_creates_none(
        monkeypatch):
    monkeypatch.setattr(settings, "task_fanout_limit", 2)
    r = await _start_project()
    sid = r["session_id"]
    since = await repo.latest_event_id()
    await _approve_plan(sid, _assigning_model(
        "jira-expert", "litellm-manager", "confluence-expert"))

    # All-or-nothing: the first two (individually fine) were NOT created.
    assert [t for t in await repo.list_tasks()
            if t.get("parent_session_id") == sid] == []
    (rej,) = [e for e in await repo.list_events_of_kinds(
        ["orchestration.batch_rejected"]) if e["ref_id"] == sid]
    (line,) = rej["payload"]["problems"]
    assert "confluence-expert" in line and "(fan-out)" in line
    # The refusal reached the orchestrator in its tool result.
    assert "(fan-out)" in (await repo.get_task(r["task_id"]))["outcome"]
    # Recorded once, for the one refused assignment — not per retry or sibling.
    (refused,) = [e for e in await repo.list_events(since_id=since, kind="task")
                  if e["kind"] == "task.refused"]
    assert refused["ref_id"] == r["task_id"]
    assert refused["payload"]["reason"] == "fan-out"
    assert refused["payload"]["limit"] == 2 and refused["payload"]["created"] == 2


async def test_the_batch_precheck_counts_children_that_already_exist(monkeypatch):
    monkeypatch.setattr(settings, "task_fanout_limit", 2)
    r = await _start_project()
    sid = r["session_id"]
    await repo.create_task("task_prior", "p", "prior", "jira-expert",
                           parent_session_id=sid, parent_task_id=r["task_id"])
    await _approve_plan(sid, _assigning_model("litellm-manager", "wiki-agent"))

    # One existing child + a batch of two reaches the cap at the second.
    assert [t["id"] for t in await repo.list_tasks()
            if t.get("parent_session_id") == sid] == ["task_prior"]
    (rej,) = [e for e in await repo.list_events_of_kinds(
        ["orchestration.batch_rejected"]) if e["ref_id"] == sid]
    (line,) = rej["payload"]["problems"]
    assert "wiki-agent" in line and "(fan-out)" in line


async def test_two_agents_creating_tasks_for_each_other_do_not_deadlock(
        monkeypatch):
    """A and B each hold their own per-agent task lock while creating a task
    for the other. Creation is background, so neither waits on the other's
    lock; a timeout makes a deadlock fail instead of hang."""
    import uuid

    from central_command.runtime import run as run_mod

    a, b = "jira-expert", "litellm-manager"
    inside: set[str] = set()
    both_inside = asyncio.Event()
    created: list[str] = []

    async def fake_run_task(agent_id, instructions, model=None,
                            parent_session_id=None, task_id=None, **_kw):
        sess = "sess_" + uuid.uuid4().hex[:12]
        await repo.create_running_session(sess, agent_id)
        await repo.start_task(task_id, sess)
        if instructions.startswith("root"):
            inside.add(agent_id)
            if len(inside) == 2:
                both_inside.set()
            await both_inside.wait()  # both locks are held at this point
            other = b if agent_id == a else a
            out = await routes.create_and_run_task(
                f"made by {agent_id}", None, other, actor=f"agent:{agent_id}",
                background=True, parent_session_id=sess)
            created.append(out["task_id"])
        return {"deferred": False, "output": "ok", "session_id": sess}

    monkeypatch.setattr(run_mod, "run_task", fake_run_task)

    async def scenario():
        for agent in (a, b):
            await routes.create_and_run_task(
                f"root for {agent}", None, agent, background=True)
        async def all_done():
            if len(created) < 2:
                return False
            return all([(await repo.get_task(t))["status"] == "DONE"
                        for t in created])
        await _settle(all_done, timeout=8.0)

    await asyncio.wait_for(scenario(), timeout=15.0)


async def test_orchestrate_pack_is_orchestrator_only():
    from central_command.runtime.packs import DEFAULT_PACKS
    from central_command.runtime.templates import TEMPLATES

    holders = [a for a, packs in DEFAULT_PACKS.items() if "orchestrate" in packs]
    assert holders == ["orchestrator"]
    for t in TEMPLATES.values():
        assert "orchestrate" not in t.default_packs, (
            "hire templates must not hand out the orchestrate pack — "
            "the orchestrator loop is a structural property")


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
