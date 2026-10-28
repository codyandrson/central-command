"""Story 6 of epic-outage-doctrine: a model DOWN mid-turn parks the session under
every landing tail, and only NO lands FAILED.

The operator's standard for every park (2026-10-27): resume once the service
is back, no long-term evidence of the outage beyond the banner and the logs,
and the same result as if the service was never offline. For a chat turn that
means no duplicated operator turn and the reply where it would have landed;
for a task run it means the turns before the outage are not re-spent.

Every model here is a FunctionModel whose script raises a 503 on a chosen call
(`_Script`), so the outage is exactly where the test puts it.
"""

from __future__ import annotations

import ast
import asyncio
import pathlib
import re
import uuid

import pytest
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from central_command.api import nerve_gateway, orchestration, routes
from central_command.config import settings
from central_command.db import repo
from central_command.runtime import converse
from central_command.runtime import models as models_mod
from tests.conftest import aresolve, make_parks_due, needs_pg

pytestmark = needs_pg

DOWN = ModelHTTPError(503, "cc-default", body="upstream unavailable")
ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "executor_mode", "dry_run")
    monkeypatch.setattr(settings, "dispatch_approval_limit", 10_000)


@pytest.fixture(autouse=True)
async def settle_leftovers():
    """The sweep is a GLOBAL worklist and every model resolution here is
    patched to one script: a parked session another test left behind would be
    driven by THIS test's sweep and eat its scripted steps. Settle them first
    (the test database is throwaway)."""
    conn = await repo._conn()
    try:
        await conn.execute("update session set status = 'DONE' "
                           "where status = 'AWAITING_RESUME'")
        await conn.execute("update proposal set status = 'FAILED', retry_state = null "
                           "where status = 'RETRY_PENDING'")
        await conn.execute("update task set retry_state = null "
                           "where status = 'ASSIGNED' and retry_state is not null")
    finally:
        await conn.close()
    yield


class _Script:
    """A model scripted call by call: each entry is a ModelResponse to return
    or an exception to raise. `calls` counts every request the model saw — the
    'tokens not re-spent' evidence."""

    def __init__(self, *steps):
        self.steps = list(steps)
        self.calls = 0

    def model(self) -> FunctionModel:
        def _respond(messages, info):
            self.calls += 1
            step = self.steps.pop(0) if self.steps else _text("done")
            if isinstance(step, BaseException):
                raise step
            return step

        return FunctionModel(_respond)


def _text(t: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=t)])


def _tool_call() -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(tool_name="current_time", args={})])


def _route_models(monkeypatch, script: _Script) -> None:
    """Every resolution path a fresh run, a chat turn and a resume take."""
    fm = script.model()

    async def _resolve(*a, **k):
        return fm

    monkeypatch.setattr(routes, "resolve_model", _resolve)
    monkeypatch.setattr(models_mod, "resolve_model", _resolve)
    monkeypatch.setattr(models_mod, "resolve_session_model", _resolve)


async def _kinds(ref_id: str) -> list[str]:
    return [e["kind"] for e in await repo.list_events(ref_id=ref_id, limit=1000)]


async def _messages(session_id: str) -> list[dict]:
    return (await repo.get_session_run_state(session_id) or {}).get("messages") or []


def _user_prompts(messages: list[dict], text: str) -> int:
    return sum(1 for m in messages for p in m.get("parts", [])
               if p.get("part_kind") == "user-prompt" and text in str(p.get("content")))


async def _sweep() -> dict:
    await make_parks_due()
    out = await orchestration.retry_sweep()
    await orchestration.sweep_settle()
    return out


# --- the chat turn --------------------------------------------------------------------


async def _send(session_id: str, message: str) -> list[dict]:
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


async def _lane() -> str:
    sid = "sess_" + uuid.uuid4().hex[:12]
    await repo.create_conversation_session(sid, "jira-expert")
    await repo.mark_session_awaiting_operator(sid)
    return sid


async def test_a_chat_turn_whose_model_is_down_parks_and_lands_the_reply(monkeypatch):
    script = _Script(DOWN, _text("DEMO-1 is due Aug 3."))
    _route_models(monkeypatch, script)
    sid = await _lane()
    ask = "When is DEMO-1 due?"

    frames = await _send(sid, ask)

    phases = [f["payload"]["data"]["phase"] for f in frames if f.get("event") == "agent"]
    assert phases[-1] == "end", "the spinner must clear on a parked turn"
    assert not [f for f in frames if f.get("event") == "cc.chat.error"]
    assert await repo.session_status(sid) == "AWAITING_RESUME"
    pending = (await repo.get_session_run_state(sid))["pending_resume"]
    assert pending["kind"] == "turn" and pending["after"] == "converse"
    assert pending["dependencies"] == ["llm"]
    assert "session.failed" not in await _kinds(sid)
    code, message = converse.why_not_sendable(
        await nerve_gateway._with_parked_on(await repo.get_session(sid)))
    assert code == "awaiting_resume" and "llm" in message
    assert "llm" in await nerve_gateway.parked_wait_line(sid)
    assert _user_prompts(await _messages(sid), ask) == 1

    out = await _sweep()                                   # every probe up

    assert sid in out["resumed"]
    assert await repo.session_status(sid) == "AWAITING_OPERATOR"
    messages = await _messages(sid)
    assert _user_prompts(messages, ask) == 1, "the operator's turn was not duplicated"
    last = messages[-1]
    assert last["kind"] == "response"
    assert "DEMO-1 is due Aug 3." in str(last["parts"])
    assert "session.failed" not in await _kinds(sid)


