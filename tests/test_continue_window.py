"""A run that spends its request window ASKS for another instead of dying.

`run_request_limit` is a loop breaker, not a budget — but a run that hits it has
usually done real work, and throwing that away is the wrong end of the trade.
So a CONTINUABLE run (slice 1: the task path) parks AWAITING_CONTINUE with its
transcript intact and puts a `continue` item in the Decisions Inbox: continue
grants another window of the same size, decline ends the run.

The load-bearing property is the TAIL. pydantic-ai raises `UsageLimitExceeded`
*after* appending the pending `ModelRequest` that carries every tool return of
the last batch, and `_run_live`'s best-effort except-path snapshot is what
persists it. Without that message the history would end in a `ModelResponse`
with unanswered tool calls and re-entry would re-execute the whole batch — so
`test_the_park_leaves_a_model_request_tail` is the test this feature stands on.

Slice 2 extends it to the two remaining high-value paths — a conversation turn
and an orchestrator round — and with them the marker's `after` becomes a real
dispatch: the same continuation driver has to re-enter three different landing
tails (`run._land`, `converse._land_turn`, `orchestration._process`) and land
each exactly as an uninterrupted run would.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from central_command.api import orchestration
from central_command.config import settings
from central_command.db import repo
from central_command.runtime.run import run_task
from tests.conftest import needs_pg

pytestmark = needs_pg


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    # demo_mode: `run_task` resolves a model itself for the agents it builds.
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "executor_mode", "dry_run")
    monkeypatch.setattr(settings, "dispatch_approval_limit", 10_000)
    monkeypatch.setattr(settings, "auditor_enabled", False)


def _looping_model(stop_after: int | None = None) -> FunctionModel:
    """A model that keeps calling one ungated tool. `stop_after` model calls it
    answers in text instead — which is how a CONTINUED run reaches a normal
    ending. `declare_gap` is on every hired agent and changes nothing."""
    calls = {"n": 0}

    def _respond(messages, info) -> ModelResponse:
        calls["n"] += 1
        if stop_after is not None and calls["n"] > stop_after:
            return ModelResponse(parts=[TextPart(content="done after the extra window")])
        return ModelResponse(parts=[ToolCallPart(
            tool_name="declare_gap",
            args={"kind": "missing_tool", "subject": "looping",
                  "need": "a tool I do not have"},
        )])

    return FunctionModel(_respond)


async def test_the_park_leaves_a_model_request_tail(monkeypatch):
    """AWAITING_CONTINUE + an OPEN `continue` item + a transcript that ends in
    the unsent ModelRequest (every tool return answered, nothing dangling)."""
    monkeypatch.setattr(settings, "run_request_limit", 3)

    out = await run_task("jira-expert", "Loop forever.", model=_looping_model())

    assert out["window_exhausted"] is True
    # The awaiting-operator shape the landing paths already understand.
    assert out["asked"] is True and out["tier"] == "continue"
    session_id = out["session_id"]
    assert await repo.session_status(session_id) == "AWAITING_CONTINUE"

    rs = await repo.get_session_run_state(session_id)
    tail = rs["messages"][-1]
    assert tail["kind"] == "request", "the unsent ModelRequest must be persisted"
    assert any(p["part_kind"] == "tool-return" for p in tail["parts"]), (
        "the tail must carry the last batch's tool returns — otherwise re-entry "
        "re-executes them"
    )
    assert rs["pending_continue"]["windows"] == 1
    assert rs["pending_continue"]["requests"] == 3

    item = await repo.get_operator_item(out["item_id"])
    assert item["kind"] == "continue" and item["status"] == "OPEN"
    assert item["session_id"] == session_id and item["agent_id"] == "jira-expert"

    parked = [e for e in await repo.list_events_of_kinds(["session.window_exhausted"])
              if e["ref_id"] == session_id]
    assert len(parked) == 1

    # NOT the auto-retried park: the sweep must never drive this one, or the
    # limit's whole point is undone.
    assert session_id not in await repo.sessions_awaiting_resume()


async def test_continuing_re_enters_with_a_fresh_window_and_lands_normally(monkeypatch):
    """The continuation reaches a normal ending — and the window size is read
    from settings NOW, not from the park marker (Cline's bug: a cap read once at
    task start makes changing it mid-task do nothing)."""
    monkeypatch.setattr(settings, "run_request_limit", 3)
    model = _looping_model(stop_after=3)

    parked = await run_task("jira-expert", "Loop, then finish.", model=model)
    session_id = parked["session_id"]
    assert await repo.session_status(session_id) == "AWAITING_CONTINUE"

    monkeypatch.setattr(settings, "run_request_limit", 25)
    out = await orchestration.continue_session(parked["item_id"], model=model)

    assert out["continued"] is True and out["window"] == 2
    assert out["deferred"] is False
    assert "done after the extra window" in out["output"]
    assert await repo.session_status(session_id) == "DONE"

    # One run, continued — not a second one: the same session carries both.
    assert out["session_id"] == session_id
    continued = [e for e in await repo.list_events_of_kinds(["session.continued"])
                 if e["ref_id"] == session_id]
    assert len(continued) == 1
    assert continued[0]["payload"]["requests"] == 25


async def test_a_second_window_parks_again_rather_than_failing(monkeypatch):
    """Repeatable by design: a run that spends the granted window asks again,
    one window higher. There is no cumulative ceiling — every window already
    costs an explicit human click."""
    monkeypatch.setattr(settings, "run_request_limit", 3)
    model = _looping_model()
    parked = await run_task("jira-expert", "Loop forever.", model=model)

    again = await orchestration.continue_session(parked["item_id"], model=model)
    assert again["window_exhausted"] is True and again["window"] == 2
    assert again["item_id"] != parked["item_id"]
    assert await repo.session_status(parked["session_id"]) == "AWAITING_CONTINUE"
    rs = await repo.get_session_run_state(parked["session_id"])
    assert rs["pending_continue"]["windows"] == 2
    assert rs["messages"][-1]["kind"] == "request"


async def test_declining_ends_the_run(monkeypatch):
    """Today's behaviour, chosen instead of imposed: session terminal FAILED."""
    monkeypatch.setattr(settings, "run_request_limit", 3)
    parked = await run_task("jira-expert", "Loop forever.", model=_looping_model())

    out = await orchestration._decline_continue(parked["item_id"])
    assert out["declined"] is True
    assert await repo.session_status(parked["session_id"]) == "FAILED"
    # The claim is the lock, so a continue racing the decline finds nothing.
    assert await orchestration.continue_session(parked["item_id"]) is None


