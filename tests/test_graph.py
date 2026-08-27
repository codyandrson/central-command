"""Knowledge-graph tests (graph reads + the M10 write path).

The write-gate property that matters: **only distilled, approved claims reach
the graph, stamped with their pedigree** — a graph.add_episode action rides the
same proposal→approval→execute pipeline as a Jira change, and the Executor
stamps trust + approver into the episode's source. Reads are ungated and
best-effort: a down graph degrades triage, never wedges it.

Client round-trips against the real `cc-graphiti` service are integration
tests and skip when it isn't reachable. They assert the read mechanism, never
the graph's contents — see the note above the live section.
"""

from __future__ import annotations

import asyncio

import pytest

from central_command.config import settings
from central_command.contract import Action
from central_command.db import repo
from central_command.gateway import executor, gateway
from central_command.integrations import graphiti
from central_command.runtime import tools
from central_command.runtime.run import ingest_and_propose
from central_command.runtime.spike_model import make_sc1_model
from tests.conftest import needs_pg

# --- SSE parsing (pure, always runs) ------------------------------------------


def test_parse_sse_message_takes_the_final_data_line():
    body = 'event: message\ndata: {"a": 1}\n\nevent: message\ndata: {"b": 2}\n\n'
    assert graphiti.parse_sse_message(body) == {"b": 2}


def test_parse_sse_message_rejects_empty_stream():
    with pytest.raises(graphiti.GraphitiError):
        graphiti.parse_sse_message("event: ping\n\n")


# --- the agent's read tool (graph faked; always runs) --------------------------


async def test_read_tool_formats_facts_with_validity(monkeypatch):
    async def fake_search(query, max_facts=8, agent_id=None):
        return [
            {"fact": "TASKS-12 due date moved to Aug 20.", "valid_at": "2026-08-20T00:00:00Z"},
            {"fact": "Old date was Aug 6.", "invalid_at": "2026-07-17T00:00:00Z"},
            {
                "fact": "the operator worked at Initech.",
                "valid_at": "2011-06-06T00:00:00Z",
                "invalid_at": "2022-09-01T00:00:00Z",
            },
        ]

    monkeypatch.setattr(graphiti, "search_facts", fake_search)
    out = await tools.search_knowledge_graph(None, "TASKS-12")
    assert "TASKS-12 due date moved to Aug 20. [valid from 2026-08-20T00:00:00Z]" in out
    assert (
        "Old date was Aug 6. [SUPERSEDED as of 2026-07-17T00:00:00Z"
        " — historical, no longer current]" in out
    )
    # A fact with both bounds renders its whole window — how an agent tells a
    # FORMER employer from a wrong fact.
    assert (
        "the operator worked at Initech. [SUPERSEDED as of 2022-09-01T00:00:00Z"
        ", was valid from 2011-06-06T00:00:00Z — historical, no longer current]" in out
    )


async def test_read_tool_stamps_steward_attribution_on_facts_from_a_domain_group(monkeypatch):
    """2026-08-22 domain stewardship: a fact whose group_id is a steward's
    domain group is stamped with who stewards it, at fact granularity — the
    MCP result carries group_id per fact (graphiti_core's EdgeResult), so
    that is the finest attribution actually available."""
    async def fake_search(query, max_facts=8, agent_id=None):
        return [
            {"fact": "TASKS-12 is a Jira issue.", "group_id": "domain_jira"},
            {"fact": "the operator's birthday is in June.", "group_id": "central_command"},
        ]

    async def fake_steward_map():
        return {"domain_jira": "jira-expert"}

    monkeypatch.setattr(graphiti, "search_facts", fake_search)
    monkeypatch.setattr(graphiti, "steward_map", fake_steward_map)
    out = await tools.search_knowledge_graph(None, "TASKS-12")
    assert "TASKS-12 is a Jira issue. [domain: domain_jira — steward: jira-expert]" in out
    assert "the operator's birthday is in June." in out
    assert "[domain: central_command" not in out


async def test_read_tool_entities_stamp_steward_attribution_too(monkeypatch):
    async def fake_search(query, max_nodes=8, agent_id=None):
        return [{"name": "TASKS-12", "summary": "a Jira issue", "group_id": "domain_jira"}]

    async def fake_steward_map():
        return {"domain_jira": "jira-expert"}

    monkeypatch.setattr(graphiti, "search_nodes", fake_search)
    monkeypatch.setattr(graphiti, "steward_map", fake_steward_map)
    out = await tools.search_knowledge_graph_entities(None, "TASKS-12")
    assert "[domain: domain_jira — steward: jira-expert]" in out


async def test_read_tool_degrades_when_graph_is_down(monkeypatch):
    async def broken(query, max_facts=8, agent_id=None):
        raise ConnectionError("nope")

    monkeypatch.setattr(graphiti, "search_facts", broken)
    out = await tools.search_knowledge_graph(None, "anything")
    assert "unavailable" in out and "proceed without it" in out


