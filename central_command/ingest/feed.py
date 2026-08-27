"""The live email feed (M12) — polls the n8n email façade and enrolls new mail.

PULL, like everything in Phase 2: Central Command asks the provider "what exists?",
enrolls only what it hasn't seen, and stops there — the dispatcher's valves
govern when any of it is actually worked. The feed deliberately has no valve of
its own beyond `feed_query`: enrollment is cheap and idempotent, and a queue
that grows is visible on the dashboard, while mail silently *not* ingested is
invisible. Scope volume with the query, not by skipping enrollment.

Opt-in (`CC_FEED_ENABLED` default false) — the poller never starts by surprise,
same rule as the dispatcher.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from central_command import events
from central_command.config import settings
from central_command.contract import is_transient
from central_command.db import repo
from central_command.ingest import ledger
from central_command.integrations import email_facade

log = logging.getLogger(__name__)


async def poll_once() -> dict:
    """One sweep: list refs → skip already-enrolled → fetch + enroll the rest.
    Returns {refs, new, enrolled} counts."""
    refs = await email_facade.list_refs(settings.feed_query)
    by_ledger_id = {ledger.provider_message_id(r["uuid"]): r for r in refs}
    known = await repo.existing_message_ids(list(by_ledger_id))
    fresh = [r for mid, r in by_ledger_id.items() if mid not in known]

    enrolled = 0
    for ref in fresh:
        msg = await email_facade.get_message(ref["uuid"])
        result = await ledger.enroll_provider_message(msg)
        if result["enrolled"]:
            enrolled += 1
            await events.emit(
                "work.enrolled",
                ref_id=result["message_id"],
                payload={"feed": "live", "source": "gmail",
                         "subject": (msg.get("subject") or "")[:120]},
                actor="feed",
            )
    return {"refs": len(refs), "new": len(fresh), "enrolled": enrolled}


class FeedPoller:
    """The background poll loop. One per process."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stopping = asyncio.Event()
        self.last_result: dict | None = None
        self.last_error: str | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def status(self) -> dict:
        return {
            "running": self.running,
            "query": settings.feed_query,
            "poll_seconds": settings.feed_poll_seconds,
            "last_result": self.last_result,
            "last_error": self.last_error,
        }

    async def start(self) -> None:
        if self.running:
            return
        self._stopping.clear()
        self._task = asyncio.create_task(self._loop())
        await events.emit(
            "feed.started",
            payload={"query": settings.feed_query,
                     "poll_seconds": settings.feed_poll_seconds},
            actor="feed",
        )

    async def stop(self) -> None:
        was_running = self.running
        self._stopping.set()
        if self._task is not None:
            await asyncio.wait([self._task], timeout=30)
            self._task = None
        if was_running:
            await events.emit("feed.stopped", actor="feed")

    async def _loop(self) -> None:
        log.info("feed: poll loop started (query=%r)", settings.feed_query)
        while not self._stopping.is_set():
            try:
                result = await poll_once()
                self.last_result = result
                self.last_error = None
                # The append-only log records occurrences, not heartbeats: an
                # empty poll every minute forever is noise, not audit.
                if result["enrolled"]:
                    await events.emit("feed.polled", payload=result, actor="feed")
            except Exception as e:  # noqa: BLE001 — the loop must survive provider flaps
                self.last_error = str(e)
                log.warning("feed: poll failed: %s", e)
                await events.emit(
                    "feed.error",
                    payload={"error": str(e)[:300], "transient": is_transient(e)},
                    actor="feed",
                )
            try:
                await asyncio.wait_for(
                    self._stopping.wait(), timeout=settings.feed_poll_seconds
                )
            except asyncio.TimeoutError:
                pass


feed = FeedPoller()


# --- backlog sweep (M13): populate the whole history, newest-first --------------

_SWEEP_KEY = "backlog_sweep"
_DAY = 86_400


