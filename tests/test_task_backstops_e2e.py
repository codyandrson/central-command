"""Task backstops, end to end — epic-task-backstops story 12, R19's own signal.

A runaway chain of agent-created tasks, with the gate already off, stops at the
backstop and the stop is loud. Every scenario here drives REAL agent runs
(`routes.create_and_run_task` -> `run_task` -> the agent's `create_task` tool)
under scripted deterministic models; nothing calls the seam directly except the
two cases that exist to exercise a lock or an inherited chain, and they say so.

For each scenario the same four things are asserted:
  * a bounded number of task rows (the refused hop writes none),
  * `task.refused` events with the right reasons, returned by the Python
    `/events` route filtered by kind,
  * the board payload (`repo.list_tasks`, what the kanban route serves) marks
    the right card,
  * after the chain settles, no task is IN_PROGRESS without a session and no
    session is left RUNNING — a spin fails loudly at the deadline, never hangs.

No real model is ever resolved: the resolver the runs use is a dispatcher over
scripted models and raises for any agent it has no script for.
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from central_command.api import orchestration, routes
from central_command.config import settings
from central_command.db import repo
from central_command.runtime.deps import TriageDeps
from central_command.runtime.tools import create_task
from tests.conftest import aresolve, needs_pg
from tests.test_orchestration import _assigning_model

pytestmark = needs_pg

A, B, C, D, E = ("jira-expert", "litellm-manager", "confluence-expert",
                 "wiki-agent", "inbox-triage")
DEADLINE = 30.0
_OPEN = ("NEW", "ASSIGNED", "IN_PROGRESS")


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "executor_mode", "dry_run")


# --- scripted models --------------------------------------------------------


def _returns(messages) -> list[str]:
    return [str(p.content) for m in messages for p in getattr(m, "parts", [])
            if isinstance(p, ToolReturnPart) and p.tool_name == "create_task"]


def _creator(calls: list[dict], seen: list[str]) -> FunctionModel:
    """Fires `calls` as create_task tool calls in ONE response, then closes out
    in text once the results are back, recording what the agent was told."""
    def respond(messages, info) -> ModelResponse:
        got = _returns(messages)
        if got:
            seen.extend(got)
            return ModelResponse(parts=[TextPart(content="Closed — " + " | ".join(got))])
        return ModelResponse(parts=[
            ToolCallPart(tool_name="create_task", args=dict(c)) for c in calls])
    return FunctionModel(respond)


def _hop(assignee: str, seen: list[str]) -> FunctionModel:
    """The self-perpetuating agent: every time it runs it creates a successor."""
    return _creator([{"agent_id": assignee, "instructions": f"carry on to {assignee}"}],
                    seen)


def _text(content: str = "Done.") -> FunctionModel:
    return FunctionModel(lambda m, i: ModelResponse(parts=[TextPart(content=content)]))


class Resolver:
    """Stands in for `routes.resolve_model`: scripted agents get their script,
    every other agent a text-only double, and the whole call is recorded."""

    def __init__(self, scripts: dict[str, FunctionModel]):
        self.scripts = scripts
        self.asked: list[str] = []

    async def __call__(self, model=None, agent_id=None, **_kw):
        assert settings.demo_mode, "a real model would have been resolved"
        self.asked.append(agent_id)
        return self.scripts.get(agent_id) or _text()


@pytest.fixture
def scripted(monkeypatch):
    """Install a resolver over `scripts`; `models.resolve_model`, the seam a
    real model would come through, raises if anything reaches it."""
    from central_command.runtime import models

    async def real(model=None, *_a, **_k):
        if model is not None:  # the builders hand back the model they were given
            return model
        raise AssertionError("a real model was resolved in an end-to-end test")

    def install(scripts: dict[str, FunctionModel]) -> Resolver:
        resolver = Resolver(scripts)
        monkeypatch.setattr(routes, "resolve_model", resolver)
        monkeypatch.setattr(models, "resolve_model", real)
        return resolver

    return install


# --- observation ------------------------------------------------------------


async def _lineage(root: str) -> list[dict]:
    """The root task and every task reachable from it by `parent_task_id`."""
    rows = await repo.list_tasks(limit=500)
    found, frontier = {root}, {root}
    while frontier:
        frontier = {t["id"] for t in rows
                    if t.get("parent_task_id") in frontier and t["id"] not in found}
        found |= frontier
    return [t for t in rows if t["id"] in found]


async def _problems(tasks: list[dict]) -> list[str]:
    """What is wrong with a settled lineage: an IN_PROGRESS task with no live
    session, or any session still RUNNING (a run left spinning)."""
    bad = []
    for t in tasks:
        status = await repo.session_status(t["session_id"]) if t.get("session_id") else None
        if t["status"] == "IN_PROGRESS" and not t.get("session_id"):
            bad.append(f"{t['id']} IN_PROGRESS without a session")
        if status == "RUNNING":
            bad.append(f"{t['id']} session {t['session_id']} still RUNNING")
        if t["status"] in _OPEN and t["status"] != "IN_PROGRESS":
            bad.append(f"{t['id']} never started ({t['status']})")
    return bad


async def _settle(root: str, count: int) -> list[dict]:
    """Wait until the lineage has `count` tasks, all past the point of running.
    Bounded: a spinning chain (more rows, a run that never lands) fails here
    with the state it was in."""
    deadline = asyncio.get_running_loop().time() + DEADLINE
    while True:
        tasks = await _lineage(root)
        if len(tasks) == count and not await _problems(tasks):
            await asyncio.sleep(0.2)  # a late straggler would show as a row
            again = await _lineage(root)
            if len(again) == count and not await _problems(again):
                return again
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(
                f"chain did not settle at {count} tasks within {DEADLINE}s: "
                f"{[(t['id'], t['agent_id'], t['status']) for t in tasks]} "
                f"{await _problems(tasks)}")
        await asyncio.sleep(0.05)


async def _refused_events(since: int) -> list[dict]:
    """task.refused as the Python /events route returns it, filtered by kind."""
    out = await routes.get_events(since=since, kind="task.refused")
    return out["events"]


async def _card(task_id: str) -> dict:
    (card,) = [t for t in await repo.list_tasks(limit=500) if t["id"] == task_id]
    return card


def _by_agent(tasks: list[dict]) -> dict[str, dict]:
    return {t["agent_id"]: t for t in tasks}


async def _operator_task(agent: str) -> str:
    out = await routes.create_and_run_task(
        "start the chain", None, agent, background=True)
    return out["task_id"]


# --- (a) the self-perpetuating agent -----------------------------------------


async def test_a_self_perpetuating_chain_stops_at_the_depth_cap(scripted):
    ring = [A, B, C, D, E]
    seen: dict[str, list[str]] = {a: [] for a in ring}
    resolver = scripted({a: _hop(ring[(i + 1) % len(ring)], seen[a])
                         for i, a in enumerate(ring[:-1])})
    assert settings.task_max_depth == 3
    since = await repo.latest_event_id()

    root = await _operator_task(A)
    # operator task + T1 + T2 + T3: the fourth creation is the one refused.
    tasks = await _settle(root, 4)

    assert [t["agent_id"] for t in sorted(tasks, key=lambda t: len(t["creator_chain"]))] \
        == [A, B, C, D]
    by = _by_agent(tasks)
    assert by[D]["creator_chain"] == [A, B, C]
    assert by[D]["created_by"] == f"agent:{C}"
    assert E not in resolver.asked  # the refused hop never ran

    (refused,) = await _refused_events(since)
    assert refused["ref_id"] == by[D]["id"]  # the last creating task
    p = refused["payload"]
    assert p["reason"] == "depth"
    assert p["chain"] == [A, B, C, D]
    assert p["creator"] == f"agent:{D}" and p["agent_id"] == E

    # The refusal text went back to the agent, which closed out quoting it.
    assert len(seen[D]) == 1 and seen[D][0].startswith("Task NOT created")
    assert "depth" in seen[D][0] and "capped at 3" in seen[D][0]
    for a in (A, B, C):
        assert len(seen[a]) == 1 and "created and assigned" in seen[a][0]

    # Loud on the board: the mark is on the refusing task's card, nowhere else.
    assert (await _card(by[D]["id"]))["refusals"]["latest_reason"] == "depth"
    assert (await _card(by[D]["id"]))["refusals"]["count"] == 1
    for a in (A, B, C):
        assert (await _card(by[a]["id"]))["refusals"] is None
    # And no inbox item: a refusal is not a decision for the operator.
    assert not [i for i in await repo.list_operator_items("OPEN")
                if i["session_id"] in {t["session_id"] for t in tasks}]


# --- (b) a pair that assign each other ----------------------------------------


async def test_two_agents_assigning_each_other_stop_as_a_cycle(scripted):
    seen: dict[str, list[str]] = {A: [], B: []}
    scripted({A: _hop(B, seen[A]), B: _hop(A, seen[B])})
    since = await repo.latest_event_id()

    root = await _operator_task(A)
    tasks = await _settle(root, 2)  # the operator's task and A's task for B

    by = _by_agent(tasks)
    (refused,) = await _refused_events(since)
    assert refused["ref_id"] == by[B]["id"]
    assert refused["payload"]["reason"] == "cycle"
    assert refused["payload"]["chain"] == [A, B]
    assert refused["payload"]["agent_id"] == A

    (text,) = seen[B]
    assert text.startswith("Task NOT created") and "cycle" in text
    assert "Taskable teammates you may assign instead:" in text
    named = text.split("Taskable teammates you may assign instead:")[1]
    assert A not in named and B not in named  # the chain is never offered back
    assert C in named

    assert (await _card(by[B]["id"]))["refusals"]["latest_reason"] == "cycle"
    assert (await _card(by[A]["id"]))["refusals"] is None


# --- (c) a fan-out ------------------------------------------------------------


async def test_a_run_firing_more_creations_than_the_limit_is_cut_at_the_limit(
        scripted, monkeypatch):
    monkeypatch.setattr(settings, "task_fanout_limit", 3)
    seen: dict[str, list[str]] = {A: []}
    targets = [B, C, D, B, C]
    scripted({A: _creator(
        [{"agent_id": t, "instructions": f"job {i}"} for i, t in enumerate(targets)],
        seen[A])})
    since = await repo.latest_event_id()

    root = await _operator_task(A)
    tasks = await _settle(root, 1 + 3)

    children = [t for t in tasks if t["id"] != root]
    assert len(children) == 3 and {t["parent_task_id"] for t in children} == {root}
    refused = await _refused_events(since)
    assert len(refused) == 2  # the 4th and 5th, one event each
    assert {e["payload"]["reason"] for e in refused} == {"fan-out"}
    assert {e["ref_id"] for e in refused} == {root}
    assert all(e["payload"]["limit"] == 3 and e["payload"]["created"] == 3
               for e in refused)
    assert len(seen[A]) == 5
    assert sum(r.startswith("Task NOT created") and "fan-out" in r
               for r in seen[A]) == 2
    card = await _card(root)
    assert card["refusals"]["count"] == 2
    assert card["refusals"]["latest_reason"] == "fan-out"


async def test_concurrent_creations_from_one_task_never_overshoot_the_limit(
        monkeypatch, no_detached_runs):
    """The lock, exercised directly: the tool calls race under asyncio.gather
    (a real run serialises them — the tool is sequential — so only a direct
    burst can overlap), and the bound is exact."""
    monkeypatch.setattr(settings, "task_fanout_limit", 4)
    root = await _operator_task(A)
    session = "sess_" + uuid.uuid4().hex[:8]
    await repo.create_running_session(session, A)
    await repo.start_task(root, session)
    since = await repo.latest_event_id()
    ctx = SimpleNamespace(deps=TriageDeps(agent_id=A, session_id=session))

    outs = await asyncio.gather(*[
        create_task(ctx, [B, C, D][i % 3], f"burst {i}") for i in range(10)])

    tasks = await _lineage(root)
    assert len(tasks) == 1 + 4
    assert sum(o.startswith("Task NOT created") for o in outs) == 6
    refused = await _refused_events(since)
    assert len(refused) == 6
    assert {e["payload"]["reason"] for e in refused} == {"fan-out"}
    assert {e["ref_id"] for e in refused} == {root}
    assert (await _card(root))["refusals"]["count"] == 6


async def test_an_inherited_chain_marks_the_ancestor_task(monkeypatch, no_detached_runs):
    """A task-less session (a consult specialist) under a task's session: its
    refusal hangs on the ANCESTOR task, whose card carries the mark. Direct to
    the tool — a consult is not a scripted run here."""
    monkeypatch.setattr(settings, "task_max_depth", 0)
    root = await _operator_task(A)
    ancestor = "sess_" + uuid.uuid4().hex[:8]
    await repo.create_running_session(ancestor, A)
    await repo.start_task(root, ancestor)
    specialist = "sess_" + uuid.uuid4().hex[:8]
    await repo.create_running_session(specialist, B, parent_session_id=ancestor)
    since = await repo.latest_event_id()

    out = await create_task(
        SimpleNamespace(deps=TriageDeps(agent_id=B, session_id=specialist)),
        C, "go deeper")

    assert out.startswith("Task NOT created") and "depth" in out
    (refused,) = await _refused_events(since)
    assert refused["ref_id"] == root
    assert refused["payload"]["chain"] == [B]  # the operator's root adds none
    assert refused["payload"]["reason"] == "depth"
    assert (await _card(root))["refusals"]["latest_reason"] == "depth"
    assert len(await _lineage(root)) == 1


# --- (d) an agent-created project ---------------------------------------------


async def _project_of(root: str) -> dict:
    """Wait for the orchestrator task the scripted agent created, parked."""
    deadline = asyncio.get_running_loop().time() + DEADLINE
    while True:
        tasks = await _lineage(root)
        projects = [t for t in tasks if t["agent_id"] == "orchestrator"]
        if projects and projects[0].get("session_id"):
            sid = projects[0]["session_id"]
            if await repo.session_status(sid) == "AWAITING_AGENTS":
                return projects[0]
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"no parked project: {tasks}")
        await asyncio.sleep(0.05)


async def test_an_agent_created_project_parks_its_plan_before_round_one(scripted):
    seen: dict[str, list[str]] = {A: []}
    scripted({A: _hop("orchestrator", seen[A])})
    since = await repo.latest_event_id()

    root = await _operator_task(A)
    project = await _project_of(root)
    tasks = await _settle(root, 2)

    assert project["created_by"] == f"agent:{A}"
    assert project["creator_chain"] == [A] and project["parent_task_id"] == root
    sid = project["session_id"]
    # Parked, not spinning: IN_PROGRESS with a live (awaiting) session.
    assert project["status"] == "IN_PROGRESS"
    assert await repo.session_status(sid) == "AWAITING_AGENTS"
    (plan,) = [i for i in await repo.list_operator_items("OPEN")
               if i["session_id"] == sid]
    assert plan["kind"] == "plan_review"
    # No assignment happened before the operator read the plan.
    assert [t for t in await repo.list_tasks(limit=500)
            if t.get("parent_session_id") == sid] == []
    assert len(tasks) == 2 and await _refused_events(since) == []


async def test_a_project_batch_over_the_limit_is_refused_whole_and_marks_the_project(
        scripted, monkeypatch):
    seen: dict[str, list[str]] = {A: []}
    scripted({A: _hop("orchestrator", seen[A])})
    root = await _operator_task(A)
    project = await _project_of(root)
    sid = project["session_id"]
    monkeypatch.setattr(settings, "task_fanout_limit", 2)
    since = await repo.latest_event_id()

    (plan,) = [i for i in await repo.list_operator_items("OPEN")
               if i["session_id"] == sid]
    await repo.answer_operator_item(plan["id"], "", "approved")
    await orchestration.maybe_resume(sid, model=_assigning_model(B, C, D))

    refused = await _refused_events(since)
    assert [e["payload"]["reason"] for e in refused] == ["fan-out"]
    assert refused[0]["ref_id"] == project["id"]  # the project task, not a child
    assert refused[0]["payload"]["limit"] == 2
    # All-or-nothing: not even the two individually-fine assignments exist.
    assert [t for t in await repo.list_tasks(limit=500)
            if t.get("parent_session_id") == sid] == []
    assert (await _card(project["id"]))["refusals"]["latest_reason"] == "fan-out"
    assert (await _card(root))["refusals"] is None
    tasks = await _settle(root, 2)
    assert not await _problems(tasks)
