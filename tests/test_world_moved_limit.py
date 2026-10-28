"""The world-moved redraft chain stops at the setting (epic-world-changed-redraft,
story 3).

The properties: an original and `world_moved_redraft_limit` redrafts each
failing on a stale version produce exactly that many linked redrafts, and the
next failure is terminal and loud — FAILED with `chain_exhausted`, one
`proposal.redraft_limit_reached`, the drafter NOT resumed, the stored history
repaired (the pending propose call answered with the terminal report, no model
run), the session resting open flagged `exec_failed`, the task FAILED naming the
limit. A conversation lane stays sendable over the repaired history. Any other
semantic failure keeps its resume and never counts; a Reject keeps the count.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from central_command.config import Settings, settings
from central_command.db import repo
from central_command.gateway import executor, gateway
from central_command.runtime import converse
from central_command.runtime.durable import load_messages, unanswered_calls
from tests.test_world_moved import (  # noqa: F401 — fixtures
    AGENT,
    _confluence_answers,
    _event,
    _page_update,
    confluence_grant,
    pinned,
)

LIVE_VERSION = 99


class _Persistent:
    """Proposes on its first turn and again on every propose return that is not
    the terminal report; a return naming the redraft limit gets a text reply.
    Records each call's propose returns, so a test reads exactly what a resumed
    run — or an operator's next turn — was handed."""

    def __init__(self, make, cap: int = 12):
        self.make, self.cap = make, cap
        self.calls: list[list[str]] = []
        self.model = FunctionModel(self._respond)

    def _respond(self, messages, info: AgentInfo) -> ModelResponse:
        parts = [p for m in messages for p in getattr(m, "parts", [])]
        made = sum(isinstance(p, ToolCallPart) and p.tool_name == "propose_action"
                   for p in parts)
        last = messages[-1]
        fresh = [str(p.content) for p in (last.parts if isinstance(last, ModelRequest) else [])
                 if isinstance(p, ToolReturnPart | RetryPromptPart)
                 and p.tool_name == "propose_action"]
        self.calls.append(fresh)
        if any("redraft limit" in r for r in fresh):
            return ModelResponse(parts=[TextPart(content="Understood — it is yours.")])
        if made == 0 or (fresh and made < self.cap):
            return ModelResponse(parts=[ToolCallPart(
                tool_name="propose_action",
                args={"proposal": self.make(made)})])
        return ModelResponse(parts=[TextPart(content="Closing out.")])


def _stale_page(made: int) -> dict:
    return _page_update(made + 4)


async def _task_session(monkeypatch, drafter: _Persistent) -> tuple[dict, str]:
    from central_command.api import routes

    async def fixed_model(**_kw):
        return drafter.model

    monkeypatch.setattr(routes, "resolve_model", fixed_model)
    task_id = f"task_wl_{uuid.uuid4().hex[:10]}"
    await repo.create_task(task_id, "edit the roadmap", "edit the page", AGENT,
                           created_by="operator")
    run = await routes._run_assigned_task(await repo.get_task(task_id))
    return await repo.load_proposal(run["proposal_id"]), task_id


async def _chain(session_id: str, first_id: str, model, failures: int) -> list[dict]:
    """Approve `failures` proposals in a row, each following the last's redraft;
    returns each approval's result."""
    outs, pid = [], first_id
    for _ in range(failures):
        out = await gateway.approve_and_execute(session_id, pid, model=model)
        outs.append(out)
        pid = out.get("redraft_proposal_id")
        if pid is None:
            break
    return outs


async def _redrafts_of(proposal_id: str) -> list[dict]:
    conn = await repo._conn()
    try:
        rows = await conn.fetch(
            "select id from proposal where redraft->>'of' = $1", proposal_id)
    finally:
        await conn.close()
    return [await repo.load_proposal(r["id"]) for r in rows]


async def _count(kind: str, ref_id: str) -> int:
    return sum(e["kind"] == kind for e in await repo.list_events(ref_id=ref_id))


