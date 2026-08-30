"""Native LiteLLM admin client — Central Command's window into the self-hosted proxy.

The toolbox rule (D23): a plain token-REST admin API belongs in a native client
that lives in git and is tested by the suite, not in n8n. This is the surface
the LiteLLM-manager agent reads from and the Executor writes through after the
operator approves — same trust split as Jira: reads are ungated and best-effort,
writes (add/delete a model) are Executor-only, post-approval.

The contract mirrors the house envelope: `{ok, kind: "litellm", operation, ...}`.
Model-controlled strings (alias names, provider model strings) are validated here,
the last stop before the proxy. Admin ops carry the LiteLLM MASTER key
(CC_LLM_PROXY_ADMIN_KEY) — the executor-held credential; without it the client is
`configured() == False` and every call fails loudly rather than half-working.
"""

from __future__ import annotations

import json
import re

import httpx

from central_command.config import settings
from central_command.integrations import http as http_client

# A LiteLLM model_name (alias) and a provider model string ("anthropic/claude-…").
# Kept deliberately conservative — these are model-controlled and name real infra.
ALIAS_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,63}$")
PROVIDER_MODEL_RE = re.compile(r"^[a-z0-9_-]+/[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$")
# A virtual-key alias — a human label; same conservative shape as a model alias.
KEY_ALIAS_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,63}$")


def _mask_secret(value: str) -> str:
    """Show only enough of a secret to confirm identity — never the whole thing.
    The plaintext of a freshly generated key must NEVER reach the event log."""
    if not isinstance(value, str) or len(value) < 8:
        return "sk-…"
    return f"{value[:5]}…{value[-4:]}"


class LiteLLMError(Exception):
    pass


def configured() -> bool:
    return bool(settings.llm_proxy_base_url and settings.llm_proxy_admin_key)


def _require_configured(op: str) -> None:
    if not configured():
        raise LiteLLMError(
            f"{op} — LiteLLM admin not configured "
            "(set CC_LLM_PROXY_BASE_URL and CC_LLM_PROXY_ADMIN_KEY)"
        )


def _alias(value: str) -> str:
    if not isinstance(value, str) or not ALIAS_RE.match(value or ""):
        raise LiteLLMError(
            f"bad model_name {value!r} — must be [a-zA-Z0-9][a-zA-Z0-9._:-]{{0,63}}"
        )
    return value


def _provider_model(value: str) -> str:
    if not isinstance(value, str) or not PROVIDER_MODEL_RE.match(value or ""):
        raise LiteLLMError(
            f"bad model {value!r} — must be '<provider>/<model>', e.g. 'anthropic/claude-sonnet-5'"
        )
    return value


# --- transport ----------------------------------------------------------------


async def _call(
    method: str, path: str, json_body: dict | None = None,
    auth_key: str | None = None, timeout: float = 30,
) -> httpx.Response:
    """`auth_key` overrides the admin key for calls made AS another key (e.g.
    /key/info self-lookup) — a secret belongs in the Authorization header,
    never in the URL, where proxy access logs and httpx error strings see it.
    `timeout` is per-call: admin reads answer in milliseconds, a probe of a
    model queued behind the llama-swap slot does not."""
    async with httpx.AsyncClient(timeout=timeout, **http_client.client_kwargs()) as client:
        return await client.request(
            method,
            settings.llm_proxy_base_url.rstrip("/") + path,
            json=json_body,
            headers={"Authorization": f"Bearer {auth_key or settings.llm_proxy_admin_key}"},
        )


def _check(resp: httpx.Response, op: str) -> None:
    if 200 <= resp.status_code < 300:
        return
    st = resp.status_code
    detail = resp.text[:300]
    if st in (401, 403):
        raise LiteLLMError(f"{op} — auth failed ({st}) — check CC_LLM_PROXY_ADMIN_KEY")
    if st == 404:
        raise LiteLLMError(f"{op} — not found (404): {detail}")
    raise LiteLLMError(f"{op} — proxy rejected the request ({st}): {detail}")


async def exposed_model_ids() -> list[str]:
    """The model ids AGENTS can address, from `/v1/models` on the endpoint they
    actually talk to (CC_LLM_BASE_URL + CC_LLM_API_KEY — the virtual key, not the
    admin plane). This is the catalog the cockpit's model dropdown offers and the
    allowlist a per-session override is validated against, so it must come from
    the same door a run goes through: what the master key can see is not
    necessarily what the agents' key is served.

    Raises LiteLLMError if the proxy is unconfigured or unreachable — an empty
    list would read as "no models", which is a different and false claim.
    """
    base = (settings.llm_base_url or "").rstrip("/")
    if not base:
        raise LiteLLMError(
            "model catalog — no LLM endpoint configured (set CC_LLM_BASE_URL)"
        )
    try:
        async with httpx.AsyncClient(timeout=10, **http_client.client_kwargs()) as client:
            resp = await client.get(
                f"{base}/v1/models",
                headers={"Authorization": f"Bearer {settings.llm_api_key}"},
            )
    except httpx.HTTPError as exc:
        raise LiteLLMError(f"model catalog — {base} unreachable: {exc}") from exc
    if resp.status_code in (401, 403):
        raise LiteLLMError(
            f"model catalog — auth failed ({resp.status_code}) — check CC_LLM_API_KEY"
        )
    if not 200 <= resp.status_code < 300:
        raise LiteLLMError(
            f"model catalog — proxy answered {resp.status_code}: {resp.text[:200]}"
        )
    data = resp.json().get("data") or []
    return sorted({str(m["id"]) for m in data if isinstance(m, dict) and m.get("id")})


async def model_accepts_images(model_id: str) -> bool:
    """Does the model an agent is about to run on accept image input?

    Read through the SAME door as `exposed_model_ids` — the agents' virtual key
    on CC_LLM_BASE_URL — because the answer must describe the endpoint a run
    actually goes through, not what the admin plane can see.

    FAILS CLOSED, and the distinction matters: LiteLLM reports
    `model_info.supports_vision` as `None` for our self-hosted entries, which
    means "nobody declared it", NOT "yes". Only an explicit `True` is a yes, so
    an undeclared model is treated as text-only. Declare the truth in
    `deploy/pi/litellm/model-preferences.yaml` (the same `model_info` home the
    routing preferences already use) rather than teaching this function to
    guess from the model's name.

    An unreachable proxy raises LiteLLMError — the caller decides whether "we
    cannot tell" refuses the send or falls through, and neither is silently
    reported as a capability answer.
    """
    for entry in await _fetch_model_info("model capabilities"):
        if not isinstance(entry, dict) or entry.get("model_name") != model_id:
            continue
        return (entry.get("model_info") or {}).get("supports_vision") is True
    # A model the endpoint does not serve cannot accept anything. Not an error:
    # the send-time caller turns this into a refusal the operator can act on.
    return False


async def _fetch_model_info(op: str) -> list:
    """GET /v1/model/info through the AGENTS' key — the same door a run goes
    through, never the admin plane. UNCACHED: `model_accepts_images` fails
    closed on it and a stale yes is a 500 the operator cannot see coming."""
    base = (settings.llm_base_url or "").rstrip("/")
    if not base:
        raise LiteLLMError(f"{op} — no LLM endpoint configured (set CC_LLM_BASE_URL)")
    try:
        async with httpx.AsyncClient(timeout=10, **http_client.client_kwargs()) as client:
            resp = await client.get(
                f"{base}/v1/model/info",
                headers={"Authorization": f"Bearer {settings.llm_api_key}"},
            )
    except httpx.HTTPError as exc:
        raise LiteLLMError(f"{op} — {base} unreachable: {exc}") from exc
    if resp.status_code in (401, 403):
        raise LiteLLMError(f"{op} — auth failed ({resp.status_code}) — check CC_LLM_API_KEY")
    if not 200 <= resp.status_code < 300:
        raise LiteLLMError(f"{op} — proxy answered {resp.status_code}: {resp.text[:200]}")
    return resp.json().get("data") or []


# Plain data + a monotonic stamp — deliberately NOT an asyncio.Lock or Event,
# which bind to the first loop that awaits them and raise from every other one
# (the module-scope loop-bound bite mark). A duplicated fetch under a race is
# cheaper than that failure mode.
_THINKING_CACHE: tuple[float, dict[str, tuple[str | None, list[str]]]] | None = None
_THINKING_TTL = 60.0


