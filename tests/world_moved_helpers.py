"""Shared fixtures and fakes for the world-moved suites
(tests/test_world_moved*.py): the pinned settings, the fake Confluence, the two
scripted drafters (`_Drafter` redrafts once, `_Persistent` until the limit's
terminal report: different stop rules, kept apart), the task-run and event
helpers, the graph deletion's fakes and the scripted coach of a charter edit
(epic-small-fixes R24). Kept out of any test module so a suite imports them
without importing another suite's tests (epic-small-fixes R29)."""

from __future__ import annotations

import uuid

import httpx
import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from central_command.config import settings
from central_command.contract.models import Action
from central_command.db import repo
from central_command.integrations import confluence, graphiti_ingest, neo4j_reader

AGENT = "jira-expert"
_ORIGINAL_CLIENT = httpx.AsyncClient


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    monkeypatch.setattr(settings, "test_model", True)
    monkeypatch.setattr(settings, "auditor_enabled", False)
    monkeypatch.setattr(settings, "confluence_base_url", "https://example.atlassian.net")
    monkeypatch.setattr(settings, "confluence_email", "bot@example.com")
    monkeypatch.setattr(settings, "confluence_api_token", "tok")
    monkeypatch.setattr(settings, "confluence_auth_mode", "basic")
    monkeypatch.setattr(settings, "confluence_api_flavor", "cloud")


