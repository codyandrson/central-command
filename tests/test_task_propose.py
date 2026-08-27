"""`task.create` — an agent proposing work for a teammate (stage 4, 2026-07-25).

The first gated capability that acts on Central Command's own work queue rather
than an external system, and the first time an agent can put work on another
agent's board. the operator's call: the proposal NAMES its assignee, and the operator's
approval is the delegation.
"""

from __future__ import annotations

import pytest

from central_command.config import settings
from central_command.contract.models import Action, Reversibility
from central_command.db import repo
from central_command.gateway import executor
from tests.conftest import needs_pg


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)


@pytest.fixture(autouse=True)
def no_detached_runs(monkeypatch):
    """Create the task, but never let a real agent run detach from the test.

    The create path is what is under test; letting it spawn a background run
    would put an uncontrolled agent session into the suite — which is the exact
    shape of the task-test flakes already root-caused twice.
    """
    from central_command.api import routes

    async def noop(task, actor, **_kw):
        return None

    monkeypatch.setattr(routes, "_run_assigned_task_detached", noop)


def _action(agent_id="jira-expert", instructions="Check TASKS-12 for a due date.",
            title=None) -> Action:
    args = {"agent_id": agent_id, "instructions": instructions}
    if title:
        args["title"] = title
    return Action(
        capability="task.create",
        arguments=args,
        target_ref={"system": "central_command", "id": agent_id, "read_version": "unknown"},
        reversibility=Reversibility.reversible,
    )


def test_task_create_is_a_registered_gated_write():
    """It must be in the registry to be proposable and in HANDLERS to be
    executable. The M16 parity test enforces the set equality; this pins the
    specific entry so a rename can't quietly drop it from both at once."""
    from central_command.gateway.capabilities import gated_write_names

    assert "task.create" in gated_write_names()
    assert "task.create" in executor.HANDLERS


@needs_pg
async def test_dry_run_creates_nothing(monkeypatch):
    """The dry-run rule holds for a capability that writes to Central Command's own
    tables, not just for external systems — otherwise "safe mode" would still
    put real work on a teammate's board."""
    monkeypatch.setattr(settings, "executor_mode", "dry_run")
    before = len(await repo.list_tasks())

    await executor.execute([_action()], approver="human:lee", source_refs=[])

    assert len(await repo.list_tasks()) == before


@needs_pg
async def test_approval_creates_the_task_assigned_to_the_named_agent(monkeypatch):
    """The proposal names the assignee and approval is the delegation.

    Actor is the APPROVER: the agent asked, the operator's approval is what
    brought the task into existence. An actor the agent could name would be an
    agent-authored claim about provenance.
    """
    monkeypatch.setattr(settings, "executor_mode", "live")
    since = await repo.latest_event_id()

    outcome = await executor.execute(
        [_action(agent_id="jira-expert", instructions="Audit TASKS-12 links.",
                 title="Link audit")],
        approver="human:lee", source_refs=[],
    )
    # What the proposing agent is told on resume must name the assignee — it
    # asked for a teammate to do something and deserves to learn who got it.
    assert "jira-expert" in outcome.result_text

    created = [
        e for e in await repo.list_events(since_id=since, kind="task")
        if e["kind"] == "task.created"
    ]
    assert len(created) == 1
    assert created[0]["actor"] == "human:lee"
    assert created[0]["payload"]["agent_id"] == "jira-expert"

    task = await repo.get_task(created[0]["ref_id"])
    assert task["agent_id"] == "jira-expert"
    assert task["title"] == "Link audit"
    # It went through THE one create path, so it is a normal board task.
    assert task["instructions"] == "Audit TASKS-12 links."


