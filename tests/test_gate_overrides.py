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
from pathlib import Path

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
from central_command.runtime import packs as packs_mod
from central_command.runtime.spike_model import make_deferred_tool_model
from tests.conftest import needs_pg
from tests.fake_world import fake_world

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


def test_the_tracer_allowlist_is_gone():
    assert not hasattr(overrides, "OVERRIDABLE")


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
    monkeypatch.setattr(settings, "test_model", True)
    fake_world(monkeypatch)
    monkeypatch.setattr(settings, "dispatch_approval_limit", 10_000)
    monkeypatch.setattr(settings, "auditor_enabled", False)
    yield


async def _ensure_pack(agent_id: str, capability: str, packs: list) -> None:
    """An override is carried on a live pack grant (story 5.16): give the
    agent the pack that covers the capability when it lacks one, remembering
    it in `packs` for `_drop_packs`."""
    held = await packs_mod.granted_packs(agent_id)
    if capability not in packs_mod.granted_capability_names(held):
        pack = _pack_for(capability)
        had_row = any(r["pack"] == pack for r in await repo.list_grants(agent_id))
        await repo.grant_pack(agent_id, pack)
        packs.append((agent_id, pack, had_row))


async def _drop_packs(packs: list) -> None:
    for agent_id, pack, had_row in packs:
        if had_row:
            await repo.revoke_pack(agent_id, pack, reason="test teardown")
            continue
        # A grant row this fixture created is removed outright, so no other
        # test reads it as the agent's grant history.
        conn = await repo._conn()
        try:
            await conn.execute(
                "delete from agent_grant where agent_id = $1 and pack = $2", agent_id, pack)
        finally:
            await conn.close()


@pytest.fixture()
async def granted():
    """Override rows this test creates, revoked afterwards so no live override
    outlives its test (a row is never deleted — the schema's own rule)."""
    made: list[str] = []
    packs: list[tuple[str, str, bool]] = []

    async def grant(agent_id: str, capability: str = "catalog.tag", **kw) -> dict:
        await _ensure_pack(agent_id, capability, packs)
        # The shadow period is off unless a test asks for one (story 5.7): the
        # default of 5 would park every proposal these tests expect decided.
        kw.setdefault("shadow_runs", 0)
        row = await repo.create_gate_override(agent_id, capability, **kw)
        made.append(row["id"])
        return row

    yield grant
    for oid in made:
        await repo.revoke_gate_override(oid, reason="test teardown")
    await _drop_packs(packs)


@pytest.fixture()
async def authored(monkeypatch):
    """Overrides authored through the operator's path (`overrides.author`,
    story 8), revoked afterwards. `shadow` is the setting at authoring time;
    `pack=False` leaves the agent without a covering pack (born suspended).
    `authored.track(id)` adopts a row a route or RPC call made."""
    made: list[str] = []
    packs: list[tuple[str, str, bool]] = []

    async def author(agent_id: str, capability: str = "catalog.tag", scope=None, *,
                     ack: bool = False, pack: bool = True, shadow: int = 0) -> dict:
        monkeypatch.setattr(settings, "gate_override_shadow_runs", shadow)
        if pack:
            await _ensure_pack(agent_id, capability, packs)
        out = await overrides.author(agent_id, capability, scope,
                                     acknowledge_irreversible=ack)
        made.append(out["override"]["id"])
        return out

    async def ensure_pack(agent_id: str, capability: str) -> None:
        await _ensure_pack(agent_id, capability, packs)

    author.track = made.append
    author.ensure_pack = ensure_pack
    yield author
    for oid in made:
        await repo.revoke_gate_override(oid, reason="test teardown")
    await _drop_packs(packs)


@pytest.fixture()
def exchange_mailbox(monkeypatch):
    """mail.send is offered only on an Exchange deployment (packs._offered)."""
    from central_command.integrations import exchange

    monkeypatch.setattr(exchange, "configured", lambda: True)


def _pack_for(capability: str) -> str:
    return next(name for name in packs_mod.PACKS
                if capability in packs_mod.granted_capability_names((name,)))


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
        "confidence": {"level": "high", "rationale": "The test states it."},
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
        "confidence": {"level": "high", "rationale": "The test states it."},
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
        "actions": [{"capability": cap, "reversibility": claimed,
                     "arguments": _VALID_ARGS.get(cap, {})}],
    }


# Well-formed arguments, so the shape check (story 5.16) is not the park reason.
_VALID_ARGS = {
    "mail.send": {"to": ["dana@example.com"], "subject": "s", "body": "b"},
    "catalog.tag": {"document_id": "doc_1", "tags": ["draft"]},
}


@needs_pg
async def test_an_unacknowledged_or_unshadowed_override_on_mail_send_is_never_honoured(
    granted, exchange_mailbox,
):
    agent = DRAFTER
    bare = await granted(agent, "mail.send")
    assert (await overrides.resolve(_row(agent, "mail.send"))).gate == "human"
    await repo.revoke_gate_override(bare["id"])
    # Acknowledged with no shadow period asked for: the class is floored at one
    # approval (story 5.7), and the acknowledgement alone no longer decides.
    no_shadow = await granted(agent, "mail.send", acknowledged_irreversible=True)
    assert no_shadow["shadow_runs"] == 1
    d = await overrides.resolve(_row(agent, "mail.send"))
    assert d.gate == "human" and d.shadowed and "0 of 1" in d.reason
    await repo.revoke_gate_override(no_shadow["id"])
    no_tick = await granted(agent, "mail.send", shadow_runs=1)
    d = await overrides.resolve(_row(agent, "mail.send"))
    assert d.gate == "human" and "acknowledgement" in d.reason
    await repo.revoke_gate_override(no_tick["id"])
    both = await granted(agent, "mail.send", acknowledged_irreversible=True, shadow_runs=1)
    d = await overrides.resolve(_row(agent, "mail.send"))
    assert d.gate == "human" and d.shadowed and "0 of 1" in d.reason
    assert [m.as_mark() for m in d.shadow] == [
        {"override_id": both["id"], "k": 0, "n": 1, "complete": False}]


@needs_pg
async def test_the_agent_written_reversibility_is_never_read(granted, exchange_mailbox):
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
    # The class waits its one approval like any other: the first parks shadowed,
    # the operator approves it, the second runs by the override.
    out = await _run(monkeypatch, DRAFTER, _tag_proposal("Tag with the tick"))
    row = await repo.load_proposal(out["proposal_id"])
    assert row["status"] == "AWAITING_HUMAN"
    assert row["override_eval"]["outcome"] == "shadow"
    await gateway_mod.approve_and_execute(out["session_id"], out["proposal_id"])
    out = await _run(monkeypatch, DRAFTER, _tag_proposal("Tag after one approval"))
    row = await repo.load_proposal(out["proposal_id"])
    assert row["status"] == "EXECUTED"
    assert row["provenance"]["approver"] == f"override:{ovr['id']}"


@needs_pg
async def test_a_row_naming_the_auditor_is_never_honoured(granted):
    row = await granted("auditor", "catalog.tag")
    d = await overrides.resolve(_row("auditor", "catalog.tag"))
    assert d.gate == "human" and d.override_id is None and not d.considered
    assert row["id"]


# --- scope: argument, target, schedule (story 5.4) ----------------------------

from central_command.contract import ARG_SPECS  # noqa: E402

_NO_ARG_SPECS = ["work.bulk_dismiss", "litellm.apply_config_change", "litellm.delete_key",
                 "loe.create", "loe.record_checkin", "loe.update", "skill.create"]
# Gated writes with no world target (nothing they name is a thing in the world):
# they take no target scope.
_NO_TARGET = {
    "graph.add_episode", "jira.create_filter", "jira.create_dashboard",
    "calendar.create_event", "mail.create_rule", "work.bulk_dismiss",
    "litellm.apply_config_change", "litellm.delete_key", "loe.create",
    "loe.record_checkin", "loe.update", "skill.create",
}


def _act(cap, **args):
    return {"capability": cap, "arguments": args,
            "target_ref": {"system": "x", "id": "FORGED-1"}}


def _ovr(cap, scope, **kw):
    return {"capability": cap, "scope": scope, "gate": "none", "shadow_runs": 0,
            "acknowledged_irreversible_at": None, "granted_at": datetime(2020, 1, 1, tzinfo=timezone.utc),
            "id": "govr_x", **kw}


def test_the_seven_writes_without_arg_specs_are_exactly_these():
    gated = {c.name for c in REGISTRY if c.kind == "write" and c.default_gate != "none"}
    assert sorted(n for n in gated if n not in ARG_SPECS) == sorted(_NO_ARG_SPECS)


def test_every_gated_write_declares_target_args_or_is_named_as_having_none():
    gated = [c for c in REGISTRY if c.kind == "write" and c.default_gate != "none"]
    undeclared = {c.name for c in gated if not c.target_args}
    assert undeclared == _NO_TARGET, sorted(undeclared ^ _NO_TARGET)
    for c in gated:
        assert c.name in ARG_SPECS or not c.target_args, c.name
        known = {a.split(" ")[0].rstrip("?") for a in c.arguments}
        assert set(c.target_args) <= known, (c.name, c.target_args)
        for cap in undeclared:
            assert overrides.validate_scope(cap, {"target": ["x"]}), cap
    # reads and sandbox rows declare nothing
    assert not any(c.target_args for c in REGISTRY if c.default_gate == "none")


