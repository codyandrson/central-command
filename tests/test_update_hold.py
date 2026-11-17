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


emitted: list[tuple] = []


@pytest.fixture(autouse=True)
def _reset(monkeypatch, tmp_path):
    hold.active, hold.target, hold.since, hold.kind = False, "", None, "update"
    api_hold._poller = None
    api_hold._triggered_at = api_hold._trigger_error = None
    monkeypatch.setenv("CC_UPDATE_TRIGGER", str(tmp_path / "trigger"))

    async def _emit(kind, ref_id=None, payload=None, actor=None):
        emitted.append((kind, payload, actor))

    emitted.clear()
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
    # An update's trigger is pinned key for key: no "action" ever appears in it.
    assert trigger == {"target": "2.30.0", "force": False, "requested_at": trigger["requested_at"]}
    assert (await api_hold.status())["triggered_at"] is not None
    assert [e[0] for e in emitted] == ["update.hold", "update.triggered"]
    assert emitted[0][1] == {"target": "2.30.0"} and emitted[1][1] == {"target": "2.30.0"}


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


# ── the restart kind (Settings > Connection) ────────────────────────────────


async def test_a_restart_hold_triggers_with_the_restart_action_when_nothing_runs(monkeypatch, tmp_path):
    running = [{"id": "sess_1", "agent_id": "ea", "mode": "oneshot", "stop_requested": True,
                "last_step_age_s": 12, "task_id": "task_1", "dispatch": False}]
    flagged = _repo(monkeypatch, running)

    out = await api_hold.engage("", "restart")
    assert out["active"] and out["kind"] == "restart" and hold.kind == "restart"
    await asyncio.sleep(0.05)
    assert flagged, "the same poller re-applies the stop flag"
    assert not (tmp_path / "trigger").exists(), "no trigger while something is RUNNING"

    running.clear()
    await asyncio.sleep(0.05)
    trigger = json.loads((tmp_path / "trigger").read_text())
    assert trigger == {"target": "", "force": False, "action": "restart",
                       "requested_at": trigger["requested_at"]}
    assert [e[0] for e in emitted] == ["restart.hold", "restart.triggered"]
    assert emitted[1][2] == "system"


async def test_restart_now_forces_the_trigger_and_names_what_it_kills(monkeypatch, tmp_path):
    _repo(monkeypatch, [{"id": "sess_9", "agent_id": "graph-curator", "mode": "oneshot",
                         "stop_requested": True, "last_step_age_s": 4000,
                         "task_id": None, "dispatch": False}])
    await api_hold.engage("", "restart")
    await api_hold.update_now("restart")
    trigger = json.loads((tmp_path / "trigger").read_text())
    assert trigger["force"] is True and trigger["action"] == "restart"
    kind, payload, actor = emitted[-1]
    assert kind == "restart.triggered" and actor == "operator"
    assert payload == {"force": True, "killed": ["sess_9"]}


async def test_cancelling_a_restart_hold_releases_it_and_resumes_what_it_parked(monkeypatch):
    _repo(monkeypatch, [])
    resumed: list[str] = []

    async def sessions_stopped_for_update():
        return ["sess_a"]

    monkeypatch.setattr(api_hold.repo, "sessions_stopped_for_update", sessions_stopped_for_update)
    from central_command.api import orchestration

    monkeypatch.setattr(orchestration, "_spawn_detached",
                        lambda unit, uid, driver: resumed.append(uid))
    await api_hold.engage("", "restart")
    out = await api_hold.release("restart")
    assert not out["active"] and out["kind"] is None and resumed == ["sess_a"]
    assert [e[0] for e in emitted] == ["restart.hold", "restart.hold_released"]


async def test_the_two_kinds_exclude_each_other_and_nothing_is_converted(monkeypatch, tmp_path):
    from fastapi import HTTPException

    _repo(monkeypatch, [{"id": "s", "agent_id": "ea", "mode": "oneshot", "stop_requested": True,
                         "last_step_age_s": 1, "task_id": "t", "dispatch": False}])
    await api_hold.engage("2.30.0")
    for call in (lambda: api_hold.engage("", "restart"),
                 lambda: api_hold.update_now("restart"),
                 lambda: api_hold.release("restart")):
        with pytest.raises(HTTPException) as e:
            await call()
        assert e.value.status_code == 409
    assert hold.active and hold.kind == "update" and hold.target == "2.30.0"
    assert not (tmp_path / "trigger").exists()
    await api_hold.release()

    await api_hold.engage("", "restart")
    for call in (lambda: api_hold.engage("2.30.0"), lambda: api_hold.update_now(),
                 lambda: api_hold.release()):
        with pytest.raises(HTTPException) as e:
            await call()
        assert e.value.status_code == 409
    assert hold.active and hold.kind == "restart"
    # Engaging the same kind again stays the idempotent no-op it always was.
    assert (await api_hold.engage("", "restart"))["kind"] == "restart"
    assert [e[0] for e in emitted].count("restart.hold") == 1


async def test_an_unknown_kind_is_refused(monkeypatch):
    from fastapi import HTTPException

    _repo(monkeypatch, [])
    with pytest.raises(HTTPException) as e:
        await api_hold.engage("", "reboot")
    assert e.value.status_code == 422 and not hold.active


async def test_a_restart_needs_the_updater_seam_where_the_update_hold_does_not(monkeypatch, tmp_path):
    """No `/run/cc-update`-style directory (the single-node profile has no root
    updater): a restart hold would pause the team for a trigger nothing reads."""
    from fastapi import HTTPException

    _repo(monkeypatch, [])
    monkeypatch.setenv("CC_UPDATE_TRIGGER", str(tmp_path / "absent" / "trigger"))
    with pytest.raises(HTTPException) as e:
        await api_hold.engage("", "restart")
    assert e.value.status_code == 501 and not hold.active


async def test_a_restart_hold_parks_under_the_reason_the_next_process_resumes():
    """The stored stop reason is shared, so `resume_held_sessions` at startup covers
    a restart exactly as it covers an update; refusals name what is waiting."""
    from central_command.runtime.converse import why_not_sendable

    assert hold.REASON == "update_hold"
    hold.active, hold.kind = True, "restart"
    code, text = why_not_sendable({"id": "sess_c", "mode": "conversation", "status": "DONE"})
    assert code == "update_hold" and text.startswith("a restart is waiting")
    hold.kind = "update"
    assert why_not_sendable({"id": "sess_c", "mode": "conversation", "status": "DONE"})[1].startswith(
        "an update is waiting")
