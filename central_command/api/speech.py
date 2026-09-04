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

The spine's virtual key must be scoped to the two aliases as well as
cc-default — mint-keys.sh / setup.sh app do that; a key minted before this
release needs re-minting (FORCE=1) or the aliases added in the proxy UI.
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
# request.form() yields Starlette's UploadFile; FastAPI's is a subclass, so
# an isinstance against the FastAPI one rejects every real upload.
from starlette.datastructures import UploadFile

from central_command.config import settings
from central_command.integrations import http as http_client

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


def _base() -> str:
    return (settings.llm_base_url or "").rstrip("/")


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.llm_api_key}"}


def _unconfigured() -> JSONResponse | None:
    if not settings.llm_base_url or not settings.llm_api_key:
        return JSONResponse({"error": "CC_LLM_BASE_URL / CC_LLM_API_KEY are not set"}, status_code=503)
    return None


@router.post("/tts")
async def tts(req: TTSRequest) -> Response:
    if (err := _unconfigured()) is not None:
        return err
    if not req.text.strip():
        return JSONResponse({"error": "Text cannot be empty or whitespace"}, status_code=400)
    body: dict = {"model": TTS_ALIAS, "voice": req.voice or settings.tts_voice, "input": req.text, "response_format": "mp3"}
    async with httpx.AsyncClient(timeout=120, **http_client.client_kwargs()) as client:
        resp = await client.post(f"{_base()}/v1/audio/speech", headers=_headers(), json=body)
    if resp.status_code != 200:
        return JSONResponse({"error": f"{TTS_ALIAS}: HTTP {resp.status_code}", "detail": resp.text[:500]}, status_code=502)
    return Response(content=resp.content, media_type=resp.headers.get("content-type", "audio/mpeg"))


@router.get("/tts/config")
async def tts_config() -> dict:
    # The cockpit's Settings > Audio reads this shape (web/server/lib/tts-config.ts).
    return {
        "defaultProvider": "openai",
        "openai": {"model": TTS_ALIAS, "voice": settings.tts_voice, "instructions": ""},
        "qwen": {"mode": "", "language": "", "speaker": "", "voiceDescription": "", "styleInstruction": ""},
        "edge": {"voice": ""},
        "xiaomi": {"model": "", "voice": "", "style": ""},
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
        return JSONResponse({"error": f"File too large (max {MAX_AUDIO_BYTES // 1024 // 1024}MB)"}, status_code=413)
    if not audio:
        return JSONResponse({"error": "Empty audio"}, status_code=400)
    data: dict[str, str] = {"model": STT_ALIAS}
    language = form.get("language")
    if isinstance(language, str) and language:
        data["language"] = language
    async with httpx.AsyncClient(timeout=180, **http_client.client_kwargs()) as client:
        resp = await client.post(
            f"{_base()}/v1/audio/transcriptions",
            headers=_headers(),
            data=data,
            files={"file": (file.filename or "audio.webm", audio, file.content_type or "application/octet-stream")},
        )
    if resp.status_code != 200:
        return JSONResponse({"error": f"{STT_ALIAS}: HTTP {resp.status_code}", "detail": resp.text[:500]}, status_code=502)
    return JSONResponse({"text": resp.json().get("text", "")})


@router.get("/transcribe/config")
async def transcribe_config() -> dict:
    # Same shape as the Node route (transcribe.ts GET): provider + model + readiness.
    return {"provider": "openai", "model": STT_ALIAS, "modelReady": True}


@router.put("/transcribe/config")
async def transcribe_config_put() -> dict:
    # A browser with a saved 'local' preference pushes it here on mount; there
    # is no local engine in this profile, so the answer is always the alias.
    return await transcribe_config()
