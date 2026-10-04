"""Per-agent scoped knowledge-graph writes and widened reads (unit only — no
Postgres, no network; the read layer's search/episode seams and the
Executor's enqueue are faked throughout)."""

import pytest

from central_command.db import repo
from central_command.gateway.executor import ExecutorError, _graph_add_episode
from central_command.integrations import graphiti, graphiti_ingest, neo4j_reader
from tests.conftest import needs_pg


@needs_pg
async def test_schema_seeds_the_founding_experts_steward_groups():
    """A fresh database (conftest re-executes schema.sql into this checkout's test database
    every run) must seed the two founding experts' domain groups — that's
    what makes domain stewardship work on a brand-new install, not just a
    hand-migrated one."""
    jira = await repo.get_agent("jira-expert")
    confluence = await repo.get_agent("confluence-expert")
    assert jira["steward_group"] == "domain_jira"
    assert confluence["steward_group"] == "domain_confluence"


@pytest.fixture
def enqueued(monkeypatch):
    """Keep the handler DB-free: capture what the Executor enqueues (the
    verification row and the ingest job are written together by
    `graphiti_ingest.enqueue`) instead of writing it. Mirrors the REAL
    signature: reference_time keyword-only, no default."""
    calls = []

    async def fake_enqueue(name, episode_body, source_description, group_id, *,
                           reference_time, proposal_id, scope, marker):
        calls.append({"name": name, "episode_body": episode_body,
                      "source_description": source_description, "group_id": group_id,
                      "reference_time": reference_time, "proposal_id": proposal_id,
                      "scope": scope, "marker": marker})
        return {"verification": {"id": "cc-test"}, "job": {"id": 1}}

    monkeypatch.setattr(graphiti_ingest, "enqueue", fake_enqueue)
    return calls


@pytest.fixture
def captured(monkeypatch):
    """The read layer's two seams: library search and the episode Cypher."""
    calls = []

    async def fake_search(kind, query, limit, group_ids):
        calls.append((kind, {"group_ids": group_ids, "limit": limit}))

        class _Results:
            edges: list = []
            nodes: list = []

        return _Results()

    async def fake_latest(group_ids, limit):
        calls.append(("episodes", {"group_ids": group_ids, "limit": limit}))
        return []

    monkeypatch.setattr(graphiti, "_search", fake_search)
    monkeypatch.setattr(neo4j_reader, "latest_episodes", fake_latest)
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


async def test_add_episode_defaults_to_the_shared_group(enqueued, no_stewards):
    args = {"name": "n", "episode_body": "b", "reference_time": "2026-01-01T00:00:00Z",
            "scope": "shared"}
    await _graph_add_episode(args, approver="lee", proposer=None)
    assert enqueued[0]["group_id"] == graphiti.settings.graph_write_group
    # The reference time rides every enqueued episode (2026-09-19): Graphiti's
    # own default is "the moment I processed this", which is the assumption
    # the argument exists to end — so nothing on the path has a default.
    assert enqueued[0]["reference_time"] == "2026-01-01T00:00:00Z"


def test_enqueue_has_no_reference_time_default():
    import inspect
    param = inspect.signature(graphiti_ingest.enqueue).parameters["reference_time"]
    assert param.kind is inspect.Parameter.KEYWORD_ONLY
    assert param.default is inspect.Parameter.empty


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
    """graphiti_core rejects group_ids outside [a-zA-Z0-9_-] — under the
    retired MCP server add_episode was QUEUED in memory, so a bad group id
    acked and then dropped the episode with the error visible only in the pod
    log (the ingest worker now fails such a job loudly). The colon in the original
    `central_command:<agent_id>` scheme silently lost every private-scope write
    from 2026-08-01 to 2026-08-15 (25 approved episodes)."""
    import re

    from central_command.runtime.roster import SEED_IDS

    valid = re.compile(r"^[a-zA-Z0-9_-]+$")
    for group in ["domain_jira", "domain_confluence", graphiti.settings.graph_write_group]:
        assert valid.fullmatch(group), group
    for agent_id in SEED_IDS + ("knowledge-steward", "ea"):
        assert valid.fullmatch(graphiti.private_group(agent_id)), agent_id


