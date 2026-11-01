"""The operator's offline re-embed (epic-embedder-integrity R4, R5, R6).

The graph is a fake driver and the embedder a fake client at the writer's own seam,
so `no_live_graph_writes` is never in question: every write here lands in `World`.
The real embedder check runs (`embedder_check_live`), because "closed the whole way"
and "open afterwards" are claims about it. Every step goes through `main()`, the
contract the runbook names, with its exit code and its words.
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from neo4j.exceptions import ServiceUnavailable

from central_command import graph_reembed
from central_command.config import Settings, settings
from central_command.contract.embedding_text import CANARY_TEXTS, TEXT_RECIPE, embedding_text
from central_command.db import repo
from central_command.events import log as events_log
from central_command.integrations import (
    embedder_record,
    graphiti,
    litellm,
    neo4j_reader,
    neo4j_writer,
)
from tests.test_embedder_record import _Session

pytestmark = pytest.mark.embedder_check_live

ALIAS = "cc-embedding"
DIMS = 4


def _vector(model: str, text: str, width: int = DIMS) -> list[float]:
    digest = hashlib.sha256(f"{model}|{text}".encode()).digest()
    return [float(b) + 1.0 for b in digest[:width]]


class Killed(BaseException):
    """What a SIGKILL looks like from inside: nothing catches it."""


class _Response:
    def __init__(self, status: int, body: dict):
        self.status_code = status
        self._body = body
        self.text = json.dumps(body)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                str(self.status_code),
                request=httpx.Request("POST", "http://proxy.example.com"),
                response=httpx.Response(self.status_code),
            )

    def json(self):
        return self._body


class World:
    """The graph, the record, the embedder, the proxy, the ledger, the log."""

    def __init__(self, entities: int = 7, communities: int = 1, edges: int = 9):
        self.model = "model-a"
        self.width = DIMS
        self.elements = {"entity": {}, "edge": {}, "community": {}}
        for i in range(entities):
            self.elements["entity"][f"e{i:02}"] = {
                "name": f"Person {i}", "summary": f"Summary {i}", "group_id": "central_command",
                "vector": _vector("old", f"entity {i}"),
            }
        for i in range(communities):
            self.elements["community"][f"c{i:02}"] = {
                "name": f"Community {i}", "vector": _vector("old", f"community {i}"),
            }
        for i in range(edges):
            self.elements["edge"][f"r{i:02}"] = {
                "fact": f"Fact number {i}", "group_id": "central_command",
                "vector": _vector("old", f"edge {i}"),
            }
        self.record: dict | None = None
        self.order: list[str] = []
        self.vector_writes = 0
        self.embed_status = None
        self.counter_status = 200
        self.batches = 0
        self.kill_after_batches = None
        self.batch_hook = None
        self.search_found = True
        self.searches: list[str] = []
        self.ledger_cleared = 0
        self.running_jobs = 0
        self.sleeps: list[float] = []
        self.cache = _Response(200, {"litellm_cache_params": {
            "supported_call_types": "['completion', 'acompletion']",
        }})
        self.read_error = None
        self.events: list[dict] = []

    # the driver -----------------------------------------------------------------

    def session(self, **kw):
        return _Session(self)

    def answer(self, query, **params):
        if self.read_error is not None:
            error, self.read_error = self.read_error[0], self.read_error[1:] or None
            raise error
        return self._answer(query, **params)

    def _answer(self, query, **params):
        if query == embedder_record._CONSTRAINT:
            self.order.append("constraint")
            return []
        if query == embedder_record._UPDATE_RECORD:
            self.order.append("record")
            self.record = {"key": "embedder", **(self.record or {})}
            for name, value in params["fields"].items():
                if value is None:
                    self.record.pop(name, None)
                else:
                    self.record[name] = value
            return [{"m": dict(self.record)}]
        if query == embedder_record._READ_RECORD:
            return [{"m": dict(self.record)}] if self.record else []
        for kind, census in embedder_record._CENSUS.items():
            if query == census:
                widths: dict[int, int] = {}
                for element in self.elements[kind].values():
                    if element.get("vector") is not None:
                        size = len(element["vector"])
                        widths[size] = widths.get(size, 0) + 1
                return [{"width": w, "n": n} for w, n in widths.items()]
        for kind in graph_reembed.KINDS:
            if query == graph_reembed._COUNT[kind]:
                return [{"n": len(self.elements[kind])}]
            if query == graph_reembed._PAGE[kind]:
                uuids = sorted(u for u in self.elements[kind] if u > params["after"])
                return [{"uuid": u, **{k: v for k, v in self.elements[kind][u].items()
                                       if k != "vector"}}
                        for u in uuids[:params["limit"]]]
            if query == graph_reembed._IDENTICAL[kind]:
                by_vector: dict[tuple, set] = {}
                for element in self.elements[kind].values():
                    if element.get("vector") is not None:
                        by_vector.setdefault(tuple(element["vector"]), set()).add(
                            _text(kind, element))
                return [{"texts": len(t)} for t in by_vector.values() if len(t) > 1]
            head = graph_reembed._TARGET[kind][0]
            if query.startswith(head):
                assert "uuid" in params
                if "CALL db.create." in query:
                    procedure = ("setRelationshipVectorProperty" if kind == "edge"
                                 else "setNodeVectorProperty")
                    assert f"db.create.{procedure}" in query, "only through the procedures"
                    assert "$vector" in query
                    self.elements[kind][params["uuid"]]["vector"] = params["vector"]
                    self.vector_writes += 1
                else:
                    assert " = null" in query
                    self.elements[kind][params["uuid"]]["vector"] = None
                self.order.append("vector")
                return []
        if query == graph_reembed._SAMPLE_ENTITIES:
            rows = sorted(
                (u, e) for u, e in self.elements["entity"].items() if e.get("vector") is not None
            )[:params["limit"]]
            return [{"uuid": u, "name": e["name"], "summary": e["summary"],
                     "group_id": e["group_id"], "vector": e["vector"]} for u, e in rows]
        if query == graph_reembed._SAMPLE_FACT:
            rows = sorted(self.elements["edge"].items())[:1]
            return [{"fact": e["fact"], "group_id": e["group_id"]} for _, e in rows]
        raise AssertionError(f"an unexpected statement: {query}")

    # the seeded record ----------------------------------------------------------

    def declare(self, epoch: int = 3, model: str | None = None, **fields):
        """A stable record of `model`, with every stored vector that model's."""
        model = model or self.model
        for kind, elements in self.elements.items():
            for element in elements.values():
                element["vector"] = _vector(model, _text(kind, element))
        self.record = {
            "key": "embedder", "alias": ALIAS, "dimensions": DIMS, "epoch": epoch,
            "text_recipe": TEXT_RECIPE, "state": "stable", "canary_texts": list(CANARY_TEXTS),
            "canary_vectors": json.dumps([_vector(model, t) for t in CANARY_TEXTS]),
            "upstream_model": f"openai/{model}", "history": ["an earlier run"], **fields,
        }

    # the embedder ---------------------------------------------------------------

    def embed_one(self, text: str) -> list[float]:
        return _vector(self.model, text, self.width)


