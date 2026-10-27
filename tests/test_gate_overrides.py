"""Gate overrides — the tracer bullet (epic-gate-overrides story 1, FR-4).

An override is the operator's per-agent relaxation of a capability's default
gate. A proposal it covers is STILL a proposal: the decide hook at the
task-run landing hands it to the one seam (`gateway.approve_and_execute`) with
approver `override:<id>`, so `proposal.decided` names the override and lands
before the Executor writes. No authoring path exists yet, so every override
row here is inserted by the test itself (`repo.create_gate_override`).
"""

from __future__ import annotations

import dataclasses
import json
import re
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from central_command.api import routes
from central_command.config import settings
from central_command.db import repo
from central_command.gateway import overrides
from central_command.gateway.capabilities import (
    DEFAULT_GATES, EXEMPT_WRITES, REGISTRY, REVERSIBILITIES, effective_shadow_runs,
    gated_write_names, registry_as_dict, requires_acknowledgement,
)
from central_command.gateway.steward_agreement import pair_confidence
from central_command.runtime.spike_model import make_deferred_tool_model
from tests.conftest import needs_pg

DRAFTER = "jira-expert"
OTHER = "litellm-manager"


# --- pure ---------------------------------------------------------------------


def test_every_row_declares_a_default_gate_from_the_closed_vocabulary():
    assert DEFAULT_GATES == ("human", "auditor", "none")
    for c in REGISTRY:
        assert c.default_gate in DEFAULT_GATES, c.name
        if c.default_gate != "auditor":
            assert c.default_gate == ("human" if c.gate == "human approval" else "none"), c.name
    counts = {v: sum(c.default_gate == v for c in REGISTRY) for v in DEFAULT_GATES}
    assert counts == {"human": 63, "auditor": 1, "none": 32}
    assert gated_write_names() == {c.name for c in REGISTRY if c.default_gate != "none" and c.kind == "write"}
    with pytest.raises(ValueError):
        dataclasses.replace(REGISTRY[0], default_gate="maybe")


def test_the_sandbox_writes_and_the_reads_default_to_none():
    for c in REGISTRY:
        if c.gate in ("ungated", "ungated read"):
            assert c.default_gate == "none", c.name


def test_auditor_is_pinned_one_to_one_to_the_graduated_class():
    from central_command.gateway.auditor import BULK_AUDIT_ACTION_CLASS

    covered = {c.name for c in REGISTRY if c.default_gate == "auditor"}
    assert covered == {BULK_AUDIT_ACTION_CLASS} == {"work.bulk_dismiss"}


def test_task_creation_has_no_row_and_no_exemption():
    names = {c.name for c in REGISTRY}
    assert not {"task.create", "create_task"} & names
    assert not any("task" in e for e in EXEMPT_WRITES)


def test_the_exemptions_are_a_closed_named_list_and_not_rows():
    assert EXEMPT_WRITES == ("operator graph curation", "wiki annotation (auto)")
    for c in REGISTRY:
        text = " ".join([c.name, c.description]).lower()
        assert "operator graph curation" not in text and "wiki annotation" not in text, c.name
        assert not c.name.startswith(("wiki.annotat", "operator."))


def test_the_default_gate_reaches_the_wire_on_every_row():
    wire = registry_as_dict()["capabilities"]
    assert len(wire) == len(REGISTRY)
    assert all(r["default_gate"] in DEFAULT_GATES for r in wire)
    assert {r["name"]: r["default_gate"] for r in wire}["work.bulk_dismiss"] == "auditor"


def test_get_capabilities_serves_default_gate_on_every_row():
    import asyncio

    body = asyncio.run(routes.get_capabilities())
    rows = body["capabilities"]
    assert len(rows) == len(REGISTRY)
    assert all(r["default_gate"] in DEFAULT_GATES for r in rows)
    assert json.loads(json.dumps(body))["capabilities"][0]["default_gate"]


def test_an_auditor_default_gate_resolves_like_human():
    assert overrides._DEFAULT_GATES["work.bulk_dismiss"] == "auditor"


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


