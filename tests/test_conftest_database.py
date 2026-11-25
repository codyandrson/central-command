"""The suite's own database plumbing in tests/conftest.py (R21, the operator, 2026-10-24:
the suite runs faster on the Pi with nothing skipped).

The throwaway test database commits without waiting for its WAL flush, and
`isolated_queue` settles LAZILY: on a test's first request through
`repo._get_pool`, whatever path makes it, so a test that never touches
Postgres pays nothing and one that reaches it only through a route still finds
earlier tests' parks settled before its first query. The two `planted` tests
run in file order on purpose: the first leaves a park behind, the second must
never see it.
"""

from __future__ import annotations

import uuid
from datetime import UTC

import asyncpg

from central_command.api import routes
from central_command.config import settings
from central_command.db import repo

from .conftest import eager_settle, needs_pg

pytestmark = needs_pg

_PLANTED = f"sess-r21-{uuid.uuid4().hex[:8]}"
_AGENT = "inbox-triage"


async def test_the_test_database_commits_unsynchronised():
    conn = await repo._conn()
    try:
        assert await conn.fetchval("show synchronous_commit") == "off"
    finally:
        await conn.close()


async def test_planted_a_parked_orchestrator_and_left_it():
    conn = await repo._conn()
    try:
        await conn.execute(
            "insert into session (id, agent_id, status) values ($1, $2, 'AWAITING_AGENTS')",
            _PLANTED,
            _AGENT,
        )
    finally:
        await conn.close()


async def test_planted_park_is_settled_before_a_route_only_test_reads():
    """No direct repo call in this test: the route is its first pool request."""
    rows = (await routes.list_sessions(limit=200, agent_id=_AGENT))["sessions"]
    planted = [r for r in rows if r["id"] == _PLANTED]
    assert planted, "the previous test's planted session is missing"
    assert planted[0]["status"] == "DONE"


def test_a_test_with_no_database_use_builds_no_pool():
    calls = []
    hooked = repo._get_pool

    async def spy():
        calls.append(1)
        return await hooked()

    repo._get_pool = spy
    try:
        assert sum(range(10)) == 45
    finally:
        repo._get_pool = hooked
    assert calls == []
    # The previous test's teardown closed its pool; nothing here built one.
    assert repo._pool is None


@eager_settle
async def test_an_eager_test_keeps_a_row_planted_over_its_own_connection():
    """The opt-in for a test that plants over `asyncpg.connect` (bypassing the
    pool door) before its first pool request: settled at setup, so the lazy
    settle cannot flip its planted park to DONE afterwards."""
    sid = f"sess-r21-eager-{uuid.uuid4().hex[:8]}"
    conn = await asyncpg.connect(settings.database_url)
    try:
        await conn.execute(
            "insert into session (id, agent_id, status) values ($1, $2, 'AWAITING_AGENTS')",
            sid,
            _AGENT,
        )
    finally:
        await conn.close()
    assert (await repo.get_session(sid))["status"] == "AWAITING_AGENTS"


async def test_the_suite_holds_the_embedder_check_open():
    """Without this every search in the suite would read the live graph and embed
    its canaries. A test of the check opts out with `embedder_check_live`."""
    from central_command.integrations import embedder_record

    verdict = await embedder_record.check()
    assert verdict.status == embedder_record.OK
    await embedder_record.require_open()


# --- the sweep of idle test databases ------------------------------------------


