"""A decision simulated under the retired dry-run mode still SAYS so.

2026-08-26: the operator approved half a dozen proposals believing they had
executed. Every one ran under the dry-run executor mode and nothing on screen
said so. That mode is gone (epic-run-modes): every approval now performs its
write, and the `status` RPC carries no mode for the cockpit to banner. What
stays is HISTORY — a decision the log records as simulated keeps its mark,
read off the append-only log (a `proposal.executed` whose result text starts
"[dry-run]"), never off a mode. The cockpit hand-declares its interfaces over
untyped RPC payloads, so both facts are pinned on the BACKEND wire rather than
in a frontend test that would build its own payload and prove nothing.
"""

from __future__ import annotations

import uuid

from fastapi.encoders import jsonable_encoder

from central_command.api import nerve_gateway, routes
from central_command.config import Settings, settings
from central_command.contract import Action
from central_command.db import repo
from central_command.events import log as events
from central_command.gateway import executor
from central_command.integrations import graphiti_ingest
from tests.conftest import needs_pg


async def test_the_status_rpc_carries_no_run_mode():
    status = await nerve_gateway._dispatch("status", {}, None)
    assert "executorMode" not in status
    assert "demoMode" not in status
    assert "operatorName" in status  # the payload itself still answers


async def test_an_approval_runs_its_handler_with_no_mode_to_consult(monkeypatch):
    """No executor mode exists to consult: an approved action reaches its
    handler (faked here at the graph ingest seam) and its result carries no
    "[dry-run]" prefix."""
    assert "executor_mode" not in Settings.model_fields
    assert not hasattr(settings, "executor_mode")
    calls = []

    async def fake_enqueue(name, episode_body, source_description, group_id, *,
                           reference_time, proposal_id, scope, marker):
        calls.append(name)
        return {"verification": {"id": "v-1"}, "job": {"id": 7}}

    monkeypatch.setattr(graphiti_ingest, "enqueue", fake_enqueue)
    action = Action(capability="graph.add_episode",
                    arguments={"name": "n", "episode_body": "b", "scope": "shared",
                               "reference_time": "2026-01-01T00:00:00Z"},
                    target_ref={"system": "graphiti", "id": "central_command"},
                    reversibility="reversible")
    out = await executor.execute([action], approver="human:lee", source_refs=[])
    assert calls == ["n"]
    assert not out.result_text.startswith("[dry-run]")


async def _executed(result_text: str) -> str:
    """An EXECUTED proposal whose `proposal.executed` event, written straight
    into the log, recorded `result_text`."""
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


async def _history_row(pid: str) -> dict:
    offset = 0
    while True:
        page = await nerve_gateway._decisions_history(200, offset)
        for r in jsonable_encoder(page["proposals"]):
            if r["id"] == pid:
                return r
        assert page["hasMore"], f"{pid} not in history"
        offset += 200


@needs_pg
async def test_a_simulated_execution_says_so_on_the_decisions_wire():
    simulated = await _executed("[dry-run] would execute jira.create_issue with {}")
    real = await _executed("created TASKS-42")

    # The history list (decisions.history) and the detail pane
    # (GET /proposals/{id}) each read the flag from their own query.
    assert (await _history_row(simulated))["simulated"] is True
    assert (await _history_row(real))["simulated"] is False, "a real write must never be tagged"
    assert jsonable_encoder(await routes.get_proposal(simulated))["simulated"] is True
    assert jsonable_encoder(await routes.get_proposal(real))["simulated"] is False
