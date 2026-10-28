"""Story 7 of epic-outage-doctrine: an embedder outage parks graph writes.

Nothing lands without its vector. A gated curation write parks RETRY_PENDING
on ('graph', 'embedder'); the operator's direct write is refused 503 naming the
embedder (the cockpit keeps the form — tested in vitest); a ledger item whose
duplicate guards need the embedder is released transient, not run unguarded;
the ingest worker takes no extraction while the probes record the embedder or
the graph down. There is no fallback embedder.

The embedder is always faked (an `httpx.AsyncClient` that cannot connect); no
bolt write is ever reached — `neo4j_writer._write` is spied and must not run.
"""

from __future__ import annotations

import pathlib
import re
import uuid

import pytest
from fastapi import HTTPException

from central_command.api import routes
from central_command.config import settings
from central_command.contract import EmbedderDown
from central_command.db import repo
from central_command.gateway import executor, gateway
from central_command.ingest import dispatcher, ledger
from central_command.integrations import graphiti_ingest, neo4j_writer
from tests.conftest import needs_pg

ROOT = pathlib.Path(__file__).resolve().parents[1]


class _Unreachable:
    async def __aenter__(self):
        raise ConnectionError("the workstation is off")

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def embedder_down(monkeypatch):
    monkeypatch.setattr(neo4j_writer.httpx, "AsyncClient", lambda *a, **k: _Unreachable())


@pytest.fixture
def writes(monkeypatch) -> list[str]:
    """Every bolt write the writer attempts. Nothing may land without its
    vector, so under an embedder outage this stays empty."""
    seen: list[str] = []

    async def _write(query, **params):
        seen.append(query)
        return []

    monkeypatch.setattr(neo4j_writer, "_write", _write)
    return seen


# --- the writer ------------------------------------------------------------------------


async def test_embed_of_empty_text_is_still_none(embedder_down):
    assert await neo4j_writer.embed("") is None


async def test_a_create_raises_before_it_writes(embedder_down, writes):
    with pytest.raises(EmbedderDown):
        await neo4j_writer.create_node("Jane Doe", [], "Works at Example Co", "central_command")
    assert writes == [], "nothing lands without its vector"


def test_there_is_no_embedding_fallback():
    """`grep -rn fallback` over the writer and the graph client finds only the
    statements that there is none."""
    for rel in ("central_command/integrations/neo4j_writer.py",
                "central_command/integrations/graphiti_client.py"):
        for line in (ROOT / rel).read_text().splitlines():
            if re.search(r"fallback", line, re.I):
                assert re.search(r"\bno fallback\b|Never a fallback", line, re.I), (rel, line)


def test_the_dispatcher_no_longer_fails_open():
    assert "fail open" not in (ROOT / "central_command/ingest/dispatcher.py").read_text()


# --- the gated write -------------------------------------------------------------------------


async def test_the_executor_flags_an_embedder_outage_with_its_dependencies(
    embedder_down, writes, monkeypatch,
):
    from central_command.contract import Action, Reversibility

    monkeypatch.setattr(settings, "executor_mode", "live")
    action = Action(capability="graph.create_node",
                    arguments={"name": "Jane Doe", "group_id": "central_command",
                               "summary": "Works at Example Co"},
                    target_ref={"system": "graphiti"}, reversibility=Reversibility.reversible)
    with pytest.raises(executor.ExecutionFailed) as failed:
        await executor.execute([action], approver="human:lee", source_refs=[],
                               proposer="graph-steward", proposal_id=None)
    assert failed.value.transient is True
    assert failed.value.dependencies == ("graph", "embedder")
    assert writes == []


@needs_pg
async def test_a_create_node_proposal_parks_retry_pending_and_writes_nothing(
    embedder_down, writes, monkeypatch,
):
    monkeypatch.setattr(settings, "executor_mode", "live")
    monkeypatch.setattr(settings, "demo_mode", True)
    pid = "prop_emb_" + uuid.uuid4().hex[:10]
    sid = "sess_emb_" + uuid.uuid4().hex[:8]
    await repo.create_running_session(sid, "jira-expert")
    await repo.save_proposal(
        pid, sid, "jira-expert", "Record Jane Doe in the graph",
        [{"capability": "graph.create_node",
          "arguments": {"name": "Jane Doe", "group_id": "central_command",
                        "summary": "Works at Example Co"},
          "target_ref": {"system": "graphiti"}, "reversibility": "reversible"}],
        [{"kind": "operator", "source_ref": "operator:test", "locator": "test",
          "claim": "Jane Doe works at Example Co"}],
        "the graph knows Jane Doe", pid,
    )

    out = await gateway.approve_and_execute(sid, pid)

    assert out["execution"] == "parked"
    row = await repo.load_proposal(pid)
    assert row["status"] == "RETRY_PENDING"
    assert row["retry_state"]["dependencies"] == ["graph", "embedder"]
    assert writes == [], "the node count is unchanged: nothing was written"


# --- the operator's direct write -------------------------------------------------------------


