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

Later stories append here, among them epic-small-fixes R35 (the operator's
ruling of 2026-10-29, Decision 10): `propose_delete_episode`, whose deletion
preview is a live graph read, parks on a graph outage like any read and its
resume parks the proposal through each tail's own landing.

The operator's acceptance standard for every DOWN park: "I expect these
processes to resume once the service is available again, with no long term
evidence of this outage (other than the immediate existence of the banner, and
any logs). Once completed, the session/run should result in the same results
as if the service was never offline."
"""

from __future__ import annotations

import ast
import asyncio
import json
import uuid
from pathlib import Path

import pytest
from neo4j.exceptions import ServiceUnavailable
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import FunctionModel

from central_command.api import nerve_gateway, orchestration, routes
from central_command.api import outages as outages_mod
from central_command.config import settings
from central_command.contract import TRANSIENT, DependencyDown, classify_failure
from central_command.db import repo
from central_command.integrations import graphiti
from central_command.runtime import durable, models, tools
from tests.conftest import aresolve, needs_pg, make_parks_due
from tests.fake_world import fake_world

FACT = "Renewal of the example.com contract is due 2026-11-01"
DEGRADED = "proceed without it"


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    # test_model: these paths resolve models themselves; a real key in .env
    # would otherwise make the "offline" suite call the model for real.
    monkeypatch.setattr(settings, "test_model", True)
    fake_world(monkeypatch)
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

    async def warm(self, dependencies):
        pass

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
        call = ToolCallPart(tool_name=name, args={})
        # The tool ships `kind: 'read'` with its pause: that beats the name
        # (`consult_agent` is both a consult wait and a read).
        assert durable.classify_deferred(call, {"kind": "read"}) == "read"


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
    assert out.startswith("knowledge graph search failed (ValueError)")


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
    # The one backoff curve: a first park waits a minute (story 4).
    assert pending["not_before"] and pending["attempts"] == 0
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

    probes = _Probes(graph=False)
    await make_parks_due()
    result = await orchestration.retry_sweep(cache=probes)
    await orchestration.sweep_settle()

    skipped = [s for s in result["skipped_gated"] if s["id"] == session_id]
    assert len(skipped) == 1 and skipped[0]["down"] == ["graph"]
    assert skipped[0]["dependency"] == ["graph", "embedder", "llm"]
    assert "graph" in probes.asked and "llm" in probes.asked
    assert session_id not in result["resumed"]
    assert graph.calls == calls, "a gated skip never touches the dependency"
    assert await repo.session_status(session_id) == "AWAITING_RESUME"
    rs = await repo.get_session_run_state(session_id)
    assert rs["pending_resume"]["attempts"] == 0, "a gated skip spends no attempt"
    assert not await _events(session_id, "session.resume_retried")


@needs_pg
async def test_the_sweep_skips_a_read_park_while_the_llm_is_down(monkeypatch, graph):
    _, out = await _parked_task_run(monkeypatch)
    await make_parks_due()
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
    await make_parks_due()
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
    # R34 (story 11.8): the retried event names what the park waited on.
    assert retried[0]["payload"]["dependencies"] == ["graph", "embedder"]
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


# ═══ story 2: every live read tool parks on DOWN, answers a NO honestly ═══════

import re  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import httpx  # noqa: E402
from pydantic_ai import CallDeferred  # noqa: E402

from central_command.contract.dependencies import (  # noqa: E402
    NOT_READS,
    TOOL_DEPENDENCY,
    is_parkable,
)
from central_command.integrations import (  # noqa: E402
    confluence,
    email_facade,
    forge,
    jira,
)
from central_command.integrations import litellm as litellm_client  # noqa: E402
from central_command.runtime import consult as consult_mod  # noqa: E402
from central_command.runtime import packs as packs_mod  # noqa: E402
from central_command.runtime.deps import TriageDeps  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


def _park_ctx(agent_id: str = "jira-expert", park: bool = True):
    return SimpleNamespace(deps=TriageDeps(agent_id=agent_id, park_reads=park))


def _outage() -> Exception:
    return httpx.ConnectError("[Errno 111] Connection refused")


def _facade_outage() -> Exception:
    try:
        raise email_facade.EmailFacadeError("email façade list unreachable at x: ") \
            from httpx.ConnectTimeout("")
    except email_facade.EmailFacadeError as e:
        return e


def _facade_no() -> Exception:
    return email_facade.EmailFacadeError("email façade list failed: HTTP 400 bad query")


async def _raising(exc):
    async def _fn(*a, **kw):
        raise exc

    return _fn


# (dependency, tool, call, patcher(monkeypatch, exc), the tuple a park records)
def _graph(mp, exc):
    async def _boom(*a, **k):
        raise exc

    mp.setattr(graphiti, "search_facts", _boom)


def _jira(mp, exc):
    async def _boom(*a, **k):
        raise exc

    mp.setattr(jira, "get_issue", _boom)


def _confluence(mp, exc):
    async def _boom(*a, **k):
        raise exc

    mp.setattr(confluence, "get_page", _boom)


def _forge(mp, exc):
    async def _boom(*a, **k):
        raise exc

    mp.setattr(forge, "list_instances", _boom)


def _calendar(mp, exc):
    from central_command.reports import ea_calendar

    mp.setattr(settings, "calendar_facade_token", "token")

    async def _boom(*a, **k):
        raise exc

    mp.setattr(ea_calendar, "day", _boom)


def _mail(mp, exc):
    async def _boom(*a, **k):
        raise exc

    mp.setattr(email_facade, "list_refs", _boom)


def _litellm(mp, exc):
    async def _boom(*a, **k):
        raise exc

    mp.setattr(litellm_client, "list_models", _boom)


def _sandbox(mp, exc):
    from central_command.integrations import sandbox_client

    mp.setattr(settings, "test_model", False)

    async def _id(ctx):
        return "sb-1"

    async def _boom(*a, **k):
        raise exc

    mp.setattr(tools, "_sandbox_id_for", _id)
    mp.setattr(sandbox_client, "exec_cmd", _boom)


_CASES = {
    "graph": (tools.search_knowledge_graph, ("contract renewal",), _graph, _outage,
              ValueError, ["graph", "embedder"]),
    "jira": (tools.jira_get_issue, ("TASKS-1",), _jira, _outage,
             ValueError, ["jira"]),
    "confluence": (tools.confluence_get_page, ("1001",), _confluence, _outage,
                   ValueError, ["confluence"]),
    "forge": (tools.forge_list_instances, (), _forge, _outage,
              ValueError, ["forge"]),
    "calendar": (tools.read_calendar, (1,), _calendar, _outage,
                 ValueError, ["calendar"]),
    "n8n": (tools.mail_search, ("from:jane.doe@example.com",), _mail, _facade_outage,
            _facade_no, ["n8n"]),
    "litellm": (tools.litellm_list_models, (), _litellm, _outage,
                ValueError, ["litellm"]),
    "sandbox": (tools.sandbox_exec, ("ls",), _sandbox, _outage,
                ValueError, ["sandbox"]),
}


@pytest.mark.parametrize("dep", sorted(_CASES))
async def test_each_dependencys_reader_parks_on_down(monkeypatch, dep):
    """One reader per dependency, under a tail (`park_reads`): a transient
    failure pauses the run with the tool's tuple — and, with no tail, re-raises
    the original failure stamped with that tuple instead."""
    fn, args, patch, outage, _no, expected = _CASES[dep]

    patch(monkeypatch, outage())
    with pytest.raises(CallDeferred) as paused:
        await fn(_park_ctx(), *args)
    meta = paused.value.metadata
    assert meta["kind"] == "read" and meta["tool"] == fn.__name__
    assert meta["dependencies"] == expected
    assert DEGRADED not in json.dumps(meta)

    patch(monkeypatch, outage())
    with pytest.raises(Exception) as raised:
        await fn(_park_ctx(park=False), *args)
    assert not isinstance(raised.value, CallDeferred)
    assert getattr(raised.value, tools._DEPENDENCIES_ATTR) == tuple(expected)


@pytest.mark.parametrize("dep", sorted(_CASES))
async def test_each_dependencys_reader_answers_a_no_with_honest_text(monkeypatch, dep):
    fn, args, patch, _outage_, no, _expected = _CASES[dep]

    patch(monkeypatch, no("the service said no") if no is ValueError else no())
    out = await fn(_park_ctx(), *args)

    assert isinstance(out, str) and out
    assert "failed" in out or "unavailable" in out


async def test_a_calendar_that_is_not_configured_stays_text(monkeypatch):
    monkeypatch.setattr(settings, "calendar_facade_token", "")
    out = await tools.read_calendar(_park_ctx(), 0)
    assert out == "calendar unavailable (not configured)"


async def test_a_local_reader_never_parks(monkeypatch):
    """Postgres-only work has nothing to wait for: nothing probes `local`, so a
    failure there propagates or degrades as it always has — under a tail too."""

    async def _boom():
        raise _outage()

    monkeypatch.setattr(repo, "list_loes", _boom)
    out = await tools.loe_list(_park_ctx())
    assert isinstance(out, str) and "lines of effort read failed" in out

    down = DependencyDown(("local",), "x", _outage())
    with pytest.raises(httpx.ConnectError):
        tools._park_read_or_reraise(_park_ctx(), down, {})
    assert not is_parkable(TOOL_DEPENDENCY["loe_list"])
    assert "loe_list" not in durable.READ_TOOLS


async def test_a_web_page_is_not_a_dependency():
    assert TOOL_DEPENDENCY["fetch_url"] == () and TOOL_DEPENDENCY["search_web"] == ()
    assert "fetch_url" not in durable.READ_TOOLS


def _registry_tool_names() -> set[str]:
    src = (REPO / "central_command/gateway/capabilities.py").read_text()
    return set(re.findall(r'holder="[^"]*?\((\w+) tool\)', src))


def test_every_read_tool_reachable_from_a_pack_has_a_dependency_entry():
    """The map cannot fall behind the tools: every non-propose tool a pack
    offers is a key of TOOL_DEPENDENCY or a named non-read, and so is every
    tool the capability registry's read rows name as their holder."""
    pack_tools = {t for p in packs_mod.PACKS.values() for t in p.tool_names
                  if not t.startswith("propose_")}
    missing = sorted(pack_tools - set(TOOL_DEPENDENCY) - NOT_READS)
    assert not missing, f"read tools with no TOOL_DEPENDENCY entry: {missing}"

    registry = _registry_tool_names()
    assert registry, "the registry's holders no longer name tools — update this scan"
    assert not sorted(registry - set(TOOL_DEPENDENCY) - NOT_READS)

    # …and nothing in the map names a tool that is gone.
    assert not sorted(n for n in TOOL_DEPENDENCY if not hasattr(tools, n))
    assert not (set(TOOL_DEPENDENCY) & NOT_READS)