@pytest.mark.parametrize("scope,args,expect", [
    ({"argument": {"name": "link_type", "values": ["Relates"]}},
     dict(from_key="DOE-1", to_key="DOE-2", link_type="Relates"), True),
    ({"argument": {"name": "link_type", "values": ["Relates"]}},
     dict(from_key="DOE-1", to_key="DOE-2", link_type="Blocks"), False),
    ({"argument": {"name": "link_type", "values": ["Relates"]}},
     dict(from_key="DOE-1", to_key="DOE-2"), False),
    ({"target": ["DOE"]}, dict(from_key="DOE-1", to_key="DOE-2", link_type="Relates"), True),
    ({"target": ["DOE"]}, dict(from_key="DOE-1", to_key="RIV-1", link_type="Relates"), False),
    ({"target": ["DOE", "RIV"]}, dict(from_key="DOE-1", to_key="RIV-1", link_type="Relates"), True),
    ({"target": ["DOE"], "argument": {"name": "link_type", "values": ["Blocks"]}},
     dict(from_key="DOE-1", to_key="DOE-2", link_type="Relates"), False),
    ({}, dict(from_key="RIV-1", to_key="RIV-2", link_type="Relates"), True),
    ({"regex": ".*"}, dict(from_key="DOE-1", to_key="DOE-2", link_type="Relates"), False),
])
def test_scope_covers_table(scope, args, expect):
    assert overrides.scope_covers(_ovr("jira.link_issues", scope), _act("jira.link_issues", **args), {}) is expect


def test_a_jira_project_scope_decides_doe_and_parks_riv():
    o = _ovr("jira.add_comment", {"target": ["DOE"]})
    assert overrides.scope_covers(o, _act("jira.add_comment", issue_key="DOE-1", body="x"), {})
    assert not overrides.scope_covers(o, _act("jira.add_comment", issue_key="RIV-1", body="x"), {})


def test_a_forged_target_ref_does_not_widen_a_target_scope():
    o = _ovr("jira.add_comment", {"target": ["DOE"]})
    a = _act("jira.add_comment", issue_key="RIV-1", body="x")
    a["target_ref"] = {"system": "jira", "id": "DOE-1"}
    assert not overrides.scope_covers(o, a, {})


def test_graph_create_edge_with_an_endpoint_outside_the_scope_parks():
    o = _ovr("graph.create_edge", {"target": ["n1", "n2"]})
    base = dict(name="r", fact="f", valid_at="unbounded")
    assert overrides.scope_covers(o, _act("graph.create_edge", source_uuid="n1", target_uuid="n2", **base), {})
    assert not overrides.scope_covers(o, _act("graph.create_edge", source_uuid="n1", target_uuid="n9", **base), {})


def test_a_capability_without_target_args_or_arg_specs_covers_no_scoped_action():
    assert not overrides.scope_covers(_ovr("jira.create_filter", {"target": ["x"]}),
                                      _act("jira.create_filter", name="n", jql="j"), {})
    assert not overrides.scope_covers(_ovr("loe.create", {"argument": {"name": "loe_id", "values": ["a"]}}),
                                      _act("loe.create", loe_id="a"), {})


def test_a_schedule_scope_matches_only_that_schedules_task():
    o = _ovr("catalog.tag", {"schedule": "mail-poll"})
    a = _act("catalog.tag", document_id="d", tags=["x"])
    assert overrides.scope_covers(o, a, {"task": {"created_by": "heartbeat:mail-poll"}})
    assert not overrides.scope_covers(o, a, {"task": {"created_by": "operator"}})
    assert not overrides.scope_covers(o, a, {"task": {"created_by": "heartbeat:other"}})
    assert not overrides.scope_covers(o, a, {"task": None}), "a ledger-sourced proposal has no task"
    assert not overrides.scope_covers(o, a, None)


def test_validate_scope_refuses():
    assert overrides.validate_scope("jira.link_issues", {"argument": {"name": "link_type", "values": ["Relates"]}}) == []
    assert overrides.validate_scope("jira.link_issues", {"argument": {"name": "nope", "values": ["x"]}})
    for cap in _NO_ARG_SPECS:
        assert overrides.validate_scope(cap, {"argument": {"name": "loe_id", "values": ["a"]}}), cap
    assert overrides.validate_scope("jira.link_issues", {"regex": "x"})
    assert overrides.validate_scope("jira.link_issues", {"target": []})
    assert overrides.validate_scope("catalog.tag", {"schedule": "mail-poll"}) == []
    assert overrides.validate_scope("jira.get_issue", {})


def test_scope_text_renders_each_form_in_words():
    assert overrides.scope_text(_ovr("catalog.tag", {})) == "every use"
    t = overrides.scope_text(_ovr("jira.link_issues", {
        "argument": {"name": "link_type", "values": ["Relates", "Blocks"]},
        "target": ["DOE"], "schedule": "mail-poll"}))
    assert "link_type is Relates or Blocks" in t
    assert "from_key and to_key are all in Jira project DOE" in t
    assert "mail-poll schedule" in t and t.count("; and ") == 2


def _prow(agent, actions):
    return {"agent_id": agent, "status": "AWAITING_HUMAN", "session_id": None,
            "created_at": datetime.now(timezone.utc) + timedelta(seconds=5), "actions": actions}


@needs_pg
async def test_in_scope_decides_out_of_scope_parks_and_a_partial_cover_parks_whole(granted):
    ovr = await granted(DRAFTER, "jira.add_comment", scope={"target": ["DOE"]})
    inside = _act("jira.add_comment", issue_key="DOE-1", body="x")
    outside = _act("jira.add_comment", issue_key="RIV-1", body="x")
    d = await overrides.resolve(_prow(DRAFTER, [inside]))
    assert (d.gate, d.override_id) == ("none", ovr["id"])
    d = await overrides.resolve(_prow(DRAFTER, [outside]))
    assert d.gate == "human" and d.considered and "scope" in d.reason
    d = await overrides.resolve(_prow(DRAFTER, [inside, outside]))
    assert d.gate == "human" and d.considered


@needs_pg
async def test_a_schedule_scoped_override_reads_the_task_row(granted, demo, monkeypatch):
    await granted(DRAFTER, scope={"schedule": "mail-poll"})
    out = await _run(monkeypatch, DRAFTER, _tag_proposal("Tag by the operator's task"))
    row = await repo.load_proposal(out["proposal_id"])
    assert row["status"] == "AWAITING_HUMAN" and "scope" in row["override_eval"]["reason"]
    # a ledger-sourced proposal (no session) never matches either
    d = await overrides.resolve(_prow(DRAFTER, [_act("catalog.tag", document_id="d", tags=["x"])]))
    assert d.gate == "human"


def test_graph_update_edge_target_scope_also_requires_the_edge_being_edited():
    o = _ovr("graph.update_edge", {"target": ["n1", "n2"]})
    assert not overrides.scope_covers(
        o, _act("graph.update_edge", uuid="e_other", source_uuid="n1", target_uuid="n2"), {})
    assert overrides.scope_covers(
        _ovr("graph.update_edge", {"target": ["e1", "n1", "n2"]}),
        _act("graph.update_edge", uuid="e1", source_uuid="n1", target_uuid="n2"), {})



# --- what a reviewer would have seen (story 5.16) -----------------------------


def _drow(agent, actions, evidence=None, session_id=None):
    return {"agent_id": agent, "status": "AWAITING_HUMAN", "session_id": session_id,
            "created_at": datetime.now(timezone.utc) + timedelta(seconds=5),
            "actions": actions, "evidence": evidence or []}


_DUE = {"issue_key": "DOE-1", "due_date": "2999-01-01"}


@needs_pg
async def test_a_clean_covered_proposal_is_still_decided(granted):
    ovr = await granted(DRAFTER, "jira.set_due_date")
    d = await overrides.resolve(_drow(DRAFTER, [_act("jira.set_due_date", **_DUE)]))
    assert (d.gate, d.override_id) == ("none", ovr["id"])


@needs_pg
@pytest.mark.parametrize("action,evidence,needle", [
    (_act("jira.set_due_date", issue_key="DOE-1", due_date="2020-01-01"), None,
     "no-past-due-dates"),
    (_act("jira.set_due_date", issue_key="DOE-1"), None, "malformed draft"),
    (_act("jira.set_due_date", **_DUE),
     [{"kind": "operator", "claim": "x", "claim_matches_source": False}],
     "evidence claim"),
])
async def test_a_flagged_covered_proposal_parks_with_the_reason(granted, action, evidence, needle):
    await granted(DRAFTER, "jira.set_due_date")
    d = await overrides.resolve(_drow(DRAFTER, [action], evidence))
    assert d.gate == "human" and d.considered and needle in d.reason


@needs_pg
async def test_an_unchecked_claim_does_not_park(granted):
    ovr = await granted(DRAFTER, "jira.set_due_date")
    ev = [{"kind": "operator", "claim": "x", "claim_matches_source": None},
          {"kind": "operator", "claim": "y"}]
    d = await overrides.resolve(_drow(DRAFTER, [_act("jira.set_due_date", **_DUE)], ev))
    assert (d.gate, d.override_id) == ("none", ovr["id"])


@needs_pg
async def test_a_capability_outside_the_drafters_grants_parks_suspended(granted):
    # A row inserted with no covering pack (bypassing the fixture's grant).
    row = await repo.create_gate_override(DRAFTER, "mail.report_spam")
    try:
        d = await overrides.resolve(_drow(DRAFTER, [_act("mail.report_spam", provider_uuid="u1")]))
        assert d.gate == "human" and "suspended" in d.reason
        assert d.suspended == (row["id"],)
    finally:
        await repo.revoke_gate_override(row["id"])


