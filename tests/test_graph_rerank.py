"""The graph reranker (design record 2026-10-04, D3 as rebuilt in v2.62.0) — no
network, no live graph.

Three tiers through ONE alias: a dedicated `/rerank` model, a chat model asked
upstream's True/False question, or none (RRF). The properties pinned here:

* the chat question is upstream's, byte for byte, read from the INSTALLED
  graphiti-core — the 2026-10-05 benchmark measured those bytes;
* the kind is chosen by `CC_GRAPH_RERANK_KIND`, empty meaning `rerank`, and an
  unknown kind is refused by name;
* a reranker that fails RAISES — no fallback order, no neutral score, no
  skipped candidate (operator's rule, 2026-10-05) — and the failure taxonomy
  decides what the agent read path retries: transient once more, then an
  honest error; semantic (a malformed answer) at once;
* every failure is counted for the Systems row and the self-check.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import json
import math
from types import SimpleNamespace

import httpx
import pytest

from central_command.config import settings
from central_command.contract import classify_failure
from central_command.contract.failures import SEMANTIC, TRANSIENT
from central_command.integrations import graphiti, graphiti_client
from central_command.runtime import tools

PROXY = "http://proxy.example.com:4000"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for name, value in {
        "llm_base_url": PROXY,
        "llm_api_key": "sk-test-not-real",
        "graph_rerank_alias": "",
        "graph_rerank_kind": "",
        "graph_semaphore_limit": 3,
        "ca_bundle": "",
        "client_cert": "",
    }.items():
        monkeypatch.setattr(settings, name, value)
    graphiti_client.reset_rerank_stats()
    yield
    graphiti_client.reset_rerank_stats()


# --- the question is upstream's, byte for byte ----------------------------------


def _upstream_prompt_parts():
    graphiti_client._prepare_environment()
    from graphiti_core.cross_encoder import openai_reranker_client as upstream

    # The WHOLE module's source: dedenting a method's source would also strip
    # the indentation INSIDE the triple-quoted prompt.
    tree = ast.parse(inspect.getsource(upstream))
    system = user = None
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == "content":
            if isinstance(node.value, ast.Constant):
                system = node.value.value
            elif isinstance(node.value, ast.JoinedStr):
                user = node.value
    assert system is not None and user is not None, "upstream's prompt moved — read it"
    return system, user


def test_the_chat_question_is_upstreams_verbatim():
    """If this fails after a graphiti-core upgrade, upstream changed the
    question: the benchmark no longer describes what we send. A human decides
    whether to follow it (and re-measure) before changing either side."""
    system, user = _upstream_prompt_parts()
    assert graphiti_client.CHAT_RERANK_SYSTEM == system
    code = compile(ast.Expression(user), "<upstream prompt>", "eval")
    for query, passage in [("Who leads Atlas?", "Jane Doe leads Atlas."),
                           ("q {braces}", "p with {braces} and\nnewline")]:
        rendered = eval(code, {"passage": passage, "query": query})  # noqa: S307 — upstream's literal
        assert graphiti_client.chat_rerank_user_prompt(query, passage) == rendered


def test_upstream_still_sends_the_logit_bias_we_deliberately_drop():
    """We do not send upstream's `logit_bias` (two OpenAI-tokenizer ids,
    unrelated tokens elsewhere). Pinned so a change upstream is noticed."""
    graphiti_client._prepare_environment()
    from graphiti_core.cross_encoder import openai_reranker_client as upstream

    src = inspect.getsource(upstream)
    assert "logit_bias={'6432': 1, '7983': 1}" in src
    assert "max_tokens=1" in src and "top_logprobs=2" in src and "temperature=0" in src


# --- the kind ----------------------------------------------------------------------


@pytest.mark.parametrize("alias,kind,expected", [
    ("", "", None), ("", "chat", None),
    ("cc-rerank", "", "rerank"), ("cc-rerank", "rerank", "rerank"),
    ("cc-rerank", "chat", "chat"), ("cc-rerank", " Chat ", "chat"),
])
def test_the_kind_follows_the_setting_and_empty_means_rerank(monkeypatch, alias, kind, expected):
    monkeypatch.setattr(settings, "graph_rerank_alias", alias)
    monkeypatch.setattr(settings, "graph_rerank_kind", kind)
    assert graphiti_client.rerank_kind() == expected
    assert graphiti_client.reranker_configured() is bool(alias)


def test_an_unknown_kind_is_refused_by_name(monkeypatch):
    monkeypatch.setattr(settings, "graph_rerank_alias", "cc-rerank")
    monkeypatch.setattr(settings, "graph_rerank_kind", "cohere")
    with pytest.raises(graphiti_client.GraphitiNotConfigured, match="CC_GRAPH_RERANK_KIND"):
        graphiti_client.rerank_kind()


def test_each_kind_builds_its_client(monkeypatch):
    assert isinstance(graphiti_client.build_reranker(), graphiti_client.NoReranker)
    monkeypatch.setattr(settings, "graph_rerank_alias", "cc-rerank")
    rr = graphiti_client.build_reranker()
    assert isinstance(rr, graphiti_client.RerankClient)
    assert rr.url == f"{PROXY}/rerank" and rr.model == "cc-rerank"
    monkeypatch.setattr(settings, "graph_rerank_kind", "chat")
    chat = graphiti_client.build_reranker()
    assert isinstance(chat, graphiti_client.ChatRerankClient)
    assert chat.model == "cc-rerank" and chat.concurrency == 3
    assert str(chat.client.base_url).rstrip("/") == f"{PROXY}/v1"


async def test_no_reranker_refuses_to_be_called():
    """It exists because the constructor needs a cross-encoder; the RRF
    recipes never call it. If something did, an unranked list would be a
    silent degradation — so it raises."""
    with pytest.raises(RuntimeError, match="CC_GRAPH_RERANK_ALIAS is empty"):
        await graphiti_client.NoReranker().rank("q", ["a"])


# --- reading the chat answer ---------------------------------------------------------


@pytest.mark.parametrize("token,expected_true", [
    ("True", True), (" true", True), ("▁True", True), ("ĠTRUE", True),
    ("False", False), (" false", False), ("▁False", False), ("FALSE.", None),
])
def test_the_answer_token_is_read_robustly(token, expected_true):
    lp = math.log(0.8)
    if expected_true is None:
        with pytest.raises(graphiti_client.RerankError):
            graphiti_client.chat_rerank_score("cc-rerank", [{"token": token, "logprob": lp}])
        return
    score = graphiti_client.chat_rerank_score("cc-rerank", [{"token": token, "logprob": lp}])
    assert score == pytest.approx(0.8 if expected_true else 0.2)


@pytest.mark.parametrize("top", [
    [{"token": "We", "logprob": -0.1}, {"token": "True", "logprob": -2.5}],  # a reasoning token
    [{"token": "<think>", "logprob": -0.01}],
])
def test_a_reasoning_token_is_an_error_naming_the_causes_never_a_neutral_score(top):
    with pytest.raises(graphiti_client.RerankError) as exc:
        graphiti_client.chat_rerank_score("cc-rerank", top)
    text = str(exc.value)
    assert "cc-rerank" in text and "thinking" in text
    assert classify_failure(exc.value) == SEMANTIC


@pytest.mark.parametrize("top", [None, []])
def test_no_logprobs_is_an_error(top):
    with pytest.raises(graphiti_client.RerankError, match="logprobs"):
        graphiti_client.chat_rerank_score("cc-rerank", top)


# --- the chat client ---------------------------------------------------------------------


class _FakeCompletions:
    def __init__(self, answer, *, fail_on=None, delay=0.0):
        self.answer, self.fail_on, self.delay = answer, fail_on, delay
        self.requests: list[dict] = []
        self.in_flight = self.max_in_flight = 0
        self.cancelled = 0

    async def create(self, **kw):
        self.requests.append(kw)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(self.delay)
            passage = kw["messages"][1]["content"]
            if self.fail_on and self.fail_on in passage:
                raise self.fail_on_exc
            token, p = self.answer(passage)
            top = [SimpleNamespace(token=token, logprob=math.log(p))]
            content = [SimpleNamespace(top_logprobs=top)]
            return SimpleNamespace(choices=[SimpleNamespace(logprobs=SimpleNamespace(content=content))])
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        finally:
            self.in_flight -= 1


def _chat_client(completions, concurrency=3):
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return graphiti_client.ChatRerankClient(client, "cc-rerank", concurrency)


async def test_the_chat_reranker_scores_p_true_and_sends_upstreams_parameters():
    probs = {"alpha": ("True", 0.9), "beta": ("False", 0.7), "gamma": ("True", 0.6)}

    def answer(passage):
        return next(v for k, v in probs.items() if k in passage)

    fake = _FakeCompletions(answer)
    out = await _chat_client(fake).rank("who", ["beta", "gamma", "alpha"])
    assert [p for p, _ in out] == ["alpha", "gamma", "beta"]
    assert [round(s, 3) for _, s in out] == [0.9, 0.6, 0.3]
    assert len(fake.requests) == 3
    for req in fake.requests:
        assert req["model"] == "cc-rerank"
        assert req["temperature"] == 0 and req["max_tokens"] == 1
        assert req["logprobs"] is True and req["top_logprobs"] == 2
        assert "logit_bias" not in req
        assert req["messages"][0] == {"role": "system", "content": graphiti_client.CHAT_RERANK_SYSTEM}
    st = graphiti_client.rerank_stats()
    assert st["calls"] == 1 and st["failures"] == 0 and st["median_latency_s"] is not None


async def test_the_chat_reranker_holds_concurrency_to_the_semaphore_limit():
    fake = _FakeCompletions(lambda p: ("True", 0.5), delay=0.01)
    await _chat_client(fake, concurrency=2).rank("q", [f"p{i}" for i in range(9)])
    assert fake.max_in_flight == 2 and len(fake.requests) == 9


async def test_one_malformed_answer_fails_the_whole_rank_and_is_counted():
    fake = _FakeCompletions(lambda p: ("We", 0.9) if "bad" in p else ("True", 0.9))
    with pytest.raises(graphiti_client.RerankError, match="'We'"):
        await _chat_client(fake).rank("q", ["ok1", "bad", "ok2"])
    st = graphiti_client.rerank_stats()
    assert st["calls"] == 1 and st["failures"] == 1 and st["malformed"] == 1
    assert st["last_error"].startswith("RerankError:")


async def test_the_first_failure_cancels_the_rest_and_propagates_raw():
    request = httpx.Request("POST", f"{PROXY}/v1/chat/completions")
    import openai

    fake = _FakeCompletions(lambda p: ("True", 0.5), delay=0.05)
    fake.fail_on = "boom"
    fake.fail_on_exc = openai.APIConnectionError(request=request)
    with pytest.raises(openai.APIConnectionError) as exc:
        await _chat_client(fake, concurrency=8).rank("q", ["boom"] + [f"p{i}" for i in range(6)])
    assert classify_failure(exc.value) == TRANSIENT
    assert fake.in_flight == 0  # nothing left running behind the error
    assert graphiti_client.rerank_stats()["failures"] == 1


# --- the /rerank client: errors propagate, classified by the one taxonomy ---------------


def _mock_httpx(monkeypatch, handler):
    real = httpx.AsyncClient

    def factory(*a, **kw):
        kw["transport"] = httpx.MockTransport(handler)
        return real(*a, **kw)

    monkeypatch.setattr(graphiti_client.httpx, "AsyncClient", factory)


@pytest.mark.parametrize("status,expected", [
    (503, TRANSIENT), (502, TRANSIENT), (429, TRANSIENT), (408, TRANSIENT),
    # 403 is transient in the taxonomy (an edge firewall's temporary deny);
    # a key-scope 403 then fails after the read path's one retry, loudly.
    (403, TRANSIENT),
    (400, SEMANTIC), (401, SEMANTIC), (404, SEMANTIC),
])
async def test_an_http_failure_raises_and_is_classified_by_its_status(monkeypatch, status, expected):
    _mock_httpx(monkeypatch, lambda r: httpx.Response(status, json={"error": "nope"}))
    client = graphiti_client.RerankClient(PROXY, "sk-x", "cc-rerank")
    with pytest.raises(graphiti_client.RerankHTTPError) as exc:
        await client.rank("q", ["a", "b"])
    assert exc.value.status_code == status
    assert classify_failure(exc.value) == expected
    assert graphiti_client.rerank_stats()["failures"] == 1


async def test_a_connection_failure_propagates_as_transient(monkeypatch):
    def refuse(r):
        raise httpx.ConnectError("refused")

    _mock_httpx(monkeypatch, refuse)
    with pytest.raises(httpx.ConnectError) as exc:
        await graphiti_client.RerankClient(PROXY, "sk-x", "cc-rerank").rank("q", ["a"])
    assert classify_failure(exc.value) == TRANSIENT


@pytest.mark.parametrize("body", [
    {"unexpected": True},
    {"results": [{"index": 0, "relevance_score": 0.5}]},  # one of two candidates scored
    {"results": [{"index": 0, "relevance_score": 0.5}, {"index": 0, "relevance_score": 0.4}]},
    {"results": [{"index": 0}, {"index": 1}]},
])
async def test_a_malformed_rerank_answer_is_a_semantic_error_not_placeholder_scores(monkeypatch, body):
    _mock_httpx(monkeypatch, lambda r: httpx.Response(200, json=body))
    with pytest.raises(graphiti_client.RerankError) as exc:
        await graphiti_client.RerankClient(PROXY, "sk-x", "cc-rerank").rank("q", ["a", "b"])
    assert classify_failure(exc.value) == SEMANTIC
    st = graphiti_client.rerank_stats()
    assert st["failures"] == 1 and st["malformed"] == 1


# --- the agent read path decides, with its existing bounded retry -----------------------


class _SearchingGraphiti:
    """A Graphiti whose search_ asks the configured reranker, as the
    cross-encoder recipes do."""

    def __init__(self):
        self.searches = 0

    async def search_(self, query, config, group_ids=None, **kw):
        self.searches += 1
        await graphiti_client.build_reranker().rank(query, ["fact one", "fact two"])
        return SimpleNamespace(edges=[], nodes=[])


@pytest.fixture
def no_wait(monkeypatch):
    waits: list[float] = []

    async def _fake(delay):
        waits.append(delay)

    monkeypatch.setattr(tools, "_sleep", _fake)
    return waits


async def _search_with(monkeypatch, handler):
    monkeypatch.setattr(settings, "graph_rerank_alias", "cc-rerank")
    _mock_httpx(monkeypatch, handler)
    fake = _SearchingGraphiti()
    monkeypatch.setattr(graphiti_client, "get_graphiti", lambda: fake)

    async def groups(_agent):
        return ["central_command"]

    monkeypatch.setattr(graphiti, "groups_for", groups)
    out = await tools.search_knowledge_graph(None, "who leads")
    return fake, out


async def test_a_transient_reranker_failure_is_retried_once_then_reported(monkeypatch, no_wait):
    fake, out = await _search_with(monkeypatch, lambda r: httpx.Response(503, json={"error": "down"}))
    assert fake.searches == 2 and no_wait == [0.5]
    assert out == ("knowledge graph unavailable (RerankHTTPError) after 2 attempts; "
                   "proceed without it")


async def test_a_malformed_reranker_answer_is_reported_at_once(monkeypatch, no_wait):
    fake, out = await _search_with(monkeypatch, lambda r: httpx.Response(200, json={"oops": 1}))
    assert fake.searches == 1 and no_wait == []
    assert out == "knowledge graph unavailable (RerankError); proceed without it"


async def test_a_working_reranker_is_not_an_error(monkeypatch, no_wait):
    body = {"results": [{"index": 1, "relevance_score": 0.9}, {"index": 0, "relevance_score": 0.1}]}
    fake, out = await _search_with(monkeypatch, lambda r: httpx.Response(200, json=body))
    assert fake.searches == 1 and "unavailable" not in out
    assert json.dumps(graphiti_client.rerank_stats())  # serialisable for the Systems row
