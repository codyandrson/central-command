"""JiraExpert phase-A tests: direct tasks ride the SAME durable gate as email
work, and a jira-expert session resumes under its own agent definition (not
inbox-triage's).

The CONSULTATION tests moved to `tests/test_consult.py` when consultation
generalized (2026-07-29): the advisory sub-run is no longer Jira-specific, so
its tests belong with the generic mechanism rather than with this one specialist.
"""

from __future__ import annotations

import pytest

from central_command.config import settings
from central_command.db import repo
from central_command.gateway import gateway
from central_command.runtime import jira_expert
from central_command.runtime.run import run_task
from central_command.runtime.spike_model import make_jira_task_model
from tests.conftest import needs_pg


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "executor_mode", "dry_run")


def _tool_names(agent) -> set[str]:
    # Tools live across the agent's toolsets since D25 (packs ride `toolsets=`).
    names: set[str] = set()
    for ts in agent.toolsets:
        names |= set(getattr(ts, "tools", {}) or {})
    return names


async def test_the_tasked_expert_holds_its_propose_and_read_tools():
    """The direct-task build. Its ADVISORY counterpart is generic now and lives
    in tests/test_consult.py — the read-only surface is asserted there."""
    agent = await jira_expert.build_jira_expert(model=make_jira_task_model())
    names = _tool_names(agent)
    assert names, "could not introspect agent tools"
    assert "propose_action" in names
    assert "jira_get_issue" in names


@needs_pg
async def test_direct_task_parks_a_proposal_behind_the_gate():
    model = make_jira_task_model()
    out = await run_task("jira-expert", "Add a dependency note to TASKS-12.", model=model)
    assert out["deferred"] is True

    row = await repo.load_proposal(out["proposal_id"])
    assert row["agent_id"] == "jira-expert"
    assert row["status"] == "AWAITING_HUMAN"
    assert row["actions"][0]["capability"] == "jira.add_comment"

    # Approval resumes the session under the jira-expert definition and
    # completes it — the multi-agent resume path working end to end.
    result = await gateway.approve_and_execute(out["session_id"], out["proposal_id"], model=model)
    assert result["ok"] is True
    assert await repo.session_status(out["session_id"]) == "DONE"


@needs_pg
async def test_direct_task_can_answer_in_text():
    model = make_jira_task_model()
    out = await run_task("jira-expert", "no change needed — just advice please", model=model)
    assert out["deferred"] is False
    assert "No Jira change" in out["output"]


async def test_only_jira_expert_is_taskable():
    with pytest.raises(ValueError):
        await run_task("inbox-triage", "do something")


# --- token accounting (the v2 usage-property regression) ----------------------


def test_total_tokens_reads_the_v2_usage_property():
    """pydantic-ai v2 made `usage` a property; the old `result.usage()` call
    raised into a catch-all and every run reported ZERO tokens — the budget
    valve was blind. Both shapes must read correctly."""
    from central_command.runtime.run import _total_tokens

    class Usage:
        total_tokens = 42

    class V2Result:
        usage = Usage()

    class V1Result:
        def usage(self):
            return Usage()

    assert _total_tokens(V2Result()) == 42
    assert _total_tokens(V1Result()) == 42


async def test_real_run_reports_nonzero_usage_shape():
    """The live regression detector: a spike run's usage must come back as a
    RunUsage the valve can read (requests >= 1 even when token counts are 0)."""
    from pydantic_ai import Agent

    from central_command.runtime.spike_model import make_jira_advice_model

    agent = Agent(make_jira_advice_model(), system_prompt="advise")
    result = await agent.run("q")
    usage = result.usage
    assert not callable(usage) and usage.requests >= 1


# --- phase B: the new gated capabilities map to the right façade calls --------


async def test_link_and_transition_handlers_call_the_facade(monkeypatch):
    from central_command.contract import Action
    from central_command.gateway import executor

    calls = []

    async def fake_link(from_key, to_key, link_type):
        calls.append(("link", from_key, to_key, link_type))
        return {"ok": True}

    async def fake_transition(issue_key, transition):
        calls.append(("transition", issue_key, transition))
        return {"ok": True}

    monkeypatch.setattr(settings, "executor_mode", "live")
    monkeypatch.setattr(executor.jira, "link_issues", fake_link)
    monkeypatch.setattr(executor.jira, "transition_issue", fake_transition)

    out = await executor.execute(
        [
            Action(
                capability="jira.link_issues",
                arguments={"from_key": "TASKS-12", "to_key": "TASKS-13",
                           "link_type": "Blocks"},
                target_ref={"system": "jira", "id": "TASKS-12", "read_version": "unknown"},
                reversibility="reversible",
            ),
            Action(
                capability="jira.transition_issue",
                arguments={"issue_key": "TASKS-13", "transition": "In Progress"},
                target_ref={"system": "jira", "id": "TASKS-13", "read_version": "unknown"},
                reversibility="reversible",
            ),
        ],
        approver="human:lee",
        source_refs=["operator:task"],
    )
    assert calls == [("link", "TASKS-12", "TASKS-13", "Blocks"),
                     ("transition", "TASKS-13", "In Progress")]
    assert "Blocks" in out.result_text and "In Progress" in out.result_text


async def test_dry_run_never_touches_link_or_transition(monkeypatch):
    from central_command.contract import Action
    from central_command.gateway import executor

    async def explode(*a, **k):  # any façade call is a test failure
        raise AssertionError("dry_run must not call the façade")

    monkeypatch.setattr(settings, "executor_mode", "dry_run")
    monkeypatch.setattr(executor.jira, "link_issues", explode)
    monkeypatch.setattr(executor.jira, "transition_issue", explode)

    out = await executor.execute(
        [Action(capability="jira.link_issues",
                arguments={"from_key": "A-1", "to_key": "A-2", "link_type": "Relates"},
                target_ref={"system": "jira", "id": "A-1", "read_version": "unknown"},
                reversibility="reversible")],
        approver="human:lee", source_refs=[],
    )
    assert "[dry-run]" in out.result_text
