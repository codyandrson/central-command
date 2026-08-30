"""Operator graph curation (integrations/neo4j_writer.py).

Two kinds of check, deliberately:

1. A trust-boundary walk that ALWAYS runs — `runtime/` may not import the
   writer. It is an ungated write path, so the rule that keeps it safe is the
   same one that keeps `gateway/` safe, and a rule with no test is a comment.
2. Round-trips against a REAL Neo4j, skipped when bolt is unreachable (same
   posture as tests/test_ledger.py: mocking the graph here would test nothing,
   because every invariant worth pinning is about what Neo4j actually stores).
   They assert the two dual-writes that fail SILENTLY — a node's types living
   in both `labels(n)` and `n.labels`, and an edge's endpoints living in both
   the relationship and its `source_node_uuid`/`target_node_uuid` properties.
   Get either half wrong and the write returns 200 while the cockpit and
   Graphiti disagree about the same object forever.
"""

from __future__ import annotations

import os
import uuid as _uuid
from pathlib import Path

import pytest

from central_command.integrations import neo4j_reader, neo4j_writer

RUNTIME = Path(__file__).resolve().parents[1] / "central_command" / "runtime"
GROUP = "zz-curation-test"


def test_runtime_never_imports_the_writer():
    offenders = [
        path.name for path in RUNTIME.rglob("*.py")
        if "neo4j_writer" in path.read_text(encoding="utf-8")
    ]
    assert offenders == [], (
        f"{offenders} import the operator write path. Agents reach the graph only "
        "through Graphiti's MCP server and a gated graph.add_episode proposal."
    )


@pytest.fixture
async def live_graph():
    # Opt-in, and enforced in conftest's `no_live_graph_writes` rather than
    # here: the graph is the team's memory, not a fixture, and these tests are
    # the only ones in the suite that write to it over bolt.
    if os.getenv("CC_LIVE_GRAPH_TESTS") != "1":
        pytest.skip("set CC_LIVE_GRAPH_TESTS=1 to run graph curation round-trips")
    if not await neo4j_reader.ping():
        pytest.skip("no bolt connection (cc-graph-bolt.service / cc-neo4j)")
    created: list[str] = []
    try:
        yield created
        for node_uuid in created:
            try:
                await neo4j_writer.delete_node(node_uuid)
            except neo4j_writer.WriteError:
                pass  # already deleted by the test (a merge, a delete)
        # Curation writes record operator-provenance Episodic nodes now; a
        # leftover scratch entity has already skewed a live read test once
        # (2026-08-15), so sweep the whole test group, not just the entities.
        await neo4j_writer._write(
            "MATCH (ep:Episodic {group_id: $g}) DETACH DELETE ep", g=GROUP
        )
    finally:
        # CLOSE the driver, don't just drop the reference. Its pool keeps
        # background liveness work running on this test's loop, and those
        # tasks log — which the events bridge turns into `log.*` rows in
        # whatever LATER test happens to be counting events. That is exactly
        # how this file made test_log_bridge and test_agents_page fail, one
        # per run, while passing itself and passing in isolation.
        await neo4j_reader._get_driver().close()
        neo4j_reader._driver = None
        neo4j_reader._driver_loop = None


async def _node(created: list[str], name: str, labels: list[str]) -> str:
    result = await neo4j_writer.create_node(name, labels, "scratch", GROUP)
    created.append(result["uuid"])
    return result["uuid"]


async def test_entity_types_are_written_to_both_places(live_graph):
    uuid = await _node(live_graph, f"ZZ {_uuid.uuid4()}", ["Person"])
    rows = await neo4j_reader._read(
        "MATCH (n:Entity {uuid: $uuid}) RETURN labels(n) AS real, n.labels AS prop",
        uuid=uuid,
    )
    assert set(rows[0]["real"]) == {"Entity", "Person"}
    assert set(rows[0]["prop"]) == {"Entity", "Person"}

    await neo4j_writer.update_node(uuid, labels=["Organization"])
    rows = await neo4j_reader._read(
        "MATCH (n:Entity {uuid: $uuid}) RETURN labels(n) AS real, n.labels AS prop",
        uuid=uuid,
    )
    assert set(rows[0]["real"]) == {"Entity", "Organization"}, "stale label survived"
    assert set(rows[0]["prop"]) == {"Entity", "Organization"}


