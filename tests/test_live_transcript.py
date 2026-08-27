"""Live transcript tests (the "rich live transcript streaming" nicety).

The properties: **a session row exists from run START** (status RUNNING) and
its transcript is persisted incrementally — the Workspace watches work happen,
never just reads it afterwards; a no-proposal run completes DONE with its
transcript kept and its session linked from the dismissal claim; a run that
dies mid-flight leaves a FAILED session with the partial transcript (what the
agent did before it broke is the record the operator needs); orphaned RUNNING
rows from a dead process are swept like stale ledger claims.
"""

from __future__ import annotations

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from central_command.config import settings
from central_command.db import repo
from central_command.ingest import dispatcher, ledger
from central_command.runtime.durable import simplify_messages
from central_command.runtime.run import ingest_and_propose
from central_command.runtime.spike_model import make_folding_model, make_no_action_model
from tests.conftest import needs_pg

pytestmark = needs_pg

RAW = (
    "Message-ID: <cc-live-1@example.com>\nFrom: news@example.com\n"
    "Subject: Weekly digest\nDate: Fri, 17 Jul 2026 08:00:00 +0000\n\n"
    "Nothing actionable here.\n"
)


@pytest.fixture(autouse=True)
async def pinned(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "executor_mode", "dry_run")
    monkeypatch.setattr(settings, "dispatch_approval_limit", 10_000)
    monkeypatch.setattr(settings, "auditor_enabled", False)
    yield
    conn = await repo._conn()
    try:
        await conn.execute("delete from work_item where message_id like '<cc-live-%'")
    finally:
        await conn.close()


async def test_transcript_is_persisted_incrementally_while_running(monkeypatch):
    """The live property itself: the run state hits the DB in growing snapshots
    BEFORE the run ends, each taken while the session is still RUNNING."""
    real_update = repo.update_session_run_state
    snapshots: list[tuple[int, str]] = []

    async def recording_update(session_id: str, run_state: dict) -> None:
        await real_update(session_id, run_state)
        snapshots.append(
            (len(run_state.get("messages", [])), await repo.session_status(session_id))
        )

    monkeypatch.setattr(repo, "update_session_run_state", recording_update)

    # The folding model runs two rounds (record_thread_decision, then the
    # proposal), so the history grows across several persist points.
    run = await ingest_and_propose("Dana: DEMO-1 slipped, due Aug 3.", model=make_folding_model())
    assert run["deferred"] is True

    counts = [c for c, _ in snapshots]
    assert len(snapshots) >= 2, "expected multiple mid-run persists"
    assert counts == sorted(counts) and counts[-1] > counts[0]
    # Every incremental snapshot happened while the session was live.
    assert all(status == "RUNNING" for _, status in snapshots)
    # And each one announced itself on the log (content stays in run_state —
    # the event carries counts only).
    steps = [
        e for e in await repo.list_events_of_kinds(["session.step"])
        if e["ref_id"] == run["session_id"]
    ]
    assert len(steps) == len(snapshots)
    assert all("messages" in (e.get("payload") or {}) for e in steps)

    # The pause finalized it as before: AWAITING_HUMAN, resumable.
    assert await repo.session_status(run["session_id"]) == "AWAITING_HUMAN"
    assert await repo.load_paused_session(run["session_id"]) is not None


async def test_no_action_run_completes_done_and_the_dismissal_links_it():
    """A dismissal is no longer a session-less claim: the run behind it is DONE
    with its transcript kept, and the parked work item carries the session id."""
    await ledger.enroll_email(RAW)
    result = await dispatcher.dispatch_once(model=make_no_action_model())

    assert result["ok"] and result.get("dismiss_pending")
    session_id = result.get("session_id")
    assert session_id, "no-action runs must report their session now"
    assert await repo.session_status(session_id) == "DONE"

    turns = simplify_messages(await repo.get_session_run_state(session_id))
    assert any(t["kind"] == "text" for t in turns)

    items = await repo.list_work_items(state="DISMISS_PENDING")
    mine = next(i for i in items if i["message_id"] == "<cc-live-1@example.com>")
    assert mine["session_id"] == session_id


async def test_failed_run_keeps_the_partial_transcript():
    calls = {"n": 0}

    def _respond(messages, info) -> ModelResponse:
        calls["n"] += 1
        if calls["n"] == 1:
            return ModelResponse(parts=[ToolCallPart(
                tool_name="record_thread_decision",
                args={"fold": False, "covered_message_ids": [],
                      "rationale": "solo email"},
            )])
        raise RuntimeError("model exploded mid-run")

    before = {s["id"] for s in await repo.list_sessions(limit=500)}
    with pytest.raises(RuntimeError, match="exploded"):
        await ingest_and_propose("Dana: DEMO-1 slipped.", model=FunctionModel(_respond))

    new = [s for s in await repo.list_sessions(limit=500) if s["id"] not in before]
    assert len(new) == 1 and new[0]["status"] == "FAILED"
    turns = simplify_messages(await repo.get_session_run_state(new[0]["id"]))
    kinds = [t["kind"] for t in turns]
    assert "tool-call" in kinds and "tool-return" in kinds  # the work before the break


async def test_a_looping_run_is_cut_off_and_keeps_its_partial_transcript(monkeypatch):
    """The loop breaker (`run_request_limit`) applied at the one run seam. A
    model that never stops calling the same tool is stopped FOR it, and the
    outcome is the same as any other mid-flight death: FAILED session, partial
    transcript kept, exception re-raised for the caller to classify.

    Not a token budget — tokens are free here. This catches the run that would
    otherwise sit RUNNING forever with nobody watching."""
    from pydantic_ai.exceptions import UsageLimitExceeded

    monkeypatch.setattr(settings, "run_request_limit", 3)

    def _loop(messages, info) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart(
            tool_name="record_thread_decision",
            args={"fold": False, "covered_message_ids": [], "rationale": "again"},
        )])

    before = {s["id"] for s in await repo.list_sessions(limit=500)}
    with pytest.raises(UsageLimitExceeded):
        await ingest_and_propose("Dana: DEMO-1 slipped.", model=FunctionModel(_loop))

    new = [s for s in await repo.list_sessions(limit=500) if s["id"] not in before]
    assert len(new) == 1 and new[0]["status"] == "FAILED"
    kinds = [t["kind"] for t in simplify_messages(
        await repo.get_session_run_state(new[0]["id"])
    )]
    assert "tool-call" in kinds and "tool-return" in kinds


async def test_stale_running_sessions_are_swept_but_fresh_ones_are_not():
    stale, fresh = "sess_livetest_stale", "sess_livetest_fresh"
    await repo.upsert_agent("inbox-triage", "Inbox Triage", "inbox-triage", "t")
    await repo.create_running_session(stale, "inbox-triage")
    await repo.create_running_session(fresh, "inbox-triage")
    conn = await repo._conn()
    try:
        await conn.execute(
            "update session set updated_at = now() - interval '1 hour' where id = $1",
            stale,
        )
        swept, landed = await repo.fail_stale_running_sessions(900)
        assert swept >= 1
        assert landed == [], "these sessions carry no task to land"
        assert await repo.session_status(stale) == "FAILED"
        assert await repo.session_status(fresh) == "RUNNING"
    finally:
        await conn.execute(
            "delete from session where id in ($1, $2)", stale, fresh
        )
        await conn.close()
