"""The chat composer tells the truth about what a send would do.

2026-07-25, the operator's first live `ask_operator`: `jira-expert` parked a task run
with a question, and the cockpit listed that PAUSED RUN in the sessions panel
with an enabled message box — which `_chat_send` refused with a 409 on every
attempt. Three related things (a blocked task, a question in the Decisions
Inbox, an untouchable transcript) with nothing on screen connecting them, and a
surface inviting input it would always reject.

The fix is one resolution, not two opinions: `_send_target` decides what a send
would do, `_chat_send` raises it and `chat.history` reports it as the composer
state. These tests hold that seam shut — a second, forked resolution inside
`_chat_send` fails the suite in the same commit that adds it.
"""

from __future__ import annotations

import inspect
import uuid

import pytest
from fastapi import HTTPException

from central_command.config import settings
from central_command.db import repo
from central_command.runtime.converse import why_not_sendable
from tests.conftest import needs_pg


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "executor_mode", "dry_run")


# ── the rule itself (offline — no session store needed) ────────────────────


def test_the_rule_names_every_state_the_operator_can_act_on():
    """`why_not_sendable` is THE statement of who may be sent a message."""
    assert why_not_sendable(
        {"id": "sess_1", "mode": "conversation", "status": "AWAITING_OPERATOR"}
    ) is None
    # A closed conversation reopens by simply being written to (M15: closing is
    # a status flip, and history stays) — so DONE is sendable, not blocked.
    assert why_not_sendable(
        {"id": "sess_1", "mode": "conversation", "status": "DONE"}
    ) is None

    assert why_not_sendable(
        {"id": "sess_2", "mode": "oneshot", "status": "AWAITING_HUMAN"}
    )[0] == "not_a_conversation"
    assert why_not_sendable(
        {"id": "sess_3", "mode": "conversation", "status": "AWAITING_HUMAN"}
    )[0] == "pending_proposal"
    assert why_not_sendable(
        {"id": "sess_4", "mode": "conversation", "status": "FAILED"}
    )[0] == "failed"
    assert why_not_sendable(
        {"id": "sess_5", "mode": "conversation", "status": "RUNNING"}
    )[0] == "turn_in_progress"
    # A conversation parked AWAITING_RESUME (outage equivalence, 2026-07-31) is
    # a turn the control plane still owes the agent — a retry will drive it. The
    # existing catch-all already tells that truth and no new refusal code (nor a
    # `_COMPOSER_DISABLING` entry) is needed; this pins it so a later status
    # rename cannot silently make a parked conversation look chattable.
    assert why_not_sendable(
        {"id": "sess_6", "mode": "conversation", "status": "AWAITING_RESUME"}
    )[0] == "turn_in_progress"


async def test_conversation_turn_enforces_exactly_that_rule(monkeypatch):
    """The runtime path and the cockpit path cannot drift: `conversation_turn`
    refuses precisely when the rule says so, using the rule's own words."""
    from central_command.runtime import converse

    for status in ("AWAITING_HUMAN", "FAILED", "RUNNING"):
        row = {"id": "sess_x", "agent_id": "a", "mode": "conversation",
               "status": status}

        async def fake_get(_sid, _row=row):
            return _row

        monkeypatch.setattr(repo, "get_session", fake_get)
        expected = why_not_sendable(row)[1]
        with pytest.raises(ValueError) as exc:
            await converse.conversation_turn("sess_x", "hello")
        assert str(exc.value) == expected


def test_chat_send_has_no_resolution_of_its_own():
    """A SOURCE WALK, not a behaviour check: `_chat_send` must decide nothing
    about which session it is talking to. It shipped once with its own copy of
    the rule that knew less than `conversation_turn` did — which is exactly how
    the composer came to disagree with the send. A count would pass forever
    while a seventh caller re-forked it; this fails on the fork.
    """
    from central_command.api import nerve_gateway

    src = inspect.getsource(nerve_gateway._chat_send)
    assert "_send_target(" in src, "the send must use the shared resolution"
    for forked in ("repo.get_session", "_latest_conversation",
                   "conversational_agent_ids", 'mode") != "conversation"'):
        assert forked not in src, (
            f"_chat_send re-derives session resolution ({forked!r}) instead of "
            "using _send_target — the composer would stop agreeing with it"
        )