class BacklogSweeper:
    """Walks the mailbox history BACKWARDS in time-windowed list queries,
    enrolling references only (content is fetched at claim time).

    Correctness lives in idempotent enrollment — re-sweeping a window enrolls
    nothing twice, and windows deliberately overlap by a day so boundary
    messages can't fall in a crack. The durable cursor (`feed_state`) is
    progress, not correctness: a restart resumes from the last finished window
    instead of re-listing from the top. The provider fails loud at 2,500 refs
    per query, so an over-full window is bisected until it fits.
    """

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stopping = asyncio.Event()
        self.state: dict | None = None
        self.last_error: str | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def status(self) -> dict:
        return {"running": self.running, "state": self.state, "last_error": self.last_error}

    async def start(self, cutoff_date: str | None = None) -> dict:
        if self.running:
            return self.status()

        # The cutoff is a fixed DATE (per deployment), never a duration — a
        # duration would silently re-anchor on every restart. No date, no sweep.
        cutoff = (cutoff_date or settings.backlog_cutoff_date or "").strip()
        if not cutoff:
            raise ValueError(
                "no backlog cutoff date — set CC_BACKLOG_CUTOFF_DATE or pass cutoff_date"
            )
        parsed = datetime.fromisoformat(cutoff)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        horizon = int(parsed.timestamp())

        state = await repo.get_feed_state(_SWEEP_KEY)
        if state is None or state.get("done"):
            # Fresh (or re-run after completion — cheap: dedupe skips the known).
            state = {
                "query": settings.backlog_query,
                "before": int(time.time()),
                "cutoff_date": cutoff,
                "horizon": horizon,
                "enrolled_total": 0,
                "windows": 0,
                "done": False,
            }
        else:
            # Mid-sweep restart: keep the cursor, honour the (possibly changed)
            # cutoff. Correctness is idempotent enrollment either way.
            state["cutoff_date"] = cutoff
            state["horizon"] = horizon
        await repo.set_feed_state(_SWEEP_KEY, state)
        self.state = state
        self._stopping.clear()
        self._task = asyncio.create_task(self._run())
        await events.emit(
            "backlog.started",
            payload={"query": state["query"], "before": state["before"],
                     "horizon": state["horizon"]},
            actor="feed",
        )
        return self.status()

    async def stop(self) -> None:
        was_running = self.running
        self._stopping.set()
        if self._task is not None:
            await asyncio.wait([self._task], timeout=60)
            self._task = None
        if was_running:
            await events.emit("backlog.stopped", payload={"state": self.state}, actor="feed")

    async def _run(self) -> None:
        state = self.state
        window_days = settings.backlog_window_days
        while state["before"] > state["horizon"] and not self._stopping.is_set():
            start = max(state["before"] - window_days * _DAY, state["horizon"])
            # Overlap the lower bound by a day: after:/before: boundary
            # semantics must never be load-bearing when idempotency is free.
            query = f"{state['query']} after:{start - _DAY} before:{state['before']}"
            try:
                refs = await email_facade.list_refs(query)
            except Exception as e:  # noqa: BLE001
                # Bisect on ANY list failure, not just the provider's cap
                # message: an over-full window surfaces as a generic n8n 500
                # ("Error in workflow"), found live 2026-08-12 on a 2,542-ref
                # month. A real outage costs ~5 futile retries per window
                # before parking at 1 day — cheap next to silently equating
                # "window too big" with "provider down".
                if window_days > 1:
                    window_days = max(1, window_days // 2)
                    log.info(
                        "backlog: list failed (%s) — bisecting to %d day(s) in "
                        "case the window is over the provider cap",
                        e, window_days,
                    )
                    continue
                self.last_error = str(e)
                await events.emit(
                    "backlog.error", payload={"error": str(e)[:300], "query": query},
                    actor="feed",
                )
                return  # stop; a restart resumes from the cursor

            by_id = {ledger.provider_message_id(r["uuid"]): r for r in refs}
            known = await repo.existing_message_ids(list(by_id))
            enrolled = 0
            for mid, ref in by_id.items():
                if mid in known:
                    continue
                result = await ledger.enroll_provider_ref(ref)
                if result["enrolled"]:
                    enrolled += 1

            state["before"] = start
            state["enrolled_total"] += enrolled
            state["windows"] += 1
            await repo.set_feed_state(_SWEEP_KEY, state)
            await events.emit(
                "backlog.window",
                payload={"refs": len(refs), "enrolled": enrolled,
                         "swept_back_to": start, "windows": state["windows"]},
                actor="feed",
            )
            window_days = settings.backlog_window_days  # reset after success
            await asyncio.sleep(1)  # be kind to the provider

        if state["before"] <= state["horizon"]:
            state["done"] = True
            await repo.set_feed_state(_SWEEP_KEY, state)
            await events.emit(
                "backlog.completed",
                payload={"enrolled_total": state["enrolled_total"],
                         "windows": state["windows"]},
                actor="feed",
            )


sweeper = BacklogSweeper()
