"""The updater's restart branch: request.json `"action": "restart"` restarts the
API process and nothing else (Settings > Connection, the card behind
`api/hold.py`'s restart hold).

Same harness as `test_k3s_update_reconcile.py`: the updater's own functions are
lifted out of cc-update.sh (never copied) and run in bash against a temp state
directory, with `systemctl`, `curl`, `sleep` and `require_compute_ssh` stubbed to
record what they were asked. What this proves is the script's branching and its
two status files; that systemd really restarts cc-uvicorn, and that
cc-update.path hands this script the request, only a real apply shows.

* an absent or "update" action is the update path, and so is a request file that
  cannot be read; every other value dies at resolve and changes nothing;
* the restart branch runs BEFORE every update-only precondition, restarts only
  cc-uvicorn, polls health, and writes restart.json — never status.json (the
  last UPDATE's result, which the Updates card reads);
* the busy gate honours `force` exactly as an update does, an update in flight
  refuses, and an unhealthy API is reported, never rolled back;
* the trigger directory's own request file is consumed, so a later hand-started
  run does not replay it.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
UPDATER = (ROOT / "deploy" / "k3s" / "cc-update.sh").read_text(encoding="utf-8")


def _fn(name: str) -> str:
    start = UPDATER.index(f"\n{name}() {{") + 1
    return UPDATER[start : UPDATER.index("\n}\n", start) + 3]


@pytest.fixture
def box(tmp_path: Path) -> Path:
    if os.name == "nt" or not shutil.which("bash"):
        pytest.skip("POSIX bash needed")
    (tmp_path / "state").mkdir()
    return tmp_path


def _run(
    box: Path,
    entry: str,
    request: object | None = None,
    *,
    sessions: int = 0,
    health_rc: int = 0,
    systemctl_rc: int = 0,
    status: dict | None = None,
    raw_request: str | None = None,
) -> subprocess.CompletedProcess:
    if raw_request is not None:
        (box / "request.json").write_text(raw_request)
    elif request is not None:
        (box / "request.json").write_text(json.dumps(request))
    if status is not None:
        (box / "state" / "status.json").write_text(json.dumps(status))
    rows = ", ".join('{"agent_id": "ea"}' for _ in range(sessions))
    script = "\n".join(
        [
            "set -uo pipefail",
            f"B='{box}'",
            'STATEDIR="$B/state"; STATUS="$STATEDIR/status.json"; RESTART_STATUS="$STATEDIR/restart.json"',
            'REQUEST="$B/request.json"; STARTED_AT=2026-11-16T10:00:00Z',
            "FORCE=0; RESTART_MODE=0; STOPPED=0; STAGE_MODE=0",
            'note() { printf "%s\\n" "$*"; }',
            'systemctl() { echo "systemctl $*" >>"$B/calls"; return ' + str(systemctl_rc) + "; }",
            'start_services() { echo start_services >>"$B/calls"; }',
            "sleep() { :; }",
            "curl() {",
            '  case "$*" in',
            f"    *sessions*) echo '{{\"sessions\": [{rows}]}}' ;;",
            f"    *health*) return {health_rc} ;;",
            "  esac",
            "}",
            'require_compute_ssh() { echo "require_compute_ssh" >>"$B/calls"; exit 99; }',
            _fn("write_status"),
            _fn("running_sessions"),
            _fn("refuse_if_busy"),
            _fn("healthy"),
            _fn("apply_in_flight"),
            _fn("die"),
            _fn("request_action"),
            _fn("write_restart"),
            _fn("restart_main"),
            _fn("main"),
            _fn("dispatch"),
            entry,
        ]
    )
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60)


def _calls(box: Path) -> list[str]:
    f = box / "calls"
    return f.read_text().splitlines() if f.exists() else []


def _restart(box: Path) -> dict:
    return json.loads((box / "state" / "restart.json").read_text())


REQ = {
    "target": "",
    "force": False,
    "action": "restart",
    "requested_at": "2026-11-16T09:59:00+00:00",
}


# ── what a restart does ─────────────────────────────────────────────────────


def test_a_restart_restarts_only_the_api_and_records_its_own_outcome(box: Path):
    r = _run(box, "dispatch", REQ)
    assert r.returncode == 0, r.stdout + r.stderr
    assert _calls(box) == ["systemctl restart cc-uvicorn"]
    out = _restart(box)
    assert out["state"] == "restarted" and out["forced"] is False
    assert out["requested_at"] == REQ["requested_at"], (
        "the cockpit tells its own request from an older one by this"
    )
    assert {"message", "started_at", "updated_at", "finished_at"} <= set(out)
    assert not (box / "state" / "status.json").exists(), "status.json is the last UPDATE's result"
    assert not (box / "request.json").exists(), "consumed, so a hand-started run does not replay it"


def test_a_restart_never_reaches_an_update_only_step(box: Path):
    r = _run(box, "dispatch", REQ)
    assert r.returncode == 0 and "require_compute_ssh" not in _calls(box)


def test_the_busy_gate_refuses_unforced_and_forced_goes_on_naming_the_cost(box: Path):
    r = _run(box, "dispatch", REQ, sessions=2)
    assert r.returncode == 1, r.stdout
    assert _calls(box) == [], "nothing was restarted"
    out = _restart(box)
    assert out["state"] == "refused" and "2 agent run(s) in flight" in out["message"]
    assert not (box / "state" / "status.json").exists()

    r = _run(box, "dispatch", {**REQ, "force": True}, sessions=2)
    assert r.returncode == 0 and "WARNING: forced" in r.stdout, r.stdout
    assert _calls(box)[-1] == "systemctl restart cc-uvicorn"
    assert _restart(box)["state"] == "restarted" and _restart(box)["forced"] is True


def test_an_unhealthy_api_is_reported_never_rolled_back(box: Path):
    r = _run(box, "dispatch", REQ, health_rc=1)
    assert r.returncode == 1
    assert _calls(box) == ["systemctl restart cc-uvicorn"], (
        "no second restart, no start_services, no rollback"
    )
    out = _restart(box)
    assert out["state"] == "failed" and "journalctl -u cc-uvicorn" in out["message"]
    assert not (box / "state" / "status.json").exists()


def test_a_failed_systemctl_is_a_failed_restart(box: Path):
    r = _run(box, "dispatch", REQ, systemctl_rc=1)
    assert r.returncode == 1
    assert (
        _restart(box)["state"] == "failed"
        and "journalctl -u cc-uvicorn" in _restart(box)["message"]
    )
    assert "start_services" not in _calls(box)


def test_an_update_in_flight_refuses_the_restart_and_a_stale_record_does_not(box: Path):
    fresh = {
        "state": "running",
        "phase": "rebuild",
        "updated_at": __import__("datetime")
        .datetime.now(__import__("datetime").timezone.utc)
        .isoformat(),
    }
    r = _run(box, "dispatch", REQ, status=fresh)
    assert r.returncode == 1 and _calls(box) == []
    assert _restart(box)["state"] == "refused" and "update is in flight" in _restart(box)["message"]
    assert json.loads((box / "state" / "status.json").read_text()) == fresh, (
        "left exactly as the update wrote it"
    )

    stale = {"state": "running", "phase": "rebuild", "updated_at": "2020-01-01T00:00:00+00:00"}
    r = _run(box, "dispatch", REQ, status=stale)
    assert r.returncode == 0 and _restart(box)["state"] == "restarted"


# ── what is an update and what is not ───────────────────────────────────────


@pytest.mark.parametrize(
    "request_body",
    [
        {"target": "2.0.0", "force": False},  # absent action: today's request
        {"target": "2.0.0", "force": False, "action": "update"},
    ],
)
def test_an_absent_or_update_action_takes_the_update_path(box: Path, request_body: dict):
    r = _run(box, "dispatch", request_body)
    assert r.returncode == 99, (
        r.stdout + r.stderr
    )  # the require_compute_ssh stub: the update's first step
    assert _calls(box) == ["require_compute_ssh"]
    assert not (box / "state" / "restart.json").exists()


def test_no_request_file_and_an_unreadable_one_are_updates_as_they_always_were(box: Path):
    assert _run(box, "dispatch").returncode == 99
    (box / "calls").unlink()
    assert _run(box, "dispatch", raw_request="{not json").returncode == 99
    assert _calls(box) == ["require_compute_ssh"]


@pytest.mark.parametrize("action", ["reboot", "Restart", "", None, 5, ["restart"]])
def test_any_other_action_dies_at_resolve_and_changes_nothing(box: Path, action):
    r = _run(box, "dispatch", {"target": "2.0.0", "force": True, "action": action})
    assert r.returncode == 1, r.stdout + r.stderr
    assert _calls(box) == [], "no service touched, no update step reached"
    status = json.loads((box / "state" / "status.json").read_text())
    assert status["state"] == "failed" and status["phase"] == "resolve"
    assert "unknown action" in status["error"] and "nothing was changed" in status["error"]
    assert not (box / "state" / "restart.json").exists()


# ── the source, for what no stub can show ───────────────────────────────────


def test_the_restart_branch_is_the_api_alone_and_comes_first():
    body = _fn("restart_main")
    assert re.findall(r"systemctl\s+\S+\s+\S+", body) == ["systemctl restart cc-uvicorn"]
    for forbidden in (
        "cc-nerve",
        "cc-sandbox-runner",
        "start_services",
        "kubectl",
        "k3s",
        "${K[@]}",
        "COMPUTE_SSH",
        "ssh",
        "git ",
        "fetch",
        "CKPT",
        "worktree",
        "backup",
        "dump",
        "prebuild",
        "write_status",
        "phase ",
        "rollback",
        "reset",
    ):
        assert forbidden not in body, forbidden
    assert "refuse_if_busy busy" in body and "healthy" in body and "apply_in_flight" in body
    # The action is dispatched before main, whose first act stays the compute-node
    # answer (tests/test_k3s_compute_ssh.py pins that): a restart never enters main.
    dispatch = _fn("dispatch")
    assert dispatch.index("request_action") < dispatch.index("restart_main")
    assert "restart_main" not in _fn("main") and "require_compute_ssh" not in dispatch
    assert UPDATER.rstrip().endswith('else\n  dispatch "$@"\nfi')
    # Only this script's own step restarts a unit; no other function gained a restart.
    assert UPDATER.count("systemctl restart cc-uvicorn") == 1


def test_a_restart_has_its_own_status_file_and_die_reports_there():
    assert 'RESTART_STATUS="$STATEDIR/restart.json"' in UPDATER
    assert "write_status" not in _fn("write_restart")
    assert 'mv -f "$tmp" "$RESTART_STATUS"' in _fn("write_restart")
    die = _fn("die")
    assert "RESTART_MODE" in die and die.index("write_restart") < die.index("write_status")


def test_the_unit_files_are_untouched_the_seam_is_the_existing_one():
    # No new unit, path unit, sudoers or polkit rule: the request rides cc-update.path.
    names = sorted(p.name for p in (ROOT / "deploy" / "k3s").glob("cc-update*"))
    assert names == [
        "cc-update-stage.path",
        "cc-update-stage.service.in",
        "cc-update-tmpfiles.conf.in",
        "cc-update.path",
        "cc-update.service.in",
        "cc-update.sh",
    ]