def _confluence_answers(monkeypatch, *, version: int, put: httpx.Response | None = None,
                        get: httpx.Response | None = None):
    """A fake Confluence through the REAL `update_page`: the GET answers the
    page at `version` (or `get` — a 404 is the page deleted since the draft,
    R25); the PUT answers `put` (a 200 at version+1 by default). Returns the
    requests made, as `(method, path)`."""
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "GET" and get is not None:
            return get
        if request.method == "GET":
            return httpx.Response(200, json={
                "id": "42", "title": "Roadmap", "spaceId": "9",
                "version": {"number": version},
                "body": {"storage": {"value": "<p>current</p>"}},
            })
        return put or httpx.Response(200, json={
            "id": "42", "title": "Roadmap", "spaceId": "9",
            "version": {"number": version + 1},
        })

    def factory(**kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return _ORIGINAL_CLIENT(**kwargs)

    monkeypatch.setattr(confluence.httpx, "AsyncClient", factory)
    return calls


def _confluence_down(monkeypatch, calls: list | None = None):
    """A Confluence that cannot be reached: every request, the re-check's read
    included, raises a transport error (DOWN)."""
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append((request.method, request.url.path))
        raise httpx.ConnectError("connection refused")

    def factory(**kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return _ORIGINAL_CLIENT(**kwargs)

    monkeypatch.setattr(confluence.httpx, "AsyncClient", factory)


def _page_update(expected_version: int) -> dict:
    return {
        "intent": "Update the roadmap page",
        "actions": [{
            "capability": "confluence.update_page",
            "arguments": {"page_id": "42", "title": "Roadmap",
                          "body_storage": "<p>new</p>",
                          "expected_version": expected_version},
            "target_ref": {"system": "confluence", "id": "42",
                           "read_version": str(expected_version)},
            "reversibility": "reversible",
        }],
        "evidence": [{"kind": "operator", "source_ref": "operator:task",
                      "locator": "test task", "claim": "Operator tasked an edit."}],
        "expected_effect": "page 42 carries the new body",
        "confidence": {"level": "high", "rationale": "The test states it."},
    }


class _Drafter:
    """Proposes `first`; resumed with a FAILED report, redrafts once with
    `second`; otherwise closes out. Records what each call saw, so a test can
    read the tool return the resumed run received and its system prompt."""

    def __init__(self, first: dict, second: dict, *, down_on_resume: int = 0):
        self.first, self.second = first, second
        self.down_on_resume = down_on_resume  # the model is DOWN this many resumes
        self.seen: list[dict] = []
        self.model = FunctionModel(self._respond)

    def _respond(self, messages, info: AgentInfo) -> ModelResponse:
        parts = [p for m in messages for p in getattr(m, "parts", [])]
        if self.down_on_resume and any(isinstance(p, ToolReturnPart) for p in parts):
            self.down_on_resume -= 1
            raise httpx.ConnectError("connection refused")
        self.seen.append({
            "system": [p.content for p in parts if isinstance(p, SystemPromptPart)],
            "returns": [str(p.content) for p in parts
                        if isinstance(p, ToolReturnPart) and p.tool_name == "propose_action"],
        })
        made = sum(isinstance(p, ToolCallPart) and p.tool_name == "propose_action"
                   for p in parts)
        if made == 0:
            return ModelResponse(parts=[ToolCallPart(
                tool_name="propose_action", args={"proposal": self.first})])
        last = self.seen[-1]["returns"]
        if made == 1 and last and "FAILED" in last[-1]:
            return ModelResponse(parts=[ToolCallPart(
                tool_name="propose_action", args={"proposal": self.second})])
        return ModelResponse(parts=[TextPart(content="Closing out.")])


@pytest.fixture
async def confluence_grant():
    from central_command.runtime import packs as packs_mod

    held = await packs_mod.granted_packs(AGENT)
    granted_here = "confluence.update_page" not in packs_mod.granted_capability_names(held)
    if granted_here:
        await repo.grant_pack(AGENT, "confluence-propose")
    yield
    if granted_here:
        conn = await repo._conn()
        try:
            await conn.execute(
                "delete from agent_grant where agent_id = $1 and pack = $2",
                AGENT, "confluence-propose")
        finally:
            await conn.close()


async def _draft(monkeypatch, drafter: _Drafter) -> dict:
    from central_command.api import routes

    async def fixed_model(**_kw):
        return drafter.model

    monkeypatch.setattr(routes, "resolve_model", fixed_model)
    task_id = f"task_wm_{uuid.uuid4().hex[:10]}"
    await repo.create_task(task_id, "edit the roadmap", "edit the page", AGENT,
                           created_by="operator")
    run = await routes._run_assigned_task(await repo.get_task(task_id))
    return await repo.load_proposal(run["proposal_id"])


async def _event(kind: str, ref_id: str) -> dict:
    return next(e for e in await repo.list_events(ref_id=ref_id) if e["kind"] == kind)


def _counting_confluence(monkeypatch, *, version: int, down: bool = False,
                         down_after_gets: int | None = None) -> dict:
    """A stateful fake Confluence: the page sits at `version` and every PUT
    moves it one on. `down` makes every request a transport error;
    `down_after_gets` answers that many GETs and then goes down. Returns the
    state, whose `calls` records every request."""
    state = {"version": version, "calls": [], "down": down}

    def handler(request: httpx.Request) -> httpx.Response:
        state["calls"].append(request.method)
        gets = state["calls"].count("GET")
        if state["down"] or (down_after_gets is not None and gets > down_after_gets):
            raise httpx.ConnectError("connection refused")
        if request.method == "PUT":
            state["version"] += 1
        return httpx.Response(200, json={
            "id": "42", "title": "Roadmap", "spaceId": "9",
            "version": {"number": state["version"]},
            "body": {"storage": {"value": "<p>current</p>"}},
        })

    def factory(**kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return _ORIGINAL_CLIENT(**kwargs)

    monkeypatch.setattr(confluence.httpx, "AsyncClient", factory)
    return state


def _drafting_model(monkeypatch, drafter: _Drafter) -> None:
    """The sweep resolves the drafter's model itself: hand it the fake."""
    from central_command.runtime import models

    async def fixed(model=None, **_kw):
        return drafter.model

    monkeypatch.setattr(models, "resolve_model", fixed)


class _Persistent:
    """Proposes on its first turn and again on every propose return that is not
    the terminal report; a return naming the redraft limit gets a text reply.
    Records each call's propose returns, so a test reads exactly what a resumed
    run — or an operator's next turn — was handed."""

    def __init__(self, make, cap: int = 12):
        self.make, self.cap = make, cap
        self.calls: list[list[str]] = []
        self.model = FunctionModel(self._respond)

    def _respond(self, messages, info: AgentInfo) -> ModelResponse:
        parts = [p for m in messages for p in getattr(m, "parts", [])]
        made = sum(isinstance(p, ToolCallPart) and p.tool_name == "propose_action"
                   for p in parts)
        last = messages[-1]
        fresh = [str(p.content) for p in (last.parts if isinstance(last, ModelRequest) else [])
                 if isinstance(p, ToolReturnPart | RetryPromptPart)
                 and p.tool_name == "propose_action"]
        self.calls.append(fresh)
        if any("redraft limit" in r for r in fresh):
            return ModelResponse(parts=[TextPart(content="Understood — it is yours.")])
        if made == 0 or (fresh and made < self.cap):
            return ModelResponse(parts=[ToolCallPart(
                tool_name="propose_action",
                args={"proposal": self.make(made)})])
        return ModelResponse(parts=[TextPart(content="Closing out.")])


async def _task_session(monkeypatch, drafter: _Persistent) -> tuple[dict, str]:
    from central_command.api import routes

    async def fixed_model(**_kw):
        return drafter.model

    monkeypatch.setattr(routes, "resolve_model", fixed_model)
    task_id = f"task_wl_{uuid.uuid4().hex[:10]}"
    await repo.create_task(task_id, "edit the roadmap", "edit the page", AGENT,
                           created_by="operator")
    run = await routes._run_assigned_task(await repo.get_task(task_id))
    return await repo.load_proposal(run["proposal_id"]), task_id


async def _redrafts_of(proposal_id: str) -> list[dict]:
    conn = await repo._conn()
    try:
        rows = await conn.fetch(
            "select id from proposal where redraft->>'of' = $1", proposal_id)
    finally:
        await conn.close()
    return [await repo.load_proposal(r["id"]) for r in rows]


async def _count(kind: str, ref_id: str) -> int:
    return sum(e["kind"] == kind for e in await repo.list_events(ref_id=ref_id))


# --- the graph deletion's fakes (story 2) ---------------------------------------


def _preview(digest: str, facts=()) -> dict:
    return {"episode": {"uuid": "X", "name": "e", "group_id": "g"}, "digest": digest,
            "facts": [{"uuid": f} for f in facts], "collateral_facts": [],
            "entities": []}


@pytest.fixture
def graph_world(monkeypatch):
    state = {"preview": None, "queued": []}

    async def fake_preview(uuid):
        return state["preview"]

    async def fake_enqueue(*a, **k):
        state["queued"].append((a, k))
        return {"id": 1}

    monkeypatch.setattr(neo4j_reader, "episode_delete_preview", fake_preview)
    monkeypatch.setattr(graphiti_ingest, "enqueue_delete", fake_enqueue)
    return state


def _delete_action(approved: dict) -> Action:
    return Action(capability="graph.delete_episode",
                  arguments={"episode_uuid": "X", "preview": approved},
                  target_ref={"system": "graph", "id": "X", "read_version": approved["digest"]},
                  reversibility="irreversible")


# --- the coached charter edit's fakes (epic-small-fixes R24) ------------------------


async def _coached_target(prefix: str, content: str = "CHARTER v1 for a world-moved test.") -> str:
    """A throwaway coaching target with its own v1 charter, so no assertion
    rides on whatever the shared dev database holds for a founding agent."""
    target = f"{prefix}-{uuid.uuid4().hex[:6]}"
    await repo.upsert_agent(target, target, "demo", "c")
    await repo.save_charter_version(target, content, "test seed", created_by="operator")
    return target


class _Coach:
    """A scripted coach for `target`: drafts a charter edit on its first turn
    (carrying `supplied_base` as an agent-written `base_version` when given,
    which the stamp must discard); on every FAILED propose return, up to `cap`
    drafts, redrafts against the charter text the report showed it; a return
    naming the redraft limit, or any other, gets a text reply. `reports` keeps
    each propose return it was handed."""

    def __init__(self, target: str, *, supplied_base: int | None = None, cap: int = 12):
        self.target, self.supplied_base, self.cap = target, supplied_base, cap
        self.reports: list[str] = []
        self.model = FunctionModel(self._respond)

    def _proposal(self, base_text: str, n: int) -> dict:
        args = {"agent_id": self.target,
                "content": f"{base_text}\n\nCOACHED RULE {n}: answer briefly.",
                "rationale": "Encodes the operator's note as a standing rule."}
        if self.supplied_base is not None:
            args["base_version"] = self.supplied_base
        return {
            "intent": f"Charter edit for {self.target}",
            "actions": [{"capability": "charter.update", "arguments": args,
                         "target_ref": {"system": "central_command", "id": self.target},
                         "reversibility": "reversible"}],
            "evidence": [{"kind": "operator", "source_ref": "operator:task",
                          "locator": "test", "claim": "The operator asked for brevity."}],
            "expected_effect": f"{self.target}'s charter gains a rule",
            "confidence": {"level": "medium", "rationale": "The test states it."},
        }

    def _respond(self, messages, info: AgentInfo) -> ModelResponse:
        import re

        parts = [p for m in messages for p in getattr(m, "parts", [])]
        made = sum(isinstance(p, ToolCallPart) and p.tool_name == "propose_action"
                   for p in parts)
        last = messages[-1]
        fresh = [str(p.content) for p in (last.parts if isinstance(last, ModelRequest) else [])
                 if isinstance(p, ToolReturnPart) and p.tool_name == "propose_action"]
        self.reports.extend(fresh)
        if made == 0:
            prompt = " ".join(str(getattr(p, "content", "")) for p in parts)
            body = re.search(r"---\n(.*?)\n---", prompt, re.DOTALL)
            return ModelResponse(parts=[ToolCallPart(
                tool_name="propose_action",
                args={"proposal": self._proposal(body.group(1) if body else "CHARTER", 0)})])
        if fresh and "FAILED" in fresh[-1] and "redraft limit" not in fresh[-1] \
                and made < self.cap:
            shown = re.search(r"charter v\d+ ---\n(.*?)\n--- end of charter ---",
                              fresh[-1], re.DOTALL)
            return ModelResponse(parts=[ToolCallPart(
                tool_name="propose_action",
                args={"proposal": self._proposal(shown.group(1) if shown else "?", made)})])
        return ModelResponse(parts=[TextPart(content="Closing out.")])
