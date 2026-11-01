"""A mismatch is a held outage with a remedy (epic-embedder-integrity story 2).

The embedder probe is one embedding of "ping": it reads UP for a model swapped
behind the alias. So the record's check decides after an UP probe, and a
mismatch is the `embedder` dependency DOWN with the check's reason (which ends
with the command to run) as the probe error. The sweep records it even with
nothing parked, never clears it, spends no attempt on parked work, and says
`dependency.up` once when the check turns ok. The graph is `FakeGraph` from
`tests/test_embedder_record.py`; the check is the real one.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from central_command.api import orchestration, outages
from central_command.config import settings
from central_command.contract import EmbedderMismatch
from central_command.db import repo
from central_command.heartbeat import probes
from central_command.integrations import embedder_record
from tests.conftest import needs_pg
from tests.test_embedder_record import ALIAS, DIMS, REMEDY, _vector, graph  # noqa: F401 — a fixture
from tests.test_retry_sweep import (  # noqa: F401 — world_log is an autouse fixture
    _empty_worklists,
    _no_probes,
    _no_touch,
    _Recorder,
    world_log,
)

pytestmark = pytest.mark.embedder_check_live

CHANGED = [0.5, 0.8660254037844386, 0.0, 0.0]


def _changed(graph):
    """The model behind the alias changed: a canary no longer matches."""
    graph.declare(canary_vectors='[[1,0,0,0],[1,0,0,0],[1,0,0,0]]')
    graph.embed = lambda text: CHANGED


def _table(monkeypatch):
    """A `dependency_probe` of its own, so the test reads what was recorded."""
    table: dict[str, dict] = {}

    async def states():
        return {k: dict(v) for k, v in table.items()}

    async def record(name, up, error=None):
        before = table.get(name)
        table[name] = {"name": name, "state": "up" if up else "down", "error": error,
                       "checked_at": datetime.now(timezone.utc),
                       "down_since": (before or {}).get("down_since") if not up and before
                       and before["state"] == "down" else (None if up else datetime.now(timezone.utc))}
        return dict(before) if before else None

    monkeypatch.setattr(repo, "dependency_probe_states", states)
    monkeypatch.setattr(repo, "record_dependency_probe", record)
    return table


# --- probe_and_record ------------------------------------------------------------


async def test_a_mismatch_with_the_probe_up_records_the_embedder_down_with_the_reason(
        monkeypatch, graph):
    _changed(graph)
    rec = _Recorder(monkeypatch)
    _no_probes(monkeypatch, embedder=True, graph=True)
    _empty_worklists(monkeypatch)
    table = _table(monkeypatch)

    cache, changes = await orchestration.probe_and_record(["embedder", "graph"])

    assert cache.results == {"embedder": False, "graph": True}
    assert changes == [{"dependency": "embedder", "state": "down"}]
    row = table["embedder"]
    assert row["state"] == "down"
    assert "has changed since the graph was embedded" in row["error"]
    assert row["error"].endswith(REMEDY)
    assert rec.kinds == ["dependency.down"]


async def test_an_agreeing_graph_records_nothing_and_asks_afresh(monkeypatch, graph):
    graph.declare()
    asked = []
    real = embedder_record.check

    async def counted(force=False):
        asked.append(force)
        return await real(force=force)

    monkeypatch.setattr(embedder_record, "check", counted)
    _no_probes(monkeypatch, embedder=True)
    _empty_worklists(monkeypatch)
    table = _table(monkeypatch)

    cache, changes = await orchestration.probe_and_record(["embedder"])

    assert cache.results == {"embedder": True} and changes == [] and table == {}
    assert asked == [True]


async def test_a_probe_that_is_down_is_not_overridden_and_the_check_is_not_asked(
        monkeypatch, graph):
    async def never(force=False):
        raise AssertionError("the check must not run on a DOWN embedder probe")

    monkeypatch.setattr(embedder_record, "check", never)
    _no_probes(monkeypatch, embedder=False)
    _empty_worklists(monkeypatch)
    table = _table(monkeypatch)

    cache, _ = await orchestration.probe_and_record(["embedder"])

    assert cache.results == {"embedder": False} and table["embedder"]["error"] == (
        "probe answered unhealthy")


async def test_a_check_that_cannot_answer_is_the_outage_it_already_is(monkeypatch, graph):
    graph.declare()
    graph.embed_error = ConnectionError("embedder refused")
    _no_probes(monkeypatch, embedder=True)
    _empty_worklists(monkeypatch)
    table = _table(monkeypatch)

    cache, changes = await orchestration.probe_and_record(["embedder"])

    assert cache.results == {"embedder": False}
    assert changes == [{"dependency": "embedder", "state": "down"}]
    assert table["embedder"]["error"] == "embedder check could not run (ConnectionError)"


async def test_with_the_graph_probed_down_a_cached_mismatch_is_held_not_cleared(
        monkeypatch, graph):
    """The check cannot run without the graph; the plain UP answer of the embedder
    probe must not clear what the check found."""
    _changed(graph)
    assert not (await embedder_record.check()).open        # cached
    _no_probes(monkeypatch, embedder=True, graph=False)
    _empty_worklists(monkeypatch)
    table = _table(monkeypatch)
    graph.read_error = ConnectionError("bolt refused")     # the check would raise

    cache, _ = await orchestration.probe_and_record(["embedder", "graph"])

    assert cache.results == {"embedder": False, "graph": False}
    assert table["embedder"]["error"].endswith(REMEDY)


async def test_a_sweep_and_its_retry_now_consult_the_check_once_per_cache(monkeypatch, graph):
    graph.declare()
    asked = []
    real = embedder_record.check

    async def counted(force=False):
        asked.append(force)
        return await real(force=force)

    monkeypatch.setattr(embedder_record, "check", counted)
    _no_probes(monkeypatch, embedder=True)
    _empty_worklists(monkeypatch)
    _table(monkeypatch)

    cache, _ = await orchestration.probe_and_record(["embedder"])
    await orchestration.probe_and_record(["embedder"], cache)

    assert asked == [True]


# --- the sweep ---------------------------------------------------------------------


async def test_with_nothing_parked_a_cached_mismatch_still_records_the_outage(
        monkeypatch, graph):
    _changed(graph)
    rec = _Recorder(monkeypatch)
    _no_touch(monkeypatch, rec)
    _no_probes(monkeypatch, embedder=True, graph=True)
    _empty_worklists(monkeypatch)
    table = _table(monkeypatch)

    quiet = await orchestration.retry_sweep()
    assert "probes" not in quiet and table == {}, "no cached verdict: no probe, as before"

    assert not (await embedder_record.check()).open        # what a gate hit or the worker tick does
    result = await orchestration.retry_sweep()

    assert result["probes"] == {"embedder": False}
    assert table["embedder"]["state"] == "down" and table["embedder"]["error"].endswith(REMEDY)
    assert rec.kinds == ["dependency.down"]


async def test_the_sweep_never_clears_a_recorded_mismatch_and_says_up_once_when_it_ends(
        monkeypatch, graph):
    _changed(graph)
    rec = _Recorder(monkeypatch)
    _no_probes(monkeypatch, embedder=True, graph=True)
    _empty_worklists(monkeypatch)
    table = _table(monkeypatch)
    await orchestration.probe_and_record(["embedder", "graph"])
    assert rec.kinds == ["dependency.down"]

    for _ in range(3):                                  # recorded down: re-probed every tick
        out = await orchestration.retry_sweep()
        assert out["probes"] == {"embedder": False}
        assert "dependency_changes" not in out
    assert table["embedder"]["state"] == "down" and rec.kinds == ["dependency.down"]

    graph.declare()                                     # the operator's run: record agrees
    graph.embed = _vector
    embedder_record.clear_cache()
    out = await orchestration.retry_sweep()

    assert out["dependency_changes"] == [{"dependency": "embedder", "state": "up"}]
    assert table["embedder"]["state"] == "up"
    assert rec.kinds == ["dependency.down", "dependency.up"]
    await orchestration.retry_sweep()
    assert rec.kinds == ["dependency.down", "dependency.up"], "up is announced once"


async def test_parked_work_waits_on_the_probe_gated_skip_with_no_driver_call(
        monkeypatch, graph):
    _changed(graph)
    rec = _Recorder(monkeypatch)
    _no_touch(monkeypatch, rec)
    _no_probes(monkeypatch, embedder=True, graph=True, llm=True)
    _empty_worklists(
        monkeypatch,
        tasks=[{"id": "task_held", "retry_state": {"dependencies": ["graph", "embedder"]}}],
    )
    _table(monkeypatch)

    async def must_not_run(*a, **k):
        raise AssertionError("a task parked on a mismatch reached its driver")

    monkeypatch.setattr(orchestration, "retry_parked_task", must_not_run)

    out = await orchestration.retry_sweep()

    assert out["retried_tasks"] == []
    assert [(s["id"], s["down"]) for s in out["skipped_gated"]] == [
        ("task_held", ["embedder"])]
    assert out["probes"]["embedder"] is False


# --- the outage surface ------------------------------------------------------------


@needs_pg
async def test_a_task_parked_on_a_mismatch_spends_no_attempt_and_is_never_loud(
        monkeypatch, graph):
    """On the real tables: three parks on a mismatch (the attempts a probe-less
    sweep would have kept spending), a sweep over a mismatched graph, and the
    unit stays at three attempts, is not `failing_despite_probe`, and the outage
    banner reads the reason. Then the record agrees and `dependency.up` follows
    exactly once."""
    task_id = "task_" + uuid.uuid4().hex[:12]
    await repo.create_task(task_id, "mismatch park", "x", "jira-expert")
    mismatch = EmbedderMismatch(f"configured a/4, graph holds b/8; {REMEDY}")
    for attempt in (1, 2, 3):
        assert await orchestration.park_task_retry(task_id, mismatch, attempts=attempt)
    graph.declare()
    _changed(graph)
    await repo.record_dependency_probe("embedder", True)
    try:
        out = await orchestration.retry_sweep(ignore_not_before=True)
        await orchestration.retry_sweep(ignore_not_before=True)

        assert out["probes"]["embedder"] is False
        assert task_id in [s["id"] for s in out["skipped_gated"]]
        assert "failing_despite_probe" not in out
        state = (await repo.get_task(task_id))["retry_state"]
        assert state["attempts"] == 3, "no attempt is spent while the dependency is down"
        failing = await repo.units_failing_despite_probe(settings.retry_loud_after_attempts)
        assert task_id not in [u["id"] for u in failing]
        loud = [e for e in await repo.list_events(ref_id=task_id, limit=100)
                if e["kind"] == "retry.failing_despite_probe"]
        assert loud == []

        dep = {d["name"]: d for d in (await outages.list_outages())["dependencies"]}["embedder"]
        assert dep["state"] == "down" and dep["error"].endswith(REMEDY)
        assert dep["failing"] == [] and dep["total"] >= 1

        ups_before = len([e for e in await repo.list_events(ref_id="embedder", limit=100)
                          if e["kind"] == "dependency.up"])
        graph.declare()
        embedder_record.clear_cache()
        graph.embed = _vector
        await orchestration.retry_sweep(ignore_not_before=True)
        await orchestration.sweep_settle()
        ups = [e for e in await repo.list_events(ref_id="embedder", limit=100)
               if e["kind"] == "dependency.up"]
        assert len(ups) == ups_before + 1
    finally:
        await repo.record_dependency_probe("embedder", True)


async def test_retry_now_runs_the_same_path_and_stays_down_on_a_mismatch(monkeypatch, graph):
    _changed(graph)
    _no_probes(monkeypatch, embedder=True)
    _empty_worklists(monkeypatch)
    table = _table(monkeypatch)
    emitted = _Recorder(monkeypatch)

    out = await outages.retry_outage("embedder")

    assert out["probe"] == "down" and sum(out["driven"].values()) == 0
    assert table["embedder"]["error"].endswith(REMEDY)
    assert "outage.retry_requested" in emitted.kinds and "dependency.down" in emitted.kinds


async def test_the_outages_list_carries_the_error_only_while_down(monkeypatch):
    now = datetime.now(timezone.utc)

    async def states():
        return {
            "embedder": {"name": "embedder", "state": "down", "checked_at": now,
                         "down_since": now, "error": f"mismatch; {REMEDY}"},
            "jira": {"name": "jira", "state": "up", "checked_at": now, "down_since": None,
                     "error": "stale text"},
            "graph": {"name": "graph", "state": "down", "checked_at": now,
                      "down_since": now, "error": None},
        }

    async def counts():
        return {}

    async def failing(n):
        return []

    monkeypatch.setattr(repo, "dependency_probe_states", states)
    monkeypatch.setattr(repo, "parked_unit_counts", counts)
    monkeypatch.setattr(repo, "units_failing_despite_probe", failing)

    rows = {d["name"]: d for d in (await outages.list_outages())["dependencies"]}

    assert rows["embedder"]["error"] == f"mismatch; {REMEDY}"
    assert "error" not in rows["jira"] and "error" not in rows["graph"]


async def test_an_unanswerable_check_records_its_kind_never_its_text(monkeypatch, graph):
    graph.declare()
    graph.embed_error = ConnectionError("refused at http://user:hunter2@10.0.0.9:4000/v1")
    _no_probes(monkeypatch, embedder=True)
    _empty_worklists(monkeypatch)
    table = _table(monkeypatch)

    await orchestration.probe_and_record(["embedder"])

    assert table["embedder"]["error"] == "embedder check could not run (ConnectionError)"
    assert "hunter2" not in table["embedder"]["error"]


async def test_the_outages_list_scrubs_userinfo_and_key_shapes_from_a_probe_error(monkeypatch):
    now = datetime.now(timezone.utc)
    dirty = ("connect to http://op:hunter2@10.0.0.9:4000/x failed; "
             "Authorization key=sk-abcdef123456 token: abc123")

    async def states():
        return {"graph": {"name": "graph", "state": "down", "checked_at": now,
                          "down_since": now, "error": dirty}}

    async def counts():
        return {}

    async def failing(n):
        return []

    monkeypatch.setattr(repo, "dependency_probe_states", states)
    monkeypatch.setattr(repo, "parked_unit_counts", counts)
    monkeypatch.setattr(repo, "units_failing_despite_probe", failing)

    error = {d["name"]: d for d in (await outages.list_outages())["dependencies"]}["graph"]["error"]

    assert "hunter2" not in error and "sk-abcdef" not in error and "abc123" not in error
    assert "http://10.0.0.9:4000/x" in error


@pytest.mark.parametrize("text,gone", [
    ("POST http://op:p@ss:w0rd@10.0.0.9:4000/v1 failed", "w0rd"),     # an @ in the password
    ("Authorization: Bearer abcDEF123456 rejected", "abcDEF123456"),
    ("bad request api_key=zzTOP-secret-1 sent", "zzTOP-secret-1"),
    ('{"x_api_token": "tok-777"}', "tok-777"),
    ("rejected sk-proj_abc.def-123456", "def-123456"),
])
def test_the_scrub_covers_the_shapes_a_probe_error_can_carry(text, gone):
    assert gone not in outages._safe_error(text)


def test_the_probe_tier_stays_pure():
    """`heartbeat/probes.py` cannot import the record (its tier note); the
    orchestration reads the check and hands the cache the answer."""
    import ast
    from pathlib import Path

    tree = ast.parse(Path(probes.__file__).read_text())
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    imported |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not [m for m in imported if "integrations" in m or "embedder_record" in m], imported
    assert ALIAS and DIMS