def _text(kind: str, element: dict) -> str:
    return embedding_text(
        kind, name=element.get("name"), summary=element.get("summary"), fact=element.get("fact"),
    )


class _Client:
    def __init__(self, world: World):
        self.world = world

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, headers=None, json=None):  # noqa: A002
        world = self.world
        if url.endswith("/utils/token_counter"):
            assert not url.endswith("/v1/utils/token_counter")
            return _Response(world.counter_status, {"total_tokens": 1234})
        assert url.endswith("/v1/embeddings"), url
        assert json["model"] == ALIAS
        if world.embed_status is not None:
            return _Response(world.embed_status, {})
        given = json["input"]
        if isinstance(given, str):
            return _Response(200, {"data": [{"index": 0, "embedding": world.embed_one(given)}]})
        world.batches += 1
        if world.kill_after_batches is not None:
            pass_batches = world.batches - world.batches_before_pass
            if pass_batches > world.kill_after_batches:
                raise Killed()
        vectors = [world.embed_one(text) for text in given]
        if world.batch_hook is not None:
            vectors = world.batch_hook(given, vectors)
        # Out of order on purpose: the caller must sort by index.
        rows = [{"index": i, "embedding": v} for i, v in enumerate(vectors)]
        return _Response(200, {"data": list(reversed(rows))})


@pytest.fixture
def world(monkeypatch, tmp_path):
    w = World()
    w.batches_before_pass = 1  # the alignment probe's batch
    monkeypatch.setattr(settings, "embed_alias", ALIAS)
    monkeypatch.setattr(settings, "embed_dim", DIMS)
    monkeypatch.setattr(settings, "embedder_check_ttl_seconds", 30.0)
    monkeypatch.setattr(settings, "embedder_canary_threshold", 0.999)
    monkeypatch.setattr(settings, "graph_reembed_backup_max_age_seconds", 3600.0)
    monkeypatch.setattr(settings, "llm_base_url", "http://proxy.example.com")
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    monkeypatch.setattr(neo4j_writer, "_EMBED_MODEL", ALIAS)
    monkeypatch.setattr(neo4j_reader, "_get_driver", lambda: w)

    async def _write(query, **params):
        return w.answer(query, **params)

    monkeypatch.setattr(neo4j_writer, "_write", _write)
    monkeypatch.setattr(neo4j_writer, "_embedding_client", lambda timeout: _Client(w))

    async def _emit(kind, ref_id=None, payload=None, actor=None):
        w.order.append(f"event:{kind}")
        w.events.append({"kind": kind, "payload": payload, "actor": actor})
        return {}

    monkeypatch.setattr(events_log, "emit", _emit)
    monkeypatch.setattr(graph_reembed.events, "emit", _emit)

    async def _running():
        return w.running_jobs

    async def _clear():
        w.ledger_cleared += 1
        return 2

    async def _close():
        return None

    monkeypatch.setattr(repo, "count_running_ingest_jobs", _running)
    monkeypatch.setattr(repo, "clear_work_item_embeddings", _clear)
    monkeypatch.setattr(repo, "close_pool", _close)

    async def _model_info(op):
        return [{"model_name": ALIAS, "litellm_params": {"model": f"openai/{w.model}"}}]

    async def _call(method, path, *a, **k):
        assert (method, path) == ("GET", "/cache/ping")
        return w.cache

    monkeypatch.setattr(litellm, "_fetch_model_info", _model_info)
    monkeypatch.setattr(litellm, "configured", lambda: True)
    monkeypatch.setattr(litellm, "_call", _call)

    async def _search(query, **kwargs):
        # The real gate, which is closed: only `operator_session` gets past it.
        await embedder_record.require_open()
        w.searches.append(query)
        return [{"uuid": "x"}] if w.search_found else []

    monkeypatch.setattr(graphiti, "search_facts", _search)
    monkeypatch.setattr(graphiti, "search_nodes", _search)

    async def _sleep(seconds):
        w.sleeps.append(seconds)

    monkeypatch.setattr(graph_reembed, "_sleep", _sleep)
    noon = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(graph_reembed, "_localnow", lambda: noon)
    w.backup_dir = tmp_path / "cc-backups"
    w.backup_dir.mkdir()
    _dump(w.backup_dir, 60)
    return w