def test_the_sweep_selects_only_idle_aged_databases_of_other_checkouts():
    """The predicate behind `_sweep_idle_test_databases`, pure: no database is
    created and nothing waits for it to age."""
    from datetime import datetime, timedelta

    from .conftest import sweepable_test_databases

    now = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
    old, fresh = now - timedelta(hours=7), now - timedelta(hours=5)
    own = "central_command_test_aaaaaaaa"
    rows = [
        (own, 0, old),  # this checkout's own
        (own + "_gw0", 0, old),  # ...and its xdist workers
        ("central_command_test_bbbbbbbb", 0, old),  # another checkout, idle, aged
        ("central_command_test_bbbbbbbb_gw3", 0, old),
        ("central_command_test_0123abcd_gw12", 0, old),
        ("central_command_test_cccccccc", 2, old),  # connected
        ("central_command_test_dddddddd", 0, fresh),  # younger than 6 hours
        ("central_command_test_eeeeeeee", 0, None),  # age unreadable: leave it
        ("central_command_test_ffffffff", 0, now - timedelta(hours=6)),  # not MORE than 6
        ("central_command", 0, old),  # the live database, never
        ("central_command_testing_x", 0, old),  # not the test prefix
        ("postgres", 0, old),
    ]
    assert sweepable_test_databases(rows, now=now, own=own) == [
        "central_command_test_bbbbbbbb",
        "central_command_test_bbbbbbbb_gw3",
        "central_command_test_0123abcd_gw12",
    ]


def test_the_sweep_drops_only_names_the_harness_builds():
    """A database somebody named `central_command_test_staging` is not a
    throwaway: the prefix alone must never select it."""
    from datetime import datetime, timedelta

    from .conftest import TEST_DB, sweepable_test_databases

    now = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
    old = now - timedelta(days=30)
    for name in (
        "central_command_test_staging",
        "central_command_test_",
        "central_command_test_ABCDEF01",
        "central_command_test_abcdef0",
        "central_command_test_abcdef012",
        "central_command_test_abcdef01_gw",
        "central_command_test_abcdef01_gwx",
        "central_command_test_abcdef01_w1",
        "central_command_test_abcdef01_gw1_x",
        "x_central_command_test_abcdef01",
    ):
        assert sweepable_test_databases([(name, 0, old)], now=now, own="other") == [], name
    # ...and the shapes `TEST_DB` itself is built in (a checkout hash, with or
    # without the xdist worker suffix) are exactly what it selects.
    assert sweepable_test_databases(
        [(TEST_DB, 0, old)], now=now, own="central_command_test_00000000"
    ) == [TEST_DB]


def test_the_sweep_does_not_connect_at_all_in_a_worker_and_never_raises(monkeypatch):
    from . import conftest

    connects = []

    async def refuse(*args, **kwargs):
        connects.append(args)
        raise OSError("no server")

    monkeypatch.setattr(conftest.asyncpg, "connect", refuse)
    monkeypatch.setattr(conftest.settings, "database_url", "postgresql://nobody@127.0.0.1:1/x")
    # An xdist worker returns before touching the database: no connect at all.
    monkeypatch.setattr(conftest, "_WORKER", "gw1")
    conftest._sweep_idle_test_databases()
    assert connects == [], "a worker must not even try to connect"
    # The main process does try, and swallows the failure.
    monkeypatch.setattr(conftest, "_WORKER", "")
    conftest._sweep_idle_test_databases()
    assert len(connects) == 1


def test_one_unreadable_database_does_not_end_the_sweep(monkeypatch):
    """`pg_stat_file` failing for one database (it is looked up per database)
    leaves that one alone and still sweeps the others."""
    from datetime import datetime, timedelta

    from . import conftest

    old = datetime.now(UTC) - timedelta(days=2)
    dropped, looked_up = [], []

    class FakeConn:
        async def fetch(self, sql, *args):
            return [
                {"datname": "central_command_test_11111111", "oid": "1", "n": 0},
                {"datname": "central_command_test_22222222", "oid": "2", "n": 0},
                {"datname": "central_command_test_33333333", "oid": "3", "n": 1},
                {"datname": "central_command_test_staging", "oid": "4", "n": 0},
            ]

        async def fetchval(self, sql, oid):
            looked_up.append(oid)
            if oid == "1":
                raise RuntimeError("could not stat file")
            return old

        async def execute(self, sql):
            dropped.append(sql)

        async def close(self):
            pass

    async def connect(*args, **kwargs):
        return FakeConn()

    monkeypatch.setattr(conftest.asyncpg, "connect", connect)
    monkeypatch.setattr(conftest, "_WORKER", "")
    conftest._sweep_idle_test_databases()
    assert dropped == ['drop database "central_command_test_22222222"']
    assert looked_up == ["1", "2"], "only an idle, harness-named database is looked up"
