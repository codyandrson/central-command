"""Speech for the single-server cockpit: /api/tts and /api/transcribe.

The Nerve cockpit's voice features live in its Node server (web/server/routes/
tts.ts, transcribe.ts). The single-node profile never runs that server — the
API serves web/dist itself (app.py) — so without these routes voice input and
read-aloud silently 404 there. These are the SAME wire shapes the cockpit
already speaks, forwarded to the deployment's own LiteLLM through the two
speech aliases: ``cc-tts`` (/v1/audio/speech) and ``cc-stt``
(/v1/audio/transcriptions). Nothing here phones an external provider; which
engine sits behind an alias is the operator's registration (a self-hosted
cc-speech pod, or the operator's own Whisper-convention models).

One job at a time, in order: both aliases carry ``max_parallel_requests: 1``
(DL-220) and LiteLLM 1.110 answers a second concurrent request with HTTP 429
where 1.98 made it wait, so each route takes its place in a FIFO line
(``integrations/speech_gate``) before it calls the proxy. A 429 that still
arrives is the same 502 as any other proxy refusal; this module retries nothing.

The spine's virtual key must be scoped to the two aliases as well as
cc-default — mint-keys.sh / setup.sh app do that; a key minted before this
release needs re-minting (FORCE=1) or the aliases added in the proxy UI.

Which speech model each route uses is the operator's choice in Settings >
Audio (`GET`/`PUT /api/speech/models`): a model LiteLLM lists with mode
``audio_speech`` (read-aloud) or ``audio_transcription`` (dictation). It is
stored in ``app_setting`` (``speech_models``); with nothing stored the aliases
are used, so an install that has only the aliases behaves as before. On the k3s
profile the cockpit's Node server answers these routes itself and stores the
choice in its tts-config.json; it reads the LISTS from the GET here.
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

# request.form() yields Starlette's UploadFile; FastAPI's is a subclass, so
# an isinstance against the FastAPI one rejects every real upload.
from starlette.datastructures import UploadFile

from central_command.config import settings
from central_command.db import repo
from central_command.integrations import http as http_client
from central_command.integrations.speech_gate import STT_GATE, TTS_GATE, Disconnected

router = APIRouter(prefix="/api")

TTS_ALIAS = "cc-tts"
STT_ALIAS = "cc-stt"
# Mirrors the Node route's limits (tts.ts MAX_TEXT_LENGTH, config.limits.transcribe).
MAX_TTS_CHARS = 5000
MAX_AUDIO_BYTES = 12 * 1024 * 1024


class TTSRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TTS_CHARS)
    voice: str | None = None
    # Accepted for wire compatibility with the cockpit; every provider routes
    # through the alias here, and `model` is the alias — never a client choice.
    provider: str | None = None
    model: str | None = None


# app_setting key for the operator's speech-model choice: {"tts": id, "stt": id}.
SPEECH_SETTING = "speech_models"
# Same shape the Node route accepts (web/server/lib/tts-config.ts SPEECH_MODEL_PATTERN).
_MODEL_ID_MAX = 128


async def _chosen() -> dict[str, str]:
    """The models the routes use now: the stored choice, else the aliases."""
    stored = await repo.get_app_setting(SPEECH_SETTING, {})
    stored = stored if isinstance(stored, dict) else {}
    return {
        "tts": str(stored.get("tts") or "") or TTS_ALIAS,
        "stt": str(stored.get("stt") or "") or STT_ALIAS,
    }


async def _catalog() -> tuple[dict[str, list[str]], str | None]:
    """The speech lists from LiteLLM, or empty lists and the reason they are missing."""
    from central_command.integrations.litellm import LiteLLMError, speech_model_ids

    try:
        lists = await speech_model_ids()
    except LiteLLMError as exc:
        return {"audio_speech": [], "audio_transcription": []}, str(exc)
    return lists, None


async def _speech_models_body() -> dict:
    chosen = await _chosen()
    lists, error = await _catalog()
    return {
        "tts": {"selected": chosen["tts"], "default": TTS_ALIAS, "models": lists["audio_speech"]},
        "stt": {
            "selected": chosen["stt"],
            "default": STT_ALIAS,
            "models": lists["audio_transcription"],
        },
        "error": error,
    }


class SpeechModelsIn(BaseModel):
    # Omitted: unchanged. Null or "": back to the alias.
    tts: str | None = Field(default=None, max_length=_MODEL_ID_MAX)
    stt: str | None = Field(default=None, max_length=_MODEL_ID_MAX)


@router.get("/speech/models")
async def speech_models() -> dict:
    """The speech models LiteLLM serves, by mode, and the one each route uses.

    Wire shape (the cockpit's `useSpeechModels` declares it; pinned by
    tests/test_speech_models.py): `{tts: {selected, default, models}, stt: {...},
    error}`. A proxy that cannot be read answers 200 with empty lists and the
    reason in `error`, so Settings shows the current model and says the list is
    unavailable instead of failing."""
    return await _speech_models_body()


@router.put("/speech/models")
async def set_speech_models(body: SpeechModelsIn) -> dict:
    """Choose the read-aloud and/or dictation model. A model must be in LiteLLM's
    list for its mode (422 otherwise; 502 when the list cannot be read), except
    the alias itself, which is always accepted: choosing it is going back to
    the default."""
    fields = body.model_fields_set
    if not fields:
        raise HTTPException(422, "name tts and/or stt")
    stored = await repo.get_app_setting(SPEECH_SETTING, {})
    stored = dict(stored) if isinstance(stored, dict) else {}
    lists: dict[str, list[str]] | None = None
    for key, mode, alias in (
        ("tts", "audio_speech", TTS_ALIAS),
        ("stt", "audio_transcription", STT_ALIAS),
    ):
        if key not in fields:
            continue
        value = (getattr(body, key) or "").strip()
        if not value or value == alias:
            stored.pop(key, None)
            continue
        if lists is None:
            lists, error = await _catalog()
            if error:
                raise HTTPException(502, f"cannot check the model right now — {error}")
        if value not in lists[mode]:
            raise HTTPException(
                422,
                f"{value!r} is not a {mode} model LiteLLM serves — choose one of: "
                f"{', '.join(lists[mode]) or '(none)'}",
            )
        stored[key] = value
    await repo.set_app_setting(SPEECH_SETTING, stored)
    return await _speech_models_body()


def _base() -> str:
    return (settings.llm_base_url or "").rstrip("/")


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.llm_api_key}"}


def _unconfigured() -> JSONResponse | None:
    if not settings.llm_base_url or not settings.llm_api_key:
        return JSONResponse(
            {"error": "CC_LLM_BASE_URL / CC_LLM_API_KEY are not set"}, status_code=503
        )
    return None


def _client_gone() -> JSONResponse:
    # The caller left while it waited its turn; nothing was sent to the proxy.
    # 499 is the "client closed request" status: nobody reads it, the log does.
    return JSONResponse({"error": "client disconnected before its turn"}, status_code=499)


@router.post("/tts")
async def tts(req: TTSRequest, request: Request) -> Response:
    if (err := _unconfigured()) is not None:
        return err
    if not req.text.strip():
        return JSONResponse({"error": "Text cannot be empty or whitespace"}, status_code=400)
    try:
        async with TTS_GATE.slot(request.is_disconnected):
            # Read inside the slot: the gate's order is arrival order, and a
            # read before it would let a slower read reorder the line.
            model = (await _chosen())["tts"]
            body: dict = {
                "model": model,
                "voice": req.voice or settings.tts_voice,
                "input": req.text,
                "response_format": "mp3",
            }
            async with httpx.AsyncClient(timeout=120, **http_client.client_kwargs()) as client:
                resp = await client.post(
                    f"{_base()}/v1/audio/speech", headers=_headers(), json=body
                )
    except Disconnected:
        return _client_gone()
    if resp.status_code != 200:
        return JSONResponse(
            {"error": f"{model}: HTTP {resp.status_code}", "detail": resp.text[:500]},
            status_code=502,
        )
    return Response(content=resp.content, media_type=resp.headers.get("content-type", "audio/mpeg"))


@router.get("/tts/config")
async def tts_config() -> dict:
    # The cockpit's Settings > Audio reads this shape (web/server/lib/tts-config.ts).
    return {
        "defaultProvider": "openai",
        "openai": {
            "model": (await _chosen())["tts"],
            "voice": settings.tts_voice,
            "instructions": "",
        },
    }


@router.post("/transcribe")
async def transcribe(request: Request) -> Response:
    if (err := _unconfigured()) is not None:
        return err
    form = await request.form()
    file = form.get("file")
    if not isinstance(file, UploadFile):
        return JSONResponse({"error": "No file found in request"}, status_code=400)
    audio = await file.read()
    if len(audio) > MAX_AUDIO_BYTES:
        return JSONResponse(
            {"error": f"File too large (max {MAX_AUDIO_BYTES // 1024 // 1024}MB)"}, status_code=413
        )
    if not audio:
        return JSONResponse({"error": "Empty audio"}, status_code=400)
    language = form.get("language")
    try:
        async with STT_GATE.slot(request.is_disconnected):
            model = (await _chosen())["stt"]  # inside the slot, as in tts()
            data: dict[str, str] = {"model": model}
            if isinstance(language, str) and language:
                data["language"] = language
            async with httpx.AsyncClient(timeout=180, **http_client.client_kwargs()) as client:
                resp = await client.post(
                    f"{_base()}/v1/audio/transcriptions",
                    headers=_headers(),
                    data=data,
                    files={
                        "file": (
                            file.filename or "audio.webm",
                            audio,
                            file.content_type or "application/octet-stream",
                        )
                    },
                )
    except Disconnected:
        return _client_gone()
    if resp.status_code != 200:
        return JSONResponse(
            {"error": f"{model}: HTTP {resp.status_code}", "detail": resp.text[:500]},
            status_code=502,
        )
    return JSONResponse({"text": resp.json().get("text", "")})


@router.get("/transcribe/config")
async def transcribe_config() -> dict:
    # Same shape as the Node route (transcribe.ts GET): provider + model + readiness.
    return {"provider": "openai", "model": (await _chosen())["stt"], "modelReady": True}
