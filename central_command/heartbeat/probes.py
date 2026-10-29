"""Cheap per-dependency health probes — the health gate in front of the retry
sweep (outage equivalence, slice 5).

**Why a gate at all.** There is no attempt limit (outage doctrine story 4): a
parked unit waits as long as the outage lasts, and its attempt count drives the
one backoff curve. Re-driving it into a dependency that is still down would only
re-park it one step further along that curve, so the sweep asks the dependency
first, and **a gated skip consumes NO attempt** and moves no `not_before`, by
construction: skipping means the driver is never called. Attempts are spent
only when the dependency LOOKED healthy and the real call still failed — and a
unit that keeps doing that is reported (`retry.failing_despite_probe`).

**Cheap.** One probe per dependency per sweep (`ProbeCache`), run CONCURRENTLY
(`ProbeCache.warm`), and a dependency nothing is parked on and nothing records
down is never probed at all — so the common tick makes zero network calls. Every
probe is a raw, UNAUTHENTICATED request (operator ruling 2026-10-27): the sweep
never spends a credential or a provider call asking whether a service is there.
A jira / confluence / forge probe is a bare GET of the configured instance's
status route; ANY answer short of the transient statuses (the same set the
failure taxonomy uses, 403 included) means the instance is up. Calendar and mail
ride the n8n façade, so they are asked of its health.

**The LLM probe is the one that costs anything** (a 1-token completion, fractions
of a cent). `litellm_check_model_health` is deliberately NOT used: its own
docstring says it makes a real provider call PER REGISTERED MODEL, which is a
health check that costs more than the work it guards.

Endpoints (the first three verified against the live Pi stack on 2026-07-31):
  n8n        GET  http://127.0.0.1:5678/healthz          -> 200 {"status":"ok"}
  calendar   the same n8n health (the façade host)
  graph      TCP  the bolt port of CC_NEO4J_URL          (since 2026-10-04: graphiti-core
             runs in-process, so the graph's dependency is Neo4j itself)
  litellm    GET  http://127.0.0.1:4000/health/liveliness -> 200 "I'm alive!"
  llm        POST a 1-token completion on the default alias
  embedder   POST one embedding of one word on CC_EMBED_ALIAS
  jira       GET  <CC_JIRA_BASE_URL>/status    (unauthenticated)
  confluence GET  <CC_CONFLUENCE_BASE_URL>/status
  forge      GET  each CC_FORGE_INSTANCES base url; up when any answers
  sandbox    GET  CC_SANDBOX_RUNNER_URL (no /health route; a bare GET proves it
             is listening, as `api/systems.py` does)
Each is derived from the SAME setting the real client uses, so a redeployment
that moves a service moves its probe with it. A service that is NOT configured
(no Jira URL, no forge instance, the test model for the model chain) has nothing to
wait for and probes UP.

The names are `contract/dependencies.DEPENDENCIES` (without `local`, which is
never probed); `PROBES` is keyed by exactly those, pinned by a parity test.
There is no UNKNOWN: a name with no probe cannot be recorded by any park.

Tier note: this module imports config, httpx and the standard library and
nothing else. It must stay that way — `heartbeat/` may never reach the gate
(tests/test_heartbeat.py).
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from central_command.config import settings

log = logging.getLogger(__name__)

# One constant, not a config knob: this is a liveness ping on the LAN (or a
# provider round-trip for a single token). Anything slower than this is, for the
# sweep's purposes, down — and the sweep's own cadence is the schedule row.
PROBE_TIMEOUT_SECONDS = 5.0

# The dependency names (the vocabulary lives in `contract/dependencies.py`,
# which this module may not import; a parity test keeps the two equal). Strings
# rather than an enum so a result dict is JSON.
LLM = "llm"
EMBEDDER = "embedder"
GRAPH = "graph"
LITELLM = "litellm"
N8N = "n8n"
CALENDAR = "calendar"
JIRA = "jira"
CONFLUENCE = "confluence"
FORGE = "forge"
SANDBOX = "sandbox"

# A response with one of these statuses is the dependency being unavailable, not
# answering. The same set as `contract.failures.TRANSIENT_STATUS` (pinned equal
# by a test), copied because this module may import config, httpx and the
# standard library only.
_DOWN_STATUS = frozenset({403, 408, 429, 500, 502, 503, 504, 529})


def _root(url: str) -> str:
    """The scheme://host:port root of a service URL, from the setting the real
    client uses — so the probe cannot drift away from the thing it probes."""
    parsed = httpx.URL(url)
    return f"{parsed.scheme}://{parsed.netloc.decode()}"


async def _get_ok(url: str) -> bool:
    async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_SECONDS) as client:
        resp = await client.get(url)
    return resp.status_code == 200


async def _answers(url: str) -> bool:
    """An UNAUTHENTICATED GET: any response that is not a transient status means
    the service is there and talking (a 401 or 404 is an answer). Raises on a
    transport failure — the cache reads a raise as DOWN."""
    async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_SECONDS) as client:
        resp = await client.get(url)
    return resp.status_code not in _DOWN_STATUS


async def probe_n8n() -> bool:
    """The n8n façade host (the email and calendar façades ride it)."""
    return await _get_ok(_root(settings.email_facade_url) + "/healthz")


async def probe_calendar() -> bool:
    """The calendar façade is a webhook on the same n8n host; its health IS the
    host's. (When the native Exchange client is configured the façade routes
    there instead, and this still asks n8n — a stated limit, see the epic.)"""
    return await _get_ok(_root(settings.calendar_facade_url) + "/healthz")


async def probe_jira() -> bool:
    """The configured Jira instance's status route, unauthenticated. Unset means
    nothing to wait for."""
    if not settings.jira_base_url:
        return True
    return await _answers(settings.jira_base_url.rstrip("/") + "/status")


async def probe_confluence() -> bool:
    if not settings.confluence_base_url:
        return True
    return await _answers(settings.confluence_base_url.rstrip("/") + "/status")


async def probe_forge() -> bool:
    """Every configured forge instance is asked at once; the forge is up when
    ANY answers (a unit parked on one instance re-parks cheaply if it was the
    other that fell). No instance configured: nothing to wait for."""
    raw = [e.split(":", 1)[1].strip() for e in settings.forge_instances.split(",")
           if "=" in e and ":" in e.split("=", 1)[1]]
    if not raw:
        return True

    async def _one(url: str) -> bool:
        try:
            return await _answers(url)
        except Exception:  # noqa: BLE001 — an unreachable instance is one "no"
            return False

    return any(await asyncio.gather(*(_one(u) for u in raw)))


async def probe_sandbox() -> bool:
    """The sandbox runner: it has no /health route (api/systems.py does the same
    bare GET), so reaching it at all is the proof."""
    if not settings.sandbox_runner_url:
        return True
    async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_SECONDS) as client:
        await client.get(settings.sandbox_runner_url)
    return True


async def probe_embedder() -> bool:
    """One embedding of a one-word input on the configured alias, through the
    same base URL and key the graph's embedder uses. The test model has no provider;
    an unconfigured live one is DOWN. Only a transient status counts as down."""
    if settings.test_model:
        return True
    base_url, api_key = settings.llm_base_url, settings.llm_api_key
    if not base_url or not api_key:
        return False
    async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_SECONDS) as client:
        resp = await client.post(
            base_url.rstrip("/") + "/v1/embeddings",
            headers={"Authorization": f"Bearer {api_key}",
                     "content-type": "application/json"},
            json={"model": settings.embed_alias, "input": "ping"},
        )
    return resp.status_code not in _DOWN_STATUS


async def probe_graph() -> bool:
    """The knowledge graph: graphiti-core runs in this process, so what a
    retried `graph.*` action needs is Neo4j's bolt port (curation writes over
    it; an add_episode only enqueues, and the ingest worker retries its own
    transient failures). A TCP connect — no driver import, per the tier note."""
    parsed = httpx.URL(settings.neo4j_url)
    host, port = parsed.host, parsed.port or 7687
    try:
        _reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=PROBE_TIMEOUT_SECONDS
        )
    except (OSError, asyncio.TimeoutError):
        return False
    writer.close()
    try:
        await writer.wait_closed()
    except OSError:
        pass
    return True


async def probe_litellm() -> bool:
    """The LiteLLM ADMIN plane — the same deployment agent traffic proxies
    through, asked the cheapest question it answers."""
    return await _get_ok(settings.llm_proxy_base_url.rstrip("/") + "/health/liveliness")


async def probe_llm() -> bool:
    """The agent model chain: a 1-token completion against the configured base
    URL and default alias (`cc-default`), i.e. exactly the route a retried agent
    run would take — proxy, routing and provider together.

    Under the test model there is no provider to ask and no run that could fail on one,
    so the answer is yes. An UNCONFIGURED live provider is reported DOWN: a
    retry would raise the moment it resolved a model, and burning an attempt on
    that helps nobody.

    A response the chain actually produced means the chain is up, even when it
    is a refusal (a 4xx about the request). Only 429 and 5xx — the shapes the
    failure taxonomy calls transient — count as down.
    """
    if settings.test_model:
        return True
    base_url, api_key = settings.llm_base_url, settings.llm_api_key
    if not base_url or not api_key:
        return False
    model = settings.default_model.split(":", 1)[-1]
    async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_SECONDS) as client:
        resp = await client.post(
            base_url.rstrip("/") + "/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}",
                     "content-type": "application/json"},
            json={"model": model, "max_tokens": 1,
                  "messages": [{"role": "user", "content": "ping"}]},
        )
    return resp.status_code != 429 and resp.status_code < 500


PROBES = {
    LLM: probe_llm,
    EMBEDDER: probe_embedder,
    GRAPH: probe_graph,
    LITELLM: probe_litellm,
    N8N: probe_n8n,
    CALENDAR: probe_calendar,
    JIRA: probe_jira,
    CONFLUENCE: probe_confluence,
    FORGE: probe_forge,
    SANDBOX: probe_sandbox,
}


class ProbeCache:
    """One answer per dependency per sweep.

    `warm()` probes a set of names CONCURRENTLY (the sweep calls it once, with
    every name a waiting unit or a recorded-down row needs), so a sweep over
    five dependencies costs the slowest probe, not the sum. Nothing is probed
    that nobody asked about, so a tick with nothing parked and nothing recorded
    down makes no network calls at all. A probe that RAISES (or times out) is a
    down dependency — an unreachable service is the case the gate exists for,
    and letting the exception escape would fail the whole sweep instead of
    deferring one worklist.
    """

    def __init__(self) -> None:
        self._results: dict[str, bool] = {}
        self._errors: dict[str, str] = {}

    async def _probe_one(self, dependency: str) -> None:
        probe = PROBES.get(dependency)
        if probe is None:  # `local`, or a name nothing can record
            self._results[dependency] = True
            return
        try:
            ok = bool(await asyncio.wait_for(probe(), PROBE_TIMEOUT_SECONDS + 1))
            if not ok:
                self._errors[dependency] = "probe answered unhealthy"
        except Exception as e:  # noqa: BLE001 — an unreachable probe IS the signal
            log.info("probe %s failed: %s", dependency, e)
            ok = False
            self._errors[dependency] = (str(e) or type(e).__name__)[:300]
        self._results[dependency] = ok

    async def warm(self, dependencies) -> None:
        """Probe every not-yet-probed name in `dependencies`, all at once."""
        todo = [d for d in dict.fromkeys(dependencies or ())
                if d and d not in self._results]
        if todo:
            await asyncio.gather(*(self._probe_one(d) for d in todo))

    async def healthy(self, dependency: str | None) -> bool:
        if dependency is None:
            return True
        if dependency not in self._results:
            await self._probe_one(dependency)
        return self._results[dependency]

    @property
    def results(self) -> dict[str, bool]:
        """What was probed this sweep, and what it said — for the sweep result,
        so a skipped tick that IS material says which dependency was down."""
        return dict(self._results)

    @property
    def errors(self) -> dict[str, str]:
        """Why each DOWN probe said no (an exception's text, or "unhealthy")."""
        return dict(self._errors)