async def _thinking_declarations() -> dict:
    """`{model_name: (mechanism, levels)}` as DECLARED in model_info. 60s TTL:
    this is read on every thinking-enabled run resolution."""
    global _THINKING_CACHE
    import time

    now = time.monotonic()
    if _THINKING_CACHE and now - _THINKING_CACHE[0] < _THINKING_TTL:
        return _THINKING_CACHE[1]
    out: dict[str, tuple[str | None, list[str]]] = {}
    for entry in await _fetch_model_info("model thinking capabilities"):
        if not isinstance(entry, dict) or not entry.get("model_name"):
            continue
        info = entry.get("model_info") or {}
        mech = info.get("thinking_mechanism")
        levels = info.get("thinking_levels")
        out[str(entry["model_name"])] = (
            mech if isinstance(mech, str) and mech else None,
            [str(x) for x in levels] if isinstance(levels, list) else [],
        )
    _THINKING_CACHE = (now, out)
    return out


async def thinking_mechanism(model_id: str) -> tuple[str | None, list[str]]:
    """How (if at all) this model is told to think, and which levels it offers.

    DECLARED, never inferred — exactly like `supports_vision`. `model_info`
    carries `thinking_mechanism` ('qwen_template' | 'anthropic') and
    `thinking_levels` (a list of THINKING_LEVELS values). A model with no
    declaration returns `(None, [])`, which means "nobody said", never "yes":
    the caller sends no thinking control at all.
    """
    return (await _thinking_declarations()).get(model_id, (None, []))


async def thinking_levels_by_model() -> dict[str, list[str]]:
    """One proxy read for the WHOLE catalog — the model dropdown needs every
    model's levels at once and must not fan out a request per model."""
    return {name: levels for name, (_m, levels) in (await _thinking_declarations()).items()}


# --- reads (ungated, best-effort at the tool layer) ---------------------------


# LiteLLM's NATIVE model_info audit/classification fields (proxy ≥1.x). Surfacing
# these — and ONLY these — from list_models keeps the agent grounded in real
# metadata without dumping the whole model-cost map that /model/info merges in.
# `created_date` is deliberately absent: it is NOT a native field (a past agent
# invented it; it stores but never renders). The native creation stamp is
# `created_at`.  `id` is pulled out as `model_id` separately.
NATIVE_MODEL_INFO_FIELDS = (
    "created_at", "created_by", "updated_at", "updated_by",
    "base_model", "tier", "team_id", "db_model",
)

# The CAPABILITY declarations — LiteLLM's own `supports_*` / `max_*` / `mode`
# vocabulary (the cost-map schema, 38 flags on this build) plus Central
# Command's two thinking declarations. `list_models` surfaces exactly these,
# and `probe_model` measures the ones a request can measure. Until 2026-08-30
# `list_models` STRIPPED every `supports_*`/`max_*` key as "cost-map noise", so
# the manager was told to declare `supports_vision` and could never see
# whether it had — nor audit a fleet for undeclared models. Undeclared reads
# as None here ("nobody said"); note LiteLLM's own aggregate view
# (`/model_group/info`) coerces the same absence to FALSE.
CAPABILITY_FIELDS = (
    "mode", "max_input_tokens", "max_output_tokens",
    "supports_function_calling", "supports_parallel_function_calling",
    "supports_tool_choice", "supports_response_schema", "supports_vision",
    "supports_pdf_input", "supports_reasoning", "supports_system_messages",
    "supports_prompt_caching", "thinking_mechanism", "thinking_levels",
)


async def list_models() -> dict:
    """Every model the proxy serves, with its provider mapping and NATIVE
    model_info metadata (created_at/created_by/updated_at, tier, base_model) —
    what the manager reads before proposing a change, and how it verifies that a
    metadata edit actually landed. Provider keys are never returned by the proxy
    (it encrypts them), so this is safe to show the agent."""
    _require_configured("list_models")
    resp = await _call("GET", "/model/info")
    _check(resp, "list_models")
    models = []
    for m in resp.json().get("data") or []:
        params = m.get("litellm_params") or {}
        info = m.get("model_info") or {}
        # Only the native audit/classification fields — never the merged cost map.
        native = {k: info.get(k) for k in NATIVE_MODEL_INFO_FIELDS if info.get(k) is not None}
        # The capability declarations, None-preserving on purpose: an absent
        # flag IS the finding (an undeclared model), so it must read as such.
        capabilities = {k: info.get(k) for k in CAPABILITY_FIELDS}
        # Any CUSTOM keys the operator stored (excluding the cost-map noise the
        # proxy merges in) so the agent can see — and correct — non-native fields
        # like a stray `created_date`.
        custom = {
            k: v for k, v in info.items()
            if k not in NATIVE_MODEL_INFO_FIELDS
            and k not in CAPABILITY_FIELDS
            and k not in ("id", "key", "litellm_provider")
            and not k.startswith(("input_cost", "output_cost", "cache_", "max_",
                                  "supports_", "search_context"))
            and not isinstance(v, (dict, list))
        }
        models.append({
            "model_name": m.get("model_name"),
            "model_id": info.get("id"),
            # the underlying provider model, when the proxy exposes it un-encrypted
            "provider_model": params.get("model") if isinstance(params.get("model"), str)
            and "/" in (params.get("model") or "") else None,
            "provider": params.get("custom_llm_provider"),
            "api_base": params.get("api_base"),
            # rides litellm_params UNENCRYPTED (verified live 2026-08-20) —
            # the ownership marker for credential-driven autodiscovery: a
            # deployment referencing a stored credential is autodiscovery's
            # to manage.
            "credential_name": params.get("litellm_credential_name"),
            "model_info": native,
            "capabilities": capabilities,
            "custom_model_info": custom or None,
        })
    return {"ok": True, "kind": "litellm", "operation": "list_models",
            "models": models, "count": len(models)}


async def list_credentials() -> dict:
    """The NAMED credentials stored in the proxy (LiteLLM's own encrypted
    credential store). A model authenticates by referencing one via
    litellm_params.litellm_credential_name — so the agent can propose a model that
    reuses a stored credential instead of relying on the Executor to inject a raw
    key. The proxy never returns the secret values (encrypted), so this is safe to
    show the agent; it sees only names and non-secret info."""
    _require_configured("list_credentials")
    resp = await _call("GET", "/credentials")
    _check(resp, "list_credentials")
    body = resp.json()
    raw = body.get("credentials") if isinstance(body, dict) else body
    creds = []
    for c in raw or []:
        info = c.get("credential_info") or {}
        creds.append({
            "credential_name": c.get("credential_name"),
            # the provider this credential is for, when the proxy exposes it
            "provider": info.get("custom_llm_provider") or info.get("provider"),
        })
    return {"ok": True, "kind": "litellm", "operation": "list_credentials",
            "credentials": creds, "count": len(creds)}


async def list_keys() -> dict:
    """The virtual keys the proxy issues — alias, model scope, spend, and id, but
    NEVER a usable secret (the proxy stores keys hashed; /key/list returns the
    hash as `token`, not the plaintext). This is what the manager reads before
    proposing to create or delete a key, and how it reports per-key spend."""
    _require_configured("list_keys")
    resp = await _call("GET", "/key/list?return_full_object=true&size=100")
    _check(resp, "list_keys")
    body = resp.json()
    rows = body.get("keys") if isinstance(body, dict) else body
    keys = []
    for k in rows or []:
        if isinstance(k, str):  # some builds return just hashes
            keys.append({"token": k}); continue
        keys.append({
            "key_alias": k.get("key_alias"),
            "token_id": k.get("token") or k.get("token_id"),  # hash, safe to show
            "models": k.get("models") or [],
            "spend": k.get("spend"),
            "created_at": k.get("created_at"),
            "team_id": k.get("team_id"),
        })
    return {"ok": True, "kind": "litellm", "operation": "list_keys",
            "keys": keys, "count": len(keys)}


FALLBACK_TYPES = ("general", "context_window", "content_policy")


async def get_routing() -> dict:
    """The proxy's routing picture: the routing STRATEGY + retry count (advisory —
    config-level, not changeable via a proposal here) and the configured FALLBACK
    chains (actionable via set_fallbacks/delete_fallbacks). Read before proposing a
    fallback change so the agent sees what is already in place."""
    _require_configured("get_routing")
    resp = await _call("GET", "/router/settings")
    _check(resp, "get_routing")
    body = resp.json()
    fields = {f.get("field_name"): f.get("field_value")
              for f in (body.get("fields") or []) if isinstance(f, dict)}
    return {"ok": True, "kind": "litellm", "operation": "get_routing",
            "routing_strategy": fields.get("routing_strategy"),
            "num_retries": fields.get("num_retries"),
            "fallbacks": fields.get("fallbacks"),
            "context_window_fallbacks": fields.get("context_window_fallbacks"),
            "content_policy_fallbacks": fields.get("content_policy_fallbacks")}