async def test_the_wait_line_names_the_dependency_and_since_when(monkeypatch):
    script = _Script(DOWN)
    _route_models(monkeypatch, script)
    sid = await _lane()
    await _send(sid, "anything")
    await repo.record_dependency_probe("llm", False, "503")
    line = await nerve_gateway.parked_wait_line(sid)
    assert re.match(r"paused — llm is down since \d{4}-\d\d-\d\d \d\d:\d\d; retrying", line)


async def test_closing_a_parked_lane_lands_it_stopped_and_clears_the_park(monkeypatch):
    script = _Script(DOWN)
    _route_models(monkeypatch, script)
    sid = await _lane()
    await _send(sid, "hello?")
    assert await repo.session_status(sid) == "AWAITING_RESUME"

    out = await converse.end_conversation(sid)

    assert out["status"] == "STOPPED"
    assert await repo.session_status(sid) == "STOPPED"
    assert sid not in await repo.sessions_awaiting_resume()
    assert (await repo.get_session(sid))["closed_reason"]
    assert "session.stopped" in await _kinds(sid)
    assert "session.failed" not in await _kinds(sid)


async def test_a_semantic_model_error_in_a_chat_turn_still_fails_with_a_reason(monkeypatch):
    script = _Script(ModelHTTPError(400, "cc-default", body="bad request"))
    _route_models(monkeypatch, script)
    sid = await _lane()
    await _send(sid, "hello?")
    assert await repo.session_status(sid) == "FAILED"
    failed = [e for e in await repo.list_events(ref_id=sid) if e["kind"] == "session.failed"]
    assert failed and failed[-1]["payload"]["reason"]


# --- the task run -----------------------------------------------------------------------


async def _task() -> dict:
    task_id = "task_" + uuid.uuid4().hex[:12]
    await repo.create_task(task_id, "turn park test", "What time is it?", "jira-expert")
    return await repo.get_task(task_id)


async def test_a_task_run_whose_second_turn_is_down_parks_and_resumes_to_done(monkeypatch):
    script = _Script(_tool_call(), DOWN, _text("It is now."))
    _route_models(monkeypatch, script)
    task = await _task()

    out = await routes._run_assigned_task(task, actor="operator")

    assert out["parked"] is True and out["kind"] == "turn"
    row = await repo.get_task(task["id"])
    assert row["status"] == "IN_PROGRESS" and not row.get("retry_state")
    sid = row["session_id"]
    assert await repo.session_status(sid) == "AWAITING_RESUME"
    before = await _messages(sid)
    assert before[-1]["kind"] == "request", "the unsent request is what re-sends"
    calls_before = script.calls                                   # 2

    await _sweep()

    row = await repo.get_task(task["id"])
    assert row["status"] == "DONE", row
    after = await _messages(sid)
    assert after[:len(before) - 1] == before[:-1], "the turns before the outage are kept"
    # The unsent request is RE-SENT, not rebuilt: same parts (the tool's
    # return is the one it gave before the outage — the tool did not re-run);
    # pydantic-ai restamps only its send metadata (run id, state, timestamp).
    def _parts(m):
        return [(p["part_kind"], p.get("tool_call_id"), p.get("content"))
                for p in m["parts"]]
    assert _parts(after[len(before) - 1]) == _parts(before[-1])
    assert len(after) == len(before) + 1
    # Only the outage turn was re-sent: this session's record gained exactly
    # one response (the model double is shared with whatever runs after the
    # task lands, so its raw call count is not this session's evidence).
    responses = lambda ms: sum(1 for m in ms if m["kind"] == "response")  # noqa: E731
    assert responses(after) == responses(before) + 1
    assert script.calls >= calls_before + 1
    assert "session.failed" not in await _kinds(sid)
    assert "task.run_parked" not in await _kinds(task["id"]), "parked in place, not fresh"