async def test_a_continue_item_needs_a_verdict_and_dispatches_on_it(monkeypatch):
    """`answer_item` is the one door: a continuation is a yes/no (no answer text
    required), and the verdict picks which driver runs."""
    monkeypatch.setattr(settings, "run_request_limit", 3)
    parked = await run_task("jira-expert", "Loop forever.", model=_looping_model())
    item_id = parked["item_id"]

    with pytest.raises(ValueError, match="approved.*declined"):
        await orchestration.answer_item(item_id, "", None)

    seen: list[str] = []

    async def _fake_continue(iid: str, model=None):
        seen.append(iid)

    monkeypatch.setattr(orchestration, "continue_session", _fake_continue)
    result = await orchestration.answer_item(item_id, "", "approved")
    assert result["verdict"] == "approved"
    await asyncio.sleep(0)  # let the background dispatch run
    assert seen == [item_id]

    # The verdict is recorded BEFORE the run it authorises.
    answered = [e for e in await repo.list_events_of_kinds(["orchestration.item_answered"])
                if e["ref_id"] == item_id]
    assert len(answered) == 1 and answered[0]["payload"]["verdict"] == "approved"

    await repo.mark_session_failed(parked["session_id"])  # tidy the parked row


async def test_a_non_continuable_run_still_fails(monkeypatch):
    """Parity: `continuable` defaults False, so every path that did not ask for
    this behaviour keeps the FAILED session it has always produced."""
    from pydantic_ai.exceptions import UsageLimitExceeded

    from central_command.runtime import run as run_mod
    from central_command.runtime.agent import build_agent_for
    from central_command.runtime.deps import TriageDeps

    monkeypatch.setattr(settings, "run_request_limit", 3)
    model = _looping_model()
    agent = await build_agent_for("jira-expert", model=model)
    session_id = "sess_parity_" + uuid.uuid4().hex[:8]

    with pytest.raises(UsageLimitExceeded):
        await run_mod._run_live(
            agent, "Loop forever.", model=model,
            deps=TriageDeps(agent_id="jira-expert", session_id=session_id),
            agent_id="jira-expert", session_id=session_id,
        )
    assert await repo.session_status(session_id) == "FAILED"


# --- slice 2: converse ---------------------------------------------------------


