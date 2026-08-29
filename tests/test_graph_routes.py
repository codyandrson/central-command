"""Wire-shape tests for the cockpit Graph panel routes (rung 3, 2026-08-09).

Same idiom as `tests/test_discussion_stall.py::
test_composer_state_carries_closed_reason_to_the_cockpit`: call the route
handler directly, monkeypatch the reader/repo seams, assert the exact JSON
keys the frontend declares its own interface over. Nothing here touches real
Neo4j or Claude — `neo4j_reader` and `repo` are monkeypatched at every call.
"""

from __future__ import annotations

import httpx
import pytest

from central_command.api import routes
from central_command.db import repo
from central_command.integrations import neo4j_reader


class _FakeResp:
    def __init__(self, status_code):
        self.status_code = status_code


class _FakeClient:
    def __init__(self, *, status_code=None, exc=None):
        self._status_code = status_code
        self._exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url):
        if self._exc:
            raise self._exc
        return _FakeResp(self._status_code)


async def test_graph_status_carries_graphistry_state_to_the_cockpit(monkeypatch):
    monkeypatch.setattr(neo4j_reader, "ping", lambda: _ok(True))
    monkeypatch.setattr(
        neo4j_reader, "counts",
        lambda: _ok({"node_count": 102, "edge_count": 112, "group_ids": ["main"]}),
    )

    # disabled: the toggle is off, so no probe is even attempted.
    monkeypatch.setattr(
        repo, "get_app_setting",
        lambda key, default: _ok({"graphistry_enabled": False, "graphistry_url": ""}),
    )
    status = await routes.graph_status()
    assert status == {
        "available": True, "node_count": 102, "edge_count": 112,
        "group_ids": ["main"], "graphistry": "disabled",
        "graphistry_enabled": False, "graphistry_url": "",
    }

    # unreachable: enabled, but the probe fails (workstation off / GPU busy —
    # an EXPECTED state, not an error, per the design spec).
    monkeypatch.setattr(
        repo, "get_app_setting",
        lambda key, default: _ok({
            "graphistry_enabled": True, "graphistry_url": "http://ws:8080",
        }),
    )
    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda **kw: _FakeClient(exc=httpx.ConnectError("refused")),
    )
    status = await routes.graph_status()
    assert status["graphistry"] == "unreachable"
    assert status["graphistry_enabled"] is True
    assert status["graphistry_url"] == "http://ws:8080"

    # live: enabled and the probe answers 2xx.
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _FakeClient(status_code=200))
    status = await routes.graph_status()
    assert status["graphistry"] == "live"


async def _ok(value):
    return value


async def test_graph_neighborhood_wire_shape(monkeypatch):
    canned = {
        "nodes": [{"uuid": "n1", "name": "Lee", "labels": ["Entity"],
                   "summary": "s", "group_id": "main"}],
        "edges": [{
            "uuid": "e1", "source": "n1", "target": "n2", "name": "USED_FOR",
            "fact": "fact text", "valid_at": "2026-07-09T05:20:06+00:00",
            "invalid_at": None, "created_at": "2026-07-09T05:20:15+00:00",
            "invalid": False,
        }],
    }

    async def fake_neighborhood(uuid, at, limit):
        return canned

    monkeypatch.setattr(neo4j_reader, "neighborhood", fake_neighborhood)
    result = await routes.graph_neighborhood(uuid="n1", at=None, limit=50)

    assert result == canned
    edge = result["edges"][0]
    assert set(edge) == {
        "uuid", "source", "target", "name", "fact",
        "valid_at", "invalid_at", "created_at", "invalid",
    }


async def test_graph_settings_put_round_trips(monkeypatch):
    store: dict = {}

    async def fake_get(key, default):
        return store.get(key, default)

    async def fake_set(key, value):
        store[key] = value

    monkeypatch.setattr(repo, "get_app_setting", fake_get)
    monkeypatch.setattr(repo, "set_app_setting", fake_set)
    monkeypatch.setattr(neo4j_reader, "ping", lambda: _ok(False))

    body = routes.GraphSettingsIn(
        graphistry_enabled=True, graphistry_url="http://ws:8080"
    )
    result = await routes.graph_settings_put(body)

    assert store[routes._GRAPH_SETTINGS_KEY] == {
        "graphistry_enabled": True, "graphistry_url": "http://ws:8080",
    }
    assert result["graphistry_enabled"] is True
    assert result["graphistry_url"] == "http://ws:8080"
    assert result["available"] is False

    # Partial update leaves the untouched field alone (PUT semantics per spec).
    body2 = routes.GraphSettingsIn(graphistry_enabled=False)
    result2 = await routes.graph_settings_put(body2)
    assert result2["graphistry_url"] == "http://ws:8080"
    assert result2["graphistry_enabled"] is False


async def test_graph_all_forwards_the_as_of_instant(monkeypatch):
    seen = {}

    async def fake_everything(group_id, limit, at=None):
        seen.update(group_id=group_id, limit=limit, at=at)
        return {"nodes": [], "edges": [], "truncated": False}

    monkeypatch.setattr(neo4j_reader, "everything", fake_everything)
    await routes.graph_all(at="2024-01-01")
    assert seen["at"] == "2024-01-01", "the time filter never reached the reader"


async def test_graph_search_clamps_a_negative_limit(monkeypatch):
    """A negative limit used to reach the Cypher `LIMIT` raw and 503 as a
    driver gql_status error."""
    seen = {}

    async def fake_search_entities(q, group_id, limit):
        seen.update(q=q, group_id=group_id, limit=limit)
        return []

    monkeypatch.setattr(neo4j_reader, "search_entities", fake_search_entities)
    await routes.graph_search(q="anything", limit=-1)
    assert seen["limit"] == 1


