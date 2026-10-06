"""The in-process Graphiti client and the read layer on it (design record
2026-10-04, D1-D3, D6) — no network, no live graph.

The properties: the dependency pin and the patches agree on ONE version; the
client is built from explicit clients only and refuses missing configuration
by name; building it never issues index DDL; a reranker failure raises (no
fallback order — tests/test_graph_rerank.py holds the rest); every
search runs on a DEEP COPY of its recipe with the requested limit (the
module-level recipe, which add_episode also reads, is never touched); and the
read results keep the keys the retired MCP server returned.
"""

from __future__ import annotations

import json
import os
import tomllib
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from central_command.config import settings
from central_command.contract import classify_failure
from central_command.contract.failures import SEMANTIC, TRANSIENT
from central_command.integrations import graphiti, graphiti_client, graphiti_patches, neo4j_reader

ROOT = Path(__file__).resolve().parents[1]

FACT_KEYS = {"uuid", "group_id", "source_node_uuid", "target_node_uuid", "created_at", "name",
             "fact", "episodes", "expired_at", "valid_at", "invalid_at", "reference_time",
             "attributes"}
NODE_KEYS = {"uuid", "name", "labels", "created_at", "summary", "group_id", "attributes"}
EPISODE_KEYS = {"uuid", "name", "content", "created_at", "source", "source_description",
                "group_id"}


def _lib():
    graphiti_client._prepare_environment()
    from graphiti_core.edges import EntityEdge
    from graphiti_core.nodes import EntityNode
    from graphiti_core.search import search_config_recipes as recipes

    return EntityEdge, EntityNode, recipes


# --- the pin --------------------------------------------------------------------


def test_the_pyproject_pin_is_the_patches_version():
    deps = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "dependencies"]
    pins = [d for d in deps if d.replace(" ", "").startswith("graphiti-core")]
    assert pins == [f"graphiti-core=={graphiti_patches.PINNED_VERSION}"]


def test_the_lock_carries_the_same_pin():
    lock = (ROOT / "requirements.lock").read_text(encoding="utf-8").splitlines()
    assert f"graphiti-core=={graphiti_patches.PINNED_VERSION}" in lock


# --- building the client ------------------------------------------------------------


@pytest.fixture
def configured(monkeypatch):
    for name, value in {
        "llm_base_url": "http://proxy.example.com:4000",
        "llm_api_key": "sk-test-not-real",
        "graph_llm_alias": "graphiti-llm",
        "graph_llm_max_tokens": 4096,
        "graph_llm_temperature": 0.0,
        "graph_semaphore_limit": 3,
        "graph_rerank_alias": "",
        "graph_rerank_kind": "",
        "embed_alias": "cc-embedding",
        "embed_dim": 1024,
        "neo4j_url": "bolt://127.0.0.1:1",
        "neo4j_password": "not-a-real-password",
    }.items():
        monkeypatch.setattr(settings, name, value)


@pytest.mark.parametrize("key,env", [("llm_api_key", "CC_LLM_API_KEY"),
                                     ("neo4j_password", "CC_NEO4J_PASSWORD"),
                                     ("llm_base_url", "CC_LLM_BASE_URL")])
def test_missing_configuration_is_refused_by_name(configured, monkeypatch, key, env):
    monkeypatch.setattr(settings, key, "")
    with pytest.raises(graphiti_client.GraphitiNotConfigured, match=env):
        graphiti_client._build()


async def test_the_client_is_built_explicitly_and_issues_no_ddl(configured):
    g = graphiti_client._build()
    try:
        assert os.environ["GRAPHITI_TELEMETRY_ENABLED"] == "false"
        assert os.environ["SEMAPHORE_LIMIT"] == "3"
        # The constructor's scheduled index DDL was cancelled before it ran.
        assert g.driver._init_task is None
        from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient

        assert isinstance(g.llm_client, OpenAIGenericClient)
        assert g.llm_client.model == "graphiti-llm"
        assert g.llm_client.max_tokens == 4096
        assert g.llm_client.temperature == 0
        assert str(g.llm_client.client.base_url).rstrip("/") == "http://proxy.example.com:4000/v1"
        assert g.embedder.config.embedding_model == "cc-embedding"
        assert g.embedder.config.embedding_dim == 1024
        # No reranker alias: our NoReranker (the RRF recipes never call it;
        # the gpt-4.1-nano fallback client upstream would build is gone).
        assert isinstance(g.cross_encoder, graphiti_client.NoReranker)
        assert g.max_coroutines == 3
    finally:
        await g.driver.close()


async def test_a_rerank_alias_selects_our_rerank_client(configured, monkeypatch):
    monkeypatch.setattr(settings, "graph_rerank_alias", "cc-rerank")
    g = graphiti_client._build()
    try:
        assert isinstance(g.cross_encoder, graphiti_client.RerankClient)
        assert g.cross_encoder.url == "http://proxy.example.com:4000/rerank"
        assert g.cross_encoder.model == "cc-rerank"
    finally:
        await g.driver.close()


