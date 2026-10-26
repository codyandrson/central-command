"""Gate overrides — the tracer bullet (epic-gate-overrides story 1, FR-4).

An override is the operator's per-agent relaxation of a capability's default
gate. A proposal it covers is STILL a proposal: the decide hook at the
task-run landing hands it to the one seam (`gateway.approve_and_execute`) with
approver `override:<id>`, so `proposal.decided` names the override and lands
before the Executor writes. No authoring path exists yet, so every override
row here is inserted by the test itself (`repo.create_gate_override`).
"""

from __future__ import annotations

import uuid

import pytest

from central_command.api import routes
from central_command.config import settings
from central_command.db import repo
from central_command.gateway import overrides
from central_command.gateway.capabilities import REGISTRY
from central_command.gateway.steward_agreement import pair_confidence
from central_command.runtime.spike_model import make_deferred_tool_model
from tests.conftest import needs_pg

DRAFTER = "jira-expert"
OTHER = "litellm-manager"


# --- pure ---------------------------------------------------------------------


def test_default_gate_is_derived_from_the_gate_string():
    for c in REGISTRY:
        expected = "human" if c.gate == "human approval" else "none"
        assert c.default_gate == expected, c.name
    by_name = {c.name: c for c in REGISTRY}
    assert by_name["catalog.tag"].default_gate == "human"


def test_the_tracer_allowlist_is_catalog_tag_only():
    assert overrides.OVERRIDABLE == frozenset({"catalog.tag"})


def test_the_approver_string_names_the_override_and_nothing_else():
    assert overrides.approver_for("govr_abc") == "override:govr_abc"
    assert overrides.override_id_of("override:govr_abc") == "govr_abc"
    assert overrides.override_id_of("human:lee") is None
    assert overrides.override_id_of("auditor") is None
    assert overrides.override_id_of(None) is None


def test_pair_confidence_counts_an_override_approval_nowhere():
    """FR-10: an auto-handled case never counts as operator agreement — and it
    must not sit in `awaiting` forever either."""
    proposals = [{"id": "p_ovr", "intent": "tag", "confidence": {"level": "high"}},
                 {"id": "p_op", "intent": "tag", "confidence": {"level": "high"}}]
    decided = [
        {"id": 1, "ref_id": "p_ovr",
         "payload": {"verdict": "approved", "override_id": "govr_1"}},
        {"id": 2, "ref_id": "p_op", "payload": {"verdict": "approved"}},
    ]
    report = pair_confidence(proposals, decided)
    assert [p["proposal_id"] for p in report["pairs"]] == ["p_op"]
    assert report["awaiting_decision"] == 0
    assert report["by_level"]["high"]["approved"] == 1


# --- through the database -----------------------------------------------------


@pytest.fixture()
async def demo(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "executor_mode", "dry_run")
    monkeypatch.setattr(settings, "dispatch_approval_limit", 10_000)
    monkeypatch.setattr(settings, "auditor_enabled", False)
    yield


@pytest.fixture()
async def granted():
    """Override rows this test creates, revoked afterwards so no live override
    outlives its test (a row is never deleted — the schema's own rule)."""
    made: list[str] = []

    async def grant(agent_id: str, capability: str = "catalog.tag", **kw) -> dict:
        row = await repo.create_gate_override(agent_id, capability, **kw)
        made.append(row["id"])
        return row

    yield grant
    for oid in made:
        await repo.revoke_gate_override(oid, reason="test teardown")


def _tag_proposal(intent: str) -> dict:
    doc_id = f"doc_{uuid.uuid4().hex[:8]}"
    return {
        "intent": intent,
        "actions": [{
            "capability": "catalog.tag",
            "arguments": {"document_id": doc_id, "tags": ["draft"]},
            "target_ref": {"system": "catalog", "id": doc_id, "read_version": "unknown"},
            "reversibility": "reversible",
        }],
        "evidence": [{
            "kind": "operator", "source_ref": "operator:task",
            "locator": "test task", "claim": "Operator tasked a tag.",
        }],
        "expected_effect": f"{doc_id} is tagged draft",
    }


def _comment_proposal(intent: str) -> dict:
    return {
        "intent": intent,
        "actions": [{
            "capability": "jira.add_comment",
            "arguments": {"issue_key": "TASKS-12", "body": "noted"},
            "target_ref": {"system": "jira", "id": "TASKS-12", "read_version": "unknown"},
            "reversibility": "reversible",
        }],
        "evidence": [{
            "kind": "operator", "source_ref": "operator:task",
            "locator": "test task", "claim": "Operator tasked a comment.",
        }],
        "expected_effect": "TASKS-12 has a comment",
    }


async def _run(monkeypatch, agent_id: str, proposal: dict) -> dict:
    """Run a real assigned task whose agent proposes `proposal` once and
    closes out in text on resume."""
    model = make_deferred_tool_model("propose_action", {"proposal": proposal})

    async def fixed_model(**_kw):
        return model

    monkeypatch.setattr(routes, "resolve_model", fixed_model)
    task_id = f"task_govr_{uuid.uuid4().hex[:10]}"
    await repo.create_task(task_id, "override tracer", "do the thing", agent_id,
                           created_by="operator")
    out = await routes._run_assigned_task(await repo.get_task(task_id))
    assert out.get("proposal_id"), out
    return out


async def _events_for(proposal_id: str) -> list[dict]:
    return await repo.list_events(ref_id=proposal_id, limit=50)


