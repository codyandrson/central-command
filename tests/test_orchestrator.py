"""D7 orchestrator tests (recommendation mode).

The properties: an unassigned task gets a persisted, event-announced routing
recommendation with the orchestrator's OWN session transcript behind it;
"no agent fits" is an honest first-class answer; a hallucinated agent id is
mechanically sanitized (no agent claim trusted unverified); and an erroring
orchestrator degrades to the old behaviour — the task just stays NEW for the
operator. Assignment itself is never touched: recommending is the whole job.
"""

from __future__ import annotations

import uuid

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from central_command.config import settings
from central_command.db import repo
from central_command.runtime import orchestrator
from central_command.runtime.durable import simplify_messages
from central_command.runtime.spike_model import make_orchestrator_model
from tests.conftest import needs_pg

pytestmark = needs_pg


@pytest.fixture(autouse=True)
async def pinned(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "executor_mode", "dry_run")
    created: list[str] = []

    def track(task_id: str) -> str:
        created.append(task_id)
        return task_id

    pinned.track = track  # type: ignore[attr-defined]
    yield
    conn = await repo._conn()
    try:
        for tid in created:
            await conn.execute("delete from task where id = $1", tid)
    finally:
        await conn.close()


async def _new_task(instructions: str) -> dict:
    task_id = pinned.track("task_orch" + uuid.uuid4().hex[:8])  # type: ignore[attr-defined]
    await repo.create_task(task_id, instructions[:80], instructions, None)
    return await repo.get_task(task_id)


async def test_recommendation_is_stored_announced_and_transcribed():
    task = await _new_task("Review TASKS-12 in Jira for missing dependency links.")
    rec = await orchestrator.recommend(task, model=make_orchestrator_model())

    assert rec["agent_id"] == "jira-expert"
    stored = (await repo.get_task(task["id"]))["recommendation"]
    assert stored["agent_id"] == "jira-expert" and stored["rationale"]

    events = [e for e in await repo.list_events_of_kinds(["task.recommended"])
              if e["ref_id"] == task["id"]]
    assert len(events) == 1
    assert events[0]["actor"] == "agent:orchestrator"
    assert events[0]["payload"]["agent_id"] == "jira-expert"

    # The orchestrator's run is a first-class session: DONE, transcript kept.
    session_id = stored["session_id"]
    assert await repo.session_status(session_id) == "DONE"
    turns = simplify_messages(await repo.get_session_run_state(session_id))
    assert any("TASKS-12" in t["content"] for t in turns)

    # Recommending never assigns — the task is still the operator's to place.
    assert (await repo.get_task(task["id"]))["status"] == "NEW"


async def test_no_fitting_agent_is_an_honest_first_class_answer():
    task = await _new_task("Water the office plants every Tuesday.")
    rec = await orchestrator.recommend(task, model=make_orchestrator_model())
    assert rec["agent_id"] is None
    assert rec["rationale"]
    assert (await repo.get_task(task["id"]))["recommendation"]["agent_id"] is None


async def test_hallucinated_agent_id_is_sanitized_on_the_record():
    def _respond(messages, info) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart(
            tool_name=info.output_tools[0].name,
            args={"agent_id": "made-up-agent", "rationale": "Sounds right."},
        )])

    task = await _new_task("Do the thing.")
    rec = await orchestrator.recommend(task, model=FunctionModel(_respond))

    assert rec["agent_id"] is None
    assert rec["sanitized_from"] == "made-up-agent"
    events = [e for e in await repo.list_events_of_kinds(["task.recommended"])
              if e["ref_id"] == task["id"]]
    assert events[0]["payload"]["sanitized"] is True


async def test_agent_listing_covers_the_whole_roster_before_first_run():
    """Membership comes from the roster rows (D24: schema.sql seeds the
    founding five), not from who has run: an agent that has never executed
    still appears in the listing (the orchestrator was invisible until its
    first recommendation; the auditor hit the same gap once)."""
    from central_command.api.routes import list_agents
    from central_command.runtime.roster import SEED_IDS

    listed = {a["id"] for a in (await list_agents())["agents"]}
    assert set(SEED_IDS) <= listed


async def test_orchestrator_error_degrades_to_the_parked_task():
    def _explode(messages, info) -> ModelResponse:
        raise RuntimeError("orchestrator down")

    task = await _new_task("Route me if you can.")
    rec = await orchestrator.recommend(task, model=FunctionModel(_explode))

    assert rec is None
    after = await repo.get_task(task["id"])
    assert after["status"] == "NEW" and after["recommendation"] is None
    errors = [e for e in await repo.list_events_of_kinds(["task.route_error"])
              if e["ref_id"] == task["id"]]
    assert len(errors) == 1 and "orchestrator down" in errors[0]["payload"]["error"]