def _dump(directory, age_seconds: float, name: str | None = None, data: bytes | None = None):
    stamp = (datetime.now(timezone.utc) - timedelta(seconds=age_seconds)).strftime("%Y%m%dT%H%M%SZ")
    path = directory / (name or f"neo4j_{stamp}.dump.gz")
    with gzip.open(path, "wb") as f:
        f.write(os.urandom(4096) if data is None else data)
    return path


def _main(world, *argv) -> int:
    step, *rest = argv
    extra = ["--backup-dir", str(world.backup_dir)] if step in ("check", "run") else []
    return graph_reembed.main([step, *extra, *rest])


def _gate(world) -> embedder_record.Verdict:
    return asyncio.run(embedder_record.check(force=True))


def _vectors_are(world, model: str, width: int = DIMS) -> bool:
    return all(
        element["vector"] == _vector(model, _text(kind, element), width)
        for kind, elements in world.elements.items() for element in elements.values()
    )


# ── the command line ─────────────────────────────────────────────────────────


def test_help_lists_the_three_steps_and_nothing_else(capsys):
    with pytest.raises(SystemExit) as done:
        graph_reembed.main(["--help"])
    assert done.value.code == 0
    out = capsys.readouterr().out
    assert "python -m central_command.graph_reembed" in out
    assert re.search(r"\{status,check,run\}", out)


def test_the_module_docstring_states_the_exit_codes():
    doc = graph_reembed.__doc__
    for code in ("exit 0", "exit 1", "exit 2", "exit 3"):
        assert code in doc


def test_a_step_that_cannot_reach_the_graph_exits_two(world, capsys):
    world.read_error = (ServiceUnavailable("Couldn't connect to graph:7687"),) * 9
    assert _main(world, "status") == 2
    assert "ServiceUnavailable" in capsys.readouterr().err


def test_a_bad_batch_size_is_refused(world, capsys):
    assert _main(world, "run", "--batch", "0") == 2


def test_the_backup_age_setting_defaults_to_an_hour():
    assert Settings.model_fields["graph_reembed_backup_max_age_seconds"].default == 3600.0


# ── status ───────────────────────────────────────────────────────────────────


def test_status_on_an_undeclared_graph_says_absent_and_counts(world, capsys):
    assert _main(world, "status") == 0
    out = capsys.readouterr().out
    assert "record: absent" in out
    assert f"entity: 7 at width {DIMS}" in out
    assert f"edge: 9 at width {DIMS}" in out
    assert f"community: 1 at width {DIMS}" in out


def test_status_names_the_record_without_its_canaries(world, capsys):
    world.declare()
    assert _main(world, "status") == 0
    out = capsys.readouterr().out
    assert f"alias={ALIAS} dimensions={DIMS} epoch=3 state=stable" in out
    assert "canary" not in out


# ── check ────────────────────────────────────────────────────────────────────


def test_a_clear_check_exits_zero_and_changes_nothing_on_the_graph(world, capsys):
    before = json.dumps(world.elements, sort_keys=True)
    assert _main(world, "check") == 0
    out = capsys.readouterr().out
    assert world.order == [], "no record, no vector, no event"
    assert world.record is None
    assert json.dumps(world.elements, sort_keys=True) == before
    assert f"{ALIAS} answers with the app key at width {DIMS} = CC_EMBED_DIM" in out
    assert "upstream model: openai/model-a" in out
    assert "the alignment probe agrees" in out
    assert "the LiteLLM cache excludes embeddings" in out
    assert "passes gzip -t" in out
    assert "7 entity, 9 edge, 1 community, 17 vectors, 1234 tokens" in out


def test_check_writes_its_report_where_it_is_told(world, capsys, tmp_path):
    reports = tmp_path / "reports"
    assert _main(world, "check", "--report-dir", str(reports)) == 0
    files = list(reports.glob("*-check.md"))
    assert len(files) == 1
    text = files[0].read_text()
    assert "17 vectors" in text and "Person 1" not in text


def test_check_reports_default_under_the_backup_dir(world, capsys):
    assert _main(world, "check") == 0
    assert list((world.backup_dir / "reembed").glob("*-check.md"))


