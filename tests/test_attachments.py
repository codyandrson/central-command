"""An attachment reaches the agent as content, or the send fails loudly.

There is no third state, and these tests are what keeps it that way. The cases
that matter are the SILENT ones — a file that converts to nothing, an image on a
text-only model, a partial delivery — because a loud failure was never the risk.

The measured behaviour these guard (2026-08-11, real proxy, `cc-default` ->
the then-current qwen3-coder-next): an Anthropic `document` block is silently dropped and the
model answers "I don't see any file attached", and markitdown converts an
image-only PDF without raising and returns zero characters.
"""

import base64
import io

import pytest

from central_command.api import attachments

# --- fixtures: real files, not mocks -----------------------------------------


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _att(name: str, media_type: str, data: bytes) -> dict:
    return {"name": name, "mimeType": media_type, "content": _b64(data)}


def _scanned_pdf() -> bytes:
    """A PDF whose only page content is an embedded image — i.e. what a SCAN
    looks like. markitdown converts it happily and yields ''."""
    img = b"\xff\xd8\xff\xdb" + b"\x00" * 64 + b"\xff\xd9"
    objs = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        (
            b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]/Resources"
            b"<</XObject<</I0 4 0 R>>>>/Contents 5 0 R>>"
        ),
        b"<</Type/XObject/Subtype/Image/Width 10/Height 10/ColorSpace/DeviceRGB"
        b"/BitsPerComponent 8/Filter/DCTDecode/Length %d>>stream\n" % len(img)
        + img
        + b"\nendstream",
    ]
    content = b"q 200 0 0 200 0 0 cm /I0 Do Q"
    objs.append(b"<</Length %d>>stream\n" % len(content) + content + b"\nendstream")
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offs = []
    for i, o in enumerate(objs, 1):
        offs.append(out.tell())
        out.write(b"%d 0 obj" % i + o + b"endobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1))
    for o in offs:
        out.write(b"%010d 00000 n \n" % o)
    out.write(
        b"trailer<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF" % (len(objs) + 1, xref)
    )
    return out.getvalue()


PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d4944415478da63f8cfc0000003010100b5c0f9ed0000000049454e44ae426082"
)


# --- the happy path still has to work ----------------------------------------


async def test_a_readable_document_reaches_the_agent_as_framed_text():
    block, manifest = await attachments.prepare(
        [_att("notes.txt", "text/plain", b"Renew the Jira licence before 2026-09-01.")],
        model_id="cc-default",
    )
    assert "Renew the Jira licence" in block
    # Framed, so the agent cannot mistake extracted content for the operator's
    # own words, and can see what it is holding.
    assert '<operator-attachment name="notes.txt"' in block
    assert block.rstrip().endswith("</operator-attachment>")
    assert manifest[0]["name"] == "notes.txt"
    assert manifest[0]["truncated"] is False


async def test_no_attachments_is_not_a_refusal():
    assert await attachments.prepare(None, model_id="cc-default") == ("", [])
    assert await attachments.prepare([], model_id="cc-default") == ("", [])


# --- the silent failures, which are the point --------------------------------


async def test_a_scan_is_refused_rather_than_delivered_as_an_empty_document(monkeypatch):
    """THE case this module exists for. markitdown does not raise here — it
    returns ''. Anything that checks only for an exception ships a confident
    answer about a document nobody read.

    demo_mode pinned since the OCR fallback (2026-08-19): with a live .env the
    session model now DECLARES vision, and an unpinned test would OCR this scan
    against the real proxy. Demo mode is the no-fallback world this test is
    about."""
    monkeypatch.setattr(attachments.settings, "demo_mode", True)
    with pytest.raises(attachments.AttachmentRefused) as e:
        await attachments.prepare(
            [_att("contract.pdf", "application/pdf", _scanned_pdf())],
            model_id="cc-default",
        )
    assert e.value.code == "attachment_no_text"
    assert e.value.filename == "contract.pdf"
    # The operator is the one who can fix it, so the message must tell them how.
    assert "scan" in e.value.message.lower()


