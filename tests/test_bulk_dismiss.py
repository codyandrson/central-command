"""Bulk dismissal, operator path (slice 1, 2026-08-17 design:
docs/superpowers/specs/2026-08-17-bulk-dismissal-design.md).

Real Postgres, like test_ledger.py/test_dismissal.py — the state-scoping
invariant lives in the SQL (`where ... and state = 'UNPROCESSED'`), so mocking
it would test nothing. The façade is always monkeypatched: these tests never
call the real n8n email façade.
"""

from __future__ import annotations

import json

import pytest

from central_command.api import routes
from central_command.db import repo
from central_command.events import log as events_log
from central_command.ingest import bulk_dismiss
from central_command.ingest.ledger import provider_message_id
from central_command.integrations import email_facade
from tests.conftest import needs_pg

pytestmark = needs_pg

_UNPROCESSED_UUID = "bd-unproc-1"
_CLAIMED_UUID = "bd-claimed-1"
_PROCESSED_UUID = "bd-processed-1"
_UNKNOWN_UUID = "bd-not-enrolled-1"  # matched by Gmail, never enrolled in the ledger


@pytest.fixture(autouse=True)
async def clean():
    try:
        await _wipe()
    except Exception:  # noqa: BLE001
        yield
        return
    yield
    await _wipe()


async def _wipe():
    conn = await repo._conn()
    try:
        await conn.execute("delete from work_item where message_id like '<gmail-msg-bd-%'")
    finally:
        await conn.close()


async def _seed():
    """One row per state that matters to the state-scoping guard."""
    for uuid in (_UNPROCESSED_UUID, _CLAIMED_UUID, _PROCESSED_UUID):
        await repo.enroll_work_item(
            "wi_" + uuid,
            provider_message_id(uuid),
            {"provider_uuid": uuid, "deferred_fetch": True},
            thread_id=f"<gmail-thread-{uuid}@central_command.feed>",
            feed="backlog",
            source="gmail",
        )
    conn = await repo._conn()
    try:
        await conn.execute(
            "update work_item set state = 'CLAIMED' where message_id = $1",
            provider_message_id(_CLAIMED_UUID),
        )
        await conn.execute(
            "update work_item set state = 'PROCESSED', terminal_at = now() where message_id = $1",
            provider_message_id(_PROCESSED_UUID),
        )
    finally:
        await conn.close()


async def _row(uuid: str) -> dict:
    conn = await repo._conn()
    try:
        r = await conn.fetchrow(
            "select state, payload from work_item where message_id = $1",
            provider_message_id(uuid),
        )
        d = dict(r)
        if isinstance(d["payload"], str):
            d["payload"] = json.loads(d["payload"])
        return d
    finally:
        await conn.close()


def _patch_facade(monkeypatch, uuids: list[str]):
    async def fake_list_refs(query):
        return [{"uuid": u, "conversation_id": f"conv-{u}"} for u in uuids]

    async def fake_get_message(uuid):
        return {"uuid": uuid, "from": f"{uuid}@example.com", "subject": "s", "date": "2026-08-17"}

    monkeypatch.setattr(email_facade, "list_refs", fake_list_refs)
    monkeypatch.setattr(email_facade, "get_message", fake_get_message)


async def test_only_the_unprocessed_row_moves(monkeypatch):
    await _seed()
    _patch_facade(
        monkeypatch, [_UNPROCESSED_UUID, _CLAIMED_UUID, _PROCESSED_UUID, _UNKNOWN_UUID]
    )

    resolved = await bulk_dismiss.resolve_query("q")
    assert resolved["provider_matches"] == 4
    assert resolved["unprocessed"] == 1
    assert resolved["message_ids"] == [provider_message_id(_UNPROCESSED_UUID)]

    prev = await bulk_dismiss.preview("q")
    result = await bulk_dismiss.execute("q", prev["match_digest"])
    assert result["dismissed"] == 1

    moved = await _row(_UNPROCESSED_UUID)
    assert moved["state"] == "PROCESSED"
    assert "operator bulk dismissal: q" in moved["payload"]["dismissal_rationale"]

    assert (await _row(_CLAIMED_UUID))["state"] == "CLAIMED"
    assert (await _row(_PROCESSED_UUID))["state"] == "PROCESSED"


async def test_digest_drift_refuses_execute(monkeypatch):
    await _seed()
    _patch_facade(monkeypatch, [_UNPROCESSED_UUID])
    prev = await bulk_dismiss.preview("q")

    with pytest.raises(ValueError, match="changed since preview"):
        await bulk_dismiss.execute("q", "stale-digest-deadbeef")

    # Refused — the row never moved.
    assert (await _row(_UNPROCESSED_UUID))["state"] == "UNPROCESSED"

    # The real (matching) digest still works.
    result = await bulk_dismiss.execute("q", prev["match_digest"])
    assert result["dismissed"] == 1


async def test_event_is_emitted_after_the_write(monkeypatch):
    await _seed()
    _patch_facade(monkeypatch, [_UNPROCESSED_UUID])
    prev = await bulk_dismiss.preview("q")

    async with events_log.subscribe() as q:
        await bulk_dismiss.execute("q", prev["match_digest"])
        # The row is already PROCESSED by the time we look — the write landed
        # before the event went out, exactly as CLAUDE.md's emit-after-write
        # exception for a completed, non-authorising operator action requires.
        assert (await _row(_UNPROCESSED_UUID))["state"] == "PROCESSED"
        event = await q.get()

    assert event["kind"] == "work.bulk_dismissed"
    assert event["actor"] == "operator"
    assert event["payload"]["dismissed"] == 1
    assert event["payload"]["query"] == "q"


async def test_preview_route_carries_samples_and_digest_never_message_ids(monkeypatch):
    """Untyped-RPC bite mark (CLAUDE.md): a frontend test building its own
    payload proves nothing about the real wire shape, so this drives the
    actual route handler — `message_ids` is stripped there deliberately
    (routes.py: "no need to ship 2,500 ids to the UI")."""
    await _seed()
    _patch_facade(monkeypatch, [_UNPROCESSED_UUID])

    out = await routes.bulk_dismiss_preview(routes.BulkDismissQueryIn(query="q"))

    assert "message_ids" not in out
    assert isinstance(out["match_digest"], str) and out["match_digest"]
    assert out["samples"] == [
        {"from": f"{_UNPROCESSED_UUID}@example.com", "subject": "s", "date": "2026-08-17"}
    ]
    assert out["provider_matches"] == 1
    assert out["unprocessed"] == 1


async def test_unenrolled_uuids_are_ignored(monkeypatch):
    _patch_facade(monkeypatch, [_UNKNOWN_UUID])
    resolved = await bulk_dismiss.resolve_query("q")
    assert resolved["provider_matches"] == 1
    assert resolved["unprocessed"] == 0
    assert resolved["message_ids"] == []
