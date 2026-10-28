"""Story 3 of epic-outage-doctrine: every dependency has one name and one probe,
probe results are persisted with edge events, every read and write maps to its
dependencies, every park records them, and the Executor classifies typed errors.

What these pin:

- the vocabulary is closed and total (parity against the tool packs, the
  Executor's registry and its handler table, and the probe table);
- the sweep GATES on every dependency a unit recorded, spending no attempt, and
  persists what the probes said — writing and announcing only a CHANGE;
- every park writer stamps what it waited on, and a row with none reads `llm`;
- the Executor flags a transient failure from the exception's TYPE, so a bare
  `ServiceUnavailable` parks instead of failing.

Probes are always monkeypatched here (the offline suite never asks a real
service); the one test of the real probe functions patches httpx's transport.
"""

from __future__ import annotations

import ast
import asyncio
import time
import uuid
from pathlib import Path

import pytest
from pydantic_ai.exceptions import ModelHTTPError

from central_command import events
from central_command.api import orchestration
from central_command.config import settings
from central_command.contract import dependencies as deps
from central_command.contract.failures import TRANSIENT_STATUS, DependencyDown
from central_command.db import repo
from central_command.gateway import executor, gateway
from central_command.gateway.capabilities import REGISTRY
from central_command.heartbeat import probes
from central_command.integrations import graphiti_ingest
from central_command.runtime import packs
from central_command.runtime.run import ingest_and_propose
from central_command.runtime.spike_model import make_sc1_model
from tests.conftest import make_parks_due, needs_pg

OUTAGE_TEXT = "Connection refused talking to the n8n façade"


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "executor_mode", "live")
    monkeypatch.setattr(settings, "dispatch_approval_limit", 10_000)


@pytest.fixture(autouse=True)
async def no_leftover_parks():
    """The sweep is a GLOBAL worklist: a RETRY_PENDING proposal one test leaves
    behind would be gated (and probed) by the next test's sweep. The test
    database is throwaway, so settle them FAILED on the way in and out."""
    async def settle():
        conn = await repo._conn()
        try:
            await conn.execute(
                "update proposal set status = 'FAILED', retry_state = null "
                "where status = 'RETRY_PENDING'")
            # Parked sessions and tasks other files left behind come due on
            # the backoff curve; a sweep here would probe their dependencies.
            await conn.execute(
                "update session set status = 'FAILED' where status = 'AWAITING_RESUME'")
            await conn.execute(
                "update task set retry_state = null "
                "where status = 'ASSIGNED' and retry_state is not null")
        finally:
            await conn.close()

    await settle()
    yield
    await settle()


def _probe_table(monkeypatch, **answers):
    """Install a probe per name. `answers[name]` is a bool, or a callable that
    returns one (for the flipping and the slow ones). Returns the ask log."""
    asked: list[str] = []

    def _make(name):
        async def _probe():
            asked.append(name)
            a = answers.get(name, True)
            return await a() if callable(a) else a

        return _probe

    monkeypatch.setattr(probes, "PROBES", {n: _make(n) for n in deps.PROBED})
    return asked


# --- the vocabulary is closed and total -----------------------------------------


def test_every_name_used_is_in_the_vocabulary():
    for table in (deps.TOOL_DEPENDENCY, deps.CAPABILITY_DEPENDENCY):
        for key, names in table.items():
            for name in names:
                assert name in deps.DEPENDENCIES, f"{key} names {name!r}"


def test_every_name_but_local_has_a_probe_and_nothing_else_does():
    assert set(probes.PROBES) == set(deps.PROBED)
    assert deps.LOCAL not in probes.PROBES
    # probes.py may not import the contract, so its copies are pinned equal.
    for const in ("LLM", "EMBEDDER", "GRAPH", "LITELLM", "N8N", "CALENDAR",
                  "JIRA", "CONFLUENCE", "FORGE", "SANDBOX"):
        assert getattr(probes, const) == getattr(deps, const)
    assert probes._DOWN_STATUS == set(TRANSIENT_STATUS)


def test_every_registry_row_and_every_handler_has_its_dependencies():
    names = {r.name for r in REGISTRY}
    assert names <= set(deps.CAPABILITY_DEPENDENCY), (
        names - set(deps.CAPABILITY_DEPENDENCY))
    assert set(executor.HANDLERS) <= set(deps.CAPABILITY_DEPENDENCY)


