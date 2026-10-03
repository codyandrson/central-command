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

import contextlib
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

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


# ── running the installer for real ─────────────────────────────────────────
# About 45 tests drive the REAL `./setup.sh` / `./update.sh` against a temp copy
# of the tree. On Windows (Git Bash) each invocation costs ~20 s before its
# first line and the slowest such test measured 304 s (2026-10-02 testbed run,
# F13/F15) — far over the suite's 120 s per-test ceiling, which is right for
# everything else. Worse, there pytest-timeout can only use its `thread`
# method, which ends the WHOLE pytest process (`os._exit`) with no summary
# when one test overruns. So the driving tests get two limits, one number each:
#
# * DRIVER_RUN_TIMEOUT — one invocation. `run_driver` enforces it ITSELF,
#   kills the invocation's whole process TREE and fails THAT test, on every
#   platform. It is what keeps a hung script from ever reaching the session
#   killer.
# * DRIVER_TEST_TIMEOUT — the pytest-timeout backstop for the whole test
#   (`@drives_installer`), for a hang outside an invocation. `run_driver`
#   shrinks its own limit to fit inside what is left of it, so the in-test
#   timeout always fires first.
#
# `run_driver` refuses to run from a test that lacks the marker (the conftest
# hook below hands it the running item), so a new driving test cannot
# silently inherit the 120 s ceiling and take a Windows session down with it.

DRIVER_RUN_TIMEOUT = 600
DRIVER_TEST_TIMEOUT = 1500
_MARGIN = 60  # seconds left for the kill and the report inside the backstop

drives_installer = pytest.mark.timeout(DRIVER_TEST_TIMEOUT)

# (item, monotonic start) of the test now running — set by tests/conftest.py's
# pytest_runtest_protocol hook; None outside a test.
CURRENT: tuple[object, float] | None = None


def _marker_timeout(item) -> float | None:
    mark = item.get_closest_marker("timeout")
    if mark is None:
        return None
    value = mark.args[0] if mark.args else mark.kwargs.get("timeout")
    return float(value) if value is not None else None


def _budget(timeout: float) -> float:
    if CURRENT is None:
        return timeout
    item, started = CURRENT
    ceiling = _marker_timeout(item)
    if ceiling is None or ceiling < DRIVER_TEST_TIMEOUT:
        pytest.fail(
            f"{getattr(item, 'nodeid', item)} runs the real installer but is not marked "
            "@drives_installer (tests/installer_source.py): under the suite's 120 s "
            "ceiling it would, on Windows, end the whole pytest session")
    left = ceiling - (time.monotonic() - started) - _MARGIN
    return max(1.0, min(timeout, left))


def _kill_tree(proc: subprocess.Popen) -> None:
    """The invocation AND everything it started. Killing bash alone leaves its
    children holding the output pipes, and reading them would then block
    forever — the very hang this exists to end."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       capture_output=True, timeout=60, check=False)
    else:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGKILL)
    with contextlib.suppress(Exception):
        proc.kill()


def run_driver(argv: list[str], *, cwd: Path, env: dict[str, str] | None = None,
               timeout: float = DRIVER_RUN_TIMEOUT,
               input: str | None = None) -> subprocess.CompletedProcess:
    """`subprocess.run(argv, capture_output=True, text=True)` for a real
    installer invocation, with stdin closed (or fed `input`) — but a timeout
    kills the process tree and FAILS the calling test instead of hanging it."""
    limit = _budget(timeout)
    proc = subprocess.Popen(
        argv, cwd=cwd, env=env, text=True, encoding="utf-8", errors="replace",
        stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        # Its own process group / session, so the tree can be killed as one.
        **({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
           else {"start_new_session": True}),
    )
    try:
        out, err = proc.communicate(input=input, timeout=limit)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        try:
            out, err = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            out, err = "", "(output not collected: the pipes stayed open after the kill)"
        pytest.fail(
            f"{' '.join(map(str, argv))} (in {cwd}) did not finish within {limit:.0f} s — "
            f"killed with its children.\n--- stdout (tail)\n{(out or '')[-4000:]}"
            f"\n--- stderr (tail)\n{(err or '')[-4000:]}")
    return subprocess.CompletedProcess(argv, proc.returncode, out, err)


# ── stubs that win on PATH, on Linux AND under Git Bash ─────────────────────
# Git for Windows' `bin\bash.exe` is a launcher that PREPENDS
# `/mingw64/bin:/usr/bin` to whatever PATH it is handed — so a stub `curl`
# placed first on the PATH a test passes in is shadowed by Git's own
# `/mingw64/bin/curl` (2026-10-02 testbed run, F15: the mint-key stub's
# argv.log was never written). A stub with no namesake there (`podman`, `uv`)
# was found either way, which is why only some harnesses failed. BASH_ENV is
# read by every non-interactive bash AFTER that launcher has set PATH, so the
# file it names puts the stub directory back in front, in the POSIX spelling
# bash resolves (cygpath on MSYS; identity elsewhere). Children inherit the
# fixed PATH.
#
# "In front" means FIRST, not "present": the stub directory is already on the
# PATH the launcher hands over — just behind its `/mingw64/bin:/usr/bin:~/bin`
# prefix — so a guard that only asked "is it on PATH?" left it shadowed
# (measured in the 2026-10-02 testbed run's second pass: PATH read
# `/mingw64/bin:/usr/bin:/c/Users/<u>/bin:/tmp/<x>/stub:…`, and `type -a curl`
# found `/mingw64/bin/curl` first). Every occurrence is removed and one is put
# at the front, so a nested bash leaves it where it is.

_BASH_ENV = r"""__cc_stub='@DIR@'
if command -v cygpath >/dev/null 2>&1; then __cc_stub="$(cygpath -u "$__cc_stub")"; fi
case "$PATH:" in
  "$__cc_stub:"*) ;;
  *) __cc_rest=":$PATH:"
     while [[ "$__cc_rest" == *":$__cc_stub:"* ]]; do __cc_rest="${__cc_rest/":$__cc_stub:"/:}"; done
     __cc_rest="${__cc_rest#:}"; __cc_rest="${__cc_rest%:}"
     PATH="$__cc_stub${__cc_rest:+:$__cc_rest}"; export PATH; unset __cc_rest ;;