def test_every_parkable_tool_is_wrapped_and_the_two_lists_agree():
    for name in durable.READ_TOOLS:
        assert hasattr(getattr(tools, name), "__wrapped__"), name
    assert durable.READ_TOOLS == frozenset(tools._parkable_reads())


def test_no_outage_path_says_proceed_without_it():
    """The wording survives only where the dependency ANSWERED (a NO), where
    nothing probed is involved (a web page), and in comments."""
    allowed_tools = {"def _read_failed"}
    for rel in ("central_command/runtime/tools.py", "central_command/runtime/consult.py"):
        tree = ast.parse((REPO / rel).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if re.search(r"proceed without (it|them)", node.value):
                    # Docstrings are prose; a string that is returned is a text.
                    if len(node.value) > 120 and "\n" in node.value:
                        continue
                    owner = _owner(tree, node)
                    assert owner in allowed_tools | {"_degraded"}, (rel, owner, node.value[:80])


def _owner(tree, target) -> str:
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if any(n is target for n in ast.walk(fn)):
                return f"def {fn.name}" if fn.name == "_read_failed" else fn.name
    return "<module>"


async def test_two_pausing_reads_in_one_turn_tell_the_second_to_call_again():
    """A turn that pauses on two reads parks one; the other is answered with
    text that says to simply call it again — not "the decision you just
    received is about the other call"."""
    messages = [
        ModelRequest(parts=[]),
        ModelResponse(parts=[
            ToolCallPart(tool_name="search_knowledge_graph", args={}, tool_call_id="a"),
            ToolCallPart(tool_name="jira_get_issue", args={}, tool_call_id="b"),
        ]),
    ]
    results = await durable.deferred_results(messages, "a", "the result")
    assert results.calls["a"] == "the result"
    assert "call it again" in str(results.calls["b"])
    assert "operator" not in str(results.calls["b"])


async def test_a_reranker_outage_is_blamed_on_litellm_not_the_graph():
    class RerankHTTPError(Exception):
        pass

    err = RerankHTTPError("503")
    assert tools._attributed(("graph", "embedder"), err) == ("litellm",)
    assert tools._attributed(("graph", "embedder"), _outage()) == ("graph", "embedder")


# --- a consult whose specialist is DOWN parks the ASKER -----------------------


def _asker_model() -> FunctionModel:
    def _respond(messages, info) -> ModelResponse:
        for m in messages:
            if isinstance(m, ModelRequest):
                for p in m.parts:
                    if isinstance(p, ToolReturnPart) and p.tool_name == "consult_agent":
                        return ModelResponse(parts=[TextPart(content=f"Advice: {p.content}")])
        return ModelResponse(parts=[ToolCallPart(
            tool_name="consult_agent",
            args={"agent_id": "jira-expert", "question": "When is the renewal due?"},
            tool_call_id="call_consult",
        )])

    return FunctionModel(_respond)


def _specialist_model() -> FunctionModel:
    def _respond(messages, info) -> ModelResponse:
        ret = _graph_return(messages)
        if ret is not None:
            return ModelResponse(parts=[TextPart(content=f"Specialist: {ret.content}")])
        return ModelResponse(parts=[ToolCallPart(
            tool_name="search_knowledge_graph", args={"query": "renewal"},
            tool_call_id="call_inner")])

    return FunctionModel(_respond)


@needs_pg
async def test_a_consult_whose_specialist_hits_a_graph_outage_parks_the_asker(
    monkeypatch, graph
):
    real = consult_mod.consult

    async def with_specialist_model(*a, **kw):
        return await real(*a, model=_specialist_model(), **kw)

    monkeypatch.setattr(consult_mod, "consult", with_specialist_model)
    monkeypatch.setattr(routes, "resolve_model", aresolve(lambda *a, **k: _asker_model()))
    task_id = "task_" + uuid.uuid4().hex[:12]
    await repo.create_task(task_id, "Ask the Jira specialist", "Consult jira-expert.",
                           "litellm-manager")

    out = await routes._run_assigned_task(await repo.get_task(task_id))

    assert out["parked"] is True
    session_id = out["session_id"]
    pending = (await repo.get_session_run_state(session_id))["pending_resume"]
    assert pending["kind"] == "read" and pending["tool"] == "consult_agent"
    assert pending["tool_call_id"] == "call_consult"
    assert pending["dependencies"] == ["graph", "embedder"]
    assert pending["args"] == {"agent_id": "jira-expert",
                               "question": "When is the renewal due?"}
    assert (await repo.get_task(task_id))["status"] == "IN_PROGRESS"
    assert await repo.session_status(session_id) == "AWAITING_RESUME"

    # The graph comes back: the sweep re-runs the WHOLE consult and the asker
    # finishes with the specialist's real answer.
    graph.down = False
    monkeypatch.setattr(models, "resolve_model", aresolve(lambda *a, **k: _asker_model()))
    await make_parks_due()
    result = await orchestration.retry_sweep(cache=_Probes())
    await orchestration.sweep_settle()
    assert session_id in result["resumed"]

    row = await repo.get_task(task_id)
    assert row["status"] == "DONE"
    assert FACT in row["outcome"]
    assert await repo.session_status(session_id) == "DONE"


# ── a propose tool that parks on its own read (epic-small-fixes R35) ─────────
#
# `propose_delete_episode` embeds a live graph read (the deletion preview). The
# operator's ruling (2026-10-29, Decision 10): with the graph down the run parks
# on the outages banner, AWAITING_RESUME on ['graph'], and the graph back, the
# resume re-executes the tool and parks the deletion proposal through
# `park_proposal` — never "nothing proposed". What each tail does on resume:
#
# - task: `run._land` parks it (`tasked`), the task holds in REVIEW;
# - converse: `converse._land_turn` parks it (`conversational`);
# - orchestrator: the round has no proposal landing, so `_process` rejects the
#   call as a stranger, as it would with no outage — no proposal;
# - anything else: refused loudly, the session FAILED with the reason.

PREVIEW = {
    "episode": {"uuid": "ep-1", "name": "wrong renewal date", "group_id": "central_command"},
    "facts": [{"uuid": "f-1"}], "entities": [], "collateral_facts": [],
    "provenance_facts": [], "digest": "d1g3st",
}


class _Preview:
    """`graphiti.episode_delete_preview` double: DOWN (a typed Neo4j outage)
    until told otherwise; `gone` makes the episode not found."""

    def __init__(self):
        self.down = True
        self.gone = False
        self.calls = 0

    async def __call__(self, uuid):
        self.calls += 1
        if self.down:
            raise ServiceUnavailable("Couldn't connect to graph:7687")
        return None if self.gone else {**PREVIEW, "episode": {**PREVIEW["episode"], "uuid": uuid}}


@pytest.fixture
def curator(monkeypatch):
    """jira-expert holds graph-curate for this test only (in process: no grant
    row is written), and the deletion preview is faked."""
    from central_command.runtime import packs as packs_mod

    real = packs_mod.granted_packs

    async def with_curate(agent_id):
        packs = await real(agent_id)
        if agent_id == "jira-expert" and "graph-curate" not in packs:
            packs = (*packs, "graph-curate")
        return packs

    monkeypatch.setattr(packs_mod, "granted_packs", with_curate)
    preview = _Preview()
    monkeypatch.setattr(graphiti, "episode_delete_preview", preview)
    return preview


def _retry_or_return(messages, call_id: str):
    for m in messages:
        if isinstance(m, ModelRequest):
            for p in m.parts:
                if getattr(p, "tool_call_id", None) == call_id and isinstance(
                    p, (ToolReturnPart, RetryPromptPart)
                ):
                    return p
    return None


def _deleting_model() -> FunctionModel:
    """Turn 1: propose the deletion. Then answer with what the call returned
    (a retry prompt, on a resume whose episode is gone)."""

    def _respond(messages, info) -> ModelResponse:
        got = _retry_or_return(messages, "call_del")
        if got is not None:
            return ModelResponse(parts=[TextPart(content=f"Answer: {got.content}")])
        return ModelResponse(parts=[ToolCallPart(
            tool_name="propose_delete_episode",
            args={"episode_uuid": "ep-1", "rationale": "the renewal date is wrong"},
            tool_call_id="call_del",
        )])

    return FunctionModel(_respond)


async def _session_proposals(session_id: str) -> list[dict]:
    return [p for p in await repo.list_proposals_for("jira-expert")
            if p["session_id"] == session_id]


async def _parked_deletion_task(monkeypatch) -> tuple[dict, str]:
    monkeypatch.setattr(routes, "resolve_model", aresolve(lambda *a, **k: _deleting_model()))
    task = await _task("Delete the wrong renewal episode")
    out = await routes._run_assigned_task(task)
    assert out["parked"] is True
    return task, out["session_id"]


async def _assert_parked_on_the_graph(session_id: str, after: str) -> None:
    assert await repo.session_status(session_id) == "AWAITING_RESUME"
    pending = (await repo.get_session_run_state(session_id))["pending_resume"]
    assert pending["kind"] == "read" and pending["tool"] == "propose_delete_episode"
    assert pending["dependencies"] == ["graph"]
    assert pending["after"] == after
    assert pending["args"] == {"episode_uuid": "ep-1",
                               "rationale": "the renewal date is wrong"}
    assert session_id in await repo.sessions_awaiting_resume()
    graph_row = next(d for d in (await outages_mod.list_outages())["dependencies"]
                     if d["name"] == "graph")
    assert graph_row["waiting"]["sessions"] >= 1, "on the outages banner"
    assert not await _session_proposals(session_id), "nothing proposed while down"


async def _assert_one_deletion_proposal(session_id: str, context_key: str) -> dict:
    (prop,) = await _session_proposals(session_id)
    full = await repo.load_proposal(prop["id"])
    (action,) = full["actions"]
    assert action["capability"] == "graph.delete_episode@v1"
    assert action["arguments"]["episode_uuid"] == "ep-1"
    assert action["arguments"]["preview"]["digest"] == "d1g3st", "the preview is embedded"
    created = await _events(prop["id"], "proposal.created")
    assert len(created) == 1, "parked through park_proposal, once"
    assert created[0]["payload"]["session_id"] == session_id
    assert created[0]["payload"].get(context_key) is True
    assert await repo.session_status(session_id) == "AWAITING_HUMAN"
    return prop


@needs_pg
async def test_a_down_graph_parks_a_deletion_task_and_the_resume_parks_the_proposal(
    monkeypatch, curator
):
    task, session_id = await _parked_deletion_task(monkeypatch)
    await _assert_parked_on_the_graph(session_id, "task")
    assert curator.calls == 2, "the bounded retry still runs before the park"
    assert (await repo.get_task(task["id"]))["status"] == "IN_PROGRESS"
    messages = json.dumps((await repo.get_session_run_state(session_id))["messages"])
    assert "nothing proposed" not in messages

    # Still down: the sweep skips it and spends nothing.
    await make_parks_due()
    result = await orchestration.retry_sweep(cache=_Probes(graph=False))
    await orchestration.sweep_settle()
    assert session_id not in result["resumed"]
    assert curator.calls == 2

    curator.down = False
    await make_parks_due()
    result = await orchestration.retry_sweep(cache=_Probes())
    await orchestration.sweep_settle()
    assert session_id in result["resumed"]

    prop = await _assert_one_deletion_proposal(session_id, "tasked")
    row = await repo.get_task(task["id"])
    assert row["status"] == "REVIEW" and row["session_id"] == session_id
    review = await _events(task["id"], "task.review")
    assert [e["payload"]["proposal_id"] for e in review] == [prop["id"]]
    assert not await _events(session_id, "session.failed")


@needs_pg
async def test_a_deletion_whose_episode_is_gone_on_resume_hands_back_the_retry(
    monkeypatch, curator
):
    """A NO after the outage is the same NO as without one: the model gets the
    ModelRetry naming list_graph_group_episodes, and the task lands DONE."""
    task, session_id = await _parked_deletion_task(monkeypatch)
    curator.down, curator.gone = False, True
    monkeypatch.setattr(models, "resolve_model", aresolve(lambda *a, **k: _deleting_model()))
    await make_parks_due()
    result = await orchestration.retry_sweep(cache=_Probes())
    await orchestration.sweep_settle()
    assert session_id in result["resumed"]

    assert not await _session_proposals(session_id)
    rs = await repo.get_session_run_state(session_id)
    retry = _retry_or_return(durable.load_messages(rs), "call_del")
    assert isinstance(retry, RetryPromptPart)
    assert "list_graph_group_episodes" in str(retry.content)
    row = await repo.get_task(task["id"])
    assert row["status"] == "DONE" and "list_graph_group_episodes" in row["outcome"]


async def _chat(session_id: str, message: str) -> list[dict]:
    frames: list[dict] = []

    async def notify(frame):
        frames.append(frame)

    await nerve_gateway._chat_send(
        {"sessionKey": f"agent:jira-expert:{session_id}", "message": message}, notify)
    for _ in range(200):
        if any(f.get("event") == "agent"
               and f["payload"]["data"]["phase"] in ("end", "error") for f in frames):
            break
        await asyncio.sleep(0.05)
    return frames


@needs_pg
async def test_a_down_graph_parks_a_chat_deletion_and_the_resume_parks_it_conversationally(
    monkeypatch, curator
):
    async def _resolve(*a, **k):
        return _deleting_model()

    monkeypatch.setattr(routes, "resolve_model", _resolve)
    monkeypatch.setattr(models, "resolve_model", _resolve)
    monkeypatch.setattr(models, "resolve_session_model", _resolve)
    session_id = "sess_" + uuid.uuid4().hex[:12]
    await repo.create_conversation_session(session_id, "jira-expert")
    await repo.mark_session_awaiting_operator(session_id)

    frames = await _chat(session_id, "Delete the wrong renewal episode ep-1.")
    phases = [f["payload"]["data"]["phase"] for f in frames if f.get("event") == "agent"]
    assert phases[-1] == "end", "the spinner clears on a parked turn"
    await _assert_parked_on_the_graph(session_id, "converse")

    curator.down = False
    await make_parks_due()
    result = await orchestration.retry_sweep(cache=_Probes())
    await orchestration.sweep_settle()
    assert session_id in result["resumed"]
    await _assert_one_deletion_proposal(session_id, "conversational")
    assert not await _events(session_id, "session.failed")


async def _repark(session_id: str, **changes) -> None:
    """Rewrite the parked read's tail — the shape an orchestrator round (or a
    tail no read parks under) would have left."""
    rs = await repo.get_session_run_state(session_id)
    await repo.update_session_run_state(
        session_id, {"pending_resume": {**rs["pending_resume"], **changes}})
    await make_parks_due()


@needs_pg
async def test_an_orchestrator_round_rejects_the_resumed_deletion_as_it_would_live(
    monkeypatch, curator
):
    """The orchestrator round holds no proposal landing: a stranger deferred
    call is a batch rejection with no outage, and so it is after one."""
    task, session_id = await _parked_deletion_task(monkeypatch)
    state = {"task_id": task["id"], "round": 0, "stalls": 0, "assigns": 0,
             "retries": 0, "plan_approved": False}
    await _repark(session_id, after="orchestrator", state=state)
    rejected: list[list[str]] = []

    async def capture(task, session_id, result, state, deps, model, calls, problems):
        rejected.append(problems)
        return {"rejected": True, "session_id": session_id}

    monkeypatch.setattr(orchestration, "_reject_batch", capture)
    curator.down = False
    out = await orchestration.resume_parked_read(session_id)

    assert out["rejected"] is True and out["after"] == "orchestrator"
    assert rejected == [["unexpected deferred call(s): ['propose_delete_episode']"]]
    assert not await _session_proposals(session_id)


@needs_pg
async def test_a_tail_with_no_proposal_landing_fails_loudly(monkeypatch, curator):
    task, session_id = await _parked_deletion_task(monkeypatch)
    await _repark(session_id, after="consulted")
    curator.down = False

    with pytest.raises(RuntimeError, match="'consulted' has no proposal landing"):
        await orchestration.resume_parked_read(session_id)

    assert await repo.session_status(session_id) == "FAILED"
    failed = await _events(session_id, "session.failed")
    assert failed and "no proposal landing" in failed[-1]["payload"]["reason"]
    assert not await _session_proposals(session_id)
    assert (await repo.get_task(task["id"]))["status"] == "FAILED"
