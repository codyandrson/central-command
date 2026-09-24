"""Sequencing tests for deploy/single/update-run.sh (2026-09-03).

The runner is the detached process behind the cockpit's "Apply update now" on
the single-node profile: stop the API -> ./update.sh apply -> restart ->
health-check, rolling back on failure, writing the status.json the dialog
polls. Here it runs against a stub deploy/single (setup.sh / update.sh that
record their calls) and a stub `curl` whose /health verdict is a flag file —
no real API, podman or network anywhere.

The working directory is the STATE dir's `update/` since v2.42.0 (design
record 2026-09-23, D7 — nothing is written inside the checkout). CC_UPDATE_DIR
is how api/update.py hands the spawned runner the directory it is polling, and
it is how this rig pins it too.
"""

from __future__ import annotations

import json
import shutil
import os
import subprocess
import sys
from pathlib import Path

import pytest

RUNNER_SRC = Path(__file__).resolve().parents[1] / "deploy" / "single" / "update-run.sh"

# The harness shadows curl/podman/setup.sh with stub scripts on PATH. Git
# Bash PREPENDS /mingw64/bin:/usr/bin to whatever PATH it is handed, so on
# Windows the real curl always wins and every health poll runs against nothing
# (2026-09-17: three tests timed out). The runner itself is exercised on
# Windows by a real update; the sequencing harness is POSIX.

pytestmark = [
    pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash"),
    pytest.mark.skipif(
        sys.platform == "win32",
        reason="Git Bash prepends /mingw64/bin:/usr/bin to PATH; stub binaries cannot shadow curl/podman",
    ),
]


@pytest.fixture()
def rig(tmp_path):
    """A fake repo tree + PATH stubs. Returns (single_dir, run, calls_file)."""
    repo = tmp_path / "repo"
    single = repo / "deploy" / "single"
    single.mkdir(parents=True)
    (repo / "VERSION").write_text("version=2.21.1\n")
    (repo / ".env").write_text("CC_API_PORT=59321\n")
    # Where the runner writes status.json / apply.log — outside the (fake)
    # checkout, exactly as the API's _update_dir() resolves it.
    upd = tmp_path / "state" / "update"
    upd.mkdir(parents=True)
    calls = tmp_path / "calls.log"
    calls.touch()
    health_flag = tmp_path / "health-ok"

    stub_bin = tmp_path / "bin"
    stub_bin.mkdir()
    # curl = "is the API up?" — true iff the flag file exists.
    (stub_bin / "curl").write_text(f"#!/usr/bin/env bash\n[[ -f '{health_flag}' ]]\n")
    (stub_bin / "curl").chmod(0o755)

    def script(name: str, body: str) -> None:
        p = single / name
        p.write_text(f"#!/usr/bin/env bash\necho \"{name} $*\" >> '{calls}'\n{body}\n")
        p.chmod(0o755)

    # Defaults: stop drops the flag, boot raises it, apply succeeds.
    script("setup.sh", f"""
case "$1" in
  stop) rm -f '{health_flag}' ;;
  boot) touch '{health_flag}' ;;
esac
exit 0
""")
    script("update.sh", "exit 0")

    runner = upd / "run.sh"
    shutil.copyfile(RUNNER_SRC, runner)

    def run(target="2.22.0"):
        # The bash the product itself would use (Git's on Windows, where PATH's
        # first `bash` is WSL's launcher), and a PATH joined the way this OS
        # joins one — the stubs must shadow the real curl/podman for bash.
        from central_command.api.update import _bash
        env = {"PATH": os.pathsep.join([str(stub_bin), "/usr/bin", "/bin"]),
               "HOME": str(tmp_path), "CC_UPDATE_DIR": str(upd)}
        proc = subprocess.run(
            [_bash() or "bash", str(runner), target, str(single)],
            capture_output=True, text=True, timeout=120, env=env,
        )
        status = json.loads((upd / "status.json").read_text())
        return proc, status

    return single, run, calls, health_flag, script, upd


def _lines(calls: Path) -> list[str]:
    return calls.read_text().strip().splitlines()


def test_success_path_stops_applies_restarts(rig):
    single, run, calls, health_flag, _, upd = rig
    health_flag.touch()  # the API is up when the runner starts
    proc, status = run()
    assert proc.returncode == 0, proc.stderr
    assert _lines(calls) == ["setup.sh stop", "update.sh apply", "setup.sh boot"]
    assert status["state"] == "success"
    assert status["target"] == "2.22.0"
    # CC_UPDATE_DRIVEN must reach update.sh or a clean apply exits 3.
    log = (upd / "apply.log").read_text()
    assert "target v2.22.0" in log


def test_apply_failure_rolls_back(rig):
    single, run, calls, health_flag, script, _upd = rig
    script("update.sh", '[[ "$1" == apply ]] && { echo "FAIL merge: conflicts" ; exit 1; }\nexit 0')
    proc, status = run()
    assert proc.returncode == 1
    assert _lines(calls) == ["setup.sh stop", "update.sh apply", "update.sh rollback", "setup.sh boot"]
    assert status["state"] == "rolled_back"
    assert "FAIL merge" in status["error"]


def test_operator_pause_restarts_and_reports(rig):
    single, run, calls, _flag, script, _upd = rig
    script("update.sh", 'echo "USERACTION llm: the model catalog needs your attention"\nexit 3')
    proc, status = run()
    assert proc.returncode == 3
    assert "setup.sh boot" in _lines(calls)  # the cockpit must come back
    assert status["state"] == "failed"
    assert status["phase"] == "operator-action"
    assert "update.sh apply" in status["error"]  # the finish-it command


def test_unhealthy_restart_is_a_loud_failure(rig, tmp_path):
    single, run, calls, health_flag, script, upd = rig
    # boot "succeeds" but health never comes up.
    script("setup.sh", f"[[ \"$1\" == stop ]] && rm -f '{health_flag}'\nexit 0")
    # Patch the runner's health wait down so the test doesn't sit 90s.
    runner = upd / "run.sh"
    runner.write_text(runner.read_text().replace("seq 1 90", "seq 1 2"))
    proc, status = run()
    assert proc.returncode == 1
    assert status["state"] == "failed"
    assert status["phase"] == "restart"