@needs_pg
async def test_revoking_the_pack_suspends_and_regranting_restores(granted):
    agent = OTHER
    ovr = await granted(agent, "catalog.tag")
    action = _act("catalog.tag", document_id="d1", tags=["x"])
    assert (await overrides.resolve(_drow(agent, [action]))).override_id == ovr["id"]
    assert await repo.revoke_pack(agent, "catalog-propose", reason="test")
    d = await overrides.resolve(_drow(agent, [action]))
    assert d.gate == "human" and d.suspended == (ovr["id"],)
    await repo.grant_pack(agent, "catalog-propose")
    assert (await overrides.resolve(_drow(agent, [action]))).override_id == ovr["id"]


@needs_pg
async def test_a_charter_update_whose_content_quotes_a_fake_capability_parks(granted):
    await granted(DRAFTER, "charter.update")
    bad = _act("charter.update", agent_id=DRAFTER,
               content="Use capability = 'jira.teleport_issue' for everything.")
    d = await overrides.resolve(_drow(DRAFTER, [bad]))
    assert d.gate == "human" and "charter-references-real-capabilities" in d.reason


@needs_pg
async def test_the_decide_hook_records_the_park_reason(granted, demo, monkeypatch):
    ovr = await granted(DRAFTER)
    assert await repo.revoke_pack(DRAFTER, "catalog-propose", reason="test")
    out = await _run(monkeypatch, DRAFTER, _tag_proposal("Tag after the pack went"))
    row = await repo.load_proposal(out["proposal_id"])
    assert row["status"] == "AWAITING_HUMAN"
    ev = row["override_eval"]
    assert ev["outcome"] == "parked" and "suspended" in ev["reason"]
    assert ev["suspended"] == [ovr["id"]]


# --- redraft and follow-on parks are never evaluated ---------------------------


async def _live_override_for_triage(granted):
    return await granted("inbox-triage", "jira.set_due_date")


@needs_pg
async def test_a_redraft_after_reject_parks_unevaluated(granted, demo):
    from central_command.gateway import gateway
    from central_command.runtime.run import ingest_and_propose
    from central_command.runtime.spike_model import make_redraft_model

    model = make_redraft_model()
    run = await ingest_and_propose("Dana: DEMO-1 slipped, due Aug 3.", model=model)
    await _live_override_for_triage(granted)
    out = await gateway.reject_with_feedback(
        run["session_id"], run["proposal_id"], "Wrong — make it 2026-08-10.", model=model)
    rid = out["redraft_proposal_id"]
    row = await repo.load_proposal(rid)
    assert row["override_eval"]["outcome"] == "parked"
    assert row["override_eval"]["reason"] == "redraft"
    assert await overrides.decide_on_park(rid, model=model) is None
    assert (await repo.load_proposal(rid))["status"] == "AWAITING_HUMAN"


@needs_pg
async def test_a_redraft_after_a_failed_execution_parks_unevaluated(granted, demo, monkeypatch):
    from central_command.gateway import executor, gateway
    from central_command.runtime.run import ingest_and_propose
    from central_command.runtime.spike_model import make_redraft_model

    model = make_redraft_model()
    run = await ingest_and_propose("Dana: DEMO-1 slipped, due Aug 3.", model=model)
    await _live_override_for_triage(granted)

    async def boom(*_a, **_kw):
        raise executor.ExecutionFailed("jira.set_due_date", "rejected: use 2026-08-10", [])

    monkeypatch.setattr(executor, "execute", boom)
    out = await gateway.approve_and_execute(run["session_id"], run["proposal_id"], model=model)
    rid = out.get("redraft_proposal_id")
    assert rid, out
    row = await repo.load_proposal(rid)
    assert row["override_eval"]["reason"] == "redraft"
    assert await overrides.decide_on_park(rid, model=model) is None
    assert (await repo.load_proposal(rid))["status"] == "AWAITING_HUMAN"


@needs_pg
async def test_a_follow_on_on_the_approved_leg_parks_unevaluated(granted, demo):
    from central_command.gateway import gateway
    from central_command.ingest import dispatcher, ledger
    from central_command.runtime.spike_model import make_steward_multifact_model
    from central_command.runtime.steward_profile import AGENT_ID as STEWARD

    facts = ["standardised on Postgres 17", "Dana owns the migration"]
    doc = ("Platform decision, 2026-10-01: the team standardised on Postgres 17. "
           "Dana owns the migration.")
    model = make_steward_multifact_model(facts)
    enrolled = await ledger.enroll_document("Override follow-on", doc)
    result = await dispatcher.dispatch_item(enrolled["item_id"], model=model)
    await granted(STEWARD, "graph.add_episode")
    out = await gateway.approve_and_execute(result["session_id"], result["proposal_id"], model=model)
    fid = out.get("follow_on_proposal_id")
    assert fid, out
    row = await repo.load_proposal(fid)
    assert row["override_eval"] == {**row["override_eval"], "outcome": "parked",
                                    "reason": "follow_on", "override_id": None}
    assert await overrides.decide_on_park(fid, model=model) is None
    assert row["status"] == "AWAITING_HUMAN"


# --- story 17: the hook at every landing point, and the startup sweep ----------

TRIAGE = "inbox-triage"
_BD_UUIDS = ["govr-bd-1", "govr-bd-2", "govr-bd-3"]
_BD_QUERY = "from:news@shop.example"


@pytest.fixture()
async def ledger_rows(monkeypatch):
    """Bulk-dismissal plumbing: real ledger rows (the reservation is SQL), a
    monkeypatched mail façade, and a live executor because the fold commit IS
    the effect. Rows this fixture enrolls are removed afterwards."""
    from central_command.integrations import email_facade

    monkeypatch.setattr(settings, "test_model", True)
    monkeypatch.setattr(settings, "dispatch_approval_limit", 10_000)
    monkeypatch.setattr(settings, "dispatch_concurrency", 1)
    monkeypatch.setattr(settings, "auditor_enabled", False)

    async def wipe():
        conn = await repo._conn()
        try:
            await conn.execute(
                "delete from work_item where message_id like '<gmail-msg-govr-bd-%'")
        finally:
            await conn.close()

    def facade(uuids, body="Deals inside. Unsubscribe at the bottom."):
        async def fake_list_refs(query):
            return [{"uuid": u, "conversation_id": u} for u in uuids]

        async def fake_get_message(uuid):
            return {"uuid": uuid, "conversation_id": uuid,
                    "from": "news@shop.example", "subject": "Your weekly digest",
                    "date": "2026-08-01T09:00:00+00:00", "body_text": body}

        monkeypatch.setattr(email_facade, "list_refs", fake_list_refs)
        monkeypatch.setattr(email_facade, "get_message", fake_get_message)

    async def propose(uuids=_BD_UUIDS, **kw) -> dict:
        from central_command.ingest import dispatcher, ledger
        from tests.test_bulk_dismiss_agent import _bulk_model

        for u in uuids:
            await ledger.enroll_provider_ref({"uuid": u, "conversation_id": u})
        facade(uuids, **kw)
        result = await dispatcher.dispatch_once(model=_bulk_model(_BD_QUERY))
        assert result and result.get("proposal_id"), result
        return result

    async def enroll(uuids) -> None:
        from central_command.ingest import ledger

        for u in uuids:
            await ledger.enroll_provider_ref({"uuid": u, "conversation_id": u})
        facade(uuids)

    propose.enroll = enroll
    await wipe()
    yield propose
    await wipe()


async def _fold_states(proposal_id: str) -> set[str]:
    conn = await repo._conn()
    try:
        rows = await conn.fetch(
            "select state from work_item where message_id like '<gmail-msg-govr-bd-%' "
            "and folded_into = $1", proposal_id)
    finally:
        await conn.close()
    return {r["state"] for r in rows}


@needs_pg
async def test_a_covered_proposal_with_folds_reserves_them_before_the_override_decides(
    ledger_rows, granted,
):
    ovr = await granted(TRIAGE, "work.bulk_dismiss")
    result = await ledger_rows()
    pid = result["proposal_id"]
    assert result["folded_message_ids"], "the bulk proposal folded its siblings"

    row = await repo.load_proposal(pid)
    assert row["status"] == "EXECUTED"
    assert row["provenance"]["approver"] == f"override:{ovr['id']}"
    assert result["override_eval"]["outcome"] == "decided"

    evs = await _events_for(pid)
    ids = {e["kind"]: e["id"] for e in evs}
    assert ids["work.fold_pending"] < ids["proposal.decided"] < ids["proposal.executed"], (
        "the folds are reserved before the override decides")
    # The normal tail committed them: none left pending.
    assert await _fold_states(pid) == {"FOLDED"}


@needs_pg
async def test_a_covered_proposal_with_no_folded_siblings_is_decided_too(
    ledger_rows, granted, monkeypatch,
):
    """F1: the hook sat inside the fold-only branch and missed every proposal
    without folded siblings — here a triage run on one email proposing a
    comment, with nothing folded."""
    from central_command.ingest import dispatcher

    log = fake_world(monkeypatch)  # no Jira call
    ovr = await granted(TRIAGE, "jira.add_comment")
    await ledger_rows.enroll(_BD_UUIDS[:1])
    model = make_deferred_tool_model(
        "propose_action", {"proposal": _comment_proposal("Comment on one email")})
    result = await dispatcher.dispatch_once(model=model)
    assert result and result.get("proposal_id"), result
    assert not result.get("folded_message_ids")
    row = await repo.load_proposal(result["proposal_id"])
    assert row["agent_id"] == TRIAGE
    assert row["status"] == "EXECUTED", row["override_eval"]
    assert row["provenance"]["approver"] == f"override:{ovr['id']}"
    # The fake saw the comment, decided by the override; no real Jira client ran.
    assert [(c, a) for c, _, a in log] == [("jira.add_comment", f"override:{ovr['id']}")]


