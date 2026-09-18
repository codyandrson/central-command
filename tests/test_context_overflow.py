"""The window guard and the tool-result ceiling (2026-09-16).

Ten inbox-triage sessions died in one day with the proxy's 400
ContextWindowExceeded: one turn opened four full marketing emails through
`mail_read`, the tool clip never fired (it was the OUTPUT cap × 4), and the
working window's estimate at 4 chars/token called a 111k-token request
half-full. Three seams changed: the estimate's density, a per-result
ceiling derived from the INPUT window, and a last-resort clip before a
request that would not fit."""

from __future__ import annotations

import pytest
from pydantic_ai.messages import ModelRequest, ToolReturnPart

from central_command.config import settings
from central_command.contract import failures
from central_command.runtime import context, tools
from tests.test_context_window import _ctx, _pairs_ok, _pin, _transcript, _window


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    context._windows.clear()
    context._sent.clear()
    yield
    context._windows.clear()
    context._sent.clear()


def test_tool_result_ceiling_follows_the_smallest_discovered_window():
    assert context.tool_result_ceiling() == int(
        settings.context_window * context.TOOL_RESULT_SHARE * context.CHARS_PER_TOKEN)
    context._windows["cc-default"] = 81920
    context._windows["big-model"] = 400000
    assert context.tool_result_ceiling() == int(81920 * 0.10 * 3)


def test_clip_default_is_the_input_window_share_not_the_output_cap():
    context._windows["cc-default"] = 81920
    ceiling = context.tool_result_ceiling()
    assert ceiling < tools._MODEL_TEXT_CEILING
    out = tools._clip("x" * (ceiling + 1))
    assert out.startswith("x" * ceiling) and "TRUNCATED" in out
    assert tools._clip("x" * ceiling) == "x" * ceiling


def test_clip_tool_results_keeps_the_head_of_every_result_and_the_pairing():
    msgs = _transcript(turns=3, payload=5000)
    out = context.clip_tool_results(msgs, 1000)
    returns = [p for m in out if isinstance(m, ModelRequest)
               for p in m.parts if isinstance(p, ToolReturnPart)]
    assert all(r.content.startswith("x" * 1000) and "[CLIPPED at 1000" in r.content for r in returns)
    assert _pairs_ok(out)
    assert context.clip_tool_results(out, 1000) == out, "idempotent — no double marker"
    small = context.clip_tool_results(_transcript(turns=2, payload=50), 1000)
    assert not any("[CLIPPED" in p.content for m in small if isinstance(m, ModelRequest)
                   for p in m.parts if isinstance(p, ToolReturnPart))


async def test_a_request_that_would_not_fit_is_clipped_not_sent(monkeypatch):
    # One turn, four huge results (the 2026-09-16 shape), no earlier turns to
    # clear — only the overflow guard can bring it under the window.
    msgs = _transcript(turns=1, payload=60_000)
    msgs[2].parts = [ToolReturnPart("read_thing", "m" * 60_000, tool_call_id="call_0")] * 4
    msgs[1].parts = msgs[1].parts[:1] + [
        type(msgs[1].parts[1])("read_thing", {"n": i}, tool_call_id="call_0") for i in range(4)]
    _pin(monkeypatch, {"summarize": False, "pressure_warning": False}, window=30_000)
    emitted = []

    async def fake_emit(kind, **kw):
        emitted.append((kind, kw["payload"]))
    from central_command import events
    monkeypatch.setattr(events, "emit", fake_emit)

    out = await _window(_ctx(session_id="sess_over"), msgs)
    assert context.estimate_messages(out) < context.estimate_messages(msgs)
    assert context.estimate_messages(out) < 30_000 * context.OVERFLOW_FRACTION
    kinds = [k for k, _ in emitted]
    assert "session.context_overflow_clipped" in kinds
    assert context.estimate_messages(msgs) > 30_000, "the record is untouched"


async def test_a_dead_summarizer_still_ends_in_the_clip_not_raw_history(monkeypatch):
    """2026-09-18: the summary call hit the model's output cap mid-reasoning and
    raised; `prepare_window` caught it at the top and sent the RAW 600k-token
    record, which the proxy refused. Losing the summary must fall through to
    the trimmed window and the overflow clip."""
    msgs = _transcript(turns=6, payload=20_000)
    _pin(monkeypatch, {"summarize": True, "summarize_threshold": 0.5,
                       "pressure_warning": False, "tool_results_keep_turns": 6},
         window=30_000)

    async def dead_summarizer(model, region):
        raise RuntimeError("Model token limit (10000) exceeded before any response was generated")
    monkeypatch.setattr(context, "summarize", dead_summarizer)
    emitted = []

    async def fake_emit(kind, **kw):
        emitted.append(kind)
    from central_command import events
    monkeypatch.setattr(events, "emit", fake_emit)

    out = await _window(_ctx(session_id="sess_deadsum"), msgs)
    assert "session.context_compacted" not in emitted
    assert "session.context_overflow_clipped" in emitted
    assert context.estimate_messages(out) < 30_000 * context.OVERFLOW_FRACTION
    assert _pairs_ok(out)


async def test_a_request_that_fits_is_not_clipped(monkeypatch):
    msgs = _transcript(turns=1, payload=2000)
    _pin(monkeypatch, {"summarize": False, "pressure_warning": False}, window=100_000)
    emitted = []

    async def fake_emit(kind, **kw):
        emitted.append(kind)
    from central_command import events
    monkeypatch.setattr(events, "emit", fake_emit)
    out = await _window(_ctx(session_id="sess_fit"), msgs)
    assert "session.context_overflow_clipped" not in emitted
    assert not any("[CLIPPED" in p.content for m in out if isinstance(m, ModelRequest)
                   for p in m.parts if isinstance(p, ToolReturnPart))


def test_context_overflow_is_semantic_so_the_attempt_cap_holds():
    """Waiting never shrinks a request. A 400 that says the window was
    exceeded must spend an attempt and park FAILED at the cap, not release
    with backoff forever."""
    body = ("litellm.ContextWindowExceededError: litellm.BadRequestError: "
            "ContextWindowExceededError: OpenAIException - request (94740 tokens) "
            "exceeds the maximum context length (81920 tokens)")
    assert failures.classify_failure_text(body) == failures.SEMANTIC


def test_graph_propose_notes_tell_the_agent_to_name_the_operator():
    from central_command.runtime import packs
    cap = next(c for c in packs.PACKS["graph-propose"].capabilities if c.name == "graph.add_episode")
    assert "NAME THE OPERATOR" in cap.notes
    assert "'the operator is a rewards member there'" not in cap.notes
