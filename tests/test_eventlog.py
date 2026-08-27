"""Event log tests (M8).

These need a real Postgres: the log's guarantees are durability and total
ordering, and both live in the database. The interesting one is
`test_stream_replays_without_gaps_or_dupes` — it emits an event *during* the
replay phase, which is exactly the race the subscribe-before-replay ordering in
`events.log.stream` exists to close.
"""

from __future__ import annotations

import asyncio

import pytest

from central_command import events
from central_command.db import repo
from tests.conftest import needs_pg

pytestmark = needs_pg


@pytest.fixture()
async def since():
    """Tests assert relative to the log's current head — it is append-only, so
    we never delete rows to isolate a test."""
    return await repo.latest_event_id()


async def test_emit_is_durable_and_ordered(since):
    a = await events.emit("test.one", ref_id="r1", payload={"n": 1}, actor="operator")
    b = await events.emit("test.two", ref_id="r2", payload={"n": 2}, actor="dispatcher")
    assert b["id"] > a["id"]

    rows = await repo.list_events(since_id=since)
    assert [r["kind"] for r in rows] == ["test.one", "test.two"]
    assert rows[0]["payload"] == {"n": 1}
    assert rows[1]["actor"] == "dispatcher"


async def test_kind_filter_matches_namespace_prefix(since):
    await events.emit("work.claimed", ref_id="wi_1", payload={})
    await events.emit("work.processed", ref_id="wi_1", payload={})
    await events.emit("proposal.created", ref_id="p_1", payload={})

    work = await repo.list_events(since_id=since, kind="work")
    assert {r["kind"] for r in work} == {"work.claimed", "work.processed"}

    exact = await repo.list_events(since_id=since, kind="work.claimed")
    assert [r["kind"] for r in exact] == ["work.claimed"]


async def test_ref_id_filter_gives_one_subject_history(since):
    await events.emit("work.claimed", ref_id="wi_hist", payload={})
    await events.emit("work.claimed", ref_id="wi_other", payload={})
    await events.emit("work.processed", ref_id="wi_hist", payload={})

    rows = await repo.list_events(since_id=since, ref_id="wi_hist")
    assert [r["kind"] for r in rows] == ["work.claimed", "work.processed"]


async def test_cursor_returns_only_what_is_new(since):
    first = await events.emit("test.cursor", payload={"n": 1})
    await events.emit("test.cursor", payload={"n": 2})

    rows = await repo.list_events(since_id=first["id"])
    assert [r["payload"]["n"] for r in rows] == [2], "since_id is exclusive"


async def test_stream_replays_without_gaps_or_dupes(since):
    """A reconnecting browser must miss nothing and see nothing twice — even if
    an event is emitted in the window between replay and going live."""
    await events.emit("test.replay", payload={"n": 1})
    await events.emit("test.replay", payload={"n": 2})

    seen: list[int] = []
    stream = events.stream(last_id=since)

    # Drain the two replayed events...
    seen.append((await anext(stream))["payload"]["n"])
    seen.append((await anext(stream))["payload"]["n"])

    # ...then emit live and confirm it arrives exactly once, in order.
    await events.emit("test.replay", payload={"n": 3})
    seen.append((await anext(stream))["payload"]["n"])

    await stream.aclose()
    assert seen == [1, 2, 3]


async def test_live_subscriber_receives_emissions(since):
    async with events.subscribe() as q:
        emitted = await events.emit("test.live", payload={"hello": "world"})
        received = await asyncio.wait_for(q.get(), timeout=2)
    assert received["id"] == emitted["id"]
    assert received["payload"] == {"hello": "world"}


