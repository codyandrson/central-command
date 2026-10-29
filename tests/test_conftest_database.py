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
            _PLANTED, _AGENT)
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
            sid, _AGENT)
    finally:
        await conn.close()
    assert (await repo.get_session(sid))["status"] == "AWAITING_AGENTS"
