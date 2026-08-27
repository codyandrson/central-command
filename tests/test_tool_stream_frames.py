"""Phase 2 tool visibility: the LIVE frames a running turn pushes.

The wire bite-mark applies here exactly as it did to `chat.history`: the
cockpit hand-declares `AgentToolStreamData` over an untyped payload, so a
frontend test that builds its own frame proves nothing about what the gateway
sends. These drive `_tool_stream_frames` against a fake run_state delta and
assert the exact dicts pushed — the shape `classifyStreamEvent` demands
(`event: 'agent'`, `stream: 'tool'`, `data.phase` + `data.name` +
`data.toolCallId` for a start, `data.toolCallId` for a result).
"""

from __future__ import annotations

import pytest

from central_command.api import nerve_gateway as ng


def _step(session_id: str = "sess_x", agent_id: str = "inbox-triage", messages: int = 1) -> dict:
    return {"kind": "session.step", "ref_id": session_id,
            "payload": {"agent_id": agent_id, "messages": messages}}


def _tool_call_state() -> dict:
    return {"messages": [
        {"kind": "request", "parts": [{"part_kind": "user-prompt", "content": "hi"}]},
        {"kind": "response", "parts": [{
            "part_kind": "tool-call", "tool_call_id": "call_1",
            "tool_name": "jira_search", "args": {"jql": "project = T1"},
        }]},
        {"kind": "request", "parts": [{
            "part_kind": "tool-return", "tool_call_id": "call_1",
            "tool_name": "jira_search", "content": "3 issues",
        }]},
    ]}


@pytest.fixture
def one_key(monkeypatch):
    """No DB: pin the sessionKey resolution to the session's own key."""
    async def keys(agent_id, session_id):
        return [f"agent:{agent_id}:{session_id}"]

    monkeypatch.setattr(ng, "_tool_stream_keys", keys)


@pytest.fixture
def run_state(monkeypatch):
    """`repo.get_session_run_state` -> whatever the test puts in the box."""
    box: dict = {"state": {}}

    async def get(session_id):
        return box["state"]

    monkeypatch.setattr(ng.repo, "get_session_run_state", get)
    return box


async def test_the_frame_shape_the_classifier_demands(one_key, run_state):
    cursors: dict = {}
    # First sight seeds the cursor from the event's own count and pushes
    # nothing — a socket joining mid-history must not replay old calls.
    assert await ng._tool_stream_frames(_step(messages=1), cursors) == []

    run_state["state"] = _tool_call_state()
    frames = await ng._tool_stream_frames(_step(messages=3), cursors)

    assert frames == [
        {"type": "event", "event": "agent", "payload": {
            "sessionKey": "agent:inbox-triage:sess_x",
            "stream": "tool",
            "data": {"phase": "start", "toolCallId": "call_1",
                     "name": "jira_search", "args": {"jql": "project = T1"}},
        }},
        {"type": "event", "event": "agent", "payload": {
            "sessionKey": "agent:inbox-triage:sess_x",
            "stream": "tool",
            "data": {"phase": "result", "toolCallId": "call_1"},
        }},
    ]


async def test_a_call_is_announced_once(one_key, run_state):
    cursors: dict = {}
    await ng._tool_stream_frames(_step(messages=1), cursors)
    run_state["state"] = _tool_call_state()
    assert len(await ng._tool_stream_frames(_step(messages=3), cursors)) == 2
    # Same state, another step: nothing new to say.
    assert await ng._tool_stream_frames(_step(messages=3), cursors) == []


async def test_a_landed_session_drops_its_cursor(one_key, run_state):
    cursors: dict = {}
    await ng._tool_stream_frames(_step(), cursors)
    assert "sess_x" in cursors
    await ng._tool_stream_frames(
        {"kind": "session.completed", "ref_id": "sess_x", "payload": {}}, cursors
    )
    assert cursors == {}


async def test_other_events_are_ignored(one_key, run_state):
    cursors: dict = {}
    assert await ng._tool_stream_frames(
        {"kind": "proposal.created", "ref_id": "prop_1", "payload": {}}, cursors
    ) == []
    assert cursors == {}


async def test_compaction_shrinking_the_window_does_not_index_past_the_end(
    one_key, run_state
):
    cursors = {"sess_x": {"at": 50, "keys": ["agent:a:sess_x"]}}
    run_state["state"] = {"messages": []}
    assert await ng._tool_stream_frames(_step(messages=0), cursors) == []
    assert cursors["sess_x"]["at"] == 0


async def test_string_args_are_parsed_so_the_frame_stays_json_shaped(one_key, run_state):
    cursors: dict = {}
    await ng._tool_stream_frames(_step(messages=0), cursors)
    run_state["state"] = {"messages": [{"kind": "response", "parts": [{
        "part_kind": "tool-call", "tool_call_id": "c2", "tool_name": "graph_search",
        "args": '{"query": "who is barbara"}',
    }]}]}
    frames = await ng._tool_stream_frames(_step(messages=1), cursors)
    assert frames[0]["payload"]["data"]["args"] == {"query": "who is barbara"}
