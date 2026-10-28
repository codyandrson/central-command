"""Story 8 of epic-outage-doctrine: a waiting approved proposal is LISTED, says
what it waits on, and can be dismissed without re-running anything.

The cockpit hand-declares its interfaces over untyped payloads, so a field the
gateway never sends is invisible to `tsc` and to every frontend test
(.claude/rules/cockpit.md). These pin, from the backend, the `waiting` block on
GET /proposals/{id} and on the `decisions.list` waiting rows, the list's own
shape (and that it is NOT the pending list), and the dismiss rule:

- dismissing a RETRY_PENDING proposal withdraws the EXECUTION: WITHDRAWN, folds
  released, drafter session closed and never resumed, `retry_state` kept, a
  distinct `proposal.execution_dismissed` and NO second `proposal.decided`;
- the task resolves FAILED with the honest count (the operator's ruling,
  2026-10-27), never DONE "no action needed";
- the transition is a SQL guard, so a dismiss racing a retry's claim cannot both
  withdraw and execute.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.encoders import jsonable_encoder

from central_command import events
from central_command.api import nerve_gateway, routes
from central_command.config import settings
from central_command.db import repo
from central_command.gateway import gateway
from tests.conftest import needs_pg

pytestmark = needs_pg

WAITING_FIELDS = {
    "dependencies", "down_since", "attempts", "next_retry", "interrupted",
    "completed_actions", "failing_despite_probe", "last_error",
}


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "executor_mode", "dry_run")


async def _parked(*, state: dict | None = None, actions: int = 2,
                  with_task: bool = False) -> dict:
    """An approved proposal parked RETRY_PENDING: the operator's decision is on
    the log, the first action landed, the second waits."""
    pid = "prop_wait_" + uuid.uuid4().hex[:10]
    sid = "sess_wait_" + uuid.uuid4().hex[:8]
    await repo.create_running_session(sid, "jira-expert")
    await repo.save_proposal(
        pid, sid, "jira-expert", "Comment on DEMO-1, then record it",
        [{"capability": "jira.add_comment" if i == 0 else "graph.add_episode",
          "arguments": {}, "target_ref": {"system": "x"}, "reversibility": "reversible"}
         for i in range(actions)],
        [], "DEMO-1 is commented", pid,
    )
    await events.emit("proposal.decided", ref_id=pid,
                      payload={"verdict": "approved", "session_id": sid},
                      actor="human:lee")
    retry_state = {
        "completed_results": ["DEMO-1 commented"], "next_action_index": 1,
        "approver": "human:lee", "attempts": 2, "failed_capability": "graph.add_episode",
        "dependencies": ["graph", "embedder"], "last_error": "Connection refused",
        "not_before": (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat(),
        **(state or {}),
    }
    await repo.park_proposal_retry(pid, retry_state)
    out = {"proposal_id": pid, "session_id": sid}
    if with_task:
        task_id = "task_" + uuid.uuid4().hex[:12]
        await repo.create_task(task_id, "wait test", "x", "jira-expert")
        await repo.start_task(task_id, sid)
        out["task_id"] = task_id
    return out


# --- the wire shape --------------------------------------------------------------------


async def test_get_proposal_carries_the_waiting_block():
    parked = await _parked()
    await repo.record_dependency_probe("embedder", False, "refused")
    down_since = (await repo.dependency_probe_states())["embedder"]["down_since"]

    row = jsonable_encoder(await routes.get_proposal(parked["proposal_id"]))

    assert row["status"] == "RETRY_PENDING"
    waiting = row["waiting"]
    assert set(waiting) == WAITING_FIELDS, set(waiting) ^ WAITING_FIELDS
    assert waiting["dependencies"] == ["graph", "embedder"]
    assert waiting["down_since"] == down_since.isoformat(), "the earliest among the down ones"
    assert waiting["attempts"] == 2
    assert waiting["next_retry"]
    assert waiting["interrupted"] is False
    assert waiting["completed_actions"] == 1
    assert waiting["failing_despite_probe"] is False
    assert waiting["last_error"] == "Connection refused"


async def test_waiting_is_null_for_every_other_status_and_down_since_for_an_up_dependency():
    parked = await _parked()
    # Nothing recorded down: a missing row is up, so there is no "down since".
    row = jsonable_encoder(await routes.get_proposal(parked["proposal_id"]))
    assert row["waiting"]["down_since"] is None

    await repo.set_proposal_status(parked["proposal_id"], "EXECUTED")
    assert jsonable_encoder(await routes.get_proposal(parked["proposal_id"]))["waiting"] is None


async def test_an_interrupted_proposal_says_so():
    parked = await _parked(state={"interrupted": True})
    row = jsonable_encoder(await routes.get_proposal(parked["proposal_id"]))
    assert row["waiting"]["interrupted"] is True


async def test_failing_despite_a_healthy_probe_comes_from_the_loud_rule():
    parked = await _parked(state={"attempts": settings.retry_loud_after_attempts})
    row = jsonable_encoder(await routes.get_proposal(parked["proposal_id"]))
    assert row["waiting"]["failing_despite_probe"] is True

    # A dependency recorded down is an outage, not a unit failing despite a
    # healthy probe.
    await repo.record_dependency_probe("graph", False, "refused")
    row = jsonable_encoder(await routes.get_proposal(parked["proposal_id"]))
    assert row["waiting"]["failing_despite_probe"] is False


async def test_decisions_list_carries_a_waiting_list_that_is_not_the_pending_list():
    parked = await _parked()
    payload = jsonable_encoder(await nerve_gateway._decisions_list())

    ids = [p["id"] for p in payload["waiting"]]
    assert parked["proposal_id"] in ids
    assert parked["proposal_id"] not in [p["id"] for p in payload["proposals"]], (
        "a decision already made does not need one: the 'needs your decision' "
        "badge counts `proposals`, never `waiting`")
    mine = next(p for p in payload["waiting"] if p["id"] == parked["proposal_id"])
    assert mine["status"] == "RETRY_PENDING"
    assert set(mine["waiting"]) == WAITING_FIELDS
    assert mine["waiting"]["dependencies"] == ["graph", "embedder"]


async def test_the_waiting_list_is_oldest_first():
    first = await _parked()
    second = await _parked()
    conn = await repo._conn()
    try:
        await conn.execute(
            "update proposal set decided_at = now() - interval '1 hour' where id = $1",
            first["proposal_id"])
    finally:
        await conn.close()
    ids = [p["id"] for p in jsonable_encoder(await nerve_gateway._decisions_list())["waiting"]]
    assert ids.index(first["proposal_id"]) < ids.index(second["proposal_id"])


async def test_the_session_wait_line_names_the_dependencies():
    parked = await _parked()
    line = await nerve_gateway.parked_proposal_wait_line(parked["proposal_id"])
    assert "graph, embedder" in line and line.startswith("paused")

    interrupted = await _parked(state={"interrupted": True})
    assert "interrupted" in await nerve_gateway.parked_proposal_wait_line(
        interrupted["proposal_id"])


# --- dismissing a waiting execution -------------------------------------------------------------


async def test_dismissing_a_waiting_proposal_withdraws_the_execution(monkeypatch):
    parked = await _parked(with_task=True)
    pid, sid, task_id = parked["proposal_id"], parked["session_id"], parked["task_id"]
    before = (await repo.load_proposal(pid))["retry_state"]

    def _boom(*a, **k):
        raise AssertionError("dismiss must not resume the agent")

    monkeypatch.setattr(gateway, "build_agent_for", _boom)

    out = await gateway.dismiss_proposal(sid, pid, "not needed any more")

    assert out["ok"] is True and out["status"] == "WITHDRAWN"
    assert out["completed_actions"] == 1 and out["actions_total"] == 2
    row = await repo.load_proposal(pid)
    assert row["status"] == "WITHDRAWN"
    assert row["retry_state"] == before, "retry_state is kept on the row for the record"
    session = await repo.get_session(sid)
    assert session["status"] == "DONE" and session["closed_reason"] == "dismissed"

    kinds = [e["kind"] for e in await repo.list_events(ref_id=pid, limit=500)]
    assert kinds.count("proposal.decided") == 1, "the decision stands; no second one"
    dismissed = [e for e in await repo.list_events(ref_id=pid, limit=500)
                 if e["kind"] == "proposal.execution_dismissed"]
    assert len(dismissed) == 1
    assert dismissed[0]["payload"]["proposal"] == pid
    assert dismissed[0]["payload"]["completed_actions"] == 1
    assert dismissed[0]["payload"]["actions_total"] == 2
    assert dismissed[0]["actor"] == "operator"

    task = await repo.get_task(task_id)
    assert task["status"] == "FAILED", "never DONE 'no action needed' for a waiting execution"
    assert task["outcome"] == (
        "operator withdrew the waiting execution; 1 of 2 actions had landed")


async def test_the_event_precedes_the_task_resolution_it_authorises():
    parked = await _parked(with_task=True)
    await gateway.dismiss_proposal(parked["session_id"], parked["proposal_id"])
    dismissed = await repo.list_events(ref_id=parked["proposal_id"], limit=500)
    failed = await repo.list_events(ref_id=parked["task_id"], limit=500)
    first = next(e for e in dismissed if e["kind"] == "proposal.execution_dismissed")
    second = next(e for e in failed if e["kind"] == "task.failed")
    assert first["id"] < second["id"]


async def test_a_dismiss_after_the_retry_claimed_it_is_refused():
    parked = await _parked()
    pid = parked["proposal_id"]
    claimed = await repo.claim_proposal_retry(pid)          # RETRY_PENDING → EXECUTING
    assert claimed is not None

    with pytest.raises(gateway.GatewayError):
        await gateway.dismiss_proposal(parked["session_id"], pid)

    assert (await repo.load_proposal(pid))["status"] == "EXECUTING", "nothing was withdrawn"
    assert "proposal.execution_dismissed" not in [
        e["kind"] for e in await repo.list_events(ref_id=pid, limit=500)]


async def test_the_sql_guard_lets_exactly_one_of_a_dismiss_and_a_claim_win():
    import asyncio

    parked = await _parked()
    pid = parked["proposal_id"]
    withdrawn, claimed = await asyncio.gather(
        repo.withdraw_proposal_if_open(pid), repo.claim_proposal_retry(pid))
    assert (withdrawn is not None) != (claimed is not None), (withdrawn, claimed)


async def test_dismissing_an_interrupted_proposal_works_the_same_way():
    parked = await _parked(state={"interrupted": True}, with_task=True)
    out = await gateway.dismiss_proposal(parked["session_id"], parked["proposal_id"])
    assert out["status"] == "WITHDRAWN"
    dismissed = [e for e in await repo.list_events(ref_id=parked["proposal_id"], limit=500)
                 if e["kind"] == "proposal.execution_dismissed"]
    assert dismissed[0]["payload"]["interrupted"] is True


async def test_the_awaiting_human_dismiss_keeps_its_outcome():
    """The other half of the rule: an AWAITING_HUMAN dismiss still resolves the
    task DONE 'no action needed' and still records a `proposal.decided`."""
    pid = "prop_wait_" + uuid.uuid4().hex[:10]
    sid = "sess_wait_" + uuid.uuid4().hex[:8]
    await repo.create_running_session(sid, "jira-expert")
    await repo.save_proposal(pid, sid, "jira-expert", "do a thing", [], [], "x", pid)
    task_id = "task_" + uuid.uuid4().hex[:12]
    await repo.create_task(task_id, "plain dismiss", "x", "jira-expert")
    await repo.start_task(task_id, sid)

    await gateway.dismiss_proposal(sid, pid, "no")

    task = await repo.get_task(task_id)
    assert task["status"] == "DONE"
    assert task["outcome"] == "operator dismissed the drafted proposal; no action needed"
    kinds = [e["kind"] for e in await repo.list_events(ref_id=pid, limit=500)]
    assert "proposal.decided" in kinds and "proposal.execution_dismissed" not in kinds


async def test_dismissing_a_waiting_proposal_releases_its_folds(monkeypatch):
    """A withdrawn execution covered nothing, so any sibling it claimed to cover
    goes back in the queue (a fold is a claim of coverage, never an outcome)."""
    released: list[tuple[str, str]] = []
    real = gateway.release_folds_for

    async def _spy(proposal_id, why):
        released.append((proposal_id, why))
        return await real(proposal_id, why)

    monkeypatch.setattr(gateway, "release_folds_for", _spy)
    parked = await _parked()
    await gateway.dismiss_proposal(parked["session_id"], parked["proposal_id"])
    assert [p for p, _ in released] == [parked["proposal_id"]]
