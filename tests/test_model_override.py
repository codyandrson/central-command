"""The cockpit's model dropdown, end to end.

Three claims, one per layer:
  * the catalog the dropdown reads comes from LiteLLM and says so when the proxy
    is unreachable (an empty list would read as "this deployment has no models");
  * choosing a model PERSISTS a per-session override that the run path actually
    resolves to — the whole point, and the thing the old `sessions.patch` no-op
    faked;
  * choosing an EFFORT persists a per-session thinking override that the run
    path delivers as chat_template_kwargs (with the no-cache rider — LiteLLM's
    cache key ignores chat_template_kwargs, so a cached answer would leak
    across opposite thinking settings).

Wire shape is asserted here, in the backend, on purpose: the cockpit hand-writes
its own interfaces over untyped payloads, so a field the API stops sending is
invisible to `tsc` and to every frontend test.
"""

from __future__ import annotations

import uuid

import pytest

from central_command.api import nerve_gateway, routes
from central_command.config import settings
from central_command.db import repo
from central_command.integrations import litellm as litellm_mod
from central_command.runtime import models as models_mod
from tests.conftest import needs_pg

CATALOG = ["cc-default", "openai/gpt-5.2"]


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    # demo_mode: anything that reaches resolve_model() must not call Claude.
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "executor_mode", "dry_run")

    async def fake_catalog() -> list[str]:
        return list(CATALOG)

    monkeypatch.setattr(litellm_mod, "exposed_model_ids", fake_catalog)

    async def fake_levels() -> dict:
        return {"cc-default": list(models_mod.THINKING_LEVELS)}

    monkeypatch.setattr(litellm_mod, "thinking_levels_by_model", fake_levels)

    async def fake_mechanism(model_id: str):
        return "qwen_template", list(models_mod.THINKING_LEVELS)

    monkeypatch.setattr(litellm_mod, "thinking_mechanism", fake_mechanism)


async def _session(agent_id: str = "inbox-triage") -> str:
    session_id = "sess_" + uuid.uuid4().hex[:12]
    await repo.create_conversation_session(session_id, agent_id)
    return session_id


async def _noop(_frame: dict) -> None:
    return None


# --- the catalog the dropdown reads -------------------------------------------


async def test_catalog_endpoint_carries_the_fields_the_cockpit_reads():
    payload = await routes.list_model_catalog()
    assert set(payload) == {"models", "default", "override", "error"}
    # Objects, not bare ids: the dropdown needs each model's DECLARED thinking
    # levels to know whether to offer an effort control at all.
    assert payload["models"] == [
        {"id": "cc-default", "thinking_levels": list(models_mod.THINKING_LEVELS)},
        {"id": "openai/gpt-5.2", "thinking_levels": []},
    ]
    assert payload["error"] is None
    # The default is a bare model id, not a "provider:model" pydantic-ai ref —
    # the dropdown compares it against the LiteLLM ids by equality.
    assert ":" not in payload["default"]


@needs_pg
async def test_catalog_endpoint_reports_a_session_override():
    session_id = await _session()
    await repo.set_session_model_override(session_id, "openai/gpt-5.2")
    payload = await routes.list_model_catalog(session_id=session_id)
    assert payload["override"] == "openai/gpt-5.2"


async def test_unreachable_litellm_is_an_error_not_an_empty_catalog(monkeypatch):
    async def boom() -> list[str]:
        raise litellm_mod.LiteLLMError("model catalog — http://x unreachable: nope")

    monkeypatch.setattr(litellm_mod, "exposed_model_ids", boom)
    payload = await routes.list_model_catalog()
    assert payload["models"] == []
    assert "unreachable" in payload["error"]


# --- picking a model ----------------------------------------------------------