async def list_teams() -> dict:
    """The teams the proxy knows — alias, id, and the models scoped to each. Read
    before proposing a team change so proposals name real team_ids."""
    _require_configured("list_teams")
    resp = await _call("GET", "/team/list")
    _check(resp, "list_teams")
    body = resp.json()
    rows = body if isinstance(body, list) else (body.get("teams") or body.get("data") or [])
    teams = []
    for t in rows or []:
        if not isinstance(t, dict):
            continue
        teams.append({
            "team_alias": t.get("team_alias"),
            "team_id": t.get("team_id"),
            "models": t.get("models") or [],
        })
    return {"ok": True, "kind": "litellm", "operation": "list_teams",
            "teams": teams, "count": len(teams)}


async def get_health() -> dict:
    """The proxy's own health — CHEAP (no provider calls): status, DB, cache,
    version, and how many success callbacks are wired. This is the 'is the proxy
    up and configured' read; use it freely."""
    _require_configured("get_health")
    resp = await _call("GET", "/health/readiness/details")
    _check(resp, "get_health")
    d = resp.json()
    return {"ok": True, "kind": "litellm", "operation": "get_health",
            "status": d.get("status"), "db": d.get("db"), "cache": d.get("cache"),
            "litellm_version": d.get("litellm_version"),
            "log_level": d.get("log_level"),
            "success_callbacks_count": len(d.get("success_callbacks") or [])}


async def check_model_health(model: str | None = None) -> dict:
    """Live health of the MODELS: which deployments answer and which are failing.
    EXPENSIVE — the proxy makes a REAL provider call to each model checked (tokens
    + latency), so only use this to diagnose a suspected outage, and pass `model`
    to check a single alias rather than the whole fleet. Returns healthy vs
    unhealthy endpoints with the error on the unhealthy ones."""
    _require_configured("check_model_health")
    path = "/health"
    if model is not None:
        path += f"?model={_alias(model)}"
    resp = await _call("GET", path)
    _check(resp, "check_model_health")
    d = resp.json()
    healthy = [e.get("model") or e.get("api_base") for e in (d.get("healthy_endpoints") or [])]
    unhealthy = [
        {"model": e.get("model"), "error": e.get("error") or e.get("exception")}
        for e in (d.get("unhealthy_endpoints") or [])
    ]
    return {"ok": True, "kind": "litellm", "operation": "check_model_health",
            "healthy": healthy, "unhealthy": unhealthy,
            "healthy_count": len(healthy), "unhealthy_count": len(unhealthy)}


# --- capability probe (2026-08-30) --------------------------------------------
#
# `/health` proves a model ANSWERS. Nothing in LiteLLM measures what it can
# do: its `supports_*()` helpers are static lookups into the public cost map,
# which knows nothing about a private model behind a gateway. The probe sends
# one small real request per capability THROUGH the proxy (so the answer
# describes the registered deployment, translation layer included) and reports
# declared-vs-observed plus the `model_info` diff a proposal would carry.
# Observed is evidence, not authority: the manager reads it, then PROPOSES
# `litellm.update_model`; the probe itself writes nothing.

_PROBE_PNG = (  # a 1x1 red PNG — the smallest image an endpoint can be asked about
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAE"
    "hQGAhKmMIQAAAABJRU5ErkJggg=="
)
# The smallest well-formed one-page PDF (a blank page, ~230 bytes) — enough
# for an endpoint that reads documents to answer, and for one that does not
# to error. Built by hand once; never parsed by us.
_PROBE_PDF = (
    "JVBERi0xLjQKMSAwIG9iajw8L1R5cGUvQ2F0YWxvZy9QYWdlcyAyIDAgUj4+ZW5kb2JqCjIg"
    "MCBvYmo8PC9UeXBlL1BhZ2VzL0tpZHNbMyAwIFJdL0NvdW50IDE+PmVuZG9iagozIDAgb2Jq"
    "PDwvVHlwZS9QYWdlL1BhcmVudCAyIDAgUi9NZWRpYUJveFswIDAgNjEyIDc5Ml0+PmVuZG9i"
    "agp0cmFpbGVyPDwvUm9vdCAxIDAgUj4+CiUlRU9G"
)
_PROBE_TOOL = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get the current weather for a city.",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    },
}
_PROBE_SCHEMA = {
    "name": "probe_answer",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {"answer": {"type": "integer"}},
        "required": ["answer"],
        "additionalProperties": False,
    },
}
PROBE_CONTEXT_CEILING = 262144   # bisect no higher than this unless asked
PROBE_CONTEXT_STEPS = 10         # ≤10 requests to land within ~0.1% of the ceiling


async def _chat(model: str, body: dict, timeout: float) -> tuple[int, dict | str]:
    """One chat completion under the ADMIN key (a freshly added model is not
    yet in any agent key's scope). Returns (status, parsed-or-text)."""
    resp = await _call("POST", "/v1/chat/completions", {"model": model, **body},
                       timeout=timeout)
    try:
        return resp.status_code, resp.json()
    except ValueError:
        return resp.status_code, resp.text[:400]


def _err(payload) -> str:
    if isinstance(payload, dict):
        e = payload.get("error")
        if isinstance(e, dict):
            return str(e.get("message") or e)[:300]
        return str(e or payload)[:300]
    return str(payload)[:300]


def _message(payload) -> dict:
    try:
        return payload["choices"][0]["message"] or {}
    except (KeyError, IndexError, TypeError):
        return {}


# A thinking model spends its output budget on reasoning BEFORE the answer:
# with max_tokens=16 cc-default answered every JSON/vision check with HTTP 200
# and empty content (measured 2026-08-30), which the first cut scored as "no".
# So every check gets a real budget, and "200, empty, finish_reason=length" is
# reported as INCONCLUSIVE (ok=None) — never as a capability finding.
PROBE_MAX_TOKENS = 1024


def _reply(st: int, payload) -> tuple[str | None, str, dict]:
    """(content, finish_reason, message) for a 200; ("", "", {}) otherwise."""
    if st != 200:
        return None, "", {}
    msg = _message(payload)
    fin = ""
    try:
        fin = str(payload["choices"][0].get("finish_reason") or "")
    except (KeyError, IndexError, TypeError, AttributeError):
        pass
    content = msg.get("content")
    return (content if isinstance(content, str) else ""), fin, msg


def _verdict(st: int, payload, ok_when: bool, detail: str) -> dict:
    """ok True/False/None + detail; None when the budget ran out before an answer."""
    if st != 200:
        return {"ok": False, "detail": f"{st}: {_err(payload)}"}
    content, fin, _ = _reply(st, payload)
    if not content and fin == "length":
        return {"ok": None, "detail": "output budget exhausted before an answer (thinking?) — inconclusive"}
    return {"ok": ok_when, "detail": detail}


