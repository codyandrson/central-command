"""Operator attachments — the ONE seam where a file becomes something an agent
can read, or the send is refused.

THE INVARIANT THIS MODULE EXISTS TO ENFORCE:

    An attachment either reaches the agent as content, or the send fails
    loudly. There is no third state.

Everything here runs BEFORE a turn starts, so a refusal mutates nothing: no
user message in the transcript, no session row touched, no lifecycle frames
owed to the cockpit (a turn that starts and then dies owes the spinner an END
frame — the 2026-07-23 "still thinking" incident — and the cheapest way to owe
nothing is to fail before the turn exists). The operator keeps their draft and
fixes the problem: swap the file, convert it, change the model, or drop it.

WHY EXTRACTION TO TEXT AND NOT NATIVE MULTIMODAL BLOCKS (measured 2026-08-11
against the real proxy, `cc-default` -> qwen3.6-27b on llama.cpp):

  - an Anthropic `image` block   -> HTTP 500, "image input is not supported -
    hint: if this is unexpected, you may need to provide the mmproj"
  - an Anthropic `document` block -> **SILENTLY DROPPED**. 200 OK, and the
    model cheerfully answers "I don't see any image, text, or file attached".

The second is the whole reason this module exists. LiteLLM translates the
blocks faithfully — the transport is fine — but a text-only model discards the
document and answers anyway, which is a confident answer about a file nobody
read. Extraction to text is model-agnostic instead: it works on the local model
today, keeps working when the model changes, and needs no provider beta header.

WHAT DECIDES: capability is DECLARED, not guessed. `litellm.model_accepts_images`
asks the proxy what the session's model accepts. Unknown reads as NO. Because
that answer is per-model, switching the cockpit's model dropdown re-runs this
verdict — the same file refused under a text-only model becomes valid under a
vision-capable one with no re-staging and no code change here.

THE TRAP THAT MOTIVATES `_extract` (verified, same date): markitdown converts an
image-only PDF — i.e. what a SCAN is — WITHOUT RAISING, and returns zero
characters. `try: convert() except: refuse` therefore ships exactly the silent
failure this module is written to prevent. The check is on the OUTPUT, never on
the exception and never on the format registry (markitdown registers an `Image`
converter that yields '' without a vision model, so "the format is supported"
is not evidence that we got anything).
"""

from __future__ import annotations

import base64
import binascii
import io

from central_command.config import settings
from central_command.integrations import http as http_client

# Same round number as the skills-library file import
# (`routes.MAX_IMPORT_FILE_BYTES`) — an operator's one-off reference document,
# not a measured limit. Kept in step with it on purpose: two different caps for
# "a file the operator hands over" would be a difference with no reason behind it.
# The transport must stay wider than this cap or the refusal never fires:
# uvicorn's --ws-max-size (cc-uvicorn.service, 96 MB) has to exceed this
# limit's base64-inflated frame (~4/3×), else the socket closes with a raw
# 1009 before this check sees the bytes (found 2026-08-20).
MAX_ATTACHMENT_BYTES = 50 * 1024 * 1024

# ponytail: ~200k chars is roughly 50k tokens against a 262144-token local
# context — room for a large document without crowding out the conversation.
# Truncation is VISIBLE (see `_frame`), which is the only property that matters
# here; tune the number when a real document needs more.
MAX_EXTRACTED_CHARS = 200_000

IMAGE_PREFIX = "image/"

