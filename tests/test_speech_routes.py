"""The single-server cockpit's speech routes forward to the two LiteLLM speech
aliases with the cockpit's own wire shapes — and refuse cleanly when the
spine's LiteLLM door is unconfigured. No network: httpx is stubbed."""

from __future__ import annotations

import httpx
import pytest

from central_command.api import speech
from central_command.api.app import app


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(speech.settings, "llm_base_url", "http://litellm.test:4000")
    monkeypatch.setattr(speech.settings, "llm_api_key", "sk-virtual")
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://cc")


def _stub_upstream(monkeypatch, handler):
    """Route the module's outbound httpx client through `handler`."""
    real = httpx.AsyncClient

    def factory(**kw):
        kw["transport"] = httpx.MockTransport(handler)
        return real(**kw)

    monkeypatch.setattr(speech.httpx, "AsyncClient", factory)


async def test_tts_forwards_to_the_alias_and_returns_audio(client, monkeypatch):
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["auth"] = req.headers["authorization"]
        seen["body"] = req.read()
        return httpx.Response(200, content=b"ID3mp3", headers={"content-type": "audio/mpeg"})

    _stub_upstream(monkeypatch, handler)
    async with client:
        r = await client.post("/api/tts", json={"text": "read this", "provider": "edge", "model": "ignored"})
    assert r.status_code == 200 and r.content == b"ID3mp3"
    assert r.headers["content-type"] == "audio/mpeg"
    assert seen["url"] == "http://litellm.test:4000/v1/audio/speech"
    assert seen["auth"] == "Bearer sk-virtual"
    assert b'"model":"cc-tts"' in seen["body"].replace(b" ", b"")  # the alias, never the client's model


async def test_transcribe_forwards_the_file_and_returns_text(client, monkeypatch):
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["body"] = req.read()
        return httpx.Response(200, json={"text": "hello there"})

    _stub_upstream(monkeypatch, handler)
    async with client:
        r = await client.post("/api/transcribe", files={"file": ("audio.webm", b"RIFFxxxx", "audio/webm")})
    assert r.status_code == 200 and r.json() == {"text": "hello there"}
    assert seen["url"] == "http://litellm.test:4000/v1/audio/transcriptions"
    assert b"cc-stt" in seen["body"] and b"RIFFxxxx" in seen["body"]


async def test_upstream_failure_is_a_502_with_the_alias_named(client, monkeypatch):
    _stub_upstream(monkeypatch, lambda req: httpx.Response(404, text="no such model"))
    async with client:
        r = await client.post("/api/tts", json={"text": "x"})
    assert r.status_code == 502 and "cc-tts" in r.json()["error"]


async def test_unconfigured_door_is_a_503_not_a_crash(monkeypatch):
    monkeypatch.setattr(speech.settings, "llm_base_url", "")
    monkeypatch.setattr(speech.settings, "llm_api_key", "")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://cc") as c:
        r = await c.post("/api/tts", json={"text": "x"})
    assert r.status_code == 503


async def test_config_endpoints_speak_the_cockpit_shapes(client):
    async with client:
        tts = (await client.get("/api/tts/config")).json()
        stt = (await client.get("/api/transcribe/config")).json()
    assert tts["defaultProvider"] == "openai" and tts["openai"]["model"] == "cc-tts"
    assert stt == {"provider": "openai", "model": "cc-stt", "modelReady": True}