async def probe_model(
    model: str, *, measure_context: bool = False,
    context_ceiling: int = PROBE_CONTEXT_CEILING, timeout: float = 300,
    battery: str | None = None,
) -> dict:
    """Measure what `model` can actually do, one small request per capability.

    Returns `declared` (the model_info as registered), `observed` (one entry
    per check: ok / detail), `suggested_model_info` (the declared→observed
    diff, ready for a `litellm.update_model` proposal) and `notes`. A failing
    plain completion ends the battery — every other check would only repeat
    that failure. `measure_context` bisects the input ceiling with ≤10 extra
    requests up to `context_ceiling` tokens; it is opt-in because a 200K-token
    prompt against a queued local model is minutes, not milliseconds.
    """
    _require_configured("probe_model")
    alias = _alias(model)
    declared: dict = {}
    provider_model = ""
    for entry in (await _call("GET", "/model/info")).json().get("data") or []:
        if isinstance(entry, dict) and entry.get("model_name") == alias:
            declared = {k: (entry.get("model_info") or {}).get(k) for k in CAPABILITY_FIELDS}
            pm = (entry.get("litellm_params") or {}).get("model")
            provider_model = pm if isinstance(pm, str) else ""
            break
    observed: dict[str, dict] = {}
    notes: list[str] = []

    # Non-chat modes get their OWN one-request battery — every chat-shaped
    # check would fail on an embedder by construction and prove nothing.
    # Dispatch is on the DECLARED mode, or the caller's explicit `battery`
    # ("chat" | "embedding" | "rerank") for a model whose mode is not
    # declared yet — which is exactly when you are probing it.
    kind = battery or declared.get("mode") or "chat"
    if kind == "embedding":
        resp = await _call("POST", "/v1/embeddings",
                           {"model": alias, "input": "probe"}, timeout=timeout)
        dims = None
        if resp.status_code == 200:
            try:
                dims = len(resp.json()["data"][0]["embedding"])
            except (KeyError, IndexError, TypeError, ValueError):
                dims = None
        observed["embedding"] = {"ok": resp.status_code == 200 and bool(dims),
                                 "value": dims,
                                 "detail": (f"{dims}-dimension vector" if dims else
                                            f"{resp.status_code}: {resp.text[:200]}")}
        return {"ok": bool(observed["embedding"]["ok"]), "kind": "litellm",
                "operation": "probe_model", "model": alias, "declared": declared,
                "observed": observed, "suggested_model_info": {}, "notes": notes}
    if kind == "rerank":
        resp = await _call("POST", "/v1/rerank",
                           {"model": alias, "query": "probe",
                            "documents": ["a probe document", "another document"]},
                           timeout=timeout)
        ok = False
        if resp.status_code == 200:
            try:
                ok = bool(resp.json().get("results"))
            except ValueError:
                ok = False
        observed["rerank"] = {"ok": ok,
                              "detail": "results returned" if ok else
                                        f"{resp.status_code}: {resp.text[:200]}"}
        return {"ok": ok, "kind": "litellm", "operation": "probe_model",
                "model": alias, "declared": declared, "observed": observed,
                "suggested_model_info": {}, "notes": notes}

    say_ok = [{"role": "user", "content": "Reply with the single word OK."}]

    # 1. chat — the gate for everything else
    st, out = await _chat(alias, {"messages": say_ok, "max_tokens": PROBE_MAX_TOKENS}, timeout)
    content, _fin, _ = _reply(st, out)
    observed["chat"] = _verdict(st, out, bool(content), (content or "")[:80])
    if st != 200:
        if st == 404:
            if "/chat_completions/" in provider_model:
                # A Responses→chat bridge alias serves ONLY the Responses API —
                # a plain chat call sends "chat_completions/<model>" upstream,
                # which no server routes (graphiti-llm, measured 2026-08-30).
                notes.append("this is a Responses-bridge alias (openai/chat_completions/ "
                             "prefix) — it answers /v1/responses only; probe the "
                             "underlying model's own alias instead")
            else:
                notes.append("404 on chat: check api_base ends in /v1 and the provider "
                             "prefix matches the gateway's format (openai/ vs anthropic/)")
        return {"ok": False, "kind": "litellm", "operation": "probe_model", "model": alias,
                "declared": declared, "observed": observed,
                "suggested_model_info": {}, "notes": notes}

    # 2. system message
    st, out = await _chat(alias, {"messages": [{"role": "system", "content": "You are terse."},
                                              *say_ok], "max_tokens": PROBE_MAX_TOKENS}, timeout)
    observed["system_messages"] = {"ok": st == 200, "detail": "" if st == 200 else f"{st}: {_err(out)}"}

    # 3. forced tool call (tool_choice) — the presence of a tool_calls entry is the proof
    st, out = await _chat(alias, {
        "messages": [{"role": "user", "content": "What is the weather in Paris?"}],
        "tools": [_PROBE_TOOL],
        "tool_choice": {"type": "function", "function": {"name": "get_weather"}},
        "max_tokens": PROBE_MAX_TOKENS,
    }, timeout)
    calls = _message(out).get("tool_calls") or [] if st == 200 else []
    observed["tool_choice"] = _verdict(st, out, bool(calls), f"{len(calls)} call(s)") \
        if not calls else {"ok": True, "detail": f"{len(calls)} call(s)"}
    # 4. tools, auto — and parallel: two cities in one turn
    st, out = await _chat(alias, {
        "messages": [{"role": "user", "content":
                      "Use the tool to get the weather in Paris and in Tokyo, both, now."}],
        "tools": [_PROBE_TOOL], "tool_choice": "auto", "max_tokens": PROBE_MAX_TOKENS,
    }, timeout)
    calls = _message(out).get("tool_calls") or [] if st == 200 else []
    observed["function_calling"] = _verdict(st, out, bool(calls), f"{len(calls)} call(s)") \
        if not calls else {"ok": True, "detail": f"{len(calls)} call(s)"}
    # Two calls prove parallel calling; ONE call is a model's choice, not
    # proof of absence (gpt-oss-20b answered one city first, 2026-08-30), so
    # anything short of two is inconclusive rather than false.
    observed["parallel_function_calling"] = {"ok": True if len(calls) >= 2 else None,
                                             "detail": f"{len(calls)} call(s) for two cities"}

    # 5. structured output — json_schema, then the looser json_object
    st, out = await _chat(alias, {
        "messages": [{"role": "user", "content": "What is 2+2? Answer as JSON."}],
        "response_format": {"type": "json_schema", "json_schema": _PROBE_SCHEMA},
        "max_tokens": PROBE_MAX_TOKENS,
    }, timeout)
    content, _fin, _ = _reply(st, out)
    parsed = None
    try:
        parsed = json.loads(content or "")
    except ValueError:
        parsed = None
    observed["response_schema"] = _verdict(st, out, isinstance(parsed, dict) and "answer" in parsed,
                                           (content or "")[:80])
    st, out = await _chat(alias, {
        "messages": [{"role": "user", "content": 'Answer as a JSON object {"answer": <int>}: what is 2+2?'}],
        "response_format": {"type": "json_object"}, "max_tokens": PROBE_MAX_TOKENS,
    }, timeout)
    content, _fin, _ = _reply(st, out)
    try:
        ok = isinstance(json.loads(content or ""), dict)
    except ValueError:
        ok = False
    observed["json_object"] = _verdict(st, out, ok, (content or "")[:80])

    # 6. vision — a 1x1 PNG; any answer is a yes, a 4xx/5xx is a no
    st, out = await _chat(alias, {
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": "What color is this image? One word."},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{_PROBE_PNG}"}},
        ]}], "max_tokens": PROBE_MAX_TOKENS,
    }, timeout)
    content, _fin, _ = _reply(st, out)
    observed["vision"] = _verdict(st, out, bool(content), (content or "")[:40])

    # 7. reasoning — the OpenAI-style control; accepted AND visible are separate facts
    st, out = await _chat(alias, {"messages": [{"role": "user", "content": "Is 17 prime? One word."}],
                                  "reasoning_effort": "low", "max_tokens": PROBE_MAX_TOKENS}, timeout)
    msg = _message(out) if st == 200 else {}
    seen = bool(msg.get("reasoning_content") or msg.get("reasoning"))
    observed["reasoning"] = {"ok": st == 200 and seen,
                             "detail": ("reasoning_content present" if seen else
                                        "accepted, no reasoning_content in the reply — a template-"
                                        "driven model (qwen_template) needs its mechanism declared by hand")
                             if st == 200 else f"{st}: {_err(out)}"}

    # 7b. pdf input — an OpenAI `file` content part carrying a blank one-page
    # PDF; an endpoint that reads documents answers, one that does not errors.
    st, out = await _chat(alias, {
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": "How many pages does this document have? One word."},
            {"type": "file", "file": {"filename": "probe.pdf",
                                      "file_data": f"data:application/pdf;base64,{_PROBE_PDF}"}},
        ]}], "max_tokens": PROBE_MAX_TOKENS,
    }, timeout)
    content, _fin, _ = _reply(st, out)
    observed["pdf_input"] = _verdict(st, out, bool(content), (content or "")[:40])

    # 7c. template-driven thinking — only when the model has NO declared
    # mechanism: send the Qwen-style chat_template_kwargs and see whether
    # reasoning_content appears. INFERENCE STOPS AT A NOTE: declaring
    # thinking_mechanism changes how the runtime CONTROLS the model
    # (runtime/models._thinking_body), so that call is the operator's.
    if not declared.get("thinking_mechanism"):
        st, out = await _chat(alias, {
            "messages": [{"role": "user", "content": "Is 17 prime? One word."}],
            "chat_template_kwargs": {"enable_thinking": True},
            "max_tokens": PROBE_MAX_TOKENS,
        }, timeout)
        msg2 = _message(out) if st == 200 else {}
        seen2 = bool(msg2.get("reasoning_content") or msg2.get("reasoning"))
        observed["thinking_template"] = {
            "ok": st == 200 and seen2,
            "detail": ("chat_template_kwargs produced reasoning_content" if seen2
                       else "" if st != 200 else "accepted, no reasoning_content"),
        }
        if st == 200 and seen2:
            notes.append("template-driven thinking works — consider declaring "
                         "thinking_mechanism: qwen_template (plus thinking_levels) "
                         "so the runtime can control it; see the model-evaluation skill")

    # 8. output ceiling — ask for far too much and read what the endpoint says
    st, out = await _chat(alias, {"messages": say_ok, "max_tokens": 1_000_000}, timeout)
    if st == 200:
        observed["max_output_tokens"] = {"ok": None, "detail": "max_tokens=1000000 accepted — no "
                                         "request-time ceiling; declare from the model card"}
    else:
        nums = [int(n) for n in re.findall(r"\d{3,}", _err(out)) if int(n) != 1_000_000]
        observed["max_output_tokens"] = {"ok": None, "detail": f"{st}: {_err(out)}",
                                         "value": max(nums) if nums else None}

    # 9. streaming
    resp = await _call("POST", "/v1/chat/completions",
                       {"model": alias, "messages": say_ok, "max_tokens": 64, "stream": True},
                       timeout=timeout)
    observed["streaming"] = {"ok": resp.status_code == 200 and "data:" in resp.text[:200],
                             "detail": "" if resp.status_code == 200 else f"{resp.status_code}"}

    # 10. input ceiling — opt-in bisect, max_tokens=1, filler of single-token words
    if measure_context:
        lo, hi = 1024, max(2048, int(context_ceiling))
        best = None
        for _ in range(PROBE_CONTEXT_STEPS):
            mid = (lo + hi) // 2
            st, out = await _chat(alias, {
                "messages": [{"role": "user", "content": ("0 " * mid) + "\nReply OK."}],
                "max_tokens": 1,
            }, timeout)
            if st == 200:
                best, lo = mid, mid + 1
            else:
                hi = mid - 1
            if lo > hi:
                break
        observed["max_input_tokens"] = {"ok": None, "value": best,
                                        "detail": f"largest accepted ≈{best} filler tokens "
                                                  f"(ceiling searched: {context_ceiling})"}

    # declared → observed diff, only where the probe can speak
    flags = {
        "supports_system_messages": observed["system_messages"]["ok"],
        "supports_tool_choice": observed["tool_choice"]["ok"],
        "supports_function_calling": observed["function_calling"]["ok"],
        "supports_parallel_function_calling": observed["parallel_function_calling"]["ok"],
        "supports_response_schema": observed["response_schema"]["ok"],
        "supports_vision": observed["vision"]["ok"],
        "supports_reasoning": observed["reasoning"]["ok"],
        "supports_pdf_input": observed["pdf_input"]["ok"],
    }
    suggested = {k: v for k, v in flags.items() if v is not None and declared.get(k) != v}
    if declared.get("mode") is None:
        suggested["mode"] = "chat"
    for key in ("max_output_tokens", "max_input_tokens"):
        val = (observed.get(key) or {}).get("value")
        if val and declared.get(key) != val:
            suggested[key] = val
    if flags["supports_function_calling"] is False and declared.get("supports_function_calling"):
        notes.append("declared tool-capable but produced no tool_calls — re-run before trusting either")
    return {"ok": True, "kind": "litellm", "operation": "probe_model", "model": alias,
            "declared": declared, "observed": observed,
            "suggested_model_info": suggested, "notes": notes}