# Scanned-PDF OCR fallback (2026-08-19). Pages are transcribed through the
# SESSION'S OWN vision model — content the model could read anyway, so the
# gateway stays model-agnostic in the sense that matters: nothing is delivered
# that the turn's model did not itself produce from the bytes. 12 pages ×
# ~6s/page measured (mmproj on CPU) keeps the synchronous send under ~90s;
# beyond the cap the remainder is visibly truncated, same rule as
# MAX_EXTRACTED_CHARS.
MAX_OCR_PAGES = 12
OCR_PROMPT = (
    "Transcribe all text in this document page exactly, preserving layout. "
    "Output only the transcription; if the page holds no text, output exactly "
    "<NO_TEXT> and nothing else."
)
# The blank-page contract is a sentinel WE define, not an absence the model
# improvises. "Output nothing" produced ad-hoc placeholders ("[Empty
# transcription]") that are truthy and sailed past the empty-output check as
# page content (found in the 2026-08-25 sweep) — a guard can only test what
# it controls.
OCR_EMPTY_SENTINEL = "<NO_TEXT>"


class AttachmentRefused(Exception):
    """One attachment could not be delivered, so the whole send is refused.

    Carries a `code` for the cockpit and a message written for the OPERATOR —
    it names the file and what to do about it, because the operator is the one
    who can fix it (describe it, convert it, switch models, drop it).
    """

    def __init__(self, code: str, message: str, filename: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.filename = filename


def _decode(att: dict) -> tuple[str, str, bytes]:
    """(filename, media_type, bytes) from one wire attachment, or refuse."""
    name = str(att.get("name") or att.get("filename") or "attachment").strip() or "attachment"
    media_type = str(att.get("mimeType") or att.get("media_type") or "").strip().lower()
    raw = att.get("content") or att.get("data") or ""
    if not isinstance(raw, str) or not raw:
        raise AttachmentRefused(
            "attachment_empty", f"{name!r} arrived with no content.", name
        )
    try:
        data = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise AttachmentRefused(
            "attachment_undecodable",
            f"{name!r} could not be decoded ({exc}). Try attaching it again.",
            name,
        ) from exc
    if not data:
        raise AttachmentRefused(
            "attachment_empty", f"{name!r} is empty (0 bytes).", name
        )
    if len(data) > MAX_ATTACHMENT_BYTES:
        raise AttachmentRefused(
            "attachment_too_large",
            f"{name!r} is {len(data):,} bytes, over the "
            f"{MAX_ATTACHMENT_BYTES:,}-byte attachment limit.",
            name,
        )
    return name, media_type, data


def _extract(name: str, data: bytes) -> tuple[str, bool]:
    """Bytes -> (markdown, truncated). Refuses rather than returning nothing.

    An empty result is a FAILURE here, not an empty document: markitdown
    returns '' for a scanned (image-only) PDF without raising, and passing that
    on would hand the agent a document that says nothing while the operator
    believes it was delivered. Extraction itself lives in `ingest/extract.py`
    (shared with the catalog watcher); this function keeps the gate's own
    refuse-on-empty contract.
    """
    from central_command.ingest.extract import extract_text_or_raise

    try:
        text = extract_text_or_raise(name, data)
    except Exception as exc:  # a malformed file must not 500 the whole send
        raise AttachmentRefused(
            "attachment_unreadable",
            f"could not read {name!r}: {exc}. Convert it to PDF, DOCX, XLSX, "
            "PPTX, CSV, HTML or plain text and try again.",
            name,
        ) from exc

    if not text:
        # ponytail: catches the 0-char scan exactly, which is the measured case.
        # A scan whose only text layer is a stray page number would still slip
        # through as "content" — the fix for that is OCR or a vision model, not
        # a bigger threshold guessed at here.
        raise AttachmentRefused(
            "attachment_no_text",
            f"{name!r} converted but contains no readable text — it is most "
            "likely a scan or an image-only PDF. Nothing would reach the agent, "
            "so this send was refused rather than answered from an empty "
            "document. Attach a text-based version, or paste the relevant part.",
            name,
        )
    if len(text) > MAX_EXTRACTED_CHARS:
        return text[:MAX_EXTRACTED_CHARS], True
    return text, False


def _frame(
    name: str,
    media_type: str,
    size: int,
    text: str,
    truncated: bool,
    extractor: str = "markitdown",
) -> str:
    """Wrap extracted text so the agent cannot mistake it for the operator's own
    words, and can see exactly what it is holding — including that it is holding
    only part of it. A truncation the agent cannot see is the same silent
    failure as a dropped file, one layer along."""
    head = (
        f'<operator-attachment name="{name}" type="{media_type or "unknown"}" '
        f'bytes="{size}" extracted-by="{extractor}"'
    )
    if truncated:
        head += f' truncated="true" delivered-chars="{len(text)}"'
    return f"{head}>\n{text}\n</operator-attachment>"


async def _transcribe_page(model_id: str, png: bytes) -> str:
    """One page image -> its text, through the session's model via the agents'
    own LiteLLM door. Raises on transport/HTTP failure; the caller decides
    that failure means 'the plain refusal stands', never a partial delivery."""
    import httpx

    base = (settings.llm_base_url or "").rstrip("/")
    async with httpx.AsyncClient(timeout=180, **http_client.client_kwargs()) as client:
        resp = await client.post(
            f"{base}/v1/chat/completions",
            headers={"Authorization": f"Bearer {settings.llm_api_key}"},
            json={
                "model": model_id,
                "max_tokens": 4096,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": OCR_PROMPT},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": "data:image/png;base64,"
                                    + base64.b64encode(png).decode()
                                },
                            },
                        ],
                    }
                ],
            },
        )
        resp.raise_for_status()
        return _normalize_ocr(resp.json()["choices"][0]["message"]["content"])