async def test_markitdown_really_does_return_nothing_for_a_scan():
    """Pins the upstream behaviour the refusal above is built on. If a future
    markitdown starts OCRing scans, this fails and the guard above can be
    revisited deliberately instead of silently becoming dead weight."""
    from markitdown import MarkItDown

    result = MarkItDown().convert_stream(
        io.BytesIO(_scanned_pdf()), file_extension=".pdf"
    )
    assert (result.text_content or "").strip() == ""


# --- the scanned-PDF OCR fallback (2026-08-19) -------------------------------
# A scan is no longer a dead end WHEN the session's model declares vision: pages
# render through pypdfium2 and the model itself transcribes them. Every refusal
# above still stands for the worlds where that path is unavailable.


def _vision_yes(monkeypatch):
    monkeypatch.setattr(attachments.settings, "demo_mode", False)
    from central_command.integrations import litellm

    async def _yes(model_id):
        return True

    monkeypatch.setattr(litellm, "model_accepts_images", _yes)


async def test_a_scan_is_delivered_when_the_session_model_declares_vision(monkeypatch):
    _vision_yes(monkeypatch)
    seen: list[tuple[str, bytes]] = []

    async def _fake_page(model_id, png):
        seen.append((model_id, png[:8]))
        return "INVOICE #4821 — TOTAL DUE: $44.94"

    monkeypatch.setattr(attachments, "_transcribe_page", _fake_page)

    block, manifest = await attachments.prepare(
        [_att("contract.pdf", "application/pdf", _scanned_pdf())],
        model_id="cc-default",
    )
    assert "TOTAL DUE: $44.94" in block
    # The frame names the extractor AND the model, because provenance for OCR'd
    # text is "what read it", not just "that it was read".
    assert 'extracted-by="vision-ocr(cc-default)"' in block
    assert manifest[0]["extractor"] == "vision-ocr(cc-default)"
    # The model saw a RENDERED PAGE (PNG magic), not the raw PDF bytes.
    assert seen == [("cc-default", b"\x89PNG\r\n\x1a\n")]


async def test_ocr_that_produces_nothing_still_refuses(monkeypatch):
    """A vision model that reads a blank scan and says nothing puts us exactly
    back in the empty-document case — the refusal must survive the fallback."""
    _vision_yes(monkeypatch)

    async def _blank(model_id, png):
        return ""

    monkeypatch.setattr(attachments, "_transcribe_page", _blank)

    with pytest.raises(attachments.AttachmentRefused) as e:
        await attachments.prepare(
            [_att("contract.pdf", "application/pdf", _scanned_pdf())],
            model_id="cc-default",
        )
    assert e.value.code == "attachment_no_text"


def test_the_blank_page_sentinel_reads_as_empty_never_as_content():
    """The blank-page contract is OUR sentinel, not a phrasing the model
    improvises — "[Empty transcription]" once sailed past the empty-output
    check as page content (2026-08-25 sweep). The sentinel normalizes to
    empty; real text passes through verbatim."""
    assert attachments._normalize_ocr(attachments.OCR_EMPTY_SENTINEL) == ""
    assert attachments._normalize_ocr(f"  {attachments.OCR_EMPTY_SENTINEL}\n") == ""
    assert attachments._normalize_ocr(None) == ""
    assert attachments._normalize_ocr("Invoice #4471, due July 1") == "Invoice #4471, due July 1"


async def test_a_transcription_error_refuses_rather_than_delivering_half(monkeypatch):
    """A page that fails mid-document must not yield a partial transcript
    delivered as the whole — same rule as the partial-send refusal."""
    _vision_yes(monkeypatch)

    async def _boom(model_id, png):
        raise RuntimeError("proxy fell over")

    monkeypatch.setattr(attachments, "_transcribe_page", _boom)

    with pytest.raises(attachments.AttachmentRefused) as e:
        await attachments.prepare(
            [_att("contract.pdf", "application/pdf", _scanned_pdf())],
            model_id="cc-default",
        )
    assert e.value.code == "attachment_no_text"


async def test_a_scan_on_a_text_only_model_keeps_the_plain_refusal(monkeypatch):
    monkeypatch.setattr(attachments.settings, "demo_mode", False)
    from central_command.integrations import litellm

    async def _no(model_id):
        return False

    monkeypatch.setattr(litellm, "model_accepts_images", _no)

    with pytest.raises(attachments.AttachmentRefused) as e:
        await attachments.prepare(
            [_att("contract.pdf", "application/pdf", _scanned_pdf())],
            model_id="cc-default",
        )
    assert e.value.code == "attachment_no_text"


