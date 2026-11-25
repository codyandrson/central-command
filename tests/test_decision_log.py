"""Guards the STRUCTURE and the citations of `docs/decisions/`, not its wording.

The log explains why a rule exists; its value is that its citations are real.
A test it names that nobody wrote, or a file it cites that is gone, tells a
reader "checked" about something nobody checked. So this reads the area files
(through `scripts/gen_decision_index.py`, the parser that builds the index)
and checks, against the files on disk:

- ids are unique, run DL-001, DL-002, ... with no gaps, and ascend in
  area-file order (an archived entry keeps its id through its stub);
- every entry carries the six required bullets, an allowed `Status:` and a
  parseable `Date:`;
- a superseded entry names a successor that exists, and a `Supersedes:`
  names an entry that exists;
- every test an `Enforced:` bullet names exists, file and function;
- every repo file an `Enforced:` or `Source:` bullet cites exists, and every
  `Rule:` link points at a file that exists;
- the README's index table is what the generator produces (`--check`).

It does NOT compare an entry's quoted `Rule:` phrase with the rule text:
rewording a rule must not break the suite. Each check loops over every entry
and reports every failure at once.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import gen_decision_index as gdi  # noqa: E402

DECISIONS_DIR = gdi.DECISIONS_DIR
ARCHIVE = ROOT / "docs" / "archive" / "decisions-archived.md"

REQUIRED_BULLETS = ["Status", "Date", "Rule", "Why", "Enforced", "Source"]
VALID_STATUSES = {"active", "superseded", "retired", "archived"}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ID_RE = re.compile(r"DL-\d{3}")

# A backticked token that names a repo file: a path with a known extension,
# optionally followed by `::function` or `:line[,line]`.
PATH_RE = re.compile(
    r"^([\w.-]+(?:/[\w.*-]+)+\.(?:py|sh|yaml|yml|md|sql|ts|tsx|js|json|txt|toml))"
    r"(?:::(\w+))?(?::[\d,-]+)?$"
)

ENTRIES = gdi.load_entries()


def _resolve(rel: str) -> Path | None:
    """A cited path, resolved against the repo root or, for a path written
    relative to the package (`runtime/tools.py`), against `central_command/`.
    None for a path outside the repo (a third-party module such as
    `pydantic_ai/concurrency.py`), which this test does not check."""
    first = rel.split("/", 1)[0]
    for base in (ROOT, ROOT / "central_command"):
        if (base / first).exists():
            return base / rel
    return None


def _defines(path: Path, name: str) -> bool:
    """Whether the module defines a function (sync or async, at any nesting) named `name`."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
        for node in ast.walk(tree)
    )


def test_the_generator_and_the_area_files_agree_on_the_entry_list():
    assert len(ENTRIES) > 200, f"expected the whole log, parsed {len(ENTRIES)} entries"
    assert all(e.area in gdi.AREA_FILES for e in ENTRIES)


def test_ids_are_unique_contiguous_and_ascend_in_area_file_order():
    ids = [e.number for e in ENTRIES]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    assert not dupes, f"duplicate DL- ids: {dupes}"
    assert ids == list(range(1, len(ids) + 1)), (
        "DL- ids must run DL-001, DL-002, ... with no gaps, in area-file order "
        f"({', '.join(gdi.AREA_FILES)}); first out of place: "
        f"{next((f'DL-{i:03d} at position {n + 1}' for n, i in enumerate(ids) if i != n + 1), None)}"
    )


def test_every_entry_has_the_required_bullets_an_allowed_status_and_a_date():
    problems = []
    for e in ENTRIES:
        missing = [b for b in REQUIRED_BULLETS if b not in e.bullets]
        if missing:
            problems.append(f"{e.id} ({e.area}): missing {missing}")
            continue
        status = e.bullets["Status"].strip()
        if status not in VALID_STATUSES:
            problems.append(f"{e.id}: Status {status!r} not in {sorted(VALID_STATUSES)}")
        date = e.bullets["Date"].strip()
        if date != "undated" and not DATE_RE.match(date):
            problems.append(f"{e.id}: Date {date!r} is not YYYY-MM-DD or 'undated'")
    assert not problems, "\n".join(problems)