@needs_pg
async def test_an_auditor_concur_that_executed_is_not_re_decided(
    ledger_rows, granted, monkeypatch,
):
    monkeypatch.setattr(settings, "auditor_enabled", True)
    monkeypatch.setattr(settings, "auditor_mode", "active")
    await granted(TRIAGE, "work.bulk_dismiss")
    result = await ledger_rows()
    pid = result["proposal_id"]
    row = await repo.load_proposal(pid)
    assert row["status"] == "EXECUTED"
    assert row["provenance"]["approver"] == "auditor"
    assert row["override_eval"] is None, "the hook saw it was no longer AWAITING_HUMAN"
    assert "override_eval" not in result
    decided = [e for e in await _events_for(pid) if e["kind"] == "proposal.decided"]
    assert len(decided) == 1 and decided[0]["actor"] == "auditor"


@needs_pg
async def test_an_auditor_challenge_that_fell_to_the_operator_is_decided_by_the_override(
    ledger_rows, granted, monkeypatch,
):
    monkeypatch.setattr(settings, "auditor_enabled", True)
    monkeypatch.setattr(settings, "auditor_mode", "active")
    ovr = await granted(TRIAGE, "work.bulk_dismiss")
    result = await ledger_rows(body="ACTION REQUIRED: confirm your delivery address today.")
    pid = result["proposal_id"]
    row = await repo.load_proposal(pid)
    assert row["status"] == "EXECUTED"
    assert row["provenance"]["approver"] == f"override:{ovr['id']}"

    evs = await _events_for(pid)
    verdict = next(e for e in evs if e["kind"] == "audit.verdict")
    decided = next(e for e in evs if e["kind"] == "proposal.decided")
    assert verdict["payload"]["verdict"] == "challenge"
    assert verdict["id"] < decided["id"], "the override hook ran after the auditor's"
    assert await _fold_states(pid) == {"FOLDED"}


async def _run_unhooked(monkeypatch, agent_id: str, proposal: dict) -> dict:
    """`_run`, with the landing hook muted — the shape a restart leaves behind
    when it kills the hook between the park and the decision."""
    async def muted(out, model=None):
        return None

    real = overrides.decide_landed
    overrides.decide_landed = muted
    try:
        return await _run(monkeypatch, agent_id, proposal)
    finally:
        overrides.decide_landed = real


@needs_pg
async def test_the_startup_sweep_decides_only_what_was_created_after_the_grant(
    demo, granted, monkeypatch,
):
    before = await _run(monkeypatch, DRAFTER, _tag_proposal("Tag parked before the grant"))
    ovr = await granted(DRAFTER)
    after = await _run_unhooked(monkeypatch, DRAFTER, _tag_proposal("Tag after the grant"))
    running = await _run_unhooked(monkeypatch, DRAFTER, _tag_proposal("Tag mid-run"))
    conn = await repo._conn()
    try:
        await conn.execute("update session set status = 'RUNNING' where id = $1",
                           running["session_id"])
    finally:
        await conn.close()

    candidates = await repo.override_sweep_candidates()
    assert after["proposal_id"] in candidates
    assert before["proposal_id"] not in candidates
    assert running["proposal_id"] not in candidates

    swept = await overrides.override_sweep()
    assert swept["decided"] >= 1
    decided = await repo.load_proposal(after["proposal_id"])
    assert decided["status"] == "EXECUTED"
    assert decided["provenance"]["approver"] == f"override:{ovr['id']}"
    for untouched in (before, running):
        row = await repo.load_proposal(untouched["proposal_id"])
        assert row["status"] == "AWAITING_HUMAN" and row["override_eval"] is None

    # A late hook on the pre-grant proposal does not decide it either.
    late = await overrides.decide_landed(before)
    assert late is not None and late["outcome"] == "parked"
    assert (await repo.load_proposal(before["proposal_id"]))["status"] == "AWAITING_HUMAN"
    conn = await repo._conn()
    try:
        await conn.execute("update session set status = 'AWAITING_HUMAN' where id = $1",
                           running["session_id"])
    finally:
        await conn.close()


@needs_pg
async def test_calling_the_hook_twice_executes_once(demo, granted, monkeypatch):
    await granted(DRAFTER)
    out = await _run_unhooked(monkeypatch, DRAFTER, _tag_proposal("Tag hooked twice"))
    first = await overrides.decide_landed(out)
    second = await overrides.decide_landed(out)
    assert first["outcome"] == "decided" and second is None
    kinds = [e["kind"] for e in await _events_for(out["proposal_id"])]
    assert kinds.count("proposal.decided") == 1
    assert kinds.count("proposal.executed") == 1


@needs_pg
async def test_a_hook_error_leaves_the_proposal_for_the_operator_loudly(
    demo, granted, monkeypatch,
):
    await granted(DRAFTER)
    out = await _run_unhooked(monkeypatch, DRAFTER, _tag_proposal("Tag whose hook breaks"))

    async def broken(_row):
        raise RuntimeError("resolver down")

    monkeypatch.setattr(overrides, "resolve", broken)
    assert await overrides.decide_landed(out) is None
    row = await repo.load_proposal(out["proposal_id"])
    assert row["status"] == "AWAITING_HUMAN"
    errors = [e for e in await _events_for(out["proposal_id"]) if e["kind"] == "override.error"]
    assert errors and errors[-1]["payload"]["stage"] == "evaluate"
    assert "resolver down" in errors[-1]["payload"]["error"]


# --- story 5: the gateway pre-check, the Executor backstop, several overrides --

from central_command.gateway import executor as executor_mod  # noqa: E402
from central_command.gateway import gateway as gateway_mod  # noqa: E402
from central_command.contract import Action  # noqa: E402


def _action_spy(monkeypatch, *, fail_at: int | None = None,
                error: str = "Connection refused talking to the n8n façade") -> list[str]:
    """Every action the Executor reaches records itself here; `fail_at` makes
    that call (0-based) raise — a transient outage by default."""
    calls: list[str] = []

    async def spy(action, approver, proposer):
        n = len(calls)
        calls.append(action.capability)
        if fail_at is not None and n == fail_at:
            raise RuntimeError(error)
        return f"done {action.capability} #{n}"

    monkeypatch.setattr(executor_mod, "_execute_action", spy)
    return calls


def _two_action_proposal(intent: str, caps=("catalog.tag", "jira.add_comment")) -> dict:
    tag, comment = _tag_proposal(intent), _comment_proposal(intent)
    by_cap = {"catalog.tag": tag["actions"][0], "jira.add_comment": comment["actions"][0]}
    actions = []
    for cap in caps:
        a = json.loads(json.dumps(by_cap[cap]))
        if cap == "catalog.tag":
            doc = f"doc_{uuid.uuid4().hex[:8]}"
            a["arguments"]["document_id"] = doc
            a["target_ref"]["id"] = doc
        actions.append(a)
    return {**tag, "actions": actions, "expected_effect": "both happen"}


async def _refused_events(pid: str) -> list[dict]:
    return [e for e in await _events_for(pid) if e["kind"] == "override.refused"]


async def _assert_handed_back(pid: str, *, decided: bool = False) -> dict:
    row = await repo.load_proposal(pid)
    assert row["status"] == "AWAITING_HUMAN"
    kinds = [e["kind"] for e in await _events_for(pid)]
    assert ("proposal.decided" in kinds) is decided
    assert "proposal.executed" not in kinds
    refused = await _refused_events(pid)
    assert refused, kinds
    return refused[-1]


def _actions_of(out_row: dict) -> list[Action]:
    return [Action(**a) for a in out_row["actions"]]


@needs_pg
@pytest.mark.parametrize("case", ["scope", "revoked", "pack_revoked", "unacknowledged"])
async def test_the_gateway_pre_check_refuses_before_any_decision(
    case, demo, granted, monkeypatch,
):
    """Override in place before the proposal (so the hook WOULD have decided
    it), then the world changes before the approval: the pre-check refuses,
    `override.refused` is logged, no `proposal.decided` exists, and the claim
    goes back to the operator."""
    kw = {"scope": {"argument": {"name": "tags", "values": ["final"]}}} if case == "scope" else {}
    if case == "unacknowledged":
        monkeypatch.setattr(overrides, "requires_acknowledgement",
                            lambda cap: cap == "catalog.tag")
    ovr = await granted(DRAFTER, **kw)
    out = await _run_unhooked(monkeypatch, DRAFTER, _tag_proposal(f"Tag pre-check {case}"))
    pid = out["proposal_id"]
    if case == "revoked":
        await repo.revoke_gate_override(ovr["id"], reason="test")
    if case == "pack_revoked":
        await repo.revoke_pack(DRAFTER, _pack_for("catalog.tag"), reason="test")

    calls = _action_spy(monkeypatch)
    with pytest.raises(overrides.OverrideRefused):
        await gateway_mod.approve_and_execute(
            out["session_id"], pid, approver=overrides.approver_for(ovr["id"]))
    refused = await _assert_handed_back(pid)
    assert refused["payload"]["override_id"] == ovr["id"]
    assert refused["payload"]["proposal_id"] == pid
    assert refused["payload"]["stage"] == "decision"
    assert refused["payload"]["reason"]
    assert calls == []

    # The operator's own approval is never affected.
    if case == "pack_revoked":
        return  # an operator approval of an ungranted capability is policy's call
    await gateway_mod.approve_and_execute(out["session_id"], pid)
    assert (await repo.load_proposal(pid))["status"] == "EXECUTED"


