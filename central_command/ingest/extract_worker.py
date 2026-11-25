"""Read ONE untrusted file in a child process and print one JSON line.

    python -m central_command.ingest.extract_worker <name> [sheet_max_rows] [max_chars]

The file's bytes arrive on stdin; the answer is `{"status", "text", "reader",
"error", "truncated"}` on stdout, status 'ok' | 'empty' | 'failed'. The text
is cut at `max_chars` HERE, so a small file that expands to hundreds of
megabytes of text never crosses the pipe into the parent.

Why a child process: a mail attachment is a file a stranger chose. The parsers
behind `extract.convert` are large and had a bad 2026 for hostile input (the
research behind DL-216 lists the advisories), and a parser that loops or
allocates without bound inside the API process takes the control plane with
it. Here it takes one child: the parent (`mail_attachments.read_isolated`)
reads a killed or crashed child as "this attachment was not read", says so to
the agent, and carries on.

The limits are the operating system's, set before any parser is imported:
CPU seconds and address space where the platform has `resource` (Linux and
macOS; native Windows has no equivalent, so there the child is isolation
without limits). Nothing here opens a network connection or writes a file,
and the parent starts it with a minimal environment: none of the credentials
the API process holds are in the child.
"""

from __future__ import annotations

import json
import sys

# CPU seconds of parsing one file may use. The slowest reader measured in the
# bake-off took 58 s on a 100-page booklet on a Pi 5; this is that with room.
CPU_SECONDS = 180
# Address space, not resident memory: the ONNX model markitdown loads and the
# interpreter itself map far more than they touch, so this sits well above the
# ~1.4 GB resident peak measured for the largest test file.
ADDRESS_SPACE_BYTES = 6 * 1024**3


def _limit() -> None:
    try:
        import resource
    except ImportError:  # native Windows
        return
    for what, value in (
        (resource.RLIMIT_CPU, CPU_SECONDS),
        (resource.RLIMIT_AS, ADDRESS_SPACE_BYTES),
    ):
        try:
            _soft, hard = resource.getrlimit(what)
            cap = value if hard == resource.RLIM_INFINITY else min(value, hard)
            resource.setrlimit(what, (cap, hard))
        except (ValueError, OSError):
            continue


def main(argv: list[str]) -> int:
    _limit()
    name = argv[1] if len(argv) > 1 else "attachment"
    rows = int(argv[2]) if len(argv) > 2 and argv[2].isdigit() else None
    max_chars = int(argv[3]) if len(argv) > 3 and argv[3].isdigit() else None
    data = sys.stdin.buffer.read()
    out = {"status": "failed", "text": "", "reader": None, "error": None, "truncated": False}
    try:
        from central_command.ingest import extract

        out["reader"] = extract.reader_for(name, data)
        text, reader = extract.convert(name, data, sheet_max_rows=rows)
        if max_chars is not None and len(text) > max_chars:
            text, out["truncated"] = text[:max_chars], True
        out.update(status="ok" if text else "empty", text=text, reader=reader)
    except MemoryError:
        out["error"] = "the file needed more memory than the reader is allowed"
    except Exception as exc:  # noqa: BLE001 — the parent reports it; nothing is raised across the pipe
        out["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
    sys.stdout.write(json.dumps(out))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
