"""Dry run must be VISIBLE — the mode, and every decision it simulated.

2026-08-26: the operator approved half a dozen proposals believing they had
executed. Every one was a `dry_run` simulation and nothing on screen said so.
Two facts have to reach the cockpit, and the cockpit hand-declares its
interfaces over untyped RPC payloads, so both are pinned on the BACKEND wire
(`test_composer_state_carries_closed_reason_to_the_cockpit`'s rule) rather than
in a frontend test that would build its own payload and prove nothing:

* the CURRENT mode, for the header banner — `status.executorMode`;
* whether a DECIDED proposal was simulated, for the history tag. That one is
  read off the append-only log, never off the current mode: the mode changes
  and the record of what happened must not.
"""

from __future__ import annotations

import uuid

from central_command.api import nerve_gateway
from central_command.config import settings
from central_command.db import repo
from central_command.events import log as events
from tests.conftest import needs_pg


async def test_status_rpc_carries_the_executor_mode(monkeypatch):
    monkeypatch.setattr(settings, "executor_mode", "dry_run")
    assert (await nerve_gateway._dispatch("status", {}, None))["executorMode"] == "dry_run"
    monkeypatch.setattr(settings, "executor_mode", "live")
    assert (await nerve_gateway._dispatch("status", {}, None))["executorMode"] == "live"


async def _executed(result_text: str) -> str:
    """An EXECUTED proposal whose execution recorded `result_text`."""
    pid = "prop_dr_" + uuid.uuid4().hex[:10]
    sid = "sess_dr_" + uuid.uuid4().hex[:8]
    await repo.create_running_session(sid, "jira-expert")
    await repo.save_proposal(
        pid, sid, "jira-expert", "do a thing", [], [], "something happens", pid,
    )
    await repo.set_proposal_executed(pid, {"approver": "operator", "source_refs": []})
    await events.emit("proposal.executed", ref_id=pid,
                      payload={"result_text": result_text}, actor="operator")
    return pid


@needs_pg
async def test_a_simulated_execution_says_so_on_the_decisions_wire():
    simulated = await _executed("[dry-run] would execute jira.create_issue with {}")
    real = await _executed("created TASKS-42")

    rows, _ = await repo.list_decided_proposals(200, 0)
    by_id = {r["id"]: r for r in rows}
    assert by_id[simulated]["simulated"] is True
    assert by_id[real]["simulated"] is False, "a real write must never be tagged"

    # The detail pane reads the same flag from its own query.
    assert (await repo.load_proposal(simulated))["simulated"] is True
    assert (await repo.load_proposal(real))["simulated"] is False
