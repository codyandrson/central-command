"""resolve_model() — the single seam where the agent's model is chosen.

Locks the one-provider shape: CC_LLM_BASE_URL + CC_LLM_API_KEY configure the
endpoint agents talk to, presence is enablement (no flag, no fallback chain),
base_url is ALWAYS pinned so ambient env vars cannot redirect traffic, and
missing config raises in live mode. Demo mode returns the deterministic model
without touching any of it.

Builds models only — no provider is called. It needs Postgres since
2026-08-19 all the same: `resolve_model` reads the agent row (the operator's
per-agent model + thinking), so an agent_id now costs a query.
"""

import pytest

from central_command.config import settings
from central_command.runtime.models import LLMProviderNotConfigured, resolve_model
from tests.conftest import needs_pg

pytestmark = needs_pg


def _base_url(model):
    return str(model._provider.client.base_url)


def _api_key(model):
    return model._provider.client.api_key


def _configure(monkeypatch, base_url="http://localhost:4000", api_key="sk-cc-virtual"):
    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(settings, "llm_base_url", base_url)
    monkeypatch.setattr(settings, "llm_api_key", api_key)


async def test_the_configured_provider_is_used(monkeypatch):
    _configure(monkeypatch)

    model = await resolve_model()
    assert type(model).__name__ == "OpenAIChatModel"
    assert "localhost:4000" in _base_url(model)
    assert _api_key(model) == "sk-cc-virtual"


async def test_anthropic_is_just_another_configured_base_url(monkeypatch):
    """Bypassing LiteLLM is a config value, not a code path."""
    _configure(monkeypatch, base_url="https://api.anthropic.com", api_key="sk-ant-direct")

    model = await resolve_model()
    assert "api.anthropic.com" in _base_url(model)
    assert _api_key(model) == "sk-ant-direct"


async def test_ambient_base_url_cannot_redirect_traffic(monkeypatch):
    """The regression this shape exists to kill: the old no-base_url branch let
    the SDK pick up its base URL from the environment (the Claude Code CLI sets
    ANTHROPIC_BASE_URL on this box; OPENAI_* is the pair the OpenAI client
    reads). base_url is now always passed explicitly."""
    _configure(monkeypatch, base_url="https://api.anthropic.com", api_key="sk-ant-direct")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://evil.example")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-ambient")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://evil.example")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-ambient")

    model = await resolve_model()
    assert "evil.example" not in _base_url(model)
    assert "api.anthropic.com" in _base_url(model)
    assert _api_key(model) == "sk-ant-direct"