async def test_curation_writes_stamp_their_embeddings(live_graph):
    """Every embedding a curation write stores carries the same
    embedding_model/embedding_dimensions stamp reembed_graph.py keys its
    resumability and --verify on — and a degraded write (no vector) carries
    no stamp, so the invariant is stamp-iff-vector, not stamp-always."""
    a = await _node(live_graph, f"ZZ A {_uuid.uuid4()}", ["Person"])
    b = await _node(live_graph, f"ZZ B {_uuid.uuid4()}", ["Person"])
    edge = await neo4j_writer.create_edge(a, b, "KNOWS", "ZZ A knows ZZ B")

    rows = await neo4j_reader._read(
        """
        MATCH (n:Entity {uuid: $a})-[e:RELATES_TO {uuid: $e}]->()
        RETURN n.name_embedding IS NOT NULL AS n_vec, n.embedding_model AS n_model,
               n.embedding_dimensions AS n_dims,
               e.fact_embedding IS NOT NULL AS e_vec, e.embedding_model AS e_model,
               e.embedding_dimensions AS e_dims
        """,
        a=a, e=edge["uuid"],
    )
    row = rows[0]
    for kind in ("n", "e"):
        if row[f"{kind}_vec"]:
            assert row[f"{kind}_model"] == neo4j_writer._EMBED_MODEL
            assert row[f"{kind}_dims"] == neo4j_writer._EMBED_DIMENSIONS
        else:  # degraded write (embedder unreachable) — no stamp either
            assert row[f"{kind}_model"] is None and row[f"{kind}_dims"] is None


async def test_repointing_an_edge_moves_the_relationship_and_its_properties(live_graph):
    a = await _node(live_graph, f"ZZ A {_uuid.uuid4()}", ["Person"])
    b = await _node(live_graph, f"ZZ B {_uuid.uuid4()}", ["Person"])
    c = await _node(live_graph, f"ZZ C {_uuid.uuid4()}", ["Person"])
    edge = await neo4j_writer.create_edge(a, b, "KNOWS", "ZZ A knows ZZ B")

    await neo4j_writer.update_edge(edge["uuid"], target_uuid=c)
    rows = await neo4j_reader._read(
        """
        MATCH (s)-[e:RELATES_TO {uuid: $uuid}]->(t)
        RETURN s.uuid AS real_source, t.uuid AS real_target,
               e.source_node_uuid AS prop_source, e.target_node_uuid AS prop_target
        """,
        uuid=edge["uuid"],
    )
    assert len(rows) == 1, "repoint left a duplicate or lost the edge"
    row = rows[0]
    # The cockpit draws from the PROPERTIES; Cypher traverses the relationship.
    # They must agree, or the panel keeps rendering the old picture.
    assert row["real_target"] == row["prop_target"] == c
    assert row["real_source"] == row["prop_source"] == a


async def test_merging_a_duplicate_moves_its_edges_to_the_survivor(live_graph):
    keep = await _node(live_graph, f"ZZ keep {_uuid.uuid4()}", ["Person"])
    drop = await _node(live_graph, f"ZZ drop {_uuid.uuid4()}", ["Person"])
    other = await _node(live_graph, f"ZZ other {_uuid.uuid4()}", ["Person"])
    await neo4j_writer.create_edge(drop, other, "KNOWS", "ZZ drop knows ZZ other")

    result = await neo4j_writer.merge_nodes(keep, drop)
    assert result["edges_moved"] == 1

    rows = await neo4j_reader._read(
        """
        MATCH (s:Entity {uuid: $keep})-[e:RELATES_TO]->(t:Entity {uuid: $other})
        RETURN e.source_node_uuid AS prop_source
        """,
        keep=keep, other=other,
    )
    assert rows and rows[0]["prop_source"] == keep
    assert await neo4j_reader._read(
        "MATCH (n:Entity {uuid: $uuid}) RETURN n", uuid=drop
    ) == [], "the duplicate survived the merge"


