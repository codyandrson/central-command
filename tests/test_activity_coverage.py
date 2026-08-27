"""Activity coverage — the Runs facets, the keyset cursor, and the guarantee.

The point of the 2026-08-11 activity-coverage record is a PROPERTY, not a
screen: every session is reachable from one surface. `test_every_mode_and_
status_combination_is_reachable` is that property as a test — a new session
producer that invents a mode or a status fails the suite in the commit that
adds it, rather than becoming another invisible 423-row cohort.

Needs a real Postgres: keyset paging and the filter predicates live in SQL, so
mocking them would test nothing (same reasoning as `test_ledger.py`).
"""

from __future__ import annotations

import pytest

from central_command.db import repo
from tests.conftest import needs_pg

pytestmark = needs_pg


@pytest.fixture()
async def runs():
    """Six sessions spanning three agents, two modes and three statuses, all
    created at the SAME instant so the tie-breaking half of the cursor is
    exercised rather than accidentally avoided."""
    ids = [f"sess_act_{i}" for i in range(6)]
    spec = [
        ("act-alpha", "oneshot", "DONE"),
        ("act-alpha", "oneshot", "FAILED"),
        ("act-alpha", "conversation", "AWAITING_OPERATOR"),
        ("act-bravo", "oneshot", "DONE"),
        ("act-bravo", "conversation", "DONE"),
        ("act-charlie", "oneshot", "FAILED"),
    ]
    agents = sorted({a for a, _, _ in spec})
    conn = await repo._conn()
    try:
        for agent in agents:
            await repo.upsert_agent(agent, agent, "cc-default", "test charter")
        for sid, (agent, mode, status) in zip(ids, spec):
            await conn.execute(
                "insert into session (id, agent_id, status, mode, created_at)"
                " values ($1, $2, $3, $4, now())",
                sid, agent, status, mode,
            )
        yield list(zip(ids, spec))
    finally:
        await conn.execute("delete from session where id = any($1::text[])", ids)
        await conn.execute("delete from agent where id = any($1::text[])", agents)
        await conn.close()


async def test_filters_narrow_to_exactly_the_matching_rows(runs):
    by_agent = await repo.list_sessions(100, agent_id="act-alpha")
    assert {r["id"] for r in by_agent} == {"sess_act_0", "sess_act_1", "sess_act_2"}

    by_mode = await repo.list_sessions(100, mode="conversation")
    assert {"sess_act_2", "sess_act_4"} <= {r["id"] for r in by_mode}
    assert all(r["mode"] == "conversation" for r in by_mode)

    combo = await repo.list_sessions(100, agent_id="act-alpha", status="FAILED")
    assert [r["id"] for r in combo] == ["sess_act_1"]


async def test_keyset_paging_never_repeats_or_skips_a_row(runs):
    """All six share a `created_at`, so a cursor on the timestamp alone would
    either loop forever or drop the whole tie group. The id is why it can't."""
    seen: list[str] = []
    cursor = None
    for _ in range(10):
        page = await repo.list_sessions(
            2, agent_id=None,
            before=cursor[0] if cursor else None,
            before_id=cursor[1] if cursor else None,
        )
        page = [r for r in page if r["id"].startswith("sess_act_")]
        if not page:
            break
        seen.extend(r["id"] for r in page)
        last = page[-1]
        cursor = (last["created_at"], last["id"])

    assert len(seen) == len(set(seen)), "a row was served twice"
    assert set(seen) == {f"sess_act_{i}" for i in range(6)}


async def test_a_run_names_its_source_from_the_work_ledger(runs):
    """423 of the Pi's 601 sessions are ledger drains with no task. Without
    `work_subject` the browser prints a uuid for 70% of its rows."""
    item_id = "wi_act_test"
    conn = await repo._conn()
    try:
        await conn.execute(
            "insert into work_item (id, message_id, feed, source, subject,"
            " payload, state, session_id)"
            " values ($1, $2, 'test', 'test', $3, '{}'::jsonb, 'PROCESSED', $4)",
            item_id, f"<{item_id}@test>", "Scanned image from the library",
            "sess_act_0",
        )
        row = next(r for r in await repo.list_sessions(100, agent_id="act-alpha")
                   if r["id"] == "sess_act_0")
        assert row["work_subject"] == "Scanned image from the library"
    finally:
        await conn.execute("delete from work_item where id = $1", item_id)
        await conn.close()


async def test_every_mode_and_status_combination_is_reachable(runs):
    """THE coverage guarantee. Every (mode, status) pair the database actually
    holds must come back from the unfaceted Runs query — which is what makes
    "all activity is visible in at least one UI" a property rather than a
    claim. A new producer inventing a mode fails here, not in six months."""
    conn = await repo._conn()
    try:
        pairs = {
            (r["mode"], r["status"])
            for r in await conn.fetch("select distinct mode, status from session")
        }
    finally:
        await conn.close()

    # The browser's default page is 50; walk the cursor to cover the table.
    reachable: set[tuple[str, str]] = set()
    cursor = None
    while True:
        page = await repo.list_sessions(
            200, before=cursor[0] if cursor else None,
            before_id=cursor[1] if cursor else None,
        )
        if not page:
            break
        reachable |= {(r["mode"], r["status"]) for r in page}
        cursor = (page[-1]["created_at"], page[-1]["id"])

    assert pairs <= reachable, f"unreachable from Activity → Runs: {pairs - reachable}"


async def test_agent_facet_vocabulary_comes_from_the_rows(runs):
    agents = await repo.session_agent_ids()
    assert {"act-alpha", "act-bravo", "act-charlie"} <= set(agents)
    assert agents == sorted(agents)
