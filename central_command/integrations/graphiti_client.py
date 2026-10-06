"""The one place a `graphiti_core.Graphiti` object is built (design record
2026-10-04, D2-D3).

Three rules shape this module:

* **graphiti_core is imported LAZILY, here and nowhere at module top level.**
  Importing it runs `load_dotenv()`, reads `SEMAPHORE_LIMIT` into a module
  constant, and arms a PostHog telemetry event for the first `Graphiti(...)`.
  `_prepare_environment()` sets `GRAPHITI_TELEMETRY_ENABLED=false` and
  `SEMAPHORE_LIMIT` BEFORE the first import, which only works if nothing
  imported at application start imports the package first. Nothing ever
  exports `OPENAI_*` into the process (config.py explains the collision);
  base URL and key are constructor arguments.
* **Every client is passed in, and missing configuration fails loudly.**
  `Graphiti.__init__` silently builds an OpenAI client wanting an OpenAI key
  for any client left as None. `GraphitiNotConfigured` names the missing
  setting instead.
* **Built per event loop**, keyed the way `neo4j_reader._get_driver` keys its
  driver: an async driver's pool holds futures bound to the loop that made it.

Construction never writes. `Neo4jDriver.__init__` schedules the index DDL as
a task when a loop is running; we cancel that task before it can run and own
the DDL in `ensure_indices()`, awaited once at application start. So a READ
(the test suite's live reads included) can never be the thing that issues
schema writes to a graph.

The runtime tier may NOT import this module (tests/test_governance.py): the
object built here carries write methods. `integrations/graphiti.py` reaches it
through a function-local import for its reads.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import statistics
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx
import openai

from central_command.config import settings
from central_command.integrations import http as http_client

log = logging.getLogger(__name__)

# The neo4j user graphiti's deployment was created with; neo4j_reader uses the
# same literal.
NEO4J_USER = "neo4j"

# The per-REQUEST timeout of a `/rerank` call: one HTTP request, which RAISES
# when it expires (httpx.ReadTimeout — transient, so the agent read path's
# bounded retry tries once more and then reports the failure). It is not a
# budget that abandons work: there is no whole-search budget and no fallback
# order (operator's rule, 2026-10-05 — routing and retries are LiteLLM's and
# the existing read-retry seam's; a reranker that fails raises).
RERANK_TIMEOUT_S = 30.0

_graphiti = None
_graphiti_loop = None


class GraphitiNotConfigured(RuntimeError):
    """A setting the graph client needs is empty. Never a fallback."""


def _prepare_environment() -> None:
    """The two process-environment knobs graphiti_core reads at import /
    first construction. Set unconditionally (our setting is the source of
    truth). Harmless after the first import, but only EFFECTIVE before it."""
    os.environ["GRAPHITI_TELEMETRY_ENABLED"] = "false"
    os.environ["SEMAPHORE_LIMIT"] = str(int(settings.graph_semaphore_limit))


def _proxy_v1() -> str:
    """CC_LLM_BASE_URL is the proxy ROOT (runtime/models.py appends /v1 the
    same way); the OpenAI clients graphiti_core uses add /chat/completions,
    /embeddings to it."""
    base = settings.llm_base_url.rstrip("/")
    return base if base.endswith("/v1") else base + "/v1"


def _proxy_root() -> str:
    return settings.llm_base_url.rstrip("/").removesuffix("/v1")


def _require_config() -> None:
    missing = [
        name
        for name, value in (
            ("CC_LLM_BASE_URL", settings.llm_base_url),
            ("CC_LLM_API_KEY", settings.llm_api_key),
            ("CC_GRAPH_LLM_ALIAS", settings.graph_llm_alias),
            ("CC_EMBED_ALIAS", settings.embed_alias),
            ("CC_EMBED_DIM", settings.embed_dim),
            ("CC_NEO4J_URL", settings.neo4j_url),
            ("CC_NEO4J_PASSWORD", settings.neo4j_password),
        )
        if not value
    ]
    if missing:
        raise GraphitiNotConfigured(
            "the knowledge-graph client is not configured — set "
            f"{', '.join(missing)} in .env. graphiti-core would otherwise build "
            "a default OpenAI client wanting an OpenAI key; it is refused instead."
        )


# --- the reranker (design record 2026-10-04, D3 as rebuilt in v2.62.0) -----------
# ONE optional alias (CC_GRAPH_RERANK_ALIAS, `cc-rerank` by convention) and the
# KIND of model behind it (CC_GRAPH_RERANK_KIND):
#   rerank  a dedicated reranker behind LiteLLM's `/rerank` (RerankClient)
#   chat    an ordinary chat model asked one True/False question per candidate,
#           scored by the probability of "True" (ChatRerankClient)
#   ""      with the alias set: `rerank` — what every install that set the alias
#           before the kind existed means by it
# No alias: no reranker; search runs the RRF recipes and the constructor gets
# NoReranker, which refuses to be called.
#
# Neither client falls back to an unranked order, and neither has a time budget
# or a retry loop of its own (operator's rule, 2026-10-05): routing and retries
# belong to LiteLLM and to the read path's bounded retry (runtime/tools.py
# `_read_with_retry`); a reranker that fails RAISES out of `search_()`, and the
# failure taxonomy (contract/failures.py) decides what is retried. Every
# failure is counted (`rerank_stats`) for the Systems row and the self-check.

RERANK_KINDS = ("rerank", "chat")

# upstream's OpenAIRerankerClient question, VERBATIM (graphiti-core 0.30.2,
# cross_encoder/openai_reranker_client.py). tests/test_graph_rerank.py renders
# upstream's own f-string from the INSTALLED source and compares: the measured
# benchmark (2026-10-05) used these bytes, and a change upstream is a failing
# test for a human to judge, not a silent drift.
CHAT_RERANK_SYSTEM = (
    "You are an expert tasked with determining whether the passage is relevant to the query"
)
_CHAT_RERANK_INDENT = " " * 27


def chat_rerank_user_prompt(query: str, passage: str) -> str:
    i = _CHAT_RERANK_INDENT
    return (
        f'\n{i}Respond with "True" if PASSAGE is relevant to QUERY and "False" otherwise.\n'
        f"{i}<PASSAGE>\n{i}{passage}\n{i}</PASSAGE>\n"
        f"{i}<QUERY>\n{i}{query}\n{i}</QUERY>\n{i}"
    )


class RerankError(RuntimeError):
    """The reranker answered, but not with something that can rank — a
    malformed `/rerank` body, or a chat answer that is neither True nor False.
    Semantic in the failure taxonomy (the same request gets the same answer),
    so the read path reports it at once instead of retrying it."""


class RerankHTTPError(openai.APIStatusError):
    """A non-2xx answer from LiteLLM's `/rerank`. An `openai.APIStatusError` on
    purpose: graphiti-core's other LiteLLM calls (extraction, embedding, the
    chat reranker) raise that type, so `contract.classify_failure` judges this
    one by the same status rule — 403/408/429/5xx transient, any other 4xx
    semantic — with no second policy for reranking."""


# --- counters (in-process; no table) --------------------------------------------
_STATS_WINDOW = 50


@dataclass
class _RerankStats:
    calls: int = 0
    failures: int = 0
    malformed: int = 0
    last_error: str | None = None
    last_error_at: str | None = None
    latencies: deque = field(default_factory=lambda: deque(maxlen=_STATS_WINDOW))


_stats = _RerankStats()


def _record_ok(started: float) -> None:
    _stats.calls += 1
    _stats.latencies.append(time.monotonic() - started)


def _record_failure(exc: BaseException) -> None:
    _stats.calls += 1
    _stats.failures += 1
    if isinstance(exc, RerankError):
        _stats.malformed += 1
    text = (str(exc).strip().splitlines() or [""])[0][:300]
    _stats.last_error = f"{type(exc).__name__}: {text}" if text else type(exc).__name__
    _stats.last_error_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def rerank_stats() -> dict:
    """This process's reranker record: rank() calls, how many RAISED, how many
    of those were malformed answers, the last error, and the median latency of
    the recent successful calls (seconds, or None)."""
    lat = sorted(_stats.latencies)
    return {
        "calls": _stats.calls,
        "failures": _stats.failures,
        "malformed": _stats.malformed,
        "last_error": _stats.last_error,
        "last_error_at": _stats.last_error_at,
        "median_latency_s": round(statistics.median(lat), 3) if lat else None,
    }


def reset_rerank_stats() -> None:
    global _stats
    _stats = _RerankStats()


async def _counted(coro_fn, started: float):
    try:
        out = await coro_fn()
    except BaseException as exc:
        if not isinstance(exc, asyncio.CancelledError):
            _record_failure(exc)
        raise
    _record_ok(started)
    return out


class RerankClient:
    """graphiti_core's cross-encoder interface over LiteLLM's `/rerank`.

    Registered as a virtual subclass of `CrossEncoderClient` when the client
    is built (graphiti_core's `GraphitiClients` checks the type), so this
    module never imports graphiti_core at top level. Every candidate must come
    back scored exactly once; anything else is a RerankError, and an HTTP
    failure is a RerankHTTPError — the search fails, it is never silently
    reordered or shortened."""

    def __init__(self, base_url: str, api_key: str, model: str) -> None:
        self.url = f"{base_url.rstrip('/').removesuffix('/v1')}/rerank"
        self.api_key = api_key
        self.model = model

    async def rank(self, query: str, passages: list[str]) -> list[tuple[str, float]]:
        if not passages:
            return []
        return await _counted(lambda: self._rank(query, passages), time.monotonic())

    async def _rank(self, query: str, passages: list[str]) -> list[tuple[str, float]]:
        async with httpx.AsyncClient(
            timeout=RERANK_TIMEOUT_S, **http_client.client_kwargs()
        ) as client:
            res = await client.post(
                self.url,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"model": self.model, "query": query, "documents": passages},
            )
        if res.status_code >= 400:
            try:
                body = res.json()
            except ValueError:
                body = res.text[:300]
            raise RerankHTTPError(
                f"/rerank via {self.model!r} answered HTTP {res.status_code}: {str(body)[:300]}",
                response=res, body=body,
            )
        try:
            results = res.json()["results"]
            scored = {int(r["index"]): float(r["relevance_score"]) for r in results}
        except (ValueError, KeyError, TypeError) as exc:
            raise RerankError(
                f"/rerank via {self.model!r} answered without a results list "
                f"({type(exc).__name__}) — is the alias a rerank-shaped endpoint?"
            ) from exc
        if sorted(scored) != list(range(len(passages))) or len(results) != len(passages):
            raise RerankError(
                f"/rerank via {self.model!r} scored {len(results)} result(s) for "
                f"{len(passages)} candidate(s) — every candidate must be scored exactly once"
            )
        ranked = [(passages[i], s) for i, s in scored.items()]
        ranked.sort(key=lambda t: t[1], reverse=True)
        return ranked


def _answer_token(token: str) -> str:
    """A top-logprob token as a word: case-folded, with the leading space or
    the SentencePiece/BPE word markers (`▁`, `Ġ`) removed, first word only."""
    t = (token or "").replace("▁", " ").replace("Ġ", " ").strip().lower()
    return t.split(" ")[0] if t else ""


def chat_rerank_score(model: str, top_logprobs) -> float:
    """P(True) from the answer's first token, upstream's scoring: the top
    token's probability when it says True, one minus it when it says False.

    `top_logprobs` is the first content token's alternatives (objects or dicts
    with `token` and `logprob`). An absent list, or a top token that is
    neither True nor False — what a model that THINKS first emits as its one
    token ("We", "<think>") — is a RerankError naming the alias and the two
    usual causes. Never a neutral score: that would be a silently random
    ordering."""
    if not top_logprobs:
        raise RerankError(
            f"the chat reranker {model!r} returned no logprobs — the endpoint behind the "
            "alias must support logprobs/top_logprobs on chat completions"
        )
    top = top_logprobs[0]
    token = top.get("token") if isinstance(top, dict) else getattr(top, "token", None)
    logprob = top.get("logprob") if isinstance(top, dict) else getattr(top, "logprob", None)
    word = _answer_token(token or "")
    if word not in ("true", "false") or logprob is None:
        raise RerankError(
            f"the chat reranker {model!r} answered {token!r} instead of True/False — a "
            "reasoning model whose thinking is not disabled on the alias answers with a "
            "reasoning token (disable thinking on the alias, e.g. chat_template_kwargs "
            "enable_thinking false on a llama.cpp backend), or the model ignores the question"
        )
    p = math.exp(float(logprob))
    return p if word == "true" else 1.0 - p


class ChatRerankClient:
    """graphiti_core's cross-encoder interface over an ordinary CHAT model:
    upstream's OpenAIRerankerClient question (verbatim — see
    `chat_rerank_user_prompt`), one request per candidate, temperature 0,
    `max_tokens=1`, `logprobs` with `top_logprobs=2`, score = P(True).

    Differences from upstream, each deliberate:
    * no `logit_bias`: upstream biases two OpenAI-tokenizer ids (`6432`,
      `7983`), which are unrelated tokens on every other tokenizer, and some
      gateways refuse the parameter;
    * the answer is read robustly (case, leading space/word markers) and a
      malformed one RAISES instead of being scored as "False";
    * at most CC_GRAPH_SEMAPHORE_LIMIT requests at once, and the first failure
      cancels the rest and propagates — no candidate is silently skipped.
    The AsyncOpenAI client is `_openai_client`'s, so it carries the same trust
    settings and the same SDK defaults as the graph's other LiteLLM calls."""

    def __init__(self, client, model: str, concurrency: int) -> None:
        self.client = client
        self.model = model
        self.concurrency = max(1, int(concurrency))

    async def rank(self, query: str, passages: list[str]) -> list[tuple[str, float]]:
        if not passages:
            return []
        return await _counted(lambda: self._rank(query, passages), time.monotonic())

    async def _one(self, sem: asyncio.Semaphore, query: str, passage: str) -> float:
        async with sem:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": CHAT_RERANK_SYSTEM},
                    {"role": "user", "content": chat_rerank_user_prompt(query, passage)},
                ],
                temperature=0,
                max_tokens=1,
                logprobs=True,
                top_logprobs=2,
            )
        choice = (response.choices or [None])[0]
        content = getattr(getattr(choice, "logprobs", None), "content", None) or []
        top = getattr(content[0], "top_logprobs", None) if content else None
        return chat_rerank_score(self.model, top)

    async def _rank(self, query: str, passages: list[str]) -> list[tuple[str, float]]:
        sem = asyncio.Semaphore(self.concurrency)
        tasks = [asyncio.ensure_future(self._one(sem, query, p)) for p in passages]
        try:
            scores = await asyncio.gather(*tasks)
        except BaseException:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        ranked = list(zip(passages, scores, strict=True))
        ranked.sort(key=lambda t: t[1], reverse=True)
        return ranked


