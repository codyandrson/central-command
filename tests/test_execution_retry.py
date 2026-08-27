"""Slice 3 of outage equivalence: an APPROVED proposal whose EXECUTION hits a
transient dependency failure queues instead of failing.

The evidence base (2026-07-31): the same LiteLLM/n8n class of transient error
that cost a resume its final turn turned an approved proposal terminally FAILED
— the operator's decision spent, the work gone, and the only way back a fresh
proposal the operator had to approve all over again.

The property these tests pin: **the decision stands; the world-change is what
queues.** The proposal rests RETRY_PENDING carrying a replay cursor, its folded
siblings stay folded (the covering claim is still expected to happen), its
drafter stays paused owing nothing yet, and a retry runs only the actions that
never completed — then hands off to the very same post-execution tail an
undisturbed approval uses, resume parking included.

A SEMANTIC failure keeps today's behaviour byte-for-byte: loud and terminal.
"""

from __future__ import annotations

import asyncio

import pytest
from pydantic_ai.exceptions import ModelHTTPError

from central_command.api import nerve_gateway, orchestration
from central_command.config import settings
from central_command.db import repo
from central_command.gateway import executor, gateway
from central_command.integrations import graphiti
from central_command.runtime.run import ingest_and_propose
from central_command.runtime.spike_model import make_sc1_model, make_spike_model
from tests.conftest import needs_pg

pytestmark = needs_pg

# The two shapes, as this deployment actually produces them.
OUTAGE_TEXT = "Connection refused talking to the n8n façade"
SEMANTIC_TEXT = "404 issue does not exist"
RESUME_OUTAGE = ModelHTTPError(429, "cc-default", body="No deployments available")


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "executor_mode", "live")  # retries need real handlers
    monkeypatch.setattr(settings, "dispatch_approval_limit", 10_000)


def _jira_spy(monkeypatch, *, fail: str | None = None) -> list[tuple]:
    """Record every jira.set_due_date call; optionally make it raise. The SPY is
    the point: a replay that re-runs a completed action shows up here as a second
    entry, which no assertion about status could catch."""
    calls: list[tuple] = []

    async def set_due_date(issue_key, due_date):
        calls.append((issue_key, due_date))
        if fail:
            raise RuntimeError(fail)

    monkeypatch.setattr(executor.jira, "set_due_date", set_due_date)
    return calls


def _graph_spy(monkeypatch, *, fail: str | None = None) -> list[tuple]:
    calls: list[tuple] = []

    async def add_episode(name, body, source_description="", group_id=None):
        calls.append((name, body))
        if fail:
            raise RuntimeError(fail)
        return "ack"

    monkeypatch.setattr(graphiti, "add_episode", add_episode)
    return calls


async def _kinds(ref_id: str) -> list[str]:
    return [e["kind"] for e in await repo.list_events(ref_id=ref_id)]


async def _event(ref_id: str, kind: str) -> dict:
    return [e for e in await repo.list_events(ref_id=ref_id) if e["kind"] == kind][0]


async def _parked_run(monkeypatch, *, fail: str = OUTAGE_TEXT) -> dict:
    """A two-action proposal (jira, then graph) approved while the GRAPH is
    down: the jira half of the world changed, the graph half never ran."""
    jira = _jira_spy(monkeypatch)
    _graph_spy(monkeypatch, fail=fail)
    model = make_sc1_model()
    run = await ingest_and_propose("Dana: DEMO-1 slipped, due Aug 3.", model=model)
    out = await gateway.approve_and_execute(
        run["session_id"], run["proposal_id"], model=model
    )
    return {**run, "out": out, "jira_calls": jira, "model": model}


# --- 1: the approve leg parks instead of failing --------------------------------