async def test_a_mid_turn_conversation_keeps_its_message_box(monkeypatch):
    """The ONE deliberate divergence, recorded here so it stays deliberate.

    A running turn blocks a send, but the composer stays ENABLED: it is
    transient (the cockpit already shows it as the spinner + abort), and
    disabling the box mid-turn would discard what the operator is typing — the
    one thing the client is authoritative about (2026-07-25, "my inputs should
    never disappear").
    """
    from central_command.api import nerve_gateway

    row = {"id": "sess_mid", "agent_id": "a", "mode": "conversation",
           "status": "RUNNING"}

    async def fake_get(_sid):
        return row

    monkeypatch.setattr(repo, "get_session", fake_get)
    state = await nerve_gateway._composer_state("agent:a:sess_mid")
    assert state["enabled"] is True
    assert state["code"] == "turn_in_progress"


async def test_chat_history_carries_the_lanes_source_email(monkeypatch):
    """Same wire-shape defence as the sidebar (`_sessions_list`): chat.history
    must report the email a lane was spawned to handle, or ChatHeader has
    nothing to render its chip from — a field the gateway stops sending is
    invisible to tsc and to every frontend test."""
    from central_command.api import nerve_gateway

    email = {
        "id": "wi_1", "kind": "email", "session_id": "sess_email",
        "subject": "Reminder: Budget Ratification Meeting",
        "payload": {"from": "hoa@example.com", "text": "please attend"},
    }

    async def fake_resolve(_key):
        return "sess_email"

    async def fake_composer(_key):
        return {"enabled": True, "session": None}

    async def fake_run_state(_sid):
        return {"messages": []}

    captured: dict = {}

    async def fake_work_items(ids):
        captured["ids"] = list(ids)
        return {"sess_email": email}

    monkeypatch.setattr(nerve_gateway, "_resolve_history_session", fake_resolve)
    monkeypatch.setattr(nerve_gateway, "_composer_state", fake_composer)
    monkeypatch.setattr(repo, "get_session_run_state", fake_run_state)
    monkeypatch.setattr(repo, "work_items_for_sessions", fake_work_items)

    payload = await nerve_gateway._chat_history(
        {"sessionKey": "agent:inbox-triage:sess_email"}
    )
    assert captured["ids"] == ["sess_email"], (
        "must resolve keyed by the resolved session id"
    )
    assert payload["sourceEmail"] == email, (
        "the header cannot show an email the server never sends"
    )


# ── the live shape: a paused task run ──────────────────────────────────────


async def _fixture_paused_run() -> tuple[str, str, str]:
    """A task run parked on a question — the exact shape of the live incident."""
    sid = "sess_" + uuid.uuid4().hex[:12]
    tid = "task_" + uuid.uuid4().hex[:12]
    iid = "item_" + uuid.uuid4().hex[:12]
    await repo.create_running_session(sid, "jira-expert")
    await repo.create_task(tid, "Composer fixture task", "do a thing", "jira-expert")
    await repo.start_task(tid, sid)
    await repo.park_task_review(tid, sid)
    await repo.mark_session_awaiting_operator(sid, {"messages": []})
    await repo.create_operator_item(
        iid, "question", sid, "jira-expert",
        "Which epic would you like restructured?", task_id=tid,
    )
    return sid, tid, iid


async def _drop_fixture(sid: str, tid: str, iid: str) -> None:
    conn = await repo._conn()
    try:
        await conn.execute("delete from operator_item where id = $1", iid)
        await conn.execute("delete from task where id = $1", tid)
        await conn.execute("delete from session where id = $1", sid)
    finally:
        await conn.close()


@needs_pg
async def test_a_paused_task_run_is_read_only_and_names_what_holds_it():
    from central_command.api.nerve_gateway import _chat_history, _chat_send

    sid, tid, iid = await _fixture_paused_run()
    try:
        key = f"agent:jira-expert:{sid}"
        payload = await _chat_history({"sessionKey": key})
        composer = payload["composer"]

        assert composer["enabled"] is False
        assert composer["code"] == "not_a_conversation"
        # What it is, in words the operator can act on.
        assert composer["kind"] == "task run"
        assert "waiting on your answer" in composer["message"]
        # The task it belongs to, by TITLE — an id names nothing.
        assert composer["task"]["id"] == tid
        assert composer["task"]["title"] == "Composer fixture task"
        # The ask that is holding it, so the three screens connect.
        assert composer["blocking"]["itemId"] == iid
        assert composer["blocking"]["kind"] == "question"

        # …and the send agrees, word for word: `refusal` is the exact 409 the
        # operator would have got had they typed anyway.
        with pytest.raises(HTTPException) as exc:
            await _chat_send({"sessionKey": key, "message": "hello?"}, _noop_notify)
        assert exc.value.status_code == 409
        assert exc.value.detail == composer["refusal"]
    finally:
        await _drop_fixture(sid, tid, iid)


