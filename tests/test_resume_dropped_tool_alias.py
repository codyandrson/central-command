"""Resume-compat for the `propose_jira_update` drop (2026-08-21).

The alias was removed from every capability pack's `tool_names` (see
`runtime/packs.py` and CLAUDE.md), but at least one live session
(prop_4a305e235710, jira-expert, AWAITING_HUMAN) has a persisted deferred call
NAMED `propose_jira_update` from before the drop. This proves such a session
still resumes correctly through `gateway.approve_and_execute` /
`build_agent_for` even though no pack offers that tool name any more.

Why this is expected to work (verified against pydantic-ai 2.18's
`_tool_execution.py`): a resumed deferred call is matched against the
persisted `DeferredToolResults` purely by `tool_call_id`. A non-`ToolApproved`
result (a plain string, as the approve path supplies, or a `ToolDenied`, as
reject supplies) short-circuits `_call_tool` *before* any lookup in the live
toolset — the tool's own function is never invoked and its registration is
never checked. So dropping the alias from the toolset cannot break a pending
resume; it only stops a FRESH call from being made under that name.
"""

from __future__ import annotations

import pytest

from central_command.config import settings
from central_command.db import repo
from central_command.gateway import gateway
from central_command.runtime.run import ingest_and_propose
from central_command.runtime.spike_model import make_spike_model
from tests.conftest import needs_pg

pytestmark = needs_pg


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    monkeypatch.setattr(settings, "executor_mode", "dry_run")  # never touch real Jira
    monkeypatch.setattr(settings, "demo_mode", True)


async def test_session_parked_under_the_dropped_alias_still_resumes():
    model = make_spike_model()
    run = await ingest_and_propose("Dana: DEMO-1 slipped, due Aug 3.", model=model)
    assert run["deferred"]
    session_id, proposal_id = run["session_id"], run["proposal_id"]

    # Simulate a pre-drop session: rewrite the persisted deferred call in place
    # from propose_action to its old name, exactly the shape a session parked
    # before 2026-08-21 would have on disk.
    run_state = await repo.get_session_run_state(session_id)
    messages = run_state["messages"]
    renamed = 0
    for msg in messages:
        for part in msg.get("parts", []):
            if part.get("part_kind") == "tool-call" and part.get("tool_name") == "propose_action":
                part["tool_name"] = "propose_jira_update"
                renamed += 1
    assert renamed == 1, "expected exactly one propose_action call to rename"
    await repo.update_session_run_state(session_id, {"messages": messages})

    # No pack offers propose_jira_update any more (tests/test_packs.py), so a
    # clean approval here proves the resume never re-resolves the pending call
    # against the live toolset.
    out = await gateway.approve_and_execute(session_id, proposal_id, model=model)
    assert "resume_parked" not in out

    row = await repo.load_proposal(proposal_id)
    assert row["status"] == "EXECUTED"
