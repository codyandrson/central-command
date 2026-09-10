"""Updates for the single-server cockpit: /api/version/check + /api/update/*.

The Nerve cockpit's update surface lives in its Node server
(web/server/routes/version-check.ts, cc-update.ts) — the k3s profile, where a
root systemd helper performs the update. The single-node profile never runs
that server (app.py serves web/dist itself), so these routes existed nowhere
there: "Check for updates" 404'd and nothing could apply — found on the
air-gapped Windows deployment, 2026-09-03. Same wire shapes the cockpit
already speaks, plus the from-file path the air-gapped install needs:

  GET  /api/version/check      VERSION file vs GitHub release/tag (online only)
  POST /api/update/upload      body = a source zip -> ./update.sh stage (init/
                               import/plan; safe under a live API) -> the
                               staged target version
  POST /api/update/apply       spawn deploy/single/update-run.sh DETACHED —
                               it stops this very process, drives
                               ./update.sh apply, restarts, health-checks,
                               rolls back on failure
  GET  /api/update/status      the runner's status.json + the staged target,
                               in cc-update.ts's UpdateProgress shape
                               (mode:"local" tells the dialog there is no
                               systemd helper here)

The apply stays gated on the operator's explicit click, and update.sh keeps
every real guard (version gate, DB backup, merge, rollback) — this module
only moves bytes and starts the runner.
"""

from __future__ import annotations

import asyncio
import calendar
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

router = APIRouter(prefix="/api")

REPO_ROOT = Path(__file__).resolve().parents[2]
SINGLE_DIR = REPO_ROOT / "deploy" / "single"


def _update_dir() -> Path:  # env seam so tests never touch the real tree
    return Path(os.environ.get("CC_UPDATE_DIR", str(SINGLE_DIR / ".update")))


def _bash() -> str | None:
    """The bash that can run deploy/single/*.sh.

    On Windows, PATH's `bash` is often System32's WSL launcher — a shell in a
    different filesystem where this repo's paths mean nothing. Git Bash is the
    documented prerequisite there; resolve it from git's own install.
    """
    if sys.platform == "win32":
        git = shutil.which("git")
        if git:
            for rel in ("../../bin/bash.exe", "../../usr/bin/bash.exe"):
                candidate = (Path(git).parent / rel).resolve()
                if candidate.is_file():
                    return str(candidate)
    return shutil.which("bash")


MAX_ZIP_BYTES = 500 * 1024 * 1024
# Mirrors cc-update.ts STALE_RUNNING_MS: a "running" older than this is a
# dead runner, not an in-flight update.
STALE_RUNNING_SECS = 5700
CHECK_CACHE_SECS = 3600
_SEMVER_TAG = re.compile(r"^v?(\d+\.\d+\.\d+)$")

_version_cache: dict | None = None  # {"latest": str, "source": str, "checked_at": float}


def read_product_version() -> str:
    try:
        m = re.search(r"^version=(.+)$", (REPO_ROOT / "VERSION").read_text(), re.M)
        return m.group(1).strip() if m else "0.0.0"
    except OSError:
        return "0.0.0"


def _semver_key(v: str) -> tuple[int, int, int]:
    parts = (v.split("-")[0].split("+")[0].split(".") + ["0", "0", "0"])[:3]
    try:
        return tuple(int(p) for p in parts)  # type: ignore[return-value]
    except ValueError:
        return (0, 0, 0)


def compare_semver(a: str, b: str) -> int:
    ka, kb = _semver_key(a), _semver_key(b)
    return (ka > kb) - (ka < kb)


def _git(*args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO_ROOT), *args],
            capture_output=True, text=True, timeout=30,
        )
        return out.stdout.strip() if out.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _github_repo() -> tuple[str, str] | None:
    origin = _git("remote", "get-url", "origin")
    if origin:
        m = re.search(r"github\.com[:/]([^/]+)/([^/.]+)", origin)
        if m:
            return m.group(1), m.group(2)
    return None


async def _latest_release_version() -> tuple[str, str] | None:
    """(version, source) from the GitHub release API, then a tag fallback."""
    repo = _github_repo()
    if repo:
        headers = {"Accept": "application/vnd.github+json"}
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"https://api.github.com/repos/{repo[0]}/{repo[1]}/releases/latest",
                    headers=headers,
                )
            if resp.status_code == 200:
                tag = _SEMVER_TAG.match(str(resp.json().get("tag_name", "")))
                if tag:
                    return tag.group(1), "release"
        except httpx.HTTPError:
            pass
    tags = _git("ls-remote", "--tags", "origin")
    if tags:
        versions = [
            m.group(1)
            for line in tags.splitlines()
            if (m := _SEMVER_TAG.match(line.rsplit("refs/tags/", 1)[-1].strip()))
        ]
        if versions:
            return max(versions, key=_semver_key), "tag"
    return None


