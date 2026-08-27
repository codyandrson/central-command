"""Model resolution — the single seam where the agent's model is chosen.

In demo mode we drive agents with the deterministic FunctionModel (no API key,
repeatable). Otherwise we build the configured model against ONE configured
provider: CC_LLM_BASE_URL + CC_LLM_API_KEY. Presence is enablement — there is
no on/off flag and no fallback chain. The client speaks OpenAI
/v1/chat/completions (2026-08-19; previously Anthropic /v1/messages): the
backing model is a local Qwen registered in LiteLLM as an `openai/...`
deployment, so the Anthropic path cost a full format translation in each
direction for nothing — the proposal spine parses pydantic-ai's normalized
tool-call args either way. Point the base URL at the self-hosted LiteLLM proxy;
the model name doubles as the alias/model id sent to that endpoint.

base_url is ALWAYS passed explicitly, so an ambient OPENAI_BASE_URL /
OPENAI_API_KEY (or the ANTHROPIC_* pair the Claude Code CLI sets on this box)
in the process environment can never redirect agent traffic. If either setting
is missing in live mode we raise rather than build a half-configured client.

This chooses ONE destination and fails if it is unreachable — it provides no
failover. Retry/fallback across models is LiteLLM's job, configured in the
proxy, not here.
"""

import logging

from central_command.config import settings

log = logging.getLogger(__name__)


class LLMProviderNotConfigured(RuntimeError):
    """Live mode was asked for a model with no provider configured."""


async def resolve_model(model=None, agent_id=None, model_name=None, thinking=None):
    """Precedence, model AND thinking:
    schedule override (`model_name`/`thinking`) > the agent ROW > CC_MODEL_<AGENT_ID>
    > the global default. The session override sits above all of it and lives in
    `resolve_session_model`.

    Async since 2026-08-19: the agent row is the operator's per-agent model/
    thinking setting, and reading it is a DB round-trip. The demo short-circuit
    stays ABOVE that read, so the offline suite never touches Postgres here.
    """
    if model is not None:
        return model
    if settings.demo_mode:
        # Per-agent demo stand-ins, at the seam rather than at call sites.
        # `make_spike_model` is the INBOX-TRIAGE double — its only move is a
        # Jira due-date proposal — so an agent whose whole behaviour is
        # something else proves nothing running on it. The steward and the
        # auditor solve this with a local `_resolve_model` because their run
        # paths are their own; the EA's are not (the heartbeat drives it
        # through `create_and_run_task`, the operator through `converse`, the
        # answer through `resume_with_answer` — three callers, all landing
        # here), so its double belongs here.
        if agent_id == "ea":
            from central_command.runtime.spike_model import make_ea_model

            return make_ea_model()
        from central_command.runtime.spike_model import make_spike_model

        return make_spike_model()

    # Live: build the configured model.
    if agent_id and (model_name is None or thinking is None):
        from central_command.db import repo

        row = await repo.get_agent(agent_id) or {}
        # '' is the UNSET sentinel in both columns — never a model id.
        model_name = model_name or (row.get("model") or None)
        thinking = thinking or (row.get("thinking") or None)
    name = (
        model_name or settings.agent_model(agent_id) or settings.default_model
    ).split(":", 1)[-1]
    return await _live_model(name, agent_id, thinking=thinking)


# The thinking levels Qwen3.8's chat template accepts, and how each spells
# itself in chat_template_kwargs. "off" is `enable_thinking: false` — there is
# NO `reasoning_effort: "none"` (it hits an explicit raise_exception in the
# template and every request 500s; measured 2026-08-18). The gateway validates
# against this same set before a level ever reaches the session row.
THINKING_LEVELS = ("off", "low", "medium", "xhigh")


def _thinking_extra_body(thinking: str) -> dict:
    """LiteLLM passes `extra_body` through to llama.cpp verbatim (its
    openai-compatible handler spreads it into the outgoing request, exempt from
    drop_params — measured live 2026-08-19, and the mechanism OpenHands ships
    for exactly this Qwen3 control). The no-cache rider is load-bearing:
    LiteLLM's cache key IGNORES chat_template_kwargs, so without it a thinking
    session can be served a non-thinking session's cached answer verbatim
    (measured: identical prompt, opposite settings, same cached response)."""
    kwargs = (
        {"enable_thinking": False}
        if thinking == "off"
        else {"enable_thinking": True, "reasoning_effort": thinking}
    )
    return {"chat_template_kwargs": kwargs, "cache": {"no-cache": True}}


# Our levels → LiteLLM's `reasoning_effort`, which it translates into the
# provider's native reasoning control (for Anthropic: output_config.effort /
# thinking). "none" is LiteLLM's documented way to send no thinking field at
# all, and "xhigh" is a level it accepts verbatim — no remapping needed.
_ANTHROPIC_EFFORT = {"off": "none", "low": "low", "medium": "medium", "xhigh": "xhigh"}


