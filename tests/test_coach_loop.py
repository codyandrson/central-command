"""D5 coaching-loop tests: the agent proposes its own charter edit from the
operator's recorded feedback; nothing changes the charter without approval;
the committed version carries agent-proposed pedigree; revert survives.
"""

from __future__ import annotations

import uuid

import pytest

from central_command import events
from central_command.config import settings
from central_command.db import repo
from central_command.gateway import gateway
from central_command.runtime.run import gather_coaching_signals, run_coach
from central_command.runtime.spike_model import make_coach_model
from tests.conftest import needs_pg

pytestmark = needs_pg


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    # charter.update writes only the (test) DB — live mode exercises the real
    # handler instead of dry-run's no-op.
    monkeypatch.setattr(settings, "executor_mode", "live")


async def _seed_rejection(agent_id: str) -> None:
    sid = "sess_d5_" + uuid.uuid4().hex[:8]
    pid = "prop_d5_" + uuid.uuid4().hex[:8]
    await repo.upsert_agent(agent_id, agent_id, "demo", "c")
    await repo.save_paused_session(sid, agent_id, {})
    await repo.save_proposal(
        proposal_id=pid, session_id=sid, agent_id=agent_id,
        intent="set a date", actions=[], evidence=[], expected_effect="",
        idempotency_key=f"{pid}:0", status="REJECTED",
    )
    await events.emit(
        "proposal.decided", ref_id=pid,
        payload={"verdict": "rejected", "feedback": "never propose past dates"},
        actor="operator",
    )


async def _seeded_agent(prefix: str) -> str:
    """A throwaway agent with ONE rejection signal and its own charter row, so
    these assertions never ride on whatever the shared dev DB holds for
    inbox-triage (nor on a built-in charter, which a throwaway id has none of)."""
    agent = f"{prefix}-" + uuid.uuid4().hex[:6]
    await _seed_rejection(agent)
    await repo.save_charter_version(
        agent, "CHARTER for a coaching test.", "test seed", created_by="operator"
    )
    return agent


async def _seed_done_task(agent_id: str, outcome: str) -> str:
    """A task this agent closed DONE, with its outcome on the record — the
    litellm-manager miss (task_9885f870fa04, 2026-08-01): a blocking question
    buried in the outcome text, never a rejection, so it was uncoachable."""
    task_id = "task_d5_" + uuid.uuid4().hex[:8]
    await repo.upsert_agent(agent_id, agent_id, "demo", "c")
    await repo.create_task(task_id, "Register the new alias", "do the thing", agent_id)
    await repo.resolve_task(task_id, "DONE", outcome=outcome)
    await events.emit(
        "task.completed", ref_id=task_id,
        payload={"outcome": outcome}, actor="system",
    )
    return task_id


async def test_a_task_outcome_is_a_selectable_signal():
    """Rejections are not the only coachable miss. A DONE task's outcome is
    offered to the picker, marked as the AGENT's words, citable by event id."""
    from central_command.api.routes import coaching_signals

    agent = "d5-task-" + uuid.uuid4().hex[:6]
    outcome = "I could not proceed without knowing which region to use."
    task_id = await _seed_done_task(agent, outcome)

    signals = (await coaching_signals(agent))["signals"]
    mine = [s for s in signals if s["kind"] == "task-outcome"]
    assert len(mine) == 1, mine  # own rows only: a throwaway agent id
    assert mine[0]["feedback"] == outcome
    assert mine[0]["author"] == "agent"
    assert task_id in (mine[0]["intent"] or "")
    row = await repo.get_event(mine[0]["event_id"])
    assert row["kind"] == "task.completed" and row["ref_id"] == task_id


