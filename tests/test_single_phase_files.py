"""One file per manifest phase, and `setup.sh` is the thin orchestrator.

The 2026-10-01 design record's D11 (Sentry's installer: "a thin orchestrator
sourcing step files in order") and its Phasing item 4, whose acceptance is
"every manifest phase has exactly one phase file". Since v2.58.0
`deploy/single/setup.sh` sources `deploy/single/phases/<phase>.sh` for each
phase `steps.tsv` declares, and each holds `phase_<phase>`, the helpers only
that phase uses and its rows' probes. What this pins:

* **the set** — the phase files are exactly the manifest's phases: none
  missing, none extra;
* **each defines its phase** — `phase_<name>` lives in `phases/<name>.sh`;
* **probes live with their rows** — every probe `steps.tsv` names is defined in
  the phase file of a row that uses it, or in `setup.sh` (the shared ones —
  `p_always`, a probe two phases read, one the driver itself calls);
* **one definition per function** — a name defined in two files would run
  whichever was sourced last;
* **the order** — `setup.sh` sources them in the manifest's phase order, by a
  quoted path (a Windows checkout lives under a folder with a space);
* **sourced, never executed** — no shebang, `bash -n` clean, and sourcing one
  twice prints nothing and fails nothing;
* **LF** — `.gitattributes` gives every phase file `eol=lf`, or a Windows
  checkout with core.autocrlf would hand Git Bash a CR in every line.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from central_command.api.update import _bash as _resolve_bash
from tests.installer_source import (
    PHASES,
    ROOT,
    SETUP,
    SINGLE,
    functions_in,
    installer_files,
    phase_files,
)

BASH = _resolve_bash() or "bash"
STEPS = SINGLE / "steps.tsv"


def _rows() -> list[list[str]]:
    rows = []
    for line in STEPS.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        rows.append(line.split("\t"))
    return rows


def _manifest_phases() -> list[str]:
    """The phases in steps.tsv's order — its first column, first appearance."""
    out: list[str] = []
    for row in _rows():
        if row[0] not in out:
            out.append(row[0])
    return out


def _defs() -> dict[Path, set[str]]:
    return {p: set(functions_in(p.read_text(encoding="utf-8"))) for p in installer_files()}


def test_every_manifest_phase_has_exactly_one_phase_file():
    files = {p.stem for p in phase_files()}
    manifest = set(_manifest_phases())
    assert files - manifest == set(), f"phase files with no phase in steps.tsv: {sorted(files - manifest)}"
    assert manifest - files == set(), f"phases in steps.tsv with no phases/<phase>.sh: {sorted(manifest - files)}"
    # Nothing else in the directory: a helper library belongs beside setup.sh.
    stray = sorted(p.name for p in PHASES.iterdir() if p.suffix != ".sh")
    assert not stray, f"deploy/single/phases/ holds only phase files: {stray}"


def test_each_phase_file_defines_its_phase_function():
    for path in phase_files():
        assert f"phase_{path.stem}" in functions_in(path.read_text(encoding="utf-8")), (
            f"{path.name} does not define phase_{path.stem}"
        )


def test_every_probe_lives_with_a_row_that_uses_it_or_in_setup_sh():
    defs = _defs()
    setup_defs = defs[SETUP]
    users: dict[str, set[str]] = {}
    for row in _rows():
        users.setdefault(row[6], set()).add(row[0])
    wrong = []
    for probe, phases in sorted(users.items()):
        homes = [p for p, names in defs.items() if probe in names]
        if not homes:
            wrong.append(f"{probe}: defined nowhere")
            continue
        home = homes[0]
        if home == SETUP or home.stem in phases:
            continue
        wrong.append(f"{probe}: in {home.name}, but only {sorted(phases)} rows use it")
    assert not wrong, (
        "a probe belongs in the phase file of a row that reads it, or in "
        "setup.sh when it is shared:\n  " + "\n  ".join(wrong)
    )


def test_no_function_is_defined_in_two_files():
    seen: dict[str, str] = {}
    dupes = []
    for path, names in _defs().items():
        for name in sorted(names):
            if name in seen:
                dupes.append(f"{name}(): {seen[name]} and {path.name}")
            seen[name] = path.name
    assert not dupes, "defined twice — the later source would silently win:\n  " + "\n  ".join(dupes)


def test_setup_sh_sources_the_phase_files_in_manifest_order():
    src = SETUP.read_text(encoding="utf-8")
    m = re.search(r"^for (\w+) in ((?:\w+ )+\w+); do\n(?:\s*#[^\n]*\n)*\s*\. \"\$HERE/phases/\$\1\.sh\"",
                  src, flags=re.M)
    assert m, 'setup.sh no longer sources the phases with a quoted `. "$HERE/phases/$<var>.sh"` loop'
    assert m.group(2).split() == _manifest_phases(), (
        f"setup.sh sources {m.group(2).split()}, steps.tsv declares {_manifest_phases()}"
    )
    # ...before main runs, which is what lets a test harness's stub (appended
    # just before the final `main "$@"`) override a phase function.
    assert m.start() < src.rindex('\nmain "$@"')


@pytest.mark.parametrize("path", installer_files(), ids=lambda p: p.name)
def test_every_installer_file_parses(path: Path):
    # cwd= + relative path: on Windows the first `bash` on PATH may be WSL's
    # launcher, which cannot open a Windows path.
    subprocess.run([BASH, "-n", path.relative_to(SINGLE).as_posix()], cwd=SINGLE, check=True)


@pytest.mark.parametrize("path", phase_files(), ids=lambda p: p.name)
def test_a_phase_file_is_sourced_never_executed(path: Path):
    """No shebang (nothing may depend on executing it) and no top-level effect:
    sourced twice under the driver's own `set -uo pipefail`, it prints nothing
    and returns 0 — what it does is define functions and constants."""
    first = path.read_text(encoding="utf-8").splitlines()[0]
    assert not first.startswith("#!"), f"{path.name} is sourced, never executed — drop the shebang"
    rel = path.relative_to(SINGLE).as_posix()
    r = subprocess.run([BASH, "-c", f'set -uo pipefail; . "./{rel}" && . "./{rel}"'],
                       cwd=SINGLE, capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout == "" and r.stderr == "", (r.returncode, r.stdout, r.stderr)


def test_the_phase_files_check_out_lf():
    git = shutil.which("git")
    if git is None:
        pytest.skip("no git to ask .gitattributes")
    files = [p.relative_to(ROOT).as_posix() for p in phase_files()]
    r = subprocess.run([git, "check-attr", "eol", "--", *files], cwd=ROOT,
                       capture_output=True, text=True, check=True)
    lines = r.stdout.strip().splitlines()
    assert len(lines) == len(files)
    assert all(line.endswith(": eol: lf") for line in lines), r.stdout
