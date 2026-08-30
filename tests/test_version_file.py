"""`VERSION` is what the updater calls "current" — not git, not the tag.

v2.3.0 (2026-08-30) shipped a CHANGELOG entry and a tag but no VERSION bump,
so every install reported 2.2.0 after a successful update and the cockpit
re-offered 2.3.0 forever: four full stop/merge/restart cycles against an
already-updated tree before anyone noticed. A release is three things —
CHANGELOG entry, VERSION bump, tag — and this pins the first two together.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _version_file() -> str:
    text = (ROOT / "VERSION").read_text()
    m = re.search(r"^version=(\d+\.\d+\.\d+)$", text, re.M)
    assert m, "VERSION has no version=X.Y.Z line"
    return m.group(1)


def _newest_changelog_release() -> str:
    for line in (ROOT / "CHANGELOG.md").read_text().splitlines():
        m = re.match(r"^## \S+ — v(\d+\.\d+\.\d+)\b", line)
        if m:
            return m.group(1)
    raise AssertionError("CHANGELOG.md has no '## <date> — vX.Y.Z' heading")


def test_version_file_matches_the_newest_changelog_release():
    assert _version_file() == _newest_changelog_release(), (
        "CHANGELOG.md announces a release VERSION does not carry — bump VERSION "
        "in the same commit (the updater reads VERSION, not the tag)"
    )
