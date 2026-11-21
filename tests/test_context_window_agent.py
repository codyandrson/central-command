"""The working window through a REAL agent run (pydantic-ai 2.66 upgrade).

`test_context_window.py` tests the window's steps on hand-built lists; this
file drives `Agent.run` / `Agent.run_stream` with the runtime's own capability
list (`context.capabilities()`), a `WindowedModel` and a deferred skill
`Capability` that carries instructions — the shape every long-lived agent has.

Why it exists: from pydantic-ai 2.59 the outgoing request is a copy of the
one a history processor sees, so the old hand-off (matched by object identity)
silently put the RAW record on the wire whenever an agent held a capability
with instructions. Every hand-built test still passed. What is pinned here:

- the model is sent the trimmed window (old tool output cleared, old
  reasoning dropped), and `all_messages()` — what gets persisted — is raw;
- a loaded skill's guidance still reaches the model, and survives a summary;
- a windowed agent's request that reaches the model without its window is
  loud (`session.context_window_unapplied`) and goes out clipped, not raw.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic_ai import Agent
from pydantic_ai.capabilities import Capability
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from central_command.config import settings
from central_command.runtime import context

GUIDANCE = "SKILL GUIDANCE: always cite the ledger row you read."
PAYLOAD = 4000
TURNS = 6


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    monkeypatch.setattr(settings, "test_model", False)
    context._windows.clear()
    context._sent.clear()
    # A FunctionModel's usage calibrates the density table; it must not leak
    # into the next test's estimates.
    monkeypatch.setattr(context, "_density", {})

    async def load():
        return {**context.DEFAULTS, "summarize": False, "pressure_warning": False}
    monkeypatch.setattr(context, "load_settings", load)
    context._windows["cc-default"] = 1_000_000  # nothing near overflow: only the trims act
    emitted: list = []

    async def fake_emit(kind, **kw):
        emitted.append((kind, kw))
    from central_command import events
    monkeypatch.setattr(events, "emit", fake_emit)
    yield emitted
    context._windows.clear()
    context._sent.clear()


def _texts(messages) -> str:
    out = []
    for m in messages:
        for p in m.parts:
            c = getattr(p, "content", None)
            out.append(c if isinstance(c, str) else str(c))
        if isinstance(m, ModelRequest) and m.instructions:
            out.append(m.instructions)
    return "\n".join(out)


def _returns(messages, tool="read_thing"):
    return [p for m in messages if isinstance(m, ModelRequest) for p in m.parts
            if isinstance(p, ToolReturnPart) and p.tool_name == tool]


def _script(turns: int = TURNS, on_request=None):
    """load the skill, call its tool, then `turns` reads (each with reasoning),
    then answer. Returns (function, the list of what each request saw).
    `on_request` is called at each request, after the window was applied."""
    seen: list = []

    def fn(messages, info: AgentInfo):
        seen.append((list(messages), [t.name for t in info.function_tools]))
        if on_request:
            on_request()
        step = len(seen)
        if step == 1:
            return ModelResponse(parts=[ToolCallPart("load_capability", {"id": "sk-window"},
                                                     tool_call_id="load-1")])
        if step == 2:
            return ModelResponse(parts=[ToolCallPart("skill_lookup", {}, tool_call_id="skill-1")])
        if step < 3 + turns:
            return ModelResponse(parts=[
                ThinkingPart(f"reasoning {step} " + "t" * 400),
                ToolCallPart("read_thing", {"n": step}, tool_call_id=f"read-{step}"),
            ])
        return ModelResponse(parts=[TextPart("done")])
    return fn, seen


def _agent(model):
    cap = Capability(id="sk-window", description="a test skill", instructions=GUIDANCE,
                     defer_loading=True)

    def skill_lookup() -> str:
        """Look the thing up in the skill's references."""
        return "the skill's answer"
    cap.tool_plain(name="skill_lookup")(skill_lookup)
    agent = Agent(model, system_prompt="charter", deps_type=SimpleNamespace,
                  capabilities=[cap, *context.capabilities()])

    @agent.tool_plain
    def read_thing(n: int) -> str:
        """Read a thing."""
        return f"thing {n} " + "x" * PAYLOAD
    return agent


def _deps():
    return SimpleNamespace(session_id=None, agent_id="ctx-agent-test")


