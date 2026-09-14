"""The update hold (api/hold.py): pause the team, wait for the agents, trigger.

Offline: the repo seams and the three loops are monkeypatched; the trigger is
written to a tmp path via CC_UPDATE_TRIGGER. Nothing here needs Postgres.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from central_command.api import hold as api_hold
from central_command.runtime import hold


@pytest.fixture(autouse=True)
def _reset(monkeypatch, tmp_path):
    hold.active, hold.target, hold.since = False, "", None
    api_hold._poller = None
    api_hold._triggered_at = api_hold._trigger_error = None
    monkeypatch.setenv("CC_UPDATE_TRIGGER", str(tmp_path / "trigger"))

    async def _emit(*a, **k):
        return None

    monkeypatch.setattr(api_hold.events, "emit", _emit)
    monkeypatch.setattr(api_hold, "_stop_loops", _noop)
    monkeypatch.setattr(api_hold, "_restart_loops", _noop)
    monkeypatch.setattr(api_hold, "_TICK_S", 0.01)
    yield
    hold.active = False
    if api_hold._poller is not None:
        api_hold._poller.cancel()
        api_hold._poller = None


async def _noop(*a, **k):
    return None


def _repo(monkeypatch, running: list[dict]):
    flagged: list[int] = []

    async def request_stop_for_update():
        flagged.append(1)
        return 0

    async def running_sessions_for_hold():
        return list(running)

    async def sessions_stopped_for_update():
        return []

    monkeypatch.setattr(api_hold.repo, "request_stop_for_update", request_stop_for_update)
    monkeypatch.setattr(api_hold.repo, "running_sessions_for_hold", running_sessions_for_hold)
    monkeypatch.setattr(api_hold.repo, "sessions_stopped_for_update", sessions_stopped_for_update)
    return flagged


async def test_engage_waits_while_runs_are_live_then_triggers_unforced(monkeypatch, tmp_path):
    running = [{"id": "sess_1", "agent_id": "ea", "mode": "oneshot", "stop_requested": True,
                "last_step_age_s": 12, "task_id": "task_1", "dispatch": False}]
    flagged = _repo(monkeypatch, running)

    out = await api_hold.engage("2.30.0")
    assert out["active"] and out["running"] == running and out["triggered_at"] is None
    await asyncio.sleep(0.05)
    assert flagged, "the poller re-applies the stop flag every tick"
    assert not (tmp_path / "trigger").exists(), "no trigger while something is RUNNING"

    running.clear()
    await asyncio.sleep(0.05)
    trigger = json.loads((tmp_path / "trigger").read_text())
    assert trigger == {"target": "2.30.0", "force": False, "requested_at": trigger["requested_at"]}
    assert (await api_hold.status())["triggered_at"] is not None


async def test_update_now_forces_the_trigger_and_names_what_it_kills(monkeypatch, tmp_path):
    _repo(monkeypatch, [{"id": "sess_9", "agent_id": "graph-curator", "mode": "oneshot",
                         "stop_requested": True, "last_step_age_s": 4000,
                         "task_id": None, "dispatch": False}])
    await api_hold.engage("2.30.0")
    await api_hold.update_now()
    assert json.loads((tmp_path / "trigger").read_text())["force"] is True


async def test_release_clears_the_flag_and_resumes_what_the_hold_parked(monkeypatch):
    _repo(monkeypatch, [])
    resumed: list[str] = []

    async def sessions_stopped_for_update():
        return ["sess_a", "sess_b"]

    monkeypatch.setattr(api_hold.repo, "sessions_stopped_for_update", sessions_stopped_for_update)

    from central_command.api import orchestration

    monkeypatch.setattr(orchestration, "_spawn_detached",
                        lambda unit, uid, driver: resumed.append(uid))
    await api_hold.engage("2.30.0")
    assert hold.active
    out = await api_hold.release()
    assert not out["active"] and not hold.active
    assert resumed == ["sess_a", "sess_b"]


async def test_a_fresh_task_run_is_refused_while_held_and_the_task_stays_assigned(monkeypatch):
    """The gate sits BEFORE `task.started`/`start_task`: nothing is emitted and
    nothing flips, so the next process's startup sweep relaunches the task."""
    from central_command.api import routes

    touched: list[str] = []

    async def start_task(*a, **k):
        touched.append("start_task")

    async def emit(*a, **k):
        touched.append("emit")

    monkeypatch.setattr(routes.repo, "start_task", start_task)
    monkeypatch.setattr(routes.events, "emit", emit)
    hold.active = True
    task = {"id": "task_h", "agent_id": "ea", "title": "t", "instructions": "i"}
    with pytest.raises(hold.RunHeld):
        await routes._run_one_assigned_task(task)
    assert touched == []


def test_the_hold_disables_the_cockpit_composer():
    from central_command.api.nerve_gateway import _COMPOSER_DISABLING
    from central_command.runtime.converse import why_not_sendable

    hold.active = True
    code, _ = why_not_sendable({"id": "sess_c", "mode": "conversation", "status": "DONE"})
    assert code == "update_hold"
    assert "update_hold" in _COMPOSER_DISABLING
    hold.active = False
    assert why_not_sendable({"id": "sess_c", "mode": "conversation", "status": "DONE"}) is None