@needs_pg
@pytest.mark.parametrize("case", ["scope", "revoked", "pack_revoked",
                                  "unacknowledged", "not_the_drafter"])
async def test_the_executor_backstop_refuses_with_nothing_written(
    case, demo, granted, monkeypatch,
):
    kw = {"scope": {"argument": {"name": "tags", "values": ["final"]}}} if case == "scope" else {}
    ovr = await granted(DRAFTER, **kw)
    out = await _run_unhooked(monkeypatch, DRAFTER, _tag_proposal(f"Tag backstop {case}"))
    row = await repo.load_proposal(out["proposal_id"])
    proposer = DRAFTER
    if case == "revoked":
        await repo.revoke_gate_override(ovr["id"], reason="test")
    if case == "pack_revoked":
        await repo.revoke_pack(DRAFTER, _pack_for("catalog.tag"), reason="test")
    if case == "unacknowledged":
        monkeypatch.setattr(overrides, "requires_acknowledgement",
                            lambda cap: cap == "catalog.tag")
    if case == "not_the_drafter":
        proposer = OTHER

    calls = _action_spy(monkeypatch)
    with pytest.raises(executor_mod.OverrideRefused) as info:
        await executor_mod.execute(
            _actions_of(row), approver=overrides.approver_for(ovr["id"]),
            source_refs=[], proposer=proposer, proposal_id=out["proposal_id"])
    assert not isinstance(info.value, executor_mod.ExecutionFailed)
    assert calls == [], "nothing ran, nothing was written"
    # An operator approver is never checked.
    await executor_mod.execute(_actions_of(row), approver="human:operator",
                               source_refs=[], proposer=proposer)
    assert calls == ["catalog.tag"]


@needs_pg
async def test_a_revoke_between_the_decision_and_execution_hands_it_back(
    demo, granted, monkeypatch,
):
    ovr = await granted(DRAFTER)
    out = await _run_unhooked(monkeypatch, DRAFTER, _tag_proposal("Tag revoked mid-flight"))
    pid = out["proposal_id"]
    real = executor_mod.execute

    async def revoke_then_execute(*a, **kw):
        await repo.revoke_gate_override(ovr["id"], reason="revoked in the window")
        return await real(*a, **kw)

    monkeypatch.setattr(executor_mod, "execute", revoke_then_execute)
    calls = _action_spy(monkeypatch)
    record = await overrides.decide_landed(out)
    assert record["outcome"] == "parked" and "revoked" in record["reason"]
    refused = await _assert_handed_back(pid, decided=True)
    assert refused["payload"]["stage"] == "execution"
    assert calls == []
    evs = await _events_for(pid)
    ids = {e["kind"]: e["id"] for e in evs}
    assert ids["proposal.decided"] < ids["override.refused"]
    assert (await repo.load_proposal(pid))["override_eval"]["outcome"] == "parked"


@needs_pg
async def test_a_retry_with_a_completed_prefix_and_a_revoked_override_fails_without_rerunning(
    demo, granted, monkeypatch,
):
    a = await granted(DRAFTER, "catalog.tag")
    b = await granted(DRAFTER, "jira.add_comment")
    out = await _run_unhooked(monkeypatch, DRAFTER, _two_action_proposal("Two actions, retried"))
    pid = out["proposal_id"]
    calls = _action_spy(monkeypatch, fail_at=1)  # the second action hits an outage
    record = await overrides.decide_landed(out)
    assert record["outcome"] == "decided"
    row = await repo.load_proposal(pid)
    assert row["status"] == "RETRY_PENDING", row["status"]
    assert calls == ["catalog.tag", "jira.add_comment"]

    await repo.revoke_gate_override(b["id"], reason="revoked before the retry")
    calls.clear()
    model = make_deferred_tool_model("propose_action", {"proposal": _tag_proposal("x")})
    result = await gateway_mod.retry_execution(pid, model=model)
    assert result["override_refused"]
    assert calls == [], "action one never re-runs, action two never runs"
    row = await repo.load_proposal(pid)
    assert row["status"] == "FAILED"
    failed = [e for e in await _events_for(pid) if e["kind"] == "proposal.failed"]
    assert failed[-1]["payload"]["completed_actions"] == ["done catalog.tag #0"]
    refused = await _refused_events(pid)
    assert refused[-1]["payload"]["override_id"] == b["id"]
    assert refused[-1]["payload"]["stage"] == "retry"
    assert sorted(refused[-1]["payload"]["override_ids"]) == sorted([a["id"], b["id"]])


@needs_pg
async def test_a_proposal_covered_by_two_different_overrides_is_decided_by_both(
    demo, granted, monkeypatch,
):
    a = await granted(DRAFTER, "catalog.tag")
    b = await granted(DRAFTER, "jira.add_comment")
    intent = f"Tag and comment ({uuid.uuid4().hex[:6]})"
    calls = _action_spy(monkeypatch)
    out = await _run(monkeypatch, DRAFTER, _two_action_proposal(intent))
    pid = out["proposal_id"]
    combined = "override:" + "+".join(sorted([a["id"], b["id"]]))
    assert overrides.approver_for([b["id"], a["id"], b["id"]]) == combined
    assert overrides.override_ids_of(combined) == sorted([a["id"], b["id"]])
    assert overrides.override_id_of(combined) is None

    row = await repo.load_proposal(pid)
    assert row["status"] == "EXECUTED"
    assert row["provenance"]["approver"] == combined
    assert row["override_eval"]["action_map"] == {"0": a["id"], "1": b["id"]}
    assert row["override_eval"]["override_ids"] == sorted([a["id"], b["id"]])
    assert calls == ["catalog.tag", "jira.add_comment"]
    decided = [e for e in await _events_for(pid) if e["kind"] == "proposal.decided"]
    assert len(decided) == 1
    assert decided[0]["actor"] == combined
    assert decided[0]["payload"]["override_ids"] == sorted([a["id"], b["id"]])
    assert "override_id" not in decided[0]["payload"]

    # Never operator agreement — the combined approver included.
    report = pair_confidence(
        [{**row, "confidence": {"level": "high", "rationale": "sure"}}],
        [{**decided[0], "payload": {"verdict": "approved"}}],  # actor alone suffices
    )
    assert report["total_paired"] == 0 and report["awaiting_decision"] == 0
    from central_command.runtime import tools

    record = await tools.read_agent_record(None, DRAFTER)
    assert intent not in record


@needs_pg
@pytest.mark.parametrize("which", [0, 1])
async def test_revoking_either_of_two_overrides_refuses_the_whole_proposal(
    which, demo, granted, monkeypatch,
):
    pair = [await granted(DRAFTER, "catalog.tag"), await granted(DRAFTER, "jira.add_comment")]
    out = await _run_unhooked(monkeypatch, DRAFTER, _two_action_proposal(f"Revoke {which}"))
    pid = out["proposal_id"]
    # Record the evaluation the hook would have written, then revoke one.
    decision = await overrides.resolve(await repo.load_proposal(pid))
    assert decision.gate == "none" and len(decision.override_ids) == 2
    await repo.record_override_eval(pid, {
        "override_id": None, "outcome": "decided", "override_ids": list(decision.override_ids),
        "action_map": {str(k): v for k, v in decision.action_map.items()}, "at": "now",
    })
    await repo.revoke_gate_override(pair[which]["id"], reason="test")
    approver = overrides.approver_for(decision.override_ids)
    row = await repo.load_proposal(pid)

    calls = _action_spy(monkeypatch)
    with pytest.raises(executor_mod.OverrideRefused) as info:
        await executor_mod.execute(_actions_of(row), approver=approver, source_refs=[],
                                   proposer=DRAFTER, proposal_id=pid)
    assert info.value.override_id == pair[which]["id"]
    assert calls == []

    with pytest.raises(overrides.OverrideRefused):
        await gateway_mod.approve_and_execute(out["session_id"], pid, approver=approver)
    refused = await _assert_handed_back(pid)
    assert refused["payload"]["override_id"] == pair[which]["id"]
    assert calls == []


@needs_pg
async def test_one_uncovered_action_still_parks_the_whole_proposal(demo, granted, monkeypatch):
    await granted(DRAFTER, "catalog.tag")
    out = await _run(monkeypatch, DRAFTER, _two_action_proposal("Only the tag is covered"))
    row = await repo.load_proposal(out["proposal_id"])
    assert row["status"] == "AWAITING_HUMAN"
    assert row["override_eval"]["outcome"] == "parked"
    assert "jira.add_comment" in row["override_eval"]["reason"]


def test_pair_confidence_skips_a_combined_override_approver():
    proposals = [{"id": "p_two", "intent": "x", "confidence": {"level": "high"}}]
    decided = [{"id": 1, "ref_id": "p_two", "actor": "override:govr_a+govr_b",
                "payload": {"verdict": "approved", "override_ids": ["govr_a", "govr_b"]}}]
    report = pair_confidence(proposals, decided)
    assert report["pairs"] == [] and report["awaiting_decision"] == 0


