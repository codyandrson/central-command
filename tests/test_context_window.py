"""Context management, slice 2: the WORKING window (2026-09-10).

The record stays raw; `context.prepare_window` rewrites the OUTGOING request
only, step by step, each step an operator toggle. What is under test:

- each pure step does exactly its one thing and leaves the wire protocol valid
  (a tool return never loses the call it answers);
- the processor is a no-op in demo mode and with every toggle off, and a
  failure inside it sends the raw history rather than killing the run;
- the summary step spends its model call once, caches in `run_state`, and
  keeps messages[0] plus a tail that starts with a response;
- the settings round-trip through the routes with partial PUT semantics.

No DDL: `context_settings` is one `app_setting` row.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from central_command.api import routes
from central_command.config import settings
from central_command.db import repo
from central_command.runtime import context
from tests.conftest import needs_pg


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    context._windows.clear()
    context._sent.clear()
    yield
    context._windows.clear()
    context._sent.clear()


def _transcript(turns: int = 4, payload: int = 1000):
    """system+prompt, then `turns` of (response: thinking + tool call) /
    (request: tool return of `payload` chars), then a final text response."""
    msgs = [ModelRequest(parts=[SystemPromptPart("charter"), UserPromptPart("do the thing")])]
    for i in range(turns):
        msgs.append(ModelResponse(parts=[
            ThinkingPart(f"thinking {i} " + "t" * 500),
            ToolCallPart("read_thing", {"n": i}, tool_call_id=f"call_{i}"),
        ]))
        msgs.append(ModelRequest(parts=[
            ToolReturnPart("read_thing", "x" * payload, tool_call_id=f"call_{i}"),
        ]))
    msgs.append(ModelResponse(parts=[ThinkingPart("final thoughts"), TextPart("done")]))
    return msgs


def _pairs_ok(msgs) -> bool:
    """Every tool return answers a call in the response immediately before it."""
    for i, m in enumerate(msgs):
        if isinstance(m, ModelRequest):
            returns = [p.tool_call_id for p in m.parts if isinstance(p, ToolReturnPart)]
            if not returns:
                continue
            prev = msgs[i - 1] if i else None
            calls = {p.tool_call_id for p in getattr(prev, "parts", []) if isinstance(p, ToolCallPart)}
            if not set(returns) <= calls:
                return False
    return True


# --- the pure steps -----------------------------------------------------------


def test_drop_thinking_keeps_only_the_last_response_reasoning():
    out = context.drop_thinking(_transcript())
    thinking = [p for m in out if isinstance(m, ModelResponse) for p in m.parts if isinstance(p, ThinkingPart)]
    assert [p.content for p in thinking] == ["final thoughts"]
    assert len(out) == len(_transcript()), "responses that still carry a call are kept"
    assert _pairs_ok(out)


def test_drop_thinking_removes_a_response_left_empty():
    msgs = [
        ModelRequest(parts=[UserPromptPart("hi")]),
        ModelResponse(parts=[ThinkingPart("only thinking")]),
        ModelRequest(parts=[UserPromptPart("again")]),
        ModelResponse(parts=[TextPart("ok")]),
    ]
    out = context.drop_thinking(msgs)
    assert [type(m).__name__ for m in out] == ["ModelRequest", "ModelRequest", "ModelResponse"]


def test_clear_tool_results_protects_the_last_n_requests_and_keeps_the_call():
    msgs = _transcript(turns=4)
    out = context.clear_tool_results(msgs, keep_turns=2)
    returns = [p for m in out if isinstance(m, ModelRequest) for p in m.parts if isinstance(p, ToolReturnPart)]
    assert [r.content.startswith("[cleared from context") for r in returns] == [True, True, False, False]
    assert "read_thing" in returns[0].content and "1000 chars" in returns[0].content
    assert returns[0].tool_call_id == "call_0", "the placeholder still answers its call"
    assert _pairs_ok(out)
    # Small results are not worth a placeholder that is nearly as long.
    small = context.clear_tool_results(_transcript(turns=2, payload=50), keep_turns=0)
    assert all(not p.content.startswith("[cleared") for m in small if isinstance(m, ModelRequest)
               for p in m.parts if isinstance(p, ToolReturnPart))


def test_pressure_note_rides_the_outgoing_request_only():
    msgs = _transcript()[:-1]  # ends on a request, as an outgoing history does
    out = context.add_pressure_note(msgs, 0.71)
    assert isinstance(out[-1].parts[-1], UserPromptPart)
    assert "71%" in out[-1].parts[-1].content
    assert msgs[-1].parts[-1].__class__ is ToolReturnPart, "the input list was not mutated"
    # Nothing to attach to when the history ends on a response.
    ends_on_response = _transcript()
    assert context.add_pressure_note(ends_on_response, 0.9) is ends_on_response


def test_summary_boundary_lands_on_a_response_and_the_tail_pairs_up():
    msgs = _transcript(turns=6)
    k = context.summary_boundary(msgs)
    assert k is not None and isinstance(msgs[k], ModelResponse) and 2 <= k < len(msgs) - 1
    out = context.apply_summary(msgs, k, "what happened before")
    assert out[0] is msgs[0]
    assert isinstance(out[1], ModelRequest) and "what happened before" in out[1].parts[0].content
    assert isinstance(out[2], ModelResponse)
    assert _pairs_ok(out)
    assert context.summary_boundary(_transcript(turns=1)) is None, "too short to be worth it"


def test_render_for_summary_leaves_thinking_and_the_charter_out():
    text = context.render_for_summary(_transcript(turns=1))
    assert "charter" not in text and "thinking 0" not in text
    assert "called read_thing" in text and "read_thing returned" in text and "done" in text


# --- the processor ------------------------------------------------------------


def _ctx(session_id=None, model_name="cc-default"):
    return SimpleNamespace(
        deps=SimpleNamespace(session_id=session_id, agent_id="ctx-test-agent"),
        model=SimpleNamespace(model_name=model_name),
    )


def _pin(monkeypatch, cfg: dict, window: int = 100_000):
    async def load():
        return {**context.DEFAULTS, **cfg}
    monkeypatch.setattr(context, "load_settings", load)
    context._windows["cc-default"] = window


async def test_demo_mode_and_all_off_send_the_raw_history(monkeypatch):
    msgs = _transcript()
    monkeypatch.setattr(settings, "demo_mode", True)
    assert await context.prepare_window(_ctx(), msgs) is msgs

    monkeypatch.setattr(settings, "demo_mode", False)
    _pin(monkeypatch, {"drop_thinking": False, "clear_tool_results": False,
                       "pressure_warning": False, "output_headroom": False, "summarize": False})
    assert await context.prepare_window(_ctx(), msgs) == msgs


async def test_defaults_trim_and_warn_without_touching_the_input(monkeypatch):
    msgs = _transcript(turns=8, payload=4000)
    raw = context.estimate_messages(msgs)
    _pin(monkeypatch, {"summarize": False}, window=20_000)  # well over a 20k window
    out = await context.prepare_window(_ctx(session_id="sess_x"), msgs)

    # 5 of 8 tool payloads cleared (3 kept) and 8 of 9 thinking parts dropped.
    assert context.estimate_messages(out) < raw * 0.6
    assert context.estimate_messages(msgs) == raw, "the record is untouched"
    assert _pairs_ok(out)
    assert "sess_x" in context._sent
    # The note is on the outgoing request when the history ends on one.
    ending_on_request = msgs[:-1]
    out2 = await context.prepare_window(_ctx(session_id="sess_x"), ending_on_request)
    assert "[context notice]" in out2[-1].parts[-1].content


async def test_a_broken_step_sends_the_raw_history(monkeypatch):
    msgs = _transcript()
    _pin(monkeypatch, {})

    def boom(*a, **k):
        raise RuntimeError("no")
    monkeypatch.setattr(context, "drop_thinking", boom)
    assert await context.prepare_window(_ctx(), msgs) is msgs


@needs_pg
async def test_summary_spends_one_call_and_is_cached_in_run_state(monkeypatch):
    agent_id = "ctx-test-agent"
    await repo.upsert_agent(agent_id, "Context Test", "ctx", "")
    session_id = "sess_" + uuid.uuid4().hex[:12]
    await repo.create_running_session(session_id, agent_id)
    await repo.update_session_run_state(session_id, {"messages": []})

    calls = []

    async def fake_summarize(model, region):
        calls.append(len(region))
        return "SUMMARY-TEXT"
    monkeypatch.setattr(context, "summarize", fake_summarize)
    # Trims off so the only thing that can bring the window down is the summary.
    _pin(monkeypatch, {"drop_thinking": False, "clear_tool_results": False,
                       "output_headroom": False, "summarize_threshold": 0.5}, window=10_000)

    msgs = _transcript(turns=6, payload=2000)  # ~13k chars -> ~3.3k tokens... make it big
    msgs = _transcript(turns=6, payload=6000)  # ~40k chars -> ~10k tokens against a 10k window
    out = await context.prepare_window(_ctx(session_id=session_id), msgs)

    assert calls, "over the threshold, the summary step ran"
    assert any(isinstance(p, UserPromptPart) and "SUMMARY-TEXT" in p.content
               for p in out[1].parts)
    assert out[0] is msgs[0] and isinstance(out[2], ModelResponse) and _pairs_ok(out)
    rs = await repo.get_session_run_state(session_id)
    assert rs["context_summary"]["text"] == "SUMMARY-TEXT"
    assert rs["messages"] == [], "the processor never writes the transcript key"
    events = [e for e in await repo.list_events_of_kinds(["session.context_compacted"])
              if e["ref_id"] == session_id]
    assert len(events) == 1 and events[0]["payload"]["after"] < events[0]["payload"]["before"]

    # Same history again: the cached summary applies, no second model call.
    out2 = await context.prepare_window(_ctx(session_id=session_id), msgs)
    assert len(calls) == 1
    assert context.estimate_messages(out2) == context.estimate_messages(out)


# --- the settings routes ------------------------------------------------------


async def test_settings_round_trip_with_partial_put(monkeypatch):
    store: dict = {}

    async def get(key, default):
        return store.get(key, default)

    async def put(key, value):
        store[key] = value
    monkeypatch.setattr(repo, "get_app_setting", get)
    monkeypatch.setattr(repo, "set_app_setting", put)

    assert await routes.context_settings_get() == context.DEFAULTS

    got = await routes.context_settings_put(routes.ContextSettingsIn(drop_thinking=False))
    assert got["drop_thinking"] is False
    assert got["summarize"] is True, "unspecified fields keep their value"

    got = await routes.context_settings_put(routes.ContextSettingsIn(tool_results_keep_turns=7))
    assert got["drop_thinking"] is False and got["tool_results_keep_turns"] == 7
    assert await context.load_settings() == got, "what the runtime reads is what the route returns"


def test_headroom_is_a_validated_number():
    with pytest.raises(ValueError):
        routes.ContextSettingsIn(summarize_threshold=0.2)
    with pytest.raises(ValueError):
        routes.ContextSettingsIn(tool_results_keep_turns=-1)
