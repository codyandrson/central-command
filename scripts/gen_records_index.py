#!/usr/bin/env python3
"""Generate `docs/superpowers/README.md` from the Status/As-built header each
design record now carries in its own file.

Before 2026-09-20 the index of design records lived as one hand-maintained
table in `docs/DESIGN.md`. That table drifted from the code (two wrong
cells, one missing row, found in the 2026-09-20 consistency audit) because
nothing forced it to stay in sync with the 53 records it summarized. The fix
moves the status onto each record itself (a `> **Status:** ...` / `> **As-built:**
...` header, checked by `tests/test_design_record_headers.py`) and generates
the index from those headers instead of hand-editing it.

Usage:
    python scripts/gen_records_index.py            # write docs/superpowers/README.md
    python scripts/gen_records_index.py --check    # exit 1 if the committed
                                                     # file differs from the
                                                     # generated one (CI gate)

Stdlib only, on purpose — this runs in the test suite and in CI without
extra dependencies.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SUPERPOWERS = ROOT / "docs" / "superpowers"
README_PATH = SUPERPOWERS / "README.md"

DIRS = ["specs", "plans", "research"]

VALID_STATUSES = {"implemented", "diverged", "partial", "superseded", "open"}

TITLE_RE = re.compile(r"^#\s+(.+?)\s*$")
STATUS_RE = re.compile(r"^>\s*\*\*Status:\*\*\s*(\S+)\s*(?:—\s*(.*))?$")
ASBUILT_RE = re.compile(r"^>\s*\*\*As-built:\*\*\s*(.*)$")
DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-")

HEADER_SCAN_LINES = 15


@dataclass(frozen=True)
class Record:
    directory: str
    filename: str
    date: str
    title: str
    status: str
    qualifier: str
    asbuilt: str

    @property
    def relpath(self) -> str:
        return f"{self.directory}/{self.filename}"


class RecordHeaderError(ValueError):
    """Raised when a record is missing a valid header."""


def _extract_date(filename: str) -> str:
    m = DATE_RE.match(filename)
    return m.group(1) if m else "0000-00-00"


def parse_record(path: Path, directory: str) -> Record:
    """Parse one design-record markdown file into a `Record`.

    Raises `RecordHeaderError` with an actionable message if the file lacks
    a valid Status or As-built line within the first `HEADER_SCAN_LINES`
    lines after its title.
    """
    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")

    title = None
    title_idx = None
    for i, line in enumerate(lines):
        m = TITLE_RE.match(line)
        if m:
            title = m.group(1)
            title_idx = i
            break
    if title is None:
        raise RecordHeaderError(f"{path}: no '# ' title line found")

    window = lines[title_idx + 1 : title_idx + 1 + HEADER_SCAN_LINES]

    status = None
    qualifier = ""
    asbuilt = None
    for line in window:
        sm = STATUS_RE.match(line)
        if sm:
            status = sm.group(1)
            qualifier = sm.group(2) or ""
            continue
        am = ASBUILT_RE.match(line)
        if am:
            asbuilt = am.group(1).strip()

    if status is None:
        raise RecordHeaderError(
            f"{path}: no '> **Status:** ...' line found within "
            f"{HEADER_SCAN_LINES} lines after the title"
        )
    if status not in VALID_STATUSES:
        raise RecordHeaderError(
            f"{path}: Status '{status}' is not one of {sorted(VALID_STATUSES)}"
        )
    if asbuilt is None:
        raise RecordHeaderError(
            f"{path}: no '> **As-built:** ...' line found within "
            f"{HEADER_SCAN_LINES} lines after the title"
        )
    if "`" not in asbuilt:
        raise RecordHeaderError(
            f"{path}: As-built line has no backticked path: {asbuilt!r}"
        )

    return Record(
        directory=directory,
        filename=path.name,
        date=_extract_date(path.name),
        title=title,
        status=status,
        qualifier=qualifier,
        asbuilt=asbuilt,
    )


def load_records() -> dict[str, list[Record]]:
    by_dir: dict[str, list[Record]] = {}
    for directory in DIRS:
        d = SUPERPOWERS / directory
        records = []
        for path in sorted(d.glob("*.md")):
            records.append(parse_record(path, directory))
        # newest first; break filename ties by reversed filename for a
        # deterministic, stable order.
        records.sort(key=lambda r: (r.date, r.filename), reverse=True)
        by_dir[directory] = records
    return by_dir


def extract_asbuilt_paths(record: "Record") -> list[str]:
    """Pull every backticked path out of an As-built line, in order."""
    return re.findall(r"`([^`]+)`", record.asbuilt)


def all_asbuilt_paths(by_dir: dict[str, list[Record]]) -> list[tuple[str, str]]:
    """Return (record_relpath, path) pairs for every backticked As-built path."""
    out = []
    for records in by_dir.values():
        for r in records:
            for p in extract_asbuilt_paths(r):
                out.append((r.relpath, p))
    return out


DIR_LABELS = {
    "specs": "Specs",
    "plans": "Plans",
    "research": "Research",
}

DIR_BLURBS = {
    "specs": "A spec records a design DECISION — what was decided, and why — "
    "before or alongside the code that implements it.",
    "plans": "A plan records the task-by-task IMPLEMENTATION of a decision, "
    "usually a spec's.",
    "research": "Research records an evidence base or a design question worked "
    "through before a decision was ready to write down as a spec.",
}


def render(by_dir: dict[str, list[Record]]) -> str:
    lines = []
    lines.append("# Design records index")
    lines.append("")
    lines.append(
        "This indexes the design records under `docs/superpowers/`: specs, "
        "plans, and research notes written as the system grew past the "
        "founding compendium in `docs/DESIGN.md`."
    )
    lines.append("")
    for directory in DIRS:
        lines.append(f"- **{DIR_LABELS[directory]}** — {DIR_BLURBS[directory]}")
    lines.append("")
    lines.append(
        "**Status vocabulary** (exactly these five words, one per record, "
        "in that record's own `> **Status:**` header): "
        + ", ".join(f"`{s}`" for s in sorted(VALID_STATUSES))
        + "."
    )
    lines.append("")
    lines.append(
        "> Generated by `scripts/gen_records_index.py` — do not hand-edit. "
        "The header in each record (`> **Status:** ...` / `> **As-built:** "
        "...`, right after its title) is the source of truth; re-run the "
        "script after changing a header."
    )
    lines.append("")

    for directory in DIRS:
        records = by_dir[directory]
        lines.append(f"## {DIR_LABELS[directory]} ({len(records)})")
        lines.append("")
        lines.append("| date | record | status | as-built |")
        lines.append("|---|---|---|---|")
        for r in records:
            link = f"[{r.title}]({r.directory}/{r.filename})"
            lines.append(f"| {r.date} | {link} | {r.status} | {r.asbuilt} |")
        lines.append("")

    return "\n".join(lines).rstrip("\n") + "\n"


def main(argv: list[str]) -> int:
    check = "--check" in argv
    try:
        by_dir = load_records()
    except RecordHeaderError as exc:
        print(f"gen_records_index: {exc}", file=sys.stderr)
        return 1

    generated = render(by_dir)

    if check:
        if not README_PATH.exists():
            print(f"gen_records_index: {README_PATH} does not exist", file=sys.stderr)
            return 1
        current = README_PATH.read_text(encoding="utf-8")
        if current != generated:
            print(
                f"gen_records_index: {README_PATH} is stale — "
                "re-run `python scripts/gen_records_index.py`",
                file=sys.stderr,
            )
            return 1
        return 0

    README_PATH.write_text(generated, encoding="utf-8")
    print(f"wrote {README_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