async def test_a_coach_run_on_a_task_outcome_verifies_its_citation():
    """The whole point of widening the picker: the coach can quote a task
    outcome and the quote is still CHECKED against the recorded source —
    as `record` evidence, since the operator did not write it."""
    agent = "d5-taskcoach-" + uuid.uuid4().hex[:6]
    outcome = "Closing as done; still unclear which region the alias belongs to."
    await _seed_done_task(agent, outcome)
    await repo.save_charter_version(
        agent, "CHARTER for a coaching test.", "test seed", created_by="operator"
    )

    signals = await gather_coaching_signals(agent)
    assert [s["kind"] for s in signals] == ["task-outcome"]

    out = await run_coach(agent, model=make_coach_model(),
                          signal_ids=[signals[0]["event_id"]])
    row = await repo.load_proposal(out["proposal_id"])
    cited = [e for e in row["evidence"] if e.get("citation")]
    assert cited, row["evidence"]
    assert all(e["kind"] == "record" for e in cited), "an agent's own words are not operator input"
    assert all(e["claim_matches_source"] is True for e in cited)
    assert all(e["citation"] == "selected" for e in cited)


async def test_no_signals_means_no_run():
    agent = "d5-blank-" + uuid.uuid4().hex[:6]
    out = await run_coach(agent, model=make_coach_model())
    assert out["deferred"] is False and out["signals"] == 0


async def test_coach_proposes_and_approval_commits_a_pedigreed_version():
    await _seed_rejection("inbox-triage")
    before = await repo.current_charter("inbox-triage")
    before_v = before["version"] if before else 0

    out = await run_coach("inbox-triage", model=make_coach_model())
    assert out["deferred"] is True and out["signals"] >= 1

    row = await repo.load_proposal(out["proposal_id"])
    assert row["actions"][0]["capability"] == "charter.update"
    # Nothing changed yet — the gate is the gate.
    mid = await repo.current_charter("inbox-triage")
    assert (mid["version"] if mid else 0) == before_v

    result = await gateway.approve_and_execute(
        out["session_id"], out["proposal_id"], model=make_coach_model()
    )
    assert result["ok"] is True

    after = await repo.current_charter("inbox-triage")
    assert after["version"] == before_v + 1
    # Stage 5a: the drafter is the rostered coach, not the target agent — the
    # pedigree now reads "agent:coach for inbox-triage (approved ...)".
    assert after["created_by"].startswith("agent:coach")
    assert "COACHED RULE" in after["content"]

    # Revert stays available: re-activating the old content is a NEW version.
    if before is not None:
        back = await repo.save_charter_version(
            "inbox-triage", before["content"], "revert test", created_by="operator"
        )
        assert back["version"] == after["version"] + 1


async def test_coach_evidence_is_verified_on_the_way_into_the_inbox():
    """The wiring half of `tests/test_coach_evidence.py`: a faithful quote of a
    curated signal arrives verified, so the reviewer sees a checked citation
    rather than the `None` (= never checked) that shipped until 2026-07-25."""
    agent = await _seeded_agent("d5-verify")

    out = await run_coach(agent, model=make_coach_model())
    row = await repo.load_proposal(out["proposal_id"])
    operator = [e for e in row["evidence"] if e["kind"] == "operator"]
    assert operator, "the coach model cites the operator"
    assert all(e["claim_matches_source"] is True for e in operator)
    assert all(e["citation"] == "selected" for e in operator)
    assert all("selected by the operator" in e["locator"] for e in operator)


async def test_a_misquoted_or_miscited_coach_claim_is_flagged_before_review():
    """The agent supplies both the quote and the pointer on this path, so both
    are checked. Neither is blocking — the flag rides to the reviewer, exactly
    like the redraft path's."""
    agent = await _seeded_agent("d5-drift")

    paraphrase = await run_coach(
        agent, model=make_coach_model(claim="the operator told me to rewrite everything")
    )
    row = await repo.load_proposal(paraphrase["proposal_id"])
    assert row["evidence"][0]["claim_matches_source"] is False
    # Flagged, not blocked: it still parks for the human.
    assert row["status"] == "AWAITING_HUMAN"

    miscited = await run_coach(agent, model=make_coach_model(source_ref="event:999999"))
    row = await repo.load_proposal(miscited["proposal_id"])
    assert row["evidence"][0]["claim_matches_source"] is False
    assert "UNVERIFIABLE" in row["evidence"][0]["locator"]


