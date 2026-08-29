"""Conversational lane (Phase 3): a multi-turn conversation is a durable session
that rests in AWAITING_OPERATOR between the operator's turns, resumes with full
history, and — Option C — when the agent proposes mid-conversation the change
gates like any other and the conversation CONTINUES once it is decided.
"""

from __future__ import annotations

import asyncio

import pytest

from central_command.config import settings
from central_command.db import repo
from central_command.gateway import gateway
from central_command.runtime import converse
from central_command.runtime.spike_model import make_converse_model
from tests.conftest import needs_pg


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "executor_mode", "dry_run")
    monkeypatch.setattr(settings, "dispatch_approval_limit", 10_000)
    monkeypatch.setattr(settings, "auditor_enabled", False)


def _model():
    return make_converse_model()


@needs_pg
async def test_every_active_agent_is_chattable_and_retired_ones_are_not():
    """Chattability stopped being a per-agent property (The operator, 2026-07-25): the
    chat page is where you talk to the team, and talking changes nothing on its
    own. This test previously asserted the opposite — that only agents flagged
    `conversational` had a lane — so it is rewritten rather than deleted: the
    rule it guards is now "active roster or nothing".
    """
    from central_command.runtime.roster import conversational_agent_ids

    chattable = await conversational_agent_ids()
    # jira-expert was the old rule's counter-example; it must now be included.
    assert "jira-expert" in chattable
    assert "inbox-triage" in chattable

    with pytest.raises(ValueError, match="active roster"):
        await converse.start_conversation("no-such-agent", "hi", model=_model())

    agent_id = "conversetest-x"
    conn = await repo._conn()
    try:
        await conn.execute("delete from agent_grant where agent_id = $1", agent_id)
        await conn.execute("delete from guidance_version where agent_id = $1", agent_id)
        await conn.execute("delete from agent where id = $1", agent_id)
    finally:
        await conn.close()
    try:
        await repo.hire_agent(agent_id, "Converse fixture", "test fixture",
                              "You are a fixture.", ["graph-read"], template="analyst")
        assert agent_id in await conversational_agent_ids()
        assert await repo.retire_agent(agent_id, "test over")
        assert agent_id not in await conversational_agent_ids()
        with pytest.raises(ValueError, match="active roster"):
            await converse.start_conversation(agent_id, "hi", model=_model())
    finally:
        conn = await repo._conn()
        try:
            await conn.execute("delete from agent_grant where agent_id = $1", agent_id)
            await conn.execute("delete from guidance_version where agent_id = $1", agent_id)
            await conn.execute("delete from agent where id = $1", agent_id)
        finally:
            await conn.close()


@needs_pg
async def test_text_turn_rests_in_awaiting_operator():
    out = await converse.start_conversation(
        "litellm-manager", "what can you do?", model=_model()
    )
    assert out["awaiting"] == "operator"
    assert "reply" in out
    s = await repo.get_session(out["session_id"])
    assert s["mode"] == "conversation" and s["status"] == "AWAITING_OPERATOR"


@needs_pg
async def test_multi_turn_resumes_with_history():
    out = await converse.start_conversation("litellm-manager", "hello", model=_model())
    sid = out["session_id"]
    # A second message resumes the SAME session with its history and replies.
    out2 = await converse.conversation_turn(sid, "still there?", model=_model())
    assert out2["session_id"] == sid and "reply" in out2
    assert await repo.session_status(sid) == "AWAITING_OPERATOR"


@needs_pg
async def test_propose_mid_conversation_parks_and_gates():
    out = await converse.start_conversation(
        "litellm-manager", "please add a cc-scratch model", model=_model()
    )
    assert out.get("proposed") is True
    row = await repo.load_proposal(out["proposal_id"])
    assert row["actions"][0]["capability"] == "litellm.add_model"
    assert row["status"] == "AWAITING_HUMAN"
    # The conversation is paused for the decision — a message is refused now.
    assert await repo.session_status(out["session_id"]) == "AWAITING_HUMAN"
    with pytest.raises(ValueError):
        await converse.conversation_turn(out["session_id"], "hurry up", model=_model())


