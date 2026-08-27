"""Cheap per-dependency health probes — the health gate in front of the retry
sweep (outage equivalence, slice 5).

**Why a gate at all.** Every retryable unit carries an attempt budget (5) whose
job is to stop a silent infinite loop. A long outage must not spend it: at a
5-minute cadence a two-hour outage would exhaust every parked unit inside 25
minutes and promote a storm of operator items — the exact opposite of "wait as
long as the outage lasts". So the sweep asks the dependency first, and **a gated
skip consumes NO attempt**, by construction: skipping means the driver is never
called, so nothing can spend one. Attempts are spent only when the dependency
LOOKED healthy and the real call still failed — genuine flappiness, which is
what the budget is for.

**Cheap, and only when needed.** One probe per dependency per sweep
(`ProbeCache`), and a dependency nothing is parked on is never probed at all —
so the common tick, with nothing parked, makes zero network calls.

**The LLM probe is the one that costs anything** (a 1-token completion, fractions
of a cent). `litellm_check_model_health` is deliberately NOT used: its own
docstring says it makes a real provider call PER REGISTERED MODEL, which is a
health check that costs more than the work it guards.

Endpoints verified against the live Pi stack on 2026-07-31 (read-only GETs):
  n8n       GET  http://127.0.0.1:5678/healthz          -> 200 {"status":"ok"}
  graphiti  GET  http://127.0.0.1:8000/health           -> 200 {"status":"healthy",...}
  litellm   GET  http://127.0.0.1:4000/health/liveliness -> 200 "I'm alive!"
Each is derived from the SAME setting the real client uses, so a redeployment
that moves a service moves its probe with it.

UNKNOWN is not DOWN: a dependency with no probe defined (`dependency_for_
capability` returning None) is attempted anyway — there the attempt IS the
probe, bounded by the budget.

Tier note: this module imports config and httpx and nothing else. It must stay
that way — `heartbeat/` may never reach the gate (tests/test_heartbeat.py).
"""

from __future__ import annotations

import logging

import httpx

from central_command.config import settings

log = logging.getLogger(__name__)

# One constant, not a config knob: this is a liveness ping on the LAN (or a
# provider round-trip for a single token). Anything slower than this is, for the
# sweep's purposes, down — and the sweep's own cadence is the schedule row.
PROBE_TIMEOUT_SECONDS = 5.0

# The dependency names. Strings rather than an enum so a result dict is JSON.
LLM = "llm"
N8N = "n8n"
GRAPHITI = "graphiti"
LITELLM = "litellm"

# Which system a FAILED capability's retry depends on. Keyed by the capability's
# prefix (`jira.set_due_date` -> `jira`), since that is what the Executor's
# dispatch table is organised by.
#
# `charter.*` is deliberately absent: it writes to Postgres, which the sweep is
# already talking to — no probe, attempt anyway.
#
# `task.*` gates on the LLM, not n8n: its executor handler
# (`routes.create_and_run_task`) is a local DB write plus an agent run — the
# slice-5 plan said n8n and the plan was WRONG about where that handler's
# dependency lives (implementer flag, corrected in review 2026-07-31).
CAPABILITY_DEPENDENCY: dict[str, str] = {
    "jira": N8N,
    "task": LLM,
    "graph": GRAPHITI,
    "litellm": LITELLM,
}


def dependency_for_capability(capability: str | None) -> str | None:
    """The dependency a retry of `capability` needs, or None for "no probe
    defined — attempt anyway". Tolerates the `@version` suffix and None."""
    if not capability:
        return None
    prefix = str(capability).split("@", 1)[0].split(".", 1)[0]
    return CAPABILITY_DEPENDENCY.get(prefix)


def _root(url: str) -> str:
    """The scheme://host:port root of a service URL, from the setting the real
    client uses — so the probe cannot drift away from the thing it probes."""
    parsed = httpx.URL(url)
    return f"{parsed.scheme}://{parsed.netloc.decode()}"


async def _get_ok(url: str) -> bool:
    async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_SECONDS) as client:
        resp = await client.get(url)
    return resp.status_code == 200


async def probe_n8n() -> bool:
    """The n8n façade host (Jira writes and the email façade both ride it)."""
    return await _get_ok(_root(settings.n8n_jira_url) + "/healthz")


async def probe_graphiti() -> bool:
    """The Graphiti MCP server (and, through it, Neo4j)."""
    return await _get_ok(_root(settings.graphiti_mcp_url) + "/health")


async def probe_litellm() -> bool:
    """The LiteLLM ADMIN plane — the same deployment agent traffic proxies
    through, asked the cheapest question it answers."""
    return await _get_ok(settings.llm_proxy_base_url.rstrip("/") + "/health/liveliness")


async def probe_llm() -> bool:
    """The agent model chain: a 1-token completion against the configured base
    URL and default alias (`cc-default`), i.e. exactly the route a retried agent
    run would take — proxy, routing and provider together.

    In demo mode there is no provider to ask and no run that could fail on one,
    so the answer is yes. An UNCONFIGURED live provider is reported DOWN: a
    retry would raise the moment it resolved a model, and burning an attempt on
    that helps nobody.

    A response the chain actually produced means the chain is up, even when it
    is a refusal (a 4xx about the request). Only 429 and 5xx — the shapes the
    failure taxonomy calls transient — count as down.
    """
    if settings.demo_mode:
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
    N8N: probe_n8n,
    GRAPHITI: probe_graphiti,
    LITELLM: probe_litellm,
}


class ProbeCache:
    """One answer per dependency per sweep.

    Deliberately lazy: nothing is probed until a parked unit actually gates on
    it, so a tick with nothing parked makes no network calls at all. A probe
    that RAISES is a down dependency — an unreachable service is the case the
    gate exists for, and letting the exception escape would fail the whole
    sweep instead of deferring one worklist.
    """

    def __init__(self) -> None:
        self._results: dict[str, bool] = {}

    async def healthy(self, dependency: str | None) -> bool:
        if dependency is None:
            return True  # UNKNOWN is not DOWN: the attempt is the probe.
        if dependency in self._results:
            return self._results[dependency]
        probe = PROBES.get(dependency)
        if probe is None:
            self._results[dependency] = True
            return True
        try:
            ok = bool(await probe())
        except Exception as e:  # noqa: BLE001 — an unreachable probe IS the signal
            log.info("probe %s failed: %s", dependency, e)
            ok = False
        self._results[dependency] = ok
        return ok

    @property
    def results(self) -> dict[str, bool]:
        """What was probed this sweep, and what it said — for the sweep result,
        so a skipped tick that IS material says which dependency was down."""
        return dict(self._results)
