"""Agent Workspace tests (M15).

The properties: **run history survives session completion** (the transcript is
the episodic record; only resumability ends at DONE), `load_paused_session`
keeps its old semantics (paused-only) via the status filter, and the transcript
simplifier renders real persisted histories without knowing every part shape.
"""

from __future__ import annotations

import pytest

from central_command.config import settings
from central_command.db import repo
from central_command.gateway import gateway
from central_command.runtime.durable import simplify_messages
from central_command.runtime.run import ingest_and_propose
from central_command.runtime.spike_model import make_spike_model
from tests.conftest import needs_pg

pytestmark = needs_pg


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "executor_mode", "dry_run")


async def test_history_survives_completion_but_resumability_ends():
    model = make_spike_model()
    run = await ingest_and_propose("Dana: DEMO-1 slipped, due Aug 3.", model=model)
    session_id = run["session_id"]

    assert await repo.load_paused_session(session_id) is not None

    await gateway.reject_with_feedback(session_id, run["proposal_id"], "Not needed.", model=model)
    assert await repo.session_status(session_id) == "DONE"

    # Paused-view: gone (old semantics preserved). Transcript-view: intact.
    assert await repo.load_paused_session(session_id) is None
    history = await repo.get_session_run_state(session_id)
    assert history is not None and history.get("messages")


async def test_transcript_renders_a_real_history():
    model = make_spike_model()
    run = await ingest_and_propose("Dana: DEMO-1 slipped, due Aug 3.", model=model)

    turns = simplify_messages(await repo.get_session_run_state(run["session_id"]))
    kinds = [t["kind"] for t in turns]
    assert "system-prompt" in kinds
    assert "user-prompt" in kinds
    assert "tool-call" in kinds
    call = next(t for t in turns if t["kind"] == "tool-call")
    assert call["tool"] == "propose_action"
    assert "DEMO-1" in call["content"]


def test_simplifier_tolerates_unknown_part_shapes():
    weird = {"messages": [{"parts": [{"part_kind": "future-thing", "payload": {"x": 1}}]}]}
    turns = simplify_messages(weird)
    assert turns[0]["kind"] == "future-thing"
    assert "x" in turns[0]["content"]


async def test_session_list_carries_intent_and_counts():
    model = make_spike_model()
    run = await ingest_and_propose("Dana: DEMO-1 slipped, due Aug 3.", model=model)
    rows = await repo.list_sessions(limit=200)
    mine = next(r for r in rows if r["id"] == run["session_id"])
    assert mine["proposal_count"] == 1
    assert "DEMO-1" in mine["latest_intent"]