async def _thinking_body(model_name: str, thinking: str) -> dict | None:
    """How this model is asked to think — DECLARED by the proxy, never guessed
    from the model's name (same rule as `supports_vision`). An undeclared
    mechanism sends NOTHING: a thinking control invented for a model that does
    not implement it is either a 500 or, worse, silently ignored."""
    from central_command.integrations import litellm as litellm_mod

    try:
        mechanism, _levels = await litellm_mod.thinking_mechanism(model_name)
    except litellm_mod.LiteLLMError as exc:
        log.warning("thinking %r for %s: cannot read the proxy's declaration (%s)"
                    " — sending no thinking control", thinking, model_name, exc)
        return None
    if mechanism == "qwen_template":
        return _thinking_extra_body(thinking)
    if mechanism == "anthropic":
        effort = _ANTHROPIC_EFFORT.get(thinking)
        if effort is None:
            log.warning("thinking %r is not a THINKING_LEVELS value", thinking)
            return None
        # The no-cache rider for the same reason as the Qwen path: a cached
        # answer must never cross a reasoning-setting boundary.
        return {"reasoning_effort": effort, "cache": {"no-cache": True}}
    log.warning(
        "thinking %r requested for %s but model_info declares no"
        " thinking_mechanism — sending no thinking control", thinking, model_name,
    )
    return None


async def _live_model(model_name: str, agent_id=None, thinking: str | None = None):
    """Build the live client for one model NAME — the provider, the per-agent
    key and the pinned max_tokens, with the name already resolved by
    `resolve_model`. `thinking` (a THINKING_LEVELS value, from whichever level
    of the precedence won) rides as whatever control this model DECLARES; None
    sends nothing and the deployment default applies. Async because that
    declaration is a proxy read — made only when a level is actually set."""
    from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
    from pydantic_ai.providers.openai import OpenAIProvider

    # A PER-AGENT key (CC_LLM_PROXY_KEY_<AGENT_ID>) authenticates that agent
    # separately against the same base URL — the spend-attribution seam. Agents
    # without one use the shared key.
    base_url = settings.llm_base_url
    api_key = settings.agent_proxy_key(agent_id) or settings.llm_api_key
    if not base_url or not api_key:
        missing = [
            name
            for name, value in (("CC_LLM_BASE_URL", base_url), ("CC_LLM_API_KEY", api_key))
            if not value
        ]
        raise LLMProviderNotConfigured(
            "no LLM provider configured for live mode — set CC_LLM_BASE_URL and "
            f"CC_LLM_API_KEY in .env (missing: {', '.join(missing)}). "
            "CC_LLM_BASE_URL is the LiteLLM proxy (e.g. http://localhost:4000) "
            "or https://api.anthropic.com."
        )

    # The OpenAI client appends /chat/completions to base_url, so the /v1
    # segment must be on the base (the Anthropic client used to add it itself —
    # CC_LLM_BASE_URL stays the bare proxy root either way).
    base = base_url.rstrip("/")
    if not base.endswith("/v1"):
        base += "/v1"
    provider = OpenAIProvider(api_key=api_key, base_url=base)
    # max_tokens is pinned HERE, at the one model seam, rather than left to the
    # client default: an unset max_tokens is an unbounded generation on the
    # local model (llama.cpp n_predict -1), and the Anthropic-era client
    # defaulted to 4096, which silently truncated any tool call that carried a
    # whole document. See settings.max_output_tokens for the incident. Pinning
    # it at the seam means every agent inherits it and no future call site can
    # forget.
    model_settings = OpenAIChatModelSettings(max_tokens=settings.max_output_tokens)
    if thinking:
        body = await _thinking_body(model_name, thinking)
        if body:
            model_settings["extra_body"] = body
    return OpenAIChatModel(model_name, provider=provider, settings=model_settings)


async def resolve_session_model(session_id: str | None, agent_id: str | None):
    """`resolve_model` for a run that belongs to a SESSION: the operator's
    per-session model override (cockpit dropdown → `session.model_override`)
    wins, otherwise the normal precedence applies.

    The override is ignored in demo mode on purpose — the deterministic doubles
    are not addressable model ids, so honouring a LiteLLM alias there would swap
    a stand-in for a live call in the one mode that promises it never does.

    The thinking override rides the same way: it applies with or without a
    model override, so a session can think harder on the default model.
    """
    if session_id and not settings.demo_mode:
        from central_command.db import repo

        override = await repo.get_session_model_override(session_id)
        thinking = await repo.get_session_thinking_override(session_id)
        if override or thinking:
            # Named like CC_MODEL_<AGENT_ID> would be — a LiteLLM model id, not
            # a pydantic-ai "provider:model" ref — so it goes through the same
            # resolver rather than being returned raw for inference. Whichever
            # half the session does NOT override falls through the normal
            # precedence (agent row, then env, then default), which is why this
            # hands both to `resolve_model` instead of re-deriving the name.
            return await resolve_model(
                agent_id=agent_id, model_name=override or None,
                thinking=thinking or None,
            )
    return await resolve_model(agent_id=agent_id)
