"""The suite cannot spend money on a model (epic-small-fixes R15, PROCESS-048).

`tests/conftest.py`'s session guard `no_live_model` replaces the installed
`OpenAIChatModel`'s network entry points (`request`, `request_stream` and the
`_completions_create` funnel under both) with functions raising
`REAL_MODEL_REFUSED`, unless `CC_LIVE_MODEL_TESTS=1`. A worktree's `.env` is a
copy of the live one and carries a real model key, so an unpinned test that
reached `resolve_model()` would otherwise bill it. The guard sits at the spend
point: building a live model object stays allowed (tests/test_models.py and
others assert on one without calling it), and `WindowedModel` wraps the same
class, so patching it covers every live path.
"""

from __future__ import annotations

import socket

import httpx
import pytest
from pydantic_ai.messages import ModelRequest, TextPart, UserPromptPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from central_command.config import settings
from central_command.runtime import context, models
from tests.conftest import REAL_MODEL_REFUSED

MESSAGES = [ModelRequest(parts=[UserPromptPart(content="hello")])]
PARAMS = ModelRequestParameters()


@pytest.fixture(autouse=True)
def opted_out(monkeypatch):
    """These tests assert the refusal, so a developer's ambient
    CC_LIVE_MODEL_TESTS=1 must not reach them; the opt-in tests set it."""
    monkeypatch.delenv("CC_LIVE_MODEL_TESTS", raising=False)


@pytest.fixture
def connects(monkeypatch):
    """Every socket connection attempted while the test runs."""
    seen: list = []
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def spy(self, address):
        seen.append(address)
        return real_connect(self, address)

    def spy_ex(self, address):
        seen.append(address)
        return real_connect_ex(self, address)

    monkeypatch.setattr(socket.socket, "connect", spy)
    monkeypatch.setattr(socket.socket, "connect_ex", spy_ex)
    return seen


async def _live_model(monkeypatch):
    """A live model built by `models._live_model` against a fake base URL and
    key. The proxy read for the model's declared output cap is the one other
    network touch on the build path, so it answers "undeclared"."""
    async def undeclared(model):
        return None

    monkeypatch.setattr(context, "output_cap_for", undeclared)
    monkeypatch.setattr(settings, "test_model", False)
    monkeypatch.setattr(settings, "llm_base_url", "http://llm.invalid:4000")
    monkeypatch.setattr(settings, "llm_api_key", "sk-not-a-real-key")
    return await models._live_model("some-model")


def _mock_completion(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={
        "id": "chatcmpl-1", "object": "chat.completion", "created": 0,
        "model": "some-model",
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": "pong"}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    })


def _answering_model() -> OpenAIChatModel:
    """An OpenAIChatModel whose HTTP client is an in-process mock: it can answer
    without any socket, so the opt-in path is testable offline."""
    client = httpx.AsyncClient(transport=httpx.MockTransport(_mock_completion))
    provider = OpenAIProvider(api_key="sk-not-a-real-key",
                              base_url="http://llm.invalid:4000/v1", http_client=client)
    return OpenAIChatModel("some-model", provider=provider)


async def test_a_built_live_models_request_is_refused_and_opens_no_socket(
    monkeypatch, connects,
):
    model = await _live_model(monkeypatch)
    assert type(model.wrapped) is OpenAIChatModel
    connects.clear()

    with pytest.raises(AssertionError, match="REAL model request"):
        await model.request(MESSAGES, None, PARAMS)
    assert connects == []


async def test_a_built_live_models_request_stream_is_refused_and_opens_no_socket(
    monkeypatch, connects,
):
    model = await _live_model(monkeypatch)
    connects.clear()

    with pytest.raises(AssertionError, match="REAL model request"):
        async with model.request_stream(MESSAGES, None, PARAMS):
            pass
    assert connects == []


async def test_the_underlying_class_is_refused_directly_too(monkeypatch, connects):
    """Not only through WindowedModel: the bare OpenAIChatModel and the
    `_completions_create` funnel refuse with the same message."""
    model = (await _live_model(monkeypatch)).wrapped
    connects.clear()

    with pytest.raises(AssertionError, match=REAL_MODEL_REFUSED):
        await model.request(MESSAGES, None, PARAMS)
    with pytest.raises(AssertionError, match=REAL_MODEL_REFUSED):
        async with model.request_stream(MESSAGES, None, PARAMS):
            pass
    with pytest.raises(AssertionError, match=REAL_MODEL_REFUSED):
        await model._completions_create(MESSAGES, False, {}, PARAMS)
    assert connects == []


def test_the_refusal_names_how_to_opt_in():
    assert "pin settings.test_model or fake the model" in REAL_MODEL_REFUSED
    assert "CC_LIVE_MODEL_TESTS=1" in REAL_MODEL_REFUSED


async def test_building_a_live_model_stays_allowed(monkeypatch):
    model = await _live_model(monkeypatch)
    assert type(model).__name__ == "WindowedModel"


async def test_the_guard_is_absent_with_the_opt_in(monkeypatch):
    """CC_LIVE_MODEL_TESTS=1 lets the real method through. The model's HTTP
    client is a mock transport, so the real path answers without a socket."""
    model = _answering_model()
    with pytest.raises(AssertionError, match="REAL model request"):
        await model.request(MESSAGES, None, PARAMS)

    monkeypatch.setenv("CC_LIVE_MODEL_TESTS", "1")
    response = await model.request(MESSAGES, None, PARAMS)
    assert [p.content for p in response.parts if isinstance(p, TextPart)] == ["pong"]


async def test_the_opt_in_is_read_per_call_not_at_install(monkeypatch):
    model = _answering_model()
    monkeypatch.setenv("CC_LIVE_MODEL_TESTS", "1")
    await model.request(MESSAGES, None, PARAMS)
    monkeypatch.delenv("CC_LIVE_MODEL_TESTS")
    with pytest.raises(AssertionError, match="REAL model request"):
        await model.request(MESSAGES, None, PARAMS)
    monkeypatch.setenv("CC_LIVE_MODEL_TESTS", "0")
    with pytest.raises(AssertionError, match="REAL model request"):
        await model.request(MESSAGES, None, PARAMS)


async def test_with_the_test_model_set_resolve_model_returns_the_double(monkeypatch):
    from pydantic_ai.models.function import FunctionModel

    monkeypatch.setattr(settings, "test_model", True)
    assert isinstance(await models.resolve_model(), FunctionModel)


def test_the_fixture_is_session_scoped_and_autouse(request):
    [fixturedef] = request.session._fixturemanager.getfixturedefs(
        "no_live_model", request.node)
    assert fixturedef.scope == "session"
    assert "no_live_model" in request.fixturenames  # autouse: this test asked for nothing
