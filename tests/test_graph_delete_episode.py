"""graph.delete_episode (design record 2026-10-04, D9; v2.61.0).

What is pinned here:

* **The rule is upstream's, and an upgrade that changes it fails the suite.**
  `neo4j_reader.deletion_set` / `compute_delete_preview` implement
  `Graphiti.remove_episode`'s rule ourselves (we delete in ONE transaction of
  our own — see `neo4j_writer.delete_episode` for why). The installed
  package's `remove_episode` source is hashed and its load-bearing lines are
  asserted, so a graphiti-core release that changes what it deletes cannot
  slip past.
* The preview computes that set, separately flags COLLATERAL facts and the
  surviving facts that lose this provenance, and fingerprints the deletion.
* Both the Executor and the deleting transaction REFUSE a set that changed
  since approval; the deletion is idempotent and does the provenance cleanup
  upstream omits.
* The deletion is a `remove_episode` job on the group's ingest queue: it
  waits behind earlier work in its group, is not held by the patch gate,
  is re-run (never marker-checked) by crash recovery, records
  `graph.episode.deleted` on completion and FAILS loudly on a refusal.
* The propose tool embeds the preview; the cockpit routes confirm a digest.

Nothing here touches a real graph: every Neo4j call goes through a fake
`run`, and the ingest worker's deletion seam (`graphiti_ingest._deleter`) is
replaced. A scratch-Neo4j differential run of our deletion against upstream's
`remove_episode` on the same fixture (identical survivors) is recorded in the
v2.61.0 CHANGELOG entry; it is not part of the suite (no test Neo4j exists).
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import pathlib
import uuid as _uuid

import pytest
from pydantic_ai import CallDeferred, ModelRetry

from central_command import events
from central_command.db import repo
from central_command.gateway import executor
from central_command.integrations import graphiti, graphiti_ingest, neo4j_reader, neo4j_writer
from central_command.runtime import hold, tools

# sha256 of `Graphiti.remove_episode`'s source segment in graphiti-core 0.30.2.
# If this fails after an upgrade, READ the new remove_episode: if the rule
# (which facts, which entities, DETACH or not) changed, change
# `neo4j_reader.deletion_set` / `compute_delete_preview` to match, then
# re-pin. Never re-pin without reading it.
REMOVE_EPISODE_SHA256 = "aef750251afea3f6defba30fe6bc27d3b576e1a833c10735881d5682ff4ed926"


def _installed(path: str) -> str:
    spec = importlib.util.find_spec("graphiti_core")  # locates, never executes
    return (pathlib.Path(spec.submodule_search_locations[0]) / path).read_text()


def _function_source(text: str, name: str, cls: str | None = None) -> str:
    tree = ast.parse(text)
    scope = tree
    if cls is not None:
        scope = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == cls)
    fn = next(n for n in ast.walk(scope)
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    return ast.get_source_segment(text, fn)


# --- the pin to upstream ---------------------------------------------------------


def test_our_deletion_rule_is_pinned_to_the_installed_remove_episode():
    src = _function_source(_installed("graphiti.py"), "remove_episode")
    assert hashlib.sha256(src.encode()).hexdigest() == REMOVE_EPISODE_SHA256, (
        "graphiti-core's Graphiti.remove_episode changed. Our deletion "
        "(neo4j_reader.deletion_set / compute_delete_preview, neo4j_writer."
        "delete_episode) implements its rule by hand — read the new source, "
        "match it, then re-pin REMOVE_EPISODE_SHA256.\n\n" + src
    )
    # The lines the rule IS, asserted by content too, so the failure says which.
    assert "for edge in edges:" in src and "edge.episodes and edge.episodes[0] == episode.uuid" in src
    assert "EntityEdge.get_by_uuids(self.driver, episode.entity_edges)" in src
    assert "get_mentioned_nodes(self.driver, [episode])" in src
    assert ("MATCH (e:Episodic)-[:MENTIONS]->(n:Entity {uuid: $uuid}) "
            "RETURN count(*) AS episode_count") in src
    assert "record['episode_count'] == 1" in src
    assert "Node.delete_by_uuids" in src and "episode.delete(self.driver)" in src


def test_upstreams_node_delete_detaches_which_is_where_collateral_comes_from():
    nodes = _installed("nodes.py")
    neo4j_branch = _function_source(nodes, "delete_by_uuids", cls="Node").split("case _:")[-1]
    assert "DETACH DELETE n" in neo4j_branch
    edges = _function_source(_installed("edges.py"), "delete_by_uuids", cls="Edge")
    assert "MENTIONS|RELATES_TO|HAS_MEMBER" in edges and "DELETE e" in edges


def test_our_queries_mirror_upstreams_reads():
    # get_mentioned_nodes: (episode:Episodic)-[:MENTIONS]->(n:Entity), DISTINCT.
    search_utils = _installed("search/search_utils.py")
    mentioned = _function_source(search_utils, "get_mentioned_nodes")
    assert "MATCH (episode:Episodic)-[:MENTIONS]->(n:Entity)" in mentioned
    assert "RETURN DISTINCT" in mentioned
    assert "(:Episodic {uuid: $uuid})-[:MENTIONS]->(n:Entity)" in neo4j_reader.PREVIEW_MENTIONED
    assert "WITH DISTINCT n" in neo4j_reader.PREVIEW_MENTIONED
    assert "count(*) AS mentions" in neo4j_reader.PREVIEW_MENTIONED
    # EntityEdge.get_by_uuids: (n:Entity)-[e:RELATES_TO]->(m:Entity) WHERE uuid IN.
    get_edges = _function_source(_installed("edges.py"), "get_by_uuids", cls="EntityEdge")
    assert "MATCH (n:Entity)-[e:RELATES_TO]->(m:Entity)" in get_edges
    assert "(a:Entity)-[r:RELATES_TO]->(b:Entity)" in neo4j_reader.PREVIEW_EDGES
    assert "r.uuid IN $uuids" in neo4j_reader.PREVIEW_EDGES


# --- the rule, as data ------------------------------------------------------------


def test_the_deletion_set_is_first_created_facts_and_singly_mentioned_entities():
    edges = [
        {"uuid": "f1", "episodes": ["X"]},          # created by X
        {"uuid": "f2", "episodes": ["Y", "X"]},     # X only CITES it
        {"uuid": "f3", "episodes": ["X", "Y"]},     # created by X, cited by Y
        {"uuid": "f4", "episodes": []},             # broken provenance: never ours to delete
    ]
    mentioned = [
        {"uuid": "A", "mentions": 1},
        {"uuid": "B", "mentions": 2},
        {"uuid": "C", "mentions": 1},
    ]
    facts, entities = neo4j_reader.deletion_set("X", edges, mentioned)
    assert [f["uuid"] for f in facts] == ["f1", "f3"]
    assert [n["uuid"] for n in entities] == ["A", "C"]


class FakeGraph:
    """Answers the preview queries from a tiny in-memory graph, and records
    every write query the deletion runs."""

    def __init__(self, episodes: dict, facts: dict, mentions: list[tuple[str, str]], names=None):
        self.episodes = episodes        # uuid -> {"name", "group_id", "entity_edges"}
        self.facts = facts              # uuid -> {"src", "dst", "episodes"}
        self.mentions = mentions        # (episode uuid, entity uuid)
        self.names = names or {}
        self.writes: list[str] = []

    def _fact_row(self, u):
        f = self.facts[u]
        return {"uuid": u, "name": "REL", "fact": f"fact {u}", "episodes": list(f["episodes"]),
                "source": f["src"], "source_name": self.names.get(f["src"], f["src"]),
                "target": f["dst"], "target_name": self.names.get(f["dst"], f["dst"])}

    async def run(self, query: str, **p):
        if query == neo4j_reader.PREVIEW_EPISODE:
            ep = self.episodes.get(p["uuid"])
            if ep is None:
                return []
            return [{"uuid": p["uuid"], "name": ep["name"], "content": "body",
                     "group_id": ep["group_id"], "source_description": "src",
                     "created_at": None, "valid_at": None,
                     "entity_edges": list(ep["entity_edges"])}]
        if query == neo4j_reader.PREVIEW_EDGES:
            return [self._fact_row(u) for u in p["uuids"] if u in self.facts]
        if query == neo4j_reader.PREVIEW_MENTIONED:
            ents = sorted({n for e, n in self.mentions if e == p["uuid"]})
            return [{"uuid": n, "name": self.names.get(n, n), "labels": ["Entity"],
                     "group_id": "g", "mentions": sum(1 for _, m in self.mentions if m == n)}
                    for n in ents]
        if query == neo4j_reader.PREVIEW_INCIDENT:
            return [self._fact_row(u) for u, f in self.facts.items()
                    if f["src"] in p["entities"] or f["dst"] in p["entities"]]
        if query == neo4j_reader.PREVIEW_CITING:
            return [self._fact_row(u) for u, f in self.facts.items() if p["uuid"] in f["episodes"]]
        if query == neo4j_reader.PREVIEW_OTHER_EPISODES:
            return [{"uuid": u} for u, ep in self.episodes.items()
                    if u != p["uuid"] and set(ep["entity_edges"]) & set(p["dead"])]
        self.writes.append(query)
        if query == neo4j_writer.DELETE_FACTS:
            for u in p["facts"]:
                self.facts.pop(u, None)
        elif query == neo4j_writer.DELETE_ENTITIES:
            for u in list(self.facts):
                if self.facts[u]["src"] in p["entities"] or self.facts[u]["dst"] in p["entities"]:
                    self.facts.pop(u)
            self.mentions = [(e, n) for e, n in self.mentions if n not in p["entities"]]
        elif query == neo4j_writer.DELETE_EPISODE:
            self.episodes.pop(p["uuid"], None)
            self.mentions = [(e, n) for e, n in self.mentions if e != p["uuid"]]
        elif query == neo4j_writer.STRIP_EPISODE_FROM_FACTS:
            hit = [f for f in self.facts.values() if p["uuid"] in f["episodes"]]
            for f in hit:
                f["episodes"] = [x for x in f["episodes"] if x != p["uuid"]]
            return [{"n": len(hit)}]
        elif query == neo4j_writer.STRIP_DEAD_FACTS_FROM_EPISODES:
            hit = [e for e in self.episodes.values() if set(e["entity_edges"]) & set(p["dead"])]
            for e in hit:
                e["entity_edges"] = [x for x in e["entity_edges"] if x not in p["dead"]]
            return [{"n": len(hit)}]
        return []


def _fixture() -> FakeGraph:
    # The same fixture the scratch-Neo4j differential run used against
    # upstream's remove_episode (survivors there: B, C, Y; f2, m3, m4).
    return FakeGraph(
        episodes={"X": {"name": "ep X", "group_id": "g", "entity_edges": ["f1", "f2", "f3", "f5"]},
                  "Y": {"name": "ep Y", "group_id": "g", "entity_edges": ["f2", "f4", "f3"]}},
        facts={"f1": {"src": "A", "dst": "B", "episodes": ["X"]},
               "f2": {"src": "B", "dst": "C", "episodes": ["Y", "X"]},
               "f3": {"src": "A", "dst": "C", "episodes": ["X", "Y"]},
               "f4": {"src": "C", "dst": "D", "episodes": ["Y"]},
               "f5": {"src": "B", "dst": "D", "episodes": ["X"]}},
        mentions=[("X", "A"), ("X", "B"), ("Y", "B"), ("Y", "C"), ("X", "D")],
        names={"A": "Jane Doe", "B": "Sam Rivers", "C": "Example Co", "D": "Lee Doe"},
    )


async def test_the_preview_lists_what_goes_flags_collateral_and_lost_provenance():
    g = _fixture()
    preview = await neo4j_reader.compute_delete_preview(g.run, "X")
    sets = neo4j_reader.preview_sets(preview)
    assert sets == {"episode": "X", "facts": ["f1", "f3", "f5"],
                    "collateral_facts": ["f4"], "entities": ["A", "D"]}
    # f2 survives (Y created it) and only loses X as a source.
    assert [f["uuid"] for f in preview["provenance_facts"]] == ["f2"]
    assert preview["episodes_losing_fact_refs"] == ["Y"]
    assert preview["facts"][0]["source_name"] == "Jane Doe"
    assert preview["episode"]["name"] == "ep X" and preview["episode"]["content"] == "body"
    assert preview["digest"] == neo4j_reader.preview_digest("X", ["f1", "f3", "f5"], ["f4"], ["A", "D"])
    json.dumps(preview)  # it rides inside a proposal


async def test_a_missing_episode_has_no_preview():
    assert await neo4j_reader.compute_delete_preview(_fixture().run, "nope") is None


def test_the_digest_is_order_free_and_moves_with_the_set():
    a = neo4j_reader.preview_digest("X", ["f1", "f3"], ["f4"], ["A", "D"])
    assert a == neo4j_reader.preview_digest("X", ["f3", "f1"], ["f4"], ["D", "A"])
    assert a != neo4j_reader.preview_digest("X", ["f1", "f3"], ["f4"], ["A"])


async def test_preview_changes_names_every_difference():
    g = _fixture()
    approved = await neo4j_reader.compute_delete_preview(g.run, "X")
    g.mentions.append(("Y", "A"))  # A is now mentioned by Y too: it survives
    current = await neo4j_reader.compute_delete_preview(g.run, "X")
    changes = neo4j_reader.preview_changes(approved, current)
    assert any("entities approved for deletion no longer would be: A" in c for c in changes)
    assert neo4j_reader.preview_changes(approved, approved) == []


# --- the deleting transaction -----------------------------------------------------


@pytest.fixture
def fake_tx(monkeypatch):
    holder = {}

    async def write_tx(work):
        return await work(holder["graph"].run)

    monkeypatch.setattr(neo4j_writer, "_write_tx", write_tx)
    return holder


async def test_the_deletion_removes_the_set_and_cleans_provenance(fake_tx):
    g = fake_tx["graph"] = _fixture()
    approved = await neo4j_reader.compute_delete_preview(g.run, "X")
    result = await neo4j_writer.delete_episode("X", expected=approved)
    assert set(g.facts) == {"f2"} and set(g.episodes) == {"Y"}
    assert g.facts["f2"]["episodes"] == ["Y"]          # the dead uuid left it
    assert g.episodes["Y"]["entity_edges"] == ["f2"]   # deleted facts left Y's list
    assert result["facts_deleted"] == ["f1", "f3", "f5"]
    assert result["collateral_deleted"] == ["f4"]
    assert result["entities_deleted"] == ["A", "D"]
    assert result["already_absent"] is False
    # The order is upstream's: facts, then entities (DETACH), then the episode.
    assert g.writes[:3] == [neo4j_writer.DELETE_FACTS, neo4j_writer.DELETE_ENTITIES,
                            neo4j_writer.DELETE_EPISODE]


async def test_a_changed_set_is_refused_and_nothing_is_deleted(fake_tx):
    g = fake_tx["graph"] = _fixture()
    approved = await neo4j_reader.compute_delete_preview(g.run, "X")
    g.mentions.append(("Y", "A"))
    with pytest.raises(neo4j_writer.PreviewChanged, match="nothing was deleted"):
        await neo4j_writer.delete_episode("X", expected=approved)
    assert g.writes == [] and "X" in g.episodes


async def test_a_rerun_after_the_episode_is_gone_is_done_and_still_cleans_up(fake_tx):
    g = fake_tx["graph"] = _fixture()
    del g.episodes["X"]  # as if the deletion committed and the landing was lost
    g.episodes["Y"]["entity_edges"] = ["f1", "f2"]
    result = await neo4j_writer.delete_episode("X", known_dead_facts=["f1"])
    assert result["already_absent"] is True
    assert "X" not in g.facts["f2"]["episodes"]
    assert g.episodes["Y"]["entity_edges"] == ["f2"]
    assert neo4j_writer.DELETE_EPISODE not in g.writes


# --- the Executor ------------------------------------------------------------------


@pytest.fixture
def preview_now(monkeypatch):
    state = {}

    async def fake_preview(uuid):
        return state.get("preview")

    monkeypatch.setattr(neo4j_reader, "episode_delete_preview", fake_preview)
    queued = []

    async def fake_enqueue(episode_uuid, group_id, *, preview, requested_by, proposal_id=None):
        queued.append((episode_uuid, group_id, preview, requested_by))
        return {"id": 77}

    monkeypatch.setattr(graphiti_ingest, "enqueue_delete", fake_enqueue)
    state["queued"] = queued
    return state


async def test_the_executor_refuses_a_proposal_that_carries_no_preview(preview_now):
    with pytest.raises(executor.ExecutorError, match="no preview"):
        await executor._graph_delete_episode({"episode_uuid": "X"}, "human:doe", "graph-curator")
    assert preview_now["queued"] == []


async def test_the_executor_refuses_when_the_set_changed(preview_now):
    g = _fixture()
    approved = await neo4j_reader.compute_delete_preview(g.run, "X")
    g.mentions.append(("Y", "A"))
    preview_now["preview"] = await neo4j_reader.compute_delete_preview(g.run, "X")
    with pytest.raises(executor.ExecutorError, match="changed since"):
        await executor._graph_delete_episode(
            {"episode_uuid": "X", "preview": approved}, "human:doe", "graph-curator")
    assert preview_now["queued"] == []


async def test_the_executor_refuses_an_episode_that_is_already_gone(preview_now):
    approved = await neo4j_reader.compute_delete_preview(_fixture().run, "X")
    with pytest.raises(executor.ExecutorError, match="already"):
        await executor._graph_delete_episode(
            {"episode_uuid": "X", "preview": approved}, "human:doe", None)


async def test_the_executor_queues_an_unchanged_deletion_on_the_episodes_group(preview_now):
    approved = await neo4j_reader.compute_delete_preview(_fixture().run, "X")
    preview_now["preview"] = approved
    out = await executor._graph_delete_episode(
        {"episode_uuid": "X", "preview": approved}, "human:doe", "graph-curator")
    assert "queued for deletion (ingest job 77)" in out
    ((uuid, group, preview, by),) = preview_now["queued"]
    assert (uuid, group, by) == ("X", "g", "human:doe")
    assert preview["digest"] == approved["digest"]


def test_the_capability_is_registered_gated_and_irreversible():
    from central_command.contract import ARG_SPECS
    from central_command.gateway.capabilities import REGISTRY
    from central_command.runtime.packs import PACKS

    cap = next(c for c in REGISTRY if c.name == "graph.delete_episode")
    assert cap.gate == "human approval" and cap.risk.startswith("IRREVERSIBLE")
    assert ARG_SPECS["graph.delete_episode"].required == ("episode_uuid",)
    assert "graph.delete_episode" in executor.HANDLERS
    curate = PACKS["graph-curate"]
    assert "graph.delete_episode" in {c.name for c in curate.capabilities}
    assert "propose_delete_episode" in curate.tool_names


# --- the propose tool ----------------------------------------------------------------


async def test_the_propose_tool_embeds_the_preview(monkeypatch):
    approved = await neo4j_reader.compute_delete_preview(_fixture().run, "X")

    async def fake_preview(uuid):
        return approved

    monkeypatch.setattr(graphiti, "episode_delete_preview", fake_preview)

    async def no_events(*a, **k):
        return None

    monkeypatch.setattr(events, "emit", no_events)
    with pytest.raises(CallDeferred) as raised:
        await tools.propose_delete_episode(None, "X", "the operator says this claim was wrong")
    proposal = raised.value.metadata["proposal"]
    (action,) = proposal["actions"]
    assert action["capability"] == "graph.delete_episode@v1"
    assert action["arguments"]["episode_uuid"] == "X"
    assert action["arguments"]["preview"]["digest"] == approved["digest"]
    assert action["reversibility"] == "irreversible"
    assert "3 fact(s), 2 entit(y/ies), 1 collateral fact(s)" in proposal["expected_effect"]


async def test_the_propose_tool_hands_back_an_unknown_uuid(monkeypatch):
    async def none(uuid):
        return None

    monkeypatch.setattr(graphiti, "episode_delete_preview", none)
    with pytest.raises(ModelRetry, match="list_graph_group_episodes"):
        await tools.propose_delete_episode(None, "nope", "why")


# --- the queue (needs Postgres) --------------------------------------------------------

from tests.conftest import needs_pg  # noqa: E402

PARKED = "PARKED_FOR_TEST"


@pytest.fixture
async def isolated_jobs():
    conn = await repo._conn()
    try:
        before = await conn.fetchval("select coalesce(max(id), 0) from graph_ingest_job")
        await conn.execute(
            f"update graph_ingest_job set status = '{PARKED}' where status in ('QUEUED', 'RUNNING')"
        )
    finally:
        await conn.close()
    try:
        yield
    finally:
        conn = await repo._conn()
        try:
            await conn.execute("delete from graph_ingest_job where id > $1", before)
            await conn.execute(
                f"update graph_ingest_job set status = 'QUEUED' where status = '{PARKED}'"
            )
        finally:
            await conn.close()


@pytest.fixture
def deleter(monkeypatch):
    calls = []
    behaviour = {}

    async def fake_delete(episode_uuid, *, expected=None, known_dead_facts=()):
        calls.append((episode_uuid, expected, list(known_dead_facts)))
        if "raise" in behaviour:
            raise behaviour["raise"]
        return {"uuid": episode_uuid, "already_absent": False, "facts_deleted": ["f1"],
                "collateral_deleted": [], "entities_deleted": ["A"],
                "facts_unlinked": 0, "episodes_updated": 0}

    monkeypatch.setattr(graphiti_ingest, "_deleter", lambda: fake_delete)
    monkeypatch.setattr(graphiti_ingest, "_patches_ok", lambda: True)
    monkeypatch.setattr(hold, "active", False)
    return {"calls": calls, "behaviour": behaviour}


def _group() -> str:
    return f"gd-{_uuid.uuid4().hex[:8]}"


def _preview(episode: str, group: str) -> dict:
    return {"episode": {"uuid": episode, "name": f"ep {episode}", "group_id": group},
            "facts": [{"uuid": "f1"}], "collateral_facts": [], "entities": [{"uuid": "A"}],
            "digest": "d"}


@needs_pg
async def test_a_deletion_job_runs_and_records_what_it_removed(isolated_jobs, deleter, monkeypatch):
    seen = []

    async def emit(kind, **kw):
        seen.append((kind, kw))

    monkeypatch.setattr(graphiti_ingest.events, "emit", emit)
    group = _group()
    job = await graphiti_ingest.enqueue_delete("EP1", group, preview=_preview("EP1", group),
                                               requested_by="operator")
    assert job["kind"] == "remove_episode" and job["verification_id"] is None
    (claimed,) = await repo.claim_ingest_jobs()
    assert await graphiti_ingest.run_job(claimed) == "done"
    row = await repo.get_ingest_job(job["id"])
    assert row["status"] == "DONE" and row["episode_uuid"] == "EP1"
    assert row["result"]["entities_deleted"] == ["A"]
    ((uuid, expected, dead),) = deleter["calls"]
    assert uuid == "EP1" and dead == ["f1"]
    assert [f["uuid"] for f in expected["facts"]] == ["f1"]
    (kind, kw), = [e for e in seen if e[0] == "graph.episode.deleted"]
    assert kw["ref_id"] == "EP1" and kw["payload"]["requested_by"] == "operator"
    assert (await repo.episode_deletion_job("EP1"))["id"] == job["id"]
    assert "EP1" in await repo.deleted_episode_uuids(["EP1", "other"])


@needs_pg
async def test_a_refused_deletion_fails_loudly(isolated_jobs, deleter, monkeypatch):
    seen = []

    async def emit(kind, **kw):
        seen.append((kind, kw))

    monkeypatch.setattr(graphiti_ingest.events, "emit", emit)
    deleter["behaviour"]["raise"] = neo4j_writer.PreviewChanged("the graph changed")
    group = _group()
    job = await graphiti_ingest.enqueue_delete("EP2", group, preview=_preview("EP2", group),
                                               requested_by="human:doe")
    (claimed,) = await repo.claim_ingest_jobs()
    assert await graphiti_ingest.run_job(claimed) == "failed"
    assert (await repo.get_ingest_job(job["id"]))["status"] == "FAILED"
    (kind, kw), = [e for e in seen if e[0] == "graph.ingest.failed"]
    assert kw["payload"]["kind"] == "remove_episode" and kw["payload"]["episode_uuid"] == "EP2"


@needs_pg
async def test_a_deletion_waits_behind_an_extraction_in_its_group(isolated_jobs, deleter):
    group = _group()
    pid = f"p-{_uuid.uuid4().hex[:8]}"
    await graphiti_ingest.enqueue(
        "first", "body", f"src | proposal={pid}", group, reference_time="2026-01-01T00:00:00Z",
        proposal_id=pid, scope="shared", marker=f"proposal={pid}",
    )
    await graphiti_ingest.enqueue_delete("EP3", group, preview=_preview("EP3", group),
                                         requested_by="operator")
    (head,) = await repo.claim_ingest_jobs()
    assert head["kind"] == "add_episode"
    assert await repo.claim_ingest_jobs() == []  # the deletion waits its turn


@needs_pg
async def test_the_patch_gate_does_not_hold_a_deletion(isolated_jobs, deleter, monkeypatch):
    monkeypatch.setattr(graphiti_ingest, "_patches_ok", lambda: False)

    class Indices:
        async def build_indices_and_constraints(self):
            return None

    monkeypatch.setattr(graphiti_ingest, "_client", lambda: Indices())
    w = graphiti_ingest.IngestWorker()
    w._recovered = True
    add_group, del_group = _group(), _group()
    pid = f"p-{_uuid.uuid4().hex[:8]}"
    await graphiti_ingest.enqueue(
        "held", "body", f"src | proposal={pid}", add_group, reference_time="2026-01-01T00:00:00Z",
        proposal_id=pid, scope="shared", marker=f"proposal={pid}",
    )
    job = await graphiti_ingest.enqueue_delete("EP4", del_group, preview=_preview("EP4", del_group),
                                               requested_by="operator")
    claimed = await w.tick()
    await w.settle()
    assert claimed == [job["id"]]
    assert (await repo.get_ingest_job(job["id"]))["status"] == "DONE"


@needs_pg
async def test_recovery_reruns_a_running_deletion_without_a_marker_lookup(isolated_jobs, deleter,
                                                                          monkeypatch):
    async def never(*a, **k):
        raise AssertionError("a deletion is never settled by marker")

    monkeypatch.setattr(neo4j_reader, "episode_by_marker", never)
    group = _group()
    job = await graphiti_ingest.enqueue_delete("EP5", group, preview=_preview("EP5", group),
                                               requested_by="operator", proposal_id="p-1")
    await repo.claim_ingest_jobs()
    out = await graphiti_ingest.recover()
    assert job["id"] in out["requeued"]
    assert (await repo.get_ingest_job(job["id"]))["status"] == "QUEUED"


# --- the cockpit routes -------------------------------------------------------------------


async def test_the_preview_route_404s_a_missing_episode(monkeypatch):
    from fastapi import HTTPException

    from central_command.api import routes

    async def none(uuid):
        return None

    monkeypatch.setattr(neo4j_reader, "episode_delete_preview", none)
    with pytest.raises(HTTPException) as e:
        await routes.graph_episode_delete_preview("nope")
    assert e.value.status_code == 404


@pytest.fixture
def route_world(monkeypatch):
    from central_command.api import routes

    state = {"emitted": [], "queued": []}
    approved = None

    async def preview(uuid):
        return state["preview"]

    async def enqueue(uuid, group, *, preview, requested_by, proposal_id=None):
        state["queued"].append((uuid, group, requested_by))
        return {"id": 9}

    async def emit(kind, **kw):
        state["emitted"].append((kind, kw, len(state["queued"])))

    monkeypatch.setattr(neo4j_reader, "episode_delete_preview", preview)
    monkeypatch.setattr(graphiti_ingest, "enqueue_delete", enqueue)
    monkeypatch.setattr(routes.events, "emit", emit)
    _ = approved
    return routes, state


async def test_a_stale_digest_is_refused_before_anything_is_queued(route_world):
    from fastapi import HTTPException

    routes, state = route_world
    state["preview"] = await neo4j_reader.compute_delete_preview(_fixture().run, "X")
    with pytest.raises(HTTPException) as e:
        await routes.graph_episode_delete("X", digest="stale")
    assert e.value.status_code == 409 and state["queued"] == []


async def test_a_confirmed_deletion_is_recorded_before_it_is_queued(route_world, monkeypatch):
    routes, state = route_world
    state["preview"] = await neo4j_reader.compute_delete_preview(_fixture().run, "X")

    async def done(job_id, timeout, poll=0.25):
        return {"id": job_id, "status": "DONE", "result": {"entities_deleted": ["A", "D"]}}

    monkeypatch.setattr(graphiti_ingest, "wait_for_job", done)
    out = await routes.graph_episode_delete("X", digest=state["preview"]["digest"])
    assert out == {"status": "done", "job_id": 9, "result": {"entities_deleted": ["A", "D"]}}
    (kind, kw, queued_before), = state["emitted"]
    assert kind == "graph.curated" and kw["payload"]["op"] == "episode.delete"
    assert queued_before == 0  # the authorising record precedes the job
    assert state["queued"] == [("X", "g", "operator")]


async def test_a_deletion_still_waiting_answers_202_queued(route_world, monkeypatch):
    routes, state = route_world
    state["preview"] = await neo4j_reader.compute_delete_preview(_fixture().run, "X")

    async def waiting(job_id, timeout, poll=0.25):
        return {"id": job_id, "status": "QUEUED"}

    monkeypatch.setattr(graphiti_ingest, "wait_for_job", waiting)
    resp = await routes.graph_episode_delete("X", digest=state["preview"]["digest"])
    assert resp.status_code == 202
    assert json.loads(resp.body) == {
        "status": "queued", "job_id": 9,
        "detail": "queued behind earlier work in this group; it runs in order",
    }


# --- Verify: a deleted episode is history, and says so ------------------------------------


async def test_the_sweep_says_a_deleted_episode_was_deleted(monkeypatch):
    from central_command.gateway import graph_auditor

    finished = {}

    async def job_for(vid):
        return {"status": "DONE", "episode_uuid": "EP"}

    async def deletion(uuid):
        return {"id": 5} if uuid == "EP" else None

    async def finish(vid, **kw):
        finished.update(kw)
        return True

    async def no_delta(uuid):
        raise AssertionError("a deleted episode is not audited as an empty extraction")

    async def no_emit(*a, **k):
        return None

    monkeypatch.setattr(repo, "ingest_job_for_verification", job_for)
    monkeypatch.setattr(repo, "episode_deletion_job", deletion)
    monkeypatch.setattr(repo, "finish_graph_verification", finish)
    monkeypatch.setattr(neo4j_reader, "episode_delta", no_delta)
    monkeypatch.setattr(graph_auditor.events, "emit", no_emit)
    row = {"id": "v1", "proposal_id": "p", "episode_name": "n", "marker": "m", "group_id": "g"}
    assert await graph_auditor.verify_one(row) == "awaiting"
    assert finished["status"] == "AWAITING_OPERATOR"
    assert finished["mechanical"] == {"missing": False, "episode_deleted": True, "deletion_job": 5}


async def test_the_verify_worklist_marks_rows_whose_episode_was_deleted(monkeypatch):
    """Wire shape: the cockpit's VerificationRow.episode_deleted."""
    from central_command.api import routes
    from central_command.gateway import graph_auditor

    rows = [
        {"id": "a", "status": "AWAITING_OPERATOR", "episode_uuid": "GONE"},
        {"id": "b", "status": "VERIFIED", "episode_uuid": "HERE"},
        {"id": "c", "status": "PROBLEM", "episode_uuid": None},
    ]

    async def list_rows(status=None, limit=None):
        return [dict(r) for r in rows if status is None or r["status"] == status]

    async def deleted(uuids):
        return {u for u in uuids if u == "GONE"}

    async def body(row):
        return "text"

    monkeypatch.setattr(repo, "list_graph_verifications", list_rows)
    monkeypatch.setattr(repo, "deleted_episode_uuids", deleted)
    monkeypatch.setattr(graph_auditor, "_approved_episode_body", body)
    out = await routes.graph_verifications()
    assert [r["episode_deleted"] for r in out["awaiting"]] == [True]
    assert {r["id"]: r["episode_deleted"] for r in out["recent_closed"]} == {"b": False, "c": False}


