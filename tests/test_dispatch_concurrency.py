"""The drain loop must overlap runs up to the concurrency valve.

Until 2026-08-16 the loop awaited each item inline, so `dispatch_concurrency`
was a no-op: in-flight never exceeded 1 whatever the valve said. The loop now
claims serially (so the ledger-backed `count_in_flight` stays honest between
valve checks) and runs each claimed item as a task. No database needed here —
claim and run are monkeypatched; what's under test is the loop's shape.
"""

from __future__ import annotations

import asyncio

from central_command.ingest import dispatcher


async def test_loop_overlaps_runs(monkeypatch):
    d = dispatcher.Dispatcher()
    items = [{"id": f"i{n}"} for n in range(3)]

    async def fake_valves():
        return True, "ok"

    async def fake_claim():
        return items.pop(0) if items else None

    running = 0
    peak = 0
    all_done = asyncio.Event()
    finished = 0

    async def fake_process(item, model=None, actor="dispatcher"):
        nonlocal running, peak, finished
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.05)
        running -= 1
        finished += 1
        if finished == 3:
            all_done.set()

    monkeypatch.setattr(dispatcher, "check_valves", fake_valves)
    monkeypatch.setattr(dispatcher.repo, "claim_next_work_item", fake_claim)
    monkeypatch.setattr(dispatcher, "process_claimed", fake_process)

    loop_task = asyncio.create_task(d._loop())
    try:
        await asyncio.wait_for(all_done.wait(), timeout=2)
    finally:
        d._stopping.set()
        await asyncio.wait_for(loop_task, timeout=2)

    assert peak == 3, f"runs must overlap (peak in flight was {peak})"
    assert not d._tasks, "finished tasks must not accumulate"
