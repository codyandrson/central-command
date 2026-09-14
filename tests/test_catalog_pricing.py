"""A price is catalog data, never an agent's claim (2026-09-13).

The litellm-manager registered nine Kilo.ai models with the card's
per-MILLION price written as the per-TOKEN price (mercury-2.5 at 2.0/7.5
instead of 2e-7/7.5e-7); the capability probe's twelve requests per model
then booked $80,589 of spend in LiteLLM. Four guards, one per layer:
the contract refuses a per-token price above a cent; the Executor drops the
proposal's cost fields and copies the credential's catalog prices; the
catalog's `pricing` is part of the drift fingerprint; the add brief says so.
"""

from __future__ import annotations

import pytest

from central_command.config import settings
from central_command.contract import Action, validate_action_args
from central_command.gateway import executor
from central_command.heartbeat import actions as hb_actions
from central_command.integrations import litellm as litellm_client

pytestmark = pytest.mark.anyio

MERCURY = {
    "id": "inception/mercury-2.5",
    "pricing": {"prompt": "0.000000200000", "completion": "0.000000750000",
                "input_cache_read": "0.000000020000"},
}


# --- contract: shape -----------------------------------------------------------


def test_pricing_from_catalog_maps_openrouter_shape_to_litellm_fields():
    assert litellm_client.pricing_from_catalog(MERCURY) == {
        "input_cost_per_token": 2e-7,
        "output_cost_per_token": 7.5e-7,
        "cache_read_input_token_cost": 2e-8,
    }
    # A free model's zero is a real price, not an absence.
    assert litellm_client.pricing_from_catalog({"pricing": {"prompt": "0", "completion": 0}}) == {
        "input_cost_per_token": 0.0, "output_cost_per_token": 0.0}
    # No pricing (OpenAI's / Anthropic's list shape) → nothing to declare.
    assert litellm_client.pricing_from_catalog({"id": "gpt-x"}) == {}
    assert litellm_client.pricing_from_catalog(None) == {}
    assert litellm_client.pricing_from_catalog({"pricing": {"prompt": "n/a"}}) == {}


def test_a_per_million_price_typed_as_per_token_is_refused_at_the_contract():
    bad = validate_action_args("litellm.add_model", {
        "model_name": "mercury-2.5", "model": "openai/inception/mercury-2.5",
        "model_info": {"input_cost_per_token": 2.0, "output_cost_per_token": 7.5},
    })
    assert len(bad) == 2 and all("per TOKEN" in p for p in bad)
    # The same ceiling on the litellm_params side, for add (`extra`) and update.
    assert validate_action_args("litellm.add_model", {
        "model_name": "m", "model": "openai/m", "extra": {"input_cost_per_token": "6.0"}})
    assert validate_action_args("litellm.update_model", {
        "model_id": "x", "litellm_params": {"output_cost_per_token": 1.8}})
    # A real per-token price passes; absence passes; a non-number is named.
    assert validate_action_args("litellm.add_model", {
        "model_name": "m", "model": "openai/m",
        "model_info": {"input_cost_per_token": 2e-7, "output_cost_per_token": 0}}) == []
    assert validate_action_args("litellm.add_model", {"model_name": "m", "model": "openai/m"}) == []
    assert validate_action_args("litellm.update_model", {
        "model_id": "x", "model_info": {"input_cost_per_token": "cheap"}}) == [
        "litellm.update_model: model_info.input_cost_per_token='cheap' is not a number"]


def test_the_litellm_model_handlers_have_a_shape_spec():
    # Every handler subscripts these (bite mark: a handler that subscripts
    # args[...] needs a spec), and the spec is what carries the price ceiling.
    assert validate_action_args("litellm.add_model", {"model": "openai/m"})
    assert validate_action_args("litellm.update_model", {})
    assert validate_action_args("litellm.delete_model", {})


# --- Executor: the price comes from the catalog ------------------------------------


def _add_action(model_info: dict | None = None, extra: dict | None = None) -> Action:
    args: dict = {"model_name": "mercury-2.5", "model": "openai/inception/mercury-2.5"}
    if model_info is not None:
        args["model_info"] = model_info
    if extra is not None:
        args["extra"] = extra
    return Action(capability="litellm.add_model", arguments=args,
                  target_ref={"system": "litellm", "id": "mercury-2.5", "read_version": "unknown"},
                  reversibility="reversible")


@pytest.fixture
def proxy(monkeypatch):
    """A fake proxy: records the add/update it receives, probes OK."""
    seen: dict = {"catalog_calls": []}

    async def fake_add(model_name, model, api_key=None, model_info=None, extra=None):
        seen["add"] = {"model_info": model_info, "extra": extra, "api_key": api_key}
        return {"ok": True, "model": {"model_name": model_name, "provider_model": model,
                                      "model_id": "id-merc"}}

    async def fake_update(model_id, model_info=None, litellm_params=None, model_name=None):
        seen["update"] = {"model_info": model_info, "litellm_params": litellm_params}
        return {"ok": True, "model": {"model_id": model_id, "updated": ["model_info"]}}

    async def fake_probe(model, measure_context=False):
        return {"ok": True, "observed": {}, "suggested_model_info": {}}

    async def fake_list_models():
        return {"models": [{"model_id": "id-merc", "model_name": "mercury-2.5",
                            "provider_model": "openai/inception/mercury-2.5",
                            "credential_name": "Kilo.ai"}]}

    async def fake_catalog_entry(credential_name, provider_model):
        seen["catalog_calls"].append((credential_name, provider_model))
        return MERCURY if credential_name == "Kilo.ai" else None

    monkeypatch.setattr(settings, "executor_mode", "live")
    monkeypatch.setattr(executor.litellm_client, "add_model", fake_add)
    monkeypatch.setattr(executor.litellm_client, "update_model", fake_update)
    monkeypatch.setattr(executor.litellm_client, "probe_model", fake_probe)
    monkeypatch.setattr(executor.litellm_client, "list_models", fake_list_models)
    monkeypatch.setattr(executor.litellm_client, "catalog_entry_for", fake_catalog_entry)
    return seen


