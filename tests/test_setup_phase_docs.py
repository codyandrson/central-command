"""Pin the prose copies of the single-node phase list to setup.sh itself.

The 2026-08-28 rework added test/boot/demo and every prose copy of the
phase list drifted (six-, seven- and nine-phase variants shipped for three
releases). tests/test_single_airgap_seams.py keeps the SCRIPTS consistent;
this walk does the same for the docs an operator (or the /setup skill's
agent) actually reads. Narrow on purpose: full phase sequence + the exit-3
gate mention, nothing fuzzier.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DOCS = [
    "README.md",
    "CLAUDE.md",
    "deploy/single/README.md",
    ".claude/skills/setup/SKILL.md",
]


def canonical_phases() -> list[str]:
    src = (ROOT / "deploy/single/setup.sh").read_text()
    for m in re.finditer(r"for p in ((?:\w+\s+)+\w+);", src):
        phases = m.group(1).split()
        if phases[0] == "validate":
            return phases
    raise AssertionError("all-phases loop not found in deploy/single/setup.sh")


def test_docs_carry_the_full_phase_list():
    phases = canonical_phases()
    # any non-letter run may separate phases: "->", "→", "/", " / ", newlines
    seq = re.compile(r"[^a-z]+".join(phases))
    for doc in DOCS:
        text = (ROOT / doc).read_text().lower()
        assert seq.search(text), f"{doc}: full phase list {phases} not found"


def test_docs_mention_the_exit3_gate():
    for doc in DOCS:
        text = (ROOT / doc).read_text()
        assert re.search(r"0/1/2/3|\*\*3\*\*", text), f"{doc}: no exit-3 mention"
