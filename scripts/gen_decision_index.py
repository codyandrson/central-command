#!/usr/bin/env python3
"""Generate the index table in `docs/decisions/README.md` from the area files.

The table restated every entry's id, title, status, date, enforcement and
area file by hand, and rows drifted from the entries they summarised. The
entries are the source of truth; this script rebuilds the table from them and
writes it between the README's BEGIN/END marker lines, leaving the
hand-written header (what the log is, the entry format, how to add an entry)
untouched. Same shape as `scripts/gen_records_index.py`.

Usage:
    python scripts/gen_decision_index.py            # rewrite the table
    python scripts/gen_decision_index.py --check    # exit 1 if the committed
                                                    # table is stale

The "enforced (short)" column is derived from the entry's `Enforced:` bullet:
the text before its first " — " is the claim; "code structure only" and
"discipline only" are kept as they are; otherwise the kind (`test:`,
`script:`, `sql:`) and the first reference are kept, with "(+N more)" for the
other references the claim names.

Stdlib only: the test suite imports it (`tests/test_decision_log.py`).
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DECISIONS_DIR = ROOT / "docs" / "decisions"
README_PATH = DECISIONS_DIR / "README.md"

# Area files in id order: ids ascend through this list.
AREA_FILES = [
    "trust-gate.md",
    "runtime.md",
    "events-data.md",
    "ingest-integrations.md",
    "graph.md",
    "models.md",
    "deploy.md",
    "cockpit.md",
    "process.md",
]

BEGIN = "<!-- BEGIN GENERATED INDEX (scripts/gen_decision_index.py) — do not hand-edit -->"
END = "<!-- END GENERATED INDEX -->"

ENTRY_HEADING_RE = re.compile(r"^### (DL-\d+) — (.+)$", re.MULTILINE)
BULLET_RE = re.compile(r"^- \*\*([^:*]+):\*\*\s?(.*)$")
# An archived entry leaves a one-line stub in its area file:
#   - **DL-NNN** — <title> — archived: [docs/archive/decisions-archived.md](...)
STUB_RE = re.compile(r"^- \*\*(DL-\d+)\*\* — (.+) — archived: \[[^\]]+\]\(([^)]+)\)\s*$", re.MULTILINE)
# A backticked token that names a reference: a repo path (optionally with
# `::test` or `:lines`) or a bare `::test` continuation of the previous path.
REF_RE = re.compile(r"^(?:::\w+|[A-Za-z_.][\w.*-]*(?:/[\w.*-]*)+(?:::\w+)?(?::[\d,]+)?)$")


@dataclass
class Entry:
    id: str
    title: str
    area: str
    bullets: dict[str, str] = field(default_factory=dict)
    body: str = ""
    archived_link: str = ""  # set for a stub whose body lives in the archive

    @property
    def number(self) -> int:
        return int(self.id.split("-")[1])


def parse_bullets(body: str) -> dict[str, str]:
    """The '- **Label:** text' bullets of one entry, with indented
    continuation lines folded into the bullet above."""
    bullets: dict[str, str] = {}
    current = None
    for line in body.splitlines():
        m = BULLET_RE.match(line)
        if m:
            current = m.group(1).strip()
            bullets[current] = m.group(2)
        elif current is not None and line.startswith("  ") and line.strip():
            bullets[current] += " " + line.strip()
        elif line.strip() == "":
            current = None
    return bullets


def split_entries(text: str, area: str) -> list[Entry]:
    """Every full entry ('### DL-NNN — title' block) and every archived stub
    in one file, in the order they appear."""
    found: list[tuple[int, Entry]] = []
    heads = list(ENTRY_HEADING_RE.finditer(text))
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        body = text[m.end():end]
        # a stub line after the last entry body is not part of that body
        stub = STUB_RE.search(body)
        if stub:
            body = body[: stub.start()]
        found.append((m.start(), Entry(m.group(1), m.group(2).strip(), area, parse_bullets(body), body)))
    for m in STUB_RE.finditer(text):
        found.append((m.start(), Entry(m.group(1), m.group(2).strip(), area,
                                       {"Status": "archived"}, "", m.group(3))))
    found.sort(key=lambda pair: pair[0])
    return [e for _, e in found]


def load_entries(archive_path: Path | None = None) -> list[Entry]:
    """Every entry across the area files, in AREA_FILES order. An archived
    stub is completed from the archive file's full entry, so its row shows
    the entry's own date and enforcement."""
    archive_path = archive_path or (ROOT / "docs" / "archive" / "decisions-archived.md")
    archived = {}
    if archive_path.exists():
        for e in split_entries(archive_path.read_text(encoding="utf-8"), "archive"):
            archived[e.id] = e
    out = []
    for area in AREA_FILES:
        path = DECISIONS_DIR / area
        if not path.exists():
            raise FileNotFoundError(f"docs/decisions/{area} is listed as an area but does not exist")
        for e in split_entries(path.read_text(encoding="utf-8"), area):
            if e.archived_link and e.id in archived:
                full = archived[e.id]
                e.bullets = dict(full.bullets, Status="archived")
                e.body = full.body
            out.append(e)
    return out


def enforced_short(enforced: str) -> str:
    claim = enforced.split(" — ")[0].strip()
    for phrase in ("code structure only", "discipline only"):
        if claim.startswith(phrase):
            return phrase
    kind = claim.split(":", 1)[0].strip()
    if kind not in ("test", "script", "sql"):
        return claim
    ticks = re.findall(r"`([^`]+)`", claim)
    refs = [t for t in ticks if REF_RE.match(t)]
    if not refs:
        return f"{kind}: `{ticks[0]}`" if ticks else claim
    more = f" (+{len(refs) - 1} more)" if len(refs) > 1 else ""
    return f"{kind}: `{refs[0]}`{more}"


def _cell(text: str) -> str:
    return re.sub(r"\s+", " ", text).replace("|", "\\|").strip()


def render_table(entries: list[Entry]) -> str:
    lines = [
        "| id | title | status | date | enforced (short) | area file |",
        "|----|-------|--------|------|-------------------|-----------|",
    ]
    for e in entries:
        status = e.bullets.get("Status", "").strip()
        date = e.bullets.get("Date", "").strip()
        enforced = enforced_short(e.bullets.get("Enforced", ""))
        lines.append(
            f"| {e.id} | {_cell(e.title)} | {status} | {date} | {_cell(enforced)} | [{e.area}]({e.area}) |"
        )
    return "\n".join(lines) + "\n"


def splice(readme: str, table: str) -> str:
    if BEGIN not in readme or END not in readme:
        raise ValueError(f"{README_PATH} lacks the BEGIN/END generated-index markers")
    head, rest = readme.split(BEGIN, 1)
    _, tail = rest.split(END, 1)
    return f"{head}{BEGIN}\n\n{table}\n{END}{tail}"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="gen_decision_index.py",
        description="Rebuild the index table in docs/decisions/README.md from the area files.")
    parser.add_argument("--check", action="store_true",
                        help="write nothing; exit 1 if the committed table is stale")
    check = parser.parse_args(argv).check
    try:
        entries = load_entries()
        current = README_PATH.read_text(encoding="utf-8")
        generated = splice(current, render_table(entries))
    except (OSError, ValueError) as exc:
        print(f"gen_decision_index: {exc}", file=sys.stderr)
        return 1
    if check:
        if current != generated:
            print(
                f"gen_decision_index: the index in {README_PATH} is stale — "
                "re-run `python scripts/gen_decision_index.py`",
                file=sys.stderr,
            )
            return 1
        return 0
    README_PATH.write_text(generated, encoding="utf-8")
    print(f"wrote {README_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