def _normalize_ocr(text: str | None) -> str:
    """A blank page is the sentinel (or nothing at all) — either way, empty.
    Anything else is transcription and passes through verbatim."""
    t = (text or "").strip()
    return "" if t == OCR_EMPTY_SENTINEL else t


async def _ocr_scanned_pdf(
    name: str, data: bytes, model_id: str | None
) -> tuple[str, bool] | None:
    """The scanned-PDF fallback: render pages, transcribe each through the
    session's model. Returns the text, or None when the path is unavailable or
    produced nothing — None means the caller's `attachment_no_text` refusal
    stands unchanged, so this can only ADD a delivery, never soften a refusal.

    FAILS CLOSED on every uncertainty: demo mode, no resolved model, a model
    that has not DECLARED vision (None is "nobody said", never "yes"), an
    unreachable proxy, bytes pypdfium2 cannot open, or a transcription error
    mid-document — a half-OCR'd document delivered as whole is the same silent
    failure this module exists to prevent.
    """
    if settings.demo_mode or not model_id:
        return None

    from central_command.integrations.litellm import LiteLLMError, model_accepts_images

    try:
        if not await model_accepts_images(model_id):
            return None
    except LiteLLMError:
        return None

    try:
        import pypdfium2

        pdf = pypdfium2.PdfDocument(data)
    except Exception:
        return None  # not a PDF pypdfium2 can open — the refusal stands

    try:
        total = len(pdf)
        pages: list[str] = []
        for i in range(min(total, MAX_OCR_PAGES)):
            bitmap = pdf[i].render(scale=2.0)
            buf = io.BytesIO()
            bitmap.to_pil().save(buf, format="PNG")
            try:
                text = await _transcribe_page(model_id, buf.getvalue())
            except Exception:
                return None
            if text:
                pages.append(f"--- page {i + 1} of {total} ---\n{text}")
        pages_truncated = total > MAX_OCR_PAGES
        if pages_truncated:
            pages.append(
                f"[pages {MAX_OCR_PAGES + 1}-{total} not transcribed — "
                f"{MAX_OCR_PAGES}-page OCR limit]"
            )
    finally:
        pdf.close()

    joined = "\n\n".join(pages).strip()
    return (joined, pages_truncated) if joined else None


