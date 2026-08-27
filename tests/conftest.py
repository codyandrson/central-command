"""Shared test fixtures.

The ledger tests exercise `claim_next_work_item`, which by design takes the next
eligible row from the *whole* queue. That makes them silently dependent on the
database containing nothing but their own rows — one stray UNPROCESSED row from
a manual experiment and tests start claiming it instead of their fixtures.

Rather than wipe a shared dev database (destructive, and not the tests' data to
delete), park pre-existing queued rows for the duration of each test and restore
them afterwards.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import asyncpg
import pytest

from central_command.config import settings
from central_command.db import repo
from central_command.integrations import graphiti, jira, neo4j_writer


def aresolve(fn):
    """Wrap a SYNC model double for the async `resolve_model` seam.

    `resolve_model` reads the agent row (per-agent model + thinking), so it is a
    coroutine function now — a plain lambda substituted for it fails at the
    `await`, not at the patch, which reads as a runtime bug in the code under
    test rather than a stale double."""
    async def _resolve(*a, **k):
        return fn(*a, **k)

    return _resolve


PARKED = "PARKED_FOR_TEST"
# Under pytest-xdist every worker gets its OWN database (central_command_test_gw0,
# …), because the suite's isolation model — park all foreign UNPROCESSED rows,
# assert on rows you created — is per-DATABASE, not per-row. Two workers
# sharing one database would park each other's fixtures mid-test. The suffix
# comes from xdist itself; a plain serial run keeps the unsuffixed name.
_WORKER = os.environ.get("PYTEST_XDIST_WORKER", "")
TEST_DB = "central_command_test" + (f"_{_WORKER}" if _WORKER else "")


@pytest.fixture(autouse=True)
def no_custom_field_discovery(monkeypatch):
    """Every Jira read consults `/rest/api/3/field` to learn the instance's
    custom fields. Seed that cache EMPTY so no test makes the call by accident
    (a faked `_call` would hand it the wrong response and the failure would read
    as a bug in the read under test). Tests that care about custom fields seed
    the cache themselves — see tests/test_jira_custom_fields.py.
    """
    monkeypatch.setattr(jira, "_custom_fields_cache", (float("inf"), []))


@pytest.fixture(scope="session", autouse=True)
def no_live_graph_writes():
    """The knowledge graph is not a test fixture — refuse a real write.

    `_switch_to_test_database` below exists because the suite once injected its
    proposals into a live Decisions Inbox. The graph had no equivalent guard and
    the same thing happened to it: suite runs left `DEMO-1 deadline slip`
    episodes in the REAL `central_command` group carrying the executor's own
    `trust=human-approved | approver=human:lee` — forged provenance on facts
    nobody approved, in the store the whole team reads (found 2026-08-15).

    Two things about the placement are deliberate.

    It guards the TRANSPORT, not `add_episode`: several tests legitimately call
    the real `add_episode` with `_call_tool` faked underneath to assert on the
    group_id it computes, and a guard one level up fails those for doing exactly
    the right thing. Refusing the `add_memory` CALL catches every writer —
    runtime, executor, or a bare integration call — and leaves reads alone, so
    the live read round-trips in tests/test_graph.py still run.

    And it is SESSION-scoped, so it is never torn down between tests. The
    writes seen here were intermittent, which is the signature of an async task
    outliving the test that spawned it: a function-scoped monkeypatch is already
    restored by the time that task lands, and the write goes to the real graph
    with nothing left to stop it.
    """
    real_call_tool = graphiti._call_tool

    async def guarded(name, arguments, timeout=30.0):
        # Same opt-in as the bolt guard below, added 2026-08-19: the
        # invalidation-attribution probe (tests/test_graph_delta_live.py) must
        # write real scratch-group episodes, because what Graphiti's queued
        # ingestion actually stamps on retired edges is a claim about the live
        # store that a fake proves nothing about. Never by default.
        if name == "add_memory" and os.getenv("CC_LIVE_GRAPH_TESTS") != "1":
            raise AssertionError(
                "a test wrote to the live knowledge graph (add_memory). It is "
                "not a fixture — fake graphiti.add_episode or graphiti._call_tool "
                "instead (see tests/test_graph.py, tests/test_graph_scope.py), or "
                "set CC_LIVE_GRAPH_TESTS=1 to run a cleanup-after-itself live "
                "probe deliberately."
            )
        return await real_call_tool(name, arguments, timeout=timeout)

    # The bolt WRITE path (operator curation, 2026-08-15) needs the same guard
    # for the same reason, one layer lower: it does not go through MCP at all,
    # so `add_memory` never sees it. It is opt-in rather than absolutely
    # refused, because its invariants — an entity's types written to two
    # places, an edge's endpoints written to two places — are claims about what
    # Neo4j actually stores and a fake proves none of them. Run them
    # deliberately with CC_LIVE_GRAPH_TESTS=1, never by default: even a
    # scratch-group node changes what the live read round-trips see (a leftover
    # entity pushed `search_facts(max_facts=3)` to four results, 2026-08-15).
    real_write = neo4j_writer._write

    async def guarded_write(query, **params):
        if os.getenv("CC_LIVE_GRAPH_TESTS") != "1":
            raise AssertionError(
                "a test wrote to the live knowledge graph over bolt "
                "(neo4j_writer). Set CC_LIVE_GRAPH_TESTS=1 to run the curation "
                "round-trips deliberately; they clean up after themselves."
            )
        return await real_write(query, **params)

    graphiti._call_tool = guarded
    neo4j_writer._write = guarded_write
    try:
        yield
    finally:
        graphiti._call_tool = real_call_tool
        neo4j_writer._write = real_write


def _switch_to_test_database() -> None:
    """Point the whole suite at a dedicated database, creating it if needed.

    Tests used to run against the same Postgres database as the running app, so
    `pytest` would inject its proposals straight into a live Decisions Inbox —
    which happened during a supervised session and left 8 stray proposals to
    review. Tests must never write into the database someone is operating on.
    """
    base = settings.database_url
    if base.rsplit("/", 1)[-1] == TEST_DB:
        return
    admin_url = base.rsplit("/", 1)[0] + "/postgres"
    test_url = base.rsplit("/", 1)[0] + "/" + TEST_DB

    async def prepare() -> bool:
        try:
            conn = await asyncpg.connect(admin_url)
        except Exception:  # noqa: BLE001 — no Postgres at all; DB tests will skip
            return False
        try:
            # Recreate from scratch EVERY run. The schema execute below is
            # idempotent, so an accreting database passes it fine — and that
            # is exactly the trap: leaked rows (hired test agents especially)
            # accumulate across runs, and by 2026-08-09 the roster had grown
            # to 297 agents, turning the agents-page parity test into ~80
            # minutes of quiet grinding that read as a hung suite. A test
            # database is nobody's record; it restarts from zero.
            await conn.execute(f'drop database if exists "{TEST_DB}" with (force)')
            await conn.execute(f'create database "{TEST_DB}"')
        finally:
            await conn.close()

        schema = (Path(__file__).resolve().parents[1] / "central_command" / "db" / "schema.sql").read_text(encoding="utf-8")
        conn = await asyncpg.connect(test_url)
        try:
            await conn.execute(schema)
        finally:
            await conn.close()
        return True

    if asyncio.run(prepare()):
        settings.database_url = test_url


_switch_to_test_database()


async def _pg_ok() -> bool:
    try:
        conn = await repo._conn()
        await conn.close()
        return True
    except Exception:  # noqa: BLE001
        return False


def pg_available() -> bool:
    """Can this run reach Postgres? Answered ONCE per session, at import.

    76 test modules used to carry their own byte-for-byte copy of this probe
    (four trivially different spellings of the same predicate, all with the
    same skip reason). Each copy also ran `asyncio.run` at import time, so
    merely COLLECTING the suite opened 76 throwaway connections at ~42ms each.
    Import `needs_pg` from here instead.
    """
    global _PG_AVAILABLE
    if _PG_AVAILABLE is None:
        try:
            _PG_AVAILABLE = asyncio.run(_pg_ok())
        except Exception:  # noqa: BLE001 — no event loop, no driver, no server
            _PG_AVAILABLE = False
    return _PG_AVAILABLE


_PG_AVAILABLE: bool | None = None

# THE marker for "this test needs a real Postgres". Use it three ways, matching
# what the modules already did: `pytestmark = needs_pg` (file-wide),
# `@needs_pg` (one test), or `pytestmark = [needs_pg, other]`.
needs_pg = pytest.mark.skipif(
    not pg_available(), reason=f"no Postgres at {settings.database_url}"
)


@pytest.fixture(autouse=True)
async def isolated_queue():
    """Hide any queued work that this test did not create, then give it back.

    Only UNPROCESSED rows are parked: CLAIMED/FOLD_PENDING rows are mid-flight
    and moving them would corrupt state a test may be asserting on.

    Availability is the CACHED answer, not a fresh probe: this fixture used to
    open a throwaway connection per test just to re-ask what `pg_available()`
    already knew — ~35ms × 1200 tests ≈ 45s of pure connect overhead.
    """
    if not pg_available():
        yield
        return

    conn = await repo._conn()
    try:
        await conn.execute(
            f"update work_item set state = '{PARKED}' where state = 'UNPROCESSED'"
        )
    finally:
        await conn.close()

    try:
        yield
    finally:
        conn = await repo._conn()
        try:
            await conn.execute(
                f"update work_item set state = 'UNPROCESSED' where state = '{PARKED}'"
            )
        finally:
            await conn.close()
