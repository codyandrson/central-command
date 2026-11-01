"""An irreversible class cannot be graduated to the auditor (epic-cockpit-rerun-
reversibility-macros R6, assumption A8).

The bar reads exactly `reversibility == "irreversible"` on the server-held
`Capability` mark — never the agent-written `Action.reversibility`. Two layers:

1. the registry refuses an `auditor` default gate on an irreversible row when it
   loads (`Capability.__post_init__`, so a planted row fails the suite);
2. `gateway.approve_and_execute(approver="auditor")` over an irreversible
   capability decides nothing: no `proposal.decided`, no write, the proposal
   stays AWAITING_HUMAN with a `policy_flags` entry on a
   `proposal.graduation_barred` event.

`work.bulk_dismiss` (the one auditor-gated class, reversible) still executes
exactly as before, and `GET /audit` lists the barred classes for the curl
reader. The dismissal and graph classes are not capabilities and are outside
this check.
"""

from __future__ import annotations

import uuid

import pytest

from central_command.api import routes
from central_command.config import settings
from central_command.db import repo
from central_command.gateway import gateway
from central_command.gateway.capabilities import (
    POLICIES,
    REGISTRY,
    Capability,
    barred_classes,
)
from tests.conftest import needs_pg
from tests.fake_world import fake_world


def _row(**kw) -> Capability:
    base = dict(
        name="x.write", kind="write", gate="human approval", default_gate="human",
        risk="r", holder="Executor", route="r", arguments=[], description="d",
        reversibility="reversible",
    )
    return Capability(**{**base, **kw})


def test_an_irreversible_row_with_an_auditor_default_gate_cannot_be_constructed():
    with pytest.raises(ValueError, match="auditor"):
        _row(default_gate="auditor", reversibility="irreversible", acknowledge=True)


def test_the_bar_reads_only_the_irreversible_mark():
    # The reversible auditor row and an acknowledged-but-reversible row (the LIVE
    # shape) are both constructible: the bar is exactly `irreversible`.
    _row(default_gate="auditor", reversibility="reversible")
    _row(default_gate="human", reversibility="reversible", acknowledge=True)


def test_no_registry_row_is_both_auditor_gated_and_irreversible():
    assert not [c.name for c in REGISTRY
                if c.default_gate == "auditor" and c.reversibility == "irreversible"]


def test_the_barred_classes_are_the_irreversible_write_rows():
    barred = barred_classes()
    assert barred == sorted(c.name for c in REGISTRY
                            if c.kind == "write" and c.reversibility == "irreversible")
    assert "mail.send" in barred
    assert "work.bulk_dismiss" not in barred


def test_the_policy_is_registered_with_its_guard_test():
    policy = next(p for p in POLICIES if p.name == "An irreversible class never graduates")
    assert policy.status == "enforced"
    assert policy.guard.startswith("tests/test_graduation_bar.py::")


# --- the gateway layer ------------------------------------------------------


@pytest.fixture()
def world(monkeypatch):
    monkeypatch.setattr(settings, "test_model", True)
    return fake_world(monkeypatch)


_ARGS = {"mail.send": {"to": ["a@example.com"], "subject": "Hi", "body": "Hello"}}


async def _proposal(capability: str, *, reversibility: str = "reversible") -> tuple[str, str]:
    pid = "prop_grad_" + uuid.uuid4().hex[:10]
    sid = "sess_grad_" + uuid.uuid4().hex[:8]
    await repo.create_running_session(sid, "jira-expert")
    await repo.save_proposal(
        pid, sid, "jira-expert", f"Run {capability}",
        # The agent's own `reversibility` claim is deliberately the opposite of
        # the registry's mark on the bar test: the bar must never read it.
        [{"capability": capability, "arguments": _ARGS.get(capability, {}), "target_ref": {"system": "x"},
          "reversibility": reversibility}],
        [], "it happens", pid,
    )
    return sid, pid


async def _kinds(pid: str) -> list[str]:
    return [e["kind"] for e in await repo.list_events(ref_id=pid, limit=500)]


