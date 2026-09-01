"""The control-plane API. Serves the JSON API (central_command.api.routes), an SSE
stream for live updates, and — when built — the React dashboard from web/dist."""

import contextlib
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from central_command import __version__
from central_command.api.routes import router
from central_command.config import settings
from central_command.events import bridge as event_bridge
from central_command.events import log as event_log
from central_command.heartbeat import engine as heartbeat_engine
from central_command.ingest.dispatcher import dispatcher
from central_command.ingest.feed import feed, sweeper


def _release_followers_on_signal() -> list:
    """Close the event log the moment SIGTERM/SIGINT arrives, BEFORE uvicorn
    starts waiting for connections to close.

    The ordering is not a detail, it is the whole thing (uvicorn `server.py`):

        await asyncio.wait_for(self._wait_tasks_to_complete(),
                               timeout=self.config.timeout_graceful_shutdown)
        ...
        if not self.force_exit:
            await self.lifespan.shutdown()

    Connections are waited on FIRST and the lifespan shutdown runs after, so a
    `close()` in the lifespan can never release the SSE response that is blocking
    the wait for it — a deadlock by ordering, measured 2026-08-15 as a restart
    that sat the full 120s and got SIGKILLed with the fix "in place". A signal
    handler is the only hook that runs early enough.

    Uvicorn installs its own handlers with `signal.signal` before startup, so the
    current handler here IS `Server.handle_exit`; we wrap rather than replace it.
    `--timeout-graceful-shutdown` stays as the backstop, and because this makes
    followers end on their own it should never fire — which is the point: a
    timeout that trips on every routine deploy is not a signal, it is noise.
    """
    import signal
    import threading

    if threading.current_thread() is not threading.main_thread():
        return []                      # signals are main-thread only (tests)
    installed = []
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            previous = signal.getsignal(sig)

            def handler(signum, frame, _previous=previous):
                event_log.close()
                if callable(_previous):
                    _previous(signum, frame)

            signal.signal(sig, handler)
            installed.append((sig, previous))
        except (ValueError, OSError):  # noqa: PERF203 — not fatal, the backstop remains
            pass
    return installed