async def test_an_image_is_refused_when_the_model_cannot_read_it(monkeypatch):
    monkeypatch.setattr(attachments.settings, "demo_mode", False)

    async def _no_vision(model_id):
        return False

    from central_command.integrations import litellm

    monkeypatch.setattr(litellm, "model_accepts_images", _no_vision)

    with pytest.raises(attachments.AttachmentRefused) as e:
        await attachments.prepare(
            [_att("screenshot.png", "image/png", PNG_1PX)], model_id="cc-default"
        )
    assert e.value.code == "attachment_images_unsupported"
    assert "cc-default" in e.value.message


async def test_unknown_capability_refuses_rather_than_assuming_yes(monkeypatch):
    """'We cannot tell' is not 'yes'. An unreachable proxy must refuse, not
    fall through and let the image be dropped downstream."""
    monkeypatch.setattr(attachments.settings, "demo_mode", False)
    from central_command.integrations import litellm

    async def _boom(model_id):
        raise litellm.LiteLLMError("proxy unreachable")

    monkeypatch.setattr(litellm, "model_accepts_images", _boom)

    with pytest.raises(attachments.AttachmentRefused) as e:
        await attachments.prepare(
            [_att("shot.png", "image/png", PNG_1PX)], model_id="cc-default"
        )
    assert e.value.code == "attachment_images_unsupported"
    assert "could not check" in e.value.message


async def test_demo_mode_refuses_images_without_asking_the_proxy(monkeypatch):
    monkeypatch.setattr(attachments.settings, "demo_mode", True)
    with pytest.raises(attachments.AttachmentRefused) as e:
        await attachments.prepare(
            [_att("shot.png", "image/png", PNG_1PX)], model_id=None
        )
    assert e.value.code == "attachment_images_unsupported"


async def test_one_bad_file_refuses_the_WHOLE_send(monkeypatch):
    """Partial delivery is the failure mode dressed as helpfulness: the operator
    is told nothing and the agent answers as though it had everything."""
    monkeypatch.setattr(attachments.settings, "demo_mode", False)
    from central_command.integrations import litellm

    async def _no_vision(model_id):
        return False

    monkeypatch.setattr(litellm, "model_accepts_images", _no_vision)

    with pytest.raises(attachments.AttachmentRefused):
        await attachments.prepare(
            [
                _att("good.txt", "text/plain", b"this one is perfectly readable"),
                _att("shot.png", "image/png", PNG_1PX),
            ],
            model_id="cc-default",
        )


async def test_truncation_is_visible_to_the_agent(monkeypatch):
    """A truncation the agent cannot see is the same silent failure as a dropped
    file, one layer along."""
    monkeypatch.setattr(attachments, "MAX_EXTRACTED_CHARS", 100)
    block, manifest = await attachments.prepare(
        [_att("big.txt", "text/plain", b"x " * 500)], model_id="cc-default"
    )
    assert 'truncated="true"' in block
    assert manifest[0]["truncated"] is True


# --- malformed input must refuse, never 500 ----------------------------------


async def test_undecodable_payload_is_refused():
    with pytest.raises(attachments.AttachmentRefused) as e:
        await attachments.prepare(
            [{"name": "x.txt", "mimeType": "text/plain", "content": "not!base64!"}],
            model_id="cc-default",
        )
    assert e.value.code == "attachment_undecodable"


async def test_empty_and_oversized_files_are_refused(monkeypatch):
    with pytest.raises(attachments.AttachmentRefused) as e:
        await attachments.prepare(
            [_att("empty.txt", "text/plain", b"")], model_id="cc-default"
        )
    assert e.value.code == "attachment_empty"

    monkeypatch.setattr(attachments, "MAX_ATTACHMENT_BYTES", 10)
    with pytest.raises(attachments.AttachmentRefused) as e:
        await attachments.prepare(
            [_att("big.txt", "text/plain", b"more than ten bytes")],
            model_id="cc-default",
        )
    assert e.value.code == "attachment_too_large"