async def _images_deliverable(model_id: str | None) -> tuple[bool, str]:
    """(deliverable, why_not) for image attachments on this session's model.

    Two blockers, reported in the order the operator would hit them, because a
    refusal that names the wrong cause sends them to fix the wrong thing.
    """
    if settings.demo_mode:
        return False, (
            "demo mode runs deterministic stand-in models that cannot read "
            "images. Attach a document, or describe the image in text."
        )
    if not model_id:
        return False, "no model is resolved for this session, so nothing can read an image."

    from central_command.integrations.litellm import LiteLLMError, model_accepts_images

    try:
        accepts = await model_accepts_images(model_id)
    except LiteLLMError as exc:
        # "We cannot tell" is not "yes". Refuse, and say which it was.
        return False, (
            f"could not check whether {model_id!r} accepts images — {exc}. "
            "The send was refused rather than risk the image being dropped."
        )
    if not accepts:
        return False, (
            f"the model this session runs on ({model_id}) cannot read images. "
            "Switch to a vision-capable model in the session's model picker, "
            "or describe the image in text."
        )
    # Reached only once a vision-capable model is configured. Delivering the
    # bytes means passing BinaryContent down the prompt path (converse -> run),
    # which is a separate change; saying so is the honest refusal, and it is
    # still a refusal rather than a drop.
    return False, (
        f"{model_id} can read images, but Central Command does not deliver image "
        "bytes to agents yet. Describe the image in text for now."
    )


async def prepare(
    attachments: list | None, *, model_id: str | None
) -> tuple[str, list[dict]]:
    """Validate EVERY attachment and return (markdown_block, manifest).

    Raises `AttachmentRefused` on the first failure — all-or-nothing per send.
    Delivering the three readable files and quietly dropping the fourth is the
    partial-success failure this module refuses to have: the operator would be
    told nothing and the agent would answer as though it had everything.

    The manifest is provenance for the event log: what was attached, how big,
    and whether it was truncated. It never contains file bytes.
    """
    if not attachments:
        return "", []

    blocks: list[str] = []
    manifest: list[dict] = []
    for att in attachments:
        if not isinstance(att, dict):
            raise AttachmentRefused("attachment_malformed", "malformed attachment payload.")
        name, media_type, data = _decode(att)

        if media_type.startswith(IMAGE_PREFIX):
            ok, why = await _images_deliverable(model_id)
            if not ok:
                raise AttachmentRefused(
                    "attachment_images_unsupported",
                    f"{name!r} is an image and cannot be delivered: {why}",
                    name,
                )

        extractor = "markitdown"
        try:
            text, truncated = _extract(name, data)
        except AttachmentRefused as refusal:
            # The scan case, and only the scan case: markitdown converted the
            # file and found no text. Content decides, not the filename —
            # _ocr_scanned_pdf returns None for anything pypdfium2 cannot open.
            if refusal.code != "attachment_no_text":
                raise
            ocr = await _ocr_scanned_pdf(name, data, model_id)
            if not ocr:
                raise
            text, truncated = ocr
            extractor = f"vision-ocr({model_id})"
            if len(text) > MAX_EXTRACTED_CHARS:
                text, truncated = text[:MAX_EXTRACTED_CHARS], True
        blocks.append(_frame(name, media_type, len(data), text, truncated, extractor))
        manifest.append(
            {
                "name": name,
                "media_type": media_type or None,
                "bytes": len(data),
                "extracted_chars": len(text),
                "truncated": truncated,
                "extractor": extractor,
            }
        )

    return "\n\n".join(blocks), manifest


async def session_model_id(session_id: str | None, agent_id: str | None) -> str | None:
    """The model alias this send would run on — the SAME precedence
    `runtime.models.resolve_session_model` applies (per-session override, then
    the per-agent override, then the default), so the capability we check is
    the capability the turn will actually have.

    Duplicated rather than imported because `runtime/` may not be reached into
    for a gateway-tier decision; `test_attachments.py` pins the two in step.
    """
    if settings.demo_mode:
        return None
    if session_id:
        from central_command.db import repo

        override = await repo.get_session_model_override(session_id)
        if override:
            return override.split(":", 1)[-1]
    return (settings.agent_model(agent_id) or settings.default_model).split(":", 1)[-1]
