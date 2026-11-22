"""Admission control for model turns (v2.28.1, per backend since v2.28.2,
pydantic-ai's limiter since DL-227): `CC_MODEL_CONCURRENCY` names alias
groups that share one backend and how many requests that backend takes at
once; `context.limit_concurrency` turns the spec into one shared
`ConcurrencyLimiter` per pool, applied at the one model seam."""
import asyncio

import pytest
from pydantic_ai.concurrency import ConcurrencyLimiter
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.concurrency import ConcurrencyLimitedModel
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.openai import OpenAIChatModel

from central_command.config import settings
from central_command.runtime import context, models


def _slow_model(log: list, name: str, hold: float = 0.05, model_name: str | None = None):
    async def fn(messages, info):
        log.append(f"start {name}")
        await asyncio.sleep(hold)
        log.append(f"end {name}")
        return ModelResponse(parts=[TextPart("ok")])

    async def stream_fn(messages, info):
        log.append(f"start {name}")
        await asyncio.sleep(hold)
        yield "ok"
        log.append(f"end {name}")
    return FunctionModel(fn, stream_function=stream_fn, model_name=model_name or name)


def _gated(log: list, name: str, hold: float = 0.05):
    """The shape `models._live_model` builds: WindowedModel outside, the
    admission gate inside."""
    return context.WindowedModel(context.limit_concurrency(_slow_model(log, name, hold)))


def _ask(model):
    return model.request([], None, ModelRequestParameters())


async def _fire(*models, n=3):
    await asyncio.gather(*[_ask(m) for m in models for _ in range(n)])


def test_the_spec_parses_pools_star_and_the_legacy_integer():
    assert context.parse_concurrency_spec("") == {}
    assert context.parse_concurrency_spec("1") == {"*": ("*", 1)}
    assert context.parse_concurrency_spec(1) == {"*": ("*", 1)}
    table = context.parse_concurrency_spec(" cc-default + gpt-4.1-nano = 1 ; *=8 ")
    assert table == {"cc-default": ("cc-default+gpt-4.1-nano", 1),
                     "gpt-4.1-nano": ("cc-default+gpt-4.1-nano", 1), "*": ("*", 8)}
    assert context.parse_concurrency_spec("a=1,b=2")["b"] == ("b", 2)
    for bad in ("cc-default", "cc-default=one", "=1", "+=1"):
        with pytest.raises(ValueError):
            context.parse_concurrency_spec(bad)


async def test_aliases_in_one_pool_share_its_single_slot(monkeypatch):
    monkeypatch.setattr(settings, "model_concurrency", "cc-default+gpt-4.1-nano=1")
    log: list = []
    a, b = _gated(log, "cc-default"), _gated(log, "gpt-4.1-nano")
    # One framework limiter INSTANCE behind both aliases — the pool is shared,
    # not merely configured alike.
    assert isinstance(a.wrapped, ConcurrencyLimitedModel)
    assert a.wrapped._limiter is b.wrapped._limiter
    assert isinstance(a.wrapped._limiter, ConcurrencyLimiter)
    assert a.wrapped._limiter.max_running == 1
    await _fire(a, b, n=2)
    assert len(log) == 8
    # Strict alternation: never two starts before an end, whichever alias.
    assert all(log[i].startswith("start") and log[i + 1].startswith("end")
               for i in range(0, len(log), 2))
    assert {e.split()[1] for e in log} == {"cc-default", "gpt-4.1-nano"}


async def test_waiting_is_first_in_first_out_with_no_rejection(monkeypatch):
    """A busy backend makes a request WAIT, in arrival order — never fail
    (no `max_queued`) and never jump the queue."""
    monkeypatch.setattr(settings, "model_concurrency", "cc-default+cc-smart=1")
    log: list = []
    holder = asyncio.create_task(_ask(_gated(log, "cc-default", hold=0.1)))
    await asyncio.sleep(0.01)
    waiters = []
    for name in ("w1", "w2", "w3", "w4", "w5"):
        model = context.WindowedModel(context.limit_concurrency(
            _slow_model(log, name, 0.01, model_name="cc-smart")))
        waiters.append(asyncio.create_task(_ask(model)))
        await asyncio.sleep(0)   # enqueue in this order
    await asyncio.gather(holder, *waiters)
    starts = [e.split()[1] for e in log if e.startswith("start")]
    assert starts == ["cc-default", "w1", "w2", "w3", "w4", "w5"]
    assert all(log[i].startswith("start") and log[i + 1] == "end" + log[i][5:]
               for i in range(0, len(log), 2))     # one at a time, each to its end


async def test_an_alias_the_spec_does_not_name_is_unlimited(monkeypatch):
    monkeypatch.setattr(settings, "model_concurrency", "cc-default=1")
    log: list = []
    model = _gated(log, "claude-sonnet-5")
    assert not isinstance(model.wrapped, ConcurrencyLimitedModel)
    await _fire(model)
    assert log[:3] == ["start claude-sonnet-5"] * 3