async def test_a_chat_kind_selects_our_chat_reranker(configured, monkeypatch):
    monkeypatch.setattr(settings, "graph_rerank_alias", "cc-rerank")
    monkeypatch.setattr(settings, "graph_rerank_kind", "chat")
    g = graphiti_client._build()
    try:
        assert isinstance(g.cross_encoder, graphiti_client.ChatRerankClient)
        assert g.cross_encoder.model == "cc-rerank"
    finally:
        await g.driver.close()


def test_an_unknown_kind_refuses_to_build(configured, monkeypatch):
    monkeypatch.setattr(settings, "graph_rerank_alias", "cc-rerank")
    monkeypatch.setattr(settings, "graph_rerank_kind", "bogus")
    with pytest.raises(graphiti_client.GraphitiNotConfigured, match="CC_GRAPH_RERANK_KIND"):
        graphiti_client._build()


# --- RerankClient (D3) --------------------------------------------------------------


def _mock_httpx(monkeypatch, handler):
    real = httpx.AsyncClient

    def factory(*a, **kw):
        kw["transport"] = httpx.MockTransport(handler)
        return real(*a, **kw)

    monkeypatch.setattr(graphiti_client.httpx, "AsyncClient", factory)


async def test_rerank_posts_documents_and_sorts_by_relevance(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"results": [
            {"index": 0, "relevance_score": 0.1},
            {"index": 2, "relevance_score": 0.9},
            {"index": 1, "relevance_score": 0.5},
        ]})

    _mock_httpx(monkeypatch, handler)
    client = graphiti_client.RerankClient("http://proxy.example.com:4000/v1", "sk-x", "cc-rerank")
    out = await client.rank("who leads", ["a", "b", "c"])
    assert out == [("c", 0.9), ("b", 0.5), ("a", 0.1)]
    assert seen["url"] == "http://proxy.example.com:4000/rerank"
    assert seen["auth"] == "Bearer sk-x"
    assert seen["body"] == {"model": "cc-rerank", "query": "who leads", "documents": ["a", "b", "c"]}


@pytest.mark.parametrize("handler,raised", [
    (lambda r: httpx.Response(503, json={"error": "down"}), graphiti_client.RerankHTTPError),
    (lambda r: httpx.Response(200, json={"unexpected": True}), graphiti_client.RerankError),
    (lambda r: (_ for _ in ()).throw(httpx.ConnectError("refused")), httpx.ConnectError),
])
async def test_rerank_raises_instead_of_falling_back_to_input_order(monkeypatch, handler, raised):
    """The retired fail-soft (input order with placeholder scores) hid a
    reranker outage as worse ordering; the operator's rule (2026-10-05) is that
    a failure raises and the read path's own retry and error handling decide."""
    _mock_httpx(monkeypatch, handler)
    client = graphiti_client.RerankClient("http://proxy.example.com:4000", "sk-x", "cc-rerank")
    with pytest.raises(raised):
        await client.rank("q", ["a", "b", "c", "d"])


# --- search: deep-copied recipe, the requested limit -----------------------------------


class _FakeGraphiti:
    def __init__(self, edges=(), nodes=()):
        self.configs = []
        self.edges, self.nodes = list(edges), list(nodes)

    async def search_(self, query, config, group_ids=None, **kw):
        self.configs.append((config, group_ids))

        class R:
            pass

        r = R()
        r.edges, r.nodes = self.edges, self.nodes
        return r


def _edge(i: int):
    EntityEdge, _, _ = _lib()
    return EntityEdge(uuid=f"e{i}", group_id="central_command", source_node_uuid="a",
                      target_node_uuid="b", created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                      name="LEADS", fact=f"fact {i}", episodes=["ep"], fact_embedding=[0.1, 0.2])


def _node(i: int):
    _, EntityNode, _ = _lib()
    return EntityNode(uuid=f"n{i}", name=f"Ada {i}", group_id="central_command",
                      labels=["Entity", "Person"], summary="leads the probe team",
                      created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                      name_embedding=[0.1], attributes={"role": "lead", "name_embedding": [1.0]})