def test_the_documented_capability_shapes():
    d = deps.dependencies_for_capability
    assert d("graph.create_node") == (deps.GRAPH, deps.EMBEDDER)
    assert d("graph.add_episode") == (deps.GRAPH, deps.EMBEDDER)
    assert d("jira.add_comment") == (deps.JIRA,)
    assert d("confluence.update_page@v2") == (deps.CONFLUENCE,)
    assert d("calendar.create_event") == (deps.CALENDAR,)
    assert d("mail.send") == d("email.get_message") == (deps.N8N,)
    assert d("litellm.add_model") == d("autodiscovery.skip") == (deps.LITELLM,)
    assert d("sandbox.exec") == d("mcp.server_deploy") == (deps.SANDBOX,)
    for local in ("catalog.tag", "charter.update", "skill.create", "loe.create",
                  "work.bulk_dismiss"):
        assert d(local) == (deps.LOCAL,)
    assert d("web.fetch") == ()


def test_every_read_tool_reachable_from_a_pack_is_mapped():
    reachable = set()
    for pack in packs.PACKS.values() if hasattr(packs, "PACKS") else ():
        reachable |= set(getattr(pack, "tools", ()) or ())
    for name in reachable:
        assert (name in deps.TOOL_DEPENDENCY or name.startswith("propose_")
                or name in deps.NOT_READS), name


def test_gate_names_drops_local_and_adds_the_model_turn():
    assert deps.gate_names(None) == ("llm",)                 # none reads llm
    assert deps.gate_names(("local",)) == ()
    assert deps.gate_names(("graph", "embedder")) == ("graph", "embedder")
    assert deps.gate_names(("graph",), model_turn=True) == ("graph", "llm")
    assert deps.gate_names(("llm",), model_turn=True) == ("llm",)


def test_probes_module_imports_only_config_httpx_and_the_standard_library():
    tree = ast.parse(Path(probes.__file__).read_text())
    allowed_own = {"central_command.config"}
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            roots.add(node.module)
    import sys

    for mod in roots:
        top = mod.split(".")[0]
        assert (mod in allowed_own or top in {"httpx"}
                or top in sys.stdlib_module_names or top == "__future__"), mod


# --- the probes themselves --------------------------------------------------------