@needs_pg
async def test_a_covered_proposal_on_a_reopened_work_item_parks_with_its_reason(
    demo, granted, monkeypatch,
):
    from central_command.ingest import ledger

    await granted(DRAFTER)
    out = await _run_unhooked(monkeypatch, DRAFTER, _tag_proposal("Tag on a reopened item"))
    enrolled = await ledger.enroll_provider_ref(
        {"uuid": f"govr-note-{uuid.uuid4().hex[:6]}", "conversation_id": "c"})
    message_id = enrolled["message_id"]
    conn = await repo._conn()
    try:
        # PROCESSED with the run's session, carrying the reopen note — the
        # shape a reopened, re-run item leaves (never claimable by a dispatch).
        await conn.execute(
            """update work_item set session_id = $2, state = 'PROCESSED',
                      payload = coalesce(payload, '{}'::jsonb) || '{"operator_note": "look again"}'
                where message_id = $1""", message_id, out["session_id"])
    finally:
        await conn.close()
    try:
        record = await overrides.decide_landed(out)
        assert record["outcome"] == "parked"
        assert record["reason"] == overrides.OPERATOR_HOLD_REASON
        assert (await repo.load_proposal(out["proposal_id"]))["status"] == "AWAITING_HUMAN"
    finally:
        conn = await repo._conn()
        try:
            await conn.execute("delete from work_item where message_id = $1", message_id)
        finally:
            await conn.close()


# --- story 6: the trust stamp names the override --------------------------------


def test_the_episode_stamp_names_the_override_and_a_human_one_is_unchanged():
    f = executor_mod.episode_source_description
    assert f("src", "human:operator", "proposal=p1") == (
        "src | trust=human-approved | approver=human:operator | proposal=p1")
    assert f("src", "override:govr_a", "proposal=p1") == (
        "src | trust=override:govr_a | approver=override:govr_a | proposal=p1")


@pytest.mark.parametrize("approver", ["human:operator", "override:govr_a"])
async def test_the_cutover_composes_the_executors_description(approver, monkeypatch):
    from central_command.gateway import graph_auditor

    args = {"name": "n", "episode_body": "b", "source_description": "src",
            "reference_time": "2026-05-12"}

    async def approved(_row):
        return args, approver

    sent: list[dict] = []

    async def create(**kw):
        sent.append(kw["payload"])

    monkeypatch.setattr(graph_auditor, "_approved_episode", approved)
    monkeypatch.setattr(graph_auditor.repo, "create_ingest_job", create)
    row = {"id": "v1", "proposal_id": "p1", "group_id": "g", "marker": "proposal=p1"}
    assert await graph_auditor._enqueue_from_proposal(row) is None
    assert sent[0]["source_description"] == executor_mod.episode_source_description(
        "src", approver, "proposal=p1")


def test_a_provenance_without_per_action_approvers_serialises_as_before():
    from central_command.contract import Provenance

    p = Provenance(approver="human:operator", source_refs=[],
                   executed_at=datetime.now(timezone.utc))
    assert "action_approvers" not in p.model_dump(mode="json")


@needs_pg
async def test_an_override_write_records_the_override_in_provenance_and_the_event(
    demo, granted, monkeypatch,
):
    ovr = await granted(DRAFTER, "jira.add_comment")
    out = await _run_unhooked(monkeypatch, DRAFTER, _comment_proposal("Comment stamp"))
    pid = out["proposal_id"]
    seen: list[str] = []

    async def spy(action, approver, proposer):
        seen.append(approver)
        return "ok"

    monkeypatch.setattr(executor_mod, "_execute_action", spy)
    assert (await overrides.decide_landed(out))["outcome"] == "decided"
    want = overrides.approver_for(ovr["id"])
    row = await repo.load_proposal(pid)
    assert row["provenance"]["approver"] == want and seen == [want]
    assert "action_approvers" not in row["provenance"]
    executed = [e for e in await _events_for(pid) if e["kind"] == "proposal.executed"]
    assert executed[-1]["actor"] == want


@needs_pg
async def test_under_several_overrides_each_write_is_stamped_with_its_covering_one(
    demo, granted, monkeypatch,
):
    a = await granted(DRAFTER, "catalog.tag")
    b = await granted(DRAFTER, "jira.add_comment")
    out = await _run_unhooked(monkeypatch, DRAFTER, _two_action_proposal("Two stamps"))
    pid = out["proposal_id"]
    seen: list[tuple[str, str]] = []

    async def spy(action, approver, proposer):
        seen.append((action.capability, approver))
        return "ok"

    monkeypatch.setattr(executor_mod, "_execute_action", spy)
    assert (await overrides.decide_landed(out))["outcome"] == "decided"
    assert seen == [("catalog.tag", overrides.approver_for(a["id"])),
                    ("jira.add_comment", overrides.approver_for(b["id"]))]
    row = await repo.load_proposal(pid)
    assert row["provenance"]["approver"] == overrides.approver_for([a["id"], b["id"]])
    assert row["provenance"]["action_approvers"] == {
        "0": overrides.approver_for(a["id"]), "1": overrides.approver_for(b["id"])}
    executed = [e for e in await _events_for(pid) if e["kind"] == "proposal.executed"]
    assert executed[-1]["payload"]["provenance"]["action_approvers"] == (
        row["provenance"]["action_approvers"])


@needs_pg
@pytest.mark.parametrize("approver,trust", [
    ("override:govr_a", "override:govr_a"), ("human:operator", "human-approved")])
async def test_an_executed_episode_carries_the_trust_of_its_approver(
    approver, trust, monkeypatch,
):
    from central_command.integrations import graphiti_ingest

    sent: list[str] = []

    async def enqueue(name, body, source_description, *a, **kw):
        sent.append(source_description)
        return {"job": {"id": 1}}

    monkeypatch.setattr(graphiti_ingest, "enqueue", enqueue)
    await executor_mod._graph_add_episode(
        {"name": "n", "episode_body": "b", "scope": "shared", "source_description": "s",
         "reference_time": "2026-05-12"}, approver, None)
    assert f" | trust={trust} | approver={approver} | " in sent[0]


# --- safeguard 3: the shadow period (story 5.7) --------------------------------


async def _shadow_events(kind: str, ref_id: str) -> list[dict]:
    return await repo.list_events(kind=kind, ref_id=ref_id, limit=100)


async def _approve(out: dict) -> None:
    """The operator's own approval of a parked proposal."""
    await gateway_mod.approve_and_execute(out["session_id"], out["proposal_id"])
    assert (await repo.load_proposal(out["proposal_id"]))["status"] == "EXECUTED"


def test_the_setting_defaults_to_five():
    assert settings.__class__.model_fields["gate_override_shadow_runs"].default == 5


@needs_pg
async def test_n_matching_approvals_end_the_shadow_period(demo, granted, monkeypatch):
    monkeypatch.setattr(settings, "gate_override_shadow_runs", 5)
    ovr = await granted(DRAFTER, shadow_runs=None)
    assert ovr["shadow_runs"] == 5
    for k in range(5):
        out = await _run(monkeypatch, DRAFTER, _tag_proposal(f"Shadow {k}"))
        pid = out["proposal_id"]
        row = await repo.load_proposal(pid)
        assert row["status"] == "AWAITING_HUMAN"
        assert row["override_eval"]["outcome"] == "shadow"
        assert row["override_eval"]["shadow"] == [
            {"override_id": ovr["id"], "k": k, "n": 5, "complete": False}]
        shadowed = await _shadow_events("override.shadowed", pid)
        assert len(shadowed) == 1
        assert (shadowed[0]["payload"]["k"], shadowed[0]["payload"]["n"]) == (k, 5)
        assert not await _shadow_events("override.live", ovr["id"])
        await _approve(out)
        assert await overrides.shadow_progress(ovr) == k + 1

    sixth = await _run(monkeypatch, DRAFTER, _tag_proposal("Sixth runs by the override"))
    row = await repo.load_proposal(sixth["proposal_id"])
    assert row["status"] == "EXECUTED"
    assert row["provenance"]["approver"] == f"override:{ovr['id']}"
    live = await _shadow_events("override.live", ovr["id"])
    assert len(live) == 1 and live[0]["payload"]["shadow_runs"] == 5
    decided = [e for e in await _events_for(sixth["proposal_id"])
               if e["kind"] == "proposal.decided"]
    assert live[0]["id"] < decided[0]["id"], "override.live precedes the first decision"
    # An override's own approval is never a shadow approval.
    assert await overrides.shadow_progress(ovr) == 5

    seventh = await _run(monkeypatch, DRAFTER, _tag_proposal("Seventh"))
    assert (await repo.load_proposal(seventh["proposal_id"]))["status"] == "EXECUTED"
    assert len(await _shadow_events("override.live", ovr["id"])) == 1, "emitted once"


@needs_pg
async def test_only_a_humans_approval_of_a_proposal_created_after_the_grant_counts(
    demo, granted, monkeypatch,
):
    early = await _run_unhooked(monkeypatch, DRAFTER, _tag_proposal("Created before the grant"))
    ovr = await granted(DRAFTER, shadow_runs=3)
    rejected = await _run(monkeypatch, DRAFTER, _tag_proposal("Will be dismissed"))
    assert (await repo.load_proposal(rejected["proposal_id"]))["override_eval"]["outcome"] == "shadow"
    await gateway_mod.dismiss_proposal(rejected["session_id"], rejected["proposal_id"], "no")
    assert await overrides.shadow_progress(ovr) == 0

    # created before granted_at, approved after: not in the pinned window
    await _approve(early)
    assert await overrides.shadow_progress(ovr) == 0

    ok = await _run(monkeypatch, DRAFTER, _tag_proposal("Counts"))
    await _approve(ok)
    assert await overrides.shadow_progress(ovr) == 1

    # a redraft or a follow-on is not an ordinary proposal
    redraft = await _run(monkeypatch, DRAFTER, _tag_proposal("A redraft"))
    await repo.amend_override_eval(redraft["proposal_id"], {
        "override_id": None, "outcome": "parked", "reason": "redraft", "at": "now"})
    await _approve(redraft)
    assert await overrides.shadow_progress(ovr) == 1


