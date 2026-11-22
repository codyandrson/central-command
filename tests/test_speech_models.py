"""The speech model pickers (Settings > Audio): the lists come from LiteLLM by
`model_info.mode`, and the choice is used by the single-node speech routes.

`GET /api/speech/models` is read by the cockpit on BOTH profiles: on the
single-node profile the browser calls it here; on k3s the Node server reads the
LISTS from it (`web/server/routes/cc-speech-models.ts`) and keeps the choice
itself. The cockpit hand-declares the shape (`useSpeechModels.ts`), so the
wire is pinned here, on the backend.
"""

from __future__ import annotations

import httpx
import pytest

from central_command.api import speech
from central_command.api.app import app
from central_command.db import repo
from central_command.integrations import litellm
from tests.conftest import needs_pg

MODEL_INFO = [
    {"model_name": "cc-tts", "model_info": {"mode": "audio_speech"}},
    {"model_name": "kokoro-alt", "model_info": {"mode": "audio_speech"}},
    {"model_name": "cc-stt", "model_info": {"mode": "audio_transcription"}},
    {"model_name": "whisper-large", "model_info": {"mode": "audio_transcription"}},
    {"model_name": "cc-default", "model_info": {"mode": "chat"}},
    {"model_name": "undeclared", "model_info": {}},
    # One model name served by two deployments appears twice in /model/info.
    {"model_name": "kokoro-alt", "model_info": {"mode": "audio_speech"}},
]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(speech.settings, "llm_base_url", "http://litellm.test:4000")
    monkeypatch.setattr(speech.settings, "llm_api_key", "sk-virtual")
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://cc")


@pytest.fixture
def catalog(monkeypatch):
    async def fake_fetch(_op):
        return MODEL_INFO

    monkeypatch.setattr(litellm, "_fetch_model_info", fake_fetch)


@pytest.fixture
async def clean_setting():
    conn = await repo._conn()
    try:
        await conn.execute("delete from app_setting where key = $1", speech.SPEECH_SETTING)
        yield
        await conn.execute("delete from app_setting where key = $1", speech.SPEECH_SETTING)
    finally:
        await conn.close()


async def test_the_lists_are_the_speech_modes_litellm_declares(catalog):
    lists = await litellm.speech_model_ids()
    assert lists == {
        "audio_speech": ["cc-tts", "kokoro-alt"],
        "audio_transcription": ["cc-stt", "whisper-large"],
    }


@needs_pg
async def test_the_wire_shape_the_settings_page_reads(client, catalog, clean_setting):
    resp = await client.get("/api/speech/models")
    assert resp.status_code == 200
    assert resp.json() == {
        "tts": {"selected": "cc-tts", "default": "cc-tts", "models": ["cc-tts", "kokoro-alt"]},
        "stt": {"selected": "cc-stt", "default": "cc-stt", "models": ["cc-stt", "whisper-large"]},
        "error": None,
    }


@needs_pg
async def test_an_unreadable_proxy_still_answers_with_the_current_models(client, monkeypatch, clean_setting):
    async def down(_op):
        raise litellm.LiteLLMError("speech model catalog — http://litellm.test:4000 unreachable")

    monkeypatch.setattr(litellm, "_fetch_model_info", down)
    body = (await client.get("/api/speech/models")).json()
    assert body["tts"] == {"selected": "cc-tts", "default": "cc-tts", "models": []}
    assert body["stt"]["selected"] == "cc-stt"
    assert "unreachable" in body["error"]

    resp = await client.put("/api/speech/models", json={"tts": "kokoro-alt"})
    assert resp.status_code == 502, "a model that cannot be checked is not stored"
    resp = await client.put("/api/speech/models", json={"tts": "cc-tts"})
    assert resp.status_code == 200, "going back to the alias needs no list"


@needs_pg
async def test_a_listed_model_is_chosen_and_an_unlisted_one_is_refused(client, catalog, clean_setting):
    resp = await client.put("/api/speech/models", json={"tts": "kokoro-alt", "stt": "whisper-large"})
    assert resp.status_code == 200
    assert resp.json()["tts"]["selected"] == "kokoro-alt"
    assert resp.json()["stt"]["selected"] == "whisper-large"

    for bad in ({"tts": "whisper-large"}, {"tts": "cc-default"}, {"stt": "nope"}):
        resp = await client.put("/api/speech/models", json=bad)
        assert resp.status_code == 422, bad
    assert (await client.get("/api/speech/models")).json()["tts"]["selected"] == "kokoro-alt"

    resp = await client.put("/api/speech/models", json={"tts": None})
    assert resp.json()["tts"]["selected"] == "cc-tts", "null goes back to the alias"
    assert resp.json()["stt"]["selected"] == "whisper-large", "an omitted field is unchanged"


@needs_pg
async def test_the_routes_send_the_chosen_model(client, catalog, clean_setting, monkeypatch):
    sent: list[tuple[str, str]] = []
    real = httpx.AsyncClient

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/speech"):
            import json

            sent.append(("tts", json.loads(req.content)["model"]))
            return httpx.Response(200, content=b"ID3", headers={"content-type": "audio/mpeg"})
        body = req.content.decode("latin-1")
        sent.append(("stt", "whisper-large" if "whisper-large" in body else body))
        return httpx.Response(200, json={"text": "hello"})

    def factory(**kw):
        kw["transport"] = httpx.MockTransport(handler)
        return real(**kw)

    # Without a choice the aliases are sent, exactly as before.
    monkeypatch.setattr(speech.httpx, "AsyncClient", factory)
    assert (await client.post("/api/tts", json={"text": "hi"})).status_code == 200
    assert sent[-1] == ("tts", "cc-tts")

    monkeypatch.setattr(speech.httpx, "AsyncClient", real)
    await client.put("/api/speech/models", json={"tts": "kokoro-alt", "stt": "whisper-large"})
    monkeypatch.setattr(speech.httpx, "AsyncClient", factory)

    assert (await client.post("/api/tts", json={"text": "hi"})).status_code == 200
    assert sent[-1] == ("tts", "kokoro-alt")
    resp = await client.post("/api/transcribe", files={"file": ("a.webm", b"RIFFxxxx", "audio/webm")})
    assert resp.status_code == 200
    assert sent[-1] == ("stt", "whisper-large")
    assert (await client.get("/api/transcribe/config")).json()["model"] == "whisper-large"
    assert (await client.get("/api/tts/config")).json()["openai"]["model"] == "kokoro-alt"
