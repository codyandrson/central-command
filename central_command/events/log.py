"""The event log (M8) — one durable append-only log, four jobs.

DESIGN.md asks a single log to serve **proactivity** (what needs attention),
**collaboration** (what the agents and the operator did), **audit** (what
happened, in order, forever), and **observability** (why the system is doing
what it's doing). Before M8 there were two half-logs: an `audit_event` table
nothing wrote to, and an in-process pub/sub whose events died with the process.

`emit()` writes to Postgres *first*, then fans out to live subscribers. That
order matters: the database is the source of truth, and the SSE stream is a
projection of it — never the other way round. So a browser that reconnects can
replay from `Last-Event-ID` and miss nothing.

**Durability is strict, not best-effort.** If the append fails, `emit()` raises
and the caller's operation fails with it. An approval that silently lost its
audit record is worse than an approval that visibly failed — and since the whole
control plane is Postgres-backed, a DB that can't take an event can't serve the
request anyway. Callers on a hot path (the dispatcher loop) already catch
broadly, so one failed emit can't wedge the drain.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator

from central_command.db import repo

log = logging.getLogger(__name__)

# Live fan-out to connected SSE clients. Bounded so a stalled browser applies
# backpressure to itself, never to the control plane.
_QUEUE_MAX = 1000
_subscribers: set[asyncio.Queue] = set()


async def emit(
    kind: str,
    ref_id: str | None = None,
    payload: dict | None = None,
    actor: str | None = None,
) -> dict:
    """Append an event durably, then publish it live. Returns the stored event.

    `kind` is a dotted namespace (`work.claimed`, `proposal.decided`) so the log
    can be filtered by prefix. `ref_id` is the subject the event is about.
    """
    event = await repo.append_event(kind, ref_id, payload or {}, actor)
    event["created_at"] = event["created_at"].isoformat()

    for q in list(_subscribers):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            # The record is already durable; this client just falls behind and
            # will catch up on reconnect via Last-Event-ID.
            log.warning("event fan-out: subscriber queue full, dropping live copy")
    return event


@contextlib.asynccontextmanager
async def subscribe() -> AsyncIterator[asyncio.Queue]:
    """A live feed of emitted events.

    **`None` is the end-of-stream sentinel**: it arrives when the log closes
    (shutdown) and means stop following. A consumer that loops on `q.get()`
    without checking for it never returns, which is the hang described below.
    """
    q: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAX)
    _subscribers.add(q)
    if _closing:                 # subscribed during shutdown: release at once
        _release(q)
    try:
        yield q
    finally:
        _subscribers.discard(q)


# --- shutdown ------------------------------------------------------------------
#
# A live subscriber follows the log forever by construction, so without a way to
# say "stop following" uvicorn's graceful shutdown waits on every open SSE
# response and cockpit WebSocket until systemd's TimeoutStopSec expires and
# SIGKILLs the process. That is not a hang under load — it is EVERY restart,
# including a routine deploy, and it is why `TimeoutStopSec` was capped at 15s
# (2026-07-21/22) rather than sized to real work. A timeout that fires on a
# healthy system is a misconfigured timeout; this is what lets it be sized to
# fire only when a shutdown is genuinely wedged.

# A plain bool and a queue sentinel, NOT an `asyncio.Event`. `Event` carries
# `_LoopBoundMixin`: it binds to the first running loop that awaits it and
# raises RuntimeError from every other one. That is invisible in production (one
# process, one loop) and wrong everywhere else — under pytest's per-test loops
# the first shape of this code had the wait() task finish with that RuntimeError
# and got read as "closing", i.e. an exception silently swallowed and served as
# end-of-stream. A sentinel needs no loop affinity and cannot mistake a failure
# for a shutdown.
_closing = False


def opened() -> None:
    """Reopen the log for a fresh process/app lifespan (clears a prior close)."""
    global _closing
    _closing = False


def close() -> None:
    """Release every live follower. Idempotent; safe to call with none open."""
    global _closing
    _closing = True
    for q in list(_subscribers):
        _release(q)


def _release(q: asyncio.Queue) -> None:
    """Deliver the end-of-stream sentinel, making room if the queue is full.

    The fan-out DROPS into a full queue (`put_nowait` raises and that client
    catches up on reconnect), so a plain `put_nowait` here would fail to release
    exactly the stalled subscriber that most needs releasing. Dropping its
    oldest pending event to make room costs nothing: it is about to stop
    following, and everything is replayable from the durable table.
    """
    with contextlib.suppress(asyncio.QueueEmpty):
        if q.full():
            q.get_nowait()
    with contextlib.suppress(asyncio.QueueFull):
        q.put_nowait(None)


async def stream(last_id: int = 0) -> AsyncIterator[dict]:
    """Replay everything after `last_id`, then follow live — no gaps, no dupes.

    Subscribing *before* the replay query is what closes the gap: an event
    emitted mid-replay lands in the queue rather than falling between the two
    phases. Anything the replay already covered is then filtered by id.

    Ends when the log closes (shutdown). Nothing is lost by ending: the browser's
    EventSource reconnects with `Last-Event-ID` and replays from the durable
    table, which is the same guarantee it already relies on for a dropped link.
    """
    async with subscribe() as q:
        for event in await repo.list_events(since_id=last_id, limit=1000):
            last_id = event["id"]
            event["created_at"] = event["created_at"].isoformat()
            yield event

        while True:
            event = await q.get()
            if event is None:          # the log closed — stop following
                return
            if event["id"] <= last_id:
                continue  # already sent during replay
            last_id = event["id"]
            yield event