async def test_a_conversation_turn_parks_and_the_composer_says_why(monkeypatch):
    """A chat turn that spends its window parks with the lane READABLE and the
    message box replaced by an explanation. `turn_in_progress` would be a lie
    the operator waits on forever (nothing is running) and `failed` would be one
    they act on wrongly (the work is intact)."""
    from central_command.api import nerve_gateway
    from central_command.runtime import converse

    monkeypatch.setattr(settings, "run_request_limit", 3)

    out = await converse.start_conversation(
        "jira-expert", message="Loop forever.", model=_looping_model()
    )
    session_id = out["session_id"]
    assert out["window_exhausted"] is True and out["awaiting"] == "continue"
    assert await repo.session_status(session_id) == "AWAITING_CONTINUE"

    rs = await repo.get_session_run_state(session_id)
    assert rs["messages"][-1]["kind"] == "request", "the unsent tail must persist"
    assert rs["pending_continue"]["after"] == "converse"
    assert rs["pending_continue"]["task_id"] is None

    item = await repo.get_operator_item(out["item_id"])
    assert item["kind"] == "continue" and item["status"] == "OPEN"

    # The rule, and the surface that must agree with it.
    code, _msg = converse.why_not_sendable(await repo.get_session(session_id))
    assert code == "awaiting_continue"
    assert code in nerve_gateway._COMPOSER_DISABLING
    composer = await nerve_gateway._composer_state(f"agent:jira-expert:{session_id}")
    assert composer["enabled"] is False and composer["code"] == "awaiting_continue"
    assert "Decisions Inbox" in composer["message"]

    # The transcript still renders: the operator sees the turn stop mid-work,
    # which is the honest picture — not an empty lane and not a fake reply.
    chat = nerve_gateway._transcript_to_chat_messages(rs)
    assert any(m["role"] == "user" for m in chat)
    assert chat[-1]["role"] == "toolResult"


async def test_continuing_a_conversation_lands_the_turn(monkeypatch):
    """The granted window re-enters the SAME lane and lands through
    `converse._land_turn` — the tail an uninterrupted turn uses."""
    from central_command.api import nerve_gateway
    from central_command.runtime import converse

    monkeypatch.setattr(settings, "run_request_limit", 3)
    model = _looping_model(stop_after=3)
    parked = await converse.start_conversation(
        "jira-expert", message="Loop, then answer.", model=model
    )
    session_id = parked["session_id"]

    monkeypatch.setattr(settings, "run_request_limit", 25)
    out = await orchestration.continue_session(parked["item_id"], model=model)

    assert out["continued"] is True and out["window"] == 2
    assert out["awaiting"] == "operator"
    assert "done after the extra window" in out["reply"]
    assert out["session_id"] == session_id
    assert await repo.session_status(session_id) == "AWAITING_OPERATOR"

    # The composer is open again — the lane is a normal conversation once more.
    composer = await nerve_gateway._composer_state(f"agent:jira-expert:{session_id}")
    assert composer["enabled"] is True

    await converse.close_session(
        session_id, agent_id="jira-expert", reason="operator", actor="test",
    )


async def test_declining_a_conversation_window_keeps_the_transcript(monkeypatch):
    """Terminal, but never destructive: sessions are never destroyed (M15), so a
    declined turn keeps every message it produced."""
    from central_command.runtime import converse

    monkeypatch.setattr(settings, "run_request_limit", 3)
    parked = await converse.start_conversation(
        "jira-expert", message="Loop forever.", model=_looping_model()
    )
    session_id = parked["session_id"]
    before = len((await repo.get_session_run_state(session_id))["messages"])

    out = await orchestration._decline_continue(parked["item_id"])
    assert out["declined"] is True
    assert await repo.session_status(session_id) == "FAILED"
    after = await repo.get_session_run_state(session_id)
    assert len(after["messages"]) == before
    assert converse.why_not_sendable(await repo.get_session(session_id))[0] == "failed"


# --- slice 2: the orchestrator loop --------------------------------------------


def _looping_project_model(stop_after: int | None = None) -> FunctionModel:
    """An orchestrator that keeps calling `record_progress` (an UNGATED tool, so
    the round never ends) — the loop `run_request_limit` exists to break."""
    calls = {"n": 0}

    def _respond(messages, info) -> ModelResponse:
        calls["n"] += 1
        if stop_after is not None and calls["n"] > stop_after:
            return ModelResponse(parts=[TextPart(
                content="PROJECT COMPLETE after the extra window")])
        return ModelResponse(parts=[ToolCallPart(
            tool_name="record_progress",
            args={"made_progress": True, "in_a_loop": False,
                  "next_step": "keep thinking"},
        )])

    return FunctionModel(_respond)


async def _project_task(instructions: str = "Demo project: think a lot.") -> dict:
    task_id = "task_" + uuid.uuid4().hex[:12]
    await repo.create_task(task_id, "Window demo", instructions, "orchestrator")
    return await repo.get_task(task_id)


async def test_an_orchestrator_round_parks_without_failing_the_project(monkeypatch):
    """The loop's own state (round/stall/plan counters) survives in the marker —
    a continuation that lost them would restart the round count and re-arm every
    backstop. And the project task is NOT touched: like a stall park, it stays
    IN_PROGRESS waiting on a human."""
    monkeypatch.setattr(settings, "run_request_limit", 3)
    task = await _project_task()

    out = await orchestration.run_project(task, model=_looping_project_model())

    assert out["parked"] is True and out["window_exhausted"] is True
    session_id = out["session_id"]
    assert await repo.session_status(session_id) == "AWAITING_CONTINUE"
    assert (await repo.get_task(task["id"]))["status"] == "IN_PROGRESS"

    rs = await repo.get_session_run_state(session_id)
    assert rs["messages"][-1]["kind"] == "request"
    marker = rs["pending_continue"]
    assert marker["after"] == "orchestrator"
    assert marker["state"]["task_id"] == task["id"]
    # The item hangs off the project even though the loop passes no task_id.
    item = await repo.get_operator_item(out["item_id"])
    assert item["task_id"] == task["id"] and item["kind"] == "continue"