@needs_pg
async def test_patching_a_model_persists_the_override_and_the_run_resolves_to_it(monkeypatch):
    session_id = await _session()
    out = await nerve_gateway._dispatch(
        "sessions.patch",
        {"key": f"agent:inbox-triage:{session_id}", "model": "openai/gpt-5.2"},
        _noop,
    )
    assert out["model"] == "openai/gpt-5.2"
    assert await repo.get_session_model_override(session_id) == "openai/gpt-5.2"

    # …and the run path uses it. Live mode, with the model BUILDER stubbed: the
    # claim is "the override names the model the turn runs on", which is exactly
    # what _live_model is handed.
    monkeypatch.setattr(settings, "demo_mode", False)
    async def fake_live(name, agent_id=None, thinking=None):
        return ("built", name)

    monkeypatch.setattr(models_mod, "_live_model", fake_live)
    assert await models_mod.resolve_session_model(session_id, "inbox-triage") == (
        "built", "openai/gpt-5.2",
    )


@needs_pg
async def test_a_null_model_clears_the_override_back_to_the_agent_default():
    session_id = await _session()
    await repo.set_session_model_override(session_id, "openai/gpt-5.2")
    await nerve_gateway._dispatch(
        "sessions.patch", {"key": f"agent:inbox-triage:{session_id}", "model": None}, _noop,
    )
    assert await repo.get_session_model_override(session_id) is None


@needs_pg
async def test_a_model_litellm_does_not_serve_is_refused():
    session_id = await _session()
    with pytest.raises(Exception) as exc:
        await nerve_gateway._dispatch(
            "sessions.patch",
            {"key": f"agent:inbox-triage:{session_id}", "model": "claude-from-a-stale-tab"},
            _noop,
        )
    assert "unknown model" in str(getattr(exc.value, "detail", exc.value))
    assert await repo.get_session_model_override(session_id) is None


@needs_pg
async def test_demo_mode_ignores_the_override():
    """The doubles are not addressable model ids — honouring an alias in the one
    mode that promises no live call would be the lie demo mode exists to avoid."""
    session_id = await _session()
    await repo.set_session_model_override(session_id, "openai/gpt-5.2")
    resolved = await models_mod.resolve_session_model(session_id, "inbox-triage")
    assert resolved != "openai/gpt-5.2"
    assert not isinstance(resolved, str)


# --- picking an effort --------------------------------------------------------


@needs_pg
async def test_patching_an_effort_persists_and_the_run_delivers_it(monkeypatch):
    session_id = await _session()
    out = await nerve_gateway._dispatch(
        "sessions.patch",
        {"key": f"agent:inbox-triage:{session_id}", "thinkingLevel": "low"},
        _noop,
    )
    assert out["thinkingLevel"] == "low"
    assert await repo.get_session_thinking_override(session_id) == "low"

    # …and the run path builds a model whose settings carry the kwargs. Live
    # mode against a fake endpoint — the model is BUILT, never called.
    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(settings, "llm_base_url", "http://localhost:4000")
    monkeypatch.setattr(settings, "llm_api_key", "sk-test-not-a-real-key")
    m = await models_mod.resolve_session_model(session_id, "inbox-triage")
    extra = m.settings["extra_body"]
    assert extra["chat_template_kwargs"] == {"enable_thinking": True, "reasoning_effort": "low"}
    # The no-cache rider is load-bearing: LiteLLM's cache key ignores
    # chat_template_kwargs (measured 2026-08-19 — identical prompt, opposite
    # thinking, same cached answer).
    assert extra["cache"] == {"no-cache": True}
    # And the pin survives the settings merge — thinking must not cost the ceiling.
    assert m.settings["max_tokens"] == settings.max_output_tokens


@needs_pg
async def test_effort_off_spells_enable_thinking_false(monkeypatch):
    """'off' is `enable_thinking: false` — there is NO reasoning_effort "none"
    (the chat template raise_exceptions on it and every request 500s)."""
    session_id = await _session()
    await repo.set_session_thinking_override(session_id, "off")
    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(settings, "llm_base_url", "http://localhost:4000")
    monkeypatch.setattr(settings, "llm_api_key", "sk-test-not-a-real-key")
    m = await models_mod.resolve_session_model(session_id, "inbox-triage")
    kwargs = m.settings["extra_body"]["chat_template_kwargs"]
    assert kwargs == {"enable_thinking": False}
    assert "reasoning_effort" not in kwargs


