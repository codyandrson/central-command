"""`GET /proposals/{id}/graph-known`: what the graph already holds about each
proposed episode's subject, shown to the reviewer. Advisory, and a failed read
is a 503, never an empty list.

Same pattern as tests/test_proposal_review_render.py: the repo is faked and the
route handler called directly. The graph reads are monkeypatched on
`central_command.integrations.graphiti` — the suite never touches the live graph.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from central_command.api import nerve_gateway, routes
from central_command.db import repo
from central_command.integrations import graphiti


async def _async(value):
    return value


def _row(actions: list[dict], agent_id: str = "inbox-triage") -> dict:
    return {
        "id": "prop_gk",
        "session_id": "sess_gk",
        "agent_id": agent_id,
        "intent": "test",
        "status": "AWAITING_HUMAN",
        "actions": actions,
        "evidence": [],
        "expected_effect": "",
        "created_at": None,
        "decided_at": None,
        "confidence": None,
        "origin": None,
    }


def _episode(name: str, body: str, **extra) -> dict:
    return {
        "capability": "graph.add_episode@v1",
        "arguments": {"name": name, "episode_body": body, "scope": "shared", **extra},
    }


_JIRA = {"capability": "jira.create_issue", "arguments": {"project": "EX", "summary": "x"}}


class _Graph:
    """Records each read's arguments and answers with edge/node results in the
    shapes `graphiti.fact_result` / `node_result` produce (extra keys included,
    which the endpoint must trim)."""

    def __init__(self, monkeypatch, *, facts=None, nodes=None, error=None):
        self.fact_calls: list[dict] = []
        self.node_calls: list[dict] = []
        self.facts, self.nodes, self.error = facts or [], nodes or [], error
        monkeypatch.setattr(graphiti, "search_facts", self._facts)
        monkeypatch.setattr(graphiti, "search_nodes", self._nodes)

    async def _facts(self, query, max_facts=8, agent_id=None, **kw):
        self.fact_calls.append({"query": query, "max_facts": max_facts, "agent_id": agent_id})
        if self.error:
            raise self.error
        return self.facts

    async def _nodes(self, query, max_nodes=8, agent_id=None, **kw):
        self.node_calls.append({"query": query, "max_nodes": max_nodes, "agent_id": agent_id})
        if self.error:
            raise self.error
        return self.nodes


_FACT = {
    "uuid": "f1",
    "fact": "Jane Doe banks with Example Credit Union",
    "name": "BANKS_WITH",
    "valid_at": "2026-01-02T00:00:00+00:00",
    "invalid_at": None,
    "group_id": "shared",
    "source_node_uuid": "n1",
    "target_node_uuid": "n2",
    "episodes": ["e1"],
    "attributes": {},
}
_NODE = {
    "uuid": "n1",
    "name": "Jane Doe",
    "summary": "A member of Example Credit Union.",
    "labels": ["Entity", "Person"],
    "created_at": None,
    "group_id": "shared",
    "attributes": {},
}


def _load(monkeypatch, row):
    monkeypatch.setattr(repo, "load_proposal", lambda pid: _async(row))


async def test_a_jira_action_and_an_episode_give_one_entry_at_the_episodes_index(monkeypatch):
    _load(monkeypatch, _row([_JIRA, _episode("Jane Doe account", "Jane Doe opened an account.")]))
    graph = _Graph(monkeypatch, facts=[_FACT], nodes=[_NODE])

    out = await routes.get_proposal_graph_known("prop_gk")

    assert out == {
        "proposal_id": "prop_gk",
        "episodes": [
            {
                "action_index": 1,
                "name": "Jane Doe account",
                "facts": [
                    {
                        "uuid": "f1",
                        "fact": "Jane Doe banks with Example Credit Union",
                        "name": "BANKS_WITH",
                        "valid_at": "2026-01-02T00:00:00+00:00",
                        "invalid_at": None,
                        "group_id": "shared",
                    }
                ],
                "entities": [
                    {
                        "uuid": "n1",
                        "name": "Jane Doe",
                        "summary": "A member of Example Credit Union.",
                    }
                ],
            }
        ],
    }
    assert graph.fact_calls[0]["max_facts"] == 6
    assert graph.node_calls[0] == {"query": "Jane Doe account", "max_nodes": 3, "agent_id": None}


async def test_the_fact_query_is_name_plus_body_capped(monkeypatch):
    _load(monkeypatch, _row([_episode("Name", "x" * 2000)]))
    graph = _Graph(monkeypatch)

    await routes.get_proposal_graph_known("prop_gk")

    q = graph.fact_calls[0]["query"]
    assert q.startswith("Name\nxxx") and len(q) == 600


async def test_two_episodes_each_get_their_own_entry(monkeypatch):
    _load(monkeypatch, _row([_episode("First", "a"), _JIRA, _episode("Second", "b")]))
    _Graph(monkeypatch, facts=[_FACT], nodes=[_NODE])

    out = await routes.get_proposal_graph_known("prop_gk")

    assert [(e["action_index"], e["name"]) for e in out["episodes"]] == [
        (0, "First"),
        (2, "Second"),
    ]


async def test_a_proposal_with_no_episode_returns_no_episodes_and_reads_nothing(monkeypatch):
    _load(monkeypatch, _row([_JIRA]))
    graph = _Graph(monkeypatch, facts=[_FACT], nodes=[_NODE])

    out = await routes.get_proposal_graph_known("prop_gk")

    assert out == {"proposal_id": "prop_gk", "episodes": []}
    assert graph.fact_calls == [] and graph.node_calls == []


async def test_a_found_nothing_is_an_empty_entry_not_an_error(monkeypatch):
    _load(monkeypatch, _row([_episode("Name", "body")]))
    _Graph(monkeypatch)

    out = await routes.get_proposal_graph_known("prop_gk")

    assert out["episodes"][0]["facts"] == [] and out["episodes"][0]["entities"] == []


async def test_a_shared_episode_reads_the_shared_set_only(monkeypatch):
    _load(monkeypatch, _row([_episode("Name", "body")], agent_id="inbox-triage"))
    graph = _Graph(monkeypatch)

    await routes.get_proposal_graph_known("prop_gk")

    assert graph.fact_calls[0]["agent_id"] is None
    assert graph.node_calls[0]["agent_id"] is None


async def test_a_private_episode_with_for_agent_reads_that_agents_partition(monkeypatch):
    action = _episode("Doctrine", "body", scope="private", for_agent="jira-expert")
    _load(monkeypatch, _row([action], agent_id="inbox-triage"))
    graph = _Graph(monkeypatch)

    await routes.get_proposal_graph_known("prop_gk")

    assert graph.fact_calls[0]["agent_id"] == "jira-expert"
    assert graph.node_calls[0]["agent_id"] == "jira-expert"
    # ...and that agent id is what makes the read set carry its partition (and
    # not the drafter's): the shared set plus private_group(for_agent).
    monkeypatch.setattr(graphiti, "steward_map", lambda: _async({}))
    groups = await graphiti.groups_for("jira-expert")
    assert graphiti.private_group("jira-expert") in groups
    assert graphiti.private_group("inbox-triage") not in groups


async def test_a_private_episode_without_for_agent_reads_the_drafters_partition(monkeypatch):
    _load(monkeypatch, _row([_episode("Mine", "body", scope="private")], agent_id="inbox-triage"))
    graph = _Graph(monkeypatch)

    await routes.get_proposal_graph_known("prop_gk")

    assert graph.fact_calls[0]["agent_id"] == "inbox-triage"
    assert graph.node_calls[0]["agent_id"] == "inbox-triage"


@pytest.mark.parametrize("failing", ["facts", "nodes"])
async def test_a_failed_graph_read_is_a_503_never_an_empty_list(monkeypatch, failing):
    _load(monkeypatch, _row([_episode("Name", "body")]))
    boom = ConnectionError("bolt unreachable")
    graph = _Graph(monkeypatch)
    if failing == "facts":
        graph.error = boom
    else:

        async def nodes(query, max_nodes=8, agent_id=None, **kw):
            raise boom

        monkeypatch.setattr(graphiti, "search_nodes", nodes)

    with pytest.raises(HTTPException) as exc:
        await routes.get_proposal_graph_known("prop_gk")

    assert exc.value.status_code == 503
    assert exc.value.detail == "graph unavailable: bolt unreachable"


async def test_an_embedder_mismatch_is_a_503_with_its_reason(monkeypatch):
    from central_command.contract import EmbedderMismatch

    _load(monkeypatch, _row([_episode("Name", "body")]))
    _Graph(monkeypatch, error=EmbedderMismatch("the embedder changed; re-embed the graph"))

    with pytest.raises(HTTPException) as exc:
        await routes.get_proposal_graph_known("prop_gk")

    assert exc.value.status_code == 503
    assert "the embedder changed" in exc.value.detail


async def test_an_unknown_proposal_is_404(monkeypatch):
    _load(monkeypatch, None)
    graph = _Graph(monkeypatch)

    with pytest.raises(HTTPException) as exc:
        await routes.get_proposal_graph_known("prop_nope")

    assert exc.value.status_code == 404
    assert graph.fact_calls == []


async def test_the_cockpit_rpc_reaches_the_route(monkeypatch):
    # The cockpit asks through the gateway RPC, not REST; a method missing from
    # the dispatch table would answer "unknown method" and the panel would show
    # an error for every proposal.
    _load(monkeypatch, _row([_episode("Name", "body")]))
    _Graph(monkeypatch, facts=[_FACT], nodes=[_NODE])

    out = await nerve_gateway._dispatch("proposals.graph_known", {"id": "prop_gk"}, None)

    assert out["episodes"][0]["action_index"] == 0