class NoReranker:
    """The constructor's cross-encoder when no reranker is configured. Search
    then runs the RRF recipes, which never call it; if anything ever does,
    that is a defect, and it says so rather than returning an unranked list."""

    async def rank(self, query: str, passages: list[str]) -> list[tuple[str, float]]:
        raise RuntimeError(
            "no reranker is configured (CC_GRAPH_RERANK_ALIAS is empty), yet a "
            "cross-encoder recipe asked for one — graph search must use the RRF recipes"
        )


def rerank_kind() -> str | None:
    """'rerank' | 'chat' for a configured reranker, None for none. An unknown
    CC_GRAPH_RERANK_KIND is GraphitiNotConfigured, naming the setting."""
    if not settings.graph_rerank_alias:
        return None
    kind = (settings.graph_rerank_kind or "").strip().lower() or "rerank"
    if kind not in RERANK_KINDS:
        raise GraphitiNotConfigured(
            f"CC_GRAPH_RERANK_KIND={settings.graph_rerank_kind!r} is not a reranker kind — "
            "set it to rerank (a dedicated /rerank model), chat (a chat model with "
            "logprobs and thinking off), or leave it empty (= rerank)"
        )
    return kind


def build_reranker(kind: str | None = None, alias: str | None = None):
    """The cross-encoder for `kind`/`alias` (default: the configured ones). The
    self-check and the benchmark build their candidates through this too."""
    if alias is None:
        alias = settings.graph_rerank_alias
        kind = rerank_kind() if kind is None else kind
    if not alias or kind is None:
        return NoReranker()
    if kind == "rerank":
        return RerankClient(_proxy_root(), settings.llm_api_key, alias)
    if kind == "chat":
        return ChatRerankClient(_openai_client(settings.llm_api_key, _proxy_v1()), alias,
                                settings.graph_semaphore_limit)
    raise GraphitiNotConfigured(f"unknown reranker kind {kind!r} (CC_GRAPH_RERANK_KIND)")


