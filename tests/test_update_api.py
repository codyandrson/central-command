"""Wire-shape tests for the single-server update routes (2026-09-03).

Same idiom as tests/test_graph_routes.py: call the handlers directly,
monkeypatch the process/git seams, assert the exact JSON the cockpit's
UpdateDialog declares its own interface over. Nothing here runs update.sh,
spawns a runner, or touches the network.
"""

from __future__ import annotations

import json
import subprocess
import time

import pytest

from central_command.api import update


def _body(resp) -> dict:
    return json.loads(resp.body)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("CC_UPDATE_DIR", str(tmp_path / "upd"))
    monkeypatch.setattr(update, "_version_cache", None)
    yield


class _FakeRequest:
    def __init__(self, chunks: list[bytes], query: dict | None = None):
        self._chunks = chunks
        self.query_params = query or {}

    async def stream(self):
        for c in self._chunks:
            yield c


def _stamp(secs_ago: int = 0) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - secs_ago))


# ── /api/version/check ──────────────────────────────────────────────────────

async def test_version_check_reports_available_update(monkeypatch):
    monkeypatch.setattr(update, "read_product_version", lambda: "2.21.1")

    async def resolved():
        return ("2.22.0", "release")

    monkeypatch.setattr(update, "_latest_release_version", resolved)
    body = _body(await update.version_check(_FakeRequest([])))
    assert body["current"] == "2.21.1"
    assert body["latest"] == "2.22.0"
    assert body["updateAvailable"] is True
    assert body["projectDir"]


async def test_version_check_airgapped_points_at_the_file_path(monkeypatch):
    monkeypatch.setattr(update, "read_product_version", lambda: "2.21.1")

    async def unresolved():
        return None

    monkeypatch.setattr(update, "_latest_release_version", unresolved)
    body = _body(await update.version_check(_FakeRequest([])))
    assert body["updateAvailable"] is False
    assert "zip" in body["error"]


# ── /api/update/status ──────────────────────────────────────────────────────

async def test_status_shape_and_staleness(tmp_path, monkeypatch):
    upd = tmp_path / "upd"
    upd.mkdir()
    monkeypatch.setenv("CC_UPDATE_DIR", str(upd))

    body = _body(await update.update_status())
    assert body == {
        "mode": "local",
        "pending": False,
        "inFlight": False,
        "status": None,
        "stage": {"pending": False, "inFlight": False, "status": None},
    }

    (upd / "status.json").write_text(json.dumps(
        {"state": "running", "phase": "apply", "updated_at": _stamp(0)}))
    assert _body(await update.update_status())["inFlight"] is True

    # A "running" older than the runner could possibly still be = a dead run.
    (upd / "status.json").write_text(json.dumps(
        {"state": "running", "phase": "apply", "updated_at": _stamp(update.STALE_RUNNING_SECS + 60)}))
    assert _body(await update.update_status())["inFlight"] is False


# ── /api/update/upload ──────────────────────────────────────────────────────

async def test_upload_stages_and_reports_target(tmp_path, monkeypatch):
    monkeypatch.setattr(update, "read_product_version", lambda: "2.21.1")
    monkeypatch.setattr(update, "_staged_target", lambda: "2.22.0")
    calls: list[tuple] = []

    def fake_run(*args):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="PASS import: ok\n", stderr="")

    monkeypatch.setattr(update, "_run_update_sh", fake_run)
    resp = await update.update_upload(_FakeRequest([b"PK\x03\x04zipbytes"]))
    assert resp.status_code == 200
    body = _body(resp)
    assert body["target"] == "2.22.0"
    assert body["updateAvailable"] is True
    assert calls and calls[0][0] == "stage"
    # The staged record is what makes the dialog offer "Apply update now".
    stage = json.loads((update._update_dir() / "stage.json").read_text())
    assert stage["state"] == "success" and stage["target"] == "2.22.0"


async def test_upload_rejects_empty_and_failed_stage(monkeypatch):
    monkeypatch.setattr(update, "read_product_version", lambda: "2.21.1")
    assert (await update.update_upload(_FakeRequest([]))).status_code == 422

    monkeypatch.setattr(update, "_staged_target", lambda: None)
    monkeypatch.setattr(update, "_run_update_sh", lambda *a: subprocess.CompletedProcess(
        a, 1, stdout="FAIL zip-shape: not a source zip\n", stderr=""))
    resp = await update.update_upload(_FakeRequest([b"junk"]))
    assert resp.status_code == 422
    assert "zip-shape" in _body(resp)["error"]


async def test_upload_refuses_while_a_run_is_in_flight(monkeypatch):
    upd = update._update_dir()
    upd.mkdir(parents=True, exist_ok=True)
    (upd / "status.json").write_text(json.dumps(
        {"state": "running", "phase": "apply", "updated_at": _stamp(0)}))
    assert (await update.update_upload(_FakeRequest([b"x"]))).status_code == 409


# ── /api/update/apply ───────────────────────────────────────────────────────

async def test_apply_spawns_the_runner_detached(monkeypatch):
    monkeypatch.setattr(update, "read_product_version", lambda: "2.21.1")
    monkeypatch.setattr(update, "_staged_target", lambda: "2.22.0")
    spawned: list[list[str]] = []
    monkeypatch.setattr(update, "_spawn_detached", spawned.append)

    resp = await update.update_apply(update.ApplyRequest(target="2.22.0"))
    assert resp.status_code == 202
    assert _body(resp) == {"triggered": True, "target": "2.22.0"}
    # A copy runs, never the tracked file — the merge rewrites it mid-run.
    assert spawned and spawned[0][1] == str(update._update_dir() / "run.sh")
    assert (update._update_dir() / "run.sh").exists()
    status = json.loads((update._update_dir() / "status.json").read_text())
    assert status["state"] == "running" and status["phase"] == "requested"


async def test_apply_409s(monkeypatch):
    monkeypatch.setattr(update, "read_product_version", lambda: "2.21.1")
    monkeypatch.setattr(update, "_staged_target", lambda: None)

    async def no_download(target):
        return "no GitHub origin to download from"

    monkeypatch.setattr(update, "_download_and_stage", no_download)

    # Re-applying the installed version: the stale-button trap.
    assert (await update.update_apply(update.ApplyRequest(target="2.21.1"))).status_code == 409
    # Nothing staged and the download path can't help.
    assert (await update.update_apply(update.ApplyRequest(target="2.22.0"))).status_code == 502
    assert (await update.update_apply(update.ApplyRequest())).status_code == 409

    monkeypatch.setattr(update, "_staged_target", lambda: "2.19.0")
    resp = await update.update_apply(update.ApplyRequest(target="2.22.0"))
    assert resp.status_code == 502  # mismatch triggers a re-download attempt first

    # In-flight guard beats everything.
    upd = update._update_dir()
    upd.mkdir(parents=True, exist_ok=True)
    (upd / "status.json").write_text(json.dumps(
        {"state": "running", "phase": "apply", "updated_at": _stamp(0)}))
    assert (await update.update_apply(update.ApplyRequest(target="2.22.0"))).status_code == 409