@pytest.mark.parametrize("status", [401, 403])
def test_check_on_a_refused_key_exits_three_naming_the_key_fix(world, capsys, status):
    world.embed_status = status
    assert _main(world, "check") == 3
    out = capsys.readouterr().out
    assert f"action: the proxy refused the app key (HTTP {status})" in out
    assert "CC_LLM_API_KEY" in out and ALIAS in out


def test_check_on_a_missing_alias_exits_three_naming_the_registration(world, capsys):
    world.embed_status = 404
    assert _main(world, "check") == 3
    assert f"the proxy refused {ALIAS} (HTTP 404): register the alias" in capsys.readouterr().out


def test_check_with_an_empty_key_exits_three(world, capsys, monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "")
    assert _main(world, "check") == 3
    assert "CC_LLM_API_KEY empty" in capsys.readouterr().out


@pytest.mark.parametrize("status", [500, 503])
def test_check_on_a_down_embedder_could_not_run(world, capsys, status):
    world.embed_status = status
    assert _main(world, "check") == 2


def test_check_on_a_misaligned_probe_exits_one(world, capsys):
    """The 2026-09-01 incident: a miss row received a neighbour's vector."""
    def shifted(texts, vectors):
        return [vectors[0], vectors[0], vectors[2], vectors[3], vectors[4], vectors[5]]

    world.batch_hook = shifted
    assert _main(world, "check") == 1
    out = capsys.readouterr().out
    assert "problem: the alignment probe failed: position 1" in out


def test_check_on_a_short_batch_exits_one(world, capsys):
    world.batch_hook = lambda texts, vectors: vectors[:3]
    assert _main(world, "check") == 1
    assert "answered 3 vectors for 6 texts" in capsys.readouterr().out


def test_check_on_a_cache_that_covers_embeddings_exits_one(world, capsys):
    world.cache = _Response(200, {"litellm_cache_params": {
        "supported_call_types": "['completion', 'embedding', 'aembedding']",
    }})
    assert _main(world, "check") == 1
    assert "the LiteLLM cache covers embeddings" in capsys.readouterr().out


def test_check_with_the_cache_off_is_clear(world, capsys):
    world.cache = _Response(503, {"detail": "Cache not initialized"})
    assert _main(world, "check") == 0
    assert "the LiteLLM cache is off" in capsys.readouterr().out


@pytest.mark.parametrize("cache", [
    _Response(401, {}), _Response(200, {}), _Response(200, {"litellm_cache_params": "text"}),
])
def test_a_cache_config_that_cannot_be_read_exits_three(world, capsys, cache):
    world.cache = cache
    assert _main(world, "check") == 3
    assert "cannot read the LiteLLM cache config" in capsys.readouterr().out


def test_no_admin_key_exits_three_naming_it(world, capsys, monkeypatch):
    monkeypatch.setattr(litellm, "configured", lambda: False)
    assert _main(world, "check") == 3
    assert "CC_LLM_PROXY_ADMIN_KEY" in capsys.readouterr().out


def test_a_width_that_differs_is_reported_and_is_no_refusal(world, capsys):
    world.width = 8
    assert _main(world, "check") == 0
    assert "at width 8, CC_EMBED_DIM is 4: a run will end awaiting_config" in capsys.readouterr().out


def test_an_upstream_model_the_proxy_will_not_name_is_no_refusal(world, capsys, monkeypatch):
    async def _boom(op):
        raise litellm.LiteLLMError("no")

    monkeypatch.setattr(litellm, "_fetch_model_info", _boom)
    assert _main(world, "check") == 0
    assert "upstream model: unknown" in capsys.readouterr().out


def test_tokens_the_proxy_will_not_count_are_marked_estimated(world, capsys):
    world.counter_status = 500
    assert _main(world, "check") == 0
    assert "tokens (estimated)" in capsys.readouterr().out


# ── the backup interlock ─────────────────────────────────────────────────────


def _only(world, age: float, **kw):
    for path in world.backup_dir.glob("*.gz"):
        path.unlink()
    return _dump(world.backup_dir, age, **kw)


def test_a_two_day_old_dump_refuses_with_the_age_and_the_command(world, capsys):
    _only(world, 2 * 86400)
    assert _main(world, "check") == 3
    out = capsys.readouterr().out
    assert "is 2d 0h 0m old, over the limit of 0d 1h 0m" in out
    assert "deploy/k3s/backup.sh" in out and "neo4j-admin database dump" in out


def test_no_dump_at_all_refuses(world, capsys):
    for path in world.backup_dir.glob("*.gz"):
        path.unlink()
    assert _main(world, "check") == 3
    assert "no neo4j_<stamp>.dump.gz in" in capsys.readouterr().out


def test_a_backup_dir_that_is_not_there_refuses(world, capsys, tmp_path):
    assert graph_reembed.main([
        "check", "--backup-dir", str(tmp_path / "nothing"), "--report-dir", str(tmp_path / "r"),
    ]) == 3


def test_a_pre_update_dump_is_not_the_backup(world, capsys):
    for path in world.backup_dir.glob("*.gz"):
        path.unlink()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    _dump(world.backup_dir, 0, name=f"neo4j_pre-update_{stamp}.dump.gz")
    assert _main(world, "check") == 3
    assert "no neo4j_<stamp>.dump.gz in" in capsys.readouterr().out