def _openai_client(api_key: str, base_url: str):
    """One AsyncOpenAI per client, honouring the integration trust settings
    (CC_CA_BUNDLE / CC_CLIENT_CERT) the other LiteLLM calls already use."""
    from openai import AsyncOpenAI, DefaultAsyncHttpxClient

    kwargs = http_client.client_kwargs()
    if kwargs:
        return AsyncOpenAI(api_key=api_key, base_url=base_url,
                           http_client=DefaultAsyncHttpxClient(**kwargs))
    return AsyncOpenAI(api_key=api_key, base_url=base_url)


def _build():
    _require_config()
    _prepare_environment()
    from graphiti_core import Graphiti, helpers
    from graphiti_core.cross_encoder.client import CrossEncoderClient
    from graphiti_core.driver.neo4j_driver import Neo4jDriver
    from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
    from graphiti_core.llm_client.config import LLMConfig
    from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient

    if helpers.SEMAPHORE_LIMIT != int(settings.graph_semaphore_limit):
        # graphiti_core was imported before this module could set the knob.
        log.warning(
            "graphiti_core SEMAPHORE_LIMIT is %s, not CC_GRAPH_SEMAPHORE_LIMIT=%s — "
            "something imported graphiti_core before integrations.graphiti_client",
            helpers.SEMAPHORE_LIMIT, settings.graph_semaphore_limit,
        )

    base_v1 = _proxy_v1()
    key = settings.llm_api_key

    llm = OpenAIGenericClient(
        config=LLMConfig(
            api_key=key, model=settings.graph_llm_alias, base_url=base_v1,
            temperature=settings.graph_llm_temperature,
        ),
        client=_openai_client(key, base_v1),
        # The constructor argument, not the config field: it overrides it.
        max_tokens=int(settings.graph_llm_max_tokens),
        structured_output_mode="json_schema",
    )
    embedder = OpenAIEmbedder(
        OpenAIEmbedderConfig(
            embedding_model=settings.embed_alias, api_key=key, base_url=base_v1,
            embedding_dim=int(settings.embed_dim),
        ),
        client=_openai_client(key, base_v1),
    )
    for cls in (RerankClient, ChatRerankClient, NoReranker):
        CrossEncoderClient.register(cls)
    cross_encoder = build_reranker()
    driver = Neo4jDriver(settings.neo4j_url, NEO4J_USER, settings.neo4j_password)
    # Cancel the constructor's scheduled index DDL before it runs (see the
    # module docstring); `ensure_indices()` owns it.
    init_task = getattr(driver, "_init_task", None)
    if init_task is not None:
        init_task.cancel()
        driver._init_task = None
    return Graphiti(
        graph_driver=driver,
        llm_client=llm,
        embedder=embedder,
        cross_encoder=cross_encoder,
        max_coroutines=int(settings.graph_semaphore_limit),
    )