# --- the server-held reversibility mark (story 5.3) ---------------------------

_TEN = {"graph.merge_nodes", "graph.delete_node", "graph.delete_edge",
        "graph.delete_episode", "jira.delete_issue", "jira.create_project",
        "confluence.create_space", "calendar.delete_event", "mail.unsubscribe",
        "mail.send"}
_LIVE_SIX = {"litellm.delete_model", "litellm.delete_team", "litellm.delete_key",
             "litellm.set_fallbacks", "litellm.delete_fallbacks",
             "litellm.apply_config_change"}


def _writes():
    return [c for c in REGISTRY if c.kind == "write"]


def test_every_write_row_has_a_mark_and_no_read_does():
    for c in REGISTRY:
        if c.kind == "write":
            assert c.reversibility in REVERSIBILITIES, c.name
        else:
            assert c.reversibility == "" and not c.acknowledge, c.name
    for bad in ("", "hard", "maybe"):
        with pytest.raises(ValueError):
            dataclasses.replace(_writes()[0], reversibility=bad)
    irr = next(c for c in _writes() if c.reversibility == "irreversible")
    with pytest.raises(ValueError):
        dataclasses.replace(irr, acknowledge=False)
    with pytest.raises(ValueError):
        dataclasses.replace(next(c for c in REGISTRY if c.kind == "read"), acknowledge=True)


def test_the_marks_follow_the_risk_text():
    for c in _writes():
        if re.search("irreversible", c.risk, re.I):
            assert c.reversibility == "irreversible", c.name
        if re.search(r"\bLIVE\b|blast radius", c.risk, re.I):
            assert c.acknowledge, c.name
    irreversible = {c.name for c in _writes() if c.reversibility == "irreversible"}
    assert irreversible == _TEN | {"mcp.tool_call"}
    assert {c.name for c in _writes() if c.acknowledge} == _TEN | {"mcp.tool_call"} | _LIVE_SIX
    # reversible-but-LIVE stays marked reversible: the mark informs, it is not the rule
    assert all(c.reversibility == "reversible" for c in _writes() if c.name in _LIVE_SIX)
    assert {c.name: c.reversibility for c in _writes()}["jira.add_comment"] == "reversible"
    assert {c.name: c.reversibility for c in _writes()}["jira.transition_issue"] == "reversible"


def test_the_acknowledgement_set_is_printed_for_the_operator(capsys):
    rows = [c for c in _writes() if c.acknowledge]
    with capsys.disabled():
        print(f"\nacknowledgement required: {len(rows)} rows "
              f"({sum(c.reversibility == 'irreversible' for c in rows)} irreversible, "
              f"{sum(c.reversibility == 'reversible' for c in rows)} LIVE/blast)")
        for c in rows:
            print(f"  {c.name:34} {c.reversibility:13} {c.risk[:70]}")
    assert len(rows) == 17


def test_requires_acknowledgement_and_the_shadow_floor():
    for name in ("mail.send", "graph.delete_node", "mcp.tool_call", *_LIVE_SIX):
        assert requires_acknowledgement(name), name
    assert not requires_acknowledgement("catalog.tag")
    assert not requires_acknowledgement("no.such.capability")
    assert requires_acknowledgement("mail.send@2")
    assert effective_shadow_runs("mail.send", 0) == 1
    assert effective_shadow_runs("litellm.apply_config_change", 0) == 1
    assert effective_shadow_runs("mail.send", 5) == 5
    assert effective_shadow_runs("catalog.tag", 0) == 0
    assert effective_shadow_runs("catalog.tag", 5) == 5


def test_the_mark_and_the_flag_reach_the_wire():
    wire = {r["name"]: r for r in registry_as_dict()["capabilities"]}
    assert wire["mail.send"]["reversibility"] == "irreversible" and wire["mail.send"]["acknowledge"] is True
    assert wire["catalog.tag"]["reversibility"] == "reversible" and wire["catalog.tag"]["acknowledge"] is False


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


