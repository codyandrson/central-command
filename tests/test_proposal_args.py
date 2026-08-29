"""Argument-shape validation runs BEFORE the Inbox and again at the Executor,
from one list (`contract.ARG_SPECS`).

Found live 2026-08-29: one `graph.add_episode` intent took the operator FOUR
approvals — `summary` instead of `episode_body`, no `name`, no `scope` —
because the Executor was the first thing to look at the arguments, and it
found one problem per round (two of them as bare KeyErrors: `'name'`).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic_ai import ModelRetry

from central_command import events
from central_command.config import settings
from central_command.contract import ARG_SPECS, Action, Proposal, Reversibility, validate_action_args
from central_command.db import repo
from central_command.gateway import executor
from central_command.gateway.capabilities import gated_write_names
from central_command.integrations import graphiti
from central_command.runtime import packs
from central_command.runtime import tools as tools_mod
from central_command.runtime.deps import TriageDeps
from tests.conftest import needs_pg

# The live 2026-08-29 first draft, verbatim in shape.
_BAD_ARGS = {"summary": "EA scope — operator contact doctrine …"}


def _proposal(arguments: dict, capability: str = "graph.add_episode") -> Proposal:
    return Proposal(
        intent="record the contact doctrine", expected_effect="graph episode",
        evidence=[],
        actions=[Action(capability=capability, arguments=arguments,
                        target_ref={"system": "graphiti", "id": "central_command"},
                        reversibility=Reversibility.reversible)],
    )


def test_every_spec_names_a_real_gated_write():
    """A spec for a capability the Executor does not dispatch is a check on
    nothing — and the seven curation handlers READ their required keys from
    the spec, so a missing one fails at import."""
    assert set(ARG_SPECS) <= gated_write_names()


def test_all_problems_are_reported_at_once():
    """One message per problem, all missing keys in one line — the one-field-
    per-round grind is what turned one bad draft into three failed approvals."""
    problems = validate_action_args("graph.add_episode", _BAD_ARGS)
    assert len(problems) == 1
    assert "name, episode_body, scope" in problems[0]
    assert "present: summary" in problems[0]
    assert validate_action_args("graph.add_episode@v1", {"scope": "team", "name": "n", "episode_body": "b"}) == [
        "graph.add_episode@v1: scope='team' is not one of shared, private"
    ]
    assert validate_action_args("jira.add_comment", {}) == []  # no spec = no guess


@needs_pg
async def test_bad_draft_is_handed_back_in_run_and_recorded():
    """The runtime half: ModelRetry (the model redrafts in the same run, the
    Inbox never sees it) AND a `proposal.rejected_in_run` event — the
    operator is spared the re-approval, not the knowledge."""
    since = await repo.latest_event_id()
    ctx = SimpleNamespace(deps=TriageDeps(agent_id="ea", session_id="sess_test_args"))
    with pytest.raises(ModelRetry) as excinfo:
        await tools_mod._validate_proposal(ctx, _proposal(_BAD_ARGS))
    assert "name, episode_body, scope" in str(excinfo.value)
    rows = await repo.list_events(since_id=since, kind="proposal.rejected_in_run")
    assert len(rows) == 1
    assert rows[0]["ref_id"] == "sess_test_args"
    assert rows[0]["payload"]["agent_id"] == "ea"
    assert rows[0]["payload"]["capabilities"] == ["graph.add_episode"]
    assert "name, episode_body, scope" in rows[0]["payload"]["problems"][0]


async def test_executor_refuses_the_same_shape_before_any_action_runs(monkeypatch):
    """The Executor half, from the same list: the trust boundary never assumes
    the runtime checked (an API-submitted proposal skips the tool path). Every
    action is checked before the FIRST runs, so the failure carries an empty
    completed-cursor and no handler is ever called — in either executor mode."""
    async def explode(*a, **k):
        raise AssertionError("handler ran on a malformed action")

    monkeypatch.setattr(graphiti, "add_episode", explode)
    good = Action(capability="graph.add_episode",
                  arguments={"name": "n", "episode_body": "b", "scope": "shared"},
                  target_ref={"system": "graphiti", "id": "central_command"},
                  reversibility=Reversibility.reversible)
    bad = _proposal(_BAD_ARGS).actions[0]
    for mode in ("dry_run", "live"):
        monkeypatch.setattr(settings, "executor_mode", mode)
        with pytest.raises(executor.ExecutionFailed) as excinfo:
            await executor.execute([good, bad], approver="human:lee", source_refs=[])
        assert excinfo.value.completed == []
        assert excinfo.value.failed_capability == "graph.add_episode"
        assert "name, episode_body, scope" in excinfo.value.error
        assert excinfo.value.error != "'name'"  # the bare KeyError is gone


def test_every_propose_tool_has_a_redraft_budget():
    """pydantic-ai's default is ONE retry; the second bad draft failed whole
    sessions live (2026-08-18). Every propose tool hands drafts back now, so
    every one carries the budget — not just bulk-dismiss."""
    toolset = packs.toolset_for(list(packs.PACKS))
    propose = {name: t for name, t in toolset.tools.items() if name.startswith("propose_")}
    assert propose, "no propose tools in the granted toolset?"
    assert {t.max_retries for t in propose.values()} == {10}