@needs_pg
async def test_auditor_approval_of_an_irreversible_capability_parks_for_the_operator(world):
    # The agent claims "reversible"; the registry says mail.send is irreversible.
    sid, pid = await _proposal("mail.send", reversibility="reversible")

    with pytest.raises(gateway.GraduationBarred):
        await gateway.approve_and_execute(sid, pid, approver="auditor")

    assert (await repo.load_proposal(pid))["status"] == "AWAITING_HUMAN"
    kinds = await _kinds(pid)
    assert "proposal.decided" not in kinds
    assert "proposal.executed" not in kinds
    assert world == [], "no write was performed"

    barred = [e for e in await repo.list_events(ref_id=pid, limit=500)
              if e["kind"] == "proposal.graduation_barred"]
    assert len(barred) == 1
    flags = barred[0]["payload"]["policy_flags"]
    assert flags[0]["policy"] == "irreversible-never-graduates"
    assert flags[0]["capability"] == "mail.send"
    assert barred[0]["actor"] == "auditor"

    # The operator's own approval is untouched by the bar.
    await gateway.approve_and_execute(sid, pid)
    assert (await repo.load_proposal(pid))["status"] == "EXECUTED"
    assert [c for c, _, _ in world] == ["mail.send"]


@needs_pg
async def test_one_irreversible_action_among_reversible_ones_parks_the_whole_proposal(world):
    pid = "prop_grad_" + uuid.uuid4().hex[:10]
    sid = "sess_grad_" + uuid.uuid4().hex[:8]
    await repo.create_running_session(sid, "jira-expert")
    acts = [{"capability": c, "arguments": _ARGS.get(c, {}), "target_ref": {"system": "x"},
             "reversibility": "reversible"} for c in ("jira.add_comment", "mail.send")]
    await repo.save_proposal(pid, sid, "jira-expert", "mixed", acts, [], "x", pid)

    with pytest.raises(gateway.GraduationBarred):
        await gateway.approve_and_execute(sid, pid, approver="auditor")

    assert world == [], "not even the reversible action ran"
    assert "proposal.decided" not in await _kinds(pid)


@needs_pg
async def test_the_auditor_still_executes_the_reversible_graduated_class(world):
    sid, pid = await _proposal("work.bulk_dismiss")

    await gateway.approve_and_execute(sid, pid, approver="auditor")

    assert (await repo.load_proposal(pid))["status"] == "EXECUTED"
    kinds = await _kinds(pid)
    assert "proposal.decided" in kinds and "proposal.graduation_barred" not in kinds
    assert [(c, a) for c, _, a in world] == [("work.bulk_dismiss", "auditor")]


@needs_pg
async def test_a_stale_status_read_cannot_open_the_bar(world, monkeypatch):
    """The bar rides the claim, not the status the caller read first: a row read
    as EXECUTING (a concurrent approval about to hand it back) that the claim
    then wins is still barred, and the claim goes back as it was."""
    sid, pid = await _proposal("mail.send")
    real = repo.load_proposal

    async def stale(proposal_id):
        row = await real(proposal_id)
        return {**row, "status": "EXECUTING"} if row else row

    monkeypatch.setattr(repo, "load_proposal", stale)
    with pytest.raises(gateway.GraduationBarred):
        await gateway.approve_and_execute(sid, pid, approver="auditor")
    monkeypatch.setattr(repo, "load_proposal", real)

    assert (await repo.load_proposal(pid))["status"] == "AWAITING_HUMAN"
    assert "proposal.decided" not in await _kinds(pid)
    assert world == []


@needs_pg
async def test_a_decided_proposal_is_not_reported_as_barred(world):
    """The bar applies to an open proposal; a settled one keeps its ordinary
    'not awaiting approval' refusal."""
    sid, pid = await _proposal("mail.send")
    await gateway.approve_and_execute(sid, pid)
    with pytest.raises(gateway.GatewayError) as info:
        await gateway.approve_and_execute(sid, pid, approver="auditor")
    assert not isinstance(info.value, gateway.GraduationBarred)


async def test_audit_status_carries_the_barred_classes():
    out = await routes.audit_status()
    assert out["barred_classes"] == barred_classes()
    assert "mail.send" in out["barred_classes"]
    assert "work.bulk_dismiss" not in out["barred_classes"]
