"""The spine's connections come from a pool (db/repo.py).

Callers still `close()` what `_conn()` hands them; the pool is what makes that
close a release instead of a 38 ms handshake on the next call.
"""

from __future__ import annotations

import asyncio

from central_command.db import repo

from .conftest import needs_pg

pytestmark = needs_pg


async def test_close_returns_the_connection_to_the_pool():
    conn = await repo._conn()
    pid = await conn.fetchval("select pg_backend_pid()")
    await conn.close()
    conn = await repo._conn()
    try:
        assert await conn.fetchval("select pg_backend_pid()") == pid
    finally:
        await conn.close()


async def test_two_open_handles_are_two_backends():
    """The ledger's `skip locked` tests race two connections on purpose."""
    a, b = await repo._conn(), await repo._conn()
    try:
        assert await a.fetchval("select pg_backend_pid()") != await b.fetchval("select pg_backend_pid()")
    finally:
        await a.close()
        await b.close()


async def test_a_second_close_is_a_no_op_and_the_pool_survives_closing():
    conn = await repo._conn()
    await conn.close()
    await conn.close()
    await repo.close_pool()
    conn = await repo._conn()
    try:
        assert await conn.fetchval("select 1") == 1
    finally:
        await conn.close()


async def test_concurrent_first_use_builds_one_pool():
    await repo.close_pool()

    async def use():
        conn = await repo._conn()
        try:
            return await conn.fetchval("select 1")
        finally:
            await conn.close()

    assert await asyncio.gather(*(use() for _ in range(8))) == [1] * 8
    assert repo._pool is not None and repo._pool_loop is asyncio.get_running_loop()