@needs_pg
async def test_approve_continues_the_conversation():
    out = await converse.start_conversation(
        "litellm-manager", "add a cc-scratch model", model=_model()
    )
    assert out.get("proposed") is True
    sid, pid = out["session_id"], out["proposal_id"]

    result = await gateway.approve_and_execute(sid, pid, model=_model())
    assert result["ok"] is True
    # Option C: the session did NOT go DONE — it is back to the resting state.
    assert await repo.session_status(sid) == "AWAITING_OPERATOR"

    # And the operator can keep talking.
    out2 = await converse.conversation_turn(sid, "great, thanks", model=_model())
    assert "reply" in out2
    assert await repo.session_status(sid) == "AWAITING_OPERATOR"


@needs_pg
async def test_reject_continues_the_conversation():
    out = await converse.start_conversation(
        "litellm-manager", "add a cc-scratch model", model=_model()
    )
    sid, pid = out["session_id"], out["proposal_id"]

    # The spike closes out in text on the denial (doesn't redraft), so the
    # conversation continues rather than parking a new proposal.
    await gateway.reject_with_feedback(sid, pid, "not now", model=_model())
    assert await repo.session_status(sid) == "AWAITING_OPERATOR"
    assert await repo.load_proposal(pid) and (await repo.load_proposal(pid))["status"] == "REJECTED"


@needs_pg
async def test_approve_survives_a_resume_failure_without_orphaning(monkeypatch):
    """The 2026-07-22 incident: after a successful execution the resume threw and
    the session was left stuck AWAITING_HUMAN with an already-EXECUTED proposal.
    Now the execution record outranks the resume — the session finalizes."""
    out = await converse.start_conversation(
        "litellm-manager", "add a cc-scratch model", model=_model()
    )
    sid, pid = out["session_id"], out["proposal_id"]

    async def boom(*a, **k):
        raise RuntimeError("resume kaput")

    monkeypatch.setattr(gateway, "resume", boom)
    since = await repo.latest_event_id()
    result = await gateway.approve_and_execute(sid, pid, model=_model())

    assert result["ok"] is True  # the execution itself still succeeded
    assert (await repo.load_proposal(pid))["status"] == "EXECUTED"
    assert await repo.session_status(sid) == "DONE"  # NOT stuck AWAITING_HUMAN
    kinds = [e["kind"] for e in await repo.list_events(since_id=since)]
    assert "session.resume_failed" in kinds


@needs_pg
async def test_end_conversation_closes_it():
    out = await converse.start_conversation("litellm-manager", "hi", model=_model())
    await converse.end_conversation(out["session_id"])
    assert await repo.session_status(out["session_id"]) == "DONE"


@needs_pg
async def test_close_is_a_flip_never_a_delete():
    """Closing keeps the row AND the transcript (M15) — the session is
    archived, never gone — and closing again is a quiet no-op that does not
    re-emit conversation.ended."""
    out = await converse.start_conversation("litellm-manager", "hi", model=_model())
    sid = out["session_id"]
    since = await repo.latest_event_id()
    await converse.end_conversation(sid)

    session = await repo.get_session(sid)
    assert session is not None and session["status"] == "DONE"
    assert await repo.get_session_run_state(sid)  # transcript kept

    again = await converse.end_conversation(sid)  # idempotent
    assert again["status"] == "DONE"
    ended = [e for e in await repo.list_events(since_id=since)
             if e["kind"] == "conversation.ended" and e["ref_id"] == sid]
    assert len(ended) == 1


