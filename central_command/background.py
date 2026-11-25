"""Keep a fire-and-forget asyncio task alive until it finishes.

The event loop holds only a weak reference to a task, so a task nobody keeps a
reference to can be garbage-collected before it is done (the asyncio docs,
`asyncio.create_task`). `keep()` is the docs' remedy: a module-level set holds
the task and its done callback drops it. It changes nothing else: the task
starts when it was created, and an exception it raises surfaces exactly as an
unreferenced task's would ("Task exception was never retrieved", once the
finished task is released).

Standard library only, so every tier may import it (`runtime/` included: it
imports nothing from the gateway or the executor).
"""

from __future__ import annotations

import asyncio

_running: set[asyncio.Task] = set()


def keep(task: asyncio.Task) -> asyncio.Task:
    """Hold `task` until it is done; returns it."""
    _running.add(task)
    task.add_done_callback(_running.discard)
    return task
