"""autodiscovery.skip (2026-09-13): the gated record of the operator's
never-add decision for one credential's catalog ids."""
import pytest

from central_command.contract import validate_action_args
from central_command.gateway import executor


def test_arg_spec_reports_every_shape_problem_at_once():
    problems = validate_action_args("autodiscovery.skip", {})
    assert len(problems) == 2 and "credential_name" in problems[0]
    assert "at least one of model_ids, vendors" in problems[1]
    assert validate_action_args("autodiscovery.skip",
                                {"credential_name": "c", "model_ids": ["a"]}) == []
    assert validate_action_args("autodiscovery.skip",
                                {"credential_name": "c", "vendors": ["openai"]}) == []
    assert validate_action_args("autodiscovery.skip",
                                {"credential_name": "c", "vendors": [], "model_ids": []}) != []


async def test_vendors_expand_to_the_credentials_unregistered_catalog_ids(monkeypatch):
    """2026-09-14: the operator answers a review by group ('skip all
    openai'); the Executor expands the group from the snapshot, so the agent
    never enumerates ids. Registered ids are not skips; an unknown vendor is
    refused rather than recorded as nothing."""
    from central_command.db import repo

    store = {
        "autodiscovery_snapshot": {"kilo": {
            "openai/gpt-a": {"disposition": "pending"},
            "openai/gpt-b": {"disposition": "skipped"},
            "openai/gpt-reg": {"disposition": "registered"},
            "anthropic/claude-x": {"disposition": "pending"},
            "bare": {"disposition": "pending"},
        }},
    }

    async def get(key, default):
        return store.get(key, default)

    async def put(key, value):
        store[key] = value

    monkeypatch.setattr(repo, "get_app_setting", get)
    monkeypatch.setattr(repo, "set_app_setting", put)

    out = await executor._autodiscovery_skip(
        {"credential_name": "kilo", "vendors": ["openai", "(no vendor)"],
         "model_ids": ["anthropic/claude-x"]}, "op", "litellm-manager")
    assert "4 catalog id(s)" in out and "2 vendor group(s) expanded" in out
    assert store["autodiscovery_decisions"]["skip_by_credential"]["kilo"] == [
        "anthropic/claude-x", "bare", "openai/gpt-a", "openai/gpt-b"]

    with pytest.raises(executor.ExecutorError, match="vendor mistral"):
        await executor._autodiscovery_skip(
            {"credential_name": "kilo", "vendors": ["mistral"]}, "op", None)
    with pytest.raises(executor.ExecutorError, match="nothing to skip"):
        await executor._autodiscovery_skip({"credential_name": "kilo"}, "op", None)


async def test_the_skip_merges_per_credential_and_leaves_the_flat_list_alone(monkeypatch):
    from central_command.db import repo

    store = {"autodiscovery_decisions": {"skip": ["legacy-id"],
                                         "skip_by_credential": {"kilo": ["k-1"]}}}

    async def get(key, default):
        return store.get(key, default)

    async def put(key, value):
        store[key] = value

    monkeypatch.setattr(repo, "get_app_setting", get)
    monkeypatch.setattr(repo, "set_app_setting", put)

    out = await executor._autodiscovery_skip(
        {"credential_name": "kilo", "model_ids": ["k-2", "k-1", " ", "k-3"]}, "op", "litellm-manager")
    assert "3 catalog id(s)" in out and "3 skipped" in out
    assert store["autodiscovery_decisions"] == {
        "skip": ["legacy-id"], "skip_by_credential": {"kilo": ["k-1", "k-2", "k-3"]}}

    await executor._autodiscovery_skip({"credential_name": "openai", "model_ids": ["k-1"]}, "op", None)
    assert store["autodiscovery_decisions"]["skip_by_credential"] == {
        "kilo": ["k-1", "k-2", "k-3"], "openai": ["k-1"]}  # same raw id, different decision

    with pytest.raises(executor.ExecutorError):
        await executor._autodiscovery_skip({"credential_name": "kilo", "model_ids": [""]}, "op", None)