async def test_the_direct_create_is_refused_503_naming_the_embedder(embedder_down, writes):
    res = await routes.graph_node_create(routes.GraphNodeIn(
        name="Jane Doe", labels=[], summary="Works at Example Co", group_id="central_command"))
    assert res.status_code == 503
    import json

    body = json.loads(res.body)
    assert body["error"] == "embedder down" and body["dependency"] == "embedder"
    assert "nothing was written" in body["detail"]
    assert writes == []


async def test_a_wrong_width_is_still_a_422_not_a_503(monkeypatch, writes):
    async def _wrong(text, timeout=60.0):
        return [0.5] * 7

    monkeypatch.setattr(neo4j_writer, "request_embedding", _wrong)
    with pytest.raises(HTTPException) as refused:
        await routes.graph_node_create(routes.GraphNodeIn(
            name="Jane Doe", labels=[], summary="", group_id="central_command"))
    assert refused.value.status_code == 422
    assert writes == []


# --- the ledger ---------------------------------------------------------------------------------


@needs_pg
async def test_a_ledger_item_whose_freeze_check_meets_the_outage_is_released(
    embedder_down, monkeypatch,
):
    import json

    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(settings, "semantic_freeze_enabled", True)
    ran: list[str] = []

    async def must_not_run(item, siblings, siblings_capped, note, model):
        ran.append(item["id"])
        return {"ok": True}

    monkeypatch.setitem(dispatcher.HANDLERS, "email",
                        dispatcher._KindHandler(must_not_run, "agent:inbox-triage", False))
    raw = (f"Message-ID: <cc-emb-{uuid.uuid4().hex[:8]}@example.com>\n"
           "From: sender@example.com\nSubject: Embedder outage\n"
           "Date: Wed, 1 Jul 2026 09:00:00 +0000\n\nJane Doe joined Example Co.\n")
    await ledger.enroll_email(raw)
    item = await repo.claim_next_work_item("sess_emb")

    out = await dispatcher.process_claimed(item)

    assert out["transient"] is True and ran == [], "not processed unguarded"
    conn = await repo._conn()
    try:
        row = await conn.fetchrow(
            "select state, not_before, dependencies, attempts from work_item where id = $1",
            item["id"])
        await conn.execute("delete from work_item where id = $1", item["id"])
    finally:
        await conn.close()
    assert row["state"] == "UNPROCESSED" and row["not_before"] is not None
    assert json.loads(row["dependencies"]) == ["embedder"]
    assert row["attempts"] == 0, "the attempt is handed back"


# --- the ingest worker ---------------------------------------------------------------------------


@needs_pg
async def test_the_ingest_worker_takes_no_extraction_while_the_embedder_is_down(monkeypatch):
    claimed: list[tuple] = []

    async def _claim(exclude_groups=None, kinds=None):
        claimed.append(kinds)
        return []

    monkeypatch.setattr(repo, "claim_ingest_jobs", _claim)
    worker = graphiti_ingest.IngestWorker()
    worker._recovered = worker._indices_ok = worker._cutover_done = True
    monkeypatch.setattr(worker, "_patches_gate", lambda: True)

    await repo.record_dependency_probe("embedder", False, "refused")
    await worker.tick()
    assert claimed == [(graphiti_ingest.REMOVE,)], "only bolt-only deletions run"

    await repo.record_dependency_probe("graph", False, "refused")
    await worker.tick()
    assert len(claimed) == 1, "the graph down: nothing is taken at all"

    await repo.record_dependency_probe("graph", True)
    await repo.record_dependency_probe("embedder", True)
    await worker.tick()
    assert claimed[-1] is None, "both up: extractions are taken again"


@needs_pg
async def test_the_sweep_probes_what_a_released_item_waits_on(embedder_down, monkeypatch):
    """No unit the sweep drives waits on the embedder, but a released ledger
    item does: the sweep still probes it, so the ingest gate and the banner
    learn of the outage."""
    from central_command.api import orchestration
    from central_command.heartbeat import probes

    asked: list[str] = []

    async def _down():
        asked.append("embedder")
        return False

    monkeypatch.setitem(probes.PROBES, "embedder", _down)
    raw = (f"Message-ID: <cc-emb-{uuid.uuid4().hex[:8]}@example.com>\n"
           "From: sender@example.com\nSubject: Embedder probe\n"
           "Date: Wed, 1 Jul 2026 09:00:00 +0000\n\nBody.\n")
    await ledger.enroll_email(raw)
    item = await repo.claim_next_work_item("sess_emb2")
    from datetime import datetime, timedelta, timezone

    await repo.release_work_item(item["id"], error="embedder down", count_attempt=False,
                                 not_before=datetime.now(timezone.utc) + timedelta(minutes=1),
                                 dependencies=("embedder",))
    try:
        await orchestration.retry_sweep()
        assert "embedder" in asked
        assert (await repo.dependency_probe_states())["embedder"]["state"] == "down"
    finally:
        conn = await repo._conn()
        try:
            await conn.execute("delete from work_item where id = $1", item["id"])
        finally:
            await conn.close()
