"""The logging→event bridge: WARNING+ records become durable events, and the
bridge can never feed itself (a record about emitting must not emit)."""

from __future__ import annotations

import asyncio
import logging

import pytest

from central_command.db import repo
from central_command.events import bridge
from tests.conftest import needs_pg

pytestmark = needs_pg


@pytest.fixture()
async def bridged():
    since = await repo.latest_event_id()
    bridge.install()
    try:
        yield since
    finally:
        await asyncio.sleep(0)  # let call_soon_threadsafe callbacks spawn tasks
        await bridge.drain()
        bridge.uninstall()


async def test_error_and_warning_records_become_events(bridged):
    log = logging.getLogger("central_command.test.bridge")
    try:
        raise ValueError("the original failure")
    except ValueError:
        log.exception("resume sweep failed for %s", "s_test")
    log.warning("valve nearly closed")
    log.info("routine chatter")  # below the handler's level — must not bridge

    await asyncio.sleep(0)
    await bridge.drain()

    # Scoped to THIS test's logger, and compared as a SET: the bridge hands
    # each record off separately, so two records emitted back-to-back are
    # written by two tasks and their relative order is not guaranteed — an
    # exception record, which formats a traceback first, loses that race to a
    # plain warning often enough to fail under parallel load. What the bridge
    # promises is that WARNING+ arrives and INFO does not; asserting a
    # between-record order asserts something it never offered.
    rows = [
        r for r in await repo.list_events(since_id=bridged, kind="log")
        if r["payload"].get("logger") == "central_command.test.bridge"
    ]
    assert sorted(r["kind"] for r in rows) == ["log.error", "log.warning"]
    err = next(r for r in rows if r["kind"] == "log.error")
    assert err["payload"]["message"] == "resume sweep failed for s_test"
    assert err["payload"]["logger"] == "central_command.test.bridge"
    assert "the original failure" in err["payload"]["traceback"]
    assert err["actor"] == "system"


async def test_the_bridge_never_feeds_itself(bridged):
    # The fan-out's own warning ("subscriber queue full") logs under
    # central_command.events.* — bridging it would emit an event about emitting.
    logging.getLogger("central_command.events.log").warning("queue full")

    await asyncio.sleep(0)
    await bridge.drain()

    assert await repo.list_events(since_id=bridged, kind="log") == []
