"""Agent search filters (design record 2026-10-04, D6; v2.61.0).

* With no filter the search call is EXACTLY v2.60.0's — same recipe choice,
  same arguments, nothing extra.
* A centre node selects the node-distance recipes, whatever the reranker.
* Date windows never put two PARAMETER-CARRYING OR groups on one field:
  graphiti_core names a date parameter by its position inside its AND group
  (`valid_at_0`), so a second such group overwrites the first. The only other
  group we build is the parameterless `IS NULL` (an open bound). Checked by
  running the library's own constructor over everything we build.
* Entity types are validated against the ontology, in the tool (ModelRetry
  listing the valid ones) and in the read (it is interpolated as a label).
* Facts no longer leak the curation writer's embedding stamps as attributes.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic_ai import ModelRetry

from central_command.config import settings
from central_command.integrations import graph_ontology, graphiti, graphiti_client
from central_command.runtime import tools

T = datetime(2025, 3, 1, tzinfo=timezone.utc)
U = datetime(2025, 9, 1, tzinfo=timezone.utc)


def _lib():
    graphiti_client._prepare_environment()
    from graphiti_core.driver.driver import GraphProvider
    from graphiti_core.search import search_config_recipes as recipes
    from graphiti_core.search.search_filters import (
        ComparisonOperator,
        edge_search_filter_query_constructor,
        node_search_filter_query_constructor,
    )

    return GraphProvider, recipes, ComparisonOperator, edge_search_filter_query_constructor, \
        node_search_filter_query_constructor


class _Fake:
    def __init__(self):
        self.calls = []

    async def search_(self, query, **kw):
        self.calls.append(kw)

        class R:
            edges: list = []
            nodes: list = []

        return R()


@pytest.fixture
def fake(monkeypatch):
    f = _Fake()
    monkeypatch.setattr(graphiti_client, "get_graphiti", lambda: f)
    return f


@pytest.mark.parametrize("rerank,edge,node", [
    ("", "EDGE_HYBRID_SEARCH_RRF", "NODE_HYBRID_SEARCH_RRF"),
    ("cc-rerank", "EDGE_HYBRID_SEARCH_CROSS_ENCODER", "NODE_HYBRID_SEARCH_CROSS_ENCODER"),
])
async def test_no_filter_makes_exactly_the_old_call(fake, monkeypatch, rerank, edge, node):
    _, recipes, *_ = _lib()
    monkeypatch.setattr(settings, "graph_rerank_alias", rerank)
    await graphiti.search_facts("q", max_facts=25, group_ids=["g"])
    await graphiti.search_nodes("q", max_nodes=15, group_ids=["g"])
    (fact_call, node_call) = fake.calls
    assert set(fact_call) == {"config", "group_ids"} and set(node_call) == {"config", "group_ids"}
    assert fact_call["config"].model_dump(exclude={"limit"}) == getattr(recipes, edge).model_dump(
        exclude={"limit"})
    assert node_call["config"].model_dump(exclude={"limit"}) == getattr(recipes, node).model_dump(
        exclude={"limit"})


@pytest.mark.parametrize("rerank", ["", "cc-rerank"])
async def test_a_centre_node_uses_the_node_distance_recipes(fake, monkeypatch, rerank):
    _, recipes, *_ = _lib()
    monkeypatch.setattr(settings, "graph_rerank_alias", rerank)
    before = recipes.EDGE_HYBRID_SEARCH_NODE_DISTANCE.limit
    await graphiti.search_facts("q", max_facts=25, group_ids=["g"], center_node_uuid="n1")
    await graphiti.search_nodes("q", max_nodes=15, group_ids=["g"], center_node_uuid="n1")
    (fact_call, node_call) = fake.calls
    assert fact_call["center_node_uuid"] == node_call["center_node_uuid"] == "n1"
    assert fact_call["config"].model_dump(exclude={"limit"}) == \
        recipes.EDGE_HYBRID_SEARCH_NODE_DISTANCE.model_dump(exclude={"limit"})
    assert node_call["config"].model_dump(exclude={"limit"}) == \
        recipes.NODE_HYBRID_SEARCH_NODE_DISTANCE.model_dump(exclude={"limit"})
    assert recipes.EDGE_HYBRID_SEARCH_NODE_DISTANCE.limit == before  # a copy, never the recipe


WINDOWS = [
    {"as_of": T},
    {"after": T},
    {"before": U},
    {"after": T, "before": U},
]


@pytest.mark.parametrize("window", WINDOWS)
def test_a_date_field_never_has_two_parameter_carrying_or_groups(window):
    GraphProvider, _, Op, edge_q, _ = _lib()
    filters = graphiti.fact_filters(**window)
    for field in ("valid_at", "invalid_at", "created_at", "expired_at"):
        groups = getattr(filters, field) or []
        carrying = [g for g in groups
                    if any(d.comparison_operator not in (Op.is_null, Op.is_not_null) for d in g)]
        assert len(carrying) <= 1, f"{field}: {len(carrying)} parameter-carrying OR groups"
        assert all(len(g) == 1 for g in groups)
    clauses, params = edge_q(filters, GraphProvider.NEO4J)
    # The library's own constructor binds every date to the instant we meant.
    if "as_of" in window:
        assert params == {"valid_at_0": T, "invalid_at_0": T}
        assert "((e.valid_at IS NULL) OR (e.valid_at <= $valid_at_0))" in clauses
        assert "((e.invalid_at IS NULL) OR (e.invalid_at > $invalid_at_0))" in clauses
    else:
        expected = {}
        if "after" in window:
            expected["invalid_at_0"] = window["after"]
            assert "((e.invalid_at IS NULL) OR (e.invalid_at > $invalid_at_0))" in clauses
        if "before" in window:
            expected["valid_at_0"] = window["before"]
            assert "((e.valid_at IS NULL) OR (e.valid_at < $valid_at_0))" in clauses
        assert params == expected


def test_two_parameter_groups_would_collide_which_is_why_we_never_build_them():
    """The hazard itself, demonstrated on the library, so the rule above has
    its reason on record: the second group's date overwrites the first's."""
    GraphProvider, _, Op, edge_q, _ = _lib()
    from graphiti_core.search.search_filters import DateFilter, SearchFilters

    bad = SearchFilters(valid_at=[[DateFilter(date=T, comparison_operator=Op.greater_than)],
                                  [DateFilter(date=U, comparison_operator=Op.less_than)]])
    _, params = edge_q(bad, GraphProvider.NEO4J)
    assert params == {"valid_at_0": U}  # T is gone


def test_no_window_is_no_filter_and_both_kinds_of_window_are_refused():
    assert graphiti.fact_filters() is None
    with pytest.raises(ValueError):
        graphiti.fact_filters(as_of=T, after=T)


def test_entity_types_become_a_label_filter_and_unknown_ones_are_refused():
    GraphProvider, _, _, _, node_q = _lib()
    f = graphiti.node_filters(["Person", "Organization"])
    clauses, _ = node_q(f, GraphProvider.NEO4J)
    assert clauses == ["n:Person|Organization"]
    assert graphiti.node_filters(None) is None and graphiti.node_filters([]) is None
    with pytest.raises(ValueError, match="valid: Person"):
        graphiti.node_filters(["Persson"])


async def test_filters_reach_the_library(fake):
    await graphiti.search_facts("q", group_ids=["g"], as_of=T)
    await graphiti.search_nodes("q", group_ids=["g"], entity_types=["Person"])
    fact_call, node_call = fake.calls
    assert fact_call["search_filter"].valid_at[1][0].date == T
    assert node_call["search_filter"].node_labels == ["Person"]
    assert "center_node_uuid" not in fact_call


# --- the agent's tools ------------------------------------------------------------------


@pytest.fixture
def captured(monkeypatch):
    seen = {}

    async def facts(query, max_facts=8, agent_id=None, **kw):
        seen["facts"] = kw
        return []

    async def nodes(query, max_nodes=8, agent_id=None, **kw):
        seen["nodes"] = kw
        return []

    monkeypatch.setattr(graphiti, "search_facts", facts)
    monkeypatch.setattr(graphiti, "search_nodes", nodes)
    return seen


async def test_the_tools_pass_nothing_extra_by_default(captured):
    await tools.search_knowledge_graph(None, "q")
    await tools.search_knowledge_graph_entities(None, "q")
    assert captured == {"facts": {}, "nodes": {}}


async def test_the_fact_tool_parses_instants(captured):
    out = await tools.search_knowledge_graph(None, "q", as_of="2025-03-01", center_entity_uuid="n1")
    assert captured["facts"] == {"as_of": T, "center_node_uuid": "n1"}
    assert "true as of 2025-03-01" in out
    await tools.search_knowledge_graph(None, "q", after="2025-03-01T00:00:00Z", before="2025-09-01")
    assert captured["facts"] == {"after": T, "before": U}


@pytest.mark.parametrize("kw,match", [
    ({"as_of": "today"}, "not an ISO-8601 instant"),
    ({"as_of": "2025-03-01", "after": "2025-01-01"}, "EITHER as_of"),
    ({"after": "2025-09-01", "before": "2025-03-01"}, "earlier than"),
])
async def test_the_fact_tool_hands_bad_windows_back(captured, kw, match):
    with pytest.raises(ModelRetry, match=match):
        await tools.search_knowledge_graph(None, "q", **kw)
    assert "facts" not in captured


async def test_the_entity_tool_names_the_valid_types(captured):
    with pytest.raises(ModelRetry) as e:
        await tools.search_knowledge_graph_entities(None, "q", entity_types=["Email"])
    for name in graph_ontology.ENTITY_TYPE_NAMES:
        assert name in str(e.value)
    await tools.search_knowledge_graph_entities(None, "q", entity_types=["Person"])
    assert captured["nodes"] == {"entity_types": ["Person"]}


async def test_the_group_search_takes_the_same_filters(captured):
    await tools.search_graph_group(None, "g", "q", before="2025-09-01", entity_types=["Topic"],
                                   center_entity_uuid="n1")
    assert captured["facts"] == {"group_ids": ["g"], "before": U, "center_node_uuid": "n1"}
    assert captured["nodes"] == {"group_ids": ["g"], "entity_types": ["Topic"],
                                 "center_node_uuid": "n1"}


# --- the embedding-stamp leak (design record D8, release 2) ------------------------------


def test_fact_attributes_no_longer_carry_the_embedding_stamps():
    graphiti_client._prepare_environment()
    from graphiti_core.edges import EntityEdge
    from graphiti_core.nodes import EntityNode

    stamps = {"embedding_model": "cc-embedding", "embedding_dimensions": 1024}
    edge = EntityEdge(uuid="e", group_id="g", source_node_uuid="a", target_node_uuid="b",
                      created_at=T, name="KNOWS", fact="Jane Doe knows Sam Rivers",
                      episodes=["ep"], attributes={**stamps, "since": "2020"})
    node = EntityNode(uuid="n", name="Jane Doe", group_id="g", labels=["Entity"], created_at=T,
                      summary="", attributes={**stamps, "role": "lead"})
    # Before v2.61.0 the fact kept both stamps (model_dump of attributes); the
    # node result already dropped keys containing 'embedding'.
    assert graphiti.fact_result(edge)["attributes"] == {"since": "2020"}
    assert graphiti.node_result(node)["attributes"] == {"role": "lead"}