async def test_a_coached_proposal_announces_itself_on_the_log():
    """The behavioural half of `tests/test_proposal_created.py`: until
    2026-07-25 a coach run parked its proposal with NO proposal.created event,
    so the audit trail saw a charter edit only at decision time. Scoped to the
    ref_id this test created — never a global count."""
    agent = await _seeded_agent("d5-event")
    out = await run_coach(agent, model=make_coach_model())

    created = [
        e for e in await repo.list_events_of_kinds(["proposal.created"])
        if e["ref_id"] == out["proposal_id"]
    ]
    assert len(created) == 1, "exactly once — two emitters would double-log it"
    # Stage 5a: the proposal row (and the event that announces it) belongs to
    # the coach — the agent that generated it — not the coaching target.
    assert created[0]["actor"] == "agent:coach"
    assert created[0]["payload"]["session_id"] == out["session_id"]
    assert created[0]["payload"]["coached"] is True
    assert created[0]["payload"]["target"] == agent


async def test_rejecting_the_coached_edit_changes_nothing():
    await _seed_rejection("inbox-triage")
    before = await repo.current_charter("inbox-triage")
    before_v = before["version"] if before else 0

    out = await run_coach("inbox-triage", model=make_coach_model())
    await gateway.reject_with_feedback(
        out["session_id"], out["proposal_id"], "not like this", model=make_coach_model()
    )
    now = await repo.current_charter("inbox-triage")
    assert (now["version"] if now else 0) == before_v


async def test_signals_are_scoped_to_the_agent():
    lonely = "d5-lonely-" + uuid.uuid4().hex[:6]
    await _seed_rejection("inbox-triage")
    ours = await gather_coaching_signals(lonely)
    assert all(s["kind"] != "rejection" for s in ours)


async def test_signals_are_newest_first_capped_and_dated():
    """The picker sorts across all four sources by event id (recency), caps at
    COACHING_SIGNAL_LIMIT, and dates every signal it can — regressions here
    would silently re-scramble or unbound the picker."""
    agent = "d5-order-" + uuid.uuid4().hex[:6]
    await _seed_rejection(agent)
    outcome = "closing as done, still unclear on the region"
    await _seed_done_task(agent, outcome)

    signals = await gather_coaching_signals(agent)
    assert len(signals) == 2
    ids = [s["event_id"] for s in signals]
    assert ids == sorted(ids, reverse=True)
    assert all("at" in s for s in signals)
    assert len(signals) <= 50


# --- the rework (The operator): operator-curated inputs + diff review -----------------


async def test_only_selected_signals_feed_the_session():
    """Two rejections on record; the operator selects ONE — the session must
    see exactly that one."""
    await _seed_rejection("inbox-triage")
    await _seed_rejection("inbox-triage")
    from central_command.runtime.run import gather_coaching_signals

    signals = [s for s in await gather_coaching_signals("inbox-triage")
               if s["kind"] == "rejection"]
    assert len(signals) >= 2
    chosen = signals[0]["event_id"]

    out = await run_coach("inbox-triage", model=make_coach_model(),
                          signal_ids=[chosen])
    assert out["deferred"] is True
    assert out["signals"] == 1  # exactly the curated input, nothing else


async def test_note_only_coaching_cites_the_request_event():
    out = await run_coach(
        "inbox-triage", model=make_coach_model(),
        signal_ids=[], note="always ask the expert before proposing links",
        note_event_id=424242,
    )
    assert out["deferred"] is True and out["signals"] == 1
    row = await repo.load_proposal(out["proposal_id"])
    assert any(e["source_ref"] == "event:424242" for e in row["evidence"])


async def test_empty_selection_and_no_note_refuses():
    await _seed_rejection("inbox-triage")
    out = await run_coach("inbox-triage", model=make_coach_model(),
                          signal_ids=[], note=None)
    assert out["deferred"] is False and out["signals"] == 0


async def test_charter_edit_review_carries_a_diff():
    from central_command.api import routes

    out = await run_coach(
        "inbox-triage", model=make_coach_model(),
        signal_ids=[], note="review as diff please", note_event_id=1,
    )
    row = await routes.get_proposal(out["proposal_id"])
    action = row["actions"][0]
    assert action["capability"] == "charter.update"
    diff = action["charter_diff"]
    assert "+COACHED RULE: review as diff please" in diff
    assert diff.startswith("--- charter ")
