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
import os

import httpx

from central_command.config import settings
from central_command.integrations import http as http_client

log = logging.getLogger(__name__)

# The neo4j user graphiti's deployment was created with; neo4j_reader uses the
# same literal.
NEO4J_USER = "neo4j"

# D3: with no `/rerank` alias configured, the stock server's factory built
# upstream's OpenAIRerankerClient on this alias. Parity, not a choice: release
# 1 changes no search behaviour (and the RRF recipes never call it).
RERANK_FALLBACK_ALIAS = "gpt-4.1-nano"

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


class RerankClient:
    """graphiti_core's cross-encoder interface over LiteLLM's `/rerank` (D3).

    Registered as a virtual subclass of `CrossEncoderClient` when the client
    is built (graphiti_core's `GraphitiClients` checks the type), so this
    module never imports graphiti_core at top level. Fail-soft on purpose,
    exactly as the retired server patch was: a reranker outage degrades the
    ORDER of results, never the search."""

    def __init__(self, base_url: str, api_key: str, model: str) -> None:
        self.url = f"{base_url.rstrip('/').removesuffix('/v1')}/rerank"
        self.api_key = api_key
        self.model = model

    async def rank(self, query: str, passages: list[str]) -> list[tuple[str, float]]:
        if not passages:
            return []
        try:
            async with httpx.AsyncClient(
                timeout=RERANK_TIMEOUT_S, **http_client.client_kwargs()
            ) as client:
                res = await client.post(
                    self.url,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={"model": self.model, "query": query, "documents": passages},
                )
                res.raise_for_status()
                results = res.json()["results"]
            ranked = [(passages[int(r["index"])], float(r["relevance_score"])) for r in results]
            ranked.sort(key=lambda t: t[1], reverse=True)
            return ranked
        except Exception as exc:  # noqa: BLE001 — fail-soft by design (D3)
            log.warning("graph rerank via %r failed, keeping input order: %s", self.model, exc)
            n = len(passages)
            return [(p, 1.0 - i / n) for i, p in enumerate(passages)]


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
    from graphiti_core.cross_encoder.openai_reranker_client import OpenAIRerankerClient
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
    if settings.graph_rerank_alias:
        CrossEncoderClient.register(RerankClient)
        cross_encoder = RerankClient(_proxy_root(), key, settings.graph_rerank_alias)
    else:
        cross_encoder = OpenAIRerankerClient(
            config=LLMConfig(api_key=key, model=RERANK_FALLBACK_ALIAS, base_url=base_v1),
            client=_openai_client(key, base_v1),
        )
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
    """Search picks the cross-encoder recipes only when a `/rerank` alias is
    set (D6); the fallback reranker exists for parity and is never asked."""
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