# --- wire shapes the cockpit hand-declares (.claude/rules/cockpit.md) -------------------


async def test_the_preview_wire_shape_is_what_the_cockpit_declares():
    """web/src/features/graph/useGraph.ts: EpisodeDeletePreview / PreviewFact."""
    preview = await neo4j_reader.compute_delete_preview(_fixture().run, "X")
    assert set(preview) == {"episode", "facts", "entities", "collateral_facts",
                            "provenance_facts", "episodes_losing_fact_refs", "digest"}
    assert set(preview["episode"]) == {"uuid", "name", "group_id", "content",
                                       "source_description", "created_at", "valid_at"}
    fact_keys = {"uuid", "name", "fact", "source", "source_name", "target", "target_name"}
    for key in ("facts", "collateral_facts", "provenance_facts"):
        assert all(set(f) == fact_keys for f in preview[key])
    assert all(set(n) == {"uuid", "name", "labels", "group_id"} for n in preview["entities"])


async def test_the_audit_reports_broken_provenance_beside_the_other_findings(monkeypatch):
    """web/src/features/graph/useGraph.ts: GraphAudit.health."""
    async def rows(query, **params):
        if query.startswith(neo4j_reader.AUDIT_BROKEN_FACT_PROVENANCE) and "count(e)" in query:
            return [{"c": 3}]
        if query.startswith(neo4j_reader.AUDIT_STALE_EPISODE_REFS) and "count(ep)" in query:
            return [{"c": 1}]
        if query.startswith(neo4j_reader.AUDIT_BROKEN_FACT_PROVENANCE):
            return [{"uuid": "f", "name": "R", "fact": "x", "source_name": "a",
                     "target_name": "b", "episodes": [], "missing_episodes": []}]
        if query.startswith(neo4j_reader.AUDIT_STALE_EPISODE_REFS):
            return [{"uuid": "ep", "name": "n", "group_id": "g", "missing_facts": ["f0"]}]
        return [{"c": 0}] if "count(" in query else []

    monkeypatch.setattr(neo4j_reader, "_read", rows)
    report = await neo4j_reader.audit(None, 0.9)
    health = report["health"]
    assert health["counts"]["broken_fact_provenance"] == 3
    assert health["counts"]["stale_episode_fact_refs"] == 1
    assert set(health["broken_fact_provenance"][0]) == {
        "uuid", "name", "fact", "source_name", "target_name", "episodes", "missing_episodes"}
    assert set(health["stale_episode_fact_refs"][0]) == {"uuid", "name", "group_id", "missing_facts"}


def test_every_pack_propose_tool_resumes_as_a_proposal():
    """A propose_* tool the resume path does not classify parks as 'unknown'
    and its approval never resumes the drafter — found while adding
    propose_delete_episode (nothing else pinned the list)."""
    from central_command.runtime import durable
    from central_command.runtime.packs import PACKS

    names = {t for p in PACKS.values() for t in p.tool_names if t.startswith("propose_")}
    assert names - durable.PROPOSE_TOOLS == set()
