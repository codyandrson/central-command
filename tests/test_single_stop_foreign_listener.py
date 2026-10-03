"""`stop` never kills a listener this install has no record of starting.

On Windows `proc_halt` falls back to the process `netstat` names on the port,
because a `kill` under Git Bash can report a success it never delivered. With
NO pid file and NO unit that fallback used to fire too — so `./setup.sh stop`
in a second tree on the default ports (a test's temp copy, another install)
would `taskkill` whatever listened there, a live API included. The fallback
now needs a record (`HALT_HOW` set); a foreign listener is reported, not shot.

`proc_halt` is lifted from the installer source and run with `taskkill`,
`netstat` and `port_listener` stubbed as shell functions, so the Windows
branch runs on Linux.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from tests.installer_source import installer_functions
from tests.test_single_driver_ledger import _bash_exe

_STUBS = r"""
note() { :; }
proc_pid_is_ours() { return 0; }
proc_signal() { :; }
sleep() { :; }
seq() { echo 1; }
port_listener() { return 0; }                     # the port is always held
netstat() { echo "  TCP    127.0.0.1:$PORT     0.0.0.0:0   LISTENING   4242"; }
taskkill() { echo "taskkill $*" >>"$LOG"; }
"""


def _halt(tmp_path: Path, *, pid_file: bool) -> tuple[int, str, str]:
    funcs = installer_functions()
    state = tmp_path / "state"
    state.mkdir()
    log = tmp_path / "kills.log"
    log.write_text("")
    pidf = state / "uvicorn.pid"
    if pid_file:
        pidf.write_text("99999\n")
    script = (
        f'STATE_DIR="{state.as_posix()}"; LOG="{log.as_posix()}"; PORT=18080\n'
        + _STUBS
        + f"proc_halt() {{\n{funcs['proc_halt']}\n}}\n"
        + f'proc_halt "{pidf.as_posix()}" "$PORT" ""; rc=$?; echo "HOW=$HALT_HOW"; exit $rc\n'
    )
    r = subprocess.run([_bash_exe(), "-c", script], capture_output=True, text=True)
    return r.returncode, r.stdout, log.read_text()


def test_a_listener_with_no_record_is_not_killed(tmp_path: Path):
    rc, out, kills = _halt(tmp_path, pid_file=False)
    assert rc == 1, out
    assert kills == "", f"killed a process this install never started: {kills!r}"
    assert "HOW=" in out and "taskkill" not in out


def test_a_listener_this_install_started_is_still_killed_when_the_signal_did_not_land(tmp_path: Path):
    rc, out, kills = _halt(tmp_path, pid_file=True)
    assert "4242" in kills, f"the recorded process's listener was not killed: {kills!r} / {out}"
    assert "taskkill /T of the listener" in out
