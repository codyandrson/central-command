"""Shared fixtures and fakes for the world-moved suites
(tests/test_world_moved*.py): the pinned settings, the fake Confluence and the
scripted drafter. Kept out of any test module so a suite imports them without
importing another suite's tests."""

from __future__ import annotations

import uuid

import httpx
import pytest
from pydantic_ai.messages import (
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from central_command.config import settings
from central_command.db import repo
from central_command.integrations import confluence

AGENT = "jira-expert"
_ORIGINAL_CLIENT = httpx.AsyncClient


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "executor_mode", "live")
    monkeypatch.setattr(settings, "auditor_enabled", False)
    monkeypatch.setattr(settings, "confluence_base_url", "https://example.atlassian.net")
    monkeypatch.setattr(settings, "confluence_email", "bot@example.com")
    monkeypatch.setattr(settings, "confluence_api_token", "tok")
    monkeypatch.setattr(settings, "confluence_auth_mode", "basic")
    monkeypatch.setattr(settings, "confluence_api_flavor", "cloud")


def _confluence_answers(monkeypatch, *, version: int, put: httpx.Response | None = None):
    """A fake Confluence through the REAL `update_page`: the GET answers the
    page at `version`; the PUT answers `put` (a 200 at version+1 by default)."""
    def handler(request: httpx.Request) -> httpx.Response:
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