async def test_executor_shared_scope_uses_default_group(monkeypatch, enqueued, no_stewards):
    args = {"name": "n", "episode_body": "b", "reference_time": "2026-01-01T00:00:00Z", "scope": "shared"}
    await _graph_add_episode(args, approver="lee", proposer="jira-expert")
    assert enqueued[0]["group_id"] == graphiti.settings.graph_write_group


async def test_executor_shared_scope_defaults_to_a_stewards_domain_group(
    monkeypatch, enqueued
):
    """The write-side half of domain stewardship: an unscoped shared write
    from an agent with a steward_group lands in that domain, not the plain
    shared group."""
    async def fake_get_agent(agent_id):
        return {"id": agent_id, "steward_group": "domain_jira"}

    monkeypatch.setattr(repo, "get_agent", fake_get_agent)
    args = {"name": "n", "episode_body": "b", "reference_time": "2026-01-01T00:00:00Z", "scope": "shared"}
    await _graph_add_episode(args, approver="lee", proposer="jira-expert")
    assert enqueued[0]["group_id"] == "domain_jira"


async def test_executor_shared_scope_explicit_group_id_wins_over_steward(
    monkeypatch, enqueued
):
    async def fake_get_agent(agent_id):
        return {"id": agent_id, "steward_group": "domain_jira"}

    monkeypatch.setattr(repo, "get_agent", fake_get_agent)
    args = {"name": "n", "episode_body": "b", "reference_time": "2026-01-01T00:00:00Z", "scope": "shared", "group_id": "some_other_group"}
    await _graph_add_episode(args, approver="lee", proposer="jira-expert")
    assert enqueued[0]["group_id"] == "some_other_group"


async def test_executor_private_scope_uses_proposers_partition_regardless_of_args(
    monkeypatch, enqueued
):
    # An agent-authored "agent_id" in args must be ignored — proposer is the
    # only trusted partition owner.
    args = {"name": "n", "episode_body": "b", "reference_time": "2026-01-01T00:00:00Z", "scope": "private", "agent_id": "someone-else"}
    await _graph_add_episode(args, approver="lee", proposer="jira-expert")
    assert enqueued[0]["group_id"] == "central_command_jira-expert"


async def test_executor_stamps_marker_and_enqueues_verification_and_job_together(
    monkeypatch, enqueued
):
    """2026-08-19 spec, durable since 2026-10-04: every approved episode leaves
    a verification row AND an ingest job (one `enqueue`, one transaction —
    tests/test_graph_ingest.py proves the transaction) and carries the row's
    marker in source_description, which is the idempotency key the worker's
    crash recovery finds the Episodic node by."""
    args = {"name": "n", "episode_body": "b", "reference_time": "2026-01-01T00:00:00Z", "scope": "private"}
    await _graph_add_episode(args, approver="lee", proposer="jira-expert")

    (row,) = enqueued
    assert row["marker"].startswith("proposal=")
    assert row["marker"] in row["source_description"]
    assert row["proposal_id"] == row["marker"].split("=", 1)[1]
    assert row["name"] == "n"
    assert row["scope"] == "private"
    # The RESOLVED group — private scope means the proposer's partition.
    assert row["group_id"] == "central_command_jira-expert"


async def test_executor_private_scope_without_proposer_raises(monkeypatch):
    args = {"name": "n", "episode_body": "b", "reference_time": "2026-01-01T00:00:00Z", "scope": "private"}
    with pytest.raises(ExecutorError):
        await _graph_add_episode(args, approver="lee", proposer=None)


async def test_executor_unknown_scope_raises(monkeypatch):
    args = {"name": "n", "episode_body": "b", "reference_time": "2026-01-01T00:00:00Z", "scope": "team"}
    with pytest.raises(ExecutorError):
        await _graph_add_episode(args, approver="lee", proposer="jira-expert")


# --- for_agent (2026-09-01): a private rule ABOUT a teammate lands in THEIR partition