@pytest.mark.parametrize("rerank,kind,edge_recipe,node_recipe", [
    ("", "", "EDGE_HYBRID_SEARCH_RRF", "NODE_HYBRID_SEARCH_RRF"),
    ("", "chat", "EDGE_HYBRID_SEARCH_RRF", "NODE_HYBRID_SEARCH_RRF"),
    ("cc-rerank", "", "EDGE_HYBRID_SEARCH_CROSS_ENCODER", "NODE_HYBRID_SEARCH_CROSS_ENCODER"),
    ("cc-rerank", "rerank", "EDGE_HYBRID_SEARCH_CROSS_ENCODER", "NODE_HYBRID_SEARCH_CROSS_ENCODER"),
    ("cc-rerank", "chat", "EDGE_HYBRID_SEARCH_CROSS_ENCODER", "NODE_HYBRID_SEARCH_CROSS_ENCODER"),
])
async def test_search_uses_a_deep_copy_with_the_requested_limit(
    monkeypatch, rerank, kind, edge_recipe, node_recipe
):
    """The tier selection: a reranker of EITHER kind selects the cross-encoder
    recipes; no alias selects rank fusion, whatever the kind says."""
    _, _, recipes = _lib()
    monkeypatch.setattr(settings, "graph_rerank_alias", rerank)
    monkeypatch.setattr(settings, "graph_rerank_kind", kind)
    fake = _FakeGraphiti(edges=[_edge(i) for i in range(30)], nodes=[_node(i) for i in range(30)])
    monkeypatch.setattr(graphiti_client, "get_graphiti", lambda: fake)
    before = {name: getattr(recipes, name).limit for name in (edge_recipe, node_recipe)}

    facts = await graphiti.search_facts("q", max_facts=25, group_ids=["central_command"])
    nodes = await graphiti.search_nodes("q", max_nodes=15, group_ids=["central_command"])

    (edge_cfg, edge_groups), (node_cfg, _) = fake.configs
    assert edge_cfg is not getattr(recipes, edge_recipe)
    assert node_cfg is not getattr(recipes, node_recipe)
    assert edge_cfg.limit == 25 and node_cfg.limit == 15
    assert edge_cfg.model_dump(exclude={"limit"}) == getattr(recipes, edge_recipe).model_dump(
        exclude={"limit"})
    assert edge_groups == ["central_command"]
    # Ask for N, get up to N (the server sliced a ten-result recipe).
    assert len(facts) == 25 and len(nodes) == 15
    # The module-level recipes add_episode reads are untouched.
    assert {name: getattr(recipes, name).limit for name in before} == before


async def test_fact_and_node_results_keep_the_servers_keys(monkeypatch):
    fake = _FakeGraphiti(edges=[_edge(1)], nodes=[_node(1)])
    monkeypatch.setattr(graphiti_client, "get_graphiti", lambda: fake)
    (fact,) = await graphiti.search_facts("q", group_ids=["central_command"])
    (node,) = await graphiti.search_nodes("q", group_ids=["central_command"])
    assert set(fact) == FACT_KEYS
    assert "fact_embedding" not in json.dumps(fact)
    assert fact["created_at"].startswith("2026-01-01T00:00:00")  # model_dump(mode="json")
    assert set(node) == NODE_KEYS
    assert node["created_at"] == "2026-01-01T00:00:00+00:00"
    assert node["labels"] == ["Entity", "Person"]
    assert node["attributes"] == {"role": "lead"}  # no key containing 'embedding'
    json.dumps([fact, node])  # wire-safe


async def test_episode_listing_keeps_the_servers_keys_and_orders_by_time(monkeypatch):
    seen = {}

    async def fake_read(query, **params):
        seen["query"], seen["params"] = query, params
        from neo4j.time import DateTime

        return [{"uuid": "ep2", "name": "two", "content": "b",
                 "created_at": DateTime(2026, 2, 1, 0, 0, 0, tzinfo=timezone.utc),
                 "source": "text", "source_description": "s", "group_id": "central_command"}]

    monkeypatch.setattr(neo4j_reader, "_read", fake_read)
    (ep,) = await graphiti.get_group_episodes(["central_command"], last_n=7)
    assert set(ep) == EPISODE_KEYS
    assert ep["created_at"].startswith("2026-02-01T00:00:00")
    assert "ORDER BY e.created_at DESC" in seen["query"]
    assert seen["params"] == {"group_ids": ["central_command"], "limit": 7}


async def test_an_invalid_group_id_is_refused_before_any_query(monkeypatch):
    async def no_read(*a, **k):
        raise AssertionError("must not query")

    monkeypatch.setattr(neo4j_reader, "_read", no_read)
    with pytest.raises(graphiti.GraphitiError):
        await graphiti.get_group_episodes(["central_command:colon"])


async def test_status_is_a_ping_that_never_raises(monkeypatch):
    async def down(*a, **k):
        from neo4j.exceptions import AuthError

        raise AuthError("unauthorized")

    monkeypatch.setattr(neo4j_reader, "_read", down)
    status = await graphiti.get_status()
    assert status["status"] == "error" and status["error"] == "AuthError"


# --- the taxonomy knows the library's exceptions --------------------------------------


def test_the_failure_taxonomy_classifies_the_graph_libraries():
    import openai
    from neo4j.exceptions import ServiceUnavailable, TransientError
    from pydantic import BaseModel, ValidationError

    req = httpx.Request("POST", "http://proxy.example.com")

    class M(BaseModel):
        n: int

    try:
        M(n="connection timeout")  # a message carrying transient-looking words
    except ValidationError as e:
        validation = e
    cases = {
        openai.APIConnectionError(request=req): TRANSIENT,
        openai.APITimeoutError(request=req): TRANSIENT,
        openai.InternalServerError("x", response=httpx.Response(503, request=req), body=None):
            TRANSIENT,
        openai.BadRequestError("x", response=httpx.Response(400, request=req), body=None):
            SEMANTIC,
        ServiceUnavailable("Couldn't connect"): TRANSIENT,
        TransientError("busy"): TRANSIENT,
        json.JSONDecodeError("Unterminated string", '{"a": "', 6): SEMANTIC,
        validation: SEMANTIC,
    }
    for exc, want in cases.items():
        assert classify_failure(exc) == want, (type(exc).__name__, want)