@needs_pg
async def test_closed_lane_leaves_the_alias_empty():
    """After a close, the "agent:<id>:main" alias resolves to NO session — the
    chat pane shows empty (obviously fresh), never the closed transcript. The
    closed session's own explicit key still serves its history."""
    from central_command.api.nerve_gateway import _resolve_history_session

    out = await converse.start_conversation("litellm-manager", "hi", model=_model())
    sid = out["session_id"]
    assert await _resolve_history_session("agent:litellm-manager:main") == sid

    await converse.end_conversation(sid)
    assert await _resolve_history_session("agent:litellm-manager:main") is None
    assert await _resolve_history_session(f"agent:litellm-manager:{sid}") == sid


@needs_pg
async def test_close_lever_on_a_root_key_closes_the_open_lane():
    """The panel's close on an agent ROOT row means "close the thing I'm
    looking at" — it closes the agent's current open conversation; with
    nothing open it refuses loudly (the root itself is a roster member,
    never closable)."""
    from fastapi import HTTPException

    from central_command.api.nerve_gateway import _close_conversation_for_key

    out = await converse.start_conversation("litellm-manager", "hi", model=_model())
    closed = await _close_conversation_for_key(
        "agent:litellm-manager:main", require_explicit=True
    )
    assert closed["session_id"] == out["session_id"]
    assert closed["status"] == "DONE"

    with pytest.raises(HTTPException, match="no open session"):
        await _close_conversation_for_key(
            "agent:litellm-manager:main", require_explicit=True
        )


@needs_pg
async def test_open_additional_session_keeps_the_current_one_open():
    """sessions.new opens a PARALLEL session — nothing already open closes,
    both stay individually continuable (multi-session per agent). Refused for
    agents without a conversational lane."""
    from fastapi import HTTPException

    from central_command.api.nerve_gateway import _dispatch

    first = await converse.start_conversation("litellm-manager", "hi", model=_model())
    out = await _dispatch("sessions.new", {"agentId": "litellm-manager"}, None)
    assert out["sessionId"] != first["session_id"]
    assert out["sessionKey"] == f"agent:litellm-manager:{out['sessionId']}"
    assert await repo.session_status(first["session_id"]) == "AWAITING_OPERATOR"
    assert await repo.session_status(out["sessionId"]) == "AWAITING_OPERATOR"

    # jira-expert used to be refused here (it lacked the `conversational`
    # flag); every ACTIVE agent is chattable now, so a parallel lane opens.
    expert = await _dispatch("sessions.new", {"agentId": "jira-expert"}, None)
    assert expert["sessionId"]
    assert await repo.session_status(expert["sessionId"]) == "AWAITING_OPERATOR"

    with pytest.raises(HTTPException, match="active roster"):
        await _dispatch("sessions.new", {"agentId": "no-such-agent"}, None)


@needs_pg
async def test_the_conversation_path_dispatches_on_the_deferred_tool():
    """B: not every deferral is a proposal.

    `extract_deferred` returns the FIRST deferred call whatever tool it is, and
    the conversation path used to hand it straight to `proposal_from_call` — so
    an `ask_operator` in a chat tried to validate a question as a Proposal
    (caught by the operator the first time he talked to the orchestrator).

    Tested at the dispatcher rather than end to end ON PURPOSE: fix C removes
    the orchestrator's project tools in chat, which deletes today's only way to
    reach this branch through a real turn. The guard stays because the next
    deferred tool anyone adds would otherwise re-open the same hole — and it
    fails honestly instead of mis-parsing.
    """
    from central_command.runtime.converse import _handle_non_proposal_deferral

    class _Call:
        def __init__(self, tool_name, args):
            self.tool_name = tool_name
            self.args = args

    out = await converse.start_conversation("jira-expert", "hello", model=_model())
    sid = out["session_id"]

    # ask_operator: the operator is right here, so the question IS the reply.
    asked = await _handle_non_proposal_deferral(
        sid, "jira-expert", _Call("ask_operator", {"question": "Do you want A or B?"})
    )
    assert asked["asked"] is True
    assert "A or B" in asked["reply"]
    assert "proposal_id" not in asked
    assert await repo.session_status(sid) == "AWAITING_OPERATOR"

    # A project-only tool: refused in words, never mis-parsed, never wedged.
    refused = await _handle_non_proposal_deferral(
        sid, "orchestrator", _Call("submit_plan", {"steps": ["a", "b"]})
    )
    assert refused["refused_tool"] == "submit_plan"
    assert "Tasks page" in refused["reply"]

    # A propose tool is NOT handled here — it falls through to the parker.
    assert await _handle_non_proposal_deferral(
        sid, "jira-expert", _Call("propose_action", {"proposal": {}})
    ) is None

    # Args arrive as a JSON string from some models; the question still reads.
    as_string = await _handle_non_proposal_deferral(
        sid, "jira-expert", _Call("ask_operator", '{"question": "stringified?"}')
    )
    assert "stringified?" in as_string["reply"]


