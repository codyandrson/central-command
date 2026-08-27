"""The heartbeat engine — the tick loop that fires due schedules.

Reuses the two patterns the codebase already trusts:
- **Postgres advisory-lock lease** (the dispatcher's zombie-process lesson):
  one heartbeat per DATABASE, enforced by the database, released on death.
- **Opt-in autostart** (`CC_HEARTBEAT_ENABLED`, default false): the loop never
  starts by surprise.

**Missed fires are skipped, never replayed.** A schedule's next due time is
computed from *now* whenever the engine first sees it (start, enable, or
edit) — a homelab box that was off overnight must not fire a burst of stale
sweeps at boot. A LOUD action emits `heartbeat.fired` BEFORE it runs (the
event-before-the-thing rule), then `heartbeat.completed` or `heartbeat.error`;
run history is the event log, not a second table.

**Quiet fires (2026-07-25).** A 5-minute mail poll that enrolls nothing wrote
two rows every time — ~576/day of "nothing happened", drowning the audit trail
the governance screens read. M12 had already settled the principle for the feed
itself (`feed.polled` only when new mail arrived); the heartbeat had
re-introduced the noise one layer up. Actions that are **non-authorising and
self-recording** may now declare an `ActionSpec.material` predicate: an
immaterial firing writes NOTHING to the log. Errors always write, and the
emit-before-the-action rule is untouched for every other action — see the
ActionSpec docstring for the two conditions and `tests/test_heartbeat.py` for
the parity test that pins which actions qualify.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from croniter import croniter

from central_command import events
from central_command.config import settings
from central_command.contract import is_transient
from central_command.db import repo
from central_command.heartbeat import actions

log = logging.getLogger(__name__)

# 0x67760001 is the dispatcher's lease (central_command/ingest/dispatcher.py).
HEARTBEAT_LEASE_KEY = 0x67760002

# Fired this much past due is still recorded (skip-not-replay only drops
# occurrences the engine never saw; lateness on ones it fires is annotated).
LATE_GRACE_SECONDS = 300


def validate_schedule(schedule_kind: str, schedule: dict) -> None:
    """Raise ValueError for a malformed time rule — create/patch-time guard."""
    if schedule_kind == "cron":
        expr = str(schedule.get("expr") or "")
        if not croniter.is_valid(expr):
            raise ValueError(f"invalid cron expression: {expr!r}")
        if schedule.get("tz"):
            ZoneInfo(str(schedule["tz"]))  # raises on unknown zone
    elif schedule_kind == "every":
        seconds = float(schedule.get("every_seconds") or 0)
        if seconds < 10:
            raise ValueError("every_seconds must be at least 10")
    elif schedule_kind == "at":
        at = datetime.fromisoformat(str(schedule.get("at") or ""))
        del at
    else:
        raise ValueError(f"unknown schedule kind {schedule_kind!r} (cron|every|at)")


def compute_next_due(
    schedule_kind: str, schedule: dict, after: datetime
) -> datetime | None:
    """The next occurrence strictly after `after` (UTC). None = never again —
    which is how a one-shot whose moment has passed gets skipped, not replayed."""
    if schedule_kind == "cron":
        tz = ZoneInfo(str(schedule.get("tz") or "UTC"))
        nxt = croniter(str(schedule["expr"]), after.astimezone(tz)).get_next(datetime)
        return nxt.astimezone(timezone.utc)
    if schedule_kind == "every":
        return after + timedelta(seconds=float(schedule["every_seconds"]))
    if schedule_kind == "at":
        at = datetime.fromisoformat(str(schedule["at"]))
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        return at if at > after else None
    raise ValueError(f"unknown schedule kind {schedule_kind!r}")


async def fire(schedule: dict, trigger: str = "schedule") -> dict:
    """Fire one schedule's action now — the ONE firing path (the tick loop and
    the operator's run-now button both land here, so the log can't tell them
    apart except by the recorded trigger)."""
    spec = actions.ACTIONS.get(schedule["action_kind"])
    if spec is None:
        raise ValueError(f"unknown action kind {schedule['action_kind']!r}")
    params = schedule.get("action_params") or {}
    quiet_eligible = spec.material is not None

    # LOUD actions (the default) emit BEFORE the action — the event that
    # authorises precedes the thing. QUIET-eligible actions authorise nothing
    # and record their own effects, so their heartbeat row is provenance and
    # is written afterwards, only if the firing actually did something.
    if not quiet_eligible:
        await events.emit(
            "heartbeat.fired",
            ref_id=schedule["id"],
            payload={"action": schedule["action_kind"], "trigger": trigger},
            actor="heartbeat",
        )
    # Stamped on EVERY firing, quiet or loud: cadence liveness is current
    # state (the crons tab's "last run"), not append-only history. It also
    # stops a failing action refiring every tick forever.
    await repo.touch_heartbeat_fired(schedule["id"])
    try:
        result = await spec.run(schedule["id"], params)
    except Exception as e:  # noqa: BLE001 — one bad action must not kill the loop
        log.exception("heartbeat: %s (%s) failed", schedule["id"], schedule["action_kind"])
        # Failure is ALWAYS material, for every action.
        await events.emit(
            "heartbeat.error",
            ref_id=schedule["id"],
            payload={"action": schedule["action_kind"], "trigger": trigger,
                     "error": str(e)[:300], "transient": is_transient(e)},
            actor="heartbeat",
        )
        return {"ok": False, "error": str(e)}
    if quiet_eligible and not spec.material(result):
        # Nothing happened: the log stays silent. `last_fired_at` above is the
        # record that the engine ran on cadence.
        return {"ok": True, "result": result, "quiet": True}
    await events.emit(
        "heartbeat.completed",
        ref_id=schedule["id"],
        payload={"action": schedule["action_kind"], "trigger": trigger,
                 "result": result},
        actor="heartbeat",
    )
    return {"ok": True, "result": result}


class HeartbeatEngine:
    """The background tick loop. One per DATABASE (advisory-lock lease)."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stopping = asyncio.Event()
        self._lease_conn = None
        # Per-schedule next-due cache, (re)computed from NOW whenever a
        # schedule is first seen or its updated_at changes — the skip-missed
        # policy lives here, deliberately in memory: nothing durable claims an
        # occurrence that was never fired.
        self._next_due: dict[str, datetime | None] = {}
        self._seen_updated: dict[str, datetime] = {}
        self.last_reason = "not started"

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def _acquire_lease(self) -> bool:
        if self._lease_conn is not None:
            return True
        conn = await repo._conn()
        try:
            got = await conn.fetchval(
                "select pg_try_advisory_lock($1)", HEARTBEAT_LEASE_KEY
            )
        except Exception:
            await conn.close()
            raise
        if not got:
            await conn.close()
            return False
        self._lease_conn = conn
        return True

    async def _release_lease(self) -> None:
        if self._lease_conn is None:
            return
        try:
            await self._lease_conn.execute(
                "select pg_advisory_unlock($1)", HEARTBEAT_LEASE_KEY
            )
        finally:
            await self._lease_conn.close()
            self._lease_conn = None

    async def start(self) -> None:
        if self.running:
            return
        if not await self._acquire_lease():
            self.last_reason = "refused: another heartbeat holds the lease"
            log.warning("heartbeat: %s", self.last_reason)
            await events.emit(
                "heartbeat.lease_refused",
                payload={"lease_key": HEARTBEAT_LEASE_KEY},
                actor="heartbeat",
            )
            return
        self._stopping.clear()
        self._next_due.clear()
        self._seen_updated.clear()
        self._task = asyncio.create_task(self._loop())
        self.last_reason = "running"
        await events.emit(
            "heartbeat.started",
            payload={"tick_seconds": settings.heartbeat_tick_seconds},
            actor="heartbeat",
        )

    async def stop(self) -> None:
        was_running = self.running
        self._stopping.set()
        if self._task is not None:
            await asyncio.wait([self._task], timeout=30)
            self._task = None
        await self._release_lease()
        if was_running:
            self.last_reason = "stopped"
            await events.emit("heartbeat.stopped", actor="heartbeat")

    async def _loop(self) -> None:
        log.info("heartbeat: tick loop started (every %.0fs)",
                 settings.heartbeat_tick_seconds)
        while not self._stopping.is_set():
            try:
                await self._tick()
            except Exception:  # noqa: BLE001 — the loop outlives any single error
                log.exception("heartbeat: tick failed")
                self.last_reason = "error (see logs)"
            try:
                await asyncio.wait_for(
                    self._stopping.wait(), timeout=settings.heartbeat_tick_seconds
                )
            except asyncio.TimeoutError:
                pass
        log.info("heartbeat: tick loop stopped")

    async def _tick(self) -> None:
        now = datetime.now(timezone.utc)
        schedules = await repo.list_heartbeat_schedules(enabled_only=True)
        live = set()
        for s in schedules:
            sid = s["id"]
            live.add(sid)
            if self._seen_updated.get(sid) != s["updated_at"]:
                # New, just-enabled, or edited: due time computed from NOW —
                # occurrences missed while off/disabled are skipped.
                self._seen_updated[sid] = s["updated_at"]
                due = compute_next_due(s["schedule_kind"], s["schedule"], now)
                self._next_due[sid] = due
                if due is None:  # a one-shot whose moment already passed
                    await self._retire_one_shot(sid, "one-shot time already passed")
                continue

            due = self._next_due.get(sid)
            if due is None or due > now:
                continue
            late = (now - due).total_seconds()
            if late > LATE_GRACE_SECONDS:
                await events.emit(
                    "heartbeat.skipped",
                    ref_id=sid,
                    payload={"late_seconds": int(late),
                             "note": "firing once now; missed occurrences are not replayed"},
                    actor="heartbeat",
                )
            await fire(s)
            nxt = compute_next_due(
                s["schedule_kind"], s["schedule"], datetime.now(timezone.utc)
            )
            self._next_due[sid] = nxt
            if nxt is None:
                await self._retire_one_shot(sid, "one-shot fired")

        # Forget disabled/deleted schedules so a re-enable recomputes from now.
        for sid in list(self._next_due):
            if sid not in live:
                self._next_due.pop(sid, None)
                self._seen_updated.pop(sid, None)

    async def _retire_one_shot(self, schedule_id: str, reason: str) -> None:
        await repo.set_heartbeat_enabled(schedule_id, False)
        await events.emit(
            "heartbeat.schedule.toggled",
            ref_id=schedule_id,
            payload={"enabled": False, "reason": reason},
            actor="heartbeat",
        )

    async def run_now(self, schedule_id: str) -> dict:
        """The operator's test button — fires the identical path, engine
        running or not, enabled or not. The cadence cache is untouched."""
        schedule = await repo.get_heartbeat_schedule(schedule_id)
        if schedule is None:
            raise KeyError(schedule_id)
        return await fire(schedule, trigger="run-now")

    def status(self) -> dict:
        return {
            "running": self.running,
            "reason": self.last_reason,
            "tick_seconds": settings.heartbeat_tick_seconds,
            "next_due": {
                sid: due.isoformat() if due else None
                for sid, due in sorted(self._next_due.items())
            },
        }


engine = HeartbeatEngine()