def _assert_windowed_and_raw(seen, record):
    last, _tools = seen[-1]
    wire_returns = _returns(last)
    assert len(wire_returns) == TURNS
    cleared = [r for r in wire_returns if str(r.content).startswith("[cleared from context")]
    # keep_turns=3 by default: the three newest requests keep their payload.
    assert len(cleared) == TURNS - 3, (
        "the model was sent the RAW record: the working window never reached the wire")
    thinking = [p for m in last if isinstance(m, ModelResponse) for p in m.parts
                if isinstance(p, ThinkingPart)]
    assert len(thinking) <= 1, "old reasoning is dropped from what is sent"
    # The record is raw: every payload whole, every thought kept.
    rec_returns = _returns(record)
    assert len(rec_returns) == TURNS
    assert all(len(str(r.content)) > PAYLOAD for r in rec_returns)
    assert "[cleared from context" not in _texts(record)
    assert sum(isinstance(p, ThinkingPart) for m in record for p in m.parts) == TURNS
    # The skill's guidance, recorded in history since pydantic-ai 2.59, still
    # reaches the model once the skill is loaded — the window must not cost it.
    assert GUIDANCE in _texts(last), "the loaded skill's guidance did not reach the model"
    assert GUIDANCE not in _texts(seen[0][0]), "an unloaded skill costs no context"


async def test_a_real_run_sends_the_window_and_persists_the_raw_record():
    fn, seen = _script()
    agent = _agent(context.WindowedModel(FunctionModel(fn, model_name="cc-default")))
    result = await agent.run("do the thing", deps=_deps())
    assert result.output == "done"
    _assert_windowed_and_raw(seen, result.all_messages())


async def test_a_streamed_run_sends_the_window_too(pinned, caplog):
    """2.66 runs the streamed request in a task whose context was copied
    between the window's two hooks; a healthy streamed run must not read as a
    missed window (no event, no error, no clip on top of the window)."""
    fn, seen = _script()

    async def stream(messages, info: AgentInfo):
        response = fn(messages, info)
        for p in response.parts:
            if isinstance(p, ToolCallPart):
                yield {0: DeltaToolCall(name=p.tool_name, json_args=p.args_as_json_str(),
                                        tool_call_id=p.tool_call_id)}
            elif isinstance(p, TextPart):
                yield p.content

    agent = _agent(context.WindowedModel(FunctionModel(stream_function=stream,
                                                       model_name="cc-default")))
    async with agent.run_stream("do the thing", deps=_deps()) as run:
        assert await run.get_output() == "done"
        record = run.all_messages()
    last = seen[-1][0]
    wire_returns = _returns(last)
    assert len(wire_returns) == TURNS
    assert sum(str(r.content).startswith("[cleared from context") for r in wire_returns) == TURNS - 3
    assert all(len(str(r.content)) > PAYLOAD for r in _returns(record)), "the record is raw"
    assert GUIDANCE in _texts(last)
    assert "session.context_window_unapplied" not in [k for k, _ in pinned]
    assert "without its working window" not in caplog.text


async def test_a_loaded_skill_survives_a_summary(monkeypatch):
    """A summary drops the region it compresses. Since 2.59 the region holds
    the `load_capability` exchange that keeps the skill loaded on the wire, and
    the recorded guidance: both must be carried past the summary, or the model
    loses a skill the record (and the execution gate) still holds loaded."""
    fn, seen = _script()
    store: dict = {}

    async def get_rs(session_id):
        return dict(store)

    async def put_rs(session_id, patch):
        store.update(patch)

    async def fake_summarize(model, region):
        return "SUMMARY-TEXT"
    from central_command.db import repo
    monkeypatch.setattr(repo, "get_session_run_state", get_rs)
    monkeypatch.setattr(repo, "update_session_run_state", put_rs)
    monkeypatch.setattr(context, "summarize", fake_summarize)

    async def load():
        return {**context.DEFAULTS, "pressure_warning": False, "clear_tool_results": False,
                "drop_thinking": False, "output_headroom": False, "summarize_threshold": 0.5}
    monkeypatch.setattr(context, "load_settings", load)
    context._windows["cc-default"] = 12_000

    agent = _agent(context.WindowedModel(FunctionModel(fn, model_name="cc-default")))
    result = await agent.run("do the thing", deps=SimpleNamespace(session_id="sess_agentsum",
                                                                  agent_id="ctx-agent-test"))
    assert store.get("context_summary"), "the window grew past the threshold: a summary ran"
    last, tools = seen[-1]
    assert "SUMMARY-TEXT" in _texts(last)
    assert len(_returns(last)) < TURNS, "the summary took the early reads with it"
    assert GUIDANCE in _texts(last), "the loaded skill's guidance went with the summary"
    assert "skill_lookup" in tools, "the loaded skill's tool went with the summary"
    assert "SUMMARY-TEXT" not in _texts(result.all_messages()), "the record never carries it"