def get_graphiti():
    """The Graphiti object for the RUNNING loop. Raises GraphitiNotConfigured
    on missing settings (never builds a default client)."""
    global _graphiti, _graphiti_loop
    loop = asyncio.get_running_loop()
    if _graphiti is None or _graphiti_loop is not loop:
        _graphiti = _build()
        _graphiti_loop = loop
    return _graphiti


def reranker_configured() -> bool:
    """Search picks the cross-encoder recipes when a reranker of EITHER kind is
    configured (D6); with none, the RRF recipes, which never call NoReranker."""
    return bool(settings.graph_rerank_alias)


def text_source():
    """`EpisodeType.text`, imported after the environment is prepared."""
    _prepare_environment()
    from graphiti_core.nodes import EpisodeType

    return EpisodeType.text


async def ensure_indices() -> None:
    """graphiti_core's indexes and constraints — `IF NOT EXISTS`, idempotent.
    Awaited once at application start (the ingest worker's start)."""
    await get_graphiti().build_indices_and_constraints()


def patch_state() -> dict[str, str]:
    """Per carried upstream fix: 'patched' | 'pristine' | 'unknown'.
    Read-only; never imports graphiti_core (D7)."""
    from central_command.integrations import graphiti_patches

    return graphiti_patches.patch_state()


def patches_ok() -> bool:
    """True when every carried fix is present in the INSTALLED package. The
    ingest worker refuses to extract without them; reads are unaffected."""
    state = patch_state()
    return bool(state) and all(v == "patched" for v in state.values())
