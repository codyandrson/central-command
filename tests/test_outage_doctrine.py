"""The outage doctrine (PRD FR-40, epic-outage-doctrine): a DOWN dependency
parks work in place and resumes it when the dependency is back, with the same
result as if it had never been down.

The epic's shared test file; later stories append to it. Story 1 is the tracer
bullet — the one mechanism every later read reuses, on one tool:

- a DOWN `search_knowledge_graph` inside a task run raises the deferred-call
  pause (`CallDeferred`, the only way a pydantic-ai tool can end a run without
  failing it), so the session parks AWAITING_RESUME with the pending read, the
  sibling call's result is kept, and no "proceed without it" text reaches the
  agent;
- the retry sweep gates the park on every dependency it recorded plus `llm`
  (a gated skip spends no attempt), re-executes the read, and resumes the run
  through the task tail with the real result for that call id — so the task
  lands exactly as a run with no outage lands;
- a run with no landing tail (ingest) re-raises the transient failure into its
  unit's own DOWN path: the ledger item is released, as before.

The operator's acceptance standard for every DOWN park: "I expect these
processes to resume once the service is available again, with no long term
evidence of this outage (other than the immediate existence of the banner, and
any logs). Once completed, the session/run should result in the same results
as if the service was never offline."
"""

from __future__ import annotations

import ast
import json
import uuid
from pathlib import Path

import pytest
from neo4j.exceptions import ServiceUnavailable
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import FunctionModel

from central_command.api import orchestration, routes
from central_command.config import settings
from central_command.contract import TRANSIENT, DependencyDown, classify_failure
from central_command.db import repo
from central_command.integrations import graphiti
from central_command.runtime import durable, models, tools
from tests.conftest import aresolve, needs_pg

FACT = "Renewal of the example.com contract is due 2026-11-01"
DEGRADED = "proceed without it"


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    # demo_mode: these paths resolve models themselves; a real key in .env
    # would otherwise make the "offline" suite call the model for real.
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "executor_mode", "dry_run")
    monkeypatch.setattr(settings, "dispatch_approval_limit", 10_000)
    monkeypatch.setattr(settings, "auditor_enabled", False)

    async def _no_wait(delay):
        return None

    monkeypatch.setattr(tools, "_sleep", _no_wait)


class _Graph:
    """`graphiti.search_facts` double: DOWN (a typed Neo4j outage) until told
    otherwise. Counts every attempt, so a gated skip is provably free."""

    def __init__(self, down: bool = True):
        self.down = down
        self.calls = 0

    async def __call__(self, query, **kw):
        self.calls += 1
        if self.down:
            raise ServiceUnavailable("Couldn't connect to graph:7687")
        return [{"uuid": "edge-1", "fact": FACT, "name": "DUE_ON"}]


@pytest.fixture
def graph(monkeypatch):
    g = _Graph()
    monkeypatch.setattr(graphiti, "search_facts", g)

    async def _no_stewards():
        return {}

    monkeypatch.setattr(graphiti, "steward_map", _no_stewards)
    return g


def _graph_return(messages) -> ToolReturnPart | None:
    for m in messages:
        if isinstance(m, ModelRequest):
            for p in m.parts:
                if isinstance(p, ToolReturnPart) and p.tool_name == "search_knowledge_graph":
                    return p
    return None


def _model() -> FunctionModel:
    """Turn 1: a graph search BESIDE a sibling call in the same response.
    Turn 2: answer with whatever the search returned — so the task's outcome
    is a function of the read's real result, and only of it."""

    def _respond(messages, info) -> ModelResponse:
        ret = _graph_return(messages)
        if ret is not None:
            return ModelResponse(parts=[TextPart(content=f"Answer: {ret.content}")])
        return ModelResponse(parts=[
            ToolCallPart(tool_name="search_knowledge_graph",
                         args={"query": "contract renewal"},
                         tool_call_id="call_graph"),
            ToolCallPart(tool_name="declare_gap",
                         args={"kind": "missing_tool", "subject": "renewals",
                               "need": "a renewals calendar"},
                         tool_call_id="call_sibling"),
        ])

    return FunctionModel(_respond)