async def test_the_orchestrator_swaps_project_tools_for_task_propose_in_chat():
    """C: in a conversation the orchestrator loses `orchestrate` (its tools are
    deferred and driven by a project driver that does not exist here) and gains
    `task-propose`.

    The reason task-propose is withheld from its PROJECT runs does not apply in
    chat: the objection was two differently-governed paths to the same act, and
    a conversation has no `assign_work` at all.
    """
    from central_command.runtime.converse import _conversational_packs

    chat_packs = _conversational_packs("orchestrator", ("orchestrate", "graph-read"))
    assert "orchestrate" not in chat_packs
    assert "task-propose" in chat_packs
    assert "graph-read" in chat_packs, "reads are the point of talking to it"

    # Every other agent is untouched — this is one agent's quirk, not a rule.
    same = ("jira-read", "jira-propose", "task-propose")
    assert _conversational_packs("jira-expert", same) == same


@needs_pg
async def test_close_refuses_while_a_proposal_is_pending():
    """A session holding an undecided proposal cannot be closed out from
    under it — the gateway's resume path needs the paused session."""
    out = await converse.start_conversation(
        "litellm-manager", "please add a cc-scratch model", model=_model()
    )
    assert out.get("proposed") is True
    with pytest.raises(ValueError, match="pending proposal"):
        await converse.end_conversation(out["session_id"])
    # Still decidable: the pause survived the refused close.
    assert await repo.session_status(out["session_id"]) == "AWAITING_HUMAN"


# --- a decision resume that ENDS by concluding the lane (seam 2) ---------------
# Conclude-before-propose is the doctrine, but the reverse order must not be
# lossy: the approve path resumes the agent, and if that turn defers
# `conclude_discussion` the session used to be rested AWAITING_OPERATOR and the
# conclusion silently dropped — the agent believing it concluded, the lane
# saying otherwise.

SUMMARY = "please add a cc-scratch model"


def _propose_then_conclude():
    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
    from pydantic_ai.models.function import AgentInfo, FunctionModel

    from central_command.runtime.spike_model import _CONVERSE_PROPOSAL

    def _respond(messages, info: AgentInfo) -> ModelResponse:
        called = {
            p.tool_name
            for m in messages
            for p in getattr(m, "parts", [])
            if isinstance(p, ToolCallPart)
        }
        if "propose_litellm_change" not in called:
            return ModelResponse(parts=[
                ToolCallPart(tool_name="propose_litellm_change",
                             args={"proposal": _CONVERSE_PROPOSAL}),
            ])
        if "conclude_discussion" not in called:
            return ModelResponse(parts=[
                ToolCallPart(tool_name="conclude_discussion",
                             args={"summary": SUMMARY}),
            ])
        return ModelResponse(parts=[TextPart(content="Closed out.")])

    return FunctionModel(_respond)


async def _link_as_discussion(session_id: str, agent_id: str) -> dict:
    """Make an existing conversation the lane of a tier-3 question, the way
    `_open_discussion` does — one paused task session, one OPEN item, linked."""
    import uuid

    suffix = uuid.uuid4().hex[:8]
    ids = {"item_id": f"conversetest-item-{suffix}",
           "task_session": f"conversetest-task-{suffix}"}
    await repo.create_running_session(ids["task_session"], agent_id)
    await repo.create_operator_item(
        ids["item_id"], "question", ids["task_session"], agent_id, "Which model?",
    )
    await repo.link_operator_item_discussion(ids["item_id"], session_id)
    return ids