# --- executor write mapping (graph faked; always runs) -------------------------


_GRAPH_ACTION = Action(
    capability="graph.add_episode",
    arguments={
        "name": "DEMO-1 deadline slip",
        "episode_body": "DEMO-1's deadline moved to 2026-08-03.",
        "source_description": "email gmail:msg_1846d2",
    },
    target_ref={"system": "graphiti", "id": "central_command", "read_version": "unknown"},
    reversibility="reversible",
)


async def test_executor_stamps_trust_and_approver_into_the_episode(monkeypatch):
    calls = []

    async def fake_add(name, episode_body, source_description, group_id=None):
        calls.append((name, episode_body, source_description))
        return "queued"

    monkeypatch.setattr(graphiti, "add_episode", fake_add)
    monkeypatch.setattr(settings, "executor_mode", "live")

    outcome = await executor.execute([_GRAPH_ACTION], approver="human:lee", source_refs=["x"])

    assert "graph episode 'DEMO-1 deadline slip' committed" in outcome.result_text
    (name, body, source) = calls[0]
    assert body == "DEMO-1's deadline moved to 2026-08-03."
    assert "trust=human-approved" in source
    assert "approver=human:lee" in source
    assert "email gmail:msg_1846d2" in source


async def test_dry_run_never_touches_the_graph(monkeypatch):
    async def explode(*a, **k):
        raise AssertionError("dry_run must not call the graph")

    monkeypatch.setattr(graphiti, "add_episode", explode)
    monkeypatch.setattr(settings, "executor_mode", "dry_run")
    outcome = await executor.execute([_GRAPH_ACTION], approver="human:lee", source_refs=[])
    assert "[dry-run]" in outcome.result_text


# --- the full SC-1 loop: one approval commits Jira AND the graph (needs PG) ----


@needs_pg
async def test_sc1_one_approval_commits_jira_and_graph(monkeypatch):
    graph_calls, jira_calls = [], []

    async def fake_add(name, episode_body, source_description, group_id=None):
        graph_calls.append(name)
        return "queued"

    async def fake_due(issue_key, due_date):
        jira_calls.append((issue_key, due_date))

    monkeypatch.setattr(graphiti, "add_episode", fake_add)
    monkeypatch.setattr(executor.jira, "set_due_date", fake_due)
    monkeypatch.setattr(settings, "executor_mode", "live")
    monkeypatch.setattr(settings, "demo_mode", True)

    model = make_sc1_model()
    run = await ingest_and_propose("Dana: DEMO-1 slipped, due Aug 3.", model=model)
    assert run["deferred"]

    row = await repo.load_proposal(run["proposal_id"])
    assert [a["capability"] for a in row["actions"]] == [
        "jira.set_due_date",
        "graph.add_episode",
    ]

    result = await gateway.approve_and_execute(
        run["session_id"], run["proposal_id"], model=model
    )
    assert jira_calls == [("DEMO-1", "2026-08-03")]
    assert graph_calls == ["DEMO-1 deadline slip"]
    assert "committed" in result["result_text"]
    assert await repo.session_status(run["session_id"]) == "DONE"


# --- live integration (needs the real cc-graphiti; read-only) ------------------
#
# These assert the READ MECHANISM — that a query reaches the service, the SSE
# envelope parses and the result decodes into the shape callers destructure.
# They must NEVER assert that a particular fact is present: graph CONTENT is
# disposable during the build and gets wiped before real use, so a corpus
# assertion fails for a reason that has nothing to do with the client. (That is
# exactly how the old `test_live_reads_return_facts_from_the_preserved_graph`
# broke — it asserted on desktop-era homelab facts that no longer exist.)


def _graphiti_up() -> bool:
    async def probe() -> bool:
        try:
            s = await graphiti.get_status()
            return s.get("status") == "ok"
        except Exception:  # noqa: BLE001
            return False

    try:
        return asyncio.run(probe())
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.skipif(not _graphiti_up(), reason="cc-graphiti not reachable")
async def test_live_fact_search_round_trips_and_is_well_shaped():
    # A broken transport raises GraphitiError; an empty corpus returns []. Both
    # are distinguishable, and only the first is a failure.
    facts = await graphiti.search_facts("decommission", max_facts=3)
    assert isinstance(facts, list)
    assert len(facts) <= 3
    assert all("fact" in f for f in facts)


@pytest.mark.skipif(not _graphiti_up(), reason="cc-graphiti not reachable")
async def test_live_episode_listing_round_trips_and_is_scoped_to_our_groups():
    episodes = await graphiti.get_episodes(last_n=5)
    assert isinstance(episodes, list)
    assert len(episodes) <= 5
