"""DL-218: the Executor writes the execution cursor around every action, and an
execution that cannot be shown untouched is never handed back for a fresh
approval.

While a proposal executes, `proposal.retry_state` says at every moment which
actions completed (`completed_results`, `next_action_index`) and which one is in
flight (`in_flight_index`, `in_flight_capability`). The properties pinned here:

- a process death or a cancellation mid-run parks the proposal `interrupted`
  with the completed prefix kept and the one uncertain action named, and the
  operator's retry-now runs ONLY from that action — never a completed one;
- a stop before any action started is handed back as before;
- a stop after an action may have landed is never returned to AWAITING_HUMAN,
  so a second approval cannot re-run what landed;
- a transient handler failure parks exactly as before, and the stored cursor
  and the gateway's splice agree (nothing is counted twice);
- a cursor write never touches a row that is no longer EXECUTING, and a
  completion that could not be recorded leaves its action in flight.

Every handler is `fake_world`'s, with `jira.add_comment` replaced by a recorder
whose result names its issue, so the order and the count of every call is
visible.
"""

from __future__ import annotations

import asyncio

import pytest

from central_command.api import orchestration
from central_command.config import settings
from central_command.db import repo
from central_command.gateway import executor, gateway
from tests.conftest import event_kinds as _kinds, needs_pg
from tests.fake_world import fake_world
from tests.world_moved_helpers import _Drafter, _draft

pytestmark = needs_pg

OUTAGE_TEXT = "Connection refused talking to the n8n façade"


class _Killed(BaseException):
    """Not an Exception: what a process death or a shutdown looks like to the
    gateway — no Executor verdict."""


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    monkeypatch.setattr(settings, "test_model", True)
    monkeypatch.setattr(settings, "auditor_enabled", False)
    monkeypatch.setattr(settings, "dispatch_approval_limit", 10_000)


@pytest.fixture(autouse=True)
async def settle_leftovers():
    """The startup landing is GLOBAL: land what other tests left EXECUTING or
    parked first, and settle this file's own afterwards (the pattern of
    tests/test_restart_landing.py)."""
    async def settle():
        conn = await repo._conn()
        try:
            await conn.execute("update proposal set status = 'FAILED', retry_state = null "
                               "where status in ('EXECUTING', 'RETRY_PENDING')")
        finally:
            await conn.close()

    await settle()
    yield
    await settle()


def _comment(n: int) -> dict:
    return {"capability": "jira.add_comment",
            "arguments": {"issue_key": f"DEMO-{n}", "body": f"note {n}"},
            "target_ref": {"system": "jira", "id": f"DEMO-{n}", "read_version": "etag:1"},
            "reversibility": "reversible"}


def _three() -> dict:
    return {
        "intent": "Comment on three issues",
        "actions": [_comment(1), _comment(2), _comment(3)],
        "evidence": [{"kind": "operator", "source_ref": "operator:task",
                      "locator": "test task", "claim": "Operator tasked three comments."}],
        "expected_effect": "DEMO-1, DEMO-2 and DEMO-3 each carry a note",
        "confidence": {"level": "high", "rationale": "The test states it."},
    }


def _result(n: int) -> str:
    return f"commented on DEMO-{n}"


def _world(monkeypatch, before: dict) -> list[str]:
    """Every handler faked; `jira.add_comment` records its issue and, when
    `before` names that issue, awaits it first (to raise, block or meddle).
    `before` is read at call time, so a test clears it to let a retry through."""
    fake_world(monkeypatch)
    calls: list[str] = []

    async def add_comment(args, approver, proposer):
        key = args["issue_key"]
        calls.append(key)
        hook = before.get(key)
        if hook is not None:
            await hook(args)
        return f"commented on {key}"

    monkeypatch.setitem(executor.HANDLERS, "jira.add_comment", add_comment)
    return calls


async def _drafted(monkeypatch) -> tuple[dict, _Drafter]:
    drafter = _Drafter(_three(), _three())
    row = await _draft(monkeypatch, drafter)
    assert [a["arguments"]["issue_key"] for a in row["actions"]] == ["DEMO-1", "DEMO-2", "DEMO-3"]
    return row, drafter


def _approve(row: dict, drafter: _Drafter):
    return gateway.approve_and_execute(row["session_id"], row["id"], model=drafter.model)


async def _event(ref_id: str, kind: str) -> dict:
    return [e for e in await repo.list_events(ref_id=ref_id, limit=1000)
            if e["kind"] == kind][-1]