def _history_ends_answered(run_state: dict) -> list:
    messages = load_messages(run_state)
    assert unanswered_calls(messages) == []
    return messages


def test_the_setting_defaults_to_three_and_refuses_a_negative(monkeypatch):
    monkeypatch.delenv("CC_WORLD_MOVED_REDRAFT_LIMIT", raising=False)
    assert Settings(_env_file=None).world_moved_redraft_limit == 3
    monkeypatch.setenv("CC_WORLD_MOVED_REDRAFT_LIMIT", "0")
    assert Settings(_env_file=None).world_moved_redraft_limit == 0
    monkeypatch.setenv("CC_WORLD_MOVED_REDRAFT_LIMIT", "-1")
    with pytest.raises(ValueError):
        Settings(_env_file=None)


@pytest.mark.usefixtures("confluence_grant")
async def test_the_fourth_world_moved_failure_is_terminal_and_loud(monkeypatch):
    drafter = _Persistent(_stale_page)
    row, task_id = await _task_session(monkeypatch, drafter)
    _confluence_answers(monkeypatch, version=LIVE_VERSION)
    sid = row["session_id"]

    outs = await _chain(sid, row["id"], drafter.model, 4)

    # Three redrafts, one root, counts 1, 2, 3 — each parked for the operator.
    ids = [row["id"]] + [o["redraft_proposal_id"] for o in outs[:3]]
    links = [(await repo.load_proposal(i))["redraft"] for i in ids[1:]]
    assert [l["world_moved_count"] for l in links] == [1, 2, 3]
    assert {l["root"] for l in links} == {row["id"]}
    assert [l["of"] for l in links] == ids[:3]
    assert all(l["cause"] == "world_moved" for l in links)

    # The fourth failure: FAILED, terminal, no fourth redraft, no resumed run.
    last = ids[3]
    assert (await repo.load_proposal(last))["status"] == "FAILED"
    assert "redraft_proposal_id" not in outs[3] and outs[3]["chain_exhausted"] is True
    assert await _redrafts_of(last) == []
    assert len(drafter.calls) == 4  # the draft plus three resumes; none for the fourth

    failed = (await _event("proposal.failed", last))["payload"]
    assert failed["chain_exhausted"] is True and failed["world_moved"]["actual"] == LIVE_VERSION
    # Below the limit the payload carries no such field.
    assert "chain_exhausted" not in (await _event("proposal.failed", ids[0]))["payload"]
    events = await repo.list_events(ref_id=last)
    kinds = [e["kind"] for e in events]
    assert kinds.count("proposal.redraft_limit_reached") == 1
    assert kinds.index("proposal.failed") < kinds.index("proposal.redraft_limit_reached")
    assert (await _event("proposal.redraft_limit_reached", last))["payload"] == {
        "root": row["id"], "count": 3, "limit": 3, "proposal_id": last}
    for earlier in ids[:3]:
        assert await _count("proposal.redraft_limit_reached", earlier) == 0
    assert await _count("session.resume_retried", sid) == 0

    # The session rests open and flagged, over a history with no dangling call.
    session = await repo.get_session(sid)
    assert session["status"] == "AWAITING_OPERATOR"
    assert "redraft limit 3" in session["exec_failure"]
    messages = _history_ends_answered(await repo.get_session_run_state(sid))
    tail = messages[-1].parts[-1]
    assert isinstance(tail, ToolReturnPart) and tail.tool_name == "propose_action"
    assert "redraft limit 3 reached" in tail.content
    assert "the operator takes it from here" in tail.content
    assert "The world moved 4 times" in tail.content
    assert await _count("session.exec_failed", sid) == 1

    # The task is FAILED naming the limit.
    task = await repo.get_task(task_id)
    assert task["status"] == "FAILED" and "redraft limit 3" in task["outcome"]
    # Nothing is armed: the sweep has nothing to resume.
    assert "pending_resume" not in (await repo.get_session_run_state(sid)) or (
        (await repo.get_session_run_state(sid))["pending_resume"].get("proposal_id")
        != last)