@needs_pg
async def test_a_proposal_evaluated_while_the_override_was_suspended_counts_nothing(
    demo, granted, monkeypatch,
):
    ovr = await granted(DRAFTER, shadow_runs=2)
    await repo.revoke_pack(DRAFTER, _pack_for("catalog.tag"), reason="test")
    out = await _run(monkeypatch, DRAFTER, _tag_proposal("Parked suspended"))
    assert ovr["id"] in (await repo.load_proposal(out["proposal_id"]))["override_eval"]["suspended"]
    await repo.grant_pack(DRAFTER, _pack_for("catalog.tag"))
    await _approve(out)
    assert await overrides.shadow_progress(ovr) == 0


@needs_pg
async def test_off_means_the_first_decides_on_a_reversible_class_but_not_an_irreversible_one(
    demo, granted, monkeypatch, exchange_mailbox,
):
    monkeypatch.setattr(settings, "gate_override_shadow_runs", 0)
    reversible = await granted(DRAFTER, shadow_runs=None)
    irreversible = await granted(DRAFTER, "mail.send", shadow_runs=None,
                                 acknowledged_irreversible=True)
    assert (reversible["shadow_runs"], irreversible["shadow_runs"]) == (0, 1)
    out = await _run(monkeypatch, DRAFTER, _tag_proposal("First, shadow off"))
    row = await repo.load_proposal(out["proposal_id"])
    assert row["status"] == "EXECUTED"
    assert row["provenance"]["approver"] == f"override:{reversible['id']}"

    d = await overrides.resolve(_row(DRAFTER, "mail.send"))
    assert d.gate == "human" and d.shadowed and "0 of 1" in d.reason


@needs_pg
async def test_a_later_change_of_the_setting_never_alters_an_authored_override(granted, monkeypatch):
    monkeypatch.setattr(settings, "gate_override_shadow_runs", 5)
    ovr = await granted(DRAFTER, shadow_runs=None)
    monkeypatch.setattr(settings, "gate_override_shadow_runs", 2)
    other = await granted(DRAFTER, "jira.add_comment", shadow_runs=None)
    monkeypatch.setattr(settings, "gate_override_shadow_runs", 0)
    assert (await repo.get_gate_override(ovr["id"]))["shadow_runs"] == 5
    assert (await repo.get_gate_override(other["id"]))["shadow_runs"] == 2
    assert (await overrides.shadow_state(await repo.get_gate_override(ovr["id"]))).n == 5


@needs_pg
async def test_the_gateway_pre_check_and_the_executor_refuse_an_override_in_its_shadow_period(
    demo, granted, monkeypatch,
):
    ovr = await granted(DRAFTER, shadow_runs=2)
    out = await _run_unhooked(monkeypatch, DRAFTER, _tag_proposal("Shadowed, forced"))
    pid = out["proposal_id"]
    row = await repo.load_proposal(pid)
    approver = overrides.approver_for(ovr["id"])

    calls = _action_spy(monkeypatch)
    with pytest.raises(executor_mod.OverrideRefused) as info:
        await executor_mod.execute(_actions_of(row), approver=approver, source_refs=[],
                                   proposer=DRAFTER, proposal_id=pid)
    assert "shadow period" in info.value.reason and "0 of 2" in info.value.reason
    with pytest.raises(overrides.OverrideRefused):
        await gateway_mod.approve_and_execute(out["session_id"], pid, approver=approver)
    refused = await _assert_handed_back(pid)
    assert "shadow period" in refused["payload"]["reason"]
    assert calls == []


@needs_pg
async def test_several_overrides_decide_only_when_every_one_is_past_its_shadow_period(
    demo, granted, monkeypatch,
):
    live = await granted(DRAFTER, "catalog.tag")                    # shadow 0
    shadowed = await granted(DRAFTER, "jira.add_comment", shadow_runs=5)
    for k in range(2):
        out = await _run(monkeypatch, DRAFTER, _comment_proposal(f"Comment {k}"))
        assert (await repo.load_proposal(out["proposal_id"]))["override_eval"]["outcome"] == "shadow"
        await _approve(out)
    assert await overrides.shadow_progress(shadowed) == 2

    out = await _run(monkeypatch, DRAFTER, _two_action_proposal("Covered by both"))
    pid = out["proposal_id"]
    row = await repo.load_proposal(pid)
    assert row["status"] == "AWAITING_HUMAN"
    assert row["override_eval"]["outcome"] == "shadow"
    marks = {m["override_id"]: m for m in row["override_eval"]["shadow"]}
    assert marks[live["id"]]["complete"] is True
    assert (marks[shadowed["id"]]["k"], marks[shadowed["id"]]["n"]) == (2, 5)
    assert f"{shadowed['id']} 2 of 5" in row["override_eval"]["reason"]
    events = await _shadow_events("override.shadowed", pid)
    assert [e["payload"]["override_id"] for e in events] == [shadowed["id"]]

    await _approve(out)
    # the approval advances the shadowed override and counts for the live one too
    assert await overrides.shadow_progress(shadowed) == 3
    assert await overrides.shadow_progress(live) == 1


@needs_pg
async def test_two_overrides_on_one_action_wait_for_the_slower(demo, granted, monkeypatch):
    everything = await granted(DRAFTER)
    narrow = await granted(DRAFTER, shadow_runs=3,
                           scope={"argument": {"name": "tags", "values": ["draft"]}})
    out = await _run(monkeypatch, DRAFTER, _tag_proposal("Both cover this tag"))
    row = await repo.load_proposal(out["proposal_id"])
    assert row["status"] == "AWAITING_HUMAN"
    assert {m["override_id"] for m in row["override_eval"]["shadow"]} == {
        everything["id"], narrow["id"]}
    assert f"{narrow['id']} 0 of 3" in row["override_eval"]["reason"]


# --- story 8: only the operator authors or revokes an override ------------------

SANDBOX_WRITE = "sandbox.exec"   # a write whose default gate is none


async def _refusal(coro) -> overrides.AuthoringRefused:
    with pytest.raises(overrides.AuthoringRefused) as e:
        await coro
    return e.value


@needs_pg
@pytest.mark.parametrize("agent_id,capability,scope,status,needle", [
    ("auditor", "catalog.tag", None, 422, "auditor"),
    (DRAFTER, "catalog.retag_everything", None, 404, "unknown capability"),
    ("no-such-agent-xyz", "catalog.tag", None, 404, "unknown agent"),
    (DRAFTER, "jira.add_comment", {"argument": {"name": "colour", "values": ["red"]}},
     422, "has no argument 'colour'"),
    (DRAFTER, SANDBOX_WRITE, None, 422, "no gate to relax"),
    (DRAFTER, "jira.list_fields", None, 422, "no gate to relax"),
    (DRAFTER, "mail.send", None, 422, "acknowledgement"),
    (DRAFTER, "litellm.apply_config_change", None, 422, "acknowledgement"),
    (DRAFTER, "work.bulk_dismiss", {"argument": {"name": "ids", "values": ["x"]}},
     422, "no argument shape"),
])
async def test_authoring_refuses(agent_id, capability, scope, status, needle):
    before = await repo.list_gate_overrides()
    e = await _refusal(overrides.author(agent_id, capability, scope))
    assert e.status == status and any(needle in p for p in e.problems), e.problems
    assert len(await repo.list_gate_overrides()) == len(before), "a refusal writes nothing"


@needs_pg
@pytest.mark.parametrize("capability", ["mail.send", "litellm.apply_config_change"])
async def test_an_acknowledgement_class_is_authored_with_the_tick_and_a_shadow_floor(
        authored, capability):
    out = await authored(DRAFTER, capability, ack=True, pack=False, shadow=0)
    row = await repo.get_gate_override(out["override"]["id"])
    assert row["acknowledged_irreversible_at"] is not None
    assert row["shadow_runs"] >= 1, "the floor holds even when the setting is 0"
    assert out["override"]["requires_acknowledgement"] is True
    assert out["override"]["state"] == "suspended" and out["warnings"], \
        "an agent with no covering pack gets it born suspended, with a warning"


@needs_pg
async def test_an_ordinary_class_copies_the_setting_and_stamps_no_tick(authored):
    out = await authored(DRAFTER, "catalog.tag", ack=True, shadow=3)
    row = await repo.get_gate_override(out["override"]["id"])
    assert row["shadow_runs"] == 3 and row["acknowledged_irreversible_at"] is None
    assert row["gate"] == "none" and row["granted_by"] == "operator"
    assert out["override"]["state"] == "shadow" and out["override"]["shadow"] == {"k": 0, "n": 3}
    assert not out["warnings"]


@needs_pg
async def test_the_auditor_gated_class_is_overridable(authored):
    out = await authored(DRAFTER, "work.bulk_dismiss", pack=False)
    assert out["override"]["capability"] == "work.bulk_dismiss"


@needs_pg
async def test_a_duplicate_live_override_is_refused_and_another_scope_is_not(authored):
    await authored(DRAFTER, "jira.add_comment", {"target": ["DOE"]})
    e = await _refusal(overrides.author(DRAFTER, "jira.add_comment", {"target": ["DOE"]}))
    assert e.status == 409
    await authored(DRAFTER, "jira.add_comment", {"target": ["RIV"]})