esac
unset __cc_stub
"""


def with_stub_path(env: dict[str, str], stub_dir: Path) -> dict[str, str]:
    """`env` with `stub_dir` first on PATH — first for real, under Git Bash too.

    A harness's MINIMAL env also needs SYSTEMROOT (and WINDIR) on Windows —
    a no-op elsewhere: without it Winsock fails inside the bash that Python
    starts, so `/dev/tcp` read "socket: Permission denied" and port_listener
    saw no listener (the 2026-10-02 testbed run's second pass)."""
    hook = stub_dir / ".bash_env.sh"
    write_lf(hook, _BASH_ENV.replace("@DIR@", stub_dir.as_posix()))
    out = dict(env)
    out["PATH"] = f"{stub_dir}{os.pathsep}{env.get('PATH', '')}"
    out["BASH_ENV"] = hook.as_posix()
    if os.name == "nt":
        for key in ("SYSTEMROOT", "WINDIR"):
            if key not in out and os.environ.get(key):
                out[key] = os.environ[key]
    return out


def python_shim(stub_dir: Path) -> Path:
    """A `python3` in `stub_dir` that runs THIS interpreter. The installer's
    `$PY` is the first `python3`/`python` that runs (cc_resolve_py), else
    `uv run … python` — and on Windows the only `python3` on PATH is the
    Microsoft Store alias, which exits 49, so `$PY` became `uv run`: in a
    harness that stubs `uv`, every `$PY -c` then printed NOTHING (the
    2026-10-02 testbed run's second pass: the mint-key harness read no key out
    of the stub proxy's JSON, and no scope). With this shim first on PATH
    (`with_stub_path`), `$PY` is a real interpreter on every host."""
    return write_lf(stub_dir / "python3",
                    f'#!/usr/bin/env bash\nexec "{Path(sys.executable).as_posix()}" "$@"\n',
                    mode=0o755)


def env_path(path: Path | str) -> str:
    """A path as a temp `.env` must spell it: forward slashes on every host.
    setup.sh SOURCES `.env`, so bash drops the backslashes of a Windows
    `str(Path)` (it would read `C:Users…`), and check's answers section FAILs
    a backslash path answer outright (v_path*, since the 2026-10-02 testbed
    run's second pass) — a harness that wrote `str(state)` stopped its own
    driver at `check` on Windows (F25). Every harness writes path VALUES into
    an answer file through this."""
    return Path(path).as_posix()


def write_lf(path: Path, text: str, *, mode: int | None = None) -> Path:
    """Write `text` with LF line endings on every OS. `Path.write_text` turns
    each "\\n" into "\\r\\n" on Windows, and a stub's DATA read back by
    `read -r` then carries a trailing CR that no comparison matches (F15: the
    n8n credential names in human_rows' stub)."""
    path.write_text(text, encoding="utf-8", newline="\n")
    if mode is not None:
        path.chmod(mode)
    return path
