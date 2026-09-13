"""Admission control for model turns (v2.28.1): `CC_MODEL_CONCURRENCY`
bounds how many requests are in flight at once, at the one model seam."""
import asyncio

from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.function import FunctionModel

from central_command.config import settings
from central_command.runtime import context


def _slow_model(log: list, hold: float = 0.05):
    async def fn(messages, info):
        log.append("start")
        await asyncio.sleep(hold)
        log.append("end")
        return ModelResponse(parts=[TextPart("ok")])
    return FunctionModel(fn)


async def _fire(model, n=3):
    await asyncio.gather(*[
        model.request([], None, ModelRequestParameters()) for _ in range(n)
    ])


async def test_one_slot_serialises_every_path_through_the_seam(monkeypatch):
    monkeypatch.setattr(settings, "model_concurrency", 1)
    log: list = []
    await _fire(context.WindowedModel(_slow_model(log)))
    assert log == ["start", "end"] * 3  # never two starts before an end


async def test_zero_means_unlimited(monkeypatch):
    monkeypatch.setattr(settings, "model_concurrency", 0)
    log: list = []
    await _fire(context.WindowedModel(_slow_model(log)))
    assert log[:3] == ["start", "start", "start"]


async def test_two_slots_admit_two(monkeypatch):
    monkeypatch.setattr(settings, "model_concurrency", 2)
    log: list = []
    await _fire(context.WindowedModel(_slow_model(log)))
    assert log[:2] == ["start", "start"] and log[2] == "end"


async def test_a_failed_request_releases_its_slot(monkeypatch):
    monkeypatch.setattr(settings, "model_concurrency", 1)

    async def boom(messages, info):
        raise RuntimeError("backend down")
    model = context.WindowedModel(FunctionModel(boom))
    for _ in range(2):
        try:
            await model.request([], None, ModelRequestParameters())
        except RuntimeError:
            pass
    log: list = []
    await asyncio.wait_for(_fire(context.WindowedModel(_slow_model(log)), n=1), 2)
    assert log == ["start", "end"]