async def test_add_prices_from_the_catalog_and_drops_what_the_agent_wrote(proxy):
    # Plausible-looking but wrong (the ceiling only catches unit errors).
    out = await executor.execute(
        [_add_action(model_info={"mode": "chat", "input_cost_per_token": 1.5e-6,
                                 "output_cost_per_token": 3e-6},
                     extra={"litellm_credential_name": "Kilo.ai",
                            "api_base": "https://api.kilo.ai/api/gateway",
                            "cache_read_input_token_cost": 9e-7})],
        approver="human:lee", source_refs=[])
    info = proxy["add"]["model_info"]
    assert info["input_cost_per_token"] == 2e-7
    assert info["output_cost_per_token"] == 7.5e-7
    assert info["cache_read_input_token_cost"] == 2e-8
    assert info["mode"] == "chat" and info["created_by"]
    assert "cache_read_input_token_cost" not in proxy["add"]["extra"]
    assert proxy["catalog_calls"] == [("Kilo.ai", "openai/inception/mercury-2.5")]
    result = out[0]["result"] if isinstance(out, list) else str(out)
    assert "priced from the 'Kilo.ai' catalog" in result
    assert "replacing the proposal's" in result


async def test_add_without_a_stored_credential_never_asks_a_catalog(proxy):
    await executor.execute(
        [_add_action(model_info={"input_cost_per_token": 3e-6})],
        approver="human:lee", source_refs=[])
    assert proxy["catalog_calls"] == []
    assert "input_cost_per_token" not in proxy["add"]["model_info"]


async def test_an_unreadable_catalog_still_adds_but_leaves_the_model_unpriced(proxy, monkeypatch):
    async def boom(credential_name, provider_model):
        raise litellm_client.LiteLLMError("provider answered 503")
    monkeypatch.setattr(executor.litellm_client, "catalog_entry_for", boom)
    out = await executor.execute(
        [_add_action(model_info={"input_cost_per_token": 3e-6},
                     extra={"litellm_credential_name": "Kilo.ai"})],
        approver="human:lee", source_refs=[])
    assert "input_cost_per_token" not in proxy["add"]["model_info"]
    result = out[0]["result"] if isinstance(out, list) else str(out)
    assert "unreadable" in result and "dropped the proposal's ['input_cost_per_token']" in result


async def test_update_that_touches_a_price_is_repriced_from_the_catalog(proxy):
    await executor.execute(
        [Action(capability="litellm.update_model",
                arguments={"model_id": "id-merc",
                           "model_info": {"input_cost_per_token": 4e-6, "supports_vision": False},
                           "litellm_params": {"rpm": 10, "output_cost_per_token": 1e-5}},
                target_ref={"system": "litellm", "id": "id-merc", "read_version": "unknown"},
                reversibility="reversible")],
        approver="human:lee", source_refs=[])
    upd = proxy["update"]
    assert upd["model_info"]["input_cost_per_token"] == 2e-7
    assert upd["model_info"]["output_cost_per_token"] == 7.5e-7
    assert upd["model_info"]["supports_vision"] is False
    assert upd["litellm_params"] == {"rpm": 10}
    assert proxy["catalog_calls"] == [("Kilo.ai", "openai/inception/mercury-2.5")]


async def test_update_that_leaves_prices_alone_makes_no_catalog_call(proxy):
    await executor.execute(
        [Action(capability="litellm.update_model",
                arguments={"model_id": "id-merc", "model_info": {"supports_vision": True}},
                target_ref={"system": "litellm", "id": "id-merc", "read_version": "unknown"},
                reversibility="reversible")],
        approver="human:lee", source_refs=[])
    assert proxy["catalog_calls"] == []
    assert proxy["update"]["model_info"] == {"supports_vision": True,
                                             "updated_by": "human:lee"}


# --- drift + brief --------------------------------------------------------------


def test_a_moved_price_changes_the_fingerprint():
    before = hb_actions.catalog_fingerprint(MERCURY)
    after = hb_actions.catalog_fingerprint(
        {**MERCURY, "pricing": {**MERCURY["pricing"], "completion": "0.000001000000"}})
    assert before != after
    assert hb_actions.catalog_fingerprint({"id": "m"}) == hb_actions.catalog_fingerprint(
        {"id": "m", "description": "changed"})


def test_the_add_brief_forbids_prices():
    brief = hb_actions._add_brief("Kilo.ai", [{"id": "inception/mercury-2.5"}])
    assert "NEVER set a price" in brief and "Executor copies per-token prices" in brief