async def test_missing_config_raises_naming_both_variables(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(settings, "llm_base_url", "")
    monkeypatch.setattr(settings, "llm_api_key", "")

    with pytest.raises(LLMProviderNotConfigured) as exc:
        await resolve_model()
    assert "CC_LLM_BASE_URL" in str(exc.value)
    assert "CC_LLM_API_KEY" in str(exc.value)


async def test_missing_key_alone_raises(monkeypatch):
    _configure(monkeypatch, api_key="")
    monkeypatch.delenv("CC_LLM_PROXY_KEY_INBOX_TRIAGE", raising=False)

    with pytest.raises(LLMProviderNotConfigured) as exc:
        await resolve_model(agent_id="inbox-triage")
    assert "CC_LLM_API_KEY" in str(exc.value)


async def test_missing_base_url_alone_raises(monkeypatch):
    _configure(monkeypatch, base_url="")

    with pytest.raises(LLMProviderNotConfigured) as exc:
        await resolve_model()
    assert "CC_LLM_BASE_URL" in str(exc.value)


async def test_per_agent_key_selected_when_configured(monkeypatch):
    """Attribution: an agent with its own CC_LLM_PROXY_KEY_<AGENT> env var
    authenticates with that key against the same base URL, so spend attributes
    to it."""
    _configure(monkeypatch, api_key="sk-shared")
    monkeypatch.setenv("CC_LLM_PROXY_KEY_INBOX_TRIAGE", "sk-triage-key")

    model = await resolve_model(agent_id="inbox-triage")
    assert _api_key(model) == "sk-triage-key"
    assert "localhost:4000" in _base_url(model)


async def test_agent_without_its_own_key_uses_the_shared_key(monkeypatch):
    _configure(monkeypatch, api_key="sk-shared")
    monkeypatch.delenv("CC_LLM_PROXY_KEY_JIRA_EXPERT", raising=False)

    model = await resolve_model(agent_id="jira-expert")
    assert _api_key(model) == "sk-shared"


async def test_per_agent_model_override_selected(monkeypatch):
    """Tiering canary: CC_MODEL_<AGENT_ID> points one agent at a different
    LiteLLM alias (e.g. the cc-smart adaptive router) without moving the rest."""
    _configure(monkeypatch)
    monkeypatch.setenv("CC_MODEL_INBOX_TRIAGE", "anthropic:cc-smart")

    assert (await resolve_model(agent_id="inbox-triage")).model_name == "cc-smart"


async def test_per_agent_model_falls_back_to_default(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(settings, "default_model", "anthropic:claude-sonnet-5")
    monkeypatch.delenv("CC_MODEL_JIRA_EXPERT", raising=False)

    assert (await resolve_model(agent_id="jira-expert")).model_name == "claude-sonnet-5"


async def test_explicit_model_override_wins_over_everything(monkeypatch):
    _configure(monkeypatch)
    sentinel = object()
    assert await resolve_model(sentinel) is sentinel


async def test_explicit_model_param_beats_per_agent_override(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setenv("CC_MODEL_INBOX_TRIAGE", "anthropic:cc-smart")
    sentinel = object()
    assert await resolve_model(sentinel, agent_id="inbox-triage") is sentinel


async def test_demo_mode_returns_the_spike_model(monkeypatch):
    """Demo mode returns before any provider config is read — even unconfigured."""
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "llm_base_url", "")
    monkeypatch.setattr(settings, "llm_api_key", "")

    model = await resolve_model()
    assert type(model).__name__ != "OpenAIChatModel"


async def test_demo_mode_ignores_per_agent_key(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setenv("CC_LLM_PROXY_KEY_INBOX_TRIAGE", "sk-triage-key")
    # Demo returns the deterministic model regardless of agent/key.
    assert type(await resolve_model(agent_id="inbox-triage")).__name__ != "OpenAIChatModel"


async def test_live_model_pins_max_tokens_above_the_client_default(monkeypatch):
    """The Anthropic-era client defaulted max_tokens to 4096, which silently
    truncated the coach's `charter.update` mid-string on the first live run —
    the tool call arrived unterminated and could not be parsed, so the run died
    after four identical retries rather than proposing anything. On the OpenAI
    path an UNSET max_tokens is an unbounded generation on the local model
    (llama.cpp n_predict -1) — the runaway-thinking failure mode.

    The ceiling is pinned at the ONE model seam so every agent inherits it. If
    this assertion ever fails, agent runs have no output ceiling at all and any
    action carrying a whole document is at the server's mercy.
    """
    from central_command.config import settings as cfg
    from central_command.runtime.models import resolve_model

    monkeypatch.setattr(cfg, "demo_mode", False)
    monkeypatch.setattr(cfg, "llm_base_url", "http://localhost:4000")
    monkeypatch.setattr(cfg, "llm_api_key", "sk-test-not-a-real-key")

    m = await resolve_model(agent_id="coach")
    assert m.settings is not None, "no ModelSettings attached — back on the 4096 default"
    assert m.settings["max_tokens"] == cfg.max_output_tokens
    assert m.settings["max_tokens"] > 4096, (
        "max_tokens must exceed pydantic-ai's 4096 default; a full charter "
        "does not fit in 4096 tokens once JSON-escaped"
    )


async def test_the_ceiling_clears_the_largest_real_charter():
    """The ceiling is only meaningful against the document it must carry."""
    from central_command.config import settings as cfg
    from central_command.runtime.agent import builtin_charter

    biggest = max(
        len(builtin_charter(a))
        for a in ("inbox-triage", "jira-expert", "auditor", "orchestrator",
                  "litellm-manager", "coach")
    )
    # ~4 chars/token, and JSON escaping inflates a newline-dense document.
    assert cfg.max_output_tokens > (biggest / 4) * 2, (
        f"largest charter is {biggest} chars (~{biggest // 4} tokens); "
        f"max_output_tokens={cfg.max_output_tokens} leaves no room to re-emit it"
    )
