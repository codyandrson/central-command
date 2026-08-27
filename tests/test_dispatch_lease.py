"""Dispatcher singleton-lease tests.

The incident (2026-07-19, live): a SIGTERMed server draining one open SSE
connection kept its dispatcher loop polling for hours. Two loops split the
queue safely (`skip locked`), but the zombie ran OLD config — its half of the
dismissals skipped the auditor. The lease turns "one drain loop per database"
into a database invariant: a second Dispatcher refuses to start while another
holds the advisory lock, and a dead process releases it automatically.
"""

from __future__ import annotations

import pytest

from central_command.config import settings
from central_command.db import repo
from central_command.ingest.dispatcher import Dispatcher
from tests.conftest import needs_pg

pytestmark = needs_pg


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    # Long poll so started loops just sleep instead of touching the queue.
    monkeypatch.setattr(settings, "dispatch_poll_seconds", 60.0)


async def test_second_dispatcher_is_refused_while_the_lease_is_held():
    a, b = Dispatcher(), Dispatcher()
    since = await repo.latest_event_id()
    await a.start()
    try:
        assert a.running
        await b.start()
        assert not b.running
        assert "lease" in b.last_reason
        refused = await repo.list_events(since_id=since, kind="dispatch.lease_refused")
        assert refused, "the refusal must be on the record"
    finally:
        await a.stop()
        await b.stop()


async def test_stopping_releases_the_lease_for_the_next_holder():
    a, b = Dispatcher(), Dispatcher()
    await a.start()
    await a.stop()
    try:
        await b.start()
        assert b.running  # the lease came free with the stop
    finally:
        await b.stop()


async def test_stop_start_cycle_on_one_instance_keeps_working():
    a = Dispatcher()
    await a.start()
    await a.stop()
    await a.start()
    try:
        assert a.running
    finally:
        await a.stop()