@needs_pg
async def test_a_task_for_a_non_taskable_agent_fails_loudly(monkeypatch):
    """Naming an agent that cannot take tasks fails the execution rather than
    creating an orphan. The auditor is the live example — it is deliberately
    not taskable because it is part of the gate."""
    monkeypatch.setattr(settings, "executor_mode", "live")
    before = len(await repo.list_tasks())

    with pytest.raises(Exception) as exc:
        await executor.execute(
            [_action(agent_id="auditor")], approver="human:lee", source_refs=[],
        )
    assert "auditor" in str(exc.value)
    assert len(await repo.list_tasks()) == before


@needs_pg
async def test_the_agents_that_can_propose_a_task_can_actually_hold_the_tool():
    """"Every agent can propose a task" — as far as it is TRUE.

    Two agents are deliberately excluded and their reasons are recorded on the
    agent row, because a grant an agent cannot use is a lie on its profile:
    the auditor takes no toolsets at all (output_type=AuditVerdict), and the
    orchestrator already creates work through assign_work after an approved
    plan. This asserts the exclusions stay explained rather than becoming
    folklore.
    """
    from central_command.runtime import packs

    for agent_id in ("inbox-triage", "jira-expert", "litellm-manager"):
        assert "task-propose" in packs.DEFAULT_PACKS[agent_id]

    assert "task-propose" not in packs.DEFAULT_PACKS["orchestrator"]
    for agent_id in ("auditor", "orchestrator"):
        row = await repo.get_agent(agent_id)
        assert "no task-propose grant" in (row["deviations"] or ""), (
            f"{agent_id} lacks the pack with no recorded reason"
        )


def test_every_template_can_propose_a_task():
    """A newly hired agent gets the pack, including the read-only analyst —
    it changes nothing itself, and raising work for someone else is the one
    thing it may ask for."""
    from central_command.runtime.templates import TEMPLATES

    for t in TEMPLATES.values():
        assert "task-propose" in t.default_packs, f"{t.id} cannot propose a task"


@needs_pg
async def test_approval_stamps_the_proposing_session_as_parent(monkeypatch):
    """A task created from an approved task.create proposal links back to the
    session that DRAFTED it (`parent_session_id`) — the board's route to the
    email that triggered the proposal, via `work_items_for_sessions`. Read
    from the PROPOSAL row server-side (never from `args`): an agent-writable
    field would be an agent-authored claim about its own provenance."""
    import uuid

    monkeypatch.setattr(settings, "executor_mode", "live")
    proposer_agent = "inbox-triage"
    suffix = uuid.uuid4().hex[:8]
    sid, pid = f"sess_propose_{suffix}", f"prop_propose_{suffix}"
    await repo.upsert_agent(proposer_agent, proposer_agent, "demo", "test charter")
    await repo.save_paused_session(sid, proposer_agent, {})
    await repo.save_proposal(
        proposal_id=pid, session_id=sid, agent_id=proposer_agent,
        intent="ask jira-expert to follow up", actions=[], evidence=[],
        expected_effect="", idempotency_key=f"{pid}:0",
    )
    since = await repo.latest_event_id()

    await executor.execute(
        [_action(agent_id="jira-expert", instructions="Follow up on TASKS-9.")],
        approver="human:lee", source_refs=[], proposer=proposer_agent,
        proposal_id=pid,
    )

    created = [
        e for e in await repo.list_events(since_id=since, kind="task")
        if e["kind"] == "task.created"
    ]
    assert len(created) == 1
    task = await repo.get_task(created[0]["ref_id"])
    assert task["parent_session_id"] == sid


@needs_pg
async def test_approval_with_no_proposal_id_leaves_parent_session_null(monkeypatch):
    """Calling the executor without a `proposal_id` (e.g. a direct test call,
    or any future caller that doesn't route through the gateway) must not
    blow up looking one up — the created task simply has no parent session,
    same as an operator-created task."""
    monkeypatch.setattr(settings, "executor_mode", "live")

    outcome = await executor.execute(
        [_action(agent_id="jira-expert", instructions="Untraceable follow-up.")],
        approver="human:lee", source_refs=[],
    )
    assert "jira-expert" in outcome.result_text