@asynccontextmanager
async def lifespan(app: FastAPI):
    event_log.opened()
    # WARNING+ log records surface in Activity → Events (log.error/log.warning
    # events). Installed here because the handler needs the serving loop.
    event_bridge.install()
    _signal_handlers = _release_followers_on_signal()
    # The roster reflects the whole team from startup — an agent must be
    # visible (and its charter editable) BEFORE its first run, not after.
    try:
        from central_command.runtime.jira_expert import ensure_registered

        await ensure_registered()
    except Exception:  # noqa: BLE001 — a down DB shows up loudly elsewhere
        pass
    try:
        # Same seam: the Confluence founders are reached by task/consult/
        # conversation, all of which need the roster row to exist first.
        from central_command.runtime.confluence_expert import (
            ensure_registered as ensure_confluence_expert,
        )
        from central_command.runtime.wiki_agent import ensure_registered as ensure_wiki_agent

        await ensure_confluence_expert()
        await ensure_wiki_agent()
    except Exception:  # noqa: BLE001 — a down DB shows up loudly elsewhere
        pass
    try:
        # The MCP manager has no ledger/heartbeat trigger of its own (unlike
        # the steward/EA) — it's reached only via task or conversation, both
        # of which need the roster row to exist first. Startup is the seam.
        from central_command.runtime.mcp_manager import ensure_registered as ensure_mcp_manager

        await ensure_mcp_manager()
    except Exception:  # noqa: BLE001 — a down DB shows up loudly elsewhere
        pass
    try:
        # The editor has no ledger/heartbeat trigger of its own either — it's
        # reached only via consult or a direct task/conversation, both of
        # which need the roster row to exist first. Startup is the seam.
        from central_command.runtime.editor import ensure_registered as ensure_editor

        await ensure_editor()
    except Exception:  # noqa: BLE001 — a down DB shows up loudly elsewhere
        pass
    try:
        # Same seam as the editor's: the accountability agent is reached only
        # by task or conversation (a check-in), both of which need the roster
        # row to exist first.
        from central_command.runtime.accountability import (
            ensure_registered as ensure_accountability,
        )

        await ensure_accountability()
    except Exception:  # noqa: BLE001 — a down DB shows up loudly elsewhere
        pass
    try:
        # Runs are in-process and nothing has run in THIS process yet, so any
        # RUNNING session is an orphan of a dead process — age 0, same argument
        # as the dispatcher's reclaim. Unconditional (not gated on dispatch):
        # conversation and task runs orphan the same way, and the dispatcher's
        # own sweep uses the 900s threshold, which let an 8-minute-old zombie
        # survive both restarts on 2026-08-17.
        from central_command.ingest.dispatcher import sweep_orphaned_sessions

        await sweep_orphaned_sessions(0)
    except Exception:  # noqa: BLE001 — a down DB shows up loudly elsewhere
        pass
    try:
        # Startup-only re-launch of tasks queued but never started before the
        # process died (`routes.startup_task_sweep` — see its docstring for
        # why this must never become a periodic sweep). Same rationale as the
        # orphan-session sweep just above: nothing has run in THIS process
        # yet, so the in-memory per-agent queue is empty and any plain-
        # ASSIGNED row is genuinely orphaned, not merely queued.
        from central_command.api.routes import startup_task_sweep

        await startup_task_sweep()
    except Exception:  # noqa: BLE001 — a down DB shows up loudly elsewhere
        pass
    try:
        # A CLAIMED work_item is also a same-process orphan by construction —
        # same argument as the two sweeps above — but `reclaim_stale_work_items`
        # was previously only called from `Dispatcher.start()`, so a hand-fed
        # item (`POST /api/emails` -> `dispatch_item`, which never goes through
        # the dispatcher loop) stayed CLAIMED forever on any run that died
        # mid-flight (e.g. a client disconnect) when dispatch was left disabled
        # (its default). Unconditional and age-0, matching the sweeps above.
        from central_command.db import repo as _repo

        await _repo.reclaim_stale_work_items(0)
    except Exception:  # noqa: BLE001 — a down DB shows up loudly elsewhere
        pass
    # Opt-in (CC_DISPATCH_ENABLED / CC_FEED_ENABLED / CC_HEARTBEAT_ENABLED):
    # background loops never start by surprise.
    if settings.dispatch_enabled:
        await dispatcher.start()
    if settings.feed_enabled:
        await feed.start()
    if settings.heartbeat_enabled:
        await heartbeat_engine.start()
    # D7 phase 2 catch-up: parked orchestrations whose wake-up hook was lost
    # to a restart resume here (the hooks are accelerators; this is the
    # guarantee). Background — startup must not block on a model round.
    try:
        import asyncio

        from central_command.api import orchestration

        asyncio.create_task(orchestration.resume_sweep())
        # Same guarantee for audits: the inline hooks a restart's
        # CancelledError killed (or a transient 5xx degraded) re-run here,
        # so a parked dismissal never waits on the human gate by accident.
        from central_command.gateway import auditor

        asyncio.create_task(auditor.audit_sweep())
    except Exception:  # noqa: BLE001 — a down DB shows up loudly elsewhere
        pass
    yield
    # Detach BEFORE closing the log: a record logged during teardown must not
    # race a loop that is about to stop taking work.
    event_bridge.uninstall()
    with contextlib.suppress(Exception):
        await event_bridge.drain()
    # Idempotent belt-and-braces: on a signalled shutdown the handler above has
    # already closed the log (it has to — see its docstring), but a programmatic
    # or test shutdown reaches no signal at all.
    event_log.close()
    import signal as _signal

    for _sig, _previous in _signal_handlers:
        with contextlib.suppress(ValueError, OSError):
            _signal.signal(_sig, _previous)
    await heartbeat_engine.stop()
    await sweeper.stop()
    await feed.stop()
    await dispatcher.stop()


# Docs live under the /api prefix with everything else, so the cockpit server
# can proxy them same-origin (Systems page "Swagger" link) without exposing
# the API port itself.
app = FastAPI(
    title="Central Command", version=__version__, lifespan=lifespan,
    docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None,
)

# Dev: the Vite dev server (5173) calls the API on 8080.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)

# The Nerve cockpit (web/) speaks the OpenClaw gateway protocol at /ws.
from central_command.api.nerve_gateway import router as nerve_gateway_router  # noqa: E402

app.include_router(nerve_gateway_router)

# Systems view (launchpad): its own module/router, kept out of routes.py.
from central_command.api.systems import router as systems_router  # noqa: E402

app.include_router(systems_router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "central_command", "version": __version__}


# Serve the built dashboard if it exists (production single-server mode).
_dist = Path(__file__).resolve().parents[2] / "web" / "dist"
if _dist.is_dir():
    app.mount("/", StaticFiles(directory=str(_dist), html=True), name="web")
