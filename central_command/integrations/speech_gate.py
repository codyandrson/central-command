"""A FIFO gate in front of each speech alias — one job at a time, in order.

The cc-tts / cc-stt LiteLLM aliases carry ``max_parallel_requests: 1`` (DL-220:
the CPU engine behind them cannot take two jobs at once). LiteLLM 1.98.0 made
the second request WAIT at that limit; 1.110.2 REJECTS it (HTTP 429
``Deployment has all max_parallel_requests slots in use``, measured
2026-11-21), and on the audio routes the proxy retries that 429 for several
seconds before returning it. The wait therefore lives here, in the process that
calls the proxy: a request takes a place in line, the proxy sees one request at
a time, and the limit on the alias stays what it always was, the pod's own
protection.

Rules this gate keeps (the operator's standing queue policy: FIFO, no time
budget, no client retry):

* waiters are served in arrival order and the slot is handed straight to the
  next one, so a newcomer can never overtake the line;
* there is NO timeout: a waiter waits for as long as the jobs ahead of it take;
* a waiter that goes away while waiting (its task is cancelled, or ``gone()``
  reports the client disconnected) leaves the line and holds nothing; if the
  slot had already been handed to it that instant, it is passed on;
* nothing here retries: a 429 that still comes back from the proxy is the
  caller's error to surface.

No ``asyncio.Lock``/``Semaphore``: those bind to the first event loop that
makes them wait, which the test suite's per-test loops would trip over. This
holds a plain deque of futures made on the running loop.

Scope: ONE API process (``cc-uvicorn`` runs a single uvicorn worker, no
``--workers``). A second worker, or the Node cockpit server's own gate
(``web/server/lib/fifo-gate.ts``, which fronts the same two aliases on the k3s
profile), would be a separate line to the same proxy.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager

# How often a waiter with a ``gone`` probe looks up from the line.
DISCONNECT_POLL_SECONDS = 0.5


class Disconnected(Exception):
    """The caller went away while it was waiting for its turn."""


class FifoGate:
    def __init__(self, size: int = 1, *, poll_seconds: float = DISCONNECT_POLL_SECONDS) -> None:
        self._free = size
        self._waiters: deque[asyncio.Future[None]] = deque()
        self._poll = poll_seconds

    @property
    def waiting(self) -> int:
        return sum(1 for w in self._waiters if not w.done())

    @property
    def free(self) -> int:
        return self._free

    def _release(self) -> None:
        # Hand the slot to the oldest live waiter without freeing it (no gap
        # for a newcomer to slip into); free it only when nobody is waiting.
        while self._waiters:
            waiter = self._waiters.popleft()
            if not waiter.done():
                waiter.set_result(None)
                return
        self._free += 1

    async def _acquire(self, gone: Callable[[], Awaitable[bool]] | None) -> None:
        if self._free > 0 and not self._waiters:
            self._free -= 1
            return
        waiter: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._waiters.append(waiter)
        try:
            if gone is None:
                await waiter
            else:
                while True:
                    try:
                        await asyncio.wait_for(asyncio.shield(waiter), self._poll)
                        break
                    except TimeoutError:
                        if await gone():
                            raise Disconnected() from None
        except BaseException:
            if waiter.done() and not waiter.cancelled():
                # The slot was handed to this waiter in the instant it left:
                # it holds one, so it gives it back to the next in line.
                self._release()
            else:
                try:
                    self._waiters.remove(waiter)
                except ValueError:
                    pass
                waiter.cancel()
            raise

    @asynccontextmanager
    async def slot(self, gone: Callable[[], Awaitable[bool]] | None = None):
        """Hold the gate's slot for the body. ``gone`` is an optional async
        probe (``request.is_disconnected``); when it answers True while the
        caller is still waiting, ``Disconnected`` is raised and the line is
        left cleanly."""
        await self._acquire(gone)
        try:
            yield
        finally:
            self._release()


# One line per alias, sized to the alias's max_parallel_requests (1 each).
TTS_GATE = FifoGate(1)
STT_GATE = FifoGate(1)

GATES = {"cc-tts": TTS_GATE, "cc-stt": STT_GATE}
