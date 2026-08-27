"""Per-agent scoped knowledge-graph writes and widened reads (unit only — no
Postgres, no network; `graphiti._call_tool` is monkeypatched throughout)."""

import pytest

from central_command.db import repo
from central_command.gateway.executor import ExecutorError, _graph_add_episode
from central_command.integrations import graphiti
from tests.conftest import needs_pg


@needs_pg
async def test_schema_seeds_the_founding_experts_steward_groups():
    """A fresh database (conftest re-executes schema.sql into central_command_test
    every run) must seed the two founding experts' domain groups — that's
    what makes domain stewardship work on a brand-new install, not just a
    hand-migrated one."""
    jira = await repo.get_agent("jira-expert")
    confluence = await repo.get_agent("confluence-expert")
    assert jira["steward_group"] == "domain_jira"
    assert confluence["steward_group"] == "domain_confluence"


@pytest.fixture
def verification_rows(monkeypatch):
    """Keep the handler DB-free: capture the verification row the executor
    writes before the graph write (2026-08-19 spec) instead of inserting it."""
    rows = []

    async def fake_create(**kwargs):
        rows.append(kwargs)
        return {"id": "cc-test", **kwargs}

    monkeypatch.setattr(repo, "create_graph_verification", fake_create)
    return rows


@pytest.fixture
def captured(monkeypatch):
    calls = []

    async def fake_call_tool(name, arguments, timeout=30.0):
        calls.append((name, arguments))
        return {"message": "ok", "facts": [], "nodes": [], "episodes": []}

    monkeypatch.setattr(graphiti, "_call_tool", fake_call_tool)
    return calls


@pytest.fixture
def no_stewards(monkeypatch):
    """Default roster fixture for tests that don't care about stewardship:
    no active agent stewards a domain, so read-group widening and write
    defaulting are no-ops and old (pre-stewardship) assertions still hold."""
    async def fake_list_agents(include_retired=True, roster_only=True):
        return []

    async def fake_get_agent(agent_id):
        return {"id": agent_id, "steward_group": ""}

    monkeypatch.setattr(repo, "list_agents", fake_list_agents)
    monkeypatch.setattr(repo, "get_agent", fake_get_agent)


async def test_add_episode_defaults_to_the_shared_group(captured):
    await graphiti.add_episode("n", "body", "src")
    assert captured[0][1]["group_id"] == graphiti.settings.graph_write_group


async def test_add_episode_honors_an_explicit_group_id(captured):
    await graphiti.add_episode("n", "body", "src", group_id="central_command_jira-expert")
    assert captured[0][1]["group_id"] == "central_command_jira-expert"


async def test_reads_without_agent_id_use_only_the_shared_groups(captured, no_stewards):
    await graphiti.search_facts("q")
    await graphiti.search_nodes("q")
    await graphiti.get_episodes()
    expected = await graphiti._read_groups()
    for _, args in captured:
        assert args["group_ids"] == expected


async def test_reads_with_agent_id_add_the_private_partition(captured, no_stewards):
    await graphiti.search_facts("q", agent_id="jira-expert")
    await graphiti.search_nodes("q", agent_id="jira-expert")
    await graphiti.get_episodes(agent_id="jira-expert")
    expected = await graphiti._read_groups() + ["central_command_jira-expert"]
    for _, args in captured:
        assert args["group_ids"] == expected


async def test_read_groups_widen_with_active_stewards(monkeypatch):
    """A steward's domain group joins the shared read set automatically —
    every agent reads it, even though only the steward writes there by
    default (2026-08-22 domain stewardship)."""
    async def fake_list_agents(include_retired=True, roster_only=True):
        assert include_retired is False  # retired stewards must not widen reads
        return [
            {"id": "jira-expert", "steward_group": "domain_jira"},
            {"id": "confluence-expert", "steward_group": "domain_confluence"},
            {"id": "inbox-triage", "steward_group": ""},
        ]

    monkeypatch.setattr(repo, "list_agents", fake_list_agents)
    groups = await graphiti._read_groups()
    assert "domain_jira" in groups
    assert "domain_confluence" in groups


async def test_steward_map_only_includes_active_agents_with_a_domain(monkeypatch):
    async def fake_list_agents(include_retired=True, roster_only=True):
        return [
            {"id": "jira-expert", "steward_group": "domain_jira"},
            {"id": "inbox-triage", "steward_group": ""},
        ]

    monkeypatch.setattr(repo, "list_agents", fake_list_agents)
    assert await graphiti.steward_map() == {"domain_jira": "jira-expert"}


def test_every_group_id_satisfies_graphitis_charset():
    """graphiti_core rejects group_ids outside [a-zA-Z0-9_-] — and add_episode
    is QUEUED, so a bad group id acks and then drops the episode with the
    error visible only in the pod log. The colon in the original
    `central_command:<agent_id>` scheme silently lost every private-scope write
    from 2026-08-01 to 2026-08-15 (25 approved episodes)."""
    import re

    from central_command.runtime.roster import SEED_IDS

    valid = re.compile(r"^[a-zA-Z0-9_-]+$")
    for group in ["domain_jira", "domain_confluence", graphiti.settings.graph_write_group]:
        assert valid.fullmatch(group), group
    for agent_id in SEED_IDS + ("knowledge-steward", "ea"):
        assert valid.fullmatch(graphiti.private_group(agent_id)), agent_id