async def test_a_transient_execution_failure_parks_the_proposal(monkeypatch):
    # Pin the setting, not the ambient .env — CC_OPERATOR_NAME varies per
    # deployment (2026-08-21 Windows validation, W6).
    monkeypatch.setattr(settings, "operator_name", "lee")
    parked = await _parked_run(monkeypatch)
    out = parked["out"]

    # Truthful to the operator: the approval succeeded, the execution is queued.
    assert out["ok"] is True
    assert out["execution"] == "parked"
    assert out["attempts"] == 1
    assert out["failed_capability"] == "graph.add_episode"

    row = await repo.load_proposal(parked["proposal_id"])
    assert row["status"] == "RETRY_PENDING"      # never terminal FAILED
    state = row["retry_state"]
    assert state["completed_results"] == ["DEMO-1 due_date set to 2026-08-03"]
    assert state["next_action_index"] == 1       # the cursor: skip what landed
    assert state["approver"] == "human:lee"
    assert state["attempts"] == 1
    assert "Connection refused" in state["last_error"]
    assert state["parked_at"]

    # The drafter is owed nothing yet — its session waits exactly as it did
    # while the decision was pending.
    assert await repo.session_status(parked["session_id"]) == "AWAITING_HUMAN"

    kinds = await _kinds(parked["proposal_id"])
    assert kinds.count("proposal.decided") == 1   # decided once, by the operator
    assert "proposal.execution_parked" in kinds
    assert "proposal.failed" not in kinds
    assert "proposal.executed" not in kinds
    ev = await _event(parked["proposal_id"], "proposal.execution_parked")
    assert ev["payload"]["transient"] is True
    assert ev["payload"]["attempts"] == 1
    assert ev["payload"]["completed_actions"] == ["DEMO-1 due_date set to 2026-08-03"]


async def test_a_parked_execution_does_not_release_its_folds(monkeypatch):
    """Contrast with FAILED: the covering proposal is still expected to happen,
    so re-queuing its siblings would re-triage mail it still covers."""
    jira = _jira_spy(monkeypatch)
    _graph_spy(monkeypatch, fail=OUTAGE_TEXT)
    model = make_sc1_model()
    run = await ingest_and_propose("Dana: DEMO-1 slipped, due Aug 3.", model=model)

    conn = await repo._conn()
    try:
        await conn.execute(
            """insert into work_item (id, message_id, feed, source, payload, state, folded_into)
               values ('wi_retry_fold', '<cc-retry-fold@x>', 'live', 'api', '{}'::jsonb,
                       'FOLD_PENDING', $1)""",
            run["proposal_id"],
        )
        await gateway.approve_and_execute(
            run["session_id"], run["proposal_id"], model=model
        )
        state = await conn.fetchval(
            "select state from work_item where id = 'wi_retry_fold'"
        )
        assert state == "FOLD_PENDING"
        assert len(jira) == 1
    finally:
        await conn.execute("delete from work_item where id = 'wi_retry_fold'")
        await conn.close()


# --- 2: a semantic failure keeps today's behaviour ------------------------------


async def test_a_semantic_execution_failure_keeps_todays_behaviour(monkeypatch):
    """Regression: only a transient failure parks. `404 issue does not exist`
    is the dependency ANSWERING — loud, terminal, coachable, exactly as before."""
    _jira_spy(monkeypatch, fail=SEMANTIC_TEXT)
    model = make_spike_model()
    run = await ingest_and_propose("Dana: DEMO-1 slipped, due Aug 3.", model=model)

    out = await gateway.approve_and_execute(
        run["session_id"], run["proposal_id"], model=model
    )

    assert out["ok"] is False
    assert out["failed_capability"] == "jira.set_due_date"
    row = await repo.load_proposal(run["proposal_id"])
    assert row["status"] == "FAILED"
    assert row["retry_state"] is None
    kinds = await _kinds(run["proposal_id"])
    assert "proposal.failed" in kinds
    assert "proposal.execution_parked" not in kinds
    assert (await _event(run["proposal_id"], "proposal.failed"))["payload"][
        "transient"
    ] is False


# --- 3: the retry converges, without re-running what landed ---------------------


async def test_the_retry_runs_only_the_remaining_actions(monkeypatch):
    # Pin the setting, not the ambient .env — CC_OPERATOR_NAME varies per
    # deployment (2026-08-21 Windows validation, W6).
    monkeypatch.setattr(settings, "operator_name", "lee")
    parked = await _parked_run(monkeypatch)
    jira_calls = parked["jira_calls"]
    assert len(jira_calls) == 1

    # The dependency is back.
    graph_calls = _graph_spy(monkeypatch)
    out = await gateway.retry_execution(parked["proposal_id"], model=parked["model"])

    assert out is not None and out["retried"] is True
    # THE point of the cursor: the Jira write did NOT happen a second time.
    assert len(jira_calls) == 1
    assert len(graph_calls) == 1

    row = await repo.load_proposal(parked["proposal_id"])
    assert row["status"] == "EXECUTED"
    assert row["provenance"]["approver"] == "human:lee"
    assert row["provenance"]["executed_at"]        # stamped now; the record shows the outage
    # The agent is told what its PROPOSAL did, not what the last attempt did.
    assert "DEMO-1 due_date set to 2026-08-03" in out["result_text"]
    assert "graph episode" in out["result_text"]

    kinds = await _kinds(parked["proposal_id"])
    assert "proposal.execution_retried" in kinds
    assert "proposal.executed" in kinds
    assert kinds.count("proposal.decided") == 1   # no second gate
    # The drafter was resumed through the shared tail and the session closed out.
    assert await repo.session_status(parked["session_id"]) == "DONE"