async def test_a_second_outage_on_the_resumed_run_parks_in_place_again(monkeypatch):
    script = _Script(_tool_call(), DOWN, DOWN, _text("It is now."))
    _route_models(monkeypatch, script)
    task = await _task()
    await routes._run_assigned_task(task, actor="operator")
    sid = (await repo.get_task(task["id"]))["session_id"]

    await _sweep()                                               # still down

    assert await repo.session_status(sid) == "AWAITING_RESUME"
    pending = (await repo.get_session_run_state(sid))["pending_resume"]
    assert pending["kind"] == "turn" and pending["attempts"] == 1
    assert (await repo.get_task(task["id"]))["status"] == "IN_PROGRESS"

    await _sweep()                                               # back

    assert (await repo.get_task(task["id"]))["status"] == "DONE"
    assert "session.failed" not in await _kinds(sid)


async def test_a_semantic_model_error_in_a_task_run_still_fails_with_a_reason(monkeypatch):
    script = _Script(ModelHTTPError(400, "cc-default", body="bad request"))
    _route_models(monkeypatch, script)
    task = await _task()
    with pytest.raises(Exception):
        await routes._run_assigned_task(task, actor="operator")
    row = await repo.get_task(task["id"])
    assert row["status"] == "FAILED"
    sid = row["session_id"]
    failed = [e for e in await repo.list_events(ref_id=sid) if e["kind"] == "session.failed"]
    assert failed and failed[-1]["payload"]["reason"]


# --- the orchestrator round --------------------------------------------------------------


async def _project() -> dict:
    task_id = "task_" + uuid.uuid4().hex[:12]
    await repo.create_task(task_id, "turn park project", "Plan a small thing.", "orchestrator")
    return await repo.get_task(task_id)


async def test_an_orchestrator_round_whose_model_is_down_parks_with_its_loop_state(monkeypatch):
    script = _Script(DOWN)
    project = await _project()
    out = await orchestration.run_project(project, model=script.model())

    assert out["parked"] is True and out["kind"] == "turn"
    sid = out["session_id"]
    assert await repo.session_status(sid) == "AWAITING_RESUME"
    pending = (await repo.get_session_run_state(sid))["pending_resume"]
    assert pending["after"] == "orchestrator"
    assert pending["state"]["task_id"] == project["id"]
    assert (await repo.get_task(project["id"]))["status"] != "FAILED"


async def test_a_semantic_model_error_in_an_orchestrator_round_still_fails(monkeypatch):
    script = _Script(ModelHTTPError(400, "cc-default", body="bad request"))
    project = await _project()
    with pytest.raises(Exception):
        await orchestration.run_project(project, model=script.model())
    sid = (await repo.get_task(project["id"]))["session_id"]
    assert await repo.session_status(sid) == "FAILED"


# --- the no-tail table ---------------------------------------------------------------------


_PARKING_CALLERS = {
    "orchestration.run_project", "orchestration._reject_batch",
    "orchestration.maybe_resume", "orchestration._drive_tail",
    "orchestration.continue_session", "orchestration.resume_stopped_session",
    "converse._turn", "run.run_task",
}


def _walk(callee: str, *, failed_only: bool = False) -> set[str]:
    found = set()
    for p in sorted((ROOT / "central_command").rglob("*.py")):
        tree = ast.parse(p.read_text())
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for n in ast.walk(fn):
                if not isinstance(n, ast.Call):
                    continue
                name = getattr(n.func, "id", getattr(n.func, "attr", None))
                if name != callee:
                    continue
                if failed_only and not any(
                        k.arg == "failed" and isinstance(k.value, ast.Constant)
                        and k.value.value is True for k in n.keywords):
                    continue
                found.add(f"{p.stem}.{fn.name}")
    return found


def _section() -> str:
    """The outage doctrine section of the architecture document (it is one
    section since story 11; the no-tail table and the list of semantic
    landings live in it)."""
    text = (ROOT / "docs" / "ARCHITECTURE.md").read_text()
    start = text.index("### The outage doctrine")
    return text[start:text.index("\n## 5.", start)]


def test_the_no_tail_table_matches_the_runs_that_cannot_park():
    """Every `_run_live` caller that does not park is a row of the table, and
    every row names one (plus the consult sub-run, which is not a `_run_live`
    run)."""
    no_tail = _walk("_run_live") - _PARKING_CALLERS - {"run._run_live"}
    section = _section()
    rows = set(re.findall(r"^ {0,2}\| [^|]+ \| `([a-z_]+\.[A-Za-z_]+)` \|", section, re.M))
    assert rows - {"tools.consult_agent"} == no_tail


def test_every_failed_landing_is_named_in_the_doc():
    """`land_session(failed=True)` sites: each is named as a semantic landing
    (the source walk `tests/test_session_failure_reason.py` keeps on reasons)."""
    section = _section()
    for site in _walk("land_session", failed_only=True):
        assert f"`{site}`" in section, site