async def test_executor_shared_scope_uses_default_group(monkeypatch, verification_rows, no_stewards):
    seen = {}

    async def fake_add_episode(name, episode_body, source_description, group_id=None):
        seen["group_id"] = group_id
        return "ack"

    monkeypatch.setattr(graphiti, "add_episode", fake_add_episode)
    args = {"name": "n", "episode_body": "b", "scope": "shared"}
    await _graph_add_episode(args, approver="lee", proposer="jira-expert")
    assert seen["group_id"] is None


async def test_executor_shared_scope_defaults_to_a_stewards_domain_group(
    monkeypatch, verification_rows
):
    """The write-side half of domain stewardship: an unscoped shared write
    from an agent with a steward_group lands in that domain, not the plain
    shared group."""
    seen = {}

    async def fake_add_episode(name, episode_body, source_description, group_id=None):
        seen["group_id"] = group_id
        return "ack"

    async def fake_get_agent(agent_id):
        return {"id": agent_id, "steward_group": "domain_jira"}

    monkeypatch.setattr(graphiti, "add_episode", fake_add_episode)
    monkeypatch.setattr(repo, "get_agent", fake_get_agent)
    args = {"name": "n", "episode_body": "b", "scope": "shared"}
    await _graph_add_episode(args, approver="lee", proposer="jira-expert")
    assert seen["group_id"] == "domain_jira"


async def test_executor_shared_scope_explicit_group_id_wins_over_steward(
    monkeypatch, verification_rows
):
    seen = {}

    async def fake_add_episode(name, episode_body, source_description, group_id=None):
        seen["group_id"] = group_id
        return "ack"

    async def fake_get_agent(agent_id):
        return {"id": agent_id, "steward_group": "domain_jira"}

    monkeypatch.setattr(graphiti, "add_episode", fake_add_episode)
    monkeypatch.setattr(repo, "get_agent", fake_get_agent)
    args = {"name": "n", "episode_body": "b", "scope": "shared", "group_id": "some_other_group"}
    await _graph_add_episode(args, approver="lee", proposer="jira-expert")
    assert seen["group_id"] == "some_other_group"


async def test_executor_private_scope_uses_proposers_partition_regardless_of_args(
    monkeypatch, verification_rows
):
    seen = {}

    async def fake_add_episode(name, episode_body, source_description, group_id=None):
        seen["group_id"] = group_id
        return "ack"

    monkeypatch.setattr(graphiti, "add_episode", fake_add_episode)
    # An agent-authored "agent_id" in args must be ignored — proposer is the
    # only trusted partition owner.
    args = {"name": "n", "episode_body": "b", "scope": "private", "agent_id": "someone-else"}
    await _graph_add_episode(args, approver="lee", proposer="jira-expert")
    assert seen["group_id"] == "central_command_jira-expert"


async def test_executor_stamps_marker_and_records_verification_before_the_write(
    monkeypatch, verification_rows
):
    """2026-08-19 spec: the ack is not graph state, so every approved episode
    leaves a verification row (written BEFORE the write — a call that times out
    after landing must still be audited) and carries the row's marker in
    source_description so the sweep can find the real Episodic node."""
    order = []
    seen = {}

    async def fake_add_episode(name, episode_body, source_description, group_id=None):
        order.append("write")
        seen["source_description"] = source_description
        seen["group_id"] = group_id
        return "ack"

    async def fake_create(**kwargs):
        order.append("row")
        verification_rows.append(kwargs)
        return {"id": "cc-test", **kwargs}

    monkeypatch.setattr(graphiti, "add_episode", fake_add_episode)
    monkeypatch.setattr(repo, "create_graph_verification", fake_create)
    args = {"name": "n", "episode_body": "b", "scope": "private"}
    await _graph_add_episode(args, approver="lee", proposer="jira-expert")

    assert order == ["row", "write"]
    (row,) = verification_rows
    assert row["marker"].startswith("proposal=")
    assert row["marker"] in seen["source_description"]
    assert row["episode_name"] == "n"
    assert row["scope"] == "private"
    # The row stores the RESOLVED group — private scope means the proposer's
    # partition, even though add_episode is called with the same value.
    assert row["group_id"] == "central_command_jira-expert" == seen["group_id"]


async def test_executor_private_scope_without_proposer_raises(monkeypatch):
    args = {"name": "n", "episode_body": "b", "scope": "private"}
    with pytest.raises(ExecutorError):
        await _graph_add_episode(args, approver="lee", proposer=None)


async def test_executor_unknown_scope_raises(monkeypatch):
    args = {"name": "n", "episode_body": "b", "scope": "team"}
    with pytest.raises(ExecutorError):
        await _graph_add_episode(args, approver="lee", proposer="jira-expert")
