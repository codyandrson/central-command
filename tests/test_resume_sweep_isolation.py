"""One bad row cannot end the startup resume sweep (epic-small-fixes R28).

Origin: epic-world-changed-redraft story 10, ruled 2026-10-29 (Decision 9),
built in story 11.6. `resume_sweep` drove every unit of every worklist with no
guard of its own, so a single parked orchestration whose state could not name
its task raised out of the loop — and every session behind it stayed parked
until the next restart. A parked orchestration with no `task_id` was worse: the
claim flipped it RUNNING and the lookup after it KeyError'd, leaving it RUNNING
for good.

Now every unit is driven under its own guard (the `retry_sweep._drive`
pattern): the failure is logged, listed in the result's `errors` and recorded as
one `session.resume_failed` (`after: "sweep"`), and the sweep goes on. A task-less
orchestration is landed FAILED with its reason BEFORE it is claimed.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from central_command.api import orchestration, routes
from central_command.config import settings
from central_command.db import repo
from tests.conftest import aresolve, needs_pg

pytestmark = needs_pg


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    monkeypatch.setattr(settings, "test_model", True)


def _text_model(text: str):
    from pydantic_ai.messages import ModelResponse, TextPart
    from pydantic_ai.models.function import FunctionModel

    return FunctionModel(
        lambda messages, info: ModelResponse(parts=[TextPart(content=text)])
    )


async def _settle(cond, timeout: float = 5.0):
    for _ in range(int(timeout / 0.02)):
        if await cond():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not reached before timeout")


async def _park_taskless_orchestration() -> str:
    """An AWAITING_AGENTS orchestration whose state cannot name its task."""
    sid = "sess_" + uuid.uuid4().hex[:12]
    await repo.create_running_session(sid, "orchestrator")
    await repo.park_session_awaiting_agents(
        sid, {"messages": [], "orch": {"pending": {}}})
    return sid


async def _events(kind: str, ref_id: str) -> list[dict]:
    return [e for e in await repo.list_events_of_kinds([kind])
            if e["ref_id"] == ref_id]


async def test_a_task_less_row_is_failed_loudly_and_the_good_row_still_resumes(
        monkeypatch):
    monkeypatch.setattr(
        routes, "resolve_model", aresolve(lambda *a, **k: _text_model("Done.")))
    # The good row: a real project parked on its plan review, then approved.
    project = await routes.create_and_run_task(
        "Demo project: review TASKS-12.", "Demo project", "orchestrator",
        actor="operator", background=False)
    good = project["session_id"]
    (plan,) = [i for i in await repo.list_operator_items("OPEN")
               if i["session_id"] == good and i["kind"] == "plan_review"]
    await repo.answer_operator_item(plan["id"], "", "approved")
    bad = await _park_taskless_orchestration()

    out = await orchestration.resume_sweep()

    # The sweep RETURNED, and said which unit broke and why.
    assert good in out["resumed"] and bad not in out["resumed"]
    (err,) = [e for e in out["errors"] if e["id"] == bad]
    assert err["unit"] == "session" and "task_id" in err["error"]
    # The bad row is FAILED with the reason, not left RUNNING or AWAITING_AGENTS.
    assert await repo.session_status(bad) == "FAILED"
    (failed,) = await _events("session.failed", bad)
    assert "task_id" in failed["payload"]["reason"]
    # Exactly one existing-kind failure record, tagged as the sweep's.
    (rec,) = await _events("session.resume_failed", bad)
    assert rec["payload"]["after"] == "sweep"
    assert "task_id" in rec["payload"]["error"]
    # The good one resumed for real: its loop ran on and the project finished.
    assert await _events("orchestration.resumed", good)

    async def done():
        return (await repo.get_task(project["task_id"]))["status"] == "DONE"
    await _settle(done)

    # A second sweep finds nothing left to say about the failed row.
    again = await orchestration.resume_sweep()
    assert not [e for e in again["errors"] if e["id"] == bad]
    assert len(await _events("session.resume_failed", bad)) == 1


async def test_a_raising_unit_in_any_worklist_is_recorded_and_the_rest_run(
        monkeypatch):
    ids = ["sess_" + uuid.uuid4().hex[:12] for _ in range(3)]
    monkeypatch.setattr(repo, "sessions_awaiting_agents", _no_sessions)
    monkeypatch.setattr(repo, "sessions_awaiting_resume", _sessions(ids))
    driven: list[str] = []

    async def drive(sid, model=None):
        if sid == ids[0]:
            raise RuntimeError("the first row is unreadable")
        driven.append(sid)
        return {"resumed": True}

    monkeypatch.setattr(orchestration, "resume_parked_session", drive)

    out = await orchestration.resume_sweep()

    assert driven == ids[1:]
    assert [s for s in ids[1:] if s in out["resumed"]] == ids[1:]
    (err,) = [e for e in out["errors"] if e["id"] == ids[0]]
    assert err == {"unit": "session", "id": ids[0],
                   "error": "the first row is unreadable"}
    (rec,) = await _events("session.resume_failed", ids[0])
    assert rec["payload"]["after"] == "sweep"


async def test_a_cancelled_sweep_is_not_swallowed(monkeypatch):
    """The guard catches Exception, never CancelledError: a shutdown mid-sweep
    must still stop it."""
    sid = "sess_" + uuid.uuid4().hex[:12]
    monkeypatch.setattr(repo, "sessions_awaiting_agents", _no_sessions)
    monkeypatch.setattr(repo, "sessions_awaiting_resume", _sessions([sid]))

    async def drive(sid, model=None):
        raise asyncio.CancelledError

    monkeypatch.setattr(orchestration, "resume_parked_session", drive)
    with pytest.raises(asyncio.CancelledError):
        await orchestration.resume_sweep()
    assert await _events("session.resume_failed", sid) == []


async def _no_sessions() -> list[str]:
    return []


def _sessions(ids: list[str]):
    async def _list(*a, **k) -> list[str]:
        return list(ids)
    return _list
