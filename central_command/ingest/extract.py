"""Shared text-extraction seam: one function turns a file's bytes into the text
an agent reads. Used by the chat-attachment gate (`api/attachments.py`), the
catalog watcher (`ingest/watcher.py`) and mail attachments
(`ingest/mail_attachments.py`, through the isolated worker
`ingest/extract_worker.py`).

WHICH READER READS WHAT (DL-216, measured on the 2026-11-14 bake-off):

  - PDF                              -> PDFium's text layer (`readers.pdf_text`)
  - xlsx / xlsm / xlsb / xls / ods   -> calamine (`readers.spreadsheet_text`)
  - ics                              -> icalendar (`readers.calendar_text`)
  - eml                              -> stdlib email + `ingest/mailtext.py`
  - rtf                              -> striprtf
  - everything else                  -> markitdown (Word, PowerPoint, HTML,
                                        CSV, EPUB, plain text, JSON, XML)

markitdown read all of these until v2.88.0. It stays where nothing measured
against it; it lost the types above because it corrupted them (PDF: columns
interleaved, prices split from their items, watermark letters inside amounts),
had no size limit (one spreadsheet became 2.5 million tokens) or passed the raw
file through (calendar, mail, RTF).

Two callers, two needs: the attachment gate must REFUSE on empty/unreadable
output (see attachments.py's module docstring — the empty-output trap is
load-bearing there, and it holds for PDFium exactly as it did for markitdown:
an image-only PDF yields '' without raising); the watcher must never abort a
walk over one bad file, so it needs a status it can record and move on.
`extract_text` serves the watcher; `extract_text_or_raise` serves the gate;
`convert` is the one conversion under both and also names the reader.
"""

from __future__ import annotations

import io
from pathlib import Path

from central_command.ingest import readers

_SPREADSHEET_SUFFIXES = {".xlsx", ".xlsm", ".xlsb", ".xls", ".ods"}


def _markitdown():
    """Built once and cached — construction loads magika's ONNX model."""
    global _MD
    try:
        return _MD
    except NameError:
        from markitdown import MarkItDown

        _MD = MarkItDown()
        return _MD


def _markitdown_text(name: str, data: bytes) -> str:
    suffix = Path(name).suffix or None
    result = _markitdown().convert_stream(io.BytesIO(data), file_extension=suffix)
    return (result.text_content or "").strip()


def reader_for(name: str, data: bytes) -> str:
    """Which reader takes this file. The name's suffix decides. Only a file
    whose name carries NO suffix (common in mail) is recognised by how it
    begins: a named `.txt` that happens to open with `BEGIN:VCALENDAR` is
    still a text file, read as one."""
    suffix = Path(name).suffix.lower()
    head = data[:1024].lstrip() if not suffix else b""
    if suffix == ".pdf" or head.startswith(b"%PDF-"):
        return "pdfium"
    if suffix in _SPREADSHEET_SUFFIXES:
        return "calamine"
    if suffix in (".ics", ".ical", ".ifb") or head.startswith(b"BEGIN:VCALENDAR"):
        return "icalendar"
    if suffix == ".eml":
        return "email"
    if suffix == ".rtf" or head.startswith(b"{\\rtf"):
        return "striprtf"
    return "markitdown"


def convert(name: str, data: bytes, *, sheet_max_rows: int | None = None) -> tuple[str, str]:
    """Bytes -> (extracted text, reader name). The text may be '' (a scanned
    PDF has no text layer). Propagates whatever the reader, or a malformed
    file, raises — callers decide what that means."""
    reader = reader_for(name, data)
    if reader == "pdfium":
        return readers.pdf_text(data), reader
    if reader == "calamine":
        rows = sheet_max_rows or readers.SHEET_MAX_ROWS
        return readers.spreadsheet_text(data, max_rows=rows), reader
    if reader == "icalendar":
        return readers.calendar_text(data), reader
    if reader == "email":
        return readers.eml_text(data), reader
    if reader == "striprtf":
        return readers.rtf_text(data), reader
    return _markitdown_text(name, data), reader


def _convert_raw(name: str, data: bytes) -> str:
    """Bytes -> extracted text (may be ''). Kept for the callers that only
    want the text."""
    return convert(name, data)[0]


def extract_text(name: str, data: bytes) -> tuple[str, str, str | None]:
    """(text, status, error). status is 'ok' | 'empty' | 'failed'. Never
    raises: a per-file extraction failure must not abort the watcher's walk.

    'empty' is a reader returning '' without raising — the scanned-PDF trap
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