async def get_spend() -> dict:
    """Recent spend, aggregated per (model, key) from the proxy's request log —
    the 'what is costing money' read. Best-effort: the log is a rolling window,
    not a ledger."""
    _require_configured("get_spend")
    resp = await _call("GET", "/spend/logs")
    _check(resp, "get_spend")
    rows = resp.json()
    rows = rows if isinstance(rows, list) else rows.get("data") or []
    agg: dict[tuple, float] = {}
    total = 0.0
    for r in rows:
        model = r.get("model") or "?"
        key = (r.get("metadata") or {}).get("user_api_key_alias") or r.get("key_alias") or "?"
        spend = float(r.get("spend") or 0)
        agg[(model, key)] = agg.get((model, key), 0.0) + spend
        total += spend
    lines = [
        {"model": k[0], "key_alias": k[1], "spend": round(v, 6)}
        for k, v in sorted(agg.items(), key=lambda kv: kv[1], reverse=True)
    ]
    # Sized to the deployment ceiling (n_ctx=262144, no output cap): 500
    # rows is generous headroom, not a token-economy cap. Kept as a bound
    # (rather than removed outright) only so an unbounded fleet can't silently
    # balloon this payload — and the drop is reported honestly, unlike a bare
    # slice, so the model never mistakes a partial breakdown for the whole one.
    _BY_MODEL_KEY_CAP = 500
    shown = lines[:_BY_MODEL_KEY_CAP]
    result = {"ok": True, "kind": "litellm", "operation": "get_spend",
              "by_model_key": shown, "total_spend": round(total, 6),
              "sampled_requests": len(rows)}
    if len(lines) > len(shown):
        result["by_model_key_omitted"] = len(lines) - len(shown)
    return result


async def _anthropic_catalog(api_key: str, api_base: str | None = None) -> list[dict]:
    # Deliberately NOT wired through integrations.http.client_kwargs(): this
    # hits the PROVIDER's own public endpoint (api.anthropic.com by default,
    # or a per-credential api_base) with that provider's own key, never the
    # operator's enterprise-internal endpoint the pluggable-trust seam exists
    # for — presenting an internal mTLS identity to a public third party
    # would be a leak, not a fix.
    base = (api_base or "https://api.anthropic.com").rstrip("/")
    models: list[dict] = []
    after_id: str | None = None
    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            params: dict = {"limit": 100}
            if after_id:
                params["after_id"] = after_id
            resp = await client.get(
                f"{base}/v1/models",
                params=params,
                headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
            )
            if not 200 <= resp.status_code < 300:
                raise LiteLLMError(
                    f"provider_catalog(anthropic) — provider answered "
                    f"{resp.status_code}: {resp.text[:200]}"
                )
            body = resp.json()
            models.extend(body.get("data") or [])
            if not body.get("has_more"):
                break
            after_id = body.get("last_id")
            if not after_id:
                break
    return models


async def _openai_compatible_catalog(api_key: str, api_base: str | None = None) -> list[dict]:
    """GET {api_base}/v1/models with a Bearer key — the OpenAI shape, also
    served by most OpenAI-compatible endpoints. Used for `openai` itself and
    as the fallback for any credential that carries an api_base but isn't one
    of the specially-handled providers; a 404/error surfaces as that
    credential's own findings error rather than being swallowed."""
    base = (api_base or "https://api.openai.com").rstrip("/")
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{base}/v1/models", headers={"Authorization": f"Bearer {api_key}"}
        )
    if not 200 <= resp.status_code < 300:
        raise LiteLLMError(
            f"provider_catalog — provider answered {resp.status_code}: {resp.text[:200]}"
        )
    return resp.json().get("data") or []


# Provider-specific catalog fetchers, keyed by the same provider name LiteLLM
# uses (custom_llm_provider / the "provider/" model-string prefix). Anything
# not listed here falls back to the generic openai-compatible fetcher ONLY if
# the credential carries an api_base (a bare provider name with no known
# fetcher and no api_base has nowhere to ask).
PROVIDER_CATALOGS = {
    "anthropic": _anthropic_catalog,
    "openai": _openai_compatible_catalog,
}


async def provider_catalog(provider: str, api_key: str, api_base: str | None = None) -> list[dict]:
    """The provider's OWN live model catalog, fetched with the credential
    (decrypted from LiteLLM's stored credentials by
    integrations.litellm_credstore) that Central Command was handed for it —
    never the proxy. What autodiscovery diffs against the proxy's configured
    deployments. Raises if the provider has no catalog fetcher and no
    api_base to try the generic openai-compatible fetcher against."""
    # Case-insensitive on purpose: the provider name is operator-typed in the
    # LiteLLM UI ("OpenAI"), the registry is lowercase. Belt to the credstore's
    # own normalization braces.
    fn = PROVIDER_CATALOGS.get((provider or "").strip().lower())
    if fn is not None:
        return await fn(api_key, api_base)
    if api_base:
        return await _openai_compatible_catalog(api_key, api_base)
    raise LiteLLMError(f"provider_catalog — no catalog fetcher for provider {provider!r}")


# --- writes (Executor only, post-approval) ------------------------------------


