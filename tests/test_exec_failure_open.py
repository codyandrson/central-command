"""A FINAL execution failure leaves the session OPEN and loud, never quietly DONE.

Operator's rule (2026-08-20): before this, `finish_decision_resume`'s failed
branch was the only `land_session` site passing no `failed=`/`reason=` — the
task landed FAILED while its session read DONE/`session.completed`, and the
sidebar showed a normal completion for a run whose write blew up. Now the
session rests AWAITING_OPERATOR with `exec_failure` set (the sidebar's red
chip), and closing it is the operator's explicit act: the ✕ on the row routes
to `_close_conversation_for_key`, which lands it FAILED with the WHY.

Epic-small-fixes story 11.4 (R27, the operator's ruling 2026-10-29): a CONVERSATION lane's
mark clears on its next successful text reply (`repo.clear_session_exec_failure`);
a task-run session keeps it for the hand-close lever.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException

from central_command.api import nerve_gateway
from central_command.db import repo
from central_command.gateway.gateway import finish_decision_resume
from tests.conftest import needs_pg

pytestmark = needs_pg


async def _insert_session(mode: str = "oneshot", status: str = "RUNNING") -> str:
    sess_id = f"sess_{uuid.uuid4().hex[:10]}"
    conn = await repo._conn()
    try:
        await conn.execute(
            "insert into session (id, agent_id, status, mode) values "
            "($1, 'jira-expert', $2, $3)",
            sess_id, status, mode,
        )
    finally:
        await conn.close()
    return sess_id


async def _finish_failed(sess_id: str) -> None:
    await finish_decision_resume(
        session_id=sess_id, agent_id="jira-expert",
        proposal_id=f"prop_{uuid.uuid4().hex[:8]}", after="failed",
        final=None, agent=None,
        failure_summary="Jira 400: no project SUPP",
    )


@pytest.mark.asyncio
async def test_a_final_execution_failure_rests_the_session_open_and_flagged():
    sess_id = await _insert_session()

    await _finish_failed(sess_id)

    row = await repo.get_session(sess_id)
    assert row["status"] == "AWAITING_OPERATOR", (
        "the session must stay OPEN — landing it DONE was the silent-close "
        "this rule exists to end"
    )
    assert row["exec_failure"] == "Jira 400: no project SUPP", (
        "the WHY rides the row: the sidebar chip and the eventual "
        "session.failed reason both read it"
    )


@pytest.mark.asyncio
async def test_the_operator_close_lever_lands_an_exec_failed_session_failed():
    sess_id = await _insert_session()
    await _finish_failed(sess_id)

    out = await nerve_gateway._close_conversation_for_key(
        f"agent:jira-expert:{sess_id}", require_explicit=True
    )

    assert out["status"] == "FAILED"
    row = await repo.get_session(sess_id)
    assert row["status"] == "FAILED", (
        "the manual close is the acknowledgment — it lands the session with "
        "the failure it was resting on, not DONE"
    )


@pytest.mark.asyncio
async def test_a_plain_run_without_an_exec_failure_still_refuses_manual_close():
    sess_id = await _insert_session(status="AWAITING_OPERATOR")

    with pytest.raises(HTTPException) as exc:
        await nerve_gateway._close_conversation_for_key(
            f"agent:jira-expert:{sess_id}", require_explicit=True
        )
    assert exc.value.status_code == 409, (
        "the carve-out is for exec-failed rests ONLY — ordinary runs still "
        "resolve through their proposals, never by hand"
    )


@pytest.mark.asyncio
async def test_clearing_the_exec_failure_is_for_conversation_lanes_only():
    lane = await _insert_session(mode="conversation", status="AWAITING_OPERATOR")
    task_run = await _insert_session(mode="oneshot", status="AWAITING_OPERATOR")
    for sid in (lane, task_run):
        await repo.mark_session_exec_failed_open(sid, "Jira 400: no project SUPP")
        await repo.clear_session_exec_failure(sid)
    assert (await repo.get_session(lane))["exec_failure"] is None
    assert (await repo.get_session(task_run))["exec_failure"] == "Jira 400: no project SUPP"