async def test_unconfigured_services_probe_up_and_a_503_probes_down(monkeypatch):
    real = {
        n: f for n, f in (("jira", probes.probe_jira), ("confluence", probes.probe_confluence),
                          ("forge", probes.probe_forge))
    }
    monkeypatch.setattr(settings, "jira_base_url", "")
    monkeypatch.setattr(settings, "confluence_base_url", "")
    monkeypatch.setattr(settings, "forge_instances", "")
    for fn in real.values():
        assert await fn() is True

    # A bare, unauthenticated GET; an answer of 401 is the service talking, a
    # 503 (or a refused connection) is it not.
    status = {"code": 401}
    seen: list[tuple[str, dict]] = []

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, **kw):
            seen.append((url, kw))
            import httpx

            return httpx.Response(status["code"], request=httpx.Request("GET", url))

    monkeypatch.setattr(probes.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(settings, "jira_base_url", "https://jira.example.com")
    assert await probes.probe_jira() is True
    status["code"] = 503
    assert await probes.probe_jira() is False
    assert seen[0][0] == "https://jira.example.com/status"
    assert "headers" not in seen[0][1], "probes are unauthenticated"


async def test_the_forge_is_up_when_any_instance_answers(monkeypatch):
    monkeypatch.setattr(
        settings, "forge_instances",
        "a=github:https://down.example.com,b=gitlab:https://up.example.com",
    )

    async def _answers(url):
        if "down" in url:
            raise OSError("refused")
        return True

    monkeypatch.setattr(probes, "_answers", _answers)
    assert await probes.probe_forge() is True
    monkeypatch.setattr(probes, "_answers", lambda url: _raise())
    assert await probes.probe_forge() is False


async def _raise():
    raise OSError("refused")


# --- the sweep: gating, persistence, edges ----------------------------------------


async def _jira_parked_proposal(monkeypatch) -> str:
    """An approved proposal parked RETRY_PENDING because jira was down."""

    async def set_due_date(issue_key, due_date):
        raise RuntimeError(OUTAGE_TEXT)

    monkeypatch.setattr(executor.jira, "set_due_date", set_due_date)
    model = make_sc1_model()
    run = await ingest_and_propose("Dana: DEMO-1 slipped, due Aug 3.", model=model)
    out = await gateway.approve_and_execute(
        run["session_id"], run["proposal_id"], model=model)
    assert out["execution"] == "parked"
    return run["proposal_id"]


async def _kinds(ref_id: str) -> list[str]:
    return [e["kind"] for e in await repo.list_events(ref_id=ref_id)]


@needs_pg
async def test_a_down_jira_skips_the_proposal_spends_no_attempt_and_is_recorded(
    monkeypatch,
):
    pid = await _jira_parked_proposal(monkeypatch)
    state = (await repo.load_proposal(pid))["retry_state"]
    assert state["dependencies"] == ["jira"] and state["attempts"] == 1

    _probe_table(monkeypatch, jira=False)
    baseline = len(await _kinds("jira"))
    await make_parks_due()
    result = await orchestration.retry_sweep()

    assert [s["id"] for s in result["skipped_gated"]] == [pid]
    assert result["skipped_gated"][0]["dependency"] == "jira"
    after = await repo.load_proposal(pid)
    assert after["status"] == "RETRY_PENDING"
    assert after["retry_state"]["attempts"] == 1, "a gated skip spends no attempt"

    rows = await repo.dependency_probe_states()
    assert rows["jira"]["state"] == "down"
    assert rows["jira"]["down_since"] is not None
    assert rows["jira"]["error"]
    assert (await _kinds("jira"))[baseline:] == ["dependency.down"]


@needs_pg
async def test_a_recorded_down_dependency_is_probed_with_no_waiting_work(monkeypatch):
    await repo.record_dependency_probe("confluence", False, "refused")
    asked = _probe_table(monkeypatch, confluence=False)
    baseline = len(await _kinds("confluence"))

    await make_parks_due()

    await orchestration.retry_sweep()

    assert asked == ["confluence"], "a banner is re-checked even with nothing parked"
    assert (await repo.dependency_probe_states())["confluence"]["state"] == "down"
    # Still down: nothing written, nothing announced.
    assert len(await _kinds("confluence")) == baseline


@needs_pg
async def test_a_nothing_parked_nothing_down_sweep_probes_nothing(monkeypatch):
    asked = _probe_table(monkeypatch)
    await orchestration.retry_sweep()
    assert asked == []
    assert await repo.dependency_probe_states() == {}


@needs_pg
async def test_a_flip_emits_exactly_one_down_then_one_up_across_three_sweeps(
    monkeypatch,
):
    up = {"on": False}

    async def _jira():
        return up["on"]

    pid = await _jira_parked_proposal(monkeypatch)
    _probe_table(monkeypatch, jira=_jira)
    # The event log is append-only and shared with earlier tests: count from here.
    baseline = len(await _kinds("jira"))

    await make_parks_due()

    await orchestration.retry_sweep()                      # down
    await make_parks_due()
    await orchestration.retry_sweep()                      # still down: silent
    assert (await _kinds("jira"))[baseline:] == ["dependency.down"]

    up["on"] = True

    async def set_due_date(issue_key, due_date):
        return None

    monkeypatch.setattr(executor.jira, "set_due_date", set_due_date)
    from tests.test_execution_retry import _graph_spy

    _graph_spy(monkeypatch)
    await make_parks_due()
    out = await orchestration.retry_sweep()                # up: the edge
    await orchestration.sweep_settle()
    assert pid in out["retried_executions"]
    await make_parks_due()
    await orchestration.retry_sweep()                      # steady: silent

    assert (await _kinds("jira"))[baseline:] == ["dependency.down", "dependency.up"]
    up_event = [e for e in await repo.list_events(ref_id="jira")
                if e["kind"] == "dependency.up"][-1]
    assert up_event["payload"]["down_since"] is not None
    row = (await repo.dependency_probe_states())["jira"]
    assert row["state"] == "up" and row["down_since"] is None


@needs_pg
async def test_probes_run_concurrently(monkeypatch):
    async def _slow():
        await asyncio.sleep(2.0)
        return True

    _probe_table(monkeypatch, jira=_slow, confluence=_slow)
    cache = probes.ProbeCache()
    started = time.monotonic()
    await cache.warm(["jira", "confluence"])
    elapsed = time.monotonic() - started

    assert elapsed < 3.0, f"two 2 s probes took {elapsed:.1f}s: they ran in series"
    assert cache.results == {"jira": True, "confluence": True}


async def test_a_probe_that_raises_is_down_with_its_error(monkeypatch):
    async def _boom():
        raise OSError("connection refused")

    _probe_table(monkeypatch, sandbox=_boom)
    cache = probes.ProbeCache()
    await cache.warm(["sandbox"])
    assert cache.results == {"sandbox": False}
    assert "connection refused" in cache.errors["sandbox"]


@needs_pg
async def test_the_startup_resume_sweep_leaves_a_proposal_whose_jira_is_down(
    monkeypatch,
):
    pid = await _jira_parked_proposal(monkeypatch)
    _probe_table(monkeypatch, jira=False)

    async def _must_not_run(*a, **k):
        raise AssertionError("the startup sweep replayed into a down dependency")

    monkeypatch.setattr(gateway, "retry_execution", _must_not_run)
    swept = await orchestration.resume_sweep()

    assert pid in swept["still_pending"] and pid not in swept["retried"]
    row = await repo.load_proposal(pid)
    assert row["status"] == "RETRY_PENDING" and row["retry_state"]["attempts"] == 1


# --- park records ------------------------------------------------------------------


@needs_pg
async def test_a_bare_neo4j_outage_parks_the_proposal_with_graph_and_embedder(
    monkeypatch,
):
    from neo4j.exceptions import ServiceUnavailable

    async def set_due_date(issue_key, due_date):
        return None

    async def enqueue(*a, **k):
        raise ServiceUnavailable("boom")  # nothing in the text says "outage"

    monkeypatch.setattr(executor.jira, "set_due_date", set_due_date)
    monkeypatch.setattr(graphiti_ingest, "enqueue", enqueue)
    model = make_sc1_model()
    run = await ingest_and_propose("Dana: DEMO-1 slipped, due Aug 3.", model=model)
    out = await gateway.approve_and_execute(
        run["session_id"], run["proposal_id"], model=model)

    assert out["execution"] == "parked"
    row = await repo.load_proposal(run["proposal_id"])
    assert row["status"] == "RETRY_PENDING"
    assert row["retry_state"]["dependencies"] == ["graph", "embedder"]
    ev = [e for e in await repo.list_events(ref_id=run["proposal_id"])
          if e["kind"] == "proposal.execution_parked"][0]
    assert ev["payload"]["dependencies"] == ["graph", "embedder"]


async def test_execution_failed_carries_the_flag_and_the_dependencies(monkeypatch):
    from neo4j.exceptions import ServiceUnavailable

    async def boom(args, approver, proposer):
        raise ServiceUnavailable("boom")

    monkeypatch.setitem(executor.HANDLERS, "graph.create_node", boom)
    from central_command.contract import Action

    from central_command.contract import Reversibility

    action = Action(capability="graph.create_node",
                    arguments={"name": "n", "group_id": "g", "summary": "s"},
                    target_ref={"kind": "node"},
                    reversibility=Reversibility.reversible)
    with pytest.raises(executor.ExecutionFailed) as caught:
        await executor.execute([action], approver="human:lee", source_refs=[],
                               proposer="agent", proposal_id=None)
    assert caught.value.transient is True
    assert caught.value.dependencies == ("graph", "embedder")

    async def semantic(args, approver, proposer):
        raise ValueError("404 issue does not exist")

    monkeypatch.setitem(executor.HANDLERS, "graph.create_node", semantic)
    with pytest.raises(executor.ExecutionFailed) as caught:
        await executor.execute([action], approver="human:lee", source_refs=[],
                               proposer="agent", proposal_id=None)
    assert caught.value.transient is False

    # A typed DependencyDown names its own dependencies.
    async def typed(args, approver, proposer):
        raise DependencyDown(("litellm",), "x", RuntimeError("down"))

    monkeypatch.setitem(executor.HANDLERS, "graph.create_node", typed)
    with pytest.raises(executor.ExecutionFailed) as caught:
        await executor.execute([action], approver="human:lee", source_refs=[],
                               proposer="agent", proposal_id=None)
    assert caught.value.transient is True and caught.value.dependencies == ("litellm",)


@needs_pg
async def test_a_task_park_records_the_model_unless_the_error_names_more():
    outage = ModelHTTPError(429, "cc-default", body="No deployments available")
    task_id = "task_" + uuid.uuid4().hex[:12]
    await repo.create_task(task_id, "dep stamp", "Advise only.", "jira-expert")
    state = await orchestration.park_task_retry(task_id, outage)
    assert state["dependencies"] == ["llm"]
    assert (await repo.get_task(task_id))["retry_state"]["dependencies"] == ["llm"]

    other = "task_" + uuid.uuid4().hex[:12]
    await repo.create_task(other, "dep stamp 2", "Advise only.", "jira-expert")
    state = await orchestration.park_task_retry(
        other, DependencyDown(("graph", "embedder"), "search_knowledge_graph",
                              RuntimeError("down")))
    assert state["dependencies"] == ["graph", "embedder"]


@needs_pg
async def test_a_decision_park_records_llm_and_a_read_park_keeps_its_tuple():
    from central_command.runtime import resume_park

    sid = "sess_" + uuid.uuid4().hex[:12]
    await repo.create_session(sid, "jira-expert") if hasattr(repo, "create_session") else None
    stored = await resume_park.park_resume(
        sid, {"after": "approved", "attempts": 1, "kind": "result",
              "text": "t", "tool_call_id": "c"},
        ModelHTTPError(429, "cc-default", body="No deployments available"))
    assert stored["dependencies"] == ["llm"]
    stored = await resume_park.park_resume(
        sid, {"after": "task", "attempts": 1, "kind": "read",
              "dependencies": ["jira"], "tool_call_id": "c"}, "down")
    assert stored["dependencies"] == ["jira"]


@needs_pg
async def test_a_transient_ledger_release_writes_work_item_dependencies():
    from central_command.ingest import ledger

    raw = (f"Message-ID: <cc-deps-{uuid.uuid4().hex[:8]}@example.com>\n"
           "From: sender@example.com\nSubject: Deps test\n"
           "Date: Wed, 1 Jul 2026 09:00:00 +0000\n\nBody.\n")
    await ledger.enroll_email(raw)
    item = await repo.claim_next_work_item("sess_deps")
    assert item is not None
    await repo.release_work_item(item["id"], error="down", count_attempt=False,
                                 dependencies=("embedder",))
    conn = await repo._conn()
    try:
        got = await conn.fetchval(
            "select dependencies from work_item where id = $1", item["id"])
        await conn.execute("delete from work_item where id = $1", item["id"])
    finally:
        await conn.close()
    import json

    assert json.loads(got) == ["embedder"]


@needs_pg
async def test_the_dispatcher_stamps_a_transient_release_with_the_model(monkeypatch):
    from central_command.ingest import dispatcher, ledger

    async def boom(item, siblings, siblings_capped, note, model):
        raise ModelHTTPError(429, "cc-default", body="No deployments available")

    monkeypatch.setitem(
        dispatcher.HANDLERS, "email",
        dispatcher._KindHandler(boom, "agent:inbox-triage", False),
    )
    raw = (f"Message-ID: <cc-deps-{uuid.uuid4().hex[:8]}@example.com>\n"
           "From: sender@example.com\nSubject: Deps test 2\n"
           "Date: Wed, 1 Jul 2026 09:00:00 +0000\n\nBody.\n")
    await ledger.enroll_email(raw)
    item = await repo.claim_next_work_item("sess_deps2")
    out = await dispatcher.process_claimed(item)
    assert out["transient"] is True
    conn = await repo._conn()
    try:
        got = await conn.fetchval(
            "select dependencies from work_item where id = $1", item["id"])
        await conn.execute("delete from work_item where id = $1", item["id"])
    finally:
        await conn.close()
    import json

    assert json.loads(got) == ["llm"]


@needs_pg
async def test_a_deferred_ingest_job_records_what_the_failing_call_needed():
    import httpx
    import openai

    embed_request = httpx.Request("POST", "http://proxy.example.com/v1/embeddings")
    chat_request = httpx.Request("POST", "http://proxy.example.com/v1/chat/completions")
    from neo4j.exceptions import ServiceUnavailable

    f = graphiti_ingest._failing_dependencies
    assert f(openai.APIConnectionError(request=embed_request)) == ("embedder",)
    assert f(openai.APIConnectionError(request=chat_request)) == ("llm",)
    assert f(ServiceUnavailable("boom")) == ("graph",)
    assert f(RuntimeError("anything else")) == ("llm",)


def test_the_dependency_events_are_not_a_registry_row():
    # Documentation drift check lives in test_architecture_doc; here we only
    # pin the kinds the sweep emits, so a rename is a deliberate act.
    src = Path(orchestration.__file__).read_text()
    assert '"dependency.up" if up else "dependency.down"' in src
    assert events is not None