async def test_curation_writes_land_on_the_governance_record(monkeypatch):
    """Every operator graph edit emits `graph.curated` — after the write, since
    it records a completed action rather than authorising one."""
    from central_command import events
    from central_command.integrations import neo4j_writer

    emitted = []

    async def fake_emit(kind, ref_id=None, payload=None, actor=None):
        emitted.append({"kind": kind, "ref_id": ref_id, "payload": payload, "actor": actor})
        return {}

    async def fake_create_edge(source, target, name, fact, valid_at=None):
        return {"uuid": "edge-1", "embedded": True, "episode": "ep-1"}

    monkeypatch.setattr(events, "emit", fake_emit)
    monkeypatch.setattr(neo4j_writer, "create_edge", fake_create_edge)

    body = routes.GraphEdgeIn(
        source_uuid="a", target_uuid="b", name="KNOWS", fact="A knows B")
    await routes.graph_edge_create(body)

    assert len(emitted) == 1
    ev = emitted[0]
    assert ev["kind"] == "graph.curated"
    assert ev["ref_id"] == "edge-1"
    assert ev["actor"] == "operator"
    assert ev["payload"]["op"] == "edge.create"
    assert ev["payload"]["detail"]["fact"] == "A knows B"
    assert ev["payload"]["result"]["episode"] == "ep-1"


async def test_graph_episodes_defaults_to_private_only_for_an_agent(monkeypatch):
    """D11-r1 + the memory-panel default (2026-08-29): with an agent_id and
    no explicit scope, the route must ask graphiti for THAT agent's private
    partition only — not the shared+private union — and report a real
    total/has_more pair so the panel never silently truncates."""
    from central_command.integrations import graphiti, neo4j_reader

    seen = {}

    async def fake_get_episodes(last_n=50, agent_id=None, private_only=False):
        seen["agent_id"] = agent_id
        seen["private_only"] = private_only
        seen["last_n"] = last_n
        return [
            {"uuid": "2", "name": "mine", "group_id": graphiti.private_group("jira-expert"),
             "created_at": "2026-08-02T00:00:00Z"},
        ]

    async def fake_count_episodes(group_ids):
        seen["group_ids"] = group_ids
        return 1

    monkeypatch.setattr(graphiti, "get_episodes", fake_get_episodes)
    monkeypatch.setattr(neo4j_reader, "count_episodes", fake_count_episodes)
    result = await routes.graph_episodes(agent_id="jira-expert", limit=50)

    assert seen["agent_id"] == "jira-expert"
    assert seen["private_only"] is True
    assert seen["last_n"] == 51  # limit+1, to derive has_more
    assert seen["group_ids"] == [graphiti.private_group("jira-expert")]
    assert result["episodes"][0]["scope"] == "private"
    assert result["total"] == 1
    assert result["has_more"] is False


async def test_graph_episodes_scope_all_widens_back_to_shared_and_private(monkeypatch):
    """scope=all is the explicit opt-out of the private-only default — same
    shared+private union and per-row tagging as before."""
    from central_command.integrations import graphiti, neo4j_reader

    seen = {}

    async def fake_get_episodes(last_n=50, agent_id=None, private_only=False):
        seen["private_only"] = private_only
        return [
            {"uuid": "1", "name": "shared", "group_id": graphiti.settings.graph_write_group,
             "created_at": "2026-08-01T00:00:00Z"},
            {"uuid": "2", "name": "mine", "group_id": graphiti.private_group("jira-expert"),
             "created_at": "2026-08-02T00:00:00Z"},
        ]

    monkeypatch.setattr(graphiti, "get_episodes", fake_get_episodes)
    monkeypatch.setattr(neo4j_reader, "count_episodes", lambda group_ids: _ok(2))
    result = await routes.graph_episodes(agent_id="jira-expert", scope="all")

    assert seen["private_only"] is False
    by_uuid = {e["uuid"]: e for e in result["episodes"]}
    assert by_uuid["1"]["scope"] == "shared"
    assert by_uuid["2"]["scope"] == "private"


async def test_graph_episodes_without_agent_id_is_all_shared(monkeypatch):
    """No agent_id means no widened read and every row reads shared — the
    unchanged behavior for a caller with no agent context (e.g. dashboard)."""
    from central_command.integrations import graphiti, neo4j_reader

    async def fake_get_episodes(last_n=50, agent_id=None, private_only=False):
        assert agent_id is None
        assert private_only is False
        return [{"uuid": "1", "name": "shared", "group_id": graphiti.settings.graph_write_group,
                  "created_at": "2026-08-01T00:00:00Z"}]

    monkeypatch.setattr(graphiti, "get_episodes", fake_get_episodes)
    monkeypatch.setattr(neo4j_reader, "count_episodes", lambda group_ids: _ok(1))
    result = await routes.graph_episodes()
    assert result["episodes"][0]["scope"] == "shared"
    assert result["total"] == 1


async def test_a_refused_curation_write_emits_nothing(monkeypatch):
    from central_command import events
    from central_command.integrations import neo4j_writer

    emitted = []

    async def fake_emit(*a, **kw):
        emitted.append(1)
        return {}

    async def refuse(uuid):
        raise neo4j_writer.WriteError("no entity with uuid nope")

    monkeypatch.setattr(events, "emit", fake_emit)
    monkeypatch.setattr(neo4j_writer, "delete_node", refuse)

    with pytest.raises(Exception):
        await routes.graph_node_delete("nope")
    assert emitted == [], "a write that never landed must not go on the record"
