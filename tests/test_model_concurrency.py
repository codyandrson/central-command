"""Admission control for model turns (v2.28.1, per backend since v2.28.2):
`CC_MODEL_CONCURRENCY` names alias groups that share one backend and how
many requests that backend takes at once, enforced at the one model seam."""
import asyncio

import pytest
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.function import FunctionModel

from central_command.config import settings
from central_command.runtime import context


def _slow_model(log: list, name: str, hold: float = 0.05):
    async def fn(messages, info):
        log.append(f"start {name}")
        await asyncio.sleep(hold)
        log.append(f"end {name}")
        return ModelResponse(parts=[TextPart("ok")])
    return FunctionModel(fn, model_name=name)


async def _fire(*models, n=3):
    await asyncio.gather(*[
        m.request([], None, ModelRequestParameters()) for m in models for _ in range(n)
    ])


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
    await _fire(context.WindowedModel(_slow_model(log, "cc-default")),
                context.WindowedModel(_slow_model(log, "gpt-4.1-nano")), n=2)
    assert len(log) == 8
    # Strict alternation: never two starts before an end, whichever alias.
    assert all(log[i].startswith("start") and log[i + 1].startswith("end")
               for i in range(0, len(log), 2))
    assert {e.split()[1] for e in log} == {"cc-default", "gpt-4.1-nano"}


async def test_an_alias_the_spec_does_not_name_is_unlimited(monkeypatch):
    monkeypatch.setattr(settings, "model_concurrency", "cc-default=1")
    log: list = []
    await _fire(context.WindowedModel(_slow_model(log, "claude-sonnet-5")))
    assert log[:3] == ["start claude-sonnet-5"] * 3


async def test_star_pools_the_unnamed_and_the_legacy_integer_is_star(monkeypatch):
    for spec in ("cc-default=1;*=2", "2", 2):
        monkeypatch.setattr(settings, "model_concurrency", spec)
        context._gates.clear()
        log: list = []
        await _fire(context.WindowedModel(_slow_model(log, "claude-sonnet-5")))
        assert log[:2] == ["start claude-sonnet-5"] * 2 and log[2] == "end claude-sonnet-5"


async def test_a_failed_request_releases_its_slot(monkeypatch):
    monkeypatch.setattr(settings, "model_concurrency", "cc-default=1")

    async def boom(messages, info):
        raise RuntimeError("backend down")
    model = context.WindowedModel(FunctionModel(boom, model_name="cc-default"))
    for _ in range(2):
        with pytest.raises(RuntimeError):
            await model.request([], None, ModelRequestParameters())
    log: list = []
    await asyncio.wait_for(_fire(context.WindowedModel(_slow_model(log, "cc-default")), n=1), 2)
    assert log == ["start cc-default", "end cc-default"]


async def test_empty_spec_gates_nothing(monkeypatch):
    monkeypatch.setattr(settings, "model_concurrency", "")
    log: list = []
    await _fire(context.WindowedModel(_slow_model(log, "cc-default")))
    assert log[:3] == ["start cc-default"] * 3