def _assert_held_at(state: dict, index: int) -> None:
    """The interrupted record: the prefix before `index` complete, the action
    at `index` the one that may or may not have landed."""
    assert state["interrupted"] is True
    assert state["next_action_index"] == index
    assert state["completed_results"] == [_result(n) for n in range(1, index + 1)]
    assert state["in_flight_index"] == index
    assert state["in_flight_capability"] == "jira.add_comment"
    assert state["interrupted_action"] == {"index": index, "capability": "jira.add_comment"}
    assert state["failed_capability"] == "jira.add_comment"


async def _retry_now_converges(row: dict, drafter: _Drafter) -> dict:
    """The operator's retry-now on an interrupted proposal (the lever
    `outages.retry_outage(include_interrupted=True)` drives), then the final
    record."""
    out = await gateway.retry_execution(row["id"], model=drafter.model)
    assert out is not None and out["retried"] is True, out
    final = await repo.load_proposal(row["id"])
    assert final["status"] == "EXECUTED"
    state = final["retry_state"]
    assert state["completed_results"] == [_result(1), _result(2), _result(3)]
    assert state["next_action_index"] == 3
    assert "in_flight_index" not in state
    executed = await _event(row["id"], "proposal.executed")
    assert executed["payload"]["result_text"] == "; ".join(
        [_result(1), _result(2), _result(3)])
    return final


# --- a stop mid-run, in this process ---------------------------------------------


async def test_a_cancel_during_action_three_parks_interrupted_and_retry_now_runs_only_it(
    monkeypatch,
):
    row, drafter = await _drafted(monkeypatch)
    started = asyncio.Event()

    async def hang(args):
        started.set()
        await asyncio.Event().wait()  # until cancelled, like a shutdown mid-call

    before = {"DEMO-3": hang}
    calls = _world(monkeypatch, before)
    running = asyncio.create_task(_approve(row, drafter))
    await asyncio.wait_for(started.wait(), 30)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running  # the cancellation is never swallowed

    parked = await repo.load_proposal(row["id"])
    assert parked["status"] == "RETRY_PENDING"
    _assert_held_at(parked["retry_state"], 2)
    ev = await _event(row["id"], "proposal.execution_interrupted")
    assert ev["payload"]["next_action_index"] == 2
    assert ev["payload"]["completed_actions"] == [_result(1), _result(2)]
    assert ev["payload"]["in_flight_index"] == 2
    assert ev["payload"]["in_flight_capability"] == "jira.add_comment"

    # The decision is spent: a second approval cannot re-run what landed.
    with pytest.raises(gateway.GatewayError):
        await _approve(row, drafter)
    assert calls == ["DEMO-1", "DEMO-2", "DEMO-3"]

    before.clear()
    await _retry_now_converges(row, drafter)
    assert calls == ["DEMO-1", "DEMO-2", "DEMO-3", "DEMO-3"], "only action 3 ran again"


async def test_a_stop_after_a_completed_action_is_never_handed_back_for_approval(monkeypatch):
    row, drafter = await _drafted(monkeypatch)

    async def die(args):
        raise _Killed()

    calls = _world(monkeypatch, {"DEMO-2": die})
    with pytest.raises(_Killed):
        await _approve(row, drafter)

    parked = await repo.load_proposal(row["id"])
    assert parked["status"] == "RETRY_PENDING", "never AWAITING_HUMAN"
    _assert_held_at(parked["retry_state"], 1)
    assert "_Killed" in parked["retry_state"]["last_error"]
    with pytest.raises(gateway.GatewayError):
        await _approve(row, drafter)
    assert calls == ["DEMO-1", "DEMO-2"]
    # The interim hold: no sweep re-runs it.
    swept = await orchestration.retry_sweep()
    await orchestration.sweep_settle()
    assert row["id"] not in swept["retried_executions"]
    assert (await repo.load_proposal(row["id"]))["status"] == "RETRY_PENDING"


async def test_a_stop_during_action_one_resumes_at_action_one(monkeypatch):
    """The accepted residual, pinned as such: the action in flight may or may
    not have landed, and retry-now runs it again — nothing else."""
    row, drafter = await _drafted(monkeypatch)

    async def die(args):
        raise _Killed()

    before = {"DEMO-1": die}
    calls = _world(monkeypatch, before)
    with pytest.raises(_Killed):
        await _approve(row, drafter)

    parked = await repo.load_proposal(row["id"])
    assert parked["status"] == "RETRY_PENDING"
    _assert_held_at(parked["retry_state"], 0)

    before.clear()
    await _retry_now_converges(row, drafter)
    assert calls == ["DEMO-1", "DEMO-1", "DEMO-2", "DEMO-3"]


