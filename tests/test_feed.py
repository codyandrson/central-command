"""Live email feed tests (M12).

The properties that matter: **provider messages get a stable ledger identity**
(Gmail's immutable id → idempotent enrollment; conversation_id → thread id, so
M9 folding works on real mail), **known mail is never re-fetched** (the poller
checks the ledger before touching the provider), and **HTML-only mail still
yields a body** (real inboxes are full of it — the first live fetch was one).

The façade is faked here; a live smoke test skips when it isn't reachable.
"""

from __future__ import annotations

import asyncio

import pytest

from central_command.db import repo
from central_command.ingest import feed, ledger
from central_command.integrations import email_facade
from tests.conftest import needs_pg

MSG = {
    "uuid": "feedtest001",
    "conversation_id": "feedthread001",
    "from": "dana@example.com",
    "subject": "TASKS-12 moving again",
    "date": "2026-07-18T10:00:00.000Z",
    "snippet": "the snippet",
    "body_text": "TASKS-12 moves to Sep 1.",
    "body_html": "",
}


@pytest.fixture(autouse=True)
async def clean():
    # NB: conftest's `pg_available()` can't be called here — it runs
    # `asyncio.run`, which inside the running test loop always throws, and that
    # silently skipped the wipe once. Probe by just doing the work.
    try:
        await _wipe()
    except Exception:  # noqa: BLE001 — no PG; the needs_pg tests skip anyway
        yield
        return
    yield
    await _wipe()


async def _wipe():
    conn = await repo._conn()
    try:
        await conn.execute(
            "delete from work_item where message_id like '<gmail-msg-feedtest%'"
        )
    finally:
        await conn.close()


def test_provider_body_falls_back_html_then_snippet():
    assert ledger.provider_body(MSG) == "TASKS-12 moves to Sep 1."
    html_only = {**MSG, "body_text": "", "body_html": "<p>Hello <b>world</b></p>"}
    assert ledger.provider_body(html_only) == "Hello  world"
    bare = {**MSG, "body_text": "", "body_html": ""}
    assert ledger.provider_body(bare) == "the snippet"


@needs_pg
async def test_enrollment_is_idempotent_and_thread_aware():
    first = await ledger.enroll_provider_message(MSG)
    again = await ledger.enroll_provider_message(MSG)
    assert first["enrolled"] and again["duplicate"]

    conn = await repo._conn()
    try:
        row = await conn.fetchrow(
            "select thread_id, feed, source, subject from work_item where message_id = $1",
            first["message_id"],
        )
    finally:
        await conn.close()
    assert row["thread_id"] == "<gmail-thread-feedthread001@central_command.feed>"
    assert (row["feed"], row["source"]) == ("live", "gmail")
    assert row["subject"] == "TASKS-12 moving again"


@needs_pg
async def test_poll_skips_known_mail_without_refetching(monkeypatch):
    fetched = []

    async def fake_list(scope_query):
        return [{"uuid": "feedtest001", "conversation_id": "feedthread001"},
                {"uuid": "feedtest002", "conversation_id": "feedthread002"}]

    async def fake_get(uuid):
        fetched.append(uuid)
        return {**MSG, "uuid": uuid, "conversation_id": "feedthread002"}

    monkeypatch.setattr(email_facade, "list_refs", fake_list)
    monkeypatch.setattr(email_facade, "get_message", fake_get)

    # First sweep: both are new → both fetched and enrolled.
    r1 = await feed.poll_once()
    assert (r1["refs"], r1["new"], r1["enrolled"]) == (2, 2, 2)
    assert sorted(fetched) == ["feedtest001", "feedtest002"]

    # Second sweep: nothing new → the provider is not asked for content at all.
    fetched.clear()
    r2 = await feed.poll_once()
    assert (r2["refs"], r2["new"], r2["enrolled"]) == (2, 0, 0)
    assert fetched == []


def _facade_up() -> bool:
    async def probe() -> bool:
        try:
            await email_facade.list_refs("newer_than:1d")
            return True
        except Exception:  # noqa: BLE001
            return False

    try:
        return asyncio.run(probe())
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.skipif(not _facade_up(), reason="cc-email-facade not reachable")
async def test_live_facade_lists_refs():
    refs = await email_facade.list_refs("newer_than:2d")
    assert all(set(r) >= {"uuid", "conversation_id"} for r in refs)
