"""`episode_delta`'s two invalidation guards, against a scripted `_read`
(the live behaviour behind them is tests/test_graph_delta_live.py, opt-in).

2026-09-15: 89 of 90 "invalidated" entries on the Verify tab were either an
edge the episode itself created with an end date (Graphiti expires a new
edge at birth whenever the extractor supplied `invalid_at`) or a neighbouring
episode's retirement caught by a fixed 30-minute window while a backlog
drained every 2-8 minutes."""

from __future__ import annotations

import pytest

from central_command.integrations import neo4j_reader

EP = "ep-1"
T0 = "2026-09-15T19:41:15+00:00"


def _script(monkeypatch, *, next_episode_at=None, window_rows=()):
    seen: list[dict] = []

    async def fake_read(query, **params):
        seen.append({"query": query, **params})
        if "MENTIONS" in query:
            return []
        if "$uuid IN r.episodes" in query:
            return []
        if "RETURN e.created_at AS created_at, e.group_id" in query:
            return [{"created_at": T0, "group_id": "g"}]
        if "min(n.created_at)" in query:
            return [{"next_created_at": next_episode_at}]
        if "$window_end" in query:
            return list(window_rows)
        raise AssertionError(f"unexpected query: {query}")

    monkeypatch.setattr(neo4j_reader, "_read", fake_read)
    return seen


@pytest.mark.asyncio
async def test_invalidation_queries_only_admit_facts_that_predate_the_episode(monkeypatch):
    seen = _script(monkeypatch)
    await neo4j_reader.episode_delta(EP)
    guarded = [q for q in seen if "r.expired_at IS NOT NULL" in q["query"]]
    assert len(guarded) == 2, "both attribution methods carry the guard"
    assert all("r.created_at < " in q["query"] for q in guarded)
    assert all("r.created_at AS created_at" in q["query"] for q in guarded)


@pytest.mark.asyncio
async def test_window_closes_when_the_next_episode_begins(monkeypatch):
    seen = _script(monkeypatch, next_episode_at="2026-09-15T19:51:24+00:00")
    await neo4j_reader.episode_delta(EP, window_minutes=30)
    window = next(q for q in seen if "$window_end" in q["query"])
    assert window["window_end"] == "2026-09-15T19:51:24+00:00"


@pytest.mark.asyncio
async def test_window_falls_back_to_the_cap_when_nothing_follows(monkeypatch):
    seen = _script(monkeypatch, next_episode_at=None)
    await neo4j_reader.episode_delta(EP, window_minutes=30)
    window = next(q for q in seen if "$window_end" in q["query"])
    assert window["window_end"] == "2026-09-15T20:11:15+00:00"


@pytest.mark.asyncio
async def test_invalidated_entries_carry_when_the_fact_was_first_known(monkeypatch):
    row = {"uuid": "e0", "name": "LEADS", "fact": "Bo leads", "source": "Bo",
           "target": "probe", "valid_at": None, "invalid_at": None,
           "expired_at": "2026-09-15T19:45:00+00:00",
           "created_at": "2026-09-01T00:00:00+00:00"}
    _script(monkeypatch, window_rows=[row])
    delta = await neo4j_reader.episode_delta(EP)
    assert delta["invalidated"] == [{**row, "attributed_by": "window"}]