async def test_star_pools_the_unnamed_and_the_legacy_integer_is_star(monkeypatch):
    for spec in ("cc-default=1;*=2", "2", 2):
        monkeypatch.setattr(settings, "model_concurrency", spec)
        context._limiters.clear()
        log: list = []
        await _fire(_gated(log, "claude-sonnet-5"))
        assert log[:2] == ["start claude-sonnet-5"] * 2 and log[2] == "end claude-sonnet-5"


async def test_a_failed_request_releases_its_slot(monkeypatch):
    monkeypatch.setattr(settings, "model_concurrency", "cc-default=1")

    async def boom(messages, info):
        raise RuntimeError("backend down")
    model = context.WindowedModel(context.limit_concurrency(
        FunctionModel(boom, model_name="cc-default")))
    for _ in range(2):
        with pytest.raises(RuntimeError):
            await _ask(model)
    log: list = []
    await asyncio.wait_for(_fire(_gated(log, "cc-default"), n=1), 2)
    assert log == ["start cc-default", "end cc-default"]


async def test_a_cancelled_request_releases_its_slot_and_its_place(monkeypatch):
    """Cancelling the holder frees the slot; cancelling a waiter removes it
    from the queue without consuming a slot."""
    monkeypatch.setattr(settings, "model_concurrency", "cc-default=1")
    log: list = []
    holder = asyncio.create_task(_ask(_gated(log, "cc-default", hold=10)))
    await asyncio.sleep(0.01)
    waiter = asyncio.create_task(_ask(_gated(log, "cc-default", hold=10)))
    await asyncio.sleep(0.01)
    limiter = context.limit_concurrency(_slow_model(log, "cc-default"))._limiter
    assert (limiter.running_count, limiter.waiting_count) == (1, 1)
    waiter.cancel()
    holder.cancel()
    for t in (waiter, holder):
        with pytest.raises(asyncio.CancelledError):
            await t
    assert (limiter.running_count, limiter.waiting_count) == (0, 0)
    log.clear()
    await asyncio.wait_for(_fire(_gated(log, "cc-default", hold=0.01), n=1), 2)
    assert log == ["start cc-default", "end cc-default"]


async def test_a_streamed_request_holds_its_slot_until_the_stream_closes(monkeypatch):
    monkeypatch.setattr(settings, "model_concurrency", "cc-default=1")
    log: list = []
    streamed = _gated(log, "cc-default", hold=0.01)
    async with streamed.request_stream([], None, ModelRequestParameters()) as stream:
        async for _ in stream:
            pass
        second = asyncio.create_task(_ask(_gated(log, "cc-default", hold=0.01)))
        await asyncio.sleep(0.05)
        assert not second.done()          # waiting behind the open stream
    await asyncio.wait_for(second, 2)
    assert log[-2:] == ["start cc-default", "end cc-default"]


async def test_empty_spec_gates_nothing(monkeypatch):
    monkeypatch.setattr(settings, "model_concurrency", "")
    log: list = []
    model = _gated(log, "cc-default")
    assert not isinstance(model.wrapped, ConcurrencyLimitedModel)
    await _fire(model)
    assert log[:3] == ["start cc-default"] * 3


async def _live(monkeypatch, alias):
    async def undeclared(model):
        return None
    monkeypatch.setattr(context, "output_cap_for", undeclared)
    monkeypatch.setattr(settings, "test_model", False)
    monkeypatch.setattr(settings, "llm_base_url", "http://llm.invalid:4000")
    monkeypatch.setattr(settings, "llm_api_key", "sk-not-a-real-key")
    return await models._live_model(alias)


async def test_the_live_seam_puts_the_gate_inside_the_window_wrapper(monkeypatch):
    monkeypatch.setattr(settings, "model_concurrency", "cc-default+cc-smart=1")
    a, b = await _live(monkeypatch, "cc-default"), await _live(monkeypatch, "cc-smart")
    assert type(a) is context.WindowedModel
    assert type(a.wrapped) is ConcurrencyLimitedModel
    assert type(a.wrapped.wrapped) is OpenAIChatModel
    assert a.wrapped._limiter is b.wrapped._limiter
    other = await _live(monkeypatch, "cc-other")
    assert type(other.wrapped) is OpenAIChatModel


async def test_the_test_model_path_stays_ungated(monkeypatch):
    monkeypatch.setattr(settings, "model_concurrency", "*=1")
    monkeypatch.setattr(settings, "test_model", True)
    model = await models.resolve_model(agent_id="inbox-triage")
    assert not isinstance(model, (ConcurrencyLimitedModel, context.WindowedModel))
