"""Task creation by an agent — the direct `create_task` trigger (story 4.7).

Stage 4 (2026-07-25) made `task.create` a gated capability: an agent proposed a
task for a teammate and the operator's approval was the delegation. The
operator's ruling of 2026-10-19 (GATE-032, FR-19) reversed it once the task
backstops shipped: task creation is a TRIGGER, not a write. An agent creates and
assigns a task directly, with no proposal and no inbox item; the run it starts
is governed like any other, so every gated change that run reaches still parks
for the operator. The file keeps its name: it pins what replaced the proposal.
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest
from pydantic_ai import ModelRetry

from central_command.api import routes
from central_command.config import settings
from central_command.db import repo
from central_command.gateway import executor
from central_command.runtime import packs
from central_command.runtime.deps import TriageDeps
from central_command.runtime.tools import create_task
from tests.conftest import aresolve, needs_pg


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)


def _ctx(agent_id: str | None, session_id: str | None) -> SimpleNamespace:
    return SimpleNamespace(deps=TriageDeps(agent_id=agent_id, session_id=session_id))


async def _running(agent_id: str) -> str:
    session_id = f"sess_create_{uuid.uuid4().hex[:8]}"
    await repo.create_running_session(session_id, agent_id)
    return session_id


async def _created_since(since: int) -> list[dict]:
    return [e for e in await repo.list_events(since_id=since, kind="task")
            if e["kind"] == "task.created"]


def test_task_create_is_no_longer_a_gated_write():
    """No registry entry and no handler survive (the operator's 2026-10-22
    decision: no deprecated handler), so the registry/HANDLERS parity holds and
    a draft naming it is an invented capability, refused in-run."""
    from central_command.gateway.capabilities import gated_write_names

    assert "task.create" not in gated_write_names()
    assert "task.create" not in executor.HANDLERS
    assert "task.create" not in packs.known_capability_names()
    assert "task-propose" not in packs.PACKS


def test_the_agents_that_proposed_tasks_now_create_them():
    """Story 4.7 grants `task-create` to exactly the agents that held
    `task-propose`; story 4.9 widened it to the rest (test_task_create_grants)."""
    for agent_id in ("inbox-triage", "jira-expert", "litellm-manager",
                     "confluence-expert", "wiki-agent"):
        assert "task-create" in packs.DEFAULT_PACKS[agent_id]
    assert "task-create" in packs.DEFAULT_PACKS["orchestrator"]


def test_every_template_can_create_a_task():
    """A newly hired agent gets the pack, including the read-only analyst —
    it changes nothing itself, and raising work for someone else is the one
    thing it may start."""
    from central_command.runtime.templates import TEMPLATES

    for t in TEMPLATES.values():
        assert "task-create" in t.default_packs, f"{t.id} cannot create a task"


def test_the_tool_is_offered_with_the_propose_tools_retry_budget():
    toolset = packs.toolset_for(("task-create",))
    (tool,) = [t for t in toolset.tools.values() if t.name == "create_task"]
    assert tool.max_retries == 10


@needs_pg
@pytest.mark.usefixtures("no_detached_runs")
async def test_an_agent_creates_a_task_with_no_proposal_and_no_inbox_item():
    creator = "inbox-triage"
    session = await _running(creator)
    since = await repo.latest_event_id()
    items_before = len(await repo.list_operator_items(status=None))

    out = await create_task(_ctx(creator, session), "jira-expert",
                            "Audit TASKS-12 links.", "Link audit")

    assert "created and assigned to jira-expert" in out
    (created,) = await _created_since(since)
    # The actor is the creating agent, derived from the running session.
    assert created["actor"] == f"agent:{creator}"
    task = await repo.get_task(created["ref_id"])
    assert task["agent_id"] == "jira-expert"
    assert task["title"] == "Link audit"
    assert task["instructions"] == "Audit TASKS-12 links."
    assert task["created_by"] == f"agent:{creator}"
    assert task["approved_by"] is None  # nobody approved: there was no gate
    assert task["parent_session_id"] == session
    assert task["creator_chain"] == [creator]
    assert not [p for p in await repo.list_proposals() if p["session_id"] == session]
    assert len(await repo.list_operator_items(status=None)) == items_before


@needs_pg
async def test_the_created_tasks_gated_proposal_still_parks_in_the_inbox(monkeypatch):
    """Assignment is a trigger: the gate attaches to what the work does, never
    to how it was started. The child runs a model that proposes a Jira write,
    and that proposal waits for the operator exactly as if the operator had
    created the task."""
    from central_command.runtime.spike_model import make_jira_task_model

    monkeypatch.setattr(routes, "resolve_model",
                        aresolve(lambda *a, **k: make_jira_task_model()))
    creator = "inbox-triage"
    session = await _running(creator)
    since = await repo.latest_event_id()

    await create_task(_ctx(creator, session), "jira-expert",
                      "Comment on TASKS-12 with the due date.")
    (created,) = await _created_since(since)
    task_id = created["ref_id"]

    for _ in range(250):
        task = await repo.get_task(task_id)
        if task["status"] == "REVIEW":
            break
        await asyncio.sleep(0.02)
    assert task["status"] == "REVIEW"
    props = [p for p in await repo.list_proposals()
             if p["session_id"] == task["session_id"]]
    assert len(props) == 1 and props[0]["status"] == "AWAITING_HUMAN"
    assert not [p for p in await repo.list_proposals() if p["session_id"] == session]


@needs_pg
@pytest.mark.usefixtures("no_detached_runs")
async def test_a_backstop_refusal_comes_back_to_the_agent_as_text(monkeypatch):
    """The seam's 422 is the agent's answer, never a crashed run: a chain past
    the depth cap is refused with the reason, recorded as task.refused, and
    leaves no task row."""
    a, b = "jira-expert", "litellm-manager"
    root = (await routes.create_and_run_task("root work", None, a))["task_id"]
    session = await _running(a)
    await repo.start_task(root, session)
    since = await repo.latest_event_id()
    before = len(await repo.list_tasks())

    monkeypatch.setattr(settings, "task_max_depth", 0)
    out = await create_task(_ctx(a, session), b, "one hop too many")

    assert out.startswith("Task NOT created")
    assert "depth" in out
    assert len(await repo.list_tasks()) == before
    refused = [e for e in await repo.list_events(since_id=since, kind="task")
               if e["kind"] == "task.refused"]
    assert len(refused) == 1 and refused[0]["ref_id"] == root
    assert not await _created_since(since)


@needs_pg
@pytest.mark.usefixtures("no_detached_runs")
async def test_a_task_for_a_non_taskable_agent_is_refused_with_no_row():
    """The auditor is deliberately not taskable — it is part of the gate."""
    session = await _running("inbox-triage")
    before = len(await repo.list_tasks())

    out = await create_task(_ctx("inbox-triage", session), "auditor", "audit yourself")

    assert out.startswith("Task NOT created") and "auditor" in out
    assert len(await repo.list_tasks()) == before


@needs_pg
@pytest.mark.usefixtures("no_detached_runs")
async def test_a_malformed_draft_is_handed_back_with_every_problem():
    session = await _running("inbox-triage")
    before = len(await repo.list_tasks())

    with pytest.raises(ModelRetry) as exc:
        await create_task(_ctx("inbox-triage", session), "", "")

    assert "agent_id" in str(exc.value) and "instructions" in str(exc.value)
    assert len(await repo.list_tasks()) == before


@needs_pg
@pytest.mark.usefixtures("no_detached_runs")
@pytest.mark.parametrize("agent_id,has_session", [(None, True), ("inbox-triage", False)])
async def test_a_run_with_no_agent_or_session_cannot_create(agent_id, has_session):
    """Provenance is the running session's; a run that carries none would make
    an unattributed task, so the tool refuses before it reaches the seam."""
    session = await _running("inbox-triage") if has_session else None
    since = await repo.latest_event_id()

    out = await create_task(_ctx(agent_id, session), "jira-expert", "do it")

    assert out.startswith("Task NOT created")
    assert not await _created_since(since)


def test_the_tool_takes_no_provenance_argument():
    """Creator, parent task and chain are derived at the seam from the running
    session — a value an agent can write is an agent claim, so the tool offers
    no parameter to write one."""
    import inspect

    params = set(inspect.signature(create_task).parameters) - {"ctx"}
    assert params == {"agent_id", "instructions", "title", "await_result"}