def test_every_archived_stub_has_its_full_entry_in_the_archive():
    stubs = [e for e in ENTRIES if e.archived_link]
    if not stubs:
        return
    assert ARCHIVE.exists(), (
        f"{len(stubs)} archived stubs but {ARCHIVE.relative_to(ROOT)} is missing"
    )
    archived_ids = {e.id for e in gdi.split_entries(ARCHIVE.read_text(encoding="utf-8"), "archive")}
    problems = [
        f"{e.id}: stub in {e.area} has no full entry in the archive"
        for e in stubs
        if e.id not in archived_ids
    ]
    problems += [
        f"{e.id}: stub link {e.archived_link!r} does not resolve"
        for e in stubs
        if not (DECISIONS_DIR / e.archived_link.split("#")[0]).exists()
    ]
    stray = archived_ids - {e.id for e in stubs}
    problems += [f"{i}: in the archive but has no stub in an area file" for i in sorted(stray)]
    assert not problems, "\n".join(problems)


def test_superseded_entries_name_successors_that_exist():
    known = {e.id for e in ENTRIES}
    problems = []
    for e in ENTRIES:
        if e.bullets.get("Status", "").strip() == "superseded":
            successors = ID_RE.findall(e.bullets.get("Superseded-by", ""))
            if not successors:
                problems.append(f"{e.id}: superseded but carries no Superseded-by: DL-nnn")
            problems += [
                f"{e.id}: Superseded-by {s} does not exist" for s in successors if s not in known
            ]
        for s in ID_RE.findall(e.bullets.get("Supersedes", "")):
            if s not in known:
                problems.append(f"{e.id}: Supersedes {s} does not exist")
    assert not problems, "\n".join(problems)


def test_every_test_an_entry_names_exists():
    """`test: path::name`, and the `::name` shorthand that continues the path
    before it, must name a function the file defines (a source check; no
    collection run, so this needs no database)."""
    problems = []
    for e in ENTRIES:
        last = None
        for tok in re.findall(r"`([^`]+)`", e.bullets.get("Enforced", "")):
            m = PATH_RE.match(tok)
            if m:
                last, func = m.group(1), m.group(2)
            elif tok.startswith("::") and last and re.fullmatch(r"::\w+", tok):
                func = tok[2:]
            else:
                continue
            path = _resolve(last)
            if path is None or "*" in last or not path.exists() or not func:
                continue  # existence is the next test's job
            if not _defines(path, func):
                problems.append(
                    f"{e.id}: Enforced names `{last}::{func}`, which {last} does not define"
                )
    assert not problems, "\n".join(problems)


def test_every_file_an_entry_cites_exists():
    """Enforced and Source are citations and must resolve. Why and the notes
    may name a file that was removed, as history, so they are not checked."""
    problems = []
    for e in ENTRIES:
        for label in ("Enforced", "Source"):
            for tok in re.findall(r"`([^`]+)`", e.bullets.get(label, "")):
                m = PATH_RE.match(tok)
                if not m or "*" in m.group(1):
                    continue
                path = _resolve(m.group(1))
                if path is not None and not path.exists():
                    problems.append(f"{e.id}: {label} cites `{tok}`, which does not exist")
        for rel in re.findall(r"\]\(([^)#\s]+)", e.bullets.get("Rule", "")):
            if not rel.startswith("http") and not (DECISIONS_DIR / rel).exists():
                problems.append(f"{e.id}: Rule links {rel!r}, which does not exist")
    assert not problems, "\n".join(problems)


def test_the_index_is_what_the_generator_produces():
    assert gdi.main(["--check"]) == 0, "re-run `python scripts/gen_decision_index.py`"
