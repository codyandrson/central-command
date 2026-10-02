"""`/api/selfcheck` — the cached result, the single-flight run, and the startup
run that must never fire by surprise.

`selfcheck.run` is replaced in every test here: these tests are about WHEN a
run happens and what the endpoints answer, never about the checks themselves
(tests/test_selfcheck.py). No test reaches a network or a live service.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import httpx
import pytest

from central_command import selfcheck
from central_command.api import selfcheck as selfcheck_api
from central_command.config import settings

ROOT = Path(__file__).resolve().parents[1]

RESULT = {"status": "pass", "running": False, "ran_at": "2026-10-02T15:04:05Z",
          "duration_ms": 12, "version": "x", "mode": "api",
          "checks": [{"name": "spine", "status": "pass", "message": "ok", "remedy": None,
                      "system": "postgres", "duration_ms": 3}]}


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch):
    monkeypatch.setattr(selfcheck_api, "_last", None)
    monkeypatch.setattr(selfcheck_api, "_task", None)


@pytest.fixture
def no_checks(monkeypatch):
    """Every check function raises if called — reading must spend nothing."""
    called = []
    for check in selfcheck.CHECKS:
        attr = f"check_{check.name.replace('-', '_')}"

        async def boom(ctx, _name=check.name):
            called.append(_name)
            raise AssertionError(f"check {_name} ran")

        monkeypatch.setattr(selfcheck, attr, boom)
    return called


@pytest.fixture
def gated_run(monkeypatch):
    """`selfcheck.run` replaced by one that waits for the test to release it."""
    state = {"calls": 0, "release": asyncio.Event()}

    async def fake_run(*, mode="api", pre_boot=False, completion_timeout=None):
        state["calls"] += 1
        await state["release"].wait()
        return dict(RESULT)

    monkeypatch.setattr(selfcheck, "run", fake_run)
    return state


async def test_never_run_before_the_first_run(no_checks):
    doc = await selfcheck_api.get_selfcheck()
    assert doc["status"] == "never_run"
    assert doc["checks"] == [] and doc["running"] is False
    assert doc["ran_at"] is None and doc["mode"] == "api"
    assert no_checks == []


async def test_get_spends_nothing(no_checks, gated_run):
    for _ in range(3):
        await selfcheck_api.get_selfcheck()
    assert gated_run["calls"] == 0
    assert no_checks == []


async def test_post_starts_exactly_one_run(gated_run):
    first = await selfcheck_api.run_selfcheck()
    second = await selfcheck_api.run_selfcheck()
    assert first.status_code == 202 and second.status_code == 202
    await asyncio.sleep(0)
    assert gated_run["calls"] == 1
    # While running: the previous result (none yet) plus running: true.
    doc = await selfcheck_api.get_selfcheck()
    assert doc["running"] is True and doc["status"] == "never_run"

    gated_run["release"].set()
    await selfcheck_api._task
    doc = await selfcheck_api.get_selfcheck()
    assert doc["running"] is False and doc["status"] == "pass"
    assert doc["checks"] == RESULT["checks"]

    # The next request starts a NEW run, and the old result stays readable
    # alongside running: true until it lands.
    gated_run["release"] = asyncio.Event()
    await selfcheck_api.run_selfcheck()
    await asyncio.sleep(0)
    doc = await selfcheck_api.get_selfcheck()
    assert gated_run["calls"] == 2
    assert doc["running"] is True and doc["status"] == "pass"
    gated_run["release"].set()
    await selfcheck_api._task


async def test_the_routes_over_http(gated_run):
    from central_command.api.app import app

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://cc") as client:
        got = await client.get("/api/selfcheck")
        assert got.status_code == 200 and got.json()["status"] == "never_run"
        posted = await client.post("/api/selfcheck/run")
        assert posted.status_code == 202 and posted.json()["running"] is True
    gated_run["release"].set()
    await selfcheck_api._task
    assert gated_run["calls"] == 1


async def test_a_runner_bug_leaves_the_last_result_and_no_dangling_task(monkeypatch):
    async def broken(**_k):
        raise RuntimeError("runner bug")

    monkeypatch.setattr(selfcheck, "run", broken)
    selfcheck_api.start_run()
    await selfcheck_api._task
    doc = selfcheck_api.document()
    assert doc["status"] == "never_run" and doc["running"] is False


# --- the startup run --------------------------------------------------------------


async def test_the_startup_run_never_fires_under_the_test_suite(monkeypatch, gated_run):
    monkeypatch.setattr(settings, "selfcheck_on_start", True)
    monkeypatch.setattr(settings, "demo_mode", False)
    assert selfcheck_api.start_on_boot() is False
    assert selfcheck_api._task is None and gated_run["calls"] == 0


@pytest.mark.parametrize("on_start,demo,expected", [
    (True, False, True), (False, False, False), (True, True, False),
])
async def test_the_startup_run_is_gated_by_its_switch_and_demo_mode(
        monkeypatch, gated_run, on_start, demo, expected):
    # Pretend we are NOT under the suite, to see the other two gates work.
    monkeypatch.setattr(selfcheck_api, "_under_test_suite", lambda: False)
    monkeypatch.setattr(settings, "selfcheck_on_start", on_start)
    monkeypatch.setattr(settings, "demo_mode", demo)
    assert selfcheck_api.start_on_boot() is expected
    await asyncio.sleep(0)
    assert gated_run["calls"] == (1 if expected else 0)
    if expected:
        gated_run["release"].set()
        await selfcheck_api._task


def test_the_switch_defaults_on_and_is_documented():
    from central_command.config import Settings

    assert Settings.model_fields["selfcheck_on_start"].default is True
    assert Settings.model_fields["selfcheck_completion_timeout"].default >= 60
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "CC_SELFCHECK_ON_START=" in example
    assert "CC_SELFCHECK_COMPLETION_TIMEOUT=" in example


def test_the_lifespan_starts_the_selfcheck_only_behind_its_switch():
    tree = ast.parse((ROOT / "central_command/api/app.py").read_text(encoding="utf-8"))
    lifespan = next(n for n in ast.walk(tree)
                    if isinstance(n, ast.AsyncFunctionDef) and n.name == "lifespan")
    guarded = []
    for node in ast.walk(lifespan):
        if isinstance(node, ast.If) and "settings.selfcheck_on_start" in ast.unparse(node.test):
            guarded += [c for c in ast.walk(node) if isinstance(c, ast.Call)
                        and isinstance(c.func, ast.Attribute) and c.func.attr == "start_on_boot"]
    every = [c for c in ast.walk(lifespan) if isinstance(c, ast.Call)
             and isinstance(c.func, ast.Attribute)
             and c.func.attr in ("start_on_boot", "start_run")]
    assert guarded and len(every) == len(guarded), (
        "the lifespan must start the self-check only through start_on_boot, "
        "under `if settings.selfcheck_on_start`")
