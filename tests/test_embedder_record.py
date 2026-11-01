"""The graph declares its embedder, and the one check closes every embedding read
on a mismatch (epic-embedder-integrity R1, R2 gate 4).

The acceptance that matters is the last section: a mismatch PARKS the session,
because a plain exception would classify as a NO and tell the agent to proceed
without the graph. The graph here is a fake driver and the embedder a fake
`request_embedding`; `no_live_graph_writes` stays on, and one test proves it
still refuses this module's writes.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from neo4j.exceptions import ServiceUnavailable
from pydantic_ai.exceptions import CallDeferred
from pydantic_ai.messages import ModelRequest, ToolReturnPart

from central_command.api import orchestration, routes
from central_command.config import Settings, settings
from central_command.contract import (
    TRANSIENT,
    DependencyDown,
    EmbedderDown,
    EmbedderMismatch,
    classify_failure,
)
from central_command.contract.embedding_text import CANARY_TEXTS, TEXT_RECIPE
from central_command.contract.failures import EMBEDDER_REMEDY
from central_command.db import repo
from central_command.integrations import embedder_record, graphiti, neo4j_reader, neo4j_writer
from central_command.runtime import durable, models, tools
from central_command.runtime.deps import TriageDeps
from tests.conftest import aresolve, make_parks_due, needs_pg
from tests.fake_world import fake_world
from tests.test_outage_doctrine import _model, _Probes, _task

pytestmark = pytest.mark.embedder_check_live

SRC = Path(embedder_record.__file__).resolve().parents[1]
ALIAS = "cc-embedding"
DIMS = 4
REMEDY = "run: python -m central_command.graph_reembed status"
DEGRADED = "proceed without it"
FACT = "The renewal of the example.com contract is due in November"


def _vector(text: str) -> list[float]:
    """A deterministic unit-ish vector per text, as a deterministic embedder gives."""
    seed = sum(text.encode()) % 97 + 1
    return [float(seed), 1.0, 2.0, 3.0]


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def __aiter__(self):
        async def gen():
            for row in self.rows:
                yield SimpleNamespace(data=lambda row=row: row)

        return gen()


class _Session:
    def __init__(self, graph):
        self.graph = graph

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def run(self, query, **params):
        return _Result(self.graph.answer(query, **params))


class FakeGraph:
    """The driver, the record and the census, plus the embedder."""

    def __init__(self):
        self.record: dict | None = None
        self.widths = {"entity": {}, "edge": {}, "community": {}}
        self.queries: list[str] = []
        self.embedded: list[str] = []
        self.embed = _vector
        self.embed_error: Exception | None = None
        self.read_error: Exception | None = None
        self.search_calls = 0

    def session(self, **kw):
        return _Session(self)

    def declare(self, **fields):
        self.record = {
            "key": "embedder", "alias": ALIAS, "dimensions": DIMS, "epoch": 1,
            "text_recipe": TEXT_RECIPE, "state": "stable",
            "canary_texts": list(CANARY_TEXTS),
            "canary_vectors": json.dumps([_vector(t) for t in CANARY_TEXTS]),
            **fields,
        }

    def answer(self, query, **params):
        self.queries.append(query)
        if self.read_error is not None:
            raise self.read_error
        if "CONSTRAINT" in query:
            return []
        if "WHERE m.alias IS NULL" in query:
            if (self.record or {}).get("alias") or (self.record or {}).get("state") in (
                "migrating", "awaiting_config",
            ):
                return []
            return self._set(params["fields"])
        if query.startswith("MERGE"):
            return self._set(params["fields"])
        if "CCGraphMeta" in query:
            return [{"m": dict(self.record)}] if self.record else []
        for kind, label in (("entity", "Entity"), ("edge", "RELATES_TO"), ("community", "Community")):
            if label in query:
                return [{"width": w, "n": n} for w, n in self.widths[kind].items()]
        raise AssertionError(f"an unexpected query: {query}")

    def _set(self, fields):
        self.record = {"key": "embedder", **(self.record or {})}
        for name, value in fields.items():
            if value is None:
                self.record.pop(name, None)
            else:
                self.record[name] = value
        return [{"m": dict(self.record)}]

    async def request_embedding(self, text, timeout=60.0):
        self.embedded.append(text)
        if self.embed_error is not None:
            raise self.embed_error
        return self.embed(text)

    def writes(self) -> list[str]:
        return [q for q in self.queries if q.startswith(("MERGE", "CREATE"))]


@pytest.fixture
def graph(monkeypatch):
    fake = FakeGraph()
    monkeypatch.setattr(settings, "embed_alias", ALIAS)
    monkeypatch.setattr(settings, "embed_dim", DIMS)
    monkeypatch.setattr(settings, "embedder_check_ttl_seconds", 30.0)
    monkeypatch.setattr(settings, "embedder_canary_threshold", 0.999)
    monkeypatch.setattr(neo4j_reader, "_get_driver", lambda: fake)

    async def write(query, **params):
        return fake.answer(query, **params)

    monkeypatch.setattr(neo4j_writer, "_write", write)
    monkeypatch.setattr(neo4j_writer, "request_embedding", fake.request_embedding)
    return fake


@pytest.fixture
def search(monkeypatch, graph):
    """`graphiti.search_facts` for real down to `search_()`, which is counted."""

    class _Library:
        async def search_(self, query, config=None, group_ids=None, **kw):
            graph.search_calls += 1
            return SimpleNamespace(edges=[SimpleNamespace(uuid="edge-1")], nodes=[])

    async def _groups(agent_id):
        return ["central_command"]

    async def _no_stewards():
        return {}

    monkeypatch.setattr(graphiti, "_client", lambda: SimpleNamespace(get_graphiti=_Library))
    monkeypatch.setattr(graphiti, "_recipe", lambda *a, **k: None)
    monkeypatch.setattr(graphiti, "fact_filters", lambda **k: None)
    monkeypatch.setattr(graphiti, "fact_result",
                        lambda e: {"uuid": e.uuid, "fact": FACT, "name": "DUE_ON"})
    monkeypatch.setattr(graphiti, "groups_for", _groups)
    monkeypatch.setattr(graphiti, "steward_map", _no_stewards)

    async def _no_wait(delay):
        return None

    monkeypatch.setattr(tools, "_sleep", _no_wait)
    return graph


def _changed_model(text: str) -> list[float]:
    """Cosine 0.5 against a canary recorded as [1, 0, 0, 0]."""
    return [0.5, 0.8660254037844386, 0.0, 0.0]


def _park_ctx(park: bool = True):
    return SimpleNamespace(deps=TriageDeps(agent_id="jira-expert", park_reads=park))


# ── the verdicts ─────────────────────────────────────────────────────────────


async def test_vectors_and_no_record_is_reason_i(graph):
    graph.widths["entity"] = {DIMS: 3}
    verdict = await embedder_record.check()
    assert (verdict.status, verdict.code) == (embedder_record.MISMATCH, "i")
    assert verdict.reason == f"graph has vectors but no embedder record; {REMEDY}"
    assert not verdict.open


@pytest.mark.parametrize("kind", ["entity", "edge", "community"])
async def test_a_vector_of_any_kind_counts_as_vectors(graph, kind):
    graph.widths[kind] = {DIMS: 1}
    assert (await embedder_record.check()).code == "i"


async def test_a_record_with_no_alias_counts_as_absent(graph):
    graph.record = {"key": "embedder", "epoch": 0}
    graph.widths["edge"] = {DIMS: 2}
    assert (await embedder_record.check()).code == "i"
    embedder_record.clear_cache()
    graph.widths["edge"] = {}
    assert (await embedder_record.check()).status == embedder_record.ABSENT_EMPTY


async def test_an_empty_graph_with_no_record_is_open(graph):
    verdict = await embedder_record.check()
    assert verdict.status == embedder_record.ABSENT_EMPTY and verdict.open
    assert graph.embedded == [], "nothing to compare, so nothing is embedded"
    assert graph.writes() == [], "the check writes nothing"


@pytest.mark.parametrize("fields,reason", [
    ({"alias": "cc-embedding-v2"}, f"configured {ALIAS}/{DIMS}, graph holds cc-embedding-v2/{DIMS}"),
    ({"dimensions": 8}, f"configured {ALIAS}/{DIMS}, graph holds {ALIAS}/8"),
])
async def test_a_record_the_configuration_disagrees_with_is_reason_ii(graph, fields, reason):
    graph.declare(**fields)
    verdict = await embedder_record.check()
    assert verdict.code == "ii"
    assert verdict.reason == f"{reason}; {REMEDY}"


async def test_a_census_width_the_record_does_not_declare_is_reason_iii(graph):
    graph.declare()
    graph.widths["entity"] = {DIMS: 90, 768: 8}
    verdict = await embedder_record.check()
    assert verdict.code == "iii"
    assert verdict.reason == f"census found width 768 on 8 entity, record says {DIMS}; {REMEDY}"


async def test_a_canary_of_the_wrong_width_is_reason_iii_too(graph):
    graph.declare()
    graph.embed = lambda text: [1.0] * 768
    verdict = await embedder_record.check()
    assert verdict.code == "iii"
    assert verdict.reason == f"census found width 768 on 3 canary, record says {DIMS}; {REMEDY}"


async def test_a_changed_model_behind_the_alias_is_reason_iv(graph):
    graph.declare(canary_vectors=json.dumps([[1.0, 0.0, 0.0, 0.0]] * 3))
    graph.embed = _changed_model
    verdict = await embedder_record.check()
    assert verdict.code == "iv"
    assert verdict.reason == (
        f"the model behind {ALIAS} has changed since the graph was embedded "
        f"(canary cosine 0.5000); {REMEDY}"
    )


async def test_one_changed_canary_is_enough(graph):
    graph.declare()
    graph.embed = lambda text: (
        _vector(text) if text != CANARY_TEXTS[1] else [-v for v in _vector(text)]
    )
    assert (await embedder_record.check()).code == "iv"


async def test_the_threshold_is_the_setting(graph, monkeypatch):
    graph.declare(canary_vectors=json.dumps([[1.0, 0.0, 0.0, 0.0]] * 3))
    graph.embed = _changed_model
    monkeypatch.setattr(settings, "embedder_canary_threshold", 0.4)
    assert (await embedder_record.check()).status == embedder_record.OK


@pytest.mark.parametrize("fields", [
    {"canary_vectors": None},
    {"canary_vectors": "not json"},
    {"canary_vectors": json.dumps([[1.0, 0.0, 0.0, 0.0]])},
    {"canary_vectors": json.dumps([[1.0, 0.0]] * 3)},
    {"canary_texts": []},
])
async def test_a_record_whose_canaries_cannot_be_compared_closes(graph, fields):
    """Nothing can confirm the model, and an answer that lets it through would
    be the fail-open. A reason of its own, because 'has changed' would be a guess."""
    graph.declare(**fields)
    verdict = await embedder_record.check()
    assert verdict.code == "iv"
    assert verdict.reason == (
        f"the record for {ALIAS} holds no usable canaries, so the model behind it cannot "
        f"be confirmed; {REMEDY}"
    )
    assert graph.embedded == []


@pytest.mark.parametrize("state", ["migrating", "awaiting_config"])
async def test_a_migration_is_reason_v_before_anything_else(graph, state):
    """Before the alias and the canaries, which are expected to disagree by then,
    and with no embedding, so a down embedder cannot turn it into DOWN."""
    graph.declare(state=state, alias="cc-embedding-v2")
    graph.widths["entity"] = {768: 8}
    graph.embed_error = httpx.ConnectError("refused")
    verdict = await embedder_record.check()
    assert verdict.code == "v"
    assert verdict.reason == f"embedding migration in progress ({state}); {REMEDY}"


async def test_an_interim_record_closes_even_an_empty_graph(graph):
    graph.record = {"key": "embedder", "epoch": 0, "state": "migrating"}
    assert (await embedder_record.check()).code == "v"


async def test_a_declared_record_in_an_unknown_state_closes(graph):
    graph.declare(state="half-done")
    assert (await embedder_record.check()).code == "v"
    embedder_record.clear_cache()
    graph.declare(state=None)
    assert (await embedder_record.check()).code == "v"


async def test_a_record_that_holds_is_ok(graph):
    graph.declare()
    graph.widths = {"entity": {DIMS: 98}, "edge": {DIMS: 111}, "community": {}}
    verdict = await embedder_record.check()
    assert verdict.status == embedder_record.OK and verdict.open
    assert verdict.reason is None
    assert graph.embedded == list(CANARY_TEXTS)


# ── the cache ────────────────────────────────────────────────────────────────


async def test_a_verdict_is_reused_until_the_ttl_passes(graph, monkeypatch):
    graph.declare()
    now = [1000.0]
    monkeypatch.setattr(embedder_record.time, "monotonic", lambda: now[0])
    await embedder_record.check()
    await embedder_record.check()
    assert len(graph.embedded) == 3, "the second check reads nothing"
    now[0] += 29.0
    await embedder_record.check()
    assert len(graph.embedded) == 3
    now[0] += 2.0
    await embedder_record.check()
    assert len(graph.embedded) == 6


async def test_force_asks_afresh_and_keeps_the_answer(graph):
    graph.declare()
    assert (await embedder_record.check()).open
    graph.declare(state="migrating")
    assert (await embedder_record.check()).open, "still the cached answer"
    assert (await embedder_record.check(force=True)).code == "v"
    assert (await embedder_record.check()).code == "v"


async def test_a_mismatch_is_cached_too(graph):
    graph.widths["entity"] = {DIMS: 1}
    await embedder_record.check()
    reads = len(graph.queries)
    assert (await embedder_record.check()).code == "i"
    assert len(graph.queries) == reads


@pytest.mark.parametrize("error", [
    httpx.ConnectError("refused"),
    EmbedderDown(httpx.ConnectError("refused")),
    httpx.HTTPStatusError(
        "503", request=httpx.Request("POST", "http://proxy.example.com"),
        response=httpx.Response(503),
    ),
])
async def test_an_embedder_that_cannot_answer_propagates_and_is_not_cached(graph, error):
    graph.declare()
    graph.embed_error = error
    with pytest.raises(type(error)):
        await embedder_record.check()
    assert embedder_record._cached is None
    graph.embed_error = None
    assert (await embedder_record.check()).status == embedder_record.OK


async def test_a_graph_that_cannot_answer_propagates_and_is_not_cached(graph):
    graph.read_error = ServiceUnavailable("Couldn't connect to graph:7687")
    with pytest.raises(ServiceUnavailable):
        await embedder_record.check()
    assert embedder_record._cached is None


async def test_an_earlier_verdict_survives_a_failed_force(graph):
    """The check that failed said nothing, so what was known stands."""
    graph.declare()
    await embedder_record.check()
    graph.embed_error = httpx.ConnectError("refused")
    with pytest.raises(httpx.ConnectError):
        await embedder_record.check(force=True)
    assert (await embedder_record.check()).status == embedder_record.OK


# ── operator_session ─────────────────────────────────────────────────────────


async def test_operator_session_answers_ok_only_inside_the_block(graph):
    graph.declare(state="migrating")
    with embedder_record.operator_session():
        assert (await embedder_record.check()).status == embedder_record.OK
        assert (await embedder_record.check(force=True)).status == embedder_record.OK
        await embedder_record.require_open()
    assert graph.queries == [], "inside the block nothing is read"
    assert (await embedder_record.check()).code == "v"
    with pytest.raises(EmbedderMismatch):
        await embedder_record.require_open()


async def test_operator_session_ends_on_an_exception_and_leaves_no_open_verdict(graph):
    graph.widths["entity"] = {DIMS: 1}
    with pytest.raises(RuntimeError):
        with embedder_record.operator_session():
            raise RuntimeError("the verification failed")
    assert embedder_record._cached is None
    assert (await embedder_record.check()).code == "i"


def test_nothing_in_the_environment_can_open_the_gate():
    source = Path(embedder_record.__file__).read_text(encoding="utf-8")
    assert "environ" not in source and "getenv" not in source
    assert not [f for f in Settings.model_fields if "operator" in f and "session" in f]
    # The two settings it does read tune the check; neither answers it.
    assert {"embedder_check_ttl_seconds", "embedder_canary_threshold"} <= set(Settings.model_fields)
    assert not [f for f in Settings.model_fields
                if f.startswith("embedder_") and f not in (
                    "embedder_check_ttl_seconds", "embedder_canary_threshold")]


def test_the_defaults_are_the_ones_the_epic_chose():
    assert Settings.model_fields["embedder_check_ttl_seconds"].default == 30.0
    assert Settings.model_fields["embedder_canary_threshold"].default == 0.999


# ── the record ───────────────────────────────────────────────────────────────


async def test_read_record_answers_none_on_no_record(graph):
    assert await embedder_record.read_record() is None


async def test_the_record_round_trips_what_neo4j_cannot_store_natively(graph):
    vectors = [[0.1, 0.2, 0.3, 0.4]] * 3
    written = await embedder_record.update_record(
        canary_vectors=vectors, counters={"entity": 98}, history=["one"], epoch=2,
    )
    stored = graph.record
    assert isinstance(stored["canary_vectors"], str) and isinstance(stored["counters"], str)
    assert stored["history"] == ["one"] and stored["epoch"] == 2
    assert stored["updated_at"]
    assert written["canary_vectors"] == vectors and written["counters"] == {"entity": 98}
    assert (await embedder_record.read_record())["canary_vectors"] == vectors


async def test_update_writes_the_constraint_first_and_merges_on_the_key(graph):
    await embedder_record.update_record(state="migrating")
    constraint, merge = graph.writes()
    assert constraint == (
        "CREATE CONSTRAINT cc_graph_meta_key IF NOT EXISTS "
        "FOR (m:CCGraphMeta) REQUIRE m.key IS UNIQUE"
    )
    assert merge.startswith("MERGE (m:CCGraphMeta {key: 'embedder'})")
    assert "group_id" not in merge, "a group_id would put it in a label-less scan"


async def test_update_is_generic_over_the_field_list_and_refuses_anything_else(graph):
    for field in embedder_record.FIELDS - {"canary_vectors", "counters"}:
        await embedder_record.update_record(**{field: "x"})
        assert graph.record[field] == "x"
    with pytest.raises(ValueError, match="group_id"):
        await embedder_record.update_record(group_id="main")


async def test_update_drops_the_cached_verdict(graph):
    graph.declare()
    assert (await embedder_record.check()).open
    await embedder_record.update_record(state="migrating")
    assert (await embedder_record.check()).code == "v"


async def test_a_none_removes_the_field(graph):
    graph.declare(committed_at="yesterday")
    await embedder_record.update_record(committed_at=None)
    assert "committed_at" not in graph.record


async def test_the_census_counts_by_kind_and_width(graph):
    graph.widths = {"entity": {DIMS: 98, 768: 2}, "edge": {DIMS: 111}, "community": {}}
    assert await embedder_record.census() == {
        "entity": {DIMS: 98, 768: 2}, "edge": {DIMS: 111}, "community": {},
    }


async def test_the_suites_guard_refuses_a_real_record_write():
    """No fake here: `no_live_graph_writes` is what stands in the way."""
    with pytest.raises(AssertionError, match="live knowledge graph"):
        await embedder_record.update_record(state="stable")
    with pytest.raises(AssertionError, match="live knowledge graph"):
        await embedder_record.ensure_constraint()


# ── bootstrap_if_empty ───────────────────────────────────────────────────────


async def test_bootstrap_writes_once_and_is_a_no_op_the_second_time(graph):
    assert await embedder_record.bootstrap_if_empty() is True
    record = await embedder_record.read_record()
    assert record["alias"] == ALIAS and record["dimensions"] == DIMS
    assert record["epoch"] == 1 and record["state"] == "stable"
    assert record["text_recipe"] == TEXT_RECIPE
    assert record["canary_texts"] == list(CANARY_TEXTS)
    assert record["canary_vectors"] == [_vector(t) for t in CANARY_TEXTS]
    assert "CCGraphMeta" in graph.writes()[0], "the constraint first"
    first = dict(graph.record)
    writes = len(graph.writes())

    assert await embedder_record.bootstrap_if_empty() is False
    assert graph.record == first and len(graph.writes()) == writes


async def test_a_bootstrapped_graph_passes_its_own_check(graph):
    assert (await embedder_record.check()).status == embedder_record.ABSENT_EMPTY
    await embedder_record.bootstrap_if_empty()
    assert (await embedder_record.check()).status == embedder_record.OK


async def test_bootstrap_will_not_declare_a_graph_that_holds_vectors(graph):
    graph.widths["edge"] = {DIMS: 1}
    assert await embedder_record.bootstrap_if_empty() is False
    assert graph.record is None and graph.writes() == []


async def test_bootstrap_will_not_touch_a_migration(graph):
    graph.record = {"key": "embedder", "epoch": 0, "state": "migrating"}
    assert await embedder_record.bootstrap_if_empty() is False
    assert graph.writes() == []


async def test_bootstrap_fills_an_interim_record_that_is_not_migrating(graph):
    graph.record = {"key": "embedder", "epoch": 0}
    assert await embedder_record.bootstrap_if_empty() is True
    assert graph.record["alias"] == ALIAS


async def test_the_second_writer_of_a_race_changes_nothing(graph, monkeypatch):
    """Both read an empty graph; the statement itself lets only one through."""
    real_read = embedder_record.read_record

    async def stale_read():
        return None

    await embedder_record.bootstrap_if_empty()
    first = dict(graph.record)
    monkeypatch.setattr(embedder_record, "read_record", stale_read)
    graph.embed = lambda text: [9.0, 9.0, 9.0, 9.0]
    assert await embedder_record.bootstrap_if_empty() is False
    assert graph.record == first
    monkeypatch.setattr(embedder_record, "read_record", real_read)


async def test_bootstrap_refuses_a_width_the_embedder_does_not_produce(graph):
    graph.embed = lambda text: [1.0] * 768
    with pytest.raises(EmbedderMismatch) as refused:
        await embedder_record.bootstrap_if_empty()
    assert refused.value.reason == (
        f"census found width 768 on 3 canary, record says {DIMS}; {REMEDY}"
    )
    assert graph.record is None and graph.writes() == []


async def test_an_embedder_that_is_down_propagates_out_of_bootstrap(graph):
    graph.embed_error = httpx.ConnectError("refused")
    with pytest.raises(httpx.ConnectError):
        await embedder_record.bootstrap_if_empty()
    assert graph.record is None


# ── the exception ────────────────────────────────────────────────────────────


def test_the_mismatch_is_a_dependency_down_on_graph_and_embedder():
    down = EmbedderMismatch(f"graph has vectors but no embedder record; {EMBEDDER_REMEDY}")
    assert isinstance(down, DependencyDown) and not isinstance(down, EmbedderDown)
    assert down.dependencies == ("graph", "embedder")
    assert classify_failure(down) == TRANSIENT
    assert down.reason.endswith(REMEDY) and down.remedy == REMEDY
    assert str(down) == down.reason
    assert down.original is down, "re-raised for a unit's own DOWN path, it still reads DOWN"


# ── the read gate ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("arrange,code", [
    (lambda g: g.widths["entity"].update({DIMS: 1}), "i"),
    (lambda g: g.declare(alias="cc-embedding-v2"), "ii"),
    (lambda g: (g.declare(), g.widths["edge"].update({768: 1})), "iii"),
    (lambda g: (g.declare(canary_vectors=json.dumps([[1.0, 0.0, 0.0, 0.0]] * 3)),
                setattr(g, "embed", _changed_model)), "iv"),
    (lambda g: g.declare(state="migrating"), "v"),
])
async def test_each_code_closes_the_read_with_its_reason(search, arrange, code):
    arrange(search)
    expected = await embedder_record.check()
    assert expected.code == code
    with pytest.raises(EmbedderMismatch) as closed:
        await graphiti.search_facts("contract renewal")
    assert closed.value.reason == expected.reason
    assert search.search_calls == 0, "the search never ran"


@pytest.mark.parametrize("call", [
    lambda: graphiti.search_facts("x"),
    lambda: graphiti.search_nodes("x"),
    lambda: graphiti.search_facts("x", group_ids=["main"]),
])
async def test_every_embedding_read_passes_the_gate(search, monkeypatch, call):
    """search_knowledge_graph, its entity form and search_graph_group all come
    through these two functions."""
    monkeypatch.setattr(graphiti, "node_filters", lambda *a, **k: None)
    search.declare(state="migrating")
    with pytest.raises(EmbedderMismatch):
        await call()
    assert search.search_calls == 0


async def test_an_empty_graph_is_open_to_a_read(search):
    assert await graphiti.search_facts("contract renewal")
    assert search.search_calls == 1


async def test_a_record_that_holds_is_open_to_a_read(search):
    search.declare()
    assert (await graphiti.search_facts("contract renewal"))[0]["fact"] == FACT


async def test_a_gate_that_cannot_answer_raises_the_outage_itself(search):
    search.declare()
    search.embed_error = httpx.ConnectError("refused")
    with pytest.raises(httpx.ConnectError):
        await graphiti.search_facts("contract renewal")
    assert search.search_calls == 0


def test_only_the_search_chokepoint_calls_the_check():
    """Bolt-only reads and `get_status` embed nothing and stay open, and
    `graphiti.py` spells neither the record's writes nor the way past the gate."""
    source = (SRC / "integrations" / "graphiti.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    users = [
        fn.name for fn in ast.walk(tree)
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(isinstance(n, ast.Name) and n.id == "embedder_record" for n in ast.walk(fn))
    ]
    assert users == ["_search"]
    for name in ("operator_session", "update_record", "bootstrap_if_empty"):
        assert name not in source
    assert not [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))
                and "embedder_record" in ast.dump(n)], "the import is function-local"