async def test_decision_is_logged_before_execution(since):
    """The log is append-only, so write order *is* read order forever. An audit
    trail that shows a proposal executing before it was approved is false —
    this caught a real bug where the API route emitted the decision after the
    gateway had already executed. The decision emit belongs in the gateway."""
    import inspect

    from central_command.gateway import gateway

    src = inspect.getsource(gateway.approve_and_execute)
    decided_at = src.index('"proposal.decided"')
    execute_call = src.index("executor.execute(")
    assert decided_at < execute_call, "decision must be recorded before acting"

    # `proposal.executed` moved into the shared post-execution tail when the
    # retry driver landed (outage equivalence slice 3) — the ordering claim is
    # unchanged, so the walk follows it rather than the guard being dropped:
    # the tail is only ever reached AFTER `executor.execute` returns.
    tail = inspect.getsource(gateway._record_execution_success)
    assert '"proposal.executed"' in tail
    assert '"proposal.decided"' not in tail, "the decision is recorded once, before acting"
    assert src.index("_record_execution_success(") > execute_call

    # And the route must not re-emit it (that is what mis-ordered the log).
    from central_command.api import routes

    assert '"proposal.decided"' not in inspect.getsource(routes.approve)


async def test_subscriber_is_removed_on_exit(since):
    async with events.subscribe():
        pass
    # A no-longer-subscribed queue must not keep receiving (or leak).
    from central_command.events import log as logmod

    assert len(logmod._subscribers) == 0


# --- shutdown: a follower must be able to STOP following -----------------------


async def test_a_follower_ends_when_the_log_closes(since):
    """A live follower follows forever by construction. Until 2026-08-15 there
    was no way to say "stop", so uvicorn's graceful shutdown waited on every open
    SSE response and cockpit WebSocket until systemd's TimeoutStopSec expired and
    SIGKILLed the process — on EVERY restart, which is how a routine deploy came
    to kill twelve in-flight resumes. The timeout could not be sized to real work
    while this was true."""
    events.log.opened()          # a prior lifespan shutdown may have closed it
    drained: list[dict] = []

    async def follow():
        async for event in events.log.stream(last_id=since):
            drained.append(event)

    before = len(events.log._subscribers)
    follower = asyncio.create_task(follow())
    for _ in range(100):               # it subscribes BEFORE its replay query
        if len(events.log._subscribers) > before:
            break
        await asyncio.sleep(0.01)
    # Prove it is genuinely FOLLOWING by delivering it one, rather than by
    # sleeping and hoping: a stream that ended early would fail here, and the
    # close below would then be proving nothing.
    await events.emit("test.follower_is_live")
    for _ in range(100):
        if drained:
            break
        await asyncio.sleep(0.02)
    assert drained, "the follower never received a live event"

    try:
        events.log.close()
        # The property is that it ends ON ITS OWN, promptly — not that it can be
        # cancelled, which was always true and never helped.
        await asyncio.wait_for(follower, timeout=2)
    finally:
        events.log.opened()


async def test_a_stalled_follower_still_ends(since):
    """The fan-out DROPS into a full queue (`put_nowait` raises QueueFull and the
    client catches up on reconnect), so a naive sentinel push would fail to
    release exactly the stalled subscriber that most needs releasing. `close()`
    makes room instead — that client is about to stop following and everything is
    replayable from the durable table."""
    events.log.opened()
    async with events.log.subscribe() as q:
        for i in range(q.maxsize):
            q.put_nowait({"id": since + 1 + i, "kind": "filler"})
        assert q.full()

        try:
            events.log.close()
            while True:                # drain to the sentinel
                item = await asyncio.wait_for(q.get(), timeout=2)
                if item is None:
                    break
        finally:
            events.log.opened()


def test_the_close_flag_is_not_loop_bound():
    """`asyncio.Event` carries `_LoopBoundMixin` — it binds to the first loop
    that awaits it and raises RuntimeError from every other one, which in the
    first version of this code was silently read as end-of-stream. A plain bool
    plus a queue sentinel has no loop affinity, so the same module works across
    the loops pytest hands each test (and would across any future one)."""
    from central_command.events import log as logmod

    assert not isinstance(logmod._closing, asyncio.Event)

    def one_loop() -> bool:
        async def go():
            async with logmod.subscribe() as q:
                logmod.close()
                return await asyncio.wait_for(q.get(), timeout=2) is None
        try:
            return asyncio.run(go())
        finally:
            logmod.opened()

    assert one_loop() and one_loop(), "a second loop must behave like the first"