@needs_pg
async def test_created_and_revoked_follow_their_writes(authored):
    out = await authored(DRAFTER)
    oid = out["override"]["id"]
    created = await repo.list_events(ref_id=oid, limit=10)
    assert [e["kind"] for e in created] == ["override.created"]
    assert created[0]["payload"]["capability"] == "catalog.tag"
    assert created[0]["actor"] == "operator"
    await overrides.revoke(oid, "done with it")
    kinds = [e["kind"] for e in sorted(await repo.list_events(ref_id=oid, limit=10),
                                       key=lambda e: e["id"])]
    assert kinds == ["override.created", "override.revoked"]
    e = await _refusal(overrides.revoke(oid))
    assert e.status == 409
    e = await _refusal(overrides.revoke("govr_nothing_here"))
    assert e.status == 404


@needs_pg
async def test_a_revoke_sets_revoked_at_and_the_next_matching_proposal_parks(authored):
    out = await authored(DRAFTER, "catalog.tag")
    oid = out["override"]["id"]
    tag = _act("catalog.tag", document_id="doc_1", tags=["draft"])
    d = await overrides.resolve(_prow(DRAFTER, [tag]))
    assert (d.gate, d.override_id) == ("none", oid)
    revoked = await overrides.revoke(oid, "operator changed their mind")
    assert revoked["state"] == "revoked" and revoked["revoked_at"] is not None
    row = await repo.get_gate_override(oid)
    assert row["revoked_at"] is not None and row["revoked_reason"] == "operator changed their mind"
    assert (await overrides.resolve(_prow(DRAFTER, [tag]))).gate == "human"


@needs_pg
async def test_re_authoring_after_a_revoke_is_a_new_id_with_a_fresh_shadow(authored):
    first = (await authored(DRAFTER, "catalog.tag", shadow=2))["override"]
    await overrides.revoke(first["id"])
    second = (await authored(DRAFTER, "catalog.tag", shadow=2))["override"]
    assert second["id"] != first["id"]
    assert second["shadow"] == {"k": 0, "n": 2} and second["state"] == "shadow"
    assert (await repo.get_gate_override(first["id"]))["revoked_at"] is not None


@needs_pg
async def test_the_route_refuses_with_the_authoring_status(authored):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as e:
        await routes.author_gate_override("auditor", routes.GateOverrideIn(capability="catalog.tag"))
    assert e.value.status_code == 422
    with pytest.raises(HTTPException) as e:
        await routes.revoke_agent_gate_override(OTHER, "govr_nothing_here")
    assert e.value.status_code == 404
    mine = (await authored(DRAFTER))["override"]["id"]
    with pytest.raises(HTTPException) as e:
        await routes.revoke_agent_gate_override(OTHER, mine)
    assert e.value.status_code == 404, "an override is revoked through its own agent"


@needs_pg
async def test_the_route_and_the_rpc_author_revoke_and_list_the_same(authored):
    from fastapi.encoders import jsonable_encoder

    from central_command.api.nerve_gateway import _dispatch

    await authored.ensure_pack(DRAFTER, "catalog.tag")
    out = await routes.author_gate_override(
        DRAFTER, routes.GateOverrideIn(capability="catalog.tag", scope={}))
    authored.track(out["override"]["id"])
    rpc = await _dispatch("overrides.author", {
        "agentId": DRAFTER, "capability": "jira.add_comment",
        "scope": {"target": ["DOE"]}}, None)
    authored.track(rpc["override"]["id"])
    assert rpc["override"]["scope_text"] == "only where issue_key is in Jira project DOE"

    rest = jsonable_encoder(await routes.list_agent_gate_overrides(DRAFTER))
    via_rpc = jsonable_encoder(await _dispatch("overrides.list", {"agentId": DRAFTER}, None))
    assert rest == via_rpc
    ids = {o["id"] for o in rest["overrides"]}
    assert {out["override"]["id"], rpc["override"]["id"]} <= ids
    by_cap = jsonable_encoder(await routes.list_capability_gate_overrides("catalog.tag"))
    assert by_cap == jsonable_encoder(
        await _dispatch("overrides.list", {"capability": "catalog.tag"}, None))
    assert all(o["capability"] == "catalog.tag" for o in by_cap["overrides"])

    revoked = await _dispatch("overrides.revoke", {
        "agentId": DRAFTER, "overrideId": rpc["override"]["id"], "reason": "x"}, None)
    assert revoked["override"]["state"] == "revoked"
    assert jsonable_encoder(await _dispatch("capabilities.list", {}, None)) == \
        jsonable_encoder(await routes.get_capabilities())


@needs_pg
async def test_matches_count_the_proposals_the_override_covered(authored, demo, monkeypatch):
    out = await authored(DRAFTER, "catalog.tag", shadow=5)
    oid = out["override"]["id"]
    assert (out["override"]["matches"], out["override"]["last_match"]) == (0, None)
    ran = await _run(monkeypatch, DRAFTER, _tag_proposal("Tag while shadowed"))
    row = next(o for o in await overrides.list_for(agent_id=DRAFTER) if o["id"] == oid)
    assert row["matches"] == 1 and row["last_match"] is not None
    assert row["state"] == "shadow" and row["shadow"] == {"k": 0, "n": 5}
    assert (await repo.load_proposal(ran["proposal_id"]))["status"] == "AWAITING_HUMAN"


# --- the no-agent-path guards ---------------------------------------------------

_PKG = Path(__file__).resolve().parents[1] / "central_command"


def _callers(attr: str, owner: str | None = None, root: Path = _PKG) -> set[tuple[str, str]]:
    """Every (module path, enclosing function) in central_command/ that calls
    `<owner>.<attr>(...)` (or `<attr>(...)` when owner is None and bare)."""
    import ast

    found: set[tuple[str, str]] = set()
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        rel = path.relative_to(root).as_posix()

        def walk(node, fn):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    walk(child, child.name)
                    continue
                if isinstance(child, ast.Call):
                    f = child.func
                    if isinstance(f, ast.Attribute) and f.attr == attr and (
                            owner is None or (isinstance(f.value, ast.Name) and f.value.id == owner)):
                        found.add((rel, fn))
                    elif isinstance(f, ast.Name) and f.id == attr:
                        found.add((rel, fn))
                walk(child, fn)

        walk(tree, "<module>")
    return found


def test_the_override_writer_has_one_caller_and_it_is_the_operators():
    assert _callers("create_gate_override") == {("gateway/overrides.py", "author")}
    assert _callers("revoke_gate_override") == {("gateway/overrides.py", "revoke")}
    assert _callers("author", "overrides") == {("api/routes.py", "author_gate_override")}
    assert _callers("revoke", "overrides") == {("api/routes.py", "revoke_agent_gate_override")}
    assert _callers("author_gate_override") == {("api/nerve_gateway.py", "_dispatch")}
    assert _callers("revoke_agent_gate_override") == {("api/nerve_gateway.py", "_dispatch")}


def test_the_caller_walk_sees_a_second_caller(tmp_path):
    """The guard above fails when a second caller of the writer appears: the
    same walk over a tree holding one more caller finds it."""
    (tmp_path / "runtime").mkdir()
    (tmp_path / "runtime" / "sneak.py").write_text(
        "from central_command.db import repo\n\n"
        "async def sneak():\n    await repo.create_gate_override('x', 'catalog.tag')\n",
        encoding="utf-8")
    assert _callers("create_gate_override", root=tmp_path) == {("runtime/sneak.py", "sneak")}


def test_runtime_imports_neither_the_authoring_path_nor_the_writer():
    for path in sorted((_PKG / "runtime").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for needle in ("gateway.overrides", "gateway import overrides",
                       "create_gate_override", "revoke_gate_override", "agent_gate_override"):
            assert needle not in text, f"{path.name} mentions {needle}"


def test_no_capability_pack_tool_or_heartbeat_action_names_an_override():
    from central_command.heartbeat.actions import ACTIONS
    from central_command.runtime import tools as runtime_tools

    assert not [c.name for c in REGISTRY if "override" in c.name]
    for pack in packs_mod.PACKS.values():
        assert not [t for t in pack.tool_names if "override" in t], pack.name
        assert "gate override" not in (pack.guidance + pack.description).lower(), pack.name
    assert not [k for k in ACTIONS if "override" in k]
    assert not [n for n in dir(runtime_tools) if "gate_override" in n or n.endswith("_override")]


def test_the_generated_charter_never_mentions_an_override():
    text = packs_mod.charter_section(tuple(packs_mod.PACKS)).lower()
    for needle in ("gate override", "agent_gate_override", "override:", "govr_",
                   "shadow period", "overrides.", "relax"):
        assert needle not in text, needle


@needs_pg
async def test_a_fresh_database_holds_zero_overrides():
    """A brand-new database (schema.sql alone, as initdb loads it) seeds no
    override; the test database is recreated the same way, but other tests
    write rows, so this applies the schema to a scratch database of its own."""
    import asyncpg

    base = settings.database_url.rsplit("/", 1)[0]
    name = f"cc_fresh_{uuid.uuid4().hex[:8]}"
    admin = await asyncpg.connect(base + "/postgres")
    try:
        await admin.execute(f'create database "{name}"')
    finally:
        await admin.close()
    try:
        conn = await asyncpg.connect(f"{base}/{name}")
        try:
            await conn.execute((_PKG / "db" / "schema.sql").read_text(encoding="utf-8"))
            assert await conn.fetchval("select count(*) from agent_gate_override") == 0
        finally:
            await conn.close()
    finally:
        admin = await asyncpg.connect(base + "/postgres")
        try:
            await admin.execute(f'drop database if exists "{name}" with (force)')
        finally:
            await admin.close()
    assert "insert into agent_gate_override" not in \
        (_PKG / "db" / "schema.sql").read_text(encoding="utf-8").lower()
