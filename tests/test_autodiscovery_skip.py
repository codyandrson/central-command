"""autodiscovery.skip (2026-09-13): the gated record of the operator's
never-add decision for one credential's catalog ids."""
import pytest

from central_command.contract import validate_action_args
from central_command.gateway import executor


def test_arg_spec_reports_every_shape_problem_at_once():
    problems = validate_action_args("autodiscovery.skip", {})
    assert len(problems) == 1 and "credential_name, model_ids" in problems[0]
    assert validate_action_args("autodiscovery.skip",
                                {"credential_name": "c", "model_ids": ["a"]}) == []


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