DOCX_MIME = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


async def test_a_corrupt_file_refuses_instead_of_raising_through():
    """A malformed upload must not 500 the send — the operator gets a reason.
    Genuinely corrupt binary makes markitdown raise; that has to land as a
    refusal, not as a stack trace through the RPC."""
    import os

    with pytest.raises(attachments.AttachmentRefused) as e:
        await attachments.prepare(
            [_att("broken.docx", DOCX_MIME, b"PK\x03\x04" + os.urandom(200))],
            model_id="cc-default",
        )
    assert e.value.code == "attachment_unreadable"
    assert e.value.filename == "broken.docx"


async def test_content_decides_the_extraction_not_the_declared_extension():
    """markitdown sniffs the bytes (magika), so the browser's filename and MIME
    are hints, not authority. Pinned because it cuts BOTH ways and both are
    wanted: a mislabelled text file is still read correctly, and a file cannot
    be smuggled past extraction by renaming it."""
    block, manifest = await attachments.prepare(
        [_att("actually-text.docx", DOCX_MIME, b"the licence renews on 2026-09-01")],
        model_id="cc-default",
    )
    assert "licence renews" in block
    # The frame still reports what the operator sent it as — we record the
    # claim, we just don't trust it to drive the parse.
    assert 'name="actually-text.docx"' in block
    assert manifest[0]["media_type"] == DOCX_MIME


# --- capability is DECLARED, and read from the agents' own door ---------------


async def test_supports_vision_none_reads_as_no(monkeypatch):
    """LiteLLM reports `supports_vision: None` for our self-hosted entries.
    None means "nobody declared it", not "yes"."""
    import httpx

    from central_command.integrations import litellm

    monkeypatch.setattr(litellm.settings, "llm_base_url", "http://proxy.test")

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {
                "data": [
                    {"model_name": "cc-default", "model_info": {"supports_vision": None}},
                    {"model_name": "seeing", "model_info": {"supports_vision": True}},
                ]
            }

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **k):
            return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", lambda **k: _Client())

    assert await litellm.model_accepts_images("cc-default") is False
    assert await litellm.model_accepts_images("seeing") is True
    # A model the endpoint does not serve cannot accept anything.
    assert await litellm.model_accepts_images("absent") is False


# --- the ENTRY, not the wrapper ----------------------------------------------


async def _refusing_send(monkeypatch, attachment: dict):
    """Drive `_chat_send` itself with one bad attachment, recording everything
    it touched. Returns (raised, notifications, events)."""
    from central_command.api import nerve_gateway

    notifications: list = []
    emitted: list = []

    async def _notify(frame):
        notifications.append(frame)

    async def _target(key):
        return {"opener_agent": "ea"}

    async def _emit(kind, ref_id=None, payload=None, actor=None):
        emitted.append({"kind": kind, "payload": payload, "actor": actor})
        return {}

    async def _model_id(session_id, agent_id):
        return "cc-default"

    monkeypatch.setattr(nerve_gateway, "_send_target", _target)
    monkeypatch.setattr(nerve_gateway.events, "emit", _emit)
    monkeypatch.setattr(attachments, "session_model_id", _model_id)
    monkeypatch.setattr(attachments.settings, "demo_mode", False)

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await nerve_gateway._chat_send(
            {"sessionKey": "agent:ea:main", "message": "have a look at this",
             "attachments": [attachment]},
            _notify,
        )
    return exc.value, notifications, emitted