@needs_pg
async def test_a_null_effort_clears_back_to_the_deployment_default():
    session_id = await _session()
    await repo.set_session_thinking_override(session_id, "medium")
    await nerve_gateway._dispatch(
        "sessions.patch", {"key": f"agent:inbox-triage:{session_id}", "thinkingLevel": None}, _noop,
    )
    assert await repo.get_session_thinking_override(session_id) is None


@needs_pg
async def test_an_effort_the_model_does_not_accept_is_refused():
    """The vendored dropdown historically offered 'high'/'minimal'/'adaptive';
    the template accepts none of them, and delivering one is a 500 on the
    session's NEXT turn — far from the click. Refuse at the click instead."""
    session_id = await _session()
    with pytest.raises(Exception) as exc:
        await nerve_gateway._dispatch(
            "sessions.patch",
            {"key": f"agent:inbox-triage:{session_id}", "thinkingLevel": "high"},
            _noop,
        )
    assert "unknown thinking level" in str(getattr(exc.value, "detail", exc.value))
    assert await repo.get_session_thinking_override(session_id) is None


def test_session_rows_carry_the_thinking_level_the_dropdown_reads():
    """Wire shape, asserted in the backend on purpose (see module docstring):
    `useModelEffort` reads `thinkingLevel` off the session row."""
    import datetime as dt

    row = {
        "id": "sess_x", "agent_id": "inbox-triage", "status": "RUNNING",
        "mode": "task", "created_at": dt.datetime.now(dt.timezone.utc),
        "updated_at": None, "parent_session_id": None, "parent_agent_id": None,
        "thinking_override": "medium",
    }
    assert nerve_gateway._session_row_dict(row)["thinkingLevel"] == "medium"


@needs_pg
async def test_no_thinking_override_builds_no_extra_body(monkeypatch):
    """The default path must stay byte-identical to before the feature: no
    extra_body at all, so the deployment's own kwargs (and the cache) apply."""
    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(settings, "llm_base_url", "http://localhost:4000")
    monkeypatch.setattr(settings, "llm_api_key", "sk-test-not-a-real-key")
    m = await models_mod.resolve_model(agent_id="inbox-triage")
    assert "extra_body" not in m.settings


async def test_label_only_patches_stay_a_no_op():
    """The spawn flow patches labels; nothing in Central Command stores them, and
    refusing would break session creation."""
    assert await nerve_gateway._dispatch(
        "sessions.patch", {"key": "agent:inbox-triage:main", "label": "renamed"}, _noop,
    ) == {}


# --- the single-profile /api/gateway/models translation ------------------------
# (2026-08-28: on the single-node profile uvicorn serves the cockpit, so this
# route must exist in FastAPI — the Node server that provides it on k3s is
# absent, and its absence rendered as "Gateway HTTP 404" in three UI spots.)


async def test_gateway_models_carries_the_shape_the_cockpit_hook_declares():
    payload = await routes.gateway_model_catalog()
    assert set(payload) == {"models", "error", "source"}
    assert payload["source"] == "litellm"
    assert payload["error"] is None
    by_id = {m["id"]: m for m in payload["models"]}
    # Every field web/src/hooks/useGatewayModelCatalog.ts reads, per model.
    for m in payload["models"]:
        assert set(m) == {"id", "label", "provider", "configured", "role", "thinkingLevels"}
        assert m["configured"] is True
    # The pinned catalog: cc-default is the resolved default -> primary.
    assert by_id["cc-default"]["role"] == "primary"
    assert by_id["cc-default"]["thinkingLevels"] == list(models_mod.THINKING_LEVELS)
    # A namespaced id splits into provider/label; a bare id falls back.
    assert by_id["openai/gpt-5.2"]["provider"] == "openai"
    assert by_id["openai/gpt-5.2"]["label"] == "gpt-5.2"
    assert by_id["cc-default"]["provider"] == "litellm"


async def test_gateway_models_reports_proxy_errors_not_an_empty_catalog(monkeypatch):
    async def boom() -> list[str]:
        raise litellm_mod.LiteLLMError("model catalog — http://x unreachable: nope")

    monkeypatch.setattr(litellm_mod, "exposed_model_ids", boom)
    payload = await routes.gateway_model_catalog()
    assert payload["models"] == []
    assert "unreachable" in payload["error"]
