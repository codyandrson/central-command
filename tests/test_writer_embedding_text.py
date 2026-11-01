"""The curation writer embeds exactly `embedding_text()` (epic-embedder-integrity
story 3, R7): entities as name + newline + summary, stripped; edges as the fact
verbatim. The same function the re-embed and the canaries use, so a hand-made
element and an extracted one (the third graphiti-core patch) share one vector
space. The graph is a router over `_write`; nothing is embedded for real."""

from __future__ import annotations

import pytest

from central_command.contract.embedding_text import embedding_text
from central_command.integrations import embedder_record, neo4j_writer


@pytest.fixture
def seen(monkeypatch):
    texts: list[str] = []

    async def embed(text):
        texts.append(text)
        return [1.0, 0.0, 0.0, 0.0]

    async def gate():
        return None

    async def write(query, **params):
        if "RETURN n.name AS name" in query:
            return [{"name": "Old name", "summary": "Old summary"}]
        if "RETURN e.name AS name" in query:
            return [{"name": "WORKS_AT", "fact": "Old fact", "source": "a", "target": "b"}]
        if "RETURN a.group_id" in query:
            return [{"group_id": "g"}]
        return []

    async def episode(*a, **k):
        return "ep-1"

    monkeypatch.setattr(neo4j_writer, "embed", embed)
    monkeypatch.setattr(embedder_record, "require_open_for_write", gate)
    monkeypatch.setattr(neo4j_writer, "_write", write)
    monkeypatch.setattr(neo4j_writer, "_record_operator_episode", episode)
    return texts


@pytest.mark.parametrize("name,summary", [
    ("Jane Doe", "Works at Example Co"), ("Jane Doe", ""), ("  Padded  ", "  two\nlines  "),
])
async def test_create_node_embeds_the_entity_recipe(seen, name, summary):
    await neo4j_writer.create_node(name, [], summary, "g")
    assert seen == [embedding_text("entity", name, summary)]


async def test_update_node_embeds_the_entity_recipe_over_the_new_text(seen):
    await neo4j_writer.update_node("u-1", name="New name")
    await neo4j_writer.update_node("u-1", summary="New summary")
    await neo4j_writer.update_node("u-1", name="Both", summary=None)
    assert seen == [
        embedding_text("entity", "New name", "Old summary"),
        embedding_text("entity", "Old name", "New summary"),
        embedding_text("entity", "Both", "Old summary"),
    ]


async def test_edges_embed_the_fact_verbatim(seen):
    await neo4j_writer.create_edge("a", "b", "WORKS_AT", "Jane works there")
    await neo4j_writer.update_edge("u-1", fact="Jane no longer works there")
    assert seen == [embedding_text("edge", fact="Jane works there"),
                    embedding_text("edge", fact="Jane no longer works there")]
    assert seen == ["Jane works there", "Jane no longer works there"]


def test_the_writer_holds_no_inline_recipe():
    """Every embed() call in the writer is handed embedding_text(...); an f-string
    or a bare variable there is a second recipe."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(neo4j_writer))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name) and n.func.id == "embed" and n.args]
    # `embed` itself is called by the dispatcher-facing helpers with a caller's text;
    # the four hand-made write sites are the ones that name a recipe.
    recipes = [c for c in calls if isinstance(c.args[0], ast.Call)
               and getattr(c.args[0].func, "id", None) == "embedding_text"]
    assert len(recipes) == 4, [ast.unparse(c) for c in calls]
