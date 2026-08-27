"""Backlog sweep tests (M13).

The properties that matter: the sweep **walks backwards in windows** with a
durable, resumable cursor; an over-cap window is **bisected, never truncated**;
enrollment is **refs-only** (no content fetched at sweep time) and the
dispatcher **hydrates at claim time**; and the pick order is **newest-first**
(D16 revised: live first, then most-recent backlog, working backwards).
"""

from __future__ import annotations

import pytest

from central_command.config import settings
from central_command.db import repo
from central_command.ingest import dispatcher, feed, ledger
from central_command.integrations import email_facade
from central_command.runtime.spike_model import make_spike_model
from tests.conftest import needs_pg

pytestmark = needs_pg


@pytest.fixture(autouse=True)
async def clean(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "executor_mode", "dry_run")
    monkeypatch.setattr(settings, "dispatch_approval_limit", 10_000)
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
        await conn.execute(
            "delete from work_item where message_id like '<gmail-msg-swp%'"
        )
        await conn.execute("delete from feed_state where key = 'backlog_sweep'")
    finally:
        await conn.close()


def _sweeper() -> feed.BacklogSweeper:
    return feed.BacklogSweeper()


def _cutoff_days_back(days: int) -> str:
    """An ISO cutoff just under `days` back, so window math stays exact
    regardless of the moment the sweeper stamps 'now'."""
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc) - timedelta(days=days) + timedelta(hours=1)).isoformat()


async def test_sweep_walks_backwards_and_persists_a_resumable_cursor(monkeypatch):
    monkeypatch.setattr(settings, "backlog_window_days", 2)
    monkeypatch.setattr(settings, "backlog_cutoff_date", _cutoff_days_back(6))
    queries: list[str] = []

    async def fake_list(scope_query):
        queries.append(scope_query)
        n = len(queries)
        return [{"uuid": f"swp{n}", "conversation_id": f"swpthread{n}"}]

    monkeypatch.setattr(email_facade, "list_refs", fake_list)

    s = _sweeper()
    await s.start()
    await s._task  # drive to completion deterministically

    # 6-day horizon / 2-day windows = 3 windows, walking backwards.
    assert len(queries) == 3
    state = await repo.get_feed_state("backlog_sweep")
    assert state["done"] is True
    assert state["windows"] == 3 and state["enrolled_total"] == 3
    assert state["before"] == state["horizon"]
    assert state["cutoff_date"] == settings.backlog_cutoff_date

    # Each query's window sits strictly before the previous one's.
    import re

    bounds = [tuple(map(int, re.search(r"after:(\d+) before:(\d+)", q).groups()))
              for q in queries]
    assert all(b1[1] > b2[1] for b1, b2 in zip(bounds, bounds[1:]))


async def test_sweep_resumes_from_the_cursor_after_an_error(monkeypatch):
    monkeypatch.setattr(settings, "backlog_window_days", 2)
    monkeypatch.setattr(settings, "backlog_cutoff_date", _cutoff_days_back(8))
    calls: list[str] = []

    async def flaky_list(scope_query):
        calls.append(scope_query)
        if len(calls) >= 2:
            # A PERSISTENT failure (provider down) stops the sweep once
            # bisection bottoms out at 1 day; the cursor keeps the one
            # finished window. (A single flake no longer parks — it gets
            # retried at a halved window, since an over-full window can
            # surface as the same generic error.)
            raise email_facade.EmailFacadeError("connection refused")
        return []

    monkeypatch.setattr(email_facade, "list_refs", flaky_list)

    s = _sweeper()
    await s.start()
    await s._task

    state = await repo.get_feed_state("backlog_sweep")
    assert state["done"] is False and state["windows"] == 1

    # A fresh sweeper resumes where the cursor left off — not from the top.
    calls.clear()

    async def fast_list(scope_query):
        calls.append(scope_query)
        return []

    monkeypatch.setattr(email_facade, "list_refs", fast_list)
    s2 = _sweeper()
    await s2.start()
    await s2._task
    final = await repo.get_feed_state("backlog_sweep")
    assert final["done"] is True
    # 8 days total, 2-day windows: 1 done before the stop, 3 after.
    assert len(calls) == 3


async def test_over_cap_window_is_bisected(monkeypatch):
    monkeypatch.setattr(settings, "backlog_window_days", 4)
    monkeypatch.setattr(settings, "backlog_cutoff_date", _cutoff_days_back(4))
    attempts: list[str] = []

    async def fake_list(scope_query):
        attempts.append(scope_query)
        if len(attempts) == 1:
            raise email_facade.EmailFacadeError(
                "lib-email-provider: list pagination cap hit (5 pages fetched)"
            )
        return []

    monkeypatch.setattr(email_facade, "list_refs", fake_list)
    s = _sweeper()
    await s.start()
    await s._task

    # First attempt failed on the cap; the retry used a halved window.
    assert len(attempts) >= 2
    state = await repo.get_feed_state("backlog_sweep")
    assert state["done"] is True