@needs_pg
async def test_the_read_only_run_still_renders_its_transcript():
    """Read-only is not hidden. The transcript is the record of what the agent
    did, and losing it would trade one lie for a blind spot (the open question
    on whether paused runs belong in the panel at all is the operator's, and this is
    the lean it is built on).
    """
    from central_command.api.nerve_gateway import _chat_history

    sid, tid, iid = await _fixture_paused_run()
    try:
        await repo.update_session_run_state(sid, {"messages": [{
            "kind": "request",
            "parts": [{"part_kind": "user-prompt", "content": "restructure the epic"}],
        }]})
        payload = await _chat_history({"sessionKey": f"agent:jira-expert:{sid}"})
        assert payload["composer"]["enabled"] is False
        assert [m["content"] for m in payload["messages"]] == ["restructure the epic"]
    finally:
        await _drop_fixture(sid, tid, iid)


@needs_pg
async def test_the_inbox_names_the_task_an_ask_blocks():
    """Item 11: the operator answering a question should be able to see which
    work it unblocks. The title is resolved in the SAME payload as the item, so
    it can never describe a different instant than the ask it labels.
    """
    from central_command.api.nerve_gateway import _decisions_list

    sid, tid, iid = await _fixture_paused_run()
    try:
        items = (await _decisions_list())["operatorItems"]
        mine = next(i for i in items if i["id"] == iid)
        assert mine["task_id"] == tid
        assert mine["task_title"] == "Composer fixture task"
    finally:
        await _drop_fixture(sid, tid, iid)


@needs_pg
async def test_a_lane_is_sendable_until_it_holds_an_undecided_proposal():
    """Two ends of the same rule, on rows this test creates (the dev database
    is shared — a seeded agent's lane may be anything by the time we look).

    An active agent with nothing open is SENDABLE: the message starts a fresh
    conversation, and disabling it would be the opposite failure — refusing
    input the server would happily have taken. The same lane holding an
    undecided proposal is not, and says which screen decides it.
    """
    from central_command.api.nerve_gateway import _composer_state

    agent_id = "composer-fixture-agent"
    await _drop_agent(agent_id)
    try:
        await repo.hire_agent(agent_id, "Composer fixture", "test fixture",
                              "You are a fixture.", ["graph-read"],
                              template="analyst")
        key = f"agent:{agent_id}:main"
        assert (await _composer_state(key))["enabled"] is True

        sid = "sess_" + uuid.uuid4().hex[:12]
        await repo.create_conversation_session(sid, agent_id)
        await repo.mark_session_awaiting_operator(sid, {"messages": []})
        assert (await _composer_state(key))["enabled"] is True

        await repo.save_paused_session(sid, agent_id, {"messages": []})
        state = await _composer_state(key)
        assert state["enabled"] is False
        assert state["code"] == "pending_proposal"
        assert "Decisions Inbox" in state["message"]
    finally:
        await _drop_agent(agent_id)


@needs_pg
async def test_an_unknown_agent_says_so_rather_than_offering_a_box():
    from central_command.api.nerve_gateway import _composer_state

    state = await _composer_state("agent:no-such-agent:main")
    assert state["enabled"] is False
    assert state["code"] == "no_lane"


async def _noop_notify(_payload: dict) -> None:
    return None


async def _drop_agent(agent_id: str) -> None:
    conn = await repo._conn()
    try:
        await conn.execute(
            "delete from session where agent_id = $1", agent_id)
        await conn.execute("delete from agent_grant where agent_id = $1", agent_id)
        await conn.execute("delete from guidance_version where agent_id = $1", agent_id)
        await conn.execute("delete from agent where id = $1", agent_id)
    finally:
        await conn.close()
