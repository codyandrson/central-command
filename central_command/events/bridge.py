"""WARNING+ log records become durable events (2026-08-15).

The internal error surface had a hole: governance failures emit events and
reach the cockpit, but plain `logger.error`/`logger.exception` calls (~10 call
sites: dispatch loop errors, resume-sweep failures, heartbeat action errors)
went only to journald — invisible in Activity → Events. This handler bridges
them: `log.error` / `log.warning` events, next to the failures already there.

Two properties are load-bearing:

- **A broken bridge must never break the caller.** Logging happens inside
  exception handlers; raising from here would mask the original error. Every
  path swallows its own failures — if the DB is down the record still lands in
  journald (and so in VictoriaLogs), which is the layer that doesn't depend on
  the app's own stores.
- **The bridge must not feed itself.** Records from `central_command.events.*` are
  skipped (the fan-out's own "queue full" warning would otherwise emit an
  event about emitting events), and a contextvar guards the emit task so
  anything logged *while* bridging — e.g. by the DB layer during a failed
  append — is dropped rather than recursed on.

This is a materiality call consistent with the heartbeat rule: errors are
always material, so bridging WARNING+ adds rows only when something is wrong.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import traceback

from central_command.events import log as event_log

_SKIP_PREFIXES = ("central_command.events",)
_TRACEBACK_LIMIT = 4000

_bridging: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "cc_log_bridge", default=False
)
_handler: _EventLogHandler | None = None
_tasks: set[asyncio.Task] = set()


class _EventLogHandler(logging.Handler):
    """Sync logging handler → async emit, via the lifespan's loop.

    `logging` is synchronous and may fire from any thread; `emit()` is async
    and loop-bound. `call_soon_threadsafe` is the one safe hand-off between
    the two — it also makes a record logged after the loop closed a swallowed
    RuntimeError rather than a crash.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        super().__init__(level=logging.WARNING)
        self._loop = loop

    def emit(self, record: logging.LogRecord) -> None:  # noqa: A003 — logging API
        if _bridging.get() or record.name.startswith(_SKIP_PREFIXES):
            return
        try:
            payload = {
                "logger": record.name,
                "level": record.levelname,
                "message": record.getMessage(),
            }
            if record.exc_info and record.exc_info != (None, None, None):
                tb = "".join(traceback.format_exception(*record.exc_info))
                payload["traceback"] = tb[-_TRACEBACK_LIMIT:]
            kind = "log.error" if record.levelno >= logging.ERROR else "log.warning"
            self._loop.call_soon_threadsafe(self._spawn, kind, payload)
        except Exception:  # noqa: BLE001, S110 — see module docstring
            pass

    def _spawn(self, kind: str, payload: dict) -> None:
        task = self._loop.create_task(_emit(kind, payload))
        _tasks.add(task)  # create_task holds only a weak ref; anchor until done
        task.add_done_callback(_tasks.discard)


async def _emit(kind: str, payload: dict) -> None:
    _bridging.set(True)  # task-local: guards anything logged during the emit
    try:
        await event_log.emit(kind, payload=payload, actor="system")
    except Exception:  # noqa: BLE001, S110 — journald still has the record
        pass


def install() -> None:
    """Attach the bridge to the running loop. Idempotent; call from lifespan."""
    global _handler
    if _handler is not None:
        return
    _handler = _EventLogHandler(asyncio.get_running_loop())
    logging.getLogger().addHandler(_handler)
    # uvicorn's default log config sets propagate=False on its loggers, so
    # records logged via "uvicorn.error" (nerve_gateway does) never reach the
    # root handler — attach there too.
    logging.getLogger("uvicorn").addHandler(_handler)


def uninstall() -> None:
    global _handler
    if _handler is None:
        return
    logging.getLogger().removeHandler(_handler)
    logging.getLogger("uvicorn").removeHandler(_handler)
    _handler = None


async def drain() -> None:
    """Await in-flight emits — for tests and orderly shutdown, not correctness."""
    await asyncio.gather(*list(_tasks), return_exceptions=True)
