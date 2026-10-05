"""Hand-made graph writes have the shape extraction writes (design record
2026-10-04, D8; v2.61.0).

graphiti-core has two write paths that disagree, and the BULK path
(`add_nodes_and_edges_bulk`, what `add_episode` uses) is the shape every
extracted object in the graph has. `neo4j_writer` keeps its own Cypher (there
is no upstream update, merge or move), so this test holds the property sets it
writes for a hand-made entity, fact, episode and MENTIONS edge against the
sets the INSTALLED package's bulk path writes — derived from the package at
test time (the dict builders in `utils/bulk_utils.py` and the Neo4j bulk
queries), never hard-coded, so an upgrade that adds, renames or drops a
property fails here with a readable diff instead of silently forking the
graph's shape. It also holds the vector storage to the bulk path's procedure
(`db.create.setNodeVectorProperty` / `setRelationshipVectorProperty`).

Deliberate differences go in `ALLOWED` with the reason. There are none today.
"""

from __future__ import annotations

import ast
import importlib.util
import pathlib
import re

import pytest

from central_command.integrations import graphiti_client, neo4j_writer

# kind -> {"extra": {prop: why}, "missing": {prop: why}}
ALLOWED: dict[str, dict[str, dict[str, str]]] = {
    "entity": {"extra": {}, "missing": {}},
    "fact": {"extra": {}, "missing": {}},
    "episode": {"extra": {}, "missing": {}},
    "mentions": {"extra": {}, "missing": {}},
}


def _installed(path: str) -> str:
    spec = importlib.util.find_spec("graphiti_core")
    return (pathlib.Path(spec.submodule_search_locations[0]) / path).read_text()


def _dict_keys(fn_src: str, target: str) -> set[str]:
    tree = ast.parse(fn_src)
    for node in ast.walk(tree):
        if (isinstance(node, (ast.Assign, ast.AnnAssign))
                and isinstance(getattr(node, "target", None) or node.targets[0], ast.Name)
                and (getattr(node, "target", None) or node.targets[0]).id == target
                and isinstance(node.value, ast.Dict)):
            return {k.value for k in node.value.keys if isinstance(k, ast.Constant)}
    raise AssertionError(f"no dict literal assigned to {target!r} — the bulk path changed shape")


def _upstream() -> dict[str, set[str]]:
    graphiti_client._prepare_environment()
    from graphiti_core.driver.driver import GraphProvider
    from graphiti_core.models.edges.edge_db_queries import (
        get_entity_edge_save_bulk_query,
        get_episodic_edge_save_bulk_query,
    )
    from graphiti_core.models.nodes.node_db_queries import (
        get_entity_node_save_bulk_query,
        get_episode_node_save_bulk_query,
    )

    bulk = _installed("utils/bulk_utils.py")
    tree = ast.parse(bulk)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "add_nodes_and_edges_bulk_tx")
    fn_src = ast.get_source_segment(bulk, fn)

    episode_q = get_episode_node_save_bulk_query(GraphProvider.NEO4J)
    episode = set(re.findall(r"(\w+):\s*episode\.\w+", re.search(r"SET n = \{(.*?)\}", episode_q, re.S).group(1)))
    mentions_q = get_episodic_edge_save_bulk_query(GraphProvider.NEO4J)
    mentions = set(re.findall(r"e\.(\w+)\s*=\s*edge\.", mentions_q))
    if "MENTIONS {uuid: edge.uuid}" in mentions_q:
        mentions.add("uuid")
    node_q = get_entity_node_save_bulk_query(GraphProvider.NEO4J, [])
    edge_q = get_entity_edge_save_bulk_query(GraphProvider.NEO4J)
    assert 'setNodeVectorProperty(n, "name_embedding"' in node_q
    assert 'setRelationshipVectorProperty(e, "fact_embedding"' in edge_q
    assert "SET n = node" in node_q and "SET e = edge" in edge_q
    return {
        "entity": _dict_keys(fn_src, "entity_data"),
        "fact": _dict_keys(fn_src, "edge_data"),
        "episode": episode,
        "mentions": mentions,
    }


# --- what OUR writer writes ---------------------------------------------------------

_KINDS = {
    r"CREATE \((\w+):Entity\b": "entity",
    r"CREATE \((\w+):Episodic\)": "episode",
    r"CREATE \(\w+\)-\[(\w+):MENTIONS\]": "mentions",
    r"CREATE \(\w+\)-\[(\w+):RELATES_TO\]": "fact",
}