async def add_model(
    model_name: str, model: str, api_key: str | None = None,
    model_info: dict | None = None, extra: dict | None = None,
) -> dict:
    """Register a model/alias in the proxy. `model_name` is the alias callers use
    (it doubles as Central Command's model name); `model` is the provider string
    ('anthropic/claude-…'). An api_key is optional — the Executor supplies the
    provider credential (it never rides a proposal); a keyless anthropic model
    falls back to the proxy env and is why the 2026-07-22 re-registration broke
    the Claude models. `model_info` is where LiteLLM stores CUSTOM METADATA
    (created_by, created_date, …) — distinct from litellm_params (`extra`).
    The proxy persists all of this to Postgres, so it survives restarts."""
    _require_configured("add_model")
    name = _alias(model_name)
    prov = _provider_model(model)
    params: dict = {"model": prov}
    if api_key:
        params["api_key"] = api_key
    if extra:
        if not isinstance(extra, dict):
            raise LiteLLMError("add_model — extra must be an object of litellm_params")
        params.update(extra)
    payload: dict = {"model_name": name, "litellm_params": params}
    if model_info:
        if not isinstance(model_info, dict):
            raise LiteLLMError("add_model — model_info must be an object of metadata fields")
        payload["model_info"] = model_info
    resp = await _call("POST", "/model/new", payload)
    _check(resp, "add_model")
    body = resp.json()
    return {"ok": True, "kind": "litellm", "operation": "add_model", "model": {
        "model_name": name,
        "provider_model": prov,
        "model_id": body.get("model_id") or (body.get("model_info") or {}).get("id"),
    }}


async def update_model(
    model_id: str, model_info: dict | None = None,
    litellm_params: dict | None = None, model_name: str | None = None,
) -> dict:
    """Edit an EXISTING model in place, addressed by its proxy model_id — the
    in-place alternative to delete+add. Routes to PATCH /model/{id}/update, which
    LiteLLM (verified on 1.84.0) documents as a true partial update: only the
    fields sent are changed; everything omitted — crucially the stored, encrypted
    provider api_key — is PRESERVED. This is why an update can never drop the
    credential the way a keyless re-registration did (2026-07-22).

    `model_info` sets custom/native metadata (use LiteLLM's native `created_at`/
    `created_by`, NOT a custom `created_date`); `litellm_params` edits provider
    params (rpm, tpm, timeout, cost overrides, or `model` to re-point the alias);
    `model_name` renames the alias. Because the proxy merges with exclude_none,
    a field cannot be nulled via update — only overwritten.
    """
    _require_configured("update_model")
    if not isinstance(model_id, str) or not model_id.strip():
        raise LiteLLMError("update_model — model_id must be a non-empty string")
    mid = model_id.strip()
    body: dict = {}
    if model_name is not None:
        body["model_name"] = _alias(model_name)
    if litellm_params is not None:
        if not isinstance(litellm_params, dict):
            raise LiteLLMError("update_model — litellm_params must be an object")
        # If the update re-points the provider model, validate it like add_model.
        if isinstance(litellm_params.get("model"), str):
            _provider_model(litellm_params["model"])
        body["litellm_params"] = litellm_params
    if model_info is not None:
        if not isinstance(model_info, dict):
            raise LiteLLMError("update_model — model_info must be an object of metadata fields")
        body["model_info"] = model_info
    if not body:
        raise LiteLLMError("update_model — nothing to change (give model_info, litellm_params, or model_name)")
    resp = await _call("PATCH", f"/model/{mid}/update", body)
    _check(resp, "update_model")
    body_out = resp.json()
    info = (body_out.get("model_info") or {}) if isinstance(body_out, dict) else {}
    return {"ok": True, "kind": "litellm", "operation": "update_model", "model": {
        "model_id": info.get("id") or mid,
        "model_name": (body_out.get("model_name") if isinstance(body_out, dict) else None) or model_name,
        "updated": sorted(body.keys()),
    }}


async def create_key(
    key_alias: str, models: list | None = None,
    metadata: dict | None = None, tags: list | None = None,
    duration: str | None = None,
) -> dict:
    """Issue a new virtual key (POST /key/generate). `key_alias` is the human
    label; `models` scopes which aliases the key may call (empty = all); `tags`
    (stored under metadata) drive per-tag spend tracking; `duration` sets a
    lifetime (e.g. '30d'). Budgets/rate-limits are deliberately NOT set here.

    IMPORTANT: the proxy returns the plaintext key ONCE. This client returns only
    a MASKED preview + the token_id (hash) — the plaintext is never surfaced to
    callers, so it can never enter a proposal result or the append-only log. Central Command
    does not USE a key until per-agent wiring (Phase 5), which will capture the
    plaintext Executor-side at creation; until then a usable token is obtained by
    regenerating the key in LiteLLM."""
    _require_configured("create_key")
    if not isinstance(key_alias, str) or not KEY_ALIAS_RE.match(key_alias or ""):
        raise LiteLLMError(
            f"bad key_alias {key_alias!r} — must be [a-zA-Z0-9][a-zA-Z0-9._:-]{{0,63}}"
        )
    payload: dict = {"key_alias": key_alias}
    if models is not None:
        if not isinstance(models, list) or not all(isinstance(m, str) and m for m in models):
            raise LiteLLMError("create_key — models must be a list of alias strings")
        payload["models"] = models
    if metadata is not None:
        if not isinstance(metadata, dict):
            raise LiteLLMError("create_key — metadata must be an object")
        payload["metadata"] = metadata
    if tags is not None:
        if not isinstance(tags, list) or not all(isinstance(t, str) and t for t in tags):
            raise LiteLLMError("create_key — tags must be a list of strings")
        payload.setdefault("metadata", {})["tags"] = tags
    if duration is not None:
        if not isinstance(duration, str) or not duration.strip():
            raise LiteLLMError("create_key — duration must be a non-empty string like '30d'")
        payload["duration"] = duration
    resp = await _call("POST", "/key/generate", payload)
    _check(resp, "create_key")
    body = resp.json()
    plaintext = body.get("key") or ""
    return {"ok": True, "kind": "litellm", "operation": "create_key", "key": {
        "key_alias": body.get("key_alias") or key_alias,
        "token_id": body.get("token_id") or body.get("token"),  # hash, safe
        "models": body.get("models") or (models or []),
        "expires": body.get("expires"),
        # masked ONLY — the plaintext is intentionally discarded here
        "key_preview": _mask_secret(plaintext),
    }}


async def set_fallbacks(
    model: str, fallback_models: list, fallback_type: str = "general",
) -> dict:
    """Set the fallback chain for a model (POST /fallback): when a call to `model`
    fails (after retries), the router tries `fallback_models` in order. `model` and
    each fallback are ALIASES from list_models. `fallback_type`: 'general' (any
    error, default), 'context_window' (too-long input), 'content_policy'. LIVE:
    this changes how the router behaves for that model immediately."""
    _require_configured("set_fallbacks")
    m = _alias(model)
    if not isinstance(fallback_models, list) or not fallback_models \
            or not all(isinstance(f, str) and ALIAS_RE.match(f) for f in fallback_models):
        raise LiteLLMError("set_fallbacks — fallback_models must be a non-empty list of alias strings")
    if fallback_type not in FALLBACK_TYPES:
        raise LiteLLMError(f"set_fallbacks — fallback_type must be one of {FALLBACK_TYPES}")
    resp = await _call("POST", "/fallback",
                       {"model": m, "fallback_models": fallback_models, "fallback_type": fallback_type})
    _check(resp, "set_fallbacks")
    return {"ok": True, "kind": "litellm", "operation": "set_fallbacks",
            "model": m, "fallback_models": fallback_models, "fallback_type": fallback_type}


async def delete_fallbacks(model: str, fallback_type: str = "general") -> dict:
    """Remove a model's fallback chain (DELETE /fallback/{model}). LIVE: that model
    loses its configured fallback behavior for `fallback_type`."""
    _require_configured("delete_fallbacks")
    m = _alias(model)
    if fallback_type not in FALLBACK_TYPES:
        raise LiteLLMError(f"delete_fallbacks — fallback_type must be one of {FALLBACK_TYPES}")
    resp = await _call("DELETE", f"/fallback/{m}?fallback_type={fallback_type}")
    _check(resp, "delete_fallbacks")
    return {"ok": True, "kind": "litellm", "operation": "delete_fallbacks",
            "model": m, "fallback_type": fallback_type}


