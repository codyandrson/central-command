"""The speech gate: cc-tts / cc-stt take one job at a time, in arrival order.

LiteLLM 1.110 REJECTS a request that finds the alias's `max_parallel_requests:
1` slot taken (HTTP 429), where 1.98 made it wait; so the wait lives in the API
process (`integrations/speech_gate`) and `api/speech.py` goes through it. No
network: the proxy is a stubbed httpx transport, the line is a real FifoGate.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from central_command.api import speech
from central_command.api.app import app
from central_command.integrations import litellm
from central_command.integrations.speech_gate import STT_GATE, TTS_GATE, Disconnected, FifoGate


async def _settle(n: int = 5) -> None:
    for _ in range(n):
        await asyncio.sleep(0)


# ── the line itself ──────────────────────────────────────────────────────────


async def test_callers_enter_one_at_a_time_in_arrival_order():
    gate = FifoGate(1)
    entered: list[int] = []
    inside = 0
    peak = 0

    async def job(n: int) -> None:
        nonlocal inside, peak
        async with gate.slot():
            inside += 1
            peak = max(peak, inside)
            entered.append(n)
            await asyncio.sleep(0.01)
            inside -= 1

    tasks = []
    for n in range(6):
        tasks.append(asyncio.create_task(job(n)))
        await _settle()  # each is in the line before the next arrives
    await asyncio.gather(*tasks)
    assert entered == [0, 1, 2, 3, 4, 5]
    assert peak == 1
    assert gate.free == 1 and gate.waiting == 0


async def test_a_newcomer_cannot_overtake_a_waiter_when_the_slot_is_freed():
    gate = FifoGate(1)
    order: list[str] = []
    release_a = asyncio.Event()

    async def holder() -> None:
        async with gate.slot():
            await release_a.wait()
            order.append("a")

    async def waiter(name: str) -> None:
        async with gate.slot():
            order.append(name)

    a = asyncio.create_task(holder())
    await _settle()
    b = asyncio.create_task(waiter("b"))
    await _settle()
    release_a.set()
    # `c` arrives in the same tick the slot is handed to `b`: it must queue.
    await _settle(1)
    c = asyncio.create_task(waiter("c"))
    await asyncio.gather(a, b, c)
    assert order == ["a", "b", "c"]
    assert gate.free == 1


async def test_a_cancelled_waiter_holds_nothing_and_the_next_one_proceeds():
    gate = FifoGate(1)
    order: list[str] = []
    release_a = asyncio.Event()

    async def holder() -> None:
        async with gate.slot():
            await release_a.wait()
            order.append("a")

    async def waiter(name: str) -> None:
        async with gate.slot():
            order.append(name)

    a = asyncio.create_task(holder())
    await _settle()
    b = asyncio.create_task(waiter("b"))
    await _settle()
    c = asyncio.create_task(waiter("c"))
    await _settle()
    assert gate.waiting == 2

    b.cancel()
    with pytest.raises(asyncio.CancelledError):
        await b
    assert gate.waiting == 1
    assert gate.free == 0  # the cancelled waiter freed nothing it did not hold
    assert not c.done()

    release_a.set()
    await asyncio.gather(a, c)
    assert order == ["a", "c"]
    assert gate.free == 1 and gate.waiting == 0


async def test_a_waiter_cancelled_in_the_instant_the_slot_is_handed_to_it_passes_it_on():
    gate = FifoGate(1)
    order: list[str] = []
    release_a = asyncio.Event()

    async def holder() -> None:
        async with gate.slot():
            await release_a.wait()

    async def waiter(name: str) -> None:
        async with gate.slot():
            order.append(name)

    a = asyncio.create_task(holder())
    await _settle()
    b = asyncio.create_task(waiter("b"))
    await _settle()
    c = asyncio.create_task(waiter("c"))
    await _settle()

    release_a.set()
    await _settle(1)  # a releases: the slot is handed to b's future, b has not run yet
    b.cancel()
    with pytest.raises(asyncio.CancelledError):
        await b
    await asyncio.wait_for(asyncio.gather(a, c), timeout=2)
    assert order == ["c"]
    assert gate.free == 1 and gate.waiting == 0


async def test_a_waiter_whose_client_went_away_leaves_the_line_without_the_slot():
    gate = FifoGate(1, poll_seconds=0.01)
    release_a = asyncio.Event()

    async def holder() -> None:
        async with gate.slot():
            await release_a.wait()

    async def gone() -> bool:
        return True

    a = asyncio.create_task(holder())
    await _settle()
    with pytest.raises(Disconnected):
        async with gate.slot(gone):
            pytest.fail("a disconnected waiter must never enter")
    assert gate.waiting == 0 and gate.free == 0  # a still holds the one slot
    release_a.set()
    await a
    assert gate.free == 1


async def test_a_waiter_with_a_connected_client_just_waits():
    gate = FifoGate(1, poll_seconds=0.01)
    release_a = asyncio.Event()
    polls = 0

    async def holder() -> None:
        async with gate.slot():
            await release_a.wait()

    async def still_here() -> bool:
        nonlocal polls
        polls += 1
        return False

    async def waiter() -> str:
        async with gate.slot(still_here):
            return "ran"

    a = asyncio.create_task(holder())
    await _settle()
    w = asyncio.create_task(waiter())
    await asyncio.sleep(0.08)  # long enough for several polls: no timeout, no giving up
    assert not w.done() and polls >= 2
    release_a.set()
    assert await w == "ran"
    await a
    assert gate.free == 1


async def test_the_slot_is_freed_when_the_body_raises():
    gate = FifoGate(1)
    with pytest.raises(RuntimeError):
        async with gate.slot():
            raise RuntimeError("proxy blew up")
    assert gate.free == 1


# ── the routes go through it ─────────────────────────────────────────────────


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(speech.settings, "llm_base_url", "http://litellm.test:4000")
    monkeypatch.setattr(speech.settings, "llm_api_key", "sk-virtual")
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://cc")


def _stub_upstream(monkeypatch, handler):
    real = httpx.AsyncClient

    def factory(**kw):
        kw["transport"] = httpx.MockTransport(handler)
        return real(**kw)

    monkeypatch.setattr(speech.httpx, "AsyncClient", factory)


@pytest.fixture(autouse=True)
def _clean_gates():
    yield
    # a test that failed mid-flight must not poison the module-level lines
    for gate in (TTS_GATE, STT_GATE):
        assert gate.waiting == 0
        assert gate.free == 1


async def test_concurrent_read_aloud_calls_reach_the_proxy_one_at_a_time_in_order(client, monkeypatch):
    inside = 0
    peak = 0
    arrived: list[str] = []

    async def handler(req: httpx.Request) -> httpx.Response:
        nonlocal inside, peak
        inside += 1
        peak = max(peak, inside)
        arrived.append(json.loads(req.content)["input"])
        await asyncio.sleep(0.03)
        inside -= 1
        return httpx.Response(200, content=b"ID3mp3", headers={"content-type": "audio/mpeg"})

    _stub_upstream(monkeypatch, handler)

    async def speak(c: httpx.AsyncClient, text: str) -> httpx.Response:
        return await c.post("/api/tts", json={"text": text})

    async with client:
        tasks = []
        for n in range(5):
            tasks.append(asyncio.create_task(speak(client, f"chunk {n}")))
            await asyncio.sleep(0.01)  # distinct arrival times
        results = await asyncio.gather(*tasks)
    assert [r.status_code for r in results] == [200] * 5
    assert peak == 1
    assert arrived == [f"chunk {n}" for n in range(5)]


async def test_concurrent_transcriptions_are_serialised_too(client, monkeypatch):
    inside = 0
    peak = 0

    async def handler(req: httpx.Request) -> httpx.Response:
        nonlocal inside, peak
        inside += 1
        peak = max(peak, inside)
        await asyncio.sleep(0.03)
        inside -= 1
        return httpx.Response(200, json={"text": "hello"})

    _stub_upstream(monkeypatch, handler)

    async def hear(c: httpx.AsyncClient) -> httpx.Response:
        return await c.post("/api/transcribe", files={"file": ("a.webm", b"RIFFxxxx", "audio/webm")})

    async with client:
        results = await asyncio.gather(*(hear(client) for _ in range(3)))
    assert [r.json() for r in results] == [{"text": "hello"}] * 3
    assert peak == 1


async def test_speech_and_transcription_are_separate_lines(client, monkeypatch):
    """The two aliases are separate deployments: a long synthesis must not
    hold up a transcription."""
    release = asyncio.Event()

    async def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/speech"):
            await release.wait()
            return httpx.Response(200, content=b"ID3mp3", headers={"content-type": "audio/mpeg"})
        return httpx.Response(200, json={"text": "heard"})

    _stub_upstream(monkeypatch, handler)
    async with client:
        speaking = asyncio.create_task(client.post("/api/tts", json={"text": "long"}))
        await _settle(20)
        heard = await asyncio.wait_for(
            client.post("/api/transcribe", files={"file": ("a.webm", b"RIFFxxxx", "audio/webm")}), timeout=2
        )
        assert heard.json() == {"text": "heard"}
        release.set()
        assert (await speaking).status_code == 200


async def test_a_proxy_429_surfaces_as_the_routes_existing_502_and_frees_the_line(client, monkeypatch):
    body = (
        '{"error":{"message":"litellm.RateLimitError: Deployment has all max_parallel_requests '
        'slots in use.","type":"throttling_error","code":"429"}}'
    )
    calls = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, text=body)

    _stub_upstream(monkeypatch, handler)
    async with client:
        r = await client.post("/api/tts", json={"text": "x"})
        again = await client.post("/api/tts", json={"text": "x"})
    assert r.status_code == 502
    assert r.json()["error"] == "cc-tts: HTTP 429"
    assert "max_parallel_requests" in r.json()["detail"]
    assert calls == 2  # surfaced as it is, and never retried by the application
    assert again.status_code == 502


async def test_a_client_that_leaves_the_line_never_reaches_the_proxy(monkeypatch):
    monkeypatch.setattr(speech.settings, "llm_base_url", "http://litellm.test:4000")
    monkeypatch.setattr(speech.settings, "llm_api_key", "sk-virtual")
    monkeypatch.setattr(TTS_GATE, "_poll", 0.01)
    reached: list[str] = []
    _stub_upstream(monkeypatch, lambda req: reached.append("hit") or httpx.Response(200, content=b"x"))

    class GoneRequest:
        async def is_disconnected(self) -> bool:
            return True

    release = asyncio.Event()

    async def holder() -> None:
        async with TTS_GATE.slot():
            await release.wait()

    a = asyncio.create_task(holder())
    await _settle()
    resp = await speech.tts(speech.TTSRequest(text="waiting in line"), GoneRequest())  # type: ignore[arg-type]
    assert resp.status_code == 499
    assert reached == []
    assert TTS_GATE.waiting == 0 and TTS_GATE.free == 0
    release.set()
    await a


# ── the other caller of the two aliases that is ours ─────────────────────────


async def test_the_model_probe_takes_its_place_in_the_same_line(monkeypatch):
    monkeypatch.setattr(litellm.settings, "llm_proxy_base_url", "http://litellm.test:4000")
    monkeypatch.setattr(litellm.settings, "llm_proxy_admin_key", "sk-admin")
    posted: list[str] = []

    async def fake_call(method, path, json_body=None, auth_key=None, timeout=30, files=None):
        if method == "GET":
            return httpx.Response(200, json={"data": [{
                "model_name": "cc-tts",
                "model_info": {"mode": "audio_speech"},
                "litellm_params": {"model": "openai/kokoro"},
            }]})
        posted.append(path)
        return httpx.Response(200, content=b"ID3")

    monkeypatch.setattr(litellm, "_call", fake_call)
    release = asyncio.Event()

    async def holder() -> None:
        async with TTS_GATE.slot():
            await release.wait()

    a = asyncio.create_task(holder())
    await _settle()
    probe = asyncio.create_task(litellm.probe_model("cc-tts"))
    await asyncio.sleep(0.05)
    assert posted == [] and not probe.done()  # queued behind the job in flight
    release.set()
    out = await asyncio.wait_for(probe, timeout=2)
    await a
    assert posted == ["/v1/audio/speech"]
    assert out["ok"] is True