def test_a_dump_of_1024_bytes_or_less_refuses(world, capsys):
    _only(world, 60, data=b"")
    assert _main(world, "check") == 3
    assert "too small to be a graph" in capsys.readouterr().out


def test_a_corrupt_dump_refuses(world, capsys):
    path = _only(world, 60)
    path.write_bytes(path.read_bytes()[:2000])
    assert _main(world, "check") == 3
    assert "fails gzip -t" in capsys.readouterr().out


def test_the_newest_dump_is_the_one_judged(world, capsys):
    _only(world, 60)
    _dump(world.backup_dir, 7200)
    assert _main(world, "check") == 0


def test_the_age_limit_is_the_setting(world, capsys, monkeypatch):
    _only(world, 7200)
    monkeypatch.setattr(settings, "graph_reembed_backup_max_age_seconds", 10_000.0)
    assert _main(world, "check") == 0


def test_the_backup_dir_defaults_to_the_setting_then_the_home(monkeypatch, tmp_path):
    args = SimpleNamespace(backup_dir=None)
    monkeypatch.setenv("CC_BACKUP_DIR", str(tmp_path))
    assert graph_reembed._backup_dir(args) == tmp_path
    monkeypatch.delenv("CC_BACKUP_DIR")
    assert graph_reembed._backup_dir(args).name == "cc-backups"


@pytest.mark.parametrize("hour,minute,inside", [
    (2, 44, False), (2, 45, True), (3, 0, True), (3, 15, True), (3, 16, False), (12, 0, False),
])
def test_the_backup_window_is_fifteen_minutes_either_side(
    world, capsys, monkeypatch, hour, minute, inside,
):
    now = datetime(2026, 11, 1, hour, minute, tzinfo=timezone.utc)
    monkeypatch.setattr(graph_reembed, "_localnow", lambda: now)
    assert _main(world, "check") == (3 if inside else 0)
    if inside:
        assert "--ignore-backup-window" in capsys.readouterr().out
    assert _main(world, "check", "--ignore-backup-window") == 0


def test_the_window_comes_from_the_timer_and_wraps_midnight(monkeypatch, tmp_path):
    timer = tmp_path / "cc-backup.timer"
    timer.write_text("[Timer]\nOnCalendar=*-*-* 00:05:00\n")
    monkeypatch.setattr(graph_reembed, "TIMER_FILE", timer)
    monkeypatch.setattr(graph_reembed, "_localnow",
                        lambda: datetime(2026, 11, 1, 23, 55, tzinfo=timezone.utc))
    assert graph_reembed._check_window(False).level == graph_reembed.ACTION
    timer.unlink()
    assert graph_reembed._timer_time() == (3, 0)


def test_the_shipped_timer_is_read():
    assert graph_reembed._timer_time() == (3, 0)


# ── run ──────────────────────────────────────────────────────────────────────


def test_run_rewrites_every_vector_writes_the_record_and_ends_stable(world, capsys):
    world.declare(model="model-old")
    assert _main(world, "run") == 0
    out = capsys.readouterr().out
    assert world.vector_writes == 17
    assert _vectors_are(world, "model-a")
    record = asyncio.run(embedder_record.read_record())
    assert record["state"] == "stable"
    assert record["epoch"] == 4 and record["dimensions"] == DIMS and record["alias"] == ALIAS
    assert record["text_recipe"] == TEXT_RECIPE
    assert record["canary_texts"] == list(CANARY_TEXTS)
    assert record["canary_vectors"] == [_vector("model-a", t) for t in CANARY_TEXTS]
    assert record["upstream_model"] == "openai/model-a"
    assert record["committed_at"] and record["run_started_at"]
    assert record["history"][0] == "an earlier run"
    assert "epoch 4" in record["history"][1] and "17 vectors" in record["history"][1]
    assert record["counters"]["entity"] == 7 and record["counters"]["edge"] == 9
    assert "previous" not in record["counters"]
    assert "state=stable" in out
    assert "rewritten: 17 vectors" in out
    assert _gate(world).status == embedder_record.OK


def test_the_event_comes_before_the_first_mutation(world, capsys):
    assert _main(world, "run") == 0
    assert world.order[0] == "event:graph.reembed.run"
    assert world.events[0]["actor"] == "operator"
    assert "vector" in world.order
    assert world.order.index("record") < world.order.index("vector")


def test_a_refused_run_is_on_the_log_and_changes_nothing(world, capsys):
    _only(world, 2 * 86400)
    assert _main(world, "run") == 3
    out = capsys.readouterr().out
    assert "2d 0h 0m old" in out and "run refused: nothing was changed" in out
    assert world.order == ["event:graph.reembed.run"]
    assert world.record is None


def test_an_undeclared_graph_gets_an_interim_record_then_its_first(world, capsys):
    states = []
    real = world._answer

    def spying(query, **params):
        if query == embedder_record._UPDATE_RECORD and "state" in params["fields"]:
            states.append((params["fields"]["state"], params["fields"].get("alias"),
                           params["fields"].get("epoch")))
        return real(query, **params)

    world._answer = spying
    assert _main(world, "run") == 0
    assert states[0] == ("migrating", None, 0)
    assert states[-1][0] == "stable"
    assert world.record["epoch"] == 1


