"""The curation writer embeds with the SCOPED key at the data-plane base, and
the suite cannot reach a real embedding (epic-small-fixes story 11.18, R14,
GRAPH-064).

`neo4j_writer.request_embedding` used to post to `CC_LLM_PROXY_BASE_URL` with
the proxy ADMIN key. It now posts to `<CC_LLM_BASE_URL>/v1/embeddings` with
`Bearer CC_LLM_API_KEY`, normalised the way `graphiti_client._proxy_v1` is, so
it matches the extractor and Graphiti's own embedder. And `tests/conftest.py`'s
session guard `no_live_graph_writes` refuses a REAL send from the writer's HTTP
client unless `CC_LIVE_GRAPH_TESTS=1`; a test that fakes `httpx.AsyncClient` or
`request_embedding` itself is unaffected.
"""

from __future__ import annotations

import socket

import httpx
import pytest

from central_command.config import settings
from central_command.integrations import neo4j_writer
from tests.conftest import REAL_EMBEDDING_REFUSED

KEY = "sk-scoped-test-key"
ADMIN = "sk-admin-test-key"


class _Capture:
    def __init__(self, calls):
        self._calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, headers=None, json=None):
        self._calls.append((url, headers, json))
        return httpx.Response(200, json={"data": [{"embedding": [0.25, 0.5]}]},
                              request=httpx.Request("POST", url))


@pytest.fixture
def captured(monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", KEY)
    monkeypatch.setattr(settings, "llm_proxy_admin_key", ADMIN)
    monkeypatch.setattr(settings, "llm_proxy_base_url", "http://admin-plane.example.com")
    calls: list = []
    monkeypatch.setattr(neo4j_writer.httpx, "AsyncClient", lambda *a, **k: _Capture(calls))
    return calls


@pytest.mark.parametrize("base", ["http://proxy.example.com", "http://proxy.example.com/",
                                  "http://proxy.example.com/v1", "http://proxy.example.com/v1/"])
async def test_the_request_goes_to_the_data_plane_with_the_scoped_key(monkeypatch, captured, base):
    monkeypatch.setattr(settings, "llm_base_url", base)
    vector = await neo4j_writer.request_embedding("hello")
    assert vector == [0.25, 0.5]
    (url, headers, body), = captured
    assert url == "http://proxy.example.com/v1/embeddings"
    assert headers == {"Authorization": f"Bearer {KEY}"}
    assert ADMIN not in str(headers)
    assert body["input"] == "hello"


async def test_an_unfaked_embedding_is_refused_and_opens_no_socket(monkeypatch):
    monkeypatch.delenv("CC_LIVE_GRAPH_TESTS", raising=False)
    monkeypatch.setattr(settings, "llm_base_url", "http://127.0.0.1:9")
    seen: list = []
    real_connect = socket.socket.connect

    def spy(self, address):
        seen.append(address)
        return real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", spy)
    with pytest.raises(AssertionError) as refused:
        await neo4j_writer.request_embedding("hello")
    assert str(refused.value) == REAL_EMBEDDING_REFUSED
    assert "a test reached a real embedding" in str(refused.value)
    assert seen == []