async def test_a_stop_before_any_action_started_is_handed_back_as_before(monkeypatch):
    row, drafter = await _drafted(monkeypatch)
    calls = _world(monkeypatch, {})

    async def die(actions):
        raise _Killed()

    monkeypatch.setattr(executor, "_recheck_world", die)
    with pytest.raises(_Killed):
        await _approve(row, drafter)

    back = await repo.load_proposal(row["id"])
    assert back["status"] == "AWAITING_HUMAN"
    assert back["retry_state"] is None, "no cursor was ever written"
    assert calls == []
    assert "proposal.execution_interrupted" not in await _kinds(row["id"])


# --- a process death: the startup landing --------------------------------------------


async def test_a_process_death_after_action_two_lands_from_the_stored_cursor(monkeypatch):
    row, drafter = await _drafted(monkeypatch)

    async def die(args):
        raise _Killed()

    async def dead_process(*a, **k):
        return None  # a dead process runs no handback

    before = {"DEMO-3": die}
    calls = _world(monkeypatch, before)
    monkeypatch.setattr(gateway, "_settle_stopped_execution", dead_process)
    with pytest.raises(_Killed):
        await _approve(row, drafter)

    # What the loop wrote is all a restart finds.
    left = await repo.load_proposal(row["id"])
    assert left["status"] == "EXECUTING"
    assert left["retry_state"]["completed_results"] == [_result(1), _result(2)]
    assert left["retry_state"]["next_action_index"] == 2
    assert left["retry_state"]["in_flight_index"] == 2

    landed = await orchestration.land_orphans_at_startup()
    assert row["id"] in landed["interrupted"]
    parked = await repo.load_proposal(row["id"])
    assert parked["status"] == "RETRY_PENDING"
    _assert_held_at(parked["retry_state"], 2)
    assert parked["retry_state"]["approver"], "the decision's approver"
    assert "proposal.execution_interrupted" in await _kinds(row["id"])

    before.clear()
    await _retry_now_converges(row, drafter)
    assert calls == ["DEMO-1", "DEMO-2", "DEMO-3", "DEMO-3"]


# --- a handler's verdict: the park is unchanged ---------------------------------------


async def test_a_transient_failure_parks_the_cursor_once_and_the_sweep_runs_the_rest(
    monkeypatch,
):
    row, drafter = await _drafted(monkeypatch)

    async def down(args):
        raise RuntimeError(OUTAGE_TEXT)

    before = {"DEMO-2": down}
    calls = _world(monkeypatch, before)
    out = await _approve(row, drafter)
    assert out["execution"] == "parked"
    assert out["completed_actions"] == [_result(1)]

    state = (await repo.load_proposal(row["id"]))["retry_state"]
    assert state["completed_results"] == [_result(1)], "not counted twice"
    assert state["next_action_index"] == 1
    assert state["attempts"] == 1
    assert "in_flight_index" not in state and "interrupted" not in state

    before.clear()
    retried = await gateway.retry_execution(row["id"], model=drafter.model)
    assert retried["retried"] is True
    assert calls == ["DEMO-1", "DEMO-2", "DEMO-2", "DEMO-3"]
    final = await repo.load_proposal(row["id"])
    assert final["status"] == "EXECUTED"
    assert final["retry_state"]["completed_results"] == [_result(1), _result(2), _result(3)]
    assert final["retry_state"]["attempts"] == 1, "the park's own keys are kept"
    executed = await _event(row["id"], "proposal.executed")
    assert executed["payload"]["result_text"] == "; ".join(
        [_result(1), _result(2), _result(3)])


async def test_a_retry_that_stops_lands_from_the_cursor_instead_of_staying_executing(
    monkeypatch,
):
    row, drafter = await _drafted(monkeypatch)

    async def down(args):
        raise RuntimeError(OUTAGE_TEXT)

    async def die(args):
        raise _Killed()

    before = {"DEMO-2": down}
    calls = _world(monkeypatch, before)
    await _approve(row, drafter)
    before["DEMO-2"] = die
    with pytest.raises(_Killed):
        await gateway.retry_execution(row["id"], model=drafter.model)

    parked = await repo.load_proposal(row["id"])
    assert parked["status"] == "RETRY_PENDING"
    _assert_held_at(parked["retry_state"], 1)
    assert calls == ["DEMO-1", "DEMO-2", "DEMO-2"]