async def test_a_retried_execution_commits_its_folds(monkeypatch):
    jira = _jira_spy(monkeypatch)
    _graph_spy(monkeypatch, fail=OUTAGE_TEXT)
    model = make_sc1_model()
    run = await ingest_and_propose("Dana: DEMO-1 slipped, due Aug 3.", model=model)

    conn = await repo._conn()
    try:
        await conn.execute(
            """insert into work_item (id, message_id, feed, source, payload, state, folded_into)
               values ('wi_retry_commit', '<cc-retry-commit@x>', 'live', 'api', '{}'::jsonb,
                       'FOLD_PENDING', $1)""",
            run["proposal_id"],
        )
        await gateway.approve_and_execute(
            run["session_id"], run["proposal_id"], model=model
        )
        _graph_spy(monkeypatch)
        await gateway.retry_execution(run["proposal_id"], model=model)
        state = await conn.fetchval(
            "select state from work_item where id = 'wi_retry_commit'"
        )
        assert state == "FOLDED"
        assert len(jira) == 1
    finally:
        await conn.execute("delete from work_item where id = 'wi_retry_commit'")
        await conn.close()


# --- 4: bounded retries, then a visible promotion -------------------------------


async def test_retries_are_bounded_and_exhaustion_promotes(monkeypatch):
    monkeypatch.setattr(settings, "execution_retry_limit", 2)
    parked = await _parked_run(monkeypatch)
    model = parked["model"]

    # Still down.
    _graph_spy(monkeypatch, fail=OUTAGE_TEXT)
    again = await gateway.retry_execution(parked["proposal_id"], model=model)
    assert again["execution"] == "parked"
    assert again["attempts"] == 2
    row = await repo.load_proposal(parked["proposal_id"])
    assert row["status"] == "RETRY_PENDING"
    # The cursor still points past the action that DID land, once.
    assert row["retry_state"]["next_action_index"] == 1
    assert row["retry_state"]["completed_results"] == [
        "DEMO-1 due_date set to 2026-08-03"
    ]

    _graph_spy(monkeypatch, fail=OUTAGE_TEXT)
    spent = await gateway.retry_execution(parked["proposal_id"], model=model)
    assert spent.get("execution") != "parked"      # the budget is spent
    assert spent["exhausted"] is True

    row = await repo.load_proposal(parked["proposal_id"])
    assert row["status"] == "FAILED"               # promotion to today's terminal state
    kinds = await _kinds(parked["proposal_id"])
    assert "proposal.execution_exhausted" in kinds
    assert "proposal.failed" in kinds

    exhausted = await _event(parked["proposal_id"], "proposal.execution_exhausted")
    assert exhausted["payload"]["attempts"] == 2
    item = await repo.get_operator_item(exhausted["payload"]["operator_item_id"])
    assert item["status"] == "OPEN" and item["kind"] == "question"
    assert parked["proposal_id"] in item["body"]
    # The honest partial record names what landed across ALL attempts.
    failed = await _event(parked["proposal_id"], "proposal.failed")
    assert failed["payload"]["completed_actions"] == [
        "DEMO-1 due_date set to 2026-08-03"
    ]


# --- 5: a semantic failure ON RETRY ends it, with the spliced record ------------


async def test_a_semantic_failure_on_retry_fails_with_the_spliced_completed_list(
    monkeypatch,
):
    parked = await _parked_run(monkeypatch)

    _graph_spy(monkeypatch, fail="400 invalid episode body")
    out = await gateway.retry_execution(parked["proposal_id"], model=parked["model"])

    assert out["ok"] is False
    assert out["exhausted"] is False               # not the outage — the answer
    row = await repo.load_proposal(parked["proposal_id"])
    assert row["status"] == "FAILED"
    kinds = await _kinds(parked["proposal_id"])
    assert "proposal.execution_exhausted" not in kinds
    failed = await _event(parked["proposal_id"], "proposal.failed")
    # The Jira write from the FIRST attempt is still part of the truth.
    assert failed["payload"]["completed_actions"] == [
        "DEMO-1 due_date set to 2026-08-03"
    ]
    assert failed["payload"]["transient"] is False