async def test_generic_provider_error_is_bisected_too(monkeypatch):
    """An over-full window surfaces as a generic n8n 500 ('Error in workflow'),
    not the cap message — found live 2026-08-12 on a 2,542-ref month. Any list
    failure bisects while the window can still shrink."""
    monkeypatch.setattr(settings, "backlog_window_days", 4)
    monkeypatch.setattr(settings, "backlog_cutoff_date", _cutoff_days_back(4))
    attempts: list[str] = []

    async def fake_list(scope_query):
        attempts.append(scope_query)
        if len(attempts) == 1:
            raise email_facade.EmailFacadeError(
                'email façade list failed: HTTP 500 {"message":"Error in workflow"}'
            )
        return []

    monkeypatch.setattr(email_facade, "list_refs", fake_list)
    s = _sweeper()
    await s.start()
    await s._task

    assert len(attempts) >= 2
    state = await repo.get_feed_state("backlog_sweep")
    assert state["done"] is True


async def test_persistent_failure_parks_at_one_day_instead_of_looping(monkeypatch):
    """A real outage must still terminate: bisection bottoms out at a 1-day
    window, then the sweep parks with the error and a restart resumes."""
    monkeypatch.setattr(settings, "backlog_window_days", 4)
    monkeypatch.setattr(settings, "backlog_cutoff_date", _cutoff_days_back(4))
    attempts: list[str] = []

    async def always_down(scope_query):
        attempts.append(scope_query)
        raise email_facade.EmailFacadeError("email façade list failed: HTTP 502")

    monkeypatch.setattr(email_facade, "list_refs", always_down)
    s = _sweeper()
    await s.start()
    await s._task

    # 4 -> 2 -> 1, then one failing attempt at 1 day parks the sweep.
    assert 2 <= len(attempts) <= 4
    assert s.last_error and "502" in s.last_error
    state = await repo.get_feed_state("backlog_sweep")
    assert state["done"] is False, "parked mid-sweep, resumable — never marked done"


async def test_refs_enroll_without_content_and_hydrate_at_claim(monkeypatch):
    fetched: list[str] = []

    async def fake_get(uuid):
        fetched.append(uuid)
        return {
            "uuid": uuid, "conversation_id": "swpthreadhy",
            "from": "dana@example.com", "subject": "DEMO-1 slipped",
            "date": "2026-07-01T09:00:00.000Z", "snippet": "s",
            "body_text": "DEMO-1 moved to Aug 3.", "body_html": "",
        }

    monkeypatch.setattr(email_facade, "get_message", fake_get)

    await ledger.enroll_provider_ref({"uuid": "swphydrate", "conversation_id": "swpthreadhy"})
    assert fetched == []  # sweep time: no content fetched

    out = await dispatcher.dispatch_once(model=make_spike_model())
    assert out["ok"] is True
    assert fetched == ["swphydrate"]  # claim time: exactly one fetch

    conn = await repo._conn()
    try:
        row = await conn.fetchrow(
            "select subject, payload, received_at, state from work_item"
            " where message_id = $1",
            ledger.provider_message_id("swphydrate"),
        )
    finally:
        await conn.close()
    import json as _json

    payload = row["payload"] if isinstance(row["payload"], dict) else _json.loads(row["payload"])
    assert row["state"] == "PROCESSED"
    assert row["subject"] == "DEMO-1 slipped"          # filled at hydrate
    assert "DEMO-1 moved to Aug 3." in payload["text"]  # reviewer's source pane works
    assert row["received_at"] is not None


async def test_sweep_refuses_to_start_without_a_cutoff(monkeypatch):
    monkeypatch.setattr(settings, "backlog_cutoff_date", "")
    with pytest.raises(ValueError):
        await _sweeper().start()


async def test_pick_order_is_live_first_then_newest(monkeypatch):
    # Backlog refs enrolled in sweep order (newest window first) …
    await ledger.enroll_provider_ref({"uuid": "swpnew", "conversation_id": "swpt1"})
    await ledger.enroll_provider_ref({"uuid": "swpold", "conversation_id": "swpt2"})
    # … plus one live arrival.
    await ledger.enroll_provider_message({
        "uuid": "swplive", "conversation_id": "swpt3", "from": "x@example.com",
        "subject": "live one", "date": "2026-07-19T00:00:00.000Z",
        "snippet": "s", "body_text": "body", "body_html": "",
    })

    order = []
    for _ in range(3):
        item = await repo.claim_next_work_item("sess_order")
        order.append(item["message_id"])
        await repo.finish_work_item(item["id"], "PROCESSED")

    assert order == [
        ledger.provider_message_id("swplive"),  # live first
        ledger.provider_message_id("swpnew"),   # then newest backlog (sweep order)
        ledger.provider_message_id("swpold"),
    ]
