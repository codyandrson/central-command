"""Coaching-surface tests: the screen is computed from the record — proposal
outcomes per agent, rejection feedback from the log verbatim, reopen notes.
Assertions are on rows these tests created, never on shared-DB totals.
"""

from __future__ import annotations

import uuid

from central_command import events
from central_command.db import repo
from tests.conftest import needs_pg

pytestmark = needs_pg


async def _make_proposal(agent_id: str, status: str, intent: str) -> str:
    sid = "sess_coach_" + uuid.uuid4().hex[:8]
    pid = "prop_coach_" + uuid.uuid4().hex[:8]
    await repo.upsert_agent(agent_id, agent_id, "demo", "test charter")
    await repo.save_paused_session(sid, agent_id, {})
    await repo.save_proposal(
        proposal_id=pid, session_id=sid, agent_id=agent_id, intent=intent,
        actions=[], evidence=[], expected_effect="", idempotency_key=f"{pid}:0",
        status=status,
    )
    return pid


async def test_counts_group_by_agent_and_status():
    agent = "coach-agent-" + uuid.uuid4().hex[:6]
    await _make_proposal(agent, "EXECUTED", "a")
    await _make_proposal(agent, "EXECUTED", "b")
    await _make_proposal(agent, "REJECTED", "c")

    rows = {(r["agent_id"], r["status"]): r["count"]
            for r in await repo.proposal_counts_by_agent()}
    assert rows[(agent, "EXECUTED")] == 2
    assert rows[(agent, "REJECTED")] == 1


async def test_coaching_route_surfaces_feedback_and_reopen_notes():
    from central_command.api import routes

    agent = "coach-agent-" + uuid.uuid4().hex[:6]
    pid = await _make_proposal(agent, "REJECTED", "Set DEMO-9 due date")
    await events.emit(
        "proposal.decided", ref_id=pid,
        payload={"verdict": "rejected", "feedback": "wrong year — use 2026"},
        actor="operator",
    )
    wid = "wi_coach_" + uuid.uuid4().hex[:8]
    await events.emit("work.reopened", ref_id=wid,
                      payload={"note": "this one is actionable"}, actor="operator")

    out = await routes.coaching()
    rej = next(r for r in out["rejections"] if r["proposal_id"] == pid)
    assert rej["feedback"] == "wrong year — use 2026"
    assert rej["intent"] == "Set DEMO-9 due date"
    assert rej["agent_id"] == agent
    reo = next(r for r in out["reopens"] if r["item_id"] == wid)
    assert reo["note"] == "this one is actionable"
    assert reo["subject"] is None  # no such work item; the join tolerates it


async def test_approved_decisions_are_not_listed_as_rejections():
    from central_command.api import routes

    pid = await _make_proposal("coach-agent-x", "EXECUTED", "fine proposal")
    await events.emit(
        "proposal.decided", ref_id=pid,
        payload={"verdict": "approved", "session_id": "s"}, actor="operator",
    )
    out = await routes.coaching()
    assert not any(r["proposal_id"] == pid for r in out["rejections"])