# --- acknowledgement, the shadow floor and the auditor (story 5.3) -------------


def _row(agent_id: str, cap: str, *, claimed: str = "reversible") -> dict:
    return {
        "agent_id": agent_id, "status": "AWAITING_HUMAN",
        "created_at": datetime.now(timezone.utc) + timedelta(seconds=5),
        "actions": [{"capability": cap, "reversibility": claimed, "arguments": {}}],
    }


@needs_pg
async def test_an_unacknowledged_or_unshadowed_override_on_mail_send_is_never_honoured(
    granted, monkeypatch,
):
    # mail.send is not on the tracer allowlist; extending it in the test only
    # makes the refusal below the acknowledgement rule's, not the allowlist's.
    monkeypatch.setattr(overrides, "OVERRIDABLE", frozenset({"catalog.tag", "mail.send"}))
    agent = DRAFTER
    bare = await granted(agent, "mail.send")
    assert (await overrides.resolve(_row(agent, "mail.send"))).gate == "human"
    await repo.revoke_gate_override(bare["id"])
    no_shadow = await granted(agent, "mail.send", acknowledged_irreversible=True)
    d = await overrides.resolve(_row(agent, "mail.send"))
    assert d.gate == "human" and "acknowledgement" in d.reason
    await repo.revoke_gate_override(no_shadow["id"])
    no_tick = await granted(agent, "mail.send", shadow_runs=1)
    d = await overrides.resolve(_row(agent, "mail.send"))
    assert d.gate == "human" and "acknowledgement" in d.reason
    await repo.revoke_gate_override(no_tick["id"])
    both = await granted(agent, "mail.send", acknowledged_irreversible=True, shadow_runs=1)
    d = await overrides.resolve(_row(agent, "mail.send"))
    assert (d.gate, d.override_id) == ("none", both["id"])


@needs_pg
async def test_the_agent_written_reversibility_is_never_read(granted, monkeypatch):
    monkeypatch.setattr(overrides, "OVERRIDABLE", frozenset({"catalog.tag", "mail.send"}))
    agent = DRAFTER
    await granted(agent, "mail.send")
    planted = await overrides.resolve(_row(agent, "mail.send", claimed="reversible"))
    honest = await overrides.resolve(_row(agent, "mail.send", claimed="irreversible"))
    assert planted == honest and planted.gate == "human"
    # and the inverse: an agent claiming 'irreversible' does not make catalog.tag need a tick
    await granted(agent, "catalog.tag")
    assert (await overrides.resolve(_row(agent, "catalog.tag", claimed="irreversible"))).gate == "none"


@needs_pg
async def test_a_planted_requirement_on_catalog_tag_parks_then_decides(
    demo, granted, monkeypatch,
):
    # The full run on the one overridable capability, with the requirement
    # planted in the module (mail.send cannot run end to end here).
    monkeypatch.setattr(overrides, "requires_acknowledgement", lambda cap: cap == "catalog.tag")
    bare = await granted(DRAFTER)
    out = await _run(monkeypatch, DRAFTER, _tag_proposal("Tag without a tick"))
    row = await repo.load_proposal(out["proposal_id"])
    assert row["status"] == "AWAITING_HUMAN"
    assert row["override_eval"]["outcome"] == "parked" and row["override_eval"]["reason"]
    await repo.revoke_gate_override(bare["id"])
    ovr = await granted(DRAFTER, acknowledged_irreversible=True, shadow_runs=1)
    out = await _run(monkeypatch, DRAFTER, _tag_proposal("Tag with the tick"))
    row = await repo.load_proposal(out["proposal_id"])
    assert row["status"] == "EXECUTED"
    assert row["provenance"]["approver"] == f"override:{ovr['id']}"


@needs_pg
async def test_a_row_naming_the_auditor_is_never_honoured(granted):
    row = await granted("auditor", "catalog.tag")
    d = await overrides.resolve(_row("auditor", "catalog.tag"))
    assert d.gate == "human" and d.override_id is None and not d.considered
    assert row["id"]