def test_the_record_module_loads_no_write_path_at_import():
    tree = ast.parse(Path(embedder_record.__file__).read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            dumped = ast.dump(node)
            for banned in ("neo4j_writer", "graphiti_client", "graphiti_ingest"):
                assert banned not in dumped


def test_runtime_never_reaches_the_record_or_the_way_past_the_gate():
    pattern = re.compile(r"embedder_record|operator_session|graph_reembed")
    offenders = [
        str(path) for path in (SRC / "runtime").rglob("*.py")
        if pattern.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


# ── a mismatch parks, it never answers ───────────────────────────────────────


async def test_a_mismatch_on_search_knowledge_graph_parks_as_a_read(search):
    search.widths["entity"] = {DIMS: 1}
    with pytest.raises(CallDeferred) as paused:
        await tools.search_knowledge_graph(_park_ctx(), "contract renewal")
    meta = paused.value.metadata
    assert meta["kind"] == "read"
    assert meta["tool"] == "search_knowledge_graph"
    assert meta["dependencies"] == ["graph", "embedder"]
    assert meta["args"] == {"query": "contract renewal"}
    assert meta["error"] == (
        f"EmbedderMismatch: graph has vectors but no embedder record; {REMEDY}"
    )
    assert search.search_calls == 0


async def test_the_entity_form_and_the_group_search_park_too(search, monkeypatch):
    monkeypatch.setattr(graphiti, "node_filters", lambda *a, **k: None)
    search.declare(state="migrating")
    for call in (
        tools.search_knowledge_graph_entities(_park_ctx(), "contract renewal"),
        tools.search_graph_group(_park_ctx(), "main", "contract renewal"),
    ):
        with pytest.raises(CallDeferred) as paused:
            await call
        assert paused.value.metadata["dependencies"] == ["graph", "embedder"]


async def test_a_run_with_no_landing_tail_gets_the_mismatch_never_the_text(search):
    search.declare(state="migrating")
    with pytest.raises(EmbedderMismatch):
        await tools.search_knowledge_graph(_park_ctx(park=False), "contract renewal")


async def test_the_read_retry_costs_one_check(search):
    """`_read_with_retry` calls the gate twice per read; the TTL is why that is
    cheap."""
    search.declare(canary_vectors=json.dumps([[1.0, 0.0, 0.0, 0.0]] * 3))
    search.embed = _changed_model
    with pytest.raises(CallDeferred):
        await tools.search_knowledge_graph(_park_ctx(), "contract renewal")
    assert len(search.embedded) == 3


@pytest.fixture
def run_pinned(monkeypatch, search):
    monkeypatch.setattr(settings, "test_model", True)
    fake_world(monkeypatch)
    monkeypatch.setattr(settings, "dispatch_approval_limit", 10_000)
    monkeypatch.setattr(settings, "auditor_enabled", False)
    monkeypatch.setattr(routes, "resolve_model", aresolve(lambda *a, **k: _model()))
    return search


@needs_pg
async def test_a_task_run_parks_on_a_mismatch_and_resumes_when_the_record_agrees(
    monkeypatch, run_pinned,
):
    """The whole acceptance: the session parks with the reason, nothing tells the
    agent to go on without the graph, the sweep re-executes the read once the
    record and the configuration agree, and the task lands with the read's real
    result."""
    graph = run_pinned
    graph.widths["entity"] = {DIMS: 1}
    task = await _task()
    out = await routes._run_assigned_task(task)

    assert out["parked"] is True and out["deferred"] is False
    session_id = out["session_id"]
    assert await repo.session_status(session_id) == "AWAITING_RESUME"
    rs = await repo.get_session_run_state(session_id)
    pending = rs["pending_resume"]
    assert pending["kind"] == "read"
    assert pending["tool"] == "search_knowledge_graph"
    assert pending["dependencies"] == ["graph", "embedder"]
    assert f"graph has vectors but no embedder record; {REMEDY}" in pending["last_error"]
    assert DEGRADED not in json.dumps(rs["messages"])
    assert graph.search_calls == 0

    # The operator's `run`, as the record sees it.
    await embedder_record.update_record(
        alias=ALIAS, dimensions=DIMS, epoch=1, state="stable",
        canary_texts=list(CANARY_TEXTS), canary_vectors=[_vector(t) for t in CANARY_TEXTS],
    )
    monkeypatch.setattr(models, "resolve_model", aresolve(lambda *a, **k: _model()))
    await make_parks_due()
    result = await orchestration.retry_sweep(cache=_Probes())
    await orchestration.sweep_settle()
    assert session_id in result["resumed"]

    assert await repo.session_status(session_id) == "DONE"
    rs = await repo.get_session_run_state(session_id)
    returned = [p for m in durable.load_messages(rs) if isinstance(m, ModelRequest)
                for p in m.parts if isinstance(p, ToolReturnPart)
                and p.tool_call_id == "call_graph"]
    assert len(returned) == 1 and FACT in str(returned[0].content)
    assert DEGRADED not in json.dumps(rs["messages"])
    assert (await repo.get_task(task["id"]))["status"] == "DONE"
    assert graph.search_calls == 1
