"""LiteLLM-manager tests (Phase 2 of the LiteLLM integration): the native admin
client validates model-controlled strings and fails loud when unconfigured, the
executor handlers map add/delete to it (and dry_run never touches the proxy), and
a direct task rides the SAME durable gate as every other agent, resuming under
the litellm-manager definition.

Registry↔executor parity and uniform-management coverage for the new agent are
enforced by tests/test_governance.py — nothing to duplicate here.
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from central_command.config import settings
from central_command.db import repo
from central_command.integrations import litellm as litellm_client
from central_command.integrations import litellm_credstore
from central_command.runtime import litellm_manager
from central_command.runtime.run import run_task
from central_command.runtime.spike_model import (
    make_litellm_create_key,
    make_litellm_fallbacks,
    make_litellm_model,
    make_litellm_update_model,
)
from tests.conftest import needs_pg


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "executor_mode", "dry_run")


# --- the native client -------------------------------------------------------


async def test_client_fails_loud_when_unconfigured(monkeypatch):
    monkeypatch.setattr(settings, "llm_proxy_admin_key", "")  # no admin key
    with pytest.raises(litellm_client.LiteLLMError):
        await litellm_client.list_models()
    with pytest.raises(litellm_client.LiteLLMError):
        await litellm_client.add_model("cc-triage", "anthropic/claude-haiku-4-5")


async def test_client_validates_model_controlled_strings(monkeypatch):
    # Configured, so validation (not the config guard) is what rejects — and it
    # rejects BEFORE any HTTP call, so no proxy is touched.
    monkeypatch.setattr(settings, "llm_proxy_admin_key", "sk-test")
    with pytest.raises(litellm_client.LiteLLMError):
        await litellm_client.add_model("bad alias!", "anthropic/claude-haiku-4-5")
    with pytest.raises(litellm_client.LiteLLMError):
        await litellm_client.add_model("cc-triage", "not-a-provider-model")
    with pytest.raises(litellm_client.LiteLLMError):
        await litellm_client.delete_model("")


# --- provider_catalog: autodiscovery's window onto the PROVIDER, not the proxy


class _FakeAsyncClient:
    """A minimal async-context-manager stand-in for httpx.AsyncClient — no
    network call, real httpx.Response objects so .json()/.status_code behave
    exactly like the real thing."""

    def __init__(self, pages):
        self._pages = pages
        self.calls: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None, headers=None):
        self.calls.append({"url": url, "params": params, "headers": headers})
        page = self._pages[len(self.calls) - 1]
        return httpx.Response(200, json=page)


async def test_provider_catalog_rejects_a_provider_with_no_fetcher_and_no_api_base():
    with pytest.raises(litellm_client.LiteLLMError, match="no catalog fetcher"):
        await litellm_client.provider_catalog("mystery-provider", "sk-x")


async def test_provider_catalog_paginates_and_uses_the_given_key(monkeypatch):
    fake = _FakeAsyncClient([
        {"data": [{"id": "claude-a"}], "has_more": True, "last_id": "claude-a"},
        {"data": [{"id": "claude-b"}], "has_more": False},
    ])
    monkeypatch.setattr(litellm_client.httpx, "AsyncClient", lambda *a, **kw: fake)

    out = await litellm_client.provider_catalog("anthropic", "sk-ant-test")

    assert [m["id"] for m in out] == ["claude-a", "claude-b"]
    assert len(fake.calls) == 2
    assert fake.calls[1]["params"]["after_id"] == "claude-a"
    assert fake.calls[0]["headers"]["x-api-key"] == "sk-ant-test"
    # NEVER the proxy — the whole point is a call to the provider directly.
    assert "anthropic.com" in fake.calls[0]["url"]


async def test_provider_catalog_honors_an_api_base_override(monkeypatch):
    fake = _FakeAsyncClient([{"data": [{"id": "claude-a"}], "has_more": False}])
    monkeypatch.setattr(litellm_client.httpx, "AsyncClient", lambda *a, **kw: fake)

    await litellm_client.provider_catalog("anthropic", "sk-ant-test", "https://proxy.example.com")

    assert fake.calls[0]["url"].startswith("https://proxy.example.com")


async def test_provider_catalog_falls_back_to_openai_compatible_with_an_api_base(monkeypatch):
    fake = _FakeAsyncClient([{"data": [{"id": "local-model"}]}])
    monkeypatch.setattr(litellm_client.httpx, "AsyncClient", lambda *a, **kw: fake)

    out = await litellm_client.provider_catalog("vllm", "sk-local", "http://localhost:8000")

    assert [m["id"] for m in out] == ["local-model"]
    assert fake.calls[0]["url"] == "http://localhost:8000/v1/models"
    assert fake.calls[0]["headers"]["Authorization"] == "Bearer sk-local"


async def test_provider_catalog_surfaces_a_provider_error(monkeypatch):
    fake = _FakeAsyncClient([{}])

    async def bad_get(self, url, params=None, headers=None):
        return httpx.Response(401, text="unauthorized")

    monkeypatch.setattr(litellm_client.httpx, "AsyncClient", lambda *a, **kw: fake)
    monkeypatch.setattr(_FakeAsyncClient, "get", bad_get)

    with pytest.raises(litellm_client.LiteLLMError, match="401"):
        await litellm_client.provider_catalog("anthropic", "sk-ant-test")


async def test_litellm_provider_catalog_tool_wraps_a_missing_credential(monkeypatch):
    from central_command.integrations import litellm_credstore
    from central_command.runtime import tools

    async def fake_creds():
        return []

    monkeypatch.setattr(litellm_credstore, "list_provider_credentials", fake_creds)
    out = await tools.litellm_provider_catalog(None, "anthropic")
    assert "unavailable" in out and "no stored credential" in out


async def test_litellm_provider_catalog_tool_never_leaks_the_credential_value(monkeypatch):
    from central_command.integrations import litellm_credstore
    from central_command.runtime import tools

    async def fake_creds():
        return [{"credential_name": "anthropic-main", "provider": "anthropic",
                 "values": {"api_key": "sk-ant-SECRET-VALUE"}}]

    async def fake_catalog(provider, api_key, api_base=None):
        assert api_key == "sk-ant-SECRET-VALUE"  # used, never returned
        return [{"id": f"{provider}-model"}]

    monkeypatch.setattr(litellm_credstore, "list_provider_credentials", fake_creds)
    monkeypatch.setattr(litellm_client, "provider_catalog", fake_catalog)
    out = await tools.litellm_provider_catalog(None, "anthropic")
    assert "anthropic-model" in out and "unavailable" not in out
    assert "sk-ant-SECRET-VALUE" not in out


async def test_update_model_validates_and_requires_a_change(monkeypatch):
    monkeypatch.setattr(settings, "llm_proxy_admin_key", "sk-test")
    # empty model_id, a change that re-points to a bad provider model, and a
    # no-op update (nothing to change) all reject before any HTTP call.
    with pytest.raises(litellm_client.LiteLLMError):
        await litellm_client.update_model("")
    with pytest.raises(litellm_client.LiteLLMError):
        await litellm_client.update_model("id-1", litellm_params={"model": "not-a-provider-model"})
    with pytest.raises(litellm_client.LiteLLMError):
        await litellm_client.update_model("id-1")  # no fields → nothing to do


async def test_update_model_patches_only_sent_fields(monkeypatch):
    """The in-place edit routes to PATCH /model/{id}/update with ONLY the fields
    given — the merge that preserves the stored api_key is what kills the key-drop
    class. We assert the exact request the client makes."""
    monkeypatch.setattr(settings, "llm_proxy_admin_key", "sk-test")
    monkeypatch.setattr(settings, "llm_proxy_base_url", "http://proxy.test")
    seen = {}

    class FakeResp:
        status_code = 200

        def json(self):
            return {"model_name": "claude-sonnet-5", "model_info": {"id": "abc-123"}}

    async def fake_call(method, path, json_body=None):
        seen.update(method=method, path=path, body=json_body)
        return FakeResp()

    monkeypatch.setattr(litellm_client, "_call", fake_call)
    out = await litellm_client.update_model(
        "abc-123", model_info={"created_at": "2026-07-21T00:00:00Z", "created_by": "x"}
    )
    assert seen["method"] == "PATCH"
    assert seen["path"] == "/model/abc-123/update"
    # No api_key, no litellm_params in the body — omitted fields are preserved by
    # the proxy, so the credential is never at risk.
    assert seen["body"] == {"model_info": {"created_at": "2026-07-21T00:00:00Z", "created_by": "x"}}
    assert out["ok"] and out["model"]["model_id"] == "abc-123"
    assert out["model"]["updated"] == ["model_info"]


# --- executor handlers -------------------------------------------------------


def test_executor_injects_provider_key_never_from_the_proposal(monkeypatch):
    """The 2026-07-22 incident: a re-registration with no key silently broke the
    Claude models. The Executor supplies the provider credential (keys never ride
    a proposal); non-anthropic providers get none from us. Pins its OWN key value:
    asserting against live settings made this pass only on an instance holding an
    Anthropic key (rehearsal finding F9, 2026-08-21 — failed on a clean install)."""
    from central_command.gateway import executor

    monkeypatch.setattr(settings, "anthropic_api_key", "sk-ant-test-not-real")
    assert executor._provider_key("anthropic/claude-sonnet-5") == "sk-ant-test-not-real"
    assert executor._provider_key("openai/gpt-4o") is None
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    # Empty means fail-loud downstream, never inject an empty credential.
    assert executor._provider_key("anthropic/claude-sonnet-5") is None


async def test_add_and_delete_handlers_call_the_client(monkeypatch):
    from central_command.contract import Action
    from central_command.gateway import executor

    monkeypatch.setattr(settings, "anthropic_api_key", "sk-ant-test")
    calls = []

    async def fake_add(model_name, model, api_key=None, model_info=None, extra=None):
        calls.append(("add", model_name, model, api_key, model_info))
        return {"ok": True, "model": {"model_name": model_name,
                                      "provider_model": model, "model_id": "id-123"}}

    async def fake_delete(model_id):
        calls.append(("delete", model_id))
        return {"ok": True}

    async def fake_health(model=None):
        calls.append(("health", model))
        return {"unhealthy": []}

    monkeypatch.setattr(settings, "executor_mode", "live")
    monkeypatch.setattr(executor.litellm_client, "add_model", fake_add)
    monkeypatch.setattr(executor.litellm_client, "delete_model", fake_delete)
    monkeypatch.setattr(executor.litellm_client, "check_model_health", fake_health)

    out = await executor.execute(
        [
            # No api_key in the proposal (charter-correct); the Executor injects
            # it. Metadata rides model_info, where LiteLLM actually stores it.
            Action(capability="litellm.add_model",
                   arguments={"model_name": "cc-triage",
                              "model": "anthropic/claude-haiku-4-5-20251001",
                              "model_info": {"created_by": "litellm-manager"}},
                   target_ref={"system": "litellm", "id": "cc-triage", "read_version": "unknown"},
                   reversibility="reversible"),
            Action(capability="litellm.delete_model",
                   arguments={"model_id": "old-999"},
                   target_ref={"system": "litellm", "id": "old-999", "read_version": "unknown"},
                   reversibility="reversible"),
        ],
        approver="human:lee", source_refs=["operator:task"],
    )
    assert calls == [
        # created_by is EXECUTOR-STAMPED (proposer, falling back to approver
        # when none — this call passes no proposer), overwriting the
        # agent-supplied claim in the action args.
        ("add", "cc-triage", "anthropic/claude-haiku-4-5-20251001",
         "sk-ant-test", {"created_by": "human:lee", "updated_by": "human:lee"}),
        # Test-on-add: one scoped health probe of the just-added alias.
        ("health", "cc-triage"),
        ("delete", "old-999"),
    ]
    assert "cc-triage" in out.result_text and "old-999" in out.result_text
    assert "tested on add: healthy" in out.result_text


async def test_executor_stamps_model_provenance_over_agent_claims(monkeypatch):
    """created_by/updated_by are provenance, and provenance is the gateway's to
    write — an agent-supplied value in the args is a claim and is overwritten
    (same rule that hands `proposer` to execute() instead of trusting args).
    The stamp MERGES into model_info (verified live 2026-08-21: the proxy's
    PATCH merges per-key), so capability fields ride through untouched."""
    from central_command.contract import Action
    from central_command.gateway import executor

    infos = []

    async def fake_add(model_name, model, api_key=None, model_info=None, extra=None):
        infos.append(model_info)
        return {"ok": True, "model": {"model_name": model_name,
                                      "provider_model": model, "model_id": "id-1"}}

    async def fake_update(model_id, model_info=None, litellm_params=None, model_name=None):
        infos.append(model_info)
        return {"ok": True, "model": {"model_id": model_id, "updated": ["model_info"]}}

    async def fake_health(model=None):
        return {"unhealthy": [{"model": model, "error": "boom"}]}

    monkeypatch.setattr(settings, "executor_mode", "live")
    monkeypatch.setattr(executor.litellm_client, "add_model", fake_add)
    monkeypatch.setattr(executor.litellm_client, "update_model", fake_update)
    monkeypatch.setattr(executor.litellm_client, "check_model_health", fake_health)

    out = await executor.execute(
        [
            Action(capability="litellm.add_model",
                   arguments={"model_name": "cc-x", "model": "anthropic/claude-x",
                              "extra": {"litellm_credential_name": "anthropic-main"},
                              "model_info": {"created_by": "not-the-drafter",
                                             "supports_vision": True}},
                   target_ref={"system": "litellm", "id": "cc-x", "read_version": "unknown"},
                   reversibility="reversible"),
            # An update carrying NO model_info still gets the stamp.
            Action(capability="litellm.update_model",
                   arguments={"model_id": "id-1"},
                   target_ref={"system": "litellm", "id": "id-1", "read_version": "unknown"},
                   reversibility="reversible"),
        ],
        approver="human:lee", source_refs=["operator:task"],
        proposer="litellm-manager",
    )
    add_info, update_info = infos
    assert add_info["created_by"] == "litellm-manager"
    assert add_info["updated_by"] == "litellm-manager"
    assert add_info["supports_vision"] is True  # merged, not replaced
    assert update_info == {"updated_by": "litellm-manager"}
    # An unhealthy probe REPORTS in the record — the add itself succeeded.
    assert "tested on add: UNHEALTHY: boom" in out.result_text


async def test_update_handler_preserves_key_on_metadata_edit(monkeypatch):
    """A pure metadata/param edit sends NO api_key — the proxy's merge preserves
    the stored one. This is the churn-free, key-safe path."""
    from central_command.contract import Action
    from central_command.gateway import executor

    monkeypatch.setattr(settings, "anthropic_api_key", "sk-ant-test")
    calls = []

    async def fake_update(model_id, model_info=None, litellm_params=None, model_name=None):
        calls.append((model_id, model_info, litellm_params, model_name))
        return {"ok": True, "model": {"model_id": model_id, "updated": ["model_info"]}}

    monkeypatch.setattr(settings, "executor_mode", "live")
    monkeypatch.setattr(executor.litellm_client, "update_model", fake_update)

    out = await executor.execute(
        [Action(capability="litellm.update_model",
                arguments={"model_id": "abc-123",
                           "model_info": {"created_at": "2026-07-21T00:00:00Z"}},
                target_ref={"system": "litellm", "id": "abc-123", "read_version": "abc-123"},
                reversibility="reversible")],
        approver="human:lee", source_refs=["operator:task"],
    )
    # model_id and model_info pass through (plus the executor's updated_by
    # stamp — approver, since no proposer rode this call); NO litellm_params,
    # so NO key injected.
    assert calls == [("abc-123",
                      {"created_at": "2026-07-21T00:00:00Z", "updated_by": "human:lee"},
                      None, None)]
    assert "abc-123" in out.result_text


async def test_update_handler_injects_key_when_repointing_to_anthropic(monkeypatch):
    """If the edit re-points litellm_params.model to anthropic/* with no key, the
    Executor injects the provider credential — an update can never leave an
    anthropic model unauthenticated (same defense as add_model)."""
    from central_command.contract import Action
    from central_command.gateway import executor

    monkeypatch.setattr(settings, "anthropic_api_key", "sk-ant-test")
    calls = []

    async def fake_update(model_id, model_info=None, litellm_params=None, model_name=None):
        calls.append((model_id, litellm_params))
        return {"ok": True, "model": {"model_id": model_id, "updated": ["litellm_params"]}}

    monkeypatch.setattr(settings, "executor_mode", "live")
    monkeypatch.setattr(executor.litellm_client, "update_model", fake_update)

    await executor.execute(
        [Action(capability="litellm.update_model",
                arguments={"model_id": "abc-123",
                           "litellm_params": {"model": "anthropic/claude-opus-4-8"}},
                target_ref={"system": "litellm", "id": "abc-123", "read_version": "abc-123"},
                reversibility="reversible")],
        approver="human:lee", source_refs=[],
    )
    assert calls == [("abc-123", {"model": "anthropic/claude-opus-4-8", "api_key": "sk-ant-test"})]


async def test_add_handler_does_not_inject_key_when_credential_named(monkeypatch):
    """Named credentials are the preferred auth path: if the proposal references a
    stored credential (extra.litellm_credential_name), the Executor injects NO raw
    key — the credential authenticates on its own. Verified live 2026-07-21."""
    from central_command.contract import Action
    from central_command.gateway import executor

    monkeypatch.setattr(settings, "anthropic_api_key", "sk-ant-test")
    calls = []

    async def fake_add(model_name, model, api_key=None, model_info=None, extra=None):
        calls.append(api_key)
        return {"ok": True, "model": {"model_name": model_name,
                                      "provider_model": model, "model_id": "id-1"}}

    monkeypatch.setattr(settings, "executor_mode", "live")
    monkeypatch.setattr(executor.litellm_client, "add_model", fake_add)

    await executor.execute(
        [Action(capability="litellm.add_model",
                arguments={"model_name": "cc-x", "model": "anthropic/claude-haiku-4-5-20251001",
                           "extra": {"litellm_credential_name": "anthropic-main"}},
                target_ref={"system": "litellm", "id": "cc-x", "read_version": "unknown"},
                reversibility="reversible")],
        approver="human:lee", source_refs=[],
    )
    assert calls == [None]  # no key injected — the named credential provides auth


async def test_update_handler_does_not_inject_key_when_credential_named(monkeypatch):
    from central_command.contract import Action
    from central_command.gateway import executor

    monkeypatch.setattr(settings, "anthropic_api_key", "sk-ant-test")
    seen = []

    async def fake_update(model_id, model_info=None, litellm_params=None, model_name=None):
        seen.append(litellm_params)
        return {"ok": True, "model": {"model_id": model_id, "updated": ["litellm_params"]}}

    monkeypatch.setattr(settings, "executor_mode", "live")
    monkeypatch.setattr(executor.litellm_client, "update_model", fake_update)

    await executor.execute(
        [Action(capability="litellm.update_model",
                arguments={"model_id": "abc-123",
                           "litellm_params": {"model": "anthropic/claude-opus-4-8",
                                              "litellm_credential_name": "anthropic-main"}},
                target_ref={"system": "litellm", "id": "abc-123", "read_version": "abc-123"},
                reversibility="reversible")],
        approver="human:lee", source_refs=[],
    )
    # params passed through unchanged — no api_key added
    assert seen == [{"model": "anthropic/claude-opus-4-8", "litellm_credential_name": "anthropic-main"}]


async def test_list_credentials_read(monkeypatch):
    monkeypatch.setattr(settings, "llm_proxy_admin_key", "sk-test")
    monkeypatch.setattr(settings, "llm_proxy_base_url", "http://proxy.test")

    class FakeResp:
        status_code = 200

        def json(self):
            return {"success": True, "credentials": [
                {"credential_name": "anthropic-main",
                 "credential_values": "<encrypted>",
                 "credential_info": {"custom_llm_provider": "anthropic"}}]}

    async def fake_call(method, path, json_body=None):
        assert method == "GET" and path == "/credentials"
        return FakeResp()

    monkeypatch.setattr(litellm_client, "_call", fake_call)
    out = await litellm_client.list_credentials()
    assert out["count"] == 1
    assert out["credentials"][0] == {"credential_name": "anthropic-main", "provider": "anthropic"}


# --- virtual keys (Phase 4 slice: no budgets) --------------------------------


async def test_create_key_validates_and_never_returns_plaintext(monkeypatch):
    monkeypatch.setattr(settings, "llm_proxy_admin_key", "sk-test")
    monkeypatch.setattr(settings, "llm_proxy_base_url", "http://proxy.test")
    with pytest.raises(litellm_client.LiteLLMError):
        await litellm_client.create_key("bad alias!")
    with pytest.raises(litellm_client.LiteLLMError):
        await litellm_client.create_key("cc-x", models=["ok", 5])  # non-string model

    SECRET = "sk-verySECRETplaintext012345"

    class FakeResp:
        status_code = 200

        def json(self):
            return {"key": SECRET, "token_id": "hash-abc", "key_alias": "cc-triage-key",
                    "models": ["claude-haiku-4-5"]}

    async def fake_call(method, path, json_body=None):
        assert method == "POST" and path == "/key/generate"
        return FakeResp()

    monkeypatch.setattr(litellm_client, "_call", fake_call)
    out = await litellm_client.create_key("cc-triage-key", models=["claude-haiku-4-5"])
    # The plaintext must NEVER appear anywhere in the returned structure.
    assert SECRET not in json.dumps(out)
    assert out["key"]["key_preview"] == "sk-ve…2345"
    assert out["key"]["token_id"] == "hash-abc"


async def test_delete_key_requires_an_identifier(monkeypatch):
    monkeypatch.setattr(settings, "llm_proxy_admin_key", "sk-test")
    with pytest.raises(litellm_client.LiteLLMError):
        await litellm_client.delete_key()  # neither alias nor key


async def test_create_key_handler_keeps_plaintext_out_of_result_text(monkeypatch):
    """The paramount invariant: a freshly generated key's plaintext must never
    reach result_text (which is logged AND fed to the resumed agent)."""
    from central_command.contract import Action
    from central_command.gateway import executor

    SECRET = "sk-DONOTLOGthissecret99999"

    async def fake_create(key_alias, models=None, metadata=None, tags=None, duration=None):
        # the client itself masks; simulate that contract (no plaintext returned)
        return {"ok": True, "key": {"key_alias": key_alias, "token_id": "hash-9",
                                    "models": models or [], "key_preview": "sk-DO…9999"}}

    monkeypatch.setattr(settings, "executor_mode", "live")
    monkeypatch.setattr(executor.litellm_client, "create_key", fake_create)

    out = await executor.execute(
        [Action(capability="litellm.create_key",
                arguments={"key_alias": "cc-triage-key", "models": ["claude-haiku-4-5"]},
                target_ref={"system": "litellm", "id": "cc-triage-key", "read_version": "unknown"},
                reversibility="reversible")],
        approver="human:lee", source_refs=[],
    )
    assert SECRET not in out.result_text
    assert "cc-triage-key" in out.result_text and "sk-DO…9999" in out.result_text


async def test_delete_key_handler_calls_client(monkeypatch):
    from central_command.contract import Action
    from central_command.gateway import executor

    calls = []

    async def fake_delete(key_alias=None, key=None):
        calls.append((key_alias, key))
        return {"ok": True, "deleted": key_alias}

    monkeypatch.setattr(settings, "executor_mode", "live")
    monkeypatch.setattr(executor.litellm_client, "delete_key", fake_delete)
    out = await executor.execute(
        [Action(capability="litellm.delete_key",
                arguments={"key_alias": "cc-triage-key"},
                target_ref={"system": "litellm", "id": "cc-triage-key", "read_version": "unknown"},
                reversibility="reversible")],
        approver="human:lee", source_refs=[],
    )
    assert calls == [("cc-triage-key", None)]
    assert "cc-triage-key" in out.result_text


async def test_list_keys_read(monkeypatch):
    monkeypatch.setattr(settings, "llm_proxy_admin_key", "sk-test")
    monkeypatch.setattr(settings, "llm_proxy_base_url", "http://proxy.test")

    class FakeResp:
        status_code = 200

        def json(self):
            return {"keys": [{"key_alias": "central_command", "token": "hash-x",
                              "models": [], "spend": 0.35, "created_at": "2026-07-22"}]}

    async def fake_call(method, path, json_body=None):
        assert method == "GET" and path.startswith("/key/list")
        return FakeResp()

    monkeypatch.setattr(litellm_client, "_call", fake_call)
    out = await litellm_client.list_keys()
    assert out["count"] == 1
    assert out["keys"][0]["key_alias"] == "central_command"
    assert out["keys"][0]["token_id"] == "hash-x"


# --- fallbacks (Phase 4: resilience/tiering) ---------------------------------


async def test_set_fallbacks_validates(monkeypatch):
    monkeypatch.setattr(settings, "llm_proxy_admin_key", "sk-test")
    with pytest.raises(litellm_client.LiteLLMError):
        await litellm_client.set_fallbacks("claude-sonnet-5", [])  # empty chain
    with pytest.raises(litellm_client.LiteLLMError):
        await litellm_client.set_fallbacks("claude-sonnet-5", ["ok", "bad alias!"])
    with pytest.raises(litellm_client.LiteLLMError):
        await litellm_client.set_fallbacks("claude-sonnet-5", ["claude-opus-4-8"], fallback_type="weird")


async def test_set_fallbacks_posts_the_chain(monkeypatch):
    monkeypatch.setattr(settings, "llm_proxy_admin_key", "sk-test")
    monkeypatch.setattr(settings, "llm_proxy_base_url", "http://proxy.test")
    seen = {}

    class FakeResp:
        status_code = 200

        def json(self):
            return {"model": "claude-sonnet-5", "fallback_models": ["claude-opus-4-8"],
                    "fallback_type": "general", "message": "ok"}

    async def fake_call(method, path, json_body=None):
        seen.update(method=method, path=path, body=json_body)
        return FakeResp()

    monkeypatch.setattr(litellm_client, "_call", fake_call)
    out = await litellm_client.set_fallbacks("claude-sonnet-5", ["claude-opus-4-8"])
    assert seen["method"] == "POST" and seen["path"] == "/fallback"
    assert seen["body"] == {"model": "claude-sonnet-5",
                            "fallback_models": ["claude-opus-4-8"], "fallback_type": "general"}
    assert out["model"] == "claude-sonnet-5"


async def test_delete_fallbacks_uses_query(monkeypatch):
    monkeypatch.setattr(settings, "llm_proxy_admin_key", "sk-test")
    monkeypatch.setattr(settings, "llm_proxy_base_url", "http://proxy.test")
    seen = {}

    class FakeResp:
        status_code = 200

        def json(self):
            return {}

    async def fake_call(method, path, json_body=None):
        seen.update(method=method, path=path)
        return FakeResp()

    monkeypatch.setattr(litellm_client, "_call", fake_call)
    await litellm_client.delete_fallbacks("claude-sonnet-5", fallback_type="context_window")
    assert seen["method"] == "DELETE"
    assert seen["path"] == "/fallback/claude-sonnet-5?fallback_type=context_window"


async def test_fallback_handlers_call_the_client(monkeypatch):
    from central_command.contract import Action
    from central_command.gateway import executor

    calls = []

    async def fake_set(model, fallback_models, fallback_type="general"):
        calls.append(("set", model, fallback_models, fallback_type))
        return {"model": model, "fallback_models": fallback_models, "fallback_type": fallback_type}

    async def fake_del(model, fallback_type="general"):
        calls.append(("del", model, fallback_type))
        return {"model": model, "fallback_type": fallback_type}

    monkeypatch.setattr(settings, "executor_mode", "live")
    monkeypatch.setattr(executor.litellm_client, "set_fallbacks", fake_set)
    monkeypatch.setattr(executor.litellm_client, "delete_fallbacks", fake_del)

    out = await executor.execute(
        [Action(capability="litellm.set_fallbacks",
                arguments={"model": "claude-sonnet-5", "fallback_models": ["claude-opus-4-8"]},
                target_ref={"system": "litellm", "id": "claude-sonnet-5", "read_version": "unknown"},
                reversibility="reversible"),
         Action(capability="litellm.delete_fallbacks",
                arguments={"model": "claude-haiku-4-5", "fallback_type": "content_policy"},
                target_ref={"system": "litellm", "id": "claude-haiku-4-5", "read_version": "unknown"},
                reversibility="reversible")],
        approver="human:lee", source_refs=[],
    )
    assert calls == [("set", "claude-sonnet-5", ["claude-opus-4-8"], "general"),
                     ("del", "claude-haiku-4-5", "content_policy")]
    assert "claude-sonnet-5" in out.result_text


async def test_get_routing_read(monkeypatch):
    monkeypatch.setattr(settings, "llm_proxy_admin_key", "sk-test")
    monkeypatch.setattr(settings, "llm_proxy_base_url", "http://proxy.test")

    class FakeResp:
        status_code = 200

        def json(self):
            return {"fields": [{"field_name": "routing_strategy", "field_value": "simple-shuffle"},
                               {"field_name": "fallbacks", "field_value": None}]}

    async def fake_call(method, path, json_body=None):
        assert path == "/router/settings"
        return FakeResp()

    monkeypatch.setattr(litellm_client, "_call", fake_call)
    out = await litellm_client.get_routing()
    assert out["routing_strategy"] == "simple-shuffle"
    assert out["fallbacks"] is None


# --- health & diagnostics reads ----------------------------------------------


async def test_get_health_read(monkeypatch):
    monkeypatch.setattr(settings, "llm_proxy_admin_key", "sk-test")
    monkeypatch.setattr(settings, "llm_proxy_base_url", "http://proxy.test")

    class FakeResp:
        status_code = 200

        def json(self):
            return {"status": "healthy", "db": "connected", "cache": "redis",
                    "litellm_version": "1.84.0", "log_level": "WARNING",
                    "success_callbacks": ["a", "b", "c"]}

    async def fake_call(method, path, json_body=None):
        assert path == "/health/readiness/details"
        return FakeResp()

    monkeypatch.setattr(litellm_client, "_call", fake_call)
    out = await litellm_client.get_health()
    assert out["status"] == "healthy" and out["litellm_version"] == "1.84.0"
    assert out["success_callbacks_count"] == 3


async def test_check_model_health_targets_one_model_and_reports_failures(monkeypatch):
    monkeypatch.setattr(settings, "llm_proxy_admin_key", "sk-test")
    monkeypatch.setattr(settings, "llm_proxy_base_url", "http://proxy.test")
    seen = {}

    class FakeResp:
        status_code = 200

        def json(self):
            return {"healthy_endpoints": [{"model": "claude-opus-4-8"}],
                    "unhealthy_endpoints": [{"model": "claude-sonnet-5",
                                             "error": "x-api-key required"}]}

    async def fake_call(method, path, json_body=None):
        seen["path"] = path
        return FakeResp()

    monkeypatch.setattr(litellm_client, "_call", fake_call)
    out = await litellm_client.check_model_health(model="claude-sonnet-5")
    assert seen["path"] == "/health?model=claude-sonnet-5"  # scoped to one alias
    assert out["healthy"] == ["claude-opus-4-8"]
    assert out["unhealthy"] == [{"model": "claude-sonnet-5", "error": "x-api-key required"}]


# --- key update + teams (Phase 4/5: no budgets) ------------------------------


async def test_update_key_validates_and_requires_a_change(monkeypatch):
    monkeypatch.setattr(settings, "llm_proxy_admin_key", "sk-test")
    with pytest.raises(litellm_client.LiteLLMError):
        await litellm_client.update_key("")  # no key
    with pytest.raises(litellm_client.LiteLLMError):
        await litellm_client.update_key("hash-1")  # nothing to change


async def test_update_key_patches_by_token(monkeypatch):
    monkeypatch.setattr(settings, "llm_proxy_admin_key", "sk-test")
    monkeypatch.setattr(settings, "llm_proxy_base_url", "http://proxy.test")
    seen = {}

    class FakeResp:
        status_code = 200

        def json(self):
            return {}

    async def fake_call(method, path, json_body=None):
        seen.update(method=method, path=path, body=json_body)
        return FakeResp()

    monkeypatch.setattr(litellm_client, "_call", fake_call)
    out = await litellm_client.update_key("hash-1", models=["claude-haiku-4-5"])
    assert seen["method"] == "POST" and seen["path"] == "/key/update"
    assert seen["body"] == {"key": "hash-1", "models": ["claude-haiku-4-5"]}
    assert out["updated"] == ["models"]


async def test_create_team_and_delete_team(monkeypatch):
    monkeypatch.setattr(settings, "llm_proxy_admin_key", "sk-test")
    monkeypatch.setattr(settings, "llm_proxy_base_url", "http://proxy.test")
    with pytest.raises(litellm_client.LiteLLMError):
        await litellm_client.create_team("bad alias!")
    with pytest.raises(litellm_client.LiteLLMError):
        await litellm_client.delete_team("")

    seen = []

    class FakeResp:
        def __init__(self, payload):
            self.status_code = 200
            self._p = payload

        def json(self):
            return self._p

    async def fake_call(method, path, json_body=None):
        seen.append((method, path, json_body))
        return FakeResp({"team_id": "team-9", "team_alias": "cc-team"})

    monkeypatch.setattr(litellm_client, "_call", fake_call)
    c = await litellm_client.create_team("cc-team", models=["claude-haiku-4-5"])
    assert c["team"]["team_id"] == "team-9"
    d = await litellm_client.delete_team("team-9")
    assert d["team_id"] == "team-9"
    assert seen[0][:2] == ("POST", "/team/new")
    assert seen[1] == ("POST", "/team/delete", {"team_ids": ["team-9"]})


async def test_key_and_team_handlers_dispatch(monkeypatch):
    """Registry↔executor parity plus the new handlers route correctly."""
    from central_command.contract import Action
    from central_command.gateway import executor
    from central_command.gateway.capabilities import gated_write_names

    assert gated_write_names() == set(executor.HANDLERS)  # parity holds with the new writes

    calls = []
    monkeypatch.setattr(settings, "executor_mode", "live")

    async def fake_update_key(key, **kw):
        calls.append(("update_key", key)); return {"updated": ["models"]}

    async def fake_create_team(team_alias, models=None):
        calls.append(("create_team", team_alias)); return {"team": {"team_alias": team_alias, "team_id": "t1"}}

    monkeypatch.setattr(executor.litellm_client, "update_key", fake_update_key)
    monkeypatch.setattr(executor.litellm_client, "create_team", fake_create_team)
    await executor.execute(
        [Action(capability="litellm.update_key", arguments={"key": "hash-1", "models": ["x"]},
                target_ref={"system": "litellm", "id": "hash-1", "read_version": "unknown"},
                reversibility="reversible"),
         Action(capability="litellm.create_team", arguments={"team_alias": "cc-team"},
                target_ref={"system": "litellm", "id": "cc-team", "read_version": "unknown"},
                reversibility="reversible")],
        approver="human:lee", source_refs=[],
    )
    assert calls == [("update_key", "hash-1"), ("create_team", "cc-team")]


async def test_dry_run_never_touches_the_proxy(monkeypatch):
    from central_command.contract import Action
    from central_command.gateway import executor

    async def explode(*a, **k):
        raise AssertionError("dry_run must not call the proxy")

    monkeypatch.setattr(settings, "executor_mode", "dry_run")
    monkeypatch.setattr(executor.litellm_client, "add_model", explode)

    out = await executor.execute(
        [Action(capability="litellm.add_model",
                arguments={"model_name": "cc-x", "model": "openai/gpt-4o"},
                target_ref={"system": "litellm", "id": "cc-x", "read_version": "unknown"},
                reversibility="reversible")],
        approver="human:lee", source_refs=[],
    )
    assert "[dry-run]" in out.result_text


# --- the agent ---------------------------------------------------------------


def _tool_names(agent) -> set[str]:
    # Tools live across the agent's toolsets since D25 (packs ride `toolsets=`).
    names: set[str] = set()
    for ts in agent.toolsets:
        names |= set(getattr(ts, "tools", {}) or {})
    return names


async def test_advisory_mode_has_no_propose_tool():
    agent = await litellm_manager.build_litellm_manager(
        model=make_litellm_model(), advisory=True)
    names = _tool_names(agent)
    assert names, "could not introspect agent tools"
    assert "propose_litellm_change" not in names
    assert "litellm_list_models" in names


async def test_litellm_manager_is_taskable():
    from central_command.runtime.roster import taskable_agent_ids

    assert "litellm-manager" in await taskable_agent_ids()


@needs_pg
async def test_direct_task_parks_a_proposal_behind_the_gate():
    from central_command.gateway import gateway

    model = make_litellm_model()
    out = await run_task(
        "litellm-manager", "Add a cc-triage alias on Haiku.", model=model
    )
    assert out["deferred"] is True

    row = await repo.load_proposal(out["proposal_id"])
    assert row["agent_id"] == "litellm-manager"
    assert row["status"] == "AWAITING_HUMAN"
    assert row["actions"][0]["capability"] == "litellm.add_model"

    # Approval resumes under the litellm-manager definition (not inbox-triage)
    # and completes the session — the multi-agent resume path for the new agent.
    # executor_mode is dry_run (pinned), so no real proxy call happens.
    result = await gateway.approve_and_execute(
        out["session_id"], out["proposal_id"], model=model
    )
    assert result["ok"] is True
    assert await repo.session_status(out["session_id"]) == "DONE"


@needs_pg
async def test_create_key_task_parks_and_resumes_behind_the_gate():
    """A create_key task rides the same durable gate and resumes under the
    litellm-manager definition. dry_run pinned — no real key is issued."""
    from central_command.gateway import gateway

    model = make_litellm_create_key()
    out = await run_task(
        "litellm-manager", "Issue a scoped virtual key for triage attribution.", model=model
    )
    assert out["deferred"] is True
    row = await repo.load_proposal(out["proposal_id"])
    assert row["actions"][0]["capability"] == "litellm.create_key"
    assert row["actions"][0]["arguments"]["key_alias"] == "cc-triage-key"

    result = await gateway.approve_and_execute(
        out["session_id"], out["proposal_id"], model=model
    )
    assert result["ok"] is True
    assert await repo.session_status(out["session_id"]) == "DONE"


@needs_pg
async def test_fallbacks_task_parks_and_resumes_behind_the_gate():
    from central_command.gateway import gateway

    model = make_litellm_fallbacks()
    out = await run_task(
        "litellm-manager", "Add a fallback from claude-sonnet-5 to claude-opus-4-8.", model=model
    )
    assert out["deferred"] is True
    row = await repo.load_proposal(out["proposal_id"])
    assert row["actions"][0]["capability"] == "litellm.set_fallbacks"
    result = await gateway.approve_and_execute(
        out["session_id"], out["proposal_id"], model=model
    )
    assert result["ok"] is True
    assert await repo.session_status(out["session_id"]) == "DONE"


@needs_pg
async def test_update_model_task_parks_and_resumes_behind_the_gate():
    """The in-place update rides the same durable gate: task → propose
    (litellm.update_model) → approve → resume under the litellm-manager
    definition. dry_run is pinned, so no real proxy call happens."""
    from central_command.gateway import gateway

    model = make_litellm_update_model()
    out = await run_task(
        "litellm-manager",
        "Fix the Claude model metadata using native created_at/created_by.",
        model=model,
    )
    assert out["deferred"] is True

    row = await repo.load_proposal(out["proposal_id"])
    assert row["agent_id"] == "litellm-manager"
    assert row["status"] == "AWAITING_HUMAN"
    assert row["actions"][0]["capability"] == "litellm.update_model"
    assert "model_id" in row["actions"][0]["arguments"]

    result = await gateway.approve_and_execute(
        out["session_id"], out["proposal_id"], model=model
    )
    assert result["ok"] is True
    assert await repo.session_status(out["session_id"]) == "DONE"


# --- litellm_credstore: LiteLLM's OWN credential store, decrypted (2026-08-20) -
# The autodiscovery redesign's enrollment surface. No test touches the real
# LiteLLM Postgres or the real .env — asyncpg.connect is stubbed with a fake
# connection, and the crypto is exercised for real (nacl SecretBox + a genuine
# cryptography AES-GCM round trip) against a TEST salt key.


class _FakeCredConn:
    def __init__(self, rows):
        self._rows = rows

    async def fetch(self, query):
        return self._rows

    async def close(self):
        pass


def _nacl_encrypt(plaintext: str, salt_key: str) -> str:
    import hashlib

    import nacl.secret
    import nacl.utils

    key = hashlib.sha256(salt_key.encode()).digest()
    box = nacl.secret.SecretBox(key)
    nonce = nacl.utils.random(nacl.secret.SecretBox.NONCE_SIZE)
    return base64.urlsafe_b64encode(bytes(box.encrypt(plaintext.encode(), nonce))).decode()


def test_litellm_decrypt_nacl_round_trip():
    salt_key = "test-salt-key"
    stored = _nacl_encrypt("sk-ant-real", salt_key)
    assert litellm_credstore.litellm_decrypt(stored, salt_key) == "sk-ant-real"


def test_litellm_decrypt_v2_gcm_round_trip():
    import hashlib
    import os as _os

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    salt_key = "test-salt-key"
    key = hashlib.sha256(salt_key.encode()).digest()
    nonce = _os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, b"sk-secret-value", None)
    stored = "v2:gcm:" + base64.urlsafe_b64encode(nonce + ct).decode()
    assert litellm_credstore.litellm_decrypt(stored, salt_key) == "sk-secret-value"


async def test_list_provider_credentials_requires_db_url(monkeypatch):
    monkeypatch.setattr(settings, "litellm_db_url", "")
    monkeypatch.setattr(settings, "litellm_salt_key", "test-salt-key")
    with pytest.raises(litellm_credstore.CredStoreError, match="CC_LITELLM_DB_URL"):
        await litellm_credstore.list_provider_credentials()


async def test_list_provider_credentials_requires_salt_key(monkeypatch):
    monkeypatch.setattr(settings, "litellm_db_url", "postgresql://stub/db")
    monkeypatch.setattr(settings, "litellm_salt_key", "")
    with pytest.raises(litellm_credstore.CredStoreError, match="CC_LITELLM_SALT_KEY"):
        await litellm_credstore.list_provider_credentials()


async def test_list_provider_credentials_decrypts_and_shapes_the_row(monkeypatch):
    salt_key = "test-salt-key"
    monkeypatch.setattr(settings, "litellm_db_url", "postgresql://stub/db")
    monkeypatch.setattr(settings, "litellm_salt_key", salt_key)
    row = {
        "credential_name": "anthropic-main",
        "credential_values": {"api_key": _nacl_encrypt("sk-ant-real", salt_key)},
        "credential_info": {"custom_llm_provider": "anthropic"},
    }

    async def fake_connect(dsn):
        return _FakeCredConn([row])

    monkeypatch.setattr(litellm_credstore.asyncpg, "connect", fake_connect)
    out = await litellm_credstore.list_provider_credentials()
    assert out == [{
        "credential_name": "anthropic-main", "provider": "anthropic",
        "values": {"api_key": "sk-ant-real"},
    }]


async def test_list_provider_credentials_treats_one_failing_field_as_plaintext(monkeypatch):
    salt_key = "test-salt-key"
    monkeypatch.setattr(settings, "litellm_db_url", "postgresql://stub/db")
    monkeypatch.setattr(settings, "litellm_salt_key", salt_key)
    row = {
        "credential_name": "mixed",
        "credential_values": {
            "api_key": "not-actually-encrypted!!",  # fails to decode/decrypt
            "api_base": _nacl_encrypt("https://api.example.com", salt_key),
        },
        "credential_info": {},
    }

    async def fake_connect(dsn):
        return _FakeCredConn([row])

    monkeypatch.setattr(litellm_credstore.asyncpg, "connect", fake_connect)
    out = await litellm_credstore.list_provider_credentials()
    assert out[0]["values"]["api_key"] == "not-actually-encrypted!!"
    assert out[0]["values"]["api_base"] == "https://api.example.com"


async def test_list_provider_credentials_raises_when_every_field_fails(monkeypatch):
    monkeypatch.setattr(settings, "litellm_db_url", "postgresql://stub/db")
    monkeypatch.setattr(settings, "litellm_salt_key", "test-salt-key")
    row = {
        "credential_name": "wrong-key",
        "credential_values": {"api_key": "not-valid-base64!!", "api_base": "also-not-valid!!"},
        "credential_info": {},
    }

    async def fake_connect(dsn):
        return _FakeCredConn([row])

    monkeypatch.setattr(litellm_credstore.asyncpg, "connect", fake_connect)
    with pytest.raises(litellm_credstore.CredStoreError, match="wrong-key"):
        await litellm_credstore.list_provider_credentials()


async def test_provider_catalog_matches_provider_case_insensitively(monkeypatch):
    # The provider name is operator-typed in the LiteLLM UI ("OpenAI") while
    # the fetcher registry is lowercase — a case mismatch made a whole
    # credential undiscoverable (2026-08-21).
    seen = {}

    async def fake_openai(api_key, api_base=None):
        seen["called"] = True
        return [{"id": "gpt-x"}]

    monkeypatch.setitem(litellm_client.PROVIDER_CATALOGS, "openai", fake_openai)
    out = await litellm_client.provider_catalog("OpenAI", "sk-test")
    assert seen.get("called") and out == [{"id": "gpt-x"}]
