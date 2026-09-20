"""Every design record under `docs/superpowers/` carries its own status.

Before 2026-09-20, `docs/DESIGN.md` carried one hand-maintained table
summarizing the fate of every spec/plan/research record — 53 files, none of
which recorded their own status anywhere. The table drifted: the 2026-09-20
consistency audit found two wrong cells and one missing row, none of which
any test would have caught, because nothing tied the table's claims back to
the records or the code.

The fix puts the status on the record itself: a `> **Status:** <word> — ...`
line and a `> **As-built:** ...` line within the first 15 lines after the
title, and `docs/superpowers/README.md` generated from those headers by
`scripts/gen_records_index.py`. This test is the guard: it fails if a record
lacks a valid header, if an As-built path does not exist in the repo, or if
the committed README has drifted from what the generator would produce.

To see this test fail: delete the `> **Status:**` line from any one record
under `docs/superpowers/{specs,plans,research}/` and re-run — restore it
afterward.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import gen_records_index as gri  # noqa: E402


def _all_record_paths() -> list[Path]:
    paths = []
    for directory in gri.DIRS:
        paths.extend(sorted((gri.SUPERPOWERS / directory).glob("*.md")))
    return paths


def test_every_record_has_a_valid_header():
    paths = _all_record_paths()
    assert paths, "expected design records under docs/superpowers/{specs,plans,research}"

    failures = []
    for path in paths:
        try:
            gri.parse_record(path, path.parent.name)
        except gri.RecordHeaderError as exc:
            failures.append(str(exc))

    assert not failures, (
        "records with missing or invalid Status/As-built headers "
        "(see docs/superpowers/{specs,plans,research}/*.md, first 15 lines "
        "after the title):\n" + "\n".join(f"  - {f}" for f in failures)
    )


def test_every_asbuilt_path_exists_in_the_repo():
    by_dir = gri.load_records()
    missing = []
    for record_relpath, path_str in gri.all_asbuilt_paths(by_dir):
        if not (ROOT / path_str).exists():
            missing.append(f"{record_relpath} cites `{path_str}`, which does not exist")

    assert not missing, (
        "As-built path(s) that do not exist in the repo — fix the record's "
        "header or add the file (docs/ROADMAP.md not existing yet is the one "
        "known, expected exception until it is added):\n"
        + "\n".join(f"  - {m}" for m in missing)
    )


def test_readme_matches_the_generator():
    by_dir = gri.load_records()
    generated = gri.render(by_dir)
    current = gri.README_PATH.read_text(encoding="utf-8")
    assert current == generated, (
        f"{gri.README_PATH} is stale relative to the record headers — "
        "re-run `python scripts/gen_records_index.py` and commit the result"
    )


def test_status_vocabulary_is_exactly_the_five_words():
    # Guards the generator's own vocabulary constant so nobody quietly widens
    # it — the header instructions promise exactly these five words.
    assert gri.VALID_STATUSES == {
        "implemented",
        "diverged",
        "partial",
        "superseded",
        "open",
    }