async def test_a_request_that_misses_its_window_is_loud_and_clipped(monkeypatch, pinned, caplog):
    """If the framework ever sends a windowed agent's request without running
    the window's hook (2.59 did the equivalent silently), the wrapper reports
    it and sends the static clip — never the raw record without a word."""
    fn, seen = _script()

    async def skipped(self, ctx, request_context):
        return request_context
    monkeypatch.setattr(context.WorkingWindow, "prepare_model_request", skipped)
    monkeypatch.setattr(settings, "context_window", 5_000)  # a clip under PAYLOAD at any density
    agent = _agent(context.WindowedModel(FunctionModel(fn, model_name="cc-default")))
    result = await agent.run("do the thing", deps=_deps())
    assert result.output == "done"
    kinds = [k for k, _ in pinned]
    assert "session.context_window_unapplied" in kinds
    assert "without its working window" in caplog.text
    size = int(5_000 * context.OVERFLOW_CLIP_SHARE * context.density_for("cc-default"))
    assert all("[CLIPPED at" in str(r.content) and len(str(r.content)) < size + 200
               for r in _returns(seen[-1][0])), "the clip went on the wire, not the raw record"
    assert all(len(str(r.content)) > PAYLOAD for r in _returns(result.all_messages()))


async def test_a_session_summarised_again_and_again_loses_nothing(monkeypatch):
    """Every re-summary maps its boundary back to the RAW record through the
    shape the previous summary built (its summary request and the carried
    skill exchange). After each request: every raw message is either inside
    the summary boundary or on the wire, the newest tool result is on the
    wire, and the carried exchange and the guidance appear exactly once."""
    from pydantic_ai.messages import ToolCallPart as Call

    store: dict = {}
    fn, seen = _script(turns=16)
    summaries = iter(f"SUMMARY-{i}" for i in range(1, 100))

    async def get_rs(session_id):
        return dict(store)

    async def put_rs(session_id, patch):
        store.update(patch)

    summarised: set = set()  # every call id a summary was written over

    async def fake_summarize(model, region):
        summarised.update(p.tool_call_id for m in region for p in m.parts
                          if isinstance(p, (Call, ToolReturnPart)))
        return next(summaries)
    from central_command.db import repo
    monkeypatch.setattr(repo, "get_session_run_state", get_rs)
    monkeypatch.setattr(repo, "update_session_run_state", put_rs)
    monkeypatch.setattr(context, "summarize", fake_summarize)

    async def load():
        return {**context.DEFAULTS, "pressure_warning": False, "clear_tool_results": False,
                "drop_thinking": False, "output_headroom": False, "summarize_threshold": 0.5}
    monkeypatch.setattr(context, "load_settings", load)
    context._windows["cc-default"] = 9_000

    agent = _agent(context.WindowedModel(FunctionModel(fn, model_name="cc-default")))
    result = await agent.run("do the thing", deps=SimpleNamespace(session_id="sess_resum",
                                                                  agent_id="ctx-agent-test"))
    record = result.all_messages()
    texts = [_texts(w) for w, _ in seen]
    made = int(next(summaries).split("-")[1]) - 1
    assert made >= 3, f"the session was summarised {made} times, not three or more"
    for i, (wire, tools) in enumerate(seen, 1):
        raw = record[:2 * i - 1]              # the record as this request was sent
        wire_ids = {p.tool_call_id for m in wire for p in m.parts
                    if isinstance(p, (Call, ToolReturnPart))}
        missing = [p.tool_call_id for m in raw for p in m.parts
                   if isinstance(p, (Call, ToolReturnPart))
                   and p.tool_call_id not in wire_ids | summarised]
        assert not missing, f"request {i}: raw messages neither summarised nor sent: {missing}"
        newest = [p.tool_call_id for p in raw[-1].parts if isinstance(p, ToolReturnPart)]
        assert set(newest) <= wire_ids, f"request {i}: the newest tool result is not on the wire"
        if i > 1:
            loads = [p for m in wire for p in m.parts
                     if isinstance(p, Call) and p.tool_name == "load_capability"]
            assert len(loads) == 1, f"request {i}: the load exchange appears {len(loads)} times"
            assert texts[i - 1].count(GUIDANCE) == 1, f"request {i}: the guidance is not sent once"
            assert "skill_lookup" in tools


async def test_without_the_private_run_context_lookup_the_run_still_windows(monkeypatch, pinned,
                                                                            caplog):
    """`get_current_run_context` lives in a private pydantic-ai module. If a
    release moves it, the missed-window check goes off with ONE loud error,
    and requests still go out windowed — never an ImportError per request."""
    import builtins

    real_import = builtins.__import__

    def no_private(name, *args, **kwargs):
        if name == "pydantic_ai._run_context":
            raise ImportError("moved")
        return real_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", no_private)
    monkeypatch.setattr(context, "_RUN_CONTEXT_LOOKUP", [])
    fn, seen = _script()
    agent = _agent(context.WindowedModel(FunctionModel(fn, model_name="cc-default")))
    result = await agent.run("do the thing", deps=_deps())
    assert result.output == "done"
    _assert_windowed_and_raw(seen, result.all_messages())
    assert caplog.text.count("get_current_run_context is unavailable") == 1, "once, not per request"
    assert "session.context_window_unapplied" not in [k for k, _ in pinned]