@needs_pg
async def test_a_covered_proposal_is_decided_by_the_override_and_runs(
    demo, granted, monkeypatch,
):
    ovr = await granted(DRAFTER)
    intent = f"Tag a draft ({uuid.uuid4().hex[:6]})"
    out = await _run(monkeypatch, DRAFTER, _tag_proposal(intent))
    pid = out["proposal_id"]

    row = await repo.load_proposal(pid)
    assert row["status"] == "EXECUTED", "no operator action was needed"
    assert row["agent_id"] == DRAFTER
    assert row["provenance"]["approver"] == f"override:{ovr['id']}"
    assert row["override_eval"]["outcome"] == "decided"
    assert row["override_eval"]["override_id"] == ovr["id"]
    assert out["override_eval"]["outcome"] == "decided"

    evs = await _events_for(pid)
    decided = [e for e in evs if e["kind"] == "proposal.decided"]
    executed = [e for e in evs if e["kind"] == "proposal.executed"]
    assert len(decided) == 1 and len(executed) == 1
    assert decided[0]["payload"]["verdict"] == "approved"
    assert decided[0]["payload"]["override_id"] == ovr["id"]
    assert decided[0]["actor"] == f"override:{ovr['id']}"
    assert executed[0]["actor"] == f"override:{ovr['id']}"
    assert decided[0]["id"] < executed[0]["id"], "the decision precedes the write"

    # The task's own landing ran: the closing turn resolved it.
    task = await repo.get_task(out["task_id"])
    assert task["status"] == "DONE"

    # Never operator agreement: neither paired nor awaiting...
    report = pair_confidence(
        [{**row, "confidence": {"level": "high", "rationale": "sure"}}], decided,
    )
    assert report["total_paired"] == 0 and report["awaiting_decision"] == 0

    # ...and the coaching reader does not show it as a verdict.
    from central_command.runtime import tools

    record = await tools.read_agent_record(None, DRAFTER)
    assert f"event:{decided[0]['id']}" not in record
    assert intent not in record


@needs_pg
async def test_another_agents_proposal_on_the_same_capability_parks(
    demo, granted, monkeypatch,
):
    await granted(DRAFTER)
    out = await _run(monkeypatch, OTHER, _tag_proposal("Tag by another agent"))
    row = await repo.load_proposal(out["proposal_id"])
    assert row["status"] == "AWAITING_HUMAN"
    assert row["override_eval"] is None, "no override of the drafter was in play"
    kinds = [e["kind"] for e in await _events_for(out["proposal_id"])]
    assert "proposal.decided" not in kinds


@needs_pg
async def test_the_drafters_other_capability_parks(demo, granted, monkeypatch):
    await granted(DRAFTER)
    out = await _run(monkeypatch, DRAFTER, _comment_proposal("Comment by the drafter"))
    row = await repo.load_proposal(out["proposal_id"])
    assert row["status"] == "AWAITING_HUMAN"
    assert row["override_eval"] is None


@needs_pg
async def test_a_revoked_override_decides_nothing(demo, granted, monkeypatch):
    ovr = await granted(DRAFTER)
    assert await repo.revoke_gate_override(ovr["id"], reason="operator took it back")
    assert not await repo.revoke_gate_override(ovr["id"]), "revoking twice is a no-op"
    assert await repo.live_gate_overrides(DRAFTER, "catalog.tag") == []

    out = await _run(monkeypatch, DRAFTER, _tag_proposal("Tag after a revoke"))
    row = await repo.load_proposal(out["proposal_id"])
    assert row["status"] == "AWAITING_HUMAN"

    # Revocation is a timestamp; the row is still there.
    conn = await repo._conn()
    try:
        kept = await conn.fetchrow(
            "select revoked_at, revoked_reason from agent_gate_override where id = $1",
            ovr["id"])
    finally:
        await conn.close()
    assert kept["revoked_at"] is not None
    assert kept["revoked_reason"] == "operator took it back"


@needs_pg
async def test_a_proposal_parked_before_the_override_is_never_decided_by_it(
    demo, granted, monkeypatch,
):
    """Parked at T0, override granted at T1, hook run at T2: the operator
    could already see this item, so the override never decides it."""
    out = await _run(monkeypatch, DRAFTER, _tag_proposal("Tag parked at T0"))
    pid = out["proposal_id"]
    assert (await repo.load_proposal(pid))["status"] == "AWAITING_HUMAN"

    await granted(DRAFTER)  # T1
    record = await overrides.decide_on_park(pid)  # T2
    assert record is not None and record["outcome"] == "parked"
    assert record["override_id"] is None
    assert "predates" in record["reason"]

    row = await repo.load_proposal(pid)
    assert row["status"] == "AWAITING_HUMAN"
    assert row["override_eval"]["outcome"] == "parked"
    assert "proposal.decided" not in [e["kind"] for e in await _events_for(pid)]


@needs_pg
async def test_the_evaluation_is_recorded_once(demo, granted, monkeypatch):
    """Compare-and-set where null: a second call of the hook does nothing."""
    out = await _run(monkeypatch, DRAFTER, _tag_proposal("Tag evaluated once"))
    pid = out["proposal_id"]
    await granted(DRAFTER)
    first = await overrides.decide_on_park(pid)
    assert first is not None
    assert await overrides.decide_on_park(pid) is None
    assert not await repo.record_override_eval(pid, {"outcome": "decided"})
    assert (await repo.load_proposal(pid))["override_eval"] == first


@needs_pg
async def test_an_operator_approval_carries_no_override_id(demo, monkeypatch):
    from central_command.gateway import gateway

    out = await _run(monkeypatch, OTHER, _tag_proposal("Tag the operator approves"))
    await gateway.approve_and_execute(out["session_id"], out["proposal_id"])
    decided = [e for e in await _events_for(out["proposal_id"])
               if e["kind"] == "proposal.decided"]
    assert decided and "override_id" not in decided[0]["payload"]
    assert decided[0]["actor"].startswith("human:")