class _Probes:
    """A ProbeCache double: one verdict per probe name, and a record of what
    the sweep asked."""

    def __init__(self, **up: bool):
        self.up = up
        self.asked: list[str | None] = []

    async def healthy(self, dependency):
        self.asked.append(dependency)
        if dependency is None:
            return True
        return self.up.get(dependency, True)

    @property
    def results(self):
        return {}


async def _task(title: str = "Renewal check") -> dict:
    task_id = "task_" + uuid.uuid4().hex[:12]
    await repo.create_task(task_id, title, "When is the contract renewal due?",
                           "jira-expert")
    return await repo.get_task(task_id)


async def _parked_task_run(monkeypatch) -> tuple[dict, dict]:
    monkeypatch.setattr(routes, "resolve_model", aresolve(lambda *a, **k: _model()))
    task = await _task()
    out = await routes._run_assigned_task(task)
    return task, out


async def _events(ref_id: str, kind: str) -> list[dict]:
    return [e for e in await repo.list_events(ref_id=ref_id) if e["kind"] == kind]


# ── the contract and the seams ───────────────────────────────────────────────


def test_dependency_down_is_transient_by_type():
    """One classifier: the typed error is the verdict, whatever its text."""
    down = DependencyDown(("graph", "embedder"), "search_knowledge_graph",
                          ValueError("no marker in this text"))
    assert classify_failure(down) == TRANSIENT
    assert down.dependencies == ("graph", "embedder")


def test_every_parkable_read_classifies_as_a_read_park():
    """`durable.READ_TOOLS` (what classifies the pause) and the runtime's
    re-executable reads (what the resume can run) are one list."""
    assert durable.READ_TOOLS == frozenset(tools._parkable_reads())
    for name in durable.READ_TOOLS:
        assert durable.classify_deferred(ToolCallPart(tool_name=name, args={})) == "read"


def test_runtime_never_imports_the_heartbeat_tier():
    """Probes stay in `heartbeat/`; the runtime parks and re-executes, the API
    tier gates. Parsed, not grepped, so every import spelling counts."""
    runtime_dir = Path(tools.__file__).parent
    for py in runtime_dir.rglob("*.py"):
        for node in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if node.level >= 2:  # `from ..heartbeat import x` / `from .. import heartbeat`
                    mod = "central_command" + (f".{mod}" if mod else "")
                names = [mod] + [f"{mod}.{a.name}" for a in node.names]
            for n in names:
                assert not n.startswith("central_command.heartbeat"), (py, n)


async def test_without_a_landing_tail_the_outage_re_raises(graph):
    """`park_reads` False (the default): the ORIGINAL transient failure, after
    the bounded two attempts — never a degradation text."""
    with pytest.raises(ServiceUnavailable):
        await tools.search_knowledge_graph(None, "contract renewal")
    assert graph.calls == 2


async def test_a_no_from_the_graph_still_returns_honest_text(monkeypatch):
    """A semantic failure is the graph answering; the doctrine does not touch
    it, even in a run that may park."""

    async def _no(query, **kw):
        raise ValueError("center_node_uuid is not a node")

    monkeypatch.setattr(graphiti, "search_facts", _no)

    from types import SimpleNamespace

    from central_command.runtime.deps import TriageDeps

    ctx = SimpleNamespace(deps=TriageDeps(agent_id="jira-expert", park_reads=True))
    out = await tools.search_knowledge_graph(ctx, "contract renewal")
    assert out == "knowledge graph unavailable (ValueError); proceed without it"


async def test_run_read_by_name_re_validates_the_stored_arguments(graph):
    graph.down = False
    from central_command.runtime.deps import TriageDeps

    deps = TriageDeps(agent_id="jira-expert", park_reads=True)
    out = await tools.run_read_by_name(
        "search_knowledge_graph", {"query": "contract renewal"}, deps)
    assert FACT in out
    with pytest.raises(Exception):
        await tools.run_read_by_name("search_knowledge_graph", {"querry": "x"}, deps)
    with pytest.raises(ValueError):
        await tools.run_read_by_name("propose_action", {}, deps)
    graph.down = True
    with pytest.raises(DependencyDown):
        await tools.run_read_by_name(
            "search_knowledge_graph", {"query": "contract renewal"}, deps)


# ── the task run parks mid-turn ──────────────────────────────────────────────