async def update_key(
    key: str, models: list | None = None, metadata: dict | None = None,
    tags: list | None = None, key_alias: str | None = None, duration: str | None = None,
) -> dict:
    """Edit an existing virtual key in place (POST /key/update), addressed by its
    token (the hash from list_keys — Central Command holds no plaintext). Change its model scope,
    metadata, tags, alias (rename), or duration. Only sent fields change (patch
    semantics). Budgets/rate-limits are deliberately NOT touched here."""
    _require_configured("update_key")
    if not isinstance(key, str) or not key.strip():
        raise LiteLLMError("update_key — key (the token/hash from list_keys) is required")
    payload: dict = {"key": key.strip()}
    if models is not None:
        if not isinstance(models, list) or not all(isinstance(m, str) and m for m in models):
            raise LiteLLMError("update_key — models must be a list of alias strings")
        payload["models"] = models
    if metadata is not None:
        if not isinstance(metadata, dict):
            raise LiteLLMError("update_key — metadata must be an object")
        payload["metadata"] = metadata
    if tags is not None:
        if not isinstance(tags, list) or not all(isinstance(t, str) and t for t in tags):
            raise LiteLLMError("update_key — tags must be a list of strings")
        payload.setdefault("metadata", {})["tags"] = tags
    if key_alias is not None:
        if not KEY_ALIAS_RE.match(key_alias):
            raise LiteLLMError(f"update_key — bad key_alias {key_alias!r}")
        payload["key_alias"] = key_alias
    if duration is not None:
        payload["duration"] = duration
    if len(payload) == 1:
        raise LiteLLMError("update_key — nothing to change")
    resp = await _call("POST", "/key/update", payload)
    _check(resp, "update_key")
    return {"ok": True, "kind": "litellm", "operation": "update_key",
            "key_alias": key_alias, "token_id": key.strip(),
            "updated": sorted(k for k in payload if k != "key")}


async def create_team(team_alias: str, models: list | None = None) -> dict:
    """Create a team (POST /team/new) for attribution / model scoping. `team_alias`
    is the human name; `models` scopes which aliases the team's keys may call
    (omit = all). Budgets/rate-limits are NOT set here."""
    _require_configured("create_team")
    if not isinstance(team_alias, str) or not KEY_ALIAS_RE.match(team_alias or ""):
        raise LiteLLMError(f"bad team_alias {team_alias!r}")
    payload: dict = {"team_alias": team_alias}
    if models is not None:
        if not isinstance(models, list) or not all(isinstance(m, str) and m for m in models):
            raise LiteLLMError("create_team — models must be a list of alias strings")
        payload["models"] = models
    resp = await _call("POST", "/team/new", payload)
    _check(resp, "create_team")
    body = resp.json()
    return {"ok": True, "kind": "litellm", "operation": "create_team", "team": {
        "team_alias": body.get("team_alias") or team_alias,
        "team_id": body.get("team_id"),
        "models": body.get("models") or (models or []),
    }}


async def delete_team(team_id: str) -> dict:
    """Delete a team by its team_id (POST /team/delete). LIVE: the team's keys lose
    their team scope; reversible by re-creating."""
    _require_configured("delete_team")
    if not isinstance(team_id, str) or not team_id.strip():
        raise LiteLLMError("delete_team — team_id must be a non-empty string")
    resp = await _call("POST", "/team/delete", {"team_ids": [team_id.strip()]})
    _check(resp, "delete_team")
    return {"ok": True, "kind": "litellm", "operation": "delete_team", "team_id": team_id.strip()}


async def delete_key(key_alias: str | None = None, key: str | None = None) -> dict:
    """Delete a virtual key (POST /key/delete) by its ALIAS (preferred — the
    plaintext key is not something Central Command holds). Passing a raw key is honored but
    unusual. Reversible in effect (a new key can be issued) but LIVE: deleting a
    key Central Command routes through would break that traffic until re-issued."""
    _require_configured("delete_key")
    payload: dict = {}
    if key_alias:
        if not KEY_ALIAS_RE.match(key_alias):
            raise LiteLLMError(f"bad key_alias {key_alias!r}")
        payload["key_aliases"] = [key_alias]
    elif key:
        payload["keys"] = [key]
    else:
        raise LiteLLMError("delete_key — give a key_alias (preferred) or a key")
    resp = await _call("POST", "/key/delete", payload)
    _check(resp, "delete_key")
    return {"ok": True, "kind": "litellm", "operation": "delete_key",
            "deleted": key_alias or _mask_secret(key or "")}


async def delete_model(model_id: str) -> dict:
    """Remove a model by its proxy model_id (from list_models). Reversible — the
    operator can re-add it — but callers should confirm they have the right id;
    the proxy deletes by id, not by alias."""
    _require_configured("delete_model")
    if not isinstance(model_id, str) or not model_id.strip():
        raise LiteLLMError("delete_model — model_id must be a non-empty string")
    resp = await _call("POST", "/model/delete", {"id": model_id.strip()})
    _check(resp, "delete_model")
    return {"ok": True, "kind": "litellm", "operation": "delete_model",
            "model": {"model_id": model_id.strip()}}


# --- MCP gateway registration (D-sandbox slice 4) -----------------------------

MCP_TRANSPORTS = ("http", "sse")


async def register_mcp_server(
    server_name: str, alias: str, url: str,
    transport: str = "http", description: str | None = None,
) -> dict:
    """Register a deployed MCP server with LiteLLM's MCP gateway (POST
    /v1/mcp/server). LIVE: any agent later granted access through the proxy
    can reach this server's tools from the moment this call returns —
    Executor-only, post-approval, same trust split as add_model."""
    _require_configured("register_mcp_server")
    if transport not in MCP_TRANSPORTS:
        raise LiteLLMError(f"register_mcp_server — transport must be one of {MCP_TRANSPORTS}")
    payload: dict = {
        "server_name": server_name, "alias": alias, "url": url, "transport": transport,
    }
    if description:
        payload["description"] = description
    resp = await _call("POST", "/v1/mcp/server", payload)
    _check(resp, "register_mcp_server")
    body = resp.json()
    return {"ok": True, "kind": "litellm", "operation": "register_mcp_server", "server": {
        "litellm_server_id": body.get("server_id"),
        "server_name": body.get("server_name") or server_name,
        "alias": body.get("alias") or alias,
        "url": body.get("url") or url,
        "transport": body.get("transport") or transport,
    }}


async def add_mcp_server_to_key(key: str, litellm_server_id: str) -> dict:
    """Add an MCP server to a virtual key's `object_permission.mcp_servers`
    (merge, never clobber: GET /key/info first, then POST /key/update with the
    widened list). Idempotent — already-present is a clean no-op.

    Why this exists (live 401→empty-tools finding, 2026-08-05): a virtual key
    sees NO MCP servers until the server id is in its object_permission — the
    proxy returns 200 with an empty tools list, a silent failure. Registration
    couples this so a registered server is actually callable by the key the
    agents ride."""
    _require_configured("add_mcp_server_to_key")
    if not isinstance(key, str) or not key.strip():
        raise LiteLLMError("add_mcp_server_to_key — key is required")
    if not isinstance(litellm_server_id, str) or not litellm_server_id.strip():
        raise LiteLLMError("add_mcp_server_to_key — litellm_server_id is required")
    key = key.strip()
    server_id = litellm_server_id.strip()

    # Self-lookup: /key/info with the key itself as the Bearer identity
    # returns that key's own info (live-verified 2026-08-05) — the plaintext
    # never rides a query string, where access logs and error URLs see it.
    resp = await _call("GET", "/key/info", auth_key=key)
    _check(resp, "add_mcp_server_to_key (key/info)")
    info = (resp.json() or {}).get("info") or {}
    current = (info.get("object_permission") or {}).get("mcp_servers") or []
    if server_id in current:
        return {"ok": True, "kind": "litellm", "operation": "add_mcp_server_to_key",
                "litellm_server_id": server_id, "changed": False}

    resp = await _call("POST", "/key/update", {
        "key": key,
        "object_permission": {"mcp_servers": [*current, server_id]},
    })
    _check(resp, "add_mcp_server_to_key (key/update)")
    return {"ok": True, "kind": "litellm", "operation": "add_mcp_server_to_key",
            "litellm_server_id": server_id, "changed": True}


