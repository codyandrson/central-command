"""Wire-shape guards for the FastAPI endpoints behind cc-sources.ts.

The Sources panel hand-declares its interfaces (web/src/features/sources/
types.ts) over untyped REST, so a key the gateway stops sending is invisible
to `tsc` AND to every frontend test — the false-coverage class CLAUDE.md
documents. Same idiom as test_cc_routes_wire.py: monkeypatch the repo, call
the route function directly, assert the exact keys the TS side reads, and
name the surface that goes blank if one vanishes.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from central_command.api import routes
from central_command.config import settings
from central_command.db import repo
from tests.conftest import needs_pg

NOW = datetime(2026, 8, 30, 9, 0, tzinfo=timezone.utc)

# Every key the panel's `Source` interface declares. One set for both kinds
# on purpose: the email row is synthesized, and a divergence here is what
# would force the panel into two types.
SOURCE_KEYS = {
    "id", "kind", "name", "read_only", "enabled", "config", "cursor",
    "last_polled_at", "overview", "feed_overview",
}


async def _async(value):
    return value


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)


@pytest.fixture()
def stub(monkeypatch):
    """A filesystem source, an email ledger census, and the mail-poll schedule."""
    monkeypatch.setattr(settings, "feed_enabled", True)
    monkeypatch.setattr(settings, "feed_query", "in:inbox newer_than:1d")
    monkeypatch.setattr(settings, "feed_poll_seconds", 60.0)
    monkeypatch.setattr(settings, "backlog_query", "in:inbox")
    monkeypatch.setattr(settings, "backlog_window_days", 30)
    monkeypatch.setattr(repo, "list_sources", lambda: _async([{
        "id": "docs-drive", "kind": "filesystem", "name": "Docs drive",
        "config": {"root": "/srv/docs"}, "enabled": True,
        "cursor": {"last_walk_at": "2026-08-30T08:00:00+00:00"},
        "created_at": NOW, "updated_at": NOW,
    }]))
    monkeypatch.setattr(repo, "catalog_overview", lambda sid: _async({
        "documents": 3, "rescinded": 1, "versions": 5, "locations": 4, "missing": 2,
        "unenrolled": 2,
    }))
    monkeypatch.setattr(repo, "count_work_items_by_state", lambda kind: _async({
        "UNPROCESSED": 2, "CLAIMED": 1, "FOLD_PENDING": 1, "DISMISS_PENDING": 1,
        "PROCESSED": 7, "FOLDED": 3, "FAILED": 2,
    }))
    monkeypatch.setattr(repo, "get_feed_state", lambda key: _async({"cursor": "2026-08-01"}))
    monkeypatch.setattr(repo, "list_heartbeat_schedules", lambda **k: _async([
        {"id": "mail-poll", "action_kind": "feed.poll", "last_fired_at": NOW},
    ]))


async def test_sources_list_carries_every_key_the_panel_reads(stub):
    """SourcesView renders both kinds through ONE `Source` interface — a
    missing key blanks a list row's counts or the detail pane entirely."""
    out = await routes.list_sources()

    assert set(out.keys()) == {"sources"}
    for row in out["sources"]:
        assert set(row) >= SOURCE_KEYS

    email, fs = out["sources"][0], out["sources"][1]

    # The email row: read-only, config from settings, counts from the ledger.
    assert email["id"] == "email-feed" and email["kind"] == "email"
    assert email["read_only"] is True and email["enabled"] is True
    assert email["config"] == {
        "query": "in:inbox newer_than:1d", "poll_seconds": 60.0,
        "backlog_query": "in:inbox", "backlog_window_days": 30,
    }
    assert email["cursor"] == {"cursor": "2026-08-01"}
    assert email["last_polled_at"] == NOW
    assert email["overview"] is None
    # enrolled = all states; pending = the four non-terminal ones; processed =
    # PROCESSED+FOLDED; failed = FAILED. The list row's count strip.
    assert email["feed_overview"] == {
        "enrolled": 17, "processed": 10, "pending": 5, "failed": 2,
    }

    # The filesystem row keeps its slice-1 shape and gains the two new keys.
    assert fs["kind"] == "filesystem" and fs["read_only"] is False
    assert fs["last_polled_at"] == "2026-08-30T08:00:00+00:00"
    assert fs["feed_overview"] is None
    assert set(fs["overview"]) == {
        "documents", "rescinded", "versions", "locations", "missing", "unenrolled",
    }


async def test_source_with_no_cursor_still_reports_last_polled_at(monkeypatch, stub):
    """`cursor` is null until the first walk — the row must still render."""
    monkeypatch.setattr(repo, "list_sources", lambda: _async([{
        "id": "docs-drive", "kind": "filesystem", "name": "Docs drive",
        "config": {"root": "/srv/docs"}, "enabled": False, "cursor": None,
        "created_at": NOW, "updated_at": NOW,
    }]))

    fs = (await routes.list_sources())["sources"][1]

    assert fs["last_polled_at"] is None


async def test_enabling_the_email_feed_is_refused_with_the_env_lever(stub):
    """The email row is synthesized from `.env`, so the toggle must not
    silently no-op — the panel shows this message."""
    with pytest.raises(HTTPException) as e:
        await routes.set_source_enabled("email-feed", routes.SourceEnableIn(enabled=False))
    assert e.value.status_code == 409
    assert "CC_FEED_ENABLED" in e.value.detail


async def test_enabling_an_unknown_source_is_404(monkeypatch):
    monkeypatch.setattr(repo, "set_source_enabled", lambda i, e: _async(False))
    with pytest.raises(HTTPException) as e:
        await routes.set_source_enabled("nope", routes.SourceEnableIn(enabled=True))
    assert e.value.status_code == 404


@needs_pg
async def test_count_work_items_by_state_groups_by_state():
    """The SQL behind the email row's counts — mocked repos pin the shape,
    this pins the query."""
    marker = uuid.uuid4().hex[:8]
    conn = await repo._conn()
    try:
        for state, n in (("UNPROCESSED", 2), ("PROCESSED", 1)):
            for i in range(n):
                await conn.execute(
                    "insert into work_item (id, message_id, feed, source, state, payload, kind)"
                    " values ($1, $1, 'live', 'fixture', $2, '{}'::jsonb, $3)",
                    f"wire-{marker}-{state}-{i}", state, f"email-{marker}",
                )
        # Scoped to this test's rows: the dev DB is shared, so a global count
        # would be someone else's queue depth.
        rows = await conn.fetch(
            "select state, count(*) as n from work_item where kind = $1 group by state",
            f"email-{marker}",
        )
        assert {r["state"]: r["n"] for r in rows} == {"UNPROCESSED": 2, "PROCESSED": 1}
        assert await repo.count_work_items_by_state(f"email-{marker}") == {
            "UNPROCESSED": 2, "PROCESSED": 1,
        }
    finally:
        await conn.execute(
            "delete from work_item where kind = $1", f"email-{marker}"
        )
        await conn.close()
