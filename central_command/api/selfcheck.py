"""`/api/selfcheck` — the application self-check (`central_command/selfcheck.py`)
served from inside the process that serves the agents, for the Systems page
and for the agent that reads it.

READINESS only, like the module it serves: a result is shown, never acted on.
Nothing here restarts, stops or reconfigures anything because a check failed.

Two of the checks spend one model request each, so the result is CACHED in
memory: `GET` returns the last document and spends nothing; a run happens once
at API start (`start_on_boot`, switchable by `CC_SELFCHECK_ON_START`) and
whenever the operator asks (`POST /api/selfcheck/run`). Single-flight — a
request while a run is in flight starts no second one.

Own module + router, registered in api/app.py exactly like `systems_router`.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from central_command import selfcheck
from central_command.config import settings

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

_last: dict | None = None
_task: asyncio.Task | None = None


def _running() -> bool:
    return _task is not None and not _task.done()


def document() -> dict:
    """The last result (or `never_run`), with `running` saying whether a new
    one is in flight. The previous result stays readable during a run."""
    doc = dict(_last) if _last is not None else selfcheck.never_run(mode="api")
    doc["running"] = _running()
    return doc


async def _run_and_keep() -> None:
    global _last
    try:
        _last = await selfcheck.run(mode="api")
    except Exception:
        # The runner reports every check's failure itself; this is the belt
        # for a bug in the runner, which must not leave a task exception
        # nobody retrieves.
        log.exception("selfcheck: the run itself failed")


def start_run() -> bool:
    """Start one run in the background; False (and nothing started) when one
    is already in flight."""
    global _task
    if _running():
        return False
    _task = asyncio.get_running_loop().create_task(_run_and_keep())
    return True


def _under_test_suite() -> bool:
    # The suite imports the app and may run its lifespan; a startup run there
    # would spend real model requests against whatever .env the checkout has.
    return "pytest" in sys.modules or bool(os.environ.get("PYTEST_CURRENT_TEST"))


def start_on_boot() -> bool:
    """The lifespan's one run at API start. Never under the test suite, never
    in demo mode (which promises no provider call), and off when
    CC_SELFCHECK_ON_START is false. Background — startup never waits on a
    model round."""
    if not settings.selfcheck_on_start or settings.demo_mode or _under_test_suite():
        return False
    return start_run()


@router.get("/selfcheck")
async def get_selfcheck() -> dict:
    """The LAST result, from memory. Spends nothing and checks nothing."""
    return document()


@router.post("/selfcheck/run", status_code=202)
async def run_selfcheck() -> JSONResponse:
    """Start a run (unless one is in flight) and answer at once with the
    current document, `running: true`; poll `GET /api/selfcheck` for the
    result."""
    start_run()
    return JSONResponse(document(), status_code=202)
