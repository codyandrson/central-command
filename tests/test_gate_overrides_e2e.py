"""Gate overrides — the closing end-to-end suite (epic-gate-overrides story 14).

The deterministic demo model drives real task runs, chat turns and ingest
claims; the operator's side goes through the real cockpit RPC
(`nerve_gateway._dispatch`) and the real gateway. Nothing here calls a model.
Fixtures and builders are the ones tests/test_gate_overrides.py defines.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException

from central_command.api import nerve_gateway, routes
from central_command.config import settings
from central_command.db import repo
from central_command.gateway import executor as executor_mod
from central_command.gateway import gateway as gateway_mod
from central_command.gateway import overrides
from central_command.gateway.steward_agreement import pair_confidence
from central_command.runtime.spike_model import make_deferred_tool_model
from tests.conftest import needs_pg
from tests.test_gate_overrides import (  # noqa: F401 — fixtures and builders
    DRAFTER, OTHER, TRIAGE, _action_spy, _actions_of, _approve, _comment_proposal,
    _events_for, _run, _run_unhooked, _tag_proposal, _two_action_proposal,
    authored, demo, exchange_mailbox, granted,
)

DRAFT_ONLY = {"argument": {"name": "tags", "values": ["draft"]}}
CONFIDENT = {"level": "high", "rationale": "sure"}


async def _rpc(method: str, params: dict) -> dict:
    return await nerve_gateway._dispatch(method, params, None)


async def _author(authored, monkeypatch, agent_id: str, capability: str = "catalog.tag",
                  scope: dict | None = None, *, ack: bool = False, shadow: int = 0,
                  pack: bool = True) -> dict:
    """Author an override through the operator's RPC; `authored` revokes it
    (and drops any pack it lent) afterwards."""
    monkeypatch.setattr(settings, "gate_override_shadow_runs", shadow)
    if pack:
        await authored.ensure_pack(agent_id, capability)
    out = await _rpc("overrides.author", {
        "agentId": agent_id, "capability": capability, "scope": scope or {},
        "acknowledgeIrreversible": ack})
    authored.track(out["override"]["id"])
    return out


def _kinds(events: list[dict]) -> list[str]:
    return [e["kind"] for e in events]


async def _inbox_ids() -> set[str]:
    return {p["id"] for p in (await nerve_gateway._decisions_list())["proposals"]}


async def _assert_parked(out: dict, *, evaluated: bool = True) -> dict:
    row = await repo.load_proposal(out["proposal_id"])
    assert row["status"] == "AWAITING_HUMAN", row["override_eval"]
    assert out["proposal_id"] in await _inbox_ids()
    kinds = _kinds(await _events_for(out["proposal_id"]))
    assert "proposal.decided" not in kinds and "proposal.executed" not in kinds
    if evaluated:
        assert row["override_eval"]["outcome"] == "parked", row["override_eval"]
    return row


# --- the lifecycle ------------------------------------------------------------


@needs_pg
async def test_the_lifecycle_five_shadowed_approvals_then_the_override_decides(
    demo, authored, monkeypatch,
):
    start = await repo.latest_event_id()
    out = await _author(authored, monkeypatch, DRAFTER, scope=DRAFT_ONLY, shadow=5)
    ovr = out["override"]
    oid = ovr["id"]
    assert (ovr["state"], ovr["shadow"]) == ("shadow", {"k": 0, "n": 5})
    listed = await _rpc("overrides.list", {"agentId": DRAFTER})
    assert oid in {o["id"] for o in listed["overrides"]}

    parked: list[dict] = []
    for k in range(5):
        run = await _run(monkeypatch, DRAFTER, _tag_proposal(f"Shadow {k}"))
        row = await repo.load_proposal(run["proposal_id"])
        assert row["status"] == "AWAITING_HUMAN"
        assert row["override_eval"]["outcome"] == "shadow"
        assert row["override_eval"]["shadow"] == [
            {"override_id": oid, "k": k, "n": 5, "complete": False}]
        assert run["proposal_id"] in await _inbox_ids(), "a shadowed proposal is in the inbox"
        await _approve(run)
        parked.append(run)
    assert await overrides.shadow_progress(await repo.get_gate_override(oid)) == 5
    shadowed = [e for e in await repo.list_events(kind="override.shadowed", since_id=start,
                                                  limit=200) if e["payload"].get("override_id") == oid]
    assert len(shadowed) == 5

    sixth = await _run(monkeypatch, DRAFTER, _tag_proposal("Sixth is the override's"))
    pid = sixth["proposal_id"]
    row = await repo.load_proposal(pid)
    assert row["status"] == "EXECUTED"
    assert pid not in await _inbox_ids(), "no inbox item for the override-decided write"
    assert row["provenance"]["approver"] == f"override:{oid}"
    assert row["override_eval"]["outcome"] == "decided"
    evs = await _events_for(pid)
    decided = [e for e in evs if e["kind"] == "proposal.decided"]
    executed = [e for e in evs if e["kind"] == "proposal.executed"]
    assert len(decided) == 1 and len(executed) == 1
    assert decided[0]["payload"]["override_id"] == oid
    assert decided[0]["actor"] == f"override:{oid}"
    assert decided[0]["id"] < executed[0]["id"], "proposal.decided precedes the write"
    assert executed[0]["payload"]["provenance"]["approver"] == f"override:{oid}"
    # An episode written under this approver names the override in its source
    # description (the one composer the Executor and the cutover share).
    assert executor_mod.episode_source_description("src", f"override:{oid}", "proposal=x") == (
        f"src | trust=override:{oid} | approver=override:{oid} | proposal=x")

    # The steward pairing: five operator verdicts count, the sixth does not.
    proposals, verdicts = [], []
    for run in [*parked, sixth]:
        proposals.append({**await repo.load_proposal(run["proposal_id"]),
                          "confidence": CONFIDENT})
        verdicts += await repo.list_events(kind="proposal.decided", ref_id=run["proposal_id"])
    report = pair_confidence(proposals, verdicts)
    assert report["total_paired"] == 5 and report["awaiting_decision"] == 0
    assert pid not in {p["proposal_id"] for p in report.get("pairs", [])}

    # Revoke over the RPC: the next matching proposal parks again.
    revoked = await _rpc("overrides.revoke", {
        "agentId": DRAFTER, "overrideId": oid, "reason": "done"})
    assert revoked["override"]["state"] == "revoked"
    after = await _run(monkeypatch, DRAFTER, _tag_proposal("After the revoke"))
    await _assert_parked(after, evaluated=False)

    # The override events are on /events, in the order they happened.
    log = (await routes.get_events(since=start, limit=2000))["events"]
    mine = [(e["kind"], e["id"]) for e in log
            if e["ref_id"] in {oid, pid} and e["kind"].startswith(("override.", "proposal."))]
    order = [k for k, _ in mine]
    assert order[0] == "override.created"
    assert order.index("override.live") < order.index("proposal.decided") < \
        order.index("proposal.executed") < order.index("override.revoked")
    assert order.count("override.live") == 1 and order.count("override.revoked") == 1


# --- what an override never decides ---------------------------------------------


@needs_pg
async def test_an_out_of_scope_proposal_parks(demo, authored, monkeypatch):
    await _author(authored, monkeypatch, DRAFTER, scope={
        "argument": {"name": "tags", "values": ["final"]}})
    out = await _run(monkeypatch, DRAFTER, _tag_proposal("Draft is outside"))
    row = await _assert_parked(out)
    assert "scope" in row["override_eval"]["reason"]


@needs_pg
async def test_another_agents_proposal_parks(demo, authored, monkeypatch):
    await _author(authored, monkeypatch, DRAFTER)
    out = await _run(monkeypatch, OTHER, _tag_proposal("Another agent drafts"))
    row = await _assert_parked(out, evaluated=False)
    assert row["override_eval"] is None


@needs_pg
async def test_a_half_covered_proposal_parks_whole(demo, authored, monkeypatch):
    await _author(authored, monkeypatch, DRAFTER, "catalog.tag")
    calls = _action_spy(monkeypatch)
    out = await _run(monkeypatch, DRAFTER, _two_action_proposal("Half covered"))
    await _assert_parked(out)
    assert calls == [], "not one of the two actions ran"


@needs_pg
async def test_a_flagged_proposal_parks_with_the_reviewers_reason(demo, authored, monkeypatch):
    await _author(authored, monkeypatch, DRAFTER, "jira.set_due_date")
    proposal = {
        "intent": "Due date in the past",
        "actions": [{
            "capability": "jira.set_due_date",
            "arguments": {"issue_key": "DOE-1", "due_date": "2020-01-01"},
            "target_ref": {"system": "jira", "id": "DOE-1", "read_version": "unknown"},
            "reversibility": "reversible"}],
        "evidence": _tag_proposal("x")["evidence"],
        "expected_effect": "DOE-1 is due in 2020",
    }
    out = await _run(monkeypatch, DRAFTER, proposal)
    row = await _assert_parked(out)
    assert "no-past-due-dates" in row["override_eval"]["reason"]


@needs_pg
async def test_a_redraft_after_reject_parks_unevaluated(demo, authored, monkeypatch):
    from central_command.runtime.run import ingest_and_propose
    from central_command.runtime.spike_model import make_redraft_model

    model = make_redraft_model()
    run = await ingest_and_propose("Dana: DEMO-1 slipped, due Aug 3.", model=model)
    await _author(authored, monkeypatch, TRIAGE, "jira.set_due_date")
    out = await gateway_mod.reject_with_feedback(
        run["session_id"], run["proposal_id"], "Wrong — make it 2026-08-10.", model=model)
    rid = out["redraft_proposal_id"]
    row = await repo.load_proposal(rid)
    assert row["status"] == "AWAITING_HUMAN"
    assert row["override_eval"]["outcome"] == "parked"
    assert row["override_eval"]["reason"] == "redraft"
    assert await overrides.decide_on_park(rid, model=model) is None
    assert rid in await _inbox_ids()


@needs_pg
async def test_an_item_parked_before_the_override_existed_stays_parked(
    demo, authored, monkeypatch,
):
    early = await _run(monkeypatch, DRAFTER, _tag_proposal("Parked before"))
    assert (await repo.load_proposal(early["proposal_id"]))["status"] == "AWAITING_HUMAN"
    await _author(authored, monkeypatch, DRAFTER)
    record = await overrides.decide_landed(early)
    assert record["outcome"] == "parked" and "predates" in record["reason"]
    await _assert_parked(early)
    # The startup sweep skips it too: it was created before the grant.
    await overrides.override_sweep()
    assert (await repo.load_proposal(early["proposal_id"]))["status"] == "AWAITING_HUMAN"


@needs_pg
async def test_a_covered_proposal_on_a_reopened_work_item_parks(demo, authored, monkeypatch):
    from central_command.ingest import ledger

    await _author(authored, monkeypatch, DRAFTER)
    out = await _run_unhooked(monkeypatch, DRAFTER, _tag_proposal("Reopened with a note"))
    enrolled = await ledger.enroll_provider_ref(
        {"uuid": f"e2e-note-{uuid.uuid4().hex[:6]}", "conversation_id": "c"})
    conn = await repo._conn()
    try:
        await conn.execute(
            """update work_item set session_id = $2, state = 'PROCESSED',
                      payload = coalesce(payload, '{}'::jsonb) || '{"operator_note": "look again"}'
                where message_id = $1""", enrolled["message_id"], out["session_id"])
    finally:
        await conn.close()
    try:
        record = await overrides.decide_landed(out)
        assert record["outcome"] == "parked"
        assert record["reason"] == overrides.OPERATOR_HOLD_REASON
        await _assert_parked(out)
    finally:
        conn = await repo._conn()
        try:
            await conn.execute("delete from work_item where message_id = $1",
                               enrolled["message_id"])
        finally:
            await conn.close()


async def _set_evidence(pid: str, evidence: list[dict]) -> None:
    conn = await repo._conn()
    try:
        await conn.execute("update proposal set evidence = $2::jsonb where id = $1",
                           pid, json.dumps(evidence))
    finally:
        await conn.close()


@needs_pg
@pytest.mark.parametrize("claim,decided", [(False, False), (None, True)])
async def test_a_false_claim_parks_and_an_unchecked_one_is_decided(
    claim, decided, demo, authored, monkeypatch,
):
    ovr = await _author(authored, monkeypatch, DRAFTER)
    out = await _run_unhooked(monkeypatch, DRAFTER, _tag_proposal(f"Claim {claim}"))
    evidence = [{**e, "claim_matches_source": claim}
                for e in (await repo.load_proposal(out["proposal_id"]))["evidence"]]
    await _set_evidence(out["proposal_id"], evidence)
    record = await overrides.decide_landed(out)
    row = await repo.load_proposal(out["proposal_id"])
    if decided:
        assert record["outcome"] == "decided" and row["status"] == "EXECUTED"
        assert row["provenance"]["approver"] == f"override:{ovr['override']['id']}"
    else:
        assert record["outcome"] == "parked" and "evidence claim" in record["reason"]
        await _assert_parked(out)


# --- revoking the pack grant ------------------------------------------------------


@needs_pg
async def test_revoking_the_pack_grant_suspends_the_override(demo, authored, monkeypatch):
    from tests.test_gate_overrides import _pack_for

    out = await _author(authored, monkeypatch, DRAFTER)
    oid = out["override"]["id"]
    first = await _run(monkeypatch, DRAFTER, _tag_proposal("Decided while granted"))
    assert (await repo.load_proposal(first["proposal_id"]))["status"] == "EXECUTED"
    await repo.revoke_pack(DRAFTER, _pack_for("catalog.tag"), reason="e2e")
    state = [o for o in (await _rpc("overrides.list", {"agentId": DRAFTER}))["overrides"]
             if o["id"] == oid][0]["state"]
    assert state == "suspended"
    second = await _run(monkeypatch, DRAFTER, _tag_proposal("Suspended"))
    row = await _assert_parked(second)
    assert "suspended" in row["override_eval"]["reason"]


# --- authoring, the auditor, acknowledgement ---------------------------------------


@needs_pg
async def test_authoring_for_the_auditor_is_refused_over_the_rpc(demo):
    before = len(await repo.list_gate_overrides())
    with pytest.raises(HTTPException) as e:
        await _rpc("overrides.author", {"agentId": "auditor", "capability": "catalog.tag",
                                        "scope": {}})
    assert e.value.status_code == 422 and "auditor" in str(e.value.detail)
    assert len(await repo.list_gate_overrides()) == before


@needs_pg
async def test_mail_send_needs_the_acknowledgement_and_a_non_zero_shadow(
    demo, authored, monkeypatch, exchange_mailbox,
):
    monkeypatch.setattr(settings, "gate_override_shadow_runs", 0)
    with pytest.raises(HTTPException) as e:
        await _rpc("overrides.author", {"agentId": DRAFTER, "capability": "mail.send",
                                        "scope": {}, "acknowledgeIrreversible": False})
    assert e.value.status_code == 422 and "acknowledgement" in str(e.value.detail)
    out = await _author(authored, monkeypatch, DRAFTER, "mail.send", ack=True, shadow=0)
    row = await repo.get_gate_override(out["override"]["id"])
    assert row["acknowledged_irreversible_at"] is not None
    assert row["shadow_runs"] >= 1


@needs_pg
async def test_a_hand_inserted_auditor_row_is_never_honoured_and_the_executor_refuses_it(
    demo, granted, monkeypatch,
):
    row = await granted("auditor", "catalog.tag")
    prop = _tag_proposal("Auditor row")
    resolved = await overrides.resolve({
        "agent_id": "auditor", "status": "AWAITING_HUMAN", "session_id": None,
        "created_at": datetime.now(timezone.utc) + timedelta(seconds=5),
        "actions": prop["actions"], "evidence": prop["evidence"]})
    assert resolved.gate == "human" and not resolved.considered

    from central_command.contract import Action

    calls = _action_spy(monkeypatch)
    with pytest.raises(executor_mod.OverrideRefused):
        await executor_mod.execute(
            [Action(**a) for a in prop["actions"]], approver=overrides.approver_for(row["id"]),
            source_refs=[], proposer="auditor")
    assert calls == [], "a forced approval writes nothing"


# --- several overrides on one proposal -----------------------------------------------


@needs_pg
async def test_two_overrides_decide_one_proposal_and_each_write_is_stamped_by_its_own(
    demo, authored, monkeypatch,
):
    a = (await _author(authored, monkeypatch, DRAFTER, "catalog.tag"))["override"]["id"]
    b = (await _author(authored, monkeypatch, DRAFTER, "jira.add_comment"))["override"]["id"]
    out = await _run(monkeypatch, DRAFTER, _two_action_proposal("Two overrides, no spy"))
    pid = out["proposal_id"]
    row = await repo.load_proposal(pid)
    combined = overrides.approver_for([a, b])
    assert row["status"] == "EXECUTED"
    assert row["provenance"]["approver"] == combined
    assert overrides.override_ids_of(row["provenance"]["approver"]) == sorted([a, b])
    assert row["provenance"]["action_approvers"] == {
        "0": overrides.approver_for(a), "1": overrides.approver_for(b)}
    decided = [e for e in await _events_for(pid) if e["kind"] == "proposal.decided"]
    assert decided[0]["payload"]["override_ids"] == sorted([a, b])
    assert pid not in await _inbox_ids()
    report = pair_confidence([{**row, "confidence": CONFIDENT}], decided)
    assert report["total_paired"] == 0 and report["awaiting_decision"] == 0


# --- another landing point ---------------------------------------------------------------


@needs_pg
async def test_the_chat_lane_landing_decides_a_covered_proposal(demo, authored, monkeypatch):
    from central_command.runtime import models

    ovr = (await _author(authored, monkeypatch, DRAFTER))["override"]["id"]
    intent = f"Tag from chat ({uuid.uuid4().hex[:6]})"
    model = make_deferred_tool_model("propose_action", {"proposal": _tag_proposal(intent)})

    async def session_model(*_a, **_kw):
        return model

    monkeypatch.setattr(models, "resolve_session_model", session_model)
    frames: list[dict] = []

    async def notify(frame):
        frames.append(frame)

    ack = await nerve_gateway._chat_send(
        {"sessionKey": f"agent:{DRAFTER}:main", "message": "tag a doc"}, notify)
    assert ack["status"] == "started"
    row = None
    for _ in range(150):
        mine = [p for p in await repo.list_proposals_for(DRAFTER) if p["intent"] == intent]
        if mine and mine[0]["status"] == "EXECUTED":
            row = await repo.load_proposal(mine[0]["id"])
            break
        await asyncio.sleep(0.2)
    assert row is not None, [p["status"] for p in await repo.list_proposals_for(DRAFTER)][:3]
    assert row["provenance"]["approver"] == f"override:{ovr}"
    assert row["id"] not in await _inbox_ids()
    kinds = _kinds(await _events_for(row["id"]))
    assert kinds.index("proposal.decided") < kinds.index("proposal.executed")


# --- no agent path ---------------------------------------------------------------------


def test_no_agent_tool_path_creates_an_override():
    pkg = Path(__file__).resolve().parents[1] / "central_command"
    for sub in ("runtime", "heartbeat"):
        for path in (pkg / sub).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            assert "create_gate_override" not in text, path
            assert "overrides.author" not in text, path
            assert "gateway.overrides" not in text and "gateway import overrides" not in text, path
