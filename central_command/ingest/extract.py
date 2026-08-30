"""Shared text-extraction seam (markitdown), used by both the chat-attachment
gate (`api/attachments.py`) and the catalog watcher (`ingest/watcher.py`,
sources-catalog slice 1). The markitdown singleton and convert-stream logic
used to live only in attachments.py; hoisted here so the watcher gets the same
extraction without importing the gateway tier's refusal machinery.

Two callers, two needs: the attachment gate must REFUSE on empty/unreadable
output (see attachments.py's module docstring — the empty-output trap is
load-bearing there); the watcher must never abort a walk over one bad file, so
it needs a status it can record and move on. `extract_text` serves the
watcher; `extract_text_or_raise` serves the gate.
"""

from __future__ import annotations

import io
from pathlib import Path


def _markitdown():
    """Built once and cached — construction loads magika's ONNX model."""
    global _MD
    try:
        return _MD
    except NameError:
        from markitdown import MarkItDown

        _MD = MarkItDown()
        return _MD


def _convert_raw(name: str, data: bytes) -> str:
    """Bytes -> extracted text (may be ''). Propagates whatever markitdown (or
    a malformed file) raises — callers decide what that means."""
    suffix = Path(name).suffix or None
    result = _markitdown().convert_stream(io.BytesIO(data), file_extension=suffix)
    return (result.text_content or "").strip()


def extract_text(name: str, data: bytes) -> tuple[str, str, str | None]:
    """(text, status, error). status is 'ok' | 'empty' | 'failed'. Never
    raises: a per-file extraction failure must not abort the watcher's walk.

    'empty' is markitdown returning '' without raising — the scanned-PDF trap
    (see attachments.py). # ponytail: OCR-at-watcher-time is the upgrade path
    for scans; not wired in here, the watcher just records extraction='empty'.
    """
    try:
        text = _convert_raw(name, data)
    except Exception as exc:  # a malformed file must not abort the walk
        return "", "failed", str(exc)
    return (text, "ok", None) if text else ("", "empty", None)


def extract_text_or_raise(name: str, data: bytes) -> str:
    """Same conversion, but propagates the underlying exception — the
    attachment gate's contract, which reports the original error to the
    operator."""
    return _convert_raw(name, data)
