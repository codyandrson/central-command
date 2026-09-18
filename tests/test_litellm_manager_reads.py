"""The two agent-facing seams behind the 2026-09-18 update_model 404s.

Seven litellm-manager sessions proposed an update against a model_id that
appears nowhere in their own transcript: the unfiltered `litellm_list_models`
did not fit the tool ceiling at 318 models, the agent could not find its row
in the truncated text, and it invented one. And kilo-auto/free hands the
`proposal` argument over as JSON text, which pydantic rejected ten times in a
row before failing the task."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError
from pydantic_ai import Tool

from central_command.integrations import litellm as litellm_client
from central_command.runtime import tools

MODELS = {"ok": True, "kind": "litellm", "operation": "list_models", "count": 3, "models": [
    {"model_name": "deepseek-v4-pro", "model_id": "1128037f-39a1", "provider_model": "openai/deepseek/deepseek-v4-pro"},
    {"model_name": "gpt-5.2-pro", "model_id": "526a5348-55a9", "provider_model": "openai/openai/gpt-5.2-pro"},
    {"model_name": "cc-default", "model_id": "aaaa", "provider_model": "openai/qwen"},
]}


@pytest.fixture
def fake_models(monkeypatch):
    async def list_models():
        return MODELS
    monkeypatch.setattr(litellm_client, "list_models", list_models)


async def test_list_models_filters_by_alias_id_or_provider_model(fake_models):
    by_alias = json.loads(await tools.litellm_list_models(None, name="deepseek"))
    assert [m["model_name"] for m in by_alias["models"]] == ["deepseek-v4-pro"]
    assert by_alias["count"] == 1 and by_alias["filter"] == "deepseek"

    by_id = json.loads(await tools.litellm_list_models(None, name="526A5348"))
    assert [m["model_name"] for m in by_id["models"]] == ["gpt-5.2-pro"]

    none = json.loads(await tools.litellm_list_models(None, name="nope"))
    assert none["models"] == [] and none["count"] == 0

    everything = json.loads(await tools.litellm_list_models(None))
    assert everything["count"] == 3 and "filter" not in everything


def _validator(tool_fn):
    return Tool(tool_fn).function_schema.validator


def test_a_stringified_proposal_is_parsed_like_an_object():
    proposal = {"actions": [{"capability": "litellm.update_model",
                             "arguments": {"model_id": "1128037f", "model_info": {"mode": "chat"}},
                             "target_ref": {"system": "litellm", "id": "1128037f", "read_version": "unknown"},
                             "reversibility": "reversible"}],
                "evidence": [], "confidence": {"level": "high", "rationale": "read it"},
                "expected_effect": "mode declared", "intent": "declare the mode"}
    for fn in (tools.propose_litellm_change, tools.propose_action, tools.propose_jira_update,
               tools.propose_calendar_change, tools.propose_loe):
        as_object = _validator(fn).validate_python({"proposal": proposal})
        as_text = _validator(fn).validate_python({"proposal": json.dumps(proposal)})
        assert as_text["proposal"] == as_object["proposal"], fn.__name__
    # The wire schema is untouched: the tool still declares an object.
    assert Tool(tools.propose_litellm_change).function_schema.json_schema["properties"]["proposal"].get("$ref")


def test_text_that_is_not_json_is_still_a_validation_error():
    with pytest.raises(ValidationError):
        _validator(tools.propose_litellm_change).validate_python({"proposal": "not json at all"})