@router.get("/version/check")
async def version_check(request: Request) -> JSONResponse:
    global _version_cache
    current = read_product_version()
    now = time.time()
    force = request.query_params.get("force") == "1"
    if force or not _version_cache or now - _version_cache["checked_at"] > CHECK_CACHE_SECS:
        resolved = await _latest_release_version()
        if resolved is None:
            return JSONResponse({
                "current": current,
                "latest": None,
                "updateAvailable": False,
                "projectDir": str(REPO_ROOT),
                "error": "could not resolve the latest release (no network, or no git origin) "
                         "— air-gapped installs update from a downloaded zip instead",
            })
        _version_cache = {"latest": resolved[0], "source": resolved[1], "checked_at": now}
    return JSONResponse({
        "current": current,
        "latest": _version_cache["latest"],
        "source": _version_cache["source"],
        "updateAvailable": compare_semver(_version_cache["latest"], current) > 0,
        "projectDir": str(REPO_ROOT),
        "checkedAt": int(_version_cache["checked_at"] * 1000),
    })


# ── the runner's records ────────────────────────────────────────────────────

def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _in_flight(status: dict | None) -> bool:
    if not status or status.get("state") != "running":
        return False
    stamp = status.get("updated_at") or status.get("started_at")
    if not stamp:
        return True
    try:
        then = calendar.timegm(time.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ"))
    except ValueError:
        return True
    return time.time() - then < STALE_RUNNING_SECS


def _staged_target() -> str | None:
    """The version sitting on the `upstream` branch, if update.sh staged one."""
    text = _git("show", "upstream:VERSION")
    if text:
        m = re.search(r"^version=(.+)$", text, re.M)
        if m:
            return m.group(1).strip()
    return None


@router.get("/update/status")
async def update_status() -> JSONResponse:
    status = _read_json(_update_dir() / "status.json")
    stage = _read_json(_update_dir() / "stage.json")
    return JSONResponse({
        "mode": "local",
        "pending": False,
        "inFlight": _in_flight(status),
        "status": status,
        "stage": {"pending": False, "inFlight": False, "status": stage},
    })


# ── upload + stage ──────────────────────────────────────────────────────────

def _run_update_sh(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [_bash() or "bash", str(SINGLE_DIR / "update.sh"), *args],
        capture_output=True, text=True, cwd=str(SINGLE_DIR), timeout=600,
    )


def _protocol_failures(out: str) -> str:
    lines = [ln for ln in out.splitlines() if ln.startswith(("FAIL ", "USERACTION "))]
    return " · ".join(lines) or "see deploy/single/setup-log.txt"


def _write_stage_record(state: str, target: str, error: str | None = None) -> None:
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    record: dict = {
        "state": state,
        "phase": "staged" if state == "success" else "stage",
        "current": read_product_version(),
        "target": target,
        "started_at": now,
        "updated_at": now,
        "finished_at": now,
    }
    if error:
        record["error"] = error
    upd = _update_dir()
    upd.mkdir(parents=True, exist_ok=True)
    tmp = upd / "stage.json.tmp"
    tmp.write_text(json.dumps(record))
    tmp.replace(upd / "stage.json")


@router.post("/update/upload")
async def update_upload(request: Request) -> JSONResponse:
    if _bash() is None or not (SINGLE_DIR / "update.sh").is_file():
        return JSONResponse({"error": "deploy/single/update.sh is not available on this install"}, status_code=503)
    if _in_flight(_read_json(_update_dir() / "status.json")):
        return JSONResponse({"error": "an update is already running"}, status_code=409)

    upd = _update_dir()
    upd.mkdir(parents=True, exist_ok=True)
    dest = upd / "upload.zip"
    size = 0
    try:
        with dest.open("wb") as fh:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_ZIP_BYTES:
                    return JSONResponse({"error": f"zip exceeds {MAX_ZIP_BYTES // (1024 * 1024)} MB"}, status_code=413)
                fh.write(chunk)
    except OSError as exc:
        return JSONResponse({"error": f"could not store the upload: {exc}"}, status_code=500)
    if size == 0:
        return JSONResponse({"error": "empty upload — send the downloaded source zip as the request body"}, status_code=422)

    # stage = init-if-needed + import + plan; only the `upstream` branch moves,
    # so this is safe while the API (this process) keeps serving.
    proc = await asyncio.to_thread(_run_update_sh, "stage", str(dest))
    target = _staged_target()
    if proc.returncode not in (0, 2, 3) or not target:
        error = _protocol_failures(proc.stdout)
        _write_stage_record("failed", target or "", error)
        return JSONResponse({"error": f"the zip did not stage: {error}"}, status_code=422)

    current = read_product_version()
    update_available = compare_semver(target, current) > 0
    _write_stage_record("success", target)
    return JSONResponse({
        "target": target,
        "current": current,
        "updateAvailable": update_available,
        "projectDir": str(REPO_ROOT),
        "plan": proc.stdout,
    })


# ── apply ───────────────────────────────────────────────────────────────────

async def _download_and_stage(target: str) -> str | None:
    """Fetch v<target>'s source zip from origin and stage it. None on success."""
    repo = _github_repo()
    if repo is None:
        return "no GitHub origin to download from — use Update from file with a downloaded zip"
    url = f"https://github.com/{repo[0]}/{repo[1]}/archive/refs/tags/v{target}.zip"
    upd = _update_dir()
    upd.mkdir(parents=True, exist_ok=True)
    dest = upd / "upload.zip"
    try:
        async with httpx.AsyncClient(timeout=300, follow_redirects=True) as client:
            async with client.stream("GET", url) as resp:
                if resp.status_code != 200:
                    return f"HTTP {resp.status_code} downloading {url} — use Update from file with a downloaded zip"
                size = 0
                with dest.open("wb") as fh:
                    async for chunk in resp.aiter_bytes():
                        size += len(chunk)
                        if size > MAX_ZIP_BYTES:
                            return "release zip is implausibly large — download it yourself and use Update from file"
                        fh.write(chunk)
    except (httpx.HTTPError, OSError) as exc:
        return f"could not download {url} ({exc}) — use Update from file with a downloaded zip"
    proc = await asyncio.to_thread(_run_update_sh, "stage", str(dest))
    if proc.returncode not in (0, 2, 3):
        return f"the downloaded zip did not stage: {_protocol_failures(proc.stdout)}"
    return None


class ApplyRequest(BaseModel):
    target: str = ""


def _spawn_detached(cmd: list[str]) -> None:
    devnull = subprocess.DEVNULL
    if sys.platform == "win32":
        flags = (
            subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.CREATE_NO_WINDOW
        )
        subprocess.Popen(cmd, stdin=devnull, stdout=devnull, stderr=devnull, creationflags=flags)
    else:
        subprocess.Popen(cmd, stdin=devnull, stdout=devnull, stderr=devnull, start_new_session=True)


@router.post("/update/apply")
async def update_apply(body: ApplyRequest) -> JSONResponse:
    if _bash() is None or not (SINGLE_DIR / "update-run.sh").is_file():
        return JSONResponse({"error": "deploy/single/update-run.sh is not available on this install"}, status_code=503)
    if _in_flight(_read_json(_update_dir() / "status.json")):
        return JSONResponse({"error": "an update is already running"}, status_code=409)
    current = read_product_version()
    if body.target and body.target == current:
        return JSONResponse({"error": f"v{current} is already installed"}, status_code=409)
    staged = _staged_target()
    if body.target and staged != body.target:
        # The online path: nothing (or something else) staged, but the check
        # named a target — fetch its source zip from the repo's own origin and
        # stage it, exactly as an uploaded file would be. Air-gapped installs
        # land in the except and are pointed at "Update from file".
        error = await _download_and_stage(body.target)
        if error:
            return JSONResponse({"error": error}, status_code=502)
        staged = _staged_target()
    if staged is None:
        return JSONResponse({"error": "nothing is staged — upload a zip first"}, status_code=409)
    if body.target and body.target != staged:
        return JSONResponse({"error": f"staged version is v{staged}, not v{body.target} — re-upload"}, status_code=409)

    upd = _update_dir()
    upd.mkdir(parents=True, exist_ok=True)
    # The merge rewrites update-run.sh mid-run and bash reads incrementally —
    # always run a copy, never the tracked file.
    runner = upd / "run.sh"
    shutil.copyfile(SINGLE_DIR / "update-run.sh", runner)

    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    tmp = upd / "status.json.tmp"
    tmp.write_text(json.dumps({
        "state": "running", "phase": "requested", "current": current,
        "target": staged, "started_at": now, "updated_at": now,
    }))
    tmp.replace(upd / "status.json")

    try:
        _spawn_detached([_bash() or "bash", str(runner), staged, str(SINGLE_DIR)])
    except OSError as exc:
        return JSONResponse({"error": f"could not start the update runner: {exc}"}, status_code=500)
    return JSONResponse({"triggered": True, "target": staged}, status_code=202)