@pytest.mark.usefixtures("confluence_grant")
async def test_a_limit_of_zero_makes_the_first_world_moved_failure_terminal(monkeypatch):
    monkeypatch.setattr(settings, "world_moved_redraft_limit", 0)
    drafter = _Persistent(_stale_page)
    row, task_id = await _task_session(monkeypatch, drafter)
    _confluence_answers(monkeypatch, version=LIVE_VERSION)

    out = await gateway.approve_and_execute(row["session_id"], row["id"], model=drafter.model)

    assert out["chain_exhausted"] is True and "redraft_proposal_id" not in out
    assert (await repo.load_proposal(row["id"]))["status"] == "FAILED"
    assert (await _event("proposal.failed", row["id"]))["payload"]["chain_exhausted"] is True
    assert (await _event("proposal.redraft_limit_reached", row["id"]))["payload"] == {
        "root": row["id"], "count": 0, "limit": 0, "proposal_id": row["id"]}
    assert len(drafter.calls) == 1
    assert "redraft limit 0" in (await repo.get_session(row["session_id"]))["exec_failure"]
    assert (await repo.get_task(task_id))["status"] == "FAILED"
    _history_ends_answered(await repo.get_session_run_state(row["session_id"]))


@pytest.mark.usefixtures("confluence_grant")
async def test_a_limit_below_the_chain_is_read_live(monkeypatch):
    monkeypatch.setattr(settings, "world_moved_redraft_limit", 1)
    drafter = _Persistent(_stale_page)
    row, _ = await _task_session(monkeypatch, drafter)
    _confluence_answers(monkeypatch, version=LIVE_VERSION)
    outs = await _chain(row["session_id"], row["id"], drafter.model, 3)
    assert len(outs) == 2 and outs[1]["chain_exhausted"] is True
    first_redraft = await repo.load_proposal(outs[0]["redraft_proposal_id"])
    assert first_redraft["redraft"]["world_moved_count"] == 1
    assert (await _event("proposal.redraft_limit_reached",
                         outs[0]["redraft_proposal_id"]))["payload"]["count"] == 1


@pytest.mark.parametrize("limit", [3, 0])
async def test_other_semantic_failures_resume_every_time_and_never_count(monkeypatch, limit):
    from central_command.runtime.run import ingest_and_propose
    from central_command.runtime.spike_model import _PROPOSAL

    monkeypatch.setattr(settings, "world_moved_redraft_limit", limit)

    async def broken(issue_key, due_date):
        raise RuntimeError("400 due date is not a valid date")

    monkeypatch.setattr(executor.jira, "set_due_date", broken)
    drafter = _Persistent(lambda made: {**_PROPOSAL, "intent": f"Redraft {made}"})
    run = await ingest_and_propose("Dana: DEMO-1 slipped, due Aug 3.", model=drafter.model)

    outs = await _chain(run["session_id"], run["proposal_id"], drafter.model, 4)

    assert len(outs) == 4 and all("redraft_proposal_id" in o for o in outs)
    assert len(drafter.calls) == 5  # the draft plus one resume per failure
    pid = run["proposal_id"]
    for out in outs:
        payload = (await _event("proposal.failed", pid))["payload"]
        assert payload["world_moved"] is None and "chain_exhausted" not in payload
        assert await _count("proposal.redraft_limit_reached", pid) == 0
        pid = out["redraft_proposal_id"]
        link = (await repo.load_proposal(pid))["redraft"]
        assert link["cause"] == "failed" and link["world_moved_count"] == 0


