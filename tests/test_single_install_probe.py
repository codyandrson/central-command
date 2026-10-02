"""`app/install` is done only when the venv carries the server boot starts (v2.58.0).

`p_install` used to read true on `import central_command` alone. A venv that
had lost uvicorn therefore read INSTALLED: the full run skipped `app` as done,
and `boot` failed on "uvicorn not in .venv" every time, with nothing in the
one command that could repair it. The probe now requires `venv_uvicorn` too —
which moved from phases/boot.sh to setup.sh, because two phases read it.

The three functions are lifted out of the installer source
(tests/installer_source.py) and run against a fake venv: a `python` that
"imports" anything, with and without a `uvicorn` beside it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from tests.installer_source import SETUP, functions_in, installer_functions
from tests.test_single_driver_ledger import _bash_exe


def _probe(repo: Path) -> int:
    funcs = installer_functions()
    defs = "\n".join(f"{n}() {{\n{funcs[n]}\n}}" for n in ("venv_python", "venv_uvicorn", "p_install"))
    r = subprocess.run([_bash_exe(), "-c", f'REPO_ROOT="{repo.as_posix()}"\n{defs}\np_install'],
                       capture_output=True, text=True)
    return r.returncode


def _venv(repo: Path, *, uvicorn: bool) -> None:
    b = repo / ".venv" / "bin"
    b.mkdir(parents=True)
    (b / "python").write_text("#!/usr/bin/env bash\nexit 0\n")
    (b / "python").chmod(0o755)
    if uvicorn:
        (b / "uvicorn").write_text("#!/usr/bin/env bash\nexit 0\n")
        (b / "uvicorn").chmod(0o755)


def test_a_venv_without_uvicorn_is_not_installed(tmp_path: Path):
    _venv(tmp_path, uvicorn=False)
    assert _probe(tmp_path) != 0


def test_a_venv_with_uvicorn_that_imports_is_installed(tmp_path: Path):
    _venv(tmp_path, uvicorn=True)
    assert _probe(tmp_path) == 0


def test_no_venv_is_not_installed(tmp_path: Path):
    assert _probe(tmp_path) != 0


def test_venv_uvicorn_is_shared_from_setup_sh():
    """Two phases read it (boot starts the API with it, app/install's probe
    requires it), so it lives in setup.sh — tests/test_single_phase_files.py's
    rule for what more than one phase uses."""
    assert "venv_uvicorn" in functions_in(SETUP.read_text(encoding="utf-8"))
    assert "venv_uvicorn" in installer_functions()["p_install"]