@pytest.fixture()
def no_task_resume(monkeypatch):
    """Concluding schedules a full agent turn on the blocked task; these tests
    are about the decision path, so the resume is recorded, not run."""
    from central_command.api import orchestration

    resumed: list[tuple[str, str]] = []

    async def _fake_resume(item_id, answer):
        resumed.append((item_id, answer))

    monkeypatch.setattr(orchestration, "_resume_task_question", _fake_resume)
    return resumed


@needs_pg
async def test_approving_a_proposal_made_in_a_lane_can_end_in_a_conclusion(
    no_task_resume,
):
    model = _propose_then_conclude()
    out = await converse.start_conversation(
        "litellm-manager", "please add a cc-scratch model", model=model
    )
    sid, pid = out["session_id"], out["proposal_id"]
    ids = await _link_as_discussion(sid, "litellm-manager")

    since = await repo.latest_event_id()
    result = await gateway.approve_and_execute(sid, pid, model=model)

    # The decision stands whatever the resume did.
    assert result["ok"] is True
    assert (await repo.load_proposal(pid))["status"] == "EXECUTED"

    # …and the lane is TERMINAL concluded, not rested awaiting an operator who
    # has nothing left to say.
    session = await repo.get_session(sid)
    assert session["status"] == "DONE"
    assert session["closed_reason"] == "concluded"

    concluded = [e for e in await repo.list_events(since_id=since)
                 if e["kind"] == "agent.discussion_concluded"
                 and e["ref_id"] == ids["item_id"]]
    assert len(concluded) == 1
    assert concluded[0]["payload"]["summary"] == SUMMARY
    assert concluded[0]["payload"]["claim_matches_source"] is True

    assert (await repo.get_operator_item(ids["item_id"]))["status"] == "ANSWERED"
    await asyncio.sleep(0)
    assert no_task_resume == [(ids["item_id"], SUMMARY)]


@needs_pg
async def test_the_same_conclusion_in_an_ordinary_conversation_still_rests(
    no_task_resume,
):
    """Narrow by construction: `conclude_discussion` outside a linked lane has
    nothing to conclude, so the conversation keeps today's Option-C behaviour
    byte-for-byte."""
    model = _propose_then_conclude()
    out = await converse.start_conversation(
        "litellm-manager", "please add a cc-scratch model", model=model
    )
    sid, pid = out["session_id"], out["proposal_id"]

    await gateway.approve_and_execute(sid, pid, model=model)

    assert await repo.session_status(sid) == "AWAITING_OPERATOR"
    assert (await repo.get_session(sid))["closed_reason"] is None
    assert no_task_resume == []


@needs_pg
async def test_a_failing_conclusion_falls_back_to_resting_the_session(
    monkeypatch, no_task_resume
):
    """Containment: the executed record outranks the resume on every path here,
    so a conclusion that raises must leave the session resting, never orphaned
    between two states."""
    from central_command.runtime import questions

    async def boom(*a, **k):
        raise RuntimeError("conclude kaput")

    monkeypatch.setattr(questions, "conclude_discussion", boom)

    model = _propose_then_conclude()
    out = await converse.start_conversation(
        "litellm-manager", "please add a cc-scratch model", model=model
    )
    sid, pid = out["session_id"], out["proposal_id"]
    await _link_as_discussion(sid, "litellm-manager")

    since = await repo.latest_event_id()
    result = await gateway.approve_and_execute(sid, pid, model=model)

    assert result["ok"] is True
    assert (await repo.load_proposal(pid))["status"] == "EXECUTED"
    assert await repo.session_status(sid) == "AWAITING_OPERATOR"
    kinds = [e["kind"] for e in await repo.list_events(since_id=since)]
    assert "session.resume_failed" in kinds
