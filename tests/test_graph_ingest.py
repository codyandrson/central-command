"""The durable graph ingest queue (design record 2026-10-04, D5).

The properties: the Executor's enqueue writes the verification row and the job
in ONE transaction; jobs in a group run strictly in order while different
groups run side by side; a transient failure re-queues with backoff and a
permanent one fails the job AND parks its verification row for the operator,
on the record; a RUNNING job left by a dead process is settled by its marker
at the next start (DONE if the episode landed, QUEUED if not); the worker
starts no extraction without the carried patches, nor while an update hold
stands.

Nothing here extracts for real: `graphiti_ingest._client` is replaced with a
fake, and conftest's `no_live_graph_writes` refuses a real client's writes
regardless. The queue table is shared by the whole suite, so `isolated_jobs`
parks every open job this test did not create, the way conftest's
`isolated_queue` parks work items.
"""

from __future__ import annotations

import asyncio
import json
import uuid as _uuid
from types import SimpleNamespace

import asyncpg
import httpx
import pytest

from central_command.db import repo
from central_command.integrations import graph_ontology, graphiti_ingest, neo4j_reader
from central_command.runtime import hold
from tests.conftest import needs_pg

pytestmark = needs_pg

PARKED = "PARKED_FOR_TEST"


@pytest.fixture(autouse=True)
async def isolated_jobs():
    """Hide every open job some other test left, then give them back; delete
    this test's own jobs afterwards."""
    conn = await repo._conn()
    try:
        before = await conn.fetchval("select coalesce(max(id), 0) from graph_ingest_job")
        await conn.execute(
            f"update graph_ingest_job set status = '{PARKED}' "
            "where status in ('QUEUED', 'RUNNING')"
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


class FakeGraphiti:
    """The two methods the worker calls. `gates[name]` holds an extraction
    open until the test releases it; `fail[name]` raises instead."""

    def __init__(self):
        self.calls: list[dict] = []
        self.gates: dict[str, asyncio.Event] = {}
        self.fail: dict[str, BaseException] = {}
        self.indices = 0

    async def build_indices_and_constraints(self):
        self.indices += 1

    async def add_episode(self, **kw):
        self.calls.append(kw)
        gate = self.gates.get(kw["name"])
        if gate is not None:
            await gate.wait()
        if kw["name"] in self.fail:
            raise self.fail[kw["name"]]
        return SimpleNamespace(
            episode=SimpleNamespace(uuid=f"ep-{kw['name']}"),
            nodes=[SimpleNamespace(uuid=f"n-{kw['name']}")],
            edges=[SimpleNamespace(uuid=f"e-{kw['name']}")],
        )


@pytest.fixture
def fake(monkeypatch):
    g = FakeGraphiti()
    monkeypatch.setattr(graphiti_ingest, "_client", lambda: g)
    monkeypatch.setattr(graphiti_ingest, "_patches_ok", lambda: True)
    monkeypatch.setattr(graphiti_ingest, "_text_source", lambda: "text")
    monkeypatch.setattr(hold, "active", False)
    return g


def _group() -> str:
    return f"gi-{_uuid.uuid4().hex[:8]}"


async def _enqueue(name: str, group: str) -> dict:
    pid = f"p-{_uuid.uuid4().hex[:10]}"
    marker = f"proposal={pid}"
    return await graphiti_ingest.enqueue(
        name, f"{name} body", f"src | trust=human-approved | approver=human:doe | {marker}",
        group, reference_time="2026-01-01T00:00:00Z", proposal_id=pid, scope="shared",
        marker=marker,
    )


async def _job(job_id: int) -> dict:
    return await repo.get_ingest_job(job_id)


async def _events(ref_id: str) -> list[str]:
    return [e["kind"] for e in await repo.list_events(ref_id=ref_id)]


# --- the Systems page's view of the queue -----------------------------------------


async def test_the_queue_summary_counts_open_work_and_names_the_retry_reason(fake):
    g = _group()
    before = await repo.ingest_queue_summary()
    a = await _enqueue("sum-a", g)
    b = await _enqueue("sum-b", _group())
    c = await _enqueue("sum-c", _group())
    quiet = await repo.ingest_queue_summary()
    assert quiet["QUEUED"] == before["QUEUED"] + 3
    # Never attempted: no retry reason to show.
    assert quiet["retry_error"] is None

    # b: attempted, re-queued with a reason (a transient failure). c: failed.
    conn = await repo._conn()
    try:
        await conn.execute(
            "update graph_ingest_job set status = 'QUEUED', attempts = 4, started_at = now(), "
            "last_error = $2 where id = $1", b["job"]["id"], "Error code: 403 - key not allowed",
        )
        await conn.execute(
            "update graph_ingest_job set status = 'FAILED', attempts = 1, last_error = 'boom' "
            "where id = $1", c["job"]["id"],
        )
    finally:
        await conn.close()
    after = await repo.ingest_queue_summary()
    assert after["QUEUED"] == before["QUEUED"] + 2 and after["FAILED"] == before["FAILED"] + 1
    assert after["RUNNING"] == before["RUNNING"]
    assert after["retry_error"] == "Error code: 403 - key not allowed"
    assert a["job"]["id"]  # (a stays QUEUED, attempts 0: not a retry)


# --- the Executor's side: one transaction ---------------------------------------


async def test_enqueue_writes_the_verification_row_and_the_job_together():
    group = _group()
    out = await _enqueue("alpha", group)
    ver, job = out["verification"], out["job"]
    assert ver["status"] == "PENDING" and ver["group_id"] == group
    assert job["status"] == "QUEUED" and job["kind"] == "add_episode"
    assert job["verification_id"] == ver["id"]
    assert job["payload"] == {
        "name": "alpha", "episode_body": "alpha body",
        "source_description": f"src | trust=human-approved | approver=human:doe | {ver['marker']}",
        "reference_time": "2026-01-01T00:00:00Z",
    }


async def test_a_failed_job_insert_leaves_no_verification_row():
    pid = f"p-{_uuid.uuid4().hex[:10]}"
    with pytest.raises(TypeError):
        await repo.create_graph_verification_with_job(
            proposal_id=pid, episode_name="x", group_id=_group(), scope="shared",
            marker=f"proposal={pid}", payload={"unserialisable": object()},
        )
    assert await repo.graph_verification_for_proposal(pid) is None


async def test_the_database_refuses_two_running_jobs_in_one_group():
    group = _group()
    a = (await _enqueue("a", group))["job"]
    b = (await _enqueue("b", group))["job"]
    conn = await repo._conn()
    try:
        await conn.execute("update graph_ingest_job set status = 'RUNNING' where id = $1", a["id"])
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(
                "update graph_ingest_job set status = 'RUNNING' where id = $1", b["id"])
    finally:
        await conn.close()


# --- ordering ------------------------------------------------------------------


async def test_one_group_runs_in_order_and_groups_run_side_by_side(fake):
    g1, g2 = _group(), _group()
    a1 = (await _enqueue("a1", g1))["job"]
    a2 = (await _enqueue("a2", g1))["job"]
    b1 = (await _enqueue("b1", g2))["job"]
    for name in ("a1", "a2", "b1"):
        fake.gates[name] = asyncio.Event()
    w = graphiti_ingest.IngestWorker()

    claimed = await w.tick()
    # The head of each group, at once — never a2 behind a1.
    assert sorted(claimed) == sorted([a1["id"], b1["id"]])
    await asyncio.sleep(0)
    assert {c["name"] for c in fake.calls} == {"a1", "b1"}  # concurrently in flight
    assert await w.tick() == []  # both groups busy; a2 waits

    fake.gates["b1"].set()
    await asyncio.sleep(0.05)
    assert (await _job(b1["id"]))["status"] == "DONE"
    assert await w.tick() == []  # g1 still busy with a1

    fake.gates["a1"].set()
    await asyncio.sleep(0.05)
    assert await w.tick() == [a2["id"]]
    fake.gates["a2"].set()
    await w.settle()
    assert [c["name"] for c in fake.calls] == ["a1", "b1", "a2"]
    done = await _job(a2["id"])
    assert done["status"] == "DONE" and done["episode_uuid"] == "ep-a2"
    assert done["result"] == {"nodes": ["n-a2"], "edges": ["e-a2"]}


async def test_the_worker_passes_the_ontology_text_source_and_the_instant(fake):
    group = _group()
    job = (await _enqueue("onto", group))["job"]
    w = graphiti_ingest.IngestWorker()
    await w.tick()
    await w.settle()
    (call,) = fake.calls
    assert call["entity_types"] is graph_ontology.ENTITY_TYPES
    assert call["source"] == "text" and call["group_id"] == group
    assert call["reference_time"].isoformat() == "2026-01-01T00:00:00+00:00"
    assert call["source_description"] == job["payload"]["source_description"]
    assert "uuid" not in call  # a new episode's uuid is never passed (it LOADS)


async def test_a_backed_off_head_holds_its_whole_group(fake):
    group = _group()
    head = (await _enqueue("h", group))["job"]
    (await _enqueue("t", group))
    conn = await repo._conn()
    try:
        await conn.execute(
            "update graph_ingest_job set not_before = now() + interval '1 hour' where id = $1",
            head["id"])
    finally:
        await conn.close()
    assert await graphiti_ingest.IngestWorker().tick() == []


# --- classification ---------------------------------------------------------------


def _openai_connection_error():
    import openai

    return openai.APIConnectionError(request=httpx.Request("POST", "http://proxy.example.com"))


def _neo4j_down():
    from neo4j.exceptions import ServiceUnavailable

    return ServiceUnavailable("Couldn't connect to bolt")


def _openai_503():
    import openai

    req = httpx.Request("POST", "http://proxy.example.com")
    return openai.InternalServerError("busy", response=httpx.Response(503, request=req), body=None)


@pytest.mark.parametrize("make", [_openai_connection_error, _neo4j_down, _openai_503,
                                  lambda: asyncio.TimeoutError()])
async def test_a_transient_failure_requeues_with_backoff(fake, make):
    group = _group()
    out = await _enqueue("tr", group)
    fake.fail["tr"] = make()
    (job,) = await repo.claim_ingest_jobs()
    assert await graphiti_ingest.run_job(job) == "deferred"
    landed = await _job(job["id"])
    assert landed["status"] == "QUEUED"
    assert landed["not_before"] is not None and landed["attempts"] == 1
    assert landed["last_error"]
    ver = await repo.get_graph_verification(out["verification"]["id"])
    assert ver["status"] == "PENDING"  # nothing parked for a transient
    assert await _events(ver["id"]) == ["graph.ingest.deferred"]


async def test_only_the_first_transient_failure_is_an_event(fake):
    out = await _enqueue("again", _group())
    fake.fail["again"] = _neo4j_down()
    for _ in range(2):
        conn = await repo._conn()
        try:
            await conn.execute("update graph_ingest_job set not_before = null where id = $1",
                               out["job"]["id"])
        finally:
            await conn.close()
        (job,) = await repo.claim_ingest_jobs()
        assert await graphiti_ingest.run_job(job) == "deferred"
    assert (await _job(out["job"]["id"]))["attempts"] == 2
    assert await _events(out["verification"]["id"]) == ["graph.ingest.deferred"]


def _truncated():
    return json.JSONDecodeError("Unterminated string starting at", '{"x": "', 6)


def _schema_mismatch():
    from pydantic import BaseModel

    class M(BaseModel):
        n: int

    try:
        M(n="nope")
    except Exception as e:  # noqa: BLE001
        return e


def _node_not_found():
    from central_command.integrations import graphiti_client

    graphiti_client._prepare_environment()
    from graphiti_core.errors import NodeNotFoundError

    return NodeNotFoundError("ep-x")


@pytest.mark.parametrize("make", [_truncated, _schema_mismatch, _node_not_found])
async def test_a_permanent_failure_fails_the_job_and_parks_its_row(fake, make):
    out = await _enqueue("perm", _group())
    fake.fail["perm"] = make()
    (job,) = await repo.claim_ingest_jobs()
    assert await graphiti_ingest.run_job(job) == "failed"
    landed = await _job(job["id"])
    assert landed["status"] == "FAILED" and landed["finished_at"] is not None
    ver = await repo.get_graph_verification(out["verification"]["id"])
    assert ver["status"] == "AWAITING_OPERATOR"
    assert ver["mechanical"]["ingest_failed"] is True
    assert type(fake.fail["perm"]).__name__ in ver["mechanical"]["error"]
    # The failure on the log BEFORE the park it causes.
    assert await _events(ver["id"]) == ["graph.ingest.failed", "graph.verification.parked"]


async def test_an_invalid_group_id_fails_without_calling_the_library(fake):
    out = await _enqueue("bad", "central_command:colon")
    (job,) = await repo.claim_ingest_jobs()
    assert await graphiti_ingest.run_job(job) == "failed"
    assert fake.calls == []
    ver = await repo.get_graph_verification(out["verification"]["id"])
    assert "InvalidGroupId" in ver["mechanical"]["error"]


async def test_a_failed_job_never_blocks_its_group(fake):
    group = _group()
    await _enqueue("first", group)
    second = (await _enqueue("second", group))["job"]
    fake.fail["first"] = _truncated()
    w = graphiti_ingest.IngestWorker()
    await w.tick()
    await w.settle()
    assert await w.tick() == [second["id"]]
    await w.settle()
    assert (await _job(second["id"]))["status"] == "DONE"


# --- crash recovery ----------------------------------------------------------------


async def _orphan(monkeypatch, *, landed: bool) -> dict:
    out = await _enqueue("orphan", _group())
    (job,) = await repo.claim_ingest_jobs()  # RUNNING, then the process "died"
    seen = []

    async def by_marker(marker, group_id):
        seen.append((marker, group_id))
        return {"uuid": "ep-landed"} if landed else None

    monkeypatch.setattr(neo4j_reader, "episode_by_marker", by_marker)
    return {"job": job, "out": out, "seen": seen}


async def test_recovery_marks_a_landed_orphan_done(monkeypatch):
    o = await _orphan(monkeypatch, landed=True)
    result = await graphiti_ingest.recover()
    assert result == {"done": [o["job"]["id"]], "requeued": []}
    assert o["seen"] == [(o["out"]["verification"]["marker"], o["job"]["group_id"])]
    landed = await _job(o["job"]["id"])
    assert landed["status"] == "DONE" and landed["episode_uuid"] == "ep-landed"


async def test_recovery_requeues_an_orphan_that_never_landed(monkeypatch):
    o = await _orphan(monkeypatch, landed=False)
    result = await graphiti_ingest.recover()
    assert result == {"done": [], "requeued": [o["job"]["id"]]}
    assert (await _job(o["job"]["id"]))["status"] == "QUEUED"


async def test_recovery_waits_when_neo4j_cannot_answer(monkeypatch, fake):
    """Deciding blind would lose an episode or extract it twice: the job stays
    RUNNING and the worker claims nothing until recovery succeeds."""
    o = await _orphan(monkeypatch, landed=False)

    async def down(marker, group_id):
        raise _neo4j_down()

    monkeypatch.setattr(neo4j_reader, "episode_by_marker", down)
    w = graphiti_ingest.IngestWorker()
    assert await w.tick() == []
    assert w.state == "recovering"
    assert (await _job(o["job"]["id"]))["status"] == "RUNNING"


async def test_a_shutdown_mid_extraction_is_recovered_at_the_next_start(monkeypatch, fake):
    monkeypatch.setattr(graphiti_ingest, "POLL_SECONDS", 0.02)
    job = (await _enqueue("slow", _group()))["job"]
    fake.gates["slow"] = asyncio.Event()  # never released
    w = graphiti_ingest.IngestWorker()
    await w.start()
    for _ in range(100):
        if fake.calls:
            break
        await asyncio.sleep(0.02)
    assert fake.calls
    await w.stop()
    assert (await _job(job["id"]))["status"] == "RUNNING"  # left for the marker

    async def absent(marker, group_id):
        return None

    monkeypatch.setattr(neo4j_reader, "episode_by_marker", absent)
    fake.gates["slow"].set()
    w2 = graphiti_ingest.IngestWorker()  # the lease was released: a new one starts
    await w2.start()
    for _ in range(200):
        if (await _job(job["id"]))["status"] == "DONE":
            break
        await asyncio.sleep(0.02)
    await w2.stop()
    assert (await _job(job["id"]))["status"] == "DONE"
    assert len(fake.calls) == 2


async def test_only_one_worker_holds_the_lease(fake):
    a, b = graphiti_ingest.IngestWorker(), graphiti_ingest.IngestWorker()
    await a.start()
    try:
        await b.start()
        assert not b.running and "lease" in b.state
    finally:
        await a.stop()
        await b.stop()


# --- the gates ------------------------------------------------------------------------


async def test_without_the_carried_patches_nothing_is_extracted(monkeypatch, fake):
    monkeypatch.setattr(graphiti_ingest, "_patches_ok", lambda: False)
    job = (await _enqueue("gated", _group()))["job"]
    w = graphiti_ingest.IngestWorker()
    assert await w.tick() == []
    assert w.state == "blocked: patches absent" and w.status()["patches_ok"] is False
    assert fake.calls == []
    assert (await _job(job["id"]))["status"] == "QUEUED"


async def test_an_update_hold_stops_new_claims(monkeypatch, fake):
    monkeypatch.setattr(hold, "active", True)
    job = (await _enqueue("held", _group()))["job"]
    w = graphiti_ingest.IngestWorker()
    assert await w.tick() == []
    assert w.state.startswith("held")
    assert (await _job(job["id"]))["status"] == "QUEUED"


async def test_index_ddl_and_the_cutover_run_once(fake):
    ran = []

    async def cutover():
        ran.append(1)

    w = graphiti_ingest.IngestWorker()
    w._cutover = cutover
    await w.tick()
    await w.tick()
    assert fake.indices == 1 and ran == [1]


def test_patches_ok_reads_the_installed_package(monkeypatch):
    from central_command.integrations import graphiti_client, graphiti_patches

    monkeypatch.setattr(graphiti_patches, "patch_state",
                        lambda: {"a.patch": "patched", "b.patch": "patched"})
    assert graphiti_client.patches_ok() is True
    monkeypatch.setattr(graphiti_patches, "patch_state",
                        lambda: {"a.patch": "patched", "b.patch": "unknown"})
    assert graphiti_client.patches_ok() is False
