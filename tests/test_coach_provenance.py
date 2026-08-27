"""A coach-drafted charter version must name the COACH as drafter and the
TARGET as subject.

`executor._charter_update` used to stamp `created_by` from `args["agent_id"]` —
the target — because `execute()` never received the drafting agent. That was
harmless while every charter edit was self-proposed, and became a false
provenance record the moment a coach could draft for someone else (stage 5a).

The drafter is taken from the proposal ROW, never from action args: an actor an
agent can write into its own arguments is an agent-authored claim about
provenance (the same rule stage 4 applied to `task.create`'s approver).
"""

from __future__ import annotations

import uuid

import pytest

from central_command.config import settings
from central_command.db import repo
from central_command.gateway import gateway
from tests.conftest import needs_pg

pytestmark = needs_pg


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    # charter.update writes only the (test) DB — live mode exercises the real
    # handler instead of dry-run's no-op.
    monkeypatch.setattr(settings, "executor_mode", "live")


async def _seed_charter_proposal(drafter: str, target: str, extra_args: dict | None = None) -> tuple[str, str]:
    """A proposal DRAFTED BY `drafter` that edits `target`'s charter."""
    sid = "sess_prov_" + uuid.uuid4().hex[:8]
    pid = "prop_prov_" + uuid.uuid4().hex[:8]
    await repo.upsert_agent(drafter, drafter, "demo", "c")
    await repo.upsert_agent(target, target, "demo", "c")
    await repo.save_charter_version(target, "CHARTER before coaching.", "seed",
                                    created_by="operator")
    await repo.save_paused_session(sid, drafter, {})
    await repo.save_proposal(
        proposal_id=pid, session_id=sid, agent_id=drafter,
        intent=f"Charter edit for {target}",
        actions=[{
            "capability": "charter.update",
            "arguments": {
                "agent_id": target,
                "content": "CHARTER before coaching.\n\nCOACHED RULE: check first.",
                "rationale": "Encodes recurring operator feedback.",
                **(extra_args or {}),
            },
            "target_ref": {"system": "central_command", "id": target,
                           "read_version": "unknown"},
            "reversibility": "reversible",
        }],
        evidence=[], expected_effect=f"{target} charter gains a rule",
        idempotency_key=f"{pid}:0", status="AWAITING_HUMAN",
    )
    return sid, pid


async def test_a_coach_drafted_version_names_the_drafter_and_the_target():
    suffix = uuid.uuid4().hex[:6]
    drafter, target = f"prov-drafter-{suffix}", f"prov-target-{suffix}"
    sid, pid = await _seed_charter_proposal(drafter, target)

    await gateway.approve_and_execute(sid, pid, approver="human:lee")

    row = await repo.current_charter(target)
    assert "COACHED RULE" in row["content"], "the edit did not commit"
    assert f"agent:{drafter}" in row["created_by"], (
        f"created_by must name the DRAFTER; got {row['created_by']!r}"
    )
    assert f"for {target}" in row["created_by"], (
        f"created_by must name the TARGET as subject; got {row['created_by']!r}"
    )


async def test_the_charter_updated_event_separates_proposer_from_target():
    suffix = uuid.uuid4().hex[:6]
    drafter, target = f"prov-ev-drafter-{suffix}", f"prov-ev-target-{suffix}"
    sid, pid = await _seed_charter_proposal(drafter, target)

    await gateway.approve_and_execute(sid, pid, approver="human:lee")

    evs = [e for e in await repo.list_events_of_kinds(["charter.updated"])
           if e["ref_id"] == target]
    assert evs, "no charter.updated event for the target"
    payload = evs[-1]["payload"]
    assert payload["proposed_by"] == f"agent:{drafter}"
    assert payload["target"] == target


async def test_a_proposer_claimed_in_action_args_never_overrides_the_row():
    """Provenance comes from the proposal row. An agent that writes
    `proposer` into its own arguments changes nothing."""
    suffix = uuid.uuid4().hex[:6]
    drafter, target = f"prov-spoof-{suffix}", f"prov-victim-{suffix}"
    sid, pid = await _seed_charter_proposal(
        drafter, target, extra_args={"proposer": "human:lee"},
    )

    await gateway.approve_and_execute(sid, pid, approver="human:lee")

    row = await repo.current_charter(target)
    assert f"agent:{drafter}" in row["created_by"]
    assert "human:lee" in row["created_by"]  # only as the APPROVER
    assert "agent:human:lee" not in row["created_by"]


async def test_a_self_proposed_edit_still_reads_as_before():
    """The existing pedigree format is unchanged when drafter == target, so
    old rows and new self-edits stay comparable."""
    agent = "prov-self-" + uuid.uuid4().hex[:6]
    sid, pid = await _seed_charter_proposal(agent, agent)

    await gateway.approve_and_execute(sid, pid, approver="human:lee")

    row = await repo.current_charter(agent)
    assert row["created_by"] == f"agent:{agent} (approved human:lee)"


async def test_an_executed_charter_update_records_its_proposal_id():
    """The version row must link back to the proposal that produced it — server
    -side truth from `executor._current_proposal_id`, the same contextvar every
    other handler reads its proposal id from, never a client-supplied arg."""
    suffix = uuid.uuid4().hex[:6]
    drafter, target = f"prov-pid-drafter-{suffix}", f"prov-pid-target-{suffix}"
    sid, pid = await _seed_charter_proposal(drafter, target)

    await gateway.approve_and_execute(sid, pid, approver="human:lee")

    row = await repo.current_charter(target)
    assert row["proposal_id"] == pid