async def test_a_retry_that_stops_before_anything_ran_goes_back_to_waiting(monkeypatch):
    """Nothing completed and nothing in flight: the claim goes back to
    RETRY_PENDING with its park record, for the sweep to drive again."""
    row, drafter = await _drafted(monkeypatch)

    async def down(args):
        raise RuntimeError(OUTAGE_TEXT)

    calls = _world(monkeypatch, {"DEMO-1": down})
    await _approve(row, drafter)
    from central_command.runtime import models

    async def die(model=None, **kw):
        raise _Killed()

    monkeypatch.setattr(models, "resolve_model", die)
    with pytest.raises(_Killed):
        await gateway.retry_execution(row["id"])

    back = await repo.load_proposal(row["id"])
    assert back["status"] == "RETRY_PENDING"
    assert not back["retry_state"].get("interrupted")
    assert back["retry_state"]["attempts"] == 1
    assert calls == ["DEMO-1"]


# --- the cursor writes themselves -------------------------------------------------------


async def test_a_completion_that_cannot_be_recorded_leaves_its_action_in_flight(monkeypatch):
    row, drafter = await _drafted(monkeypatch)
    calls = _world(monkeypatch, {})
    real = repo.record_action_completed

    async def flaky(proposal_id, index, result):
        if index == 1:
            raise ConnectionError("the database went away")
        return await real(proposal_id, index, result)

    monkeypatch.setattr(repo, "record_action_completed", flaky)
    with pytest.raises(executor.ExecutionCursorError) as stopped:
        await _approve(row, drafter)
    assert stopped.value.landed is True and stopped.value.index == 1

    parked = await repo.load_proposal(row["id"])
    assert parked["status"] == "RETRY_PENDING"
    _assert_held_at(parked["retry_state"], 1)
    assert "returned (commented on DEMO-2)" in parked["retry_state"]["last_error"]
    assert calls == ["DEMO-1", "DEMO-2"], "nothing ran after the unrecorded completion"


async def test_a_row_withdrawn_mid_execution_is_not_written_or_moved(monkeypatch):
    row, drafter = await _drafted(monkeypatch)
    seen: dict = {}

    async def withdraw(args):
        conn = await repo._conn()
        try:
            await conn.execute("update proposal set status = 'WITHDRAWN' where id = $1",
                               row["id"])
        finally:
            await conn.close()
        seen["state"] = (await repo.load_proposal(row["id"]))["retry_state"]

    calls = _world(monkeypatch, {"DEMO-2": withdraw})
    with pytest.raises(executor.ExecutionCursorError):
        await _approve(row, drafter)

    after = await repo.load_proposal(row["id"])
    assert after["status"] == "WITHDRAWN", "the late write flipped nothing"
    assert after["retry_state"] == seen["state"], "and wrote nothing"
    assert after["retry_state"]["in_flight_index"] == 1
    assert calls == ["DEMO-1", "DEMO-2"], "action 3 never ran"
    assert "proposal.execution_interrupted" not in await _kinds(row["id"])


async def test_the_cursor_writes_are_idempotent_by_index_and_guarded(monkeypatch):
    row, _drafter = await _drafted(monkeypatch)
    assert await repo.claim_proposal_for_execution(row["id"]) is not None

    assert await repo.record_action_started(row["id"], 0, "jira.add_comment") is True
    assert await repo.record_action_started(row["id"], 0, "jira.add_comment") is True
    assert await repo.record_action_completed(row["id"], 0, "first") is True
    assert await repo.record_action_completed(row["id"], 0, "first") is True
    state = (await repo.load_proposal(row["id"]))["retry_state"]
    assert state == {"completed_results": ["first"], "next_action_index": 1}

    with pytest.raises(repo.ExecutionCursorMismatch):
        await repo.record_action_started(row["id"], 0, "jira.add_comment")
    with pytest.raises(repo.ExecutionCursorMismatch):
        await repo.record_action_completed(row["id"], 2, "skipped one")

    await repo.set_proposal_status(row["id"], "FAILED")
    assert await repo.record_action_started(row["id"], 1, "jira.add_comment") is False
    assert await repo.record_action_completed(row["id"], 1, "late") is False
    assert (await repo.load_proposal(row["id"]))["retry_state"] == state