async def test_continuing_an_orchestrator_round_lands_through_process(monkeypatch):
    """The continuation re-enters `_process` — the round processor — so the
    project completes exactly as an uninterrupted round would have."""
    monkeypatch.setattr(settings, "run_request_limit", 3)
    model = _looping_project_model(stop_after=3)
    task = await _project_task()
    parked = await orchestration.run_project(task, model=model)

    monkeypatch.setattr(settings, "run_request_limit", 25)
    out = await orchestration.continue_session(parked["item_id"], model=model)

    assert out["continued"] is True and out["window"] == 2
    assert out["done"] is True
    assert "PROJECT COMPLETE" in out["output"]
    assert await repo.session_status(parked["session_id"]) == "DONE"
    assert (await repo.get_task(task["id"]))["status"] == "DONE"


async def test_an_orchestrator_second_window_parks_again(monkeypatch):
    """Re-parking on every path, with the loop state carried forward."""
    monkeypatch.setattr(settings, "run_request_limit", 3)
    model = _looping_project_model()
    task = await _project_task()
    parked = await orchestration.run_project(task, model=model)

    again = await orchestration.continue_session(parked["item_id"], model=model)
    assert again["window_exhausted"] is True and again["window"] == 2
    assert again["item_id"] != parked["item_id"]
    rs = await repo.get_session_run_state(parked["session_id"])
    assert rs["pending_continue"]["windows"] == 2
    assert rs["pending_continue"]["state"]["task_id"] == task["id"]

    # Declining a project window fails the project honestly, not silently.
    await orchestration._decline_continue(again["item_id"])
    assert (await repo.get_task(task["id"]))["status"] == "FAILED"


async def test_the_marker_after_picks_the_landing_tail(monkeypatch):
    """The slice-1 assumption was "always task". Assert the dispatch itself:
    each `after` re-enters ITS tail and no other — a mis-dispatch would land a
    conversation as a task (marking the wrong rows) and be invisible otherwise.
    """
    from central_command.runtime import converse

    monkeypatch.setattr(settings, "run_request_limit", 3)
    landed: list[str] = []

    async def _fake_land(result, **kw):
        landed.append("task")
        return {"deferred": False, "output": "x", "session_id": kw["session_id"]}

    async def _fake_land_turn(session_id, agent_id, result):
        landed.append("converse")
        return {"session_id": session_id, "reply": "x", "awaiting": "operator"}

    from central_command.runtime import run as run_mod

    monkeypatch.setattr(run_mod, "_land", _fake_land)
    monkeypatch.setattr(converse, "_land_turn", _fake_land_turn)

    task_parked = await run_task(
        "jira-expert", "Loop forever.", model=_looping_model())
    chat_parked = await converse.start_conversation(
        "jira-expert", message="Loop forever.", model=_looping_model())

    monkeypatch.setattr(settings, "run_request_limit", 25)
    await orchestration.continue_session(
        task_parked["item_id"], model=_looping_model(stop_after=0))
    assert landed == ["task"]
    await orchestration.continue_session(
        chat_parked["item_id"], model=_looping_model(stop_after=0))
    assert landed == ["task", "converse"]


async def test_the_decisions_payload_carries_the_new_kind():
    """The cockpit hand-declares its wire shapes, so a `kind` the gateway never
    sends is invisible to tsc AND to every frontend test — the item would simply
    never render. This asserts the BACKEND wire, like
    `test_composer_state_carries_closed_reason_to_the_cockpit`."""
    from central_command.api import nerve_gateway

    suffix = uuid.uuid4().hex[:8]
    session_id, item_id = f"sess_wire_{suffix}", f"item_wire_{suffix}"
    await repo.create_running_session(session_id, "jira-expert")
    await repo.create_operator_item(
        item_id, "continue", session_id, "jira-expert", "another window?"
    )
    try:
        payload = await nerve_gateway._decisions_list()
        mine = next(i for i in payload["operatorItems"] if i["id"] == item_id)
        assert mine["kind"] == "continue"
        assert mine["body"] == "another window?"
        assert mine["status"] == "OPEN"
    finally:
        conn = await repo._conn()
        try:
            await conn.execute("delete from operator_item where id = $1", item_id)
            await conn.execute("delete from session where id = $1", session_id)
        finally:
            await conn.close()