def test_the_gate_is_closed_from_the_first_write_until_the_last(world, capsys):
    """Asked at every vector write, through the real check."""
    verdicts = []
    real = world._answer
    world.declare(model="model-old")

    def spying(query, **params):
        out = real(query, **params)
        if "db.create." in query:
            state = world.record["state"]
            verdicts.append(state)
        return out

    world._answer = spying
    assert _main(world, "run") == 0
    assert set(verdicts) == {"migrating"} and len(verdicts) == 17


def test_it_waits_out_the_ttl_before_touching_a_vector(world, capsys):
    assert _main(world, "run") == 0
    assert world.sleeps[0] == 35.0


def test_it_waits_for_running_jobs_then_goes_on(world, capsys, monkeypatch):
    answers = iter([2, 1, 0])

    async def _running():
        return next(answers)

    monkeypatch.setattr(repo, "count_running_ingest_jobs", _running)
    assert _main(world, "run") == 0
    assert world.sleeps[1:3] == [graph_reembed.JOB_POLL_SECONDS] * 2


def test_a_job_that_never_ends_refuses_with_the_graph_left_closed(world, capsys, monkeypatch):
    world.running_jobs = 1
    world.declare()
    monkeypatch.setattr(graph_reembed, "JOB_WAIT_SECONDS", 0.0)
    assert _main(world, "run") == 3
    assert "still RUNNING" in capsys.readouterr().out
    assert world.record["state"] == "migrating" and world.vector_writes == 0
    assert _gate(world).code == "v"


def test_batches_go_through_a_cursor_never_one_query(world, capsys):
    pages = []
    real = world._answer

    def spying(query, **params):
        if query == graph_reembed._PAGE["edge"]:
            pages.append((params["after"], params["limit"]))
        return real(query, **params)

    world._answer = spying
    assert _main(world, "run", "--batch", "4") == 0
    # The pass's pages; the plan's counting pages read at 500.
    assert [p for p in pages if p[1] == 4] == [("", 4), ("r03", 4), ("r07", 4), ("r08", 4)]


def test_a_batch_sends_as_many_requests_as_it_takes(world, capsys):
    assert _main(world, "run", "--batch", "3") == 0
    # 1 alignment probe; entity 3 pages, edge 3, community 1.
    assert world.batches == 8


def test_identical_vectors_across_texts_abort_the_batch(world, capsys):
    world.declare(model="model-old")

    def collapse(texts, vectors):
        return [vectors[0]] * len(vectors) if len(texts) != 6 else vectors

    world.batch_hook = collapse
    assert _main(world, "run") == 1
    out = capsys.readouterr().out
    assert "identical vectors for different texts in a entity batch" in out
    assert "the batch was not written" in out
    assert world.vector_writes == 0
    assert world.record["state"] == "migrating"
    assert world.record["epoch"] == 3


def test_the_same_text_twice_may_share_a_vector(world, capsys):
    for element in world.elements["edge"].values():
        element["fact"] = "the same fact"
    assert _main(world, "run") == 0


def test_a_wrong_width_mid_pass_fails_the_line(world, capsys):
    def narrow(texts, vectors):
        return [v[:2] for v in vectors] if len(texts) != 6 else vectors

    world.batch_hook = narrow
    assert _main(world, "run") == 1
    assert "the embedder answered width 2, measured 4" in capsys.readouterr().out
    assert world.record["state"] == "migrating"


def test_an_element_with_no_text_is_cleared_and_is_not_a_lost_vector(world, capsys):
    world.elements["community"]["c00"]["name"] = ""
    assert _main(world, "run") == 0
    assert world.elements["community"]["c00"]["vector"] is None
    assert world.record["state"] == "stable"
    assert world.vector_writes == 16


def test_a_database_blip_is_retried_with_backoff(world, capsys):
    real = world._answer
    failed = []

    def blinking(query, **params):
        if query == graph_reembed._PAGE["edge"] and not failed:
            failed.append(1)
            raise ServiceUnavailable("a blip")
        return real(query, **params)

    world._answer = blinking
    assert _main(world, "run") == 0
    assert 1.0 in world.sleeps


def test_a_database_that_stays_down_exits_two_with_the_graph_closed(world, capsys):
    real = world._answer
    world.declare()

    def dead(query, **params):
        if query == graph_reembed._PAGE["community"] and world.record["state"] == "migrating":
            raise ServiceUnavailable("gone")
        return real(query, **params)

    world._answer = dead
    assert _main(world, "run") == 2
    assert world.record["state"] == "migrating"
    assert _gate(world).code == "v"


def test_an_embedder_that_goes_down_mid_pass_exits_two_with_the_graph_closed(world, capsys):
    def down(texts, vectors):
        if len(texts) != 6:
            world.embed_status = 503
        return vectors

    world.batch_hook = down
    world.declare()
    assert _main(world, "run") == 2
    assert world.record["state"] == "migrating"


# ── a kill, and the re-run ───────────────────────────────────────────────────