@needs_pg
async def test_a_down_graph_search_parks_the_task_run_mid_turn(monkeypatch, graph):
    task, out = await _parked_task_run(monkeypatch)

    assert out["parked"] is True and out["deferred"] is False
    session_id = out["session_id"]
    assert graph.calls == 2, "the bounded retry still runs before the park"
    assert await repo.session_status(session_id) == "AWAITING_RESUME"

    rs = await repo.get_session_run_state(session_id)
    pending = rs["pending_resume"]
    assert pending["kind"] == "read"
    assert pending["tool"] == "search_knowledge_graph"
    assert pending["tool_call_id"] == "call_graph"
    assert pending["dependencies"] == ["graph", "embedder"]
    assert pending["after"] == "task"
    assert pending["args"]["query"] == "contract renewal"
    assert pending["attempts"] == 0
    assert pending["not_before"] is None
    assert "ServiceUnavailable" in pending["last_error"]
    assert pending["parked_at"]
    assert "text" not in pending, "no turn text is copied into the park"

    # The history is persisted up to the tool-call request, the sibling's
    # result with it — and nothing told the agent to proceed without the read.
    messages = durable.load_messages(rs)
    sibling = [p for m in messages if isinstance(m, ModelRequest) for p in m.parts
               if isinstance(p, ToolReturnPart) and p.tool_call_id == "call_sibling"]
    assert len(sibling) == 1
    assert [c.tool_call_id for c in durable.unanswered_calls(messages)] == ["call_graph"]
    assert DEGRADED not in json.dumps(rs["messages"])

    row = await repo.get_task(task["id"])
    assert row["status"] == "IN_PROGRESS"
    assert row["session_id"] == session_id

    parked = await _events(session_id, "session.resume_parked")
    assert len(parked) == 1
    assert parked[0]["payload"]["dependencies"] == ["graph", "embedder"]
    assert parked[0]["payload"]["transient"] is True
    assert not await _events(task["id"], "task.run_failed")
    assert not await _events(task["id"], "task.run_parked")


@needs_pg
async def test_the_sweep_skips_a_read_park_while_the_graph_is_down(monkeypatch, graph):
    _, out = await _parked_task_run(monkeypatch)
    session_id = out["session_id"]
    calls = graph.calls

    probes = _Probes(graphiti=False)
    result = await orchestration.retry_sweep(cache=probes)
    await orchestration.sweep_settle()

    skipped = [s for s in result["skipped_gated"] if s["id"] == session_id]
    assert len(skipped) == 1 and skipped[0]["down"] == ["graph"]
    assert skipped[0]["dependency"] == ["graph", "embedder", "llm"]
    assert "graphiti" in probes.asked and "llm" in probes.asked
    assert session_id not in result["resumed"]
    assert graph.calls == calls, "a gated skip never touches the dependency"
    assert await repo.session_status(session_id) == "AWAITING_RESUME"
    rs = await repo.get_session_run_state(session_id)
    assert rs["pending_resume"]["attempts"] == 0, "a gated skip spends no attempt"
    assert not await _events(session_id, "session.resume_retried")


@needs_pg
async def test_the_sweep_skips_a_read_park_while_the_llm_is_down(monkeypatch, graph):
    _, out = await _parked_task_run(monkeypatch)
    result = await orchestration.retry_sweep(cache=_Probes(llm=False))
    await orchestration.sweep_settle()
    skipped = [s for s in result["skipped_gated"] if s["id"] == out["session_id"]]
    assert skipped and skipped[0]["down"] == ["llm"]