async def test_a_refused_attachment_starts_no_turn_and_mutates_nothing(monkeypatch):
    """The whole reason validation lives before the turn: a refusal must leave
    NO user message in the transcript, NO session touched, and NO lifecycle
    frame owed to the cockpit. A turn that starts and then dies owes the spinner
    an END frame — the 2026-07-23 "still thinking" incident — and the cheapest
    way to owe nothing is to fail before the turn exists."""
    # Pin the capability answer (not demo_mode — `_refusing_send` re-pins that
    # False): with a live .env the model declares vision and the OCR fallback
    # would reach the real proxy. A text-only answer keeps this the refusal
    # world the test is about.
    from central_command.integrations import litellm

    async def _no_vision(model_id):
        return False

    monkeypatch.setattr(litellm, "model_accepts_images", _no_vision)
    raised, notifications, emitted = await _refusing_send(
        monkeypatch, _att("contract.pdf", "application/pdf", _scanned_pdf())
    )

    assert raised.status_code == 422
    # The operator is told which file and what to do — not "bad request".
    assert "contract.pdf" in raised.detail
    assert "scan" in raised.detail.lower()

    # Nothing was pushed at the cockpit: no lifecycle start, so no spinner, so
    # no end frame owed. This is the assertion that proves no turn began.
    assert notifications == []

    # …and it is on the record, so a recurring capability gap is visible rather
    # than dying in a dismissed toast.
    assert [e["kind"] for e in emitted] == ["chat.attachment_refused"]
    assert emitted[0]["payload"]["code"] == "attachment_no_text"
    assert emitted[0]["payload"]["filename"] == "contract.pdf"
    assert emitted[0]["actor"] == "operator"


async def test_a_refused_image_names_the_model_that_cannot_read_it(monkeypatch):
    from central_command.integrations import litellm

    async def _no_vision(model_id):
        return False

    monkeypatch.setattr(litellm, "model_accepts_images", _no_vision)
    raised, notifications, emitted = await _refusing_send(
        monkeypatch, _att("shot.png", "image/png", PNG_1PX)
    )
    assert raised.status_code == 422
    assert "cc-default" in raised.detail
    assert notifications == []
    assert emitted[0]["payload"]["code"] == "attachment_images_unsupported"


def test_attachments_are_resolved_before_the_turn_is_created():
    """A SOURCE WALK, because the ordering IS the guarantee. Move
    `attachments.prepare` inside `_run()` and every behavioural test above still
    passes while the half-state comes back: a transcript row for a message the
    agent never received. Guard the order, not just the outcome."""
    import inspect

    from central_command.api import nerve_gateway

    src = inspect.getsource(nerve_gateway._chat_send)
    assert "attachments.prepare" in src, "_chat_send no longer validates attachments"
    assert src.index("attachments.prepare") < src.index("asyncio.create_task"), (
        "_chat_send prepares attachments AFTER starting the turn — a refusal "
        "would then leave a started turn and a transcript row behind"
    )


async def test_session_model_precedence_matches_the_runtime_seam():
    """`session_model_id` duplicates `runtime.models.resolve_session_model`'s
    precedence because the gateway may not reach into `runtime/`. If the runtime
    seam's precedence changes and this does not, the capability we check stops
    describing the model the turn actually runs on."""
    import inspect

    from central_command.runtime import models

    src = inspect.getsource(models.resolve_session_model) + inspect.getsource(
        models.resolve_model
    )
    # Both sides resolve: session override -> per-agent -> default.
    assert "get_session_model_override" in src
    assert "agent_model" in src and "default_model" in src
    ours = inspect.getsource(attachments.session_model_id)
    assert "get_session_model_override" in ours
    assert "agent_model" in ours and "default_model" in ours


async def test_a_file_with_no_message_is_a_valid_send(monkeypatch):
    """The composer lets an operator send a file with no words. Refusing that
    as "message must not be empty" would be a refusal about the wrong thing."""
    from central_command.api import nerve_gateway

    sent: list = []

    async def _target(key):
        return {"opener_agent": "ea"}

    async def _model_id(session_id, agent_id):
        return "cc-default"

    async def _emit(kind, ref_id=None, payload=None, actor=None):
        return {}

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(nerve_gateway, "_send_target", _target)
    monkeypatch.setattr(nerve_gateway.events, "emit", _emit)
    monkeypatch.setattr(attachments, "session_model_id", _model_id)
    monkeypatch.setattr(nerve_gateway.asyncio, "create_task", lambda coro: (coro.close(), sent.append(True))[1])

    ack = await nerve_gateway._chat_send(
        {
            "sessionKey": "agent:ea:main",
            "message": "",
            "attachments": [_att("notes.txt", "text/plain", b"the licence renews 2026-09-01")],
        },
        _noop,
    )
    assert ack["status"] == "started"
    assert sent == [True]
