"""The update hold: pause the team, wait for the agents to land, then trigger.

Shape (operator decision, 2026-09-13, after six deploys in a day each killed
the runs in flight):

  engage  → the three background loops stop (nothing NEW is scheduled), every
            task-linked or conversation run is asked to stop at its next node
            boundary (it parks STOPPED with `reason = update_hold`), fresh task
            runs are refused (`routes._run_one_assigned_task`) and the chat
            composer is disabled (`converse.why_not_sendable`). A poller
            re-applies the stop flag every tick — a turn that starts during
            the hold (an approval's resume) parks too — and writes the
            updater's trigger the moment nothing is RUNNING.
  now     → write the trigger with `force` regardless; whatever is still
            RUNNING dies with the process and is swept FAILED at startup, and
            the wait screen said so.
  release → cancel: loops restart per settings, and the sessions the hold
            parked are resumed — the operator never pressed stop on them.

The wait is unbounded by design and the poller keeps re-writing the trigger
(never more than once per `_RETRIGGER_S`) while the hold stands, because
the updater's own busy gate may refuse a trigger whose window a late resume
slipped into. What ends the hold is the updater stopping this process, or
the operator: the flag is in memory (`runtime/hold.py`) and the next process
starts unheld, resuming what this one parked (`resume_held_sessions`, called
from the lifespan).

Root never waits: the updater is a systemd oneshot with a start timeout, and
the cockpit reads a run as dead after ~95 minutes of silence. All the waiting
happens here, in the process that owns the loops and the run gate.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from central_command import events
from central_command.config import settings
from central_command.db import repo
from central_command.runtime import hold

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/update/hold")

_TICK_S = 5
_RETRIGGER_S = 300
_poller: asyncio.Task | None = None
_triggered_at: datetime | None = None
_trigger_error: str | None = None


def _trigger_path() -> str:
    # Same path (and env override) as the cockpit server's writer; the dir is
    # tmpfs and codyslab-writable (deploy/k3s/cc-update-tmpfiles.conf).
    return os.environ.get("CC_UPDATE_TRIGGER", "/run/cc-update/trigger")


def _write_trigger(force: bool) -> None:
    global _triggered_at, _trigger_error
    path = _trigger_path()
    tmp = f"{path}.tmp"
    try:
        with open(tmp, "w") as f:
            json.dump({"target": hold.target, "force": force,
                       "requested_at": datetime.now(timezone.utc).isoformat()}, f)
        os.replace(tmp, path)  # atomic: the path unit never sees a half-written file
        _triggered_at, _trigger_error = datetime.now(timezone.utc), None
    except OSError as e:
        _trigger_error = f"could not write {path}: {e}"
        log.warning("update hold: %s", _trigger_error)


def _loops():
    from central_command.heartbeat import engine as heartbeat_engine
    from central_command.ingest.dispatcher import dispatcher
    from central_command.ingest.feed import feed, sweeper

    return heartbeat_engine, feed, sweeper, dispatcher


async def _stop_loops() -> None:
    for loop in _loops():
        try:
            await loop.stop()
        except Exception:  # noqa: BLE001 — one loop must not keep the others running
            log.exception("update hold: stopping %s failed", type(loop).__name__)


async def _restart_loops() -> None:
    heartbeat_engine, feed, _sweeper, dispatcher = _loops()
    if settings.dispatch_enabled:
        await dispatcher.start()
    if settings.feed_enabled:
        await feed.start()
    if settings.heartbeat_enabled:
        await heartbeat_engine.start()


async def _poll() -> None:
    # Loop stops can wait 30s each on in-flight work — never inline in the
    # request that engaged the hold.
    await _stop_loops()
    while hold.active:
        try:
            await repo.request_stop_for_update()
            running = await repo.running_sessions_for_hold()
            if not running and not os.path.exists(_trigger_path()) and (
                _triggered_at is None
                or (datetime.now(timezone.utc) - _triggered_at).total_seconds() > _RETRIGGER_S
            ):
                _write_trigger(force=False)
                await events.emit("update.triggered", payload={"target": hold.target},
                                  actor="system")
        except Exception:  # noqa: BLE001 — the hold outlives one bad tick
            log.exception("update hold: tick failed")
        await asyncio.sleep(_TICK_S)


async def status() -> dict:
    running = await repo.running_sessions_for_hold() if hold.active else []
    return {
        "active": hold.active,
        "target": hold.target,
        "since": hold.since.isoformat() if hold.since else None,
        "running": running,
        "triggered_at": _triggered_at.isoformat() if _triggered_at else None,
        "trigger_error": _trigger_error,
    }


async def engage(target: str) -> dict:
    global _poller, _triggered_at, _trigger_error
    if not hold.active:
        hold.active, hold.target, hold.since = True, target, datetime.now(timezone.utc)
        _triggered_at = _trigger_error = None
        await events.emit("update.hold", payload={"target": target}, actor="operator")
        _poller = asyncio.create_task(_poll())
    return await status()


async def release() -> dict:
    global _poller
    if not hold.active:
        return await status()
    hold.active = False
    if _poller is not None:
        _poller.cancel()
        _poller = None
    await events.emit("update.hold_released", payload={"target": hold.target},
                      actor="operator")
    await _restart_loops()
    await resume_held_sessions()
    return await status()


async def update_now() -> dict:
    if not hold.active:
        raise HTTPException(409, "no update hold is engaged")
    _write_trigger(force=True)
    if _trigger_error:
        raise HTTPException(503, _trigger_error)
    running = await repo.running_sessions_for_hold()
    await events.emit("update.triggered",
                      payload={"target": hold.target, "force": True,
                               "killed": [r["id"] for r in running]},
                      actor="operator")
    return await status()


async def resume_held_sessions() -> list[str]:
    """Resume every session the hold parked. Startup (the process the updater
    started) and cancel both come through here. Detached per session: a resume
    is a full model turn."""
    from central_command.api import orchestration

    ids = await repo.sessions_stopped_for_update()
    for sid in ids:
        orchestration._spawn_detached(
            "session", sid, lambda sid=sid: orchestration.resume_stopped_session(sid))
    if ids:
        log.info("update hold: resuming %d session(s) parked for the update", len(ids))
    return ids


class HoldIn(BaseModel):
    target: str = ""


@router.get("")
async def get_hold() -> dict:
    return await status()


@router.post("")
async def post_hold(body: HoldIn) -> dict:
    return await engage(body.target)


@router.post("/now")
async def post_now() -> dict:
    return await update_now()


@router.delete("")
async def delete_hold() -> dict:
    return await release()