# --- 6: the claim is the SQL flip ----------------------------------------------


async def test_two_concurrent_retries_execute_exactly_once(monkeypatch):
    parked = await _parked_run(monkeypatch)
    graph_calls = _graph_spy(monkeypatch)

    a, b = await asyncio.gather(
        gateway.retry_execution(parked["proposal_id"], model=parked["model"]),
        gateway.retry_execution(parked["proposal_id"], model=parked["model"]),
    )

    assert [a is not None, b is not None].count(True) == 1, (
        "the RETRY_PENDING→EXECUTING flip is the lock"
    )
    assert len(graph_calls) == 1
    assert len(parked["jira_calls"]) == 1
    assert (await repo.load_proposal(parked["proposal_id"]))["status"] == "EXECUTED"


# --- 7: the Decisions Inbox never re-asks --------------------------------------


async def test_a_parked_execution_is_absent_from_the_decisions_wire(monkeypatch):
    """On the REAL payload the cockpit fetches: a decided proposal must not
    reappear as decidable — the operator decided once."""
    parked = await _parked_run(monkeypatch)

    wire = await nerve_gateway._decisions_list()

    assert parked["proposal_id"] not in [p["id"] for p in wire["proposals"]]
    assert all(p["status"] == "AWAITING_HUMAN" for p in wire["proposals"])


# --- 8: composition with slice 2 ------------------------------------------------


async def test_a_retried_execution_whose_resume_outages_parks_the_session(monkeypatch):
    """The two slices compose: the retried execution SUCCEEDS (proposal
    EXECUTED, world changed) and the resume leg's own transient failure parks
    the session AWAITING_RESUME — because the retry hands off to the same
    post-execution tail, not to a copy of it."""
    parked = await _parked_run(monkeypatch)
    _graph_spy(monkeypatch)

    async def resume_boom(*a, **k):
        raise RESUME_OUTAGE

    monkeypatch.setattr(gateway, "resume", resume_boom)
    out = await gateway.retry_execution(parked["proposal_id"], model=parked["model"])

    assert out["resume_parked"] is True
    assert (await repo.load_proposal(parked["proposal_id"]))["status"] == "EXECUTED"
    assert await repo.session_status(parked["session_id"]) == "AWAITING_RESUME"
    pending = (await repo.get_session_run_state(parked["session_id"]))["pending_resume"]
    assert pending["after"] == "approved"
    assert pending["proposal_id"] == parked["proposal_id"]
    # The agent is still owed the FULL outcome, not the last attempt's half.
    assert "DEMO-1 due_date set to 2026-08-03" in pending["text"]
    assert "graph episode" in pending["text"]


# --- the sweep drives parked executions ----------------------------------------


async def test_resume_sweep_retries_parked_executions(monkeypatch):
    """The "after a restart, or whenever in doubt" lever covers parked
    executions too — the automatic cadence is slice 5."""
    parked = await _parked_run(monkeypatch)
    _graph_spy(monkeypatch)

    swept = await orchestration.resume_sweep()

    assert parked["proposal_id"] in swept["retried"]
    assert (await repo.load_proposal(parked["proposal_id"]))["status"] == "EXECUTED"


# --- cockpit honesty: a parked execution never reads as an undecided proposal ---


async def test_the_cockpit_notice_tells_the_truth_about_a_parked_execution(monkeypatch):
    """The drafter's session deliberately stays AWAITING_HUMAN while its
    APPROVED proposal sits RETRY_PENDING — so the read-only notice kept saying
    "awaiting your decision" about a decision the operator already made (the
    slice-3 residual, same honesty class the AWAITING_RESUME branch fixed)."""
    from central_command.api import nerve_gateway

    parked = await _parked_run(monkeypatch)

    session = await repo.get_session(parked["session_id"])
    notice = await nerve_gateway._run_session_context(session)
    assert "awaiting your decision" not in notice["message"]
    assert "queued for retry" in notice["message"]