async def test_an_unknown_entity_type_is_refused(live_graph):
    with pytest.raises(neo4j_writer.WriteError, match="unknown entity type"):
        await neo4j_writer.create_node("ZZ bad", ["Persson"], "", GROUP)


async def test_operator_writes_carry_real_provenance(live_graph):
    """A hand-made fact records WHO entered it and WHEN — an Episodic node in
    the same shape extraction writes, MENTIONS-linked to the entities and
    listed in the edge's `episodes`. The old `episodes = []` convention read
    as missing data, not as authorship."""
    a = await _node(live_graph, f"ZZ A {_uuid.uuid4()}", ["Person"])
    b = await _node(live_graph, f"ZZ B {_uuid.uuid4()}", ["Person"])
    edge = await neo4j_writer.create_edge(a, b, "KNOWS", "ZZ A knows ZZ B")

    rows = await neo4j_reader._read(
        """
        MATCH ()-[e:RELATES_TO {uuid: $uuid}]->()
        MATCH (ep:Episodic) WHERE ep.uuid IN e.episodes
        RETURN e.episodes AS episodes, ep.source_description AS source,
               ep.content AS content, ep.valid_at AS valid_at,
               ep.entity_edges AS entity_edges,
               count { (ep)-[:MENTIONS]->(:Entity) } AS mentions
        """,
        uuid=edge["uuid"],
    )
    assert rows, "edge carries no resolvable episode"
    row = rows[0]
    assert row["episodes"] == [edge["episode"]]
    assert "operator" in row["source"] and "trust=operator-direct" in row["source"]
    assert row["content"] == "ZZ A knows ZZ B"
    assert row["valid_at"] is not None, "provenance without a timestamp"
    assert row["entity_edges"] == [edge["uuid"]]
    assert row["mentions"] == 2, "episode must mention both endpoints"

    # The node create records its own episode too, feeding the Provenance drawer.
    node_prov = await neo4j_reader.provenance(a)
    assert any(
        "operator" in (ep["source_description"] or "") for ep in node_prov
    ), "node provenance missing the operator episode"


async def test_a_validity_bound_can_be_cleared_to_open_ended(live_graph):
    """The operator can declare either bound OPEN: clear_valid nulls valid_at
    exactly as clear_invalid nulls invalid_at — distinct from omitting the
    field, which leaves it alone."""
    a = await _node(live_graph, f"ZZ A {_uuid.uuid4()}", ["Person"])
    b = await _node(live_graph, f"ZZ B {_uuid.uuid4()}", ["Person"])
    edge = await neo4j_writer.create_edge(
        a, b, "KNOWS", "ZZ A knows ZZ B", valid_at="2020-01-01")

    await neo4j_writer.update_edge(
        edge["uuid"], invalid_at="2024-01-01")
    await neo4j_writer.update_edge(edge["uuid"], clear_valid=True)
    rows = await neo4j_reader._read(
        """
        MATCH ()-[e:RELATES_TO {uuid: $uuid}]->()
        RETURN e.valid_at AS valid_at, e.invalid_at AS invalid_at
        """,
        uuid=edge["uuid"],
    )
    assert rows[0]["valid_at"] is None, "clear_valid left the start bound set"
    assert rows[0]["invalid_at"] is not None, "clearing one bound touched the other"

    # An open-start edge is VALID at any instant before its end — the as-of
    # views must show it, not drop it as unknowable.
    hood = await neo4j_reader.neighborhood(a, at="2015-06-01", limit=10)
    assert any(e["uuid"] == edge["uuid"] for e in hood["edges"]), (
        "open-start edge missing from the as-of view"
    )
    hood_after = await neo4j_reader.neighborhood(a, at="2025-06-01", limit=10)
    assert not any(e["uuid"] == edge["uuid"] for e in hood_after["edges"]), (
        "edge shown past its invalid_at"
    )
