"""The single-node installer's SOURCE, for the tests that read it rather than run it.

Since v2.58.0 (2026-10-01 design record, D11) `deploy/single/setup.sh` is a thin
orchestrator that sources one file per manifest phase from
`deploy/single/phases/`. A test that walks the installer's functions, lifts one
out, or greps it for `set_kv` targets has to read ALL of those files — a test
that kept reading `setup.sh` alone would go on passing while it silently stopped
looking at the code that moved. So there is ONE definition of "the installer's
source" here, and the source-reading tests ask it instead of globbing for
themselves.

Not a test module (no `test_` prefix): pytest does not collect it.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SINGLE = ROOT / "deploy" / "single"
SETUP = SINGLE / "setup.sh"
PHASES = SINGLE / "phases"


def phase_files() -> list[Path]:
    """deploy/single/phases/*.sh, sorted. Never empty: an empty list would make
    every caller below quietly test `setup.sh` alone again."""
    files = sorted(PHASES.glob("*.sh"))
    assert files, f"no phase files under {PHASES} — the installer is split into them"
    return files


def installer_files() -> list[Path]:
    """setup.sh first, then every phase file it sources."""
    return [SETUP, *phase_files()]


def installer_texts() -> list[str]:
    """Each installer file's text, in installer_files() order. For a parser
    whose notion of "where a function ends" should stop at a file boundary."""
    return [p.read_text(encoding="utf-8") for p in installer_files()]


def installer_source() -> str:
    """The whole installer as ONE text: setup.sh, then the phase files, each
    ending in a newline — so `"\\n<name>() {"` finds a function in any of them."""
    return "".join(t if t.endswith("\n") else t + "\n" for t in installer_texts())


# ── the installer's functions ───────────────────────────────────────────────
# A STRICT extractor (moved here from tests/test_single_steps_schema.py in
# v2.58.0, where it was written for the probes): a one-line `f() { ...; }` is
# closed on its own line. The looser walk test_single_check_is_dry used closed
# a function only on a line that is exactly `}`, so a one-liner swallowed every
# function after it into its body — and once setup.sh was split, WHICH
# functions got swallowed depended on which file a neighbour had moved to.
# Each file is parsed on its own, because a function never spans two files.


def functions_in(text: str) -> dict[str, str]:
    """name -> body, for every top-level function defined in one file's text."""
    out: dict[str, str] = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\(\)\s*\{(.*)$", lines[i])
        if not m:
            i += 1
            continue
        name, rest = m.group(1), m.group(2)
        if rest.rstrip().endswith("}"):
            out[name] = rest.rstrip()[:-1]
            i += 1
            continue
        body: list[str] = []
        i += 1
        while i < len(lines) and lines[i] != "}":
            body.append(lines[i])
            i += 1
        out[name] = "\n".join(body)
        i += 1
    return out


def installer_functions() -> dict[str, str]:
    """name -> body over setup.sh AND every phase file. A name defined in two
    files is an error here, not a silent last-one-wins: sourcing order would
    decide which body runs (tests/test_single_phase_files.py says so by name)."""
    out: dict[str, str] = {}
    where: dict[str, str] = {}
    for path in installer_files():
        for name, body in functions_in(path.read_text(encoding="utf-8")).items():
            assert name not in out, f"{name}() is defined in both {where[name]} and {path.name}"
            out[name] = body
            where[name] = path.name
    return out


def single_scripts() -> list[Path]:
    """Every shell script of the single-node profile: deploy/single/*.sh plus
    the phase files under deploy/single/phases/. What a `deploy/single/*.sh`
    glob meant before the split."""
    return sorted(SINGLE.glob("*.sh")) + phase_files()