@needs_pg
async def test_with_the_probes_up_the_read_resumes_in_place_and_the_task_lands(
    monkeypatch, graph
):
    task, out = await _parked_task_run(monkeypatch)
    session_id = out["session_id"]

    # The run the operator's standard compares against: the same task, graph up.
    graph.down = False
    clean_task = await _task("Renewal check, no outage")
    clean = await routes._run_assigned_task(clean_task)
    clean_outcome = (await repo.get_task(clean_task["id"]))["outcome"]
    assert clean["deferred"] is False and FACT in clean_outcome

    # The resume resolves its model per agent, like every resume leg.
    monkeypatch.setattr(models, "resolve_model", aresolve(lambda *a, **k: _model()))
    result = await orchestration.retry_sweep(cache=_Probes())
    await orchestration.sweep_settle()
    assert session_id in result["resumed"]

    # Same session, resumed in place; the read's REAL result is the tool
    # return for the parked call id.
    assert await repo.session_status(session_id) == "DONE"
    rs = await repo.get_session_run_state(session_id)
    ret = [p for m in durable.load_messages(rs) if isinstance(m, ModelRequest)
           for p in m.parts if isinstance(p, ToolReturnPart)
           and p.tool_call_id == "call_graph"]
    assert len(ret) == 1 and FACT in str(ret[0].content)
    assert DEGRADED not in json.dumps(rs["messages"])

    row = await repo.get_task(task["id"])
    assert row["status"] == "DONE"
    assert row["session_id"] == session_id
    assert row["outcome"] == clean_outcome, "the same result as with no outage"

    retried = await _events(session_id, "session.resume_retried")
    assert len(retried) == 1 and retried[0]["payload"]["kind"] == "read"
    assert len(await _events(task["id"], "task.completed")) == 1
    assert not await _events(task["id"], "task.run_failed")


@needs_pg
async def test_a_read_still_down_on_re_execution_re_parks_with_one_more_attempt(
    monkeypatch, graph
):
    """The probe said up and the read still failed DOWN: back to
    AWAITING_RESUME with `attempts + 1`, the task untouched. No limit."""
    task, out = await _parked_task_run(monkeypatch)
    session_id = out["session_id"]

    driven = await orchestration.resume_parked_read(session_id)
    assert driven["parked"] is True and driven["attempts"] == 1
    assert await repo.session_status(session_id) == "AWAITING_RESUME"
    rs = await repo.get_session_run_state(session_id)
    assert rs["pending_resume"]["attempts"] == 1
    assert rs["pending_resume"]["tool_call_id"] == "call_graph"
    assert (await repo.get_task(task["id"]))["status"] == "IN_PROGRESS"
    assert len(await _events(session_id, "session.resume_parked")) == 2

    # The generic driver routes a read park to the read driver too.
    again = await orchestration.resume_parked_session(session_id)
    assert again["parked"] is True and again["attempts"] == 2


@needs_pg
async def test_the_claim_is_the_lock(monkeypatch, graph):
    _, out = await _parked_task_run(monkeypatch)
    session_id = out["session_id"]
    assert await repo.claim_parked_resume(session_id) is not None
    assert await orchestration.resume_parked_read(session_id) is None


# ── a run with no landing tail keeps its unit's DOWN path ────────────────────


@pytest.fixture()
async def clean_ledger():
    async def wipe():
        conn = await repo._conn()
        try:
            await conn.execute(
                "delete from work_item where message_id like '<cc-doctrine-%'"
            )
        finally:
            await conn.close()

    await wipe()
    yield
    await wipe()


@needs_pg
async def test_an_ingest_runs_down_read_re_raises_and_the_item_releases(
    monkeypatch, graph, clean_ledger
):
    from central_command.ingest import dispatcher, ledger

    def _respond(messages, info) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart(
            tool_name="search_knowledge_graph", args={"query": "sender history"},
            tool_call_id="call_graph")])

    await ledger.enroll_email(
        "Message-ID: <cc-doctrine-1@example.com>\n"
        "From: jane.doe@example.com\n"
        "Subject: Contract renewal\n"
        "Date: Wed, 1 Jul 2026 09:00:00 +0000\n"
        "\nWhen is our renewal due?\n"
    )
    item = await repo.claim_next_work_item("sess_doctrine_ingest")
    assert item is not None and item["message_id"] == "<cc-doctrine-1@example.com>"

    out = await dispatcher.process_claimed(item, model=FunctionModel(_respond))

    assert out["ok"] is False and out["transient"] is True
    released = [e for e in await repo.list_events(ref_id=item["id"])
                if e["kind"] == "work.released"]
    assert released and released[-1]["payload"]["transient"] is True
    # The ORIGINAL Neo4j failure reached the dispatcher, not a wrapper.
    assert "Couldn't connect to graph:7687" in released[-1]["payload"]["error"]
    assert graph.calls == 2