def _written(queries: list[str]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {k: set() for k in ("entity", "fact", "episode", "mentions")}
    for q in queries:
        for pattern, kind in _KINDS.items():
            for var in re.findall(pattern, q):
                out[kind] |= set(re.findall(rf"(?<![\w.]){var}\.(\w+)\s*=(?!=)", q))
                out[kind] |= set(re.findall(
                    rf"set(?:Node|Relationship)VectorProperty\({var},\s*'(\w+)'", q))
    return out


@pytest.fixture
def recorded(monkeypatch):
    queries: list[str] = []

    async def fake_write(query, **params):
        queries.append(query)
        if "RETURN a.group_id AS group_id" in query:
            return [{"group_id": "g"}]
        return []

    async def fake_embed(text):
        return [0.0] * neo4j_writer._EMBED_DIMENSIONS

    monkeypatch.setattr(neo4j_writer, "_write", fake_write)
    monkeypatch.setattr(neo4j_writer, "embed", fake_embed)
    return queries


async def test_hand_made_writes_have_the_installed_bulk_shape(recorded):
    await neo4j_writer.create_node("Jane Doe", ["Person"], "a person", "g")
    await neo4j_writer.create_edge("a", "b", "WORKS_AT", "Jane Doe works at Example Co",
                                   valid_at=None)
    ours, upstream = _written(recorded), _upstream()
    problems = []
    for kind in ("entity", "fact", "episode", "mentions"):
        extra = ours[kind] - upstream[kind] - set(ALLOWED[kind]["extra"])
        missing = upstream[kind] - ours[kind] - set(ALLOWED[kind]["missing"])
        if extra or missing:
            problems.append(f"{kind}: we write {sorted(extra)} that the bulk path does not; "
                            f"the bulk path writes {sorted(missing)} that we do not")
        stale = (set(ALLOWED[kind]["extra"]) - ours[kind]) | (set(ALLOWED[kind]["missing"]) & ours[kind])
        if stale:
            problems.append(f"{kind}: ALLOWED entries no longer needed: {sorted(stale)}")
    assert not problems, "\n".join(problems)


async def test_vectors_are_stored_with_the_bulk_paths_procedures(recorded):
    await neo4j_writer.create_node("Jane Doe", ["Person"], "a person", "g")
    await neo4j_writer.create_edge("a", "b", "WORKS_AT", "Jane Doe works at Example Co", valid_at=None)
    joined = "\n".join(recorded)
    assert "db.create.setNodeVectorProperty(n, 'name_embedding', $vector)" in joined
    assert "db.create.setRelationshipVectorProperty(e, 'fact_embedding', $vector)" in joined
    assert "name_embedding = $" not in joined and "fact_embedding = $" not in joined


async def test_a_degraded_write_clears_the_vector_instead_of_calling_the_procedure(recorded, monkeypatch):
    async def down(text):
        return None

    monkeypatch.setattr(neo4j_writer, "embed", down)
    await neo4j_writer.create_node("Jane Doe", ["Person"], "a person", "g")
    (create,) = [q for q in recorded if "CREATE (n:Entity" in q]
    # The procedure refuses a null vector (verified on Neo4j 5.26), and a stale
    # vector beside changed text would keep matching the old text.
    assert "setNodeVectorProperty" not in create and "n.name_embedding = null" in create


async def test_no_embedding_stamp_is_written_any_more(recorded):
    """`embedding_model`/`embedding_dimensions` (v2.8.1–v2.60.0) were not in
    the bulk shape and upstream's readers surface unknown properties as
    attributes; nothing reads them but the re-embed migration, which writes
    its own."""
    await neo4j_writer.create_node("Jane Doe", ["Person"], "a person", "g")
    await neo4j_writer.create_edge("a", "b", "WORKS_AT", "Jane Doe works at Example Co", valid_at=None)
    assert "embedding_model" not in "\n".join(recorded)
    assert "embedding_dimensions" not in "\n".join(recorded)


def test_reads_take_the_relationship_as_truth():
    """The canonical switch: every cockpit edge read takes its endpoints from
    the relationship, and only the audit's mismatch check still reads the
    duplicated properties."""
    src = pathlib.Path(neo4j_writer.__file__).with_name("neo4j_reader.py").read_text()
    tree = ast.parse(src)
    for fn in (n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)):
        body = ast.get_source_segment(src, fn)
        if fn.name == "audit":
            assert "e.source_node_uuid <> a.uuid" in body  # the mismatch audit stays
            continue
        assert "source_node_uuid" not in body and "target_node_uuid" not in body, fn.name