@pytest.mark.usefixtures("confluence_grant")
async def test_a_reject_in_the_middle_keeps_the_root_and_the_count(monkeypatch):
    drafter = _Persistent(_stale_page)
    row, _ = await _task_session(monkeypatch, drafter)
    _confluence_answers(monkeypatch, version=LIVE_VERSION)
    sid = row["session_id"]

    first = await gateway.approve_and_execute(sid, row["id"], model=drafter.model)
    moved = first["redraft_proposal_id"]  # count 1
    rejected = await gateway.reject_with_feedback(sid, moved, "not that one", model=drafter.model)
    after_reject = await repo.load_proposal(rejected["redraft_proposal_id"])
    assert after_reject["redraft"] == {
        "of": moved, "root": row["id"], "cause": "rejected", "world_moved_count": 1}

    second = await gateway.approve_and_execute(
        sid, rejected["redraft_proposal_id"], model=drafter.model)
    link = (await repo.load_proposal(second["redraft_proposal_id"]))["redraft"]
    assert link["root"] == row["id"] and link["world_moved_count"] == 2
    assert link["cause"] == "world_moved"


@pytest.mark.usefixtures("confluence_grant")
async def test_a_conversation_lane_at_the_limit_stays_sendable_over_a_repaired_history(
    monkeypatch,
):
    drafter = _Persistent(_stale_page)
    _confluence_answers(monkeypatch, version=LIVE_VERSION)
    out = await converse.start_conversation(
        AGENT, "update the roadmap page", model=drafter.model)
    sid, first = out["session_id"], out["proposal_id"]

    outs = await _chain(sid, first, drafter.model, 4)
    assert outs[3]["chain_exhausted"] is True and len(outs) == 4
    last = outs[2]["redraft_proposal_id"]
    assert (await repo.load_proposal(last))["status"] == "FAILED"
    assert (await repo.load_proposal(last))["redraft"]["world_moved_count"] == 3

    session = await repo.get_session(sid)
    assert session["status"] == "AWAITING_OPERATOR"
    assert "redraft limit 3" in session["exec_failure"]
    assert converse.why_not_sendable(session) is None  # sendable, unchanged
    messages = _history_ends_answered(await repo.get_session_run_state(sid))
    tail = messages[-1].parts[-1]
    assert isinstance(tail, ToolReturnPart) and "redraft limit 3 reached" in tail.content
    assert len(drafter.calls) == 4

    # The operator's next turn runs against that history and is answered; the
    # model saw the report as the propose call's return, never a dangling call.
    reply = await converse.conversation_turn(sid, "what happened?", model=drafter.model)
    assert reply["reply"] == "Understood — it is yours."
    assert any("redraft limit 3 reached" in r for r in drafter.calls[-1])
    assert (await repo.get_session(sid))["status"] == "AWAITING_OPERATOR"


async def test_a_sibling_deferred_in_the_same_response_is_answered_too():
    from central_command.runtime.durable import answer_pending_calls

    messages = [ModelResponse(parts=[
        ToolCallPart(tool_name="propose_action", args={}, tool_call_id="a"),
        ToolCallPart(tool_name="ask_operator", args={}, tool_call_id="b"),
    ])]
    answered = answer_pending_calls(messages, "a", "REPORT")
    assert unanswered_calls(answered) == []
    returns = {p.tool_call_id: p.content for p in answered[-1].parts}
    assert returns["a"] == "REPORT" and "NOT processed" in returns["b"]
    # An id the record does not hold is not this record's to answer.
    assert answer_pending_calls(messages, "zzz", "REPORT") is None
    assert answer_pending_calls(answered, "a", "REPORT") is None


def test_the_landing_is_written_after_the_event_with_nothing_armed():
    """A source guard on the shape the ticket states: at the limit the failure
    path lands without arming a park record (an armed marker would make the
    resume sweep run the drafter)."""
    import inspect

    src = inspect.getsource(gateway._record_execution_failure)
    limit_branch = src[src.index("if chain_exhausted:\n        # The drafter"):
                       src.index("if run_state is not None and run_state.get(\"tool_call_id\"):\n"
                                 "        # see the note")]
    assert "resume_park.arm" not in limit_branch
    assert "finish_decision_resume" in limit_branch
