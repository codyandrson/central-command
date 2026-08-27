"""Task 1's empirical question, answered live: when Graphiti invalidates an
edge during an episode's ingestion, does that episode's uuid land in the
edge's `episodes` property, or is `expired_at`-in-window the only signal?

Opt-in only (`CC_LIVE_GRAPH_TESTS=1`), same posture as test_graph_curation.py:
mocking the graph here would test nothing, the whole point is what Neo4j and
the queued local extractor actually do. Runs in a dedicated scratch group
(`gvtest_attribution`, not in `graph_read_groups`) and cleans up in a finally
block regardless of outcome.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid as _uuid

import pytest

from central_command.integrations import graphiti, neo4j_reader, neo4j_writer

GROUP = "gvtest_attribution"
POLL_SECONDS = 15
POLL_TIMEOUT_SECONDS = 6 * 60


async def _poll_for_episode(marker: str, want_edge: bool) -> tuple[dict, dict] | None:
    """Poll episode_by_marker → episode_delta until the episode exists (and,
    if want_edge, has produced at least one edge). Returns (episode, delta) or
    None on timeout."""
    deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        episode = await neo4j_reader.episode_by_marker(marker, GROUP)
        if episode:
            delta = await neo4j_reader.episode_delta(episode["uuid"])
            if not want_edge or delta["edges"]:
                return episode, delta
        await asyncio.sleep(POLL_SECONDS)
    return None


async def _poll_for_invalidation(marker: str) -> tuple[dict, dict] | None:
    """Like _poll_for_episode, but succeeds once the episode exists AND
    (some edge in the group has expired_at set OR the timeout elapses) — the
    plan allows a full timeout as a valid (if disappointing) outcome here."""
    deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
    episode = None
    while time.monotonic() < deadline:
        if episode is None:
            episode = await neo4j_reader.episode_by_marker(marker, GROUP)
        if episode:
            delta = await neo4j_reader.episode_delta(episode["uuid"])
            if delta["invalidated"]:
                return episode, delta
        await asyncio.sleep(POLL_SECONDS)
    if episode:
        delta = await neo4j_reader.episode_delta(episode["uuid"])
        return episode, delta
    return None


async def test_invalidation_attribution_live():
    if os.getenv("CC_LIVE_GRAPH_TESTS") != "1":
        pytest.skip("set CC_LIVE_GRAPH_TESTS=1 to run the live attribution probe")
    if not await neo4j_reader.ping():
        pytest.skip("no bolt connection (cc-graph-bolt.service / cc-neo4j)")

    # conftest's `no_live_graph_writes` guard lets `add_memory` through under
    # the same CC_LIVE_GRAPH_TESTS=1 opt-in as the bolt-write guard (this test
    # is why the MCP side gained the opt-in, 2026-08-19).
    marker1 = f"attribution-probe-{_uuid.uuid4()}"
    marker2 = f"attribution-probe-{_uuid.uuid4()}"
    try:
        await graphiti.add_episode(
            name="attribution probe 1",
            episode_body=(
                "Marisol Vexley is the chief executive of Scratchcorp "
                "Attribution Test Ltd."
            ),
            source_description=f"gvtest | marker={marker1}",
            group_id=GROUP,
        )
        result1 = await _poll_for_episode(marker1, want_edge=True)
        assert result1 is not None, (
            f"episode 1 (marker={marker1}) never appeared with an edge within "
            f"{POLL_TIMEOUT_SECONDS}s — that absence is itself the silent-drop "
            "finding this test exists to surface."
        )
        episode1, delta1 = result1
        print(f"\n=== episode 1: {episode1['uuid']} — {len(delta1['edges'])} edge(s) ===")

        await graphiti.add_episode(
            name="attribution probe 2",
            episode_body=(
                "Barnaby Quilcott is now the chief executive of Scratchcorp "
                "Attribution Test Ltd, replacing Marisol Vexley."
            ),
            source_description=f"gvtest | marker={marker2}",
            group_id=GROUP,
        )
        result2 = await _poll_for_invalidation(marker2)
        assert result2 is not None, (
            f"episode 2 (marker={marker2}) never appeared within "
            f"{POLL_TIMEOUT_SECONDS}s."
        )
        episode2, delta2 = result2

        print("\n" + "=" * 70)
        print("EMPIRICAL FINDING — invalidation attribution")
        print("=" * 70)
        print(f"episode 2 uuid: {episode2['uuid']}")
        print(f"invalidated edge count: {len(delta2['invalidated'])}")
        if not delta2["invalidated"]:
            print(
                "NO invalidation observed within the timeout — episode 2 may "
                "not have finished ingesting, or the contradiction was not "
                "recognised as one. See episode2 delta below for what DID land."
            )
            print(f"episode 2 delta: {delta2}")
        for edge in delta2["invalidated"]:
            print(
                f"  edge {edge['uuid']} fact={edge['fact']!r} "
                f"attributed_by={edge['attributed_by']} "
                f"expired_at={edge['expired_at']}"
            )
        print("=" * 70)
    finally:
        await neo4j_writer._write(
            "MATCH (n) WHERE n.group_id = $g DETACH DELETE n", g=GROUP
        )
        remaining = await neo4j_reader._read(
            "MATCH (n) WHERE n.group_id = $g RETURN count(n) AS c", g=GROUP
        )
        assert remaining[0]["c"] == 0, (
            f"scratch group {GROUP!r} left {remaining[0]['c']} node(s) behind"
        )