def test_a_kill_after_batch_one_leaves_it_closed_and_a_re_run_ends_stable(world, capsys):
    world.declare(model="model-old")
    world.kill_after_batches = 1
    with pytest.raises(Killed):
        _main(world, "run", "--batch", "3")
    assert world.record["state"] == "migrating"
    assert world.vector_writes == 3
    assert not _vectors_are(world, "model-a"), "half and half, and nothing can see it"
    assert _gate(world).code == "v"

    world.kill_after_batches = None
    world.vector_writes = 0
    assert _main(world, "run", "--batch", "3") == 0
    assert world.vector_writes == 17
    assert _vectors_are(world, "model-a")
    assert world.record["state"] == "stable"
    assert world.record["epoch"] == 4, "one completed migration, however many tries"
    assert _gate(world).status == embedder_record.OK


def test_a_kill_on_an_undeclared_graph_re_runs_to_its_first_record(world, capsys):
    world.kill_after_batches = 1
    with pytest.raises(Killed):
        _main(world, "run", "--batch", "3")
    assert world.record["state"] == "migrating" and not world.record.get("alias")
    assert _gate(world).code == "v"
    world.kill_after_batches = None
    assert _main(world, "run", "--batch", "3") == 0
    assert world.record["epoch"] == 1 and world.record["alias"] == ALIAS


def test_a_failed_verification_re_runs_without_counting_twice(world, capsys):
    world.declare(model="model-old")
    world.search_found = False
    assert _main(world, "run") == 1
    assert world.record["state"] == "migrating" and world.record["epoch"] == 4
    world.search_found = True
    assert _main(world, "run") == 0
    assert world.record["epoch"] == 4
    assert len(world.record["history"]) == 2, "one line per completed run"


def test_a_re_run_still_compares_with_the_record_from_before_the_migration(world, capsys):
    """The killed run overwrote the record's model. The ledger decision must not
    read its own half-written record and call that the same model."""
    world.declare(model="model-old")
    world.search_found = False
    assert _main(world, "run") == 1
    assert world.ledger_cleared == 0
    world.search_found = True
    assert _main(world, "run") == 0
    assert world.ledger_cleared == 1


# ── the verification ─────────────────────────────────────────────────────────


def test_the_searches_run_under_the_operator_session_and_only_there(world, capsys):
    world.declare(model="model-old")
    assert _main(world, "run") == 0
    assert len(world.searches) == 3
    with pytest.raises(embedder_record.EmbedderMismatch):
        world.record["state"] = "migrating"
        asyncio.run(_search_outside_a_session())


async def _search_outside_a_session():
    embedder_record.clear_cache()
    await graphiti.search_facts("anything")


def test_a_search_that_finds_nothing_fails_its_line_and_stays_closed(world, capsys):
    world.search_found = False
    assert _main(world, "run") == 1
    out = capsys.readouterr().out
    assert "problem: search_facts by a stored fact found nothing" in out
    assert "the record stays migrating" in out
    assert world.record["state"] == "migrating"


def test_a_search_that_raises_fails_its_line(world, capsys, monkeypatch):
    async def _boom(query, **kwargs):
        raise RuntimeError("no")

    monkeypatch.setattr(graphiti, "search_nodes", _boom)
    assert _main(world, "run") == 1
    assert "search_nodes by a stored name raised RuntimeError" in capsys.readouterr().out


def test_the_model_moving_mid_run_fails_the_canaries_and_the_sample(world, capsys):
    real = world.embed_one
    calls = []

    def moving(text):
        # Single calls after the pass: the fresh canaries for the record, then the
        # verification's, which must disagree.
        calls.append(text)
        if world.record and world.record.get("canary_vectors") and world.record["state"] == "migrating":
            return _vector("model-moved", text)
        return real(text)

    world.embed_one = moving
    assert _main(world, "run") == 1
    out = capsys.readouterr().out
    assert "problem: canaries: the alias disagrees with the record" in out
    assert "problem: sample: vector and text disagree" in out
    assert world.record["state"] == "migrating"


def test_a_vector_of_the_wrong_width_left_in_the_graph_fails_the_census(world, capsys):
    real = world._answer

    def stale(query, **params):
        out = real(query, **params)
        if query == embedder_record._CENSUS["edge"]:
            return out + [{"width": 768, "n": 2}]
        return out

    world._answer = stale
    assert _main(world, "run") == 1
    assert "problem: census: 2 edge at width 768, measured 4" in capsys.readouterr().out


def test_an_element_that_lost_its_vector_fails_the_census(world, capsys):
    real = world._answer
    state = {"seen": 0}

    def losing(query, **params):
        out = real(query, **params)
        if query == embedder_record._CENSUS["entity"]:
            state["seen"] += 1
            if world.record and world.record.get("alias"):
                return [{"width": DIMS, "n": 5}]
        return out

    world._answer = losing
    assert _main(world, "run") == 1
    assert "an element lost its vector (entity had 7, now 5)" in capsys.readouterr().out


def test_identical_vectors_left_in_the_graph_fail_their_line(world, capsys):
    real = world._answer

    def twins(query, **params):
        if query == graph_reembed._IDENTICAL["edge"]:
            return [{"texts": 2}]
        return real(query, **params)

    world._answer = twins
    assert _main(world, "run") == 1
    assert "identical vectors: 2 different edge texts share a vector" in capsys.readouterr().out


def test_the_failure_names_every_line_not_only_the_first(world, capsys):
    world.search_found = False
    real = world._answer

    def twins(query, **params):
        if query == graph_reembed._IDENTICAL["edge"]:
            return [{"texts": 2}]
        return real(query, **params)

    world._answer = twins
    assert _main(world, "run") == 1
    out = capsys.readouterr().out
    assert "identical vectors: 2" in out and "found nothing" in out