async def test_executor_private_for_agent_targets_that_agents_partition(
    monkeypatch, enqueued
):
    async def fake_get_agent(agent_id):
        return {"id": agent_id, "role": "triage email", "status": "ACTIVE"}

    monkeypatch.setattr(repo, "get_agent", fake_get_agent)
    args = {"name": "n", "episode_body": "b", "reference_time": "2026-01-01T00:00:00Z", "scope": "private", "for_agent": "inbox-triage"}
    await _graph_add_episode(args, approver="lee", proposer="ea")
    assert enqueued[0]["group_id"] == "central_command_inbox-triage"


@pytest.mark.parametrize("row", [
    None,
    {"id": "ghost", "role": "", "status": "ACTIVE"},        # registered, never hired
    {"id": "ghost", "role": "old hand", "status": "RETIRED"},
])
async def test_executor_private_for_agent_must_be_an_active_roster_member(
    monkeypatch, enqueued, row
):
    async def fake_get_agent(agent_id):
        return row

    monkeypatch.setattr(repo, "get_agent", fake_get_agent)
    args = {"name": "n", "episode_body": "b", "reference_time": "2026-01-01T00:00:00Z", "scope": "private", "for_agent": "ghost"}
    with pytest.raises(ExecutorError, match="for_agent"):
        await _graph_add_episode(args, approver="lee", proposer="ea")
    assert enqueued == []  # nothing queued


async def test_executor_private_for_agent_self_needs_no_roster_lookup(
    monkeypatch, enqueued
):
    """for_agent == proposer is the plain private path — no lookup, so a
    proposer that is not (yet) a roster member keeps working as before."""
    async def fake_get_agent(agent_id):
        raise AssertionError("no lookup expected")

    monkeypatch.setattr(repo, "get_agent", fake_get_agent)
    args = {"name": "n", "episode_body": "b", "reference_time": "2026-01-01T00:00:00Z", "scope": "private", "for_agent": "ea"}
    await _graph_add_episode(args, approver="lee", proposer="ea")
    assert enqueued[0]["group_id"] == "central_command_ea"


# --- the curator's any-partition reads (graph-curate pack) ---------------------


async def test_group_ids_override_bypasses_caller_scope(captured, no_stewards):
    await graphiti.search_facts("q", agent_id="graph-curator", group_ids=["central_command_ea"])
    await graphiti.search_nodes("q", agent_id="graph-curator", group_ids=["central_command_ea"])
    await graphiti.get_group_episodes(["central_command_ea"])
    for _, args in captured:
        assert args["group_ids"] == ["central_command_ea"]


async def test_known_groups_covers_shared_domains_and_every_roster_partition(monkeypatch):
    async def fake_list_agents(include_retired=True, roster_only=True):
        assert include_retired is False
        return [{"id": "ea", "steward_group": ""}, {"id": "jira-expert", "steward_group": "domain_jira"}]

    monkeypatch.setattr(repo, "list_agents", fake_list_agents)
    groups = await graphiti.known_groups()
    assert "domain_jira" in groups
    assert "central_command_ea" in groups and "central_command_jira-expert" in groups
    assert groups.index("central_command") < groups.index("central_command_ea")


def test_any_partition_reads_are_granted_only_with_graph_curate():
    """The one exception to 'an agent never reads another's partition' is
    held by the curate pack alone — no read-only pack may carry it."""
    from central_command.runtime import packs

    wide = {"list_graph_groups", "list_graph_group_episodes", "search_graph_group"}
    for pack in packs.PACKS.values():
        if pack.name == "graph-curate":
            assert wide <= set(pack.tool_names)
        else:
            assert not (wide & set(pack.tool_names)), pack.name


def test_rescope_episode_is_wired_end_to_end():
    from central_command.contract.args import ARG_SPECS
    from central_command.gateway import capabilities, executor
    from central_command.integrations import neo4j_writer
    from central_command.runtime import packs

    assert ARG_SPECS["graph.rescope_episode"].required == ("episode_uuid", "group_id")
    assert "graph.rescope_episode" in executor.HANDLERS
    assert callable(neo4j_writer.rescope_episode)
    assert any(c.name == "graph.rescope_episode" for c in packs.PACKS["graph-curate"].capabilities)
    assert any(c.name == "graph.rescope_episode" for c in capabilities.REGISTRY)