async def remove_mcp_server_from_key(key: str, litellm_server_id: str) -> dict:
    """Remove an MCP server from a virtual key's `object_permission.mcp_servers`
    (merge, never clobber: GET /key/info first, then POST /key/update with the
    narrowed list) — the counterpart to add_mcp_server_to_key, so a removed
    server doesn't leave a dangling id in the shared key's permission list.
    Idempotent — already-absent is a clean no-op, no /key/update call."""
    _require_configured("remove_mcp_server_from_key")
    if not isinstance(key, str) or not key.strip():
        raise LiteLLMError("remove_mcp_server_from_key — key is required")
    if not isinstance(litellm_server_id, str) or not litellm_server_id.strip():
        raise LiteLLMError("remove_mcp_server_from_key — litellm_server_id is required")
    key = key.strip()
    server_id = litellm_server_id.strip()

    resp = await _call("GET", "/key/info", auth_key=key)
    _check(resp, "remove_mcp_server_from_key (key/info)")
    info = (resp.json() or {}).get("info") or {}
    current = (info.get("object_permission") or {}).get("mcp_servers") or []
    if server_id not in current:
        return {"ok": True, "kind": "litellm", "operation": "remove_mcp_server_from_key",
                "litellm_server_id": server_id, "changed": False}

    resp = await _call("POST", "/key/update", {
        "key": key,
        "object_permission": {"mcp_servers": [s for s in current if s != server_id]},
    })
    _check(resp, "remove_mcp_server_from_key (key/update)")
    return {"ok": True, "kind": "litellm", "operation": "remove_mcp_server_from_key",
            "litellm_server_id": server_id, "changed": True}


async def deregister_mcp_server(litellm_server_id: str) -> None:
    """Remove a server from LiteLLM's MCP gateway (DELETE /v1/mcp/server/{id}).
    Accepts 200/202/204 as success (the live proxy returns 202)."""
    _require_configured("deregister_mcp_server")
    if not isinstance(litellm_server_id, str) or not litellm_server_id.strip():
        raise LiteLLMError("deregister_mcp_server — litellm_server_id must be a non-empty string")
    resp = await _call("DELETE", f"/v1/mcp/server/{litellm_server_id.strip()}")
    _check(resp, "deregister_mcp_server")


async def list_mcp_servers() -> list:
    """Every server currently registered with the proxy's MCP gateway (GET
    /v1/mcp/server) — read before registering, to check idempotently whether a
    server is already there."""
    _require_configured("list_mcp_servers")
    resp = await _call("GET", "/v1/mcp/server")
    _check(resp, "list_mcp_servers")
    body = resp.json()
    return body if isinstance(body, list) else (body.get("data") or [])


# --- MCP tool discovery + invocation (D-sandbox slice 5) ---------------------
# LiteLLM exposes tool discovery/invocation under /mcp-rest, NOT under the
# /v1/mcp/server admin path above. Both shapes are VERIFIED against the live
# proxy via the echo-demo arc (2026-08-04/05): tools/call directly (the gated
# echo call returned through it — see executor._mcp_tool_call), tools/list by
# the mcp_tool.echo row it seeded (upsert_mcp_tools no-ops on an empty list,
# so the row exists only because discovery got a real non-empty response).
# The caller (executor._litellm_register_mcp_server) treats ANY failure here
# as non-fatal, and call_mcp_tool's caller wraps it in ExecutorError like
# every other façade call.


async def list_mcp_server_tools(server_name: str) -> list[dict]:
    """The tools ONE registered server exposes (GET /mcp-rest/tools/list?
    server_id=<name>) — called once, right after a successful
    litellm.register_mcp_server, to seed `mcp_tool` rows. Shape verified live
    2026-08-05 (echo-demo discovery seeded mcp_tool.echo): the server's
    underscored name/alias as a `server_id` query param.
    Returns a list of {'name', 'description'} — whatever extra fields the
    proxy sends back are dropped."""
    _require_configured("list_mcp_server_tools")
    resp = await _call("GET", f"/mcp-rest/tools/list?server_id={server_name}")
    _check(resp, "list_mcp_server_tools")
    body = resp.json()
    rows = body if isinstance(body, list) else (body.get("tools") or body.get("data") or [])
    return [
        {"name": t.get("name"), "description": t.get("description") or ""}
        for t in rows if isinstance(t, dict) and t.get("name")
    ]


async def call_mcp_tool(server_name: str, tool_name: str, arguments: dict) -> dict:
    """Invoke one MCP-served tool through the proxy (POST /mcp-rest/tools/call)
    with the admin key — Executor-only, post-approval, same trust split as
    every other litellm.* write. Body shape verified live 2026-08-04/05:
    {'server_id', 'name', 'arguments'} — server_id in the BODY, and it 404s
    on a name it never registered."""
    _require_configured("call_mcp_tool")
    payload = {"server_id": server_name, "name": tool_name, "arguments": arguments or {}}
    resp = await _call("POST", "/mcp-rest/tools/call", payload)
    _check(resp, "call_mcp_tool")
    return resp.json()


# --- usage (read-only; LiteLLM is the system of record for spend/limits) -------


async def usage_snapshot(days: int = 30) -> dict:
    """Spend + token usage as LiteLLM records it. Display-only: Central Command
    never re-derives or persists usage itself — budgets and limits live in
    LiteLLM (The operator's call, 2026-07-22), the cockpit just shows its numbers."""
    from datetime import date, timedelta

    _require_configured("usage_snapshot")
    start = (date.today() - timedelta(days=days)).isoformat()
    end = (date.today() + timedelta(days=1)).isoformat()

    resp = await _call("GET", "/global/spend")
    _check(resp, "usage_snapshot")
    global_spend = resp.json()

    # Aggregated server-side, NOT summed here from raw /spend/logs rows. The
    # raw dump grew with history (107 MB of spend rows by 2026-08-15) until
    # serializing a month of it blew the 30s timeout — the first error the new
    # log console ever surfaced. /user/daily/activity is the OSS aggregation
    # endpoint (LiteLLM's own usage UI reads it; /global/spend/report is the
    # Enterprise-gated sibling, probed 2026-08-15 and refused).
    #
    # MEASURED 2026-08-21 on the live proxy: pagination slices the underlying
    # raw rows (per key×model×date), not whole days — page 1 at page_size=31
    # held one partial-day bucket (108 requests) while the full window held
    # 1700+, and the response `metadata` totals disagreed with summed rows.
    # So: page-walk and merge by date across pages, never trust metadata
    # totals (only has_more/total_pages, to decide when to stop).
    by_model: dict[str, dict] = {}
    by_date: dict[str, dict] = {}
    page = 1
    while page <= 60:  # runaway guard
        resp = await _call(
            "GET",
            f"/user/daily/activity?start_date={start}&end_date={end}"
            f"&page_size={days + 1}&page={page}",
        )
        _check(resp, "usage_snapshot")
        body = resp.json()
        results = body.get("results") or []
        if not results:
            break
        for day in results:
            if not isinstance(day, dict):
                continue
            day_agg = by_date.setdefault(
                str(day.get("date")),
                {"date": str(day.get("date")), "spend": 0.0, "input": 0, "output": 0, "requests": 0},
            )
            models = (day.get("breakdown") or {}).get("models") or {}
            for model, entry in models.items():
                m = (entry or {}).get("metrics") or {}
                spend = float(m.get("spend") or 0)
                inp = int(m.get("prompt_tokens") or 0)
                out = int(m.get("completion_tokens") or 0)
                reqs = int(m.get("api_requests") or 0)

                agg = by_model.setdefault(
                    str(model), {"cost": 0.0, "input": 0, "output": 0, "requests": 0}
                )
                agg["cost"] += spend
                agg["input"] += inp
                agg["output"] += out
                agg["requests"] += reqs

                day_agg["spend"] += spend
                day_agg["input"] += inp
                day_agg["output"] += out
                day_agg["requests"] += reqs

        metadata = body.get("metadata") or {}
        total_pages = metadata.get("total_pages")
        has_more = metadata.get("has_more")
        if has_more is False:
            break
        if total_pages is not None and page >= total_pages:
            break
        if has_more is None and total_pages is None:
            break  # proxy isn't paginating — one page is the whole answer
        page += 1

    daily = [by_date[d] for d in sorted(by_date)]
    window_spend = sum(d["spend"] for d in daily)

    return {
        "spend": float(global_spend.get("spend") or 0),
        "max_budget": global_spend.get("max_budget"),
        "window_days": days,
        "window_spend": window_spend,
        "daily": daily,
        "proxy_ui_url": settings.llm_proxy_ui_url or None,
        "models": [
            {"model": m, **agg}
            for m, agg in sorted(by_model.items(), key=lambda kv: -kv[1]["cost"])
        ],
    }