def test_the_report_holds_counts_and_never_graph_text(world, capsys):
    world.declare(model="model-old", epoch=3)
    assert _main(world, "run") == 0
    reports = [p for p in (world.backup_dir / "reembed").glob("*.md")
               if not p.name.endswith("-check.md")]
    assert len(reports) == 1
    text = reports[0].read_text()
    assert "epoch: 3 -> 4" in text
    assert "openai/model-old -> openai/model-a" in text
    assert f"after: entity: 7 at width {DIMS}" in text
    assert "rewritten: 17 vectors" in text
    for secret in ("Person 1", "Fact number", "Summary 1"):
        assert secret not in text


# ── the ledger's vectors (A13) ───────────────────────────────────────────────


def test_a_changed_model_clears_the_ledgers_vectors(world, capsys):
    world.declare(model="model-old")
    assert _main(world, "run") == 0
    assert world.ledger_cleared == 1
    assert "cleared work_item.embedding on 2 rows" in capsys.readouterr().out


def test_the_same_model_leaves_the_ledgers_vectors(world, capsys):
    world.declare(upstream_model="openai/model-a")
    assert _main(world, "run") == 0
    assert world.ledger_cleared == 0


def test_a_first_use_leaves_the_ledgers_vectors(world, capsys):
    assert _main(world, "run") == 0
    assert world.record["epoch"] == 1 and world.ledger_cleared == 0


def test_a_model_the_proxy_never_named_still_counts_as_changed(world, capsys, monkeypatch):
    """A bootstrap record has no upstream model; the canaries answer instead."""
    world.declare(model="model-old")
    world.record.pop("upstream_model")

    async def _none(op):
        return []

    monkeypatch.setattr(litellm, "_fetch_model_info", _none)
    assert _main(world, "run") == 0
    assert world.ledger_cleared == 1


# ── a width change (R5) ──────────────────────────────────────────────────────


EXIT_3 = (
    "action: the graph is re-embedded at width 8 and CC_EMBED_DIM is 4: set CC_EMBED_DIM=8 "
    "in .env, restart the units (the setting is bound at import), then run: "
    "python -m central_command.graph_reembed run --finish"
)


def test_a_width_change_ends_awaiting_config_and_names_the_key(world, capsys):
    world.declare(model="model-old")
    world.width = 8
    assert _main(world, "run") == 3
    out = capsys.readouterr().out
    assert EXIT_3 in out
    assert world.record["state"] == "awaiting_config"
    assert world.record["dimensions"] == 8 and world.record["epoch"] == 4
    assert _vectors_are(world, "model-a", 8)
    assert world.searches == [], "this process would embed the query at the old width"
    assert "search: skipped" in out
    assert world.ledger_cleared == 0, "not until it opens"
    assert _gate(world).code == "v"


def test_finish_refuses_until_the_settings_match(world, capsys):
    world.declare(model="model-old")
    world.width = 8
    assert _main(world, "run") == 3
    capsys.readouterr()
    assert _main(world, "run", "--finish") == 3
    assert EXIT_3 in capsys.readouterr().out
    assert world.record["state"] == "awaiting_config"


def test_finish_opens_once_the_settings_match_and_clears_the_ledger(world, capsys, monkeypatch):
    world.declare(model="model-old")
    world.width = 8
    assert _main(world, "run") == 3
    monkeypatch.setattr(settings, "embed_dim", 8)
    assert _main(world, "run", "--finish") == 0
    assert world.record["state"] == "stable"
    assert world.record["epoch"] == 4
    assert len(world.searches) == 3
    assert world.ledger_cleared == 1
    assert "previous" not in world.record["counters"]
    assert _gate(world).status == embedder_record.OK


def test_finish_that_fails_a_line_stays_closed(world, capsys, monkeypatch):
    world.declare(model="model-old")
    world.width = 8
    assert _main(world, "run") == 3
    monkeypatch.setattr(settings, "embed_dim", 8)
    world.search_found = False
    assert _main(world, "run", "--finish") == 1
    assert world.record["state"] == "awaiting_config"
    assert world.ledger_cleared == 0


@pytest.mark.parametrize("state", [None, "stable", "migrating"])
def test_finish_with_nothing_to_finish_exits_three(world, capsys, state):
    if state:
        world.declare(state=state)
    assert _main(world, "run", "--finish") == 3
    assert f"nothing to finish: the record's state is {state}" in capsys.readouterr().out
    assert world.vector_writes == 0


def test_finish_is_on_the_log_too(world, capsys):
    assert _main(world, "run", "--finish") == 3
    assert world.events[0]["kind"] == "graph.reembed.run"
    assert world.events[0]["payload"]["finish"] is True


def test_the_cache_ping_response_shape_is_the_vendored_documents():
    """`/cache/ping`'s field, as LiteLLM documents it. A live answer is the
    rehearsal's to check."""
    from pathlib import Path

    doc = Path(graph_reembed.ROOT, "docs/vendor/litellm/docs/proxy/caching.md").read_text()
    assert '"litellm_cache_params"' in doc and '"supported_call_types"' in doc
