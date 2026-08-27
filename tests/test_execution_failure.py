"""Execution-failure hardening tests.

The gap (found live: an approved proposal targeting a nonexistent Jira issue):
`approve_and_execute` emitted the decision, then the executor threw — leaving
the proposal AWAITING_HUMAN (re-approvable → double execution of completed
actions), folded siblings locked forever, the session parked, and a log
showing a decision with no outcome.

The properties now: a failed execution makes the proposal terminally FAILED
(never re-approvable), releases its folds, records `proposal.failed` with the
honest partial results, and resumes the agent with the truth — which may
redraft (e.g. a corrected issue key), and that redraft lands in the inbox.
"""

from __future__ import annotations

import pytest

from central_command.config import settings
from central_command.db import repo
from central_command.gateway import executor, gateway
from central_command.integrations import graphiti
from central_command.runtime.run import ingest_and_propose
from central_command.runtime.spike_model import make_sc1_model, make_spike_model
from tests.conftest import needs_pg

pytestmark = needs_pg


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "executor_mode", "live")  # failures need the live path


async def _jira_down(monkeypatch):
    async def broken(issue_key, due_date):
        raise RuntimeError("404 issue does not exist")

    monkeypatch.setattr(executor.jira, "set_due_date", broken)


async def test_failed_execution_is_terminal_and_honest(monkeypatch):
    await _jira_down(monkeypatch)
    model = make_spike_model()
    run = await ingest_and_propose("Dana: DEMO-1 slipped, due Aug 3.", model=model)

    out = await gateway.approve_and_execute(run["session_id"], run["proposal_id"], model=model)

    assert out["ok"] is False
    assert out["failed_capability"] == "jira.set_due_date"
    assert "404" in out["error"]
    assert out["completed_actions"] == []

    row = await repo.load_proposal(run["proposal_id"])
    assert row["status"] == "FAILED"
    # Operator's rule (2026-08-20): the session rests OPEN and flagged — a
    # quiet DONE was hiding the failure in the sidebar. It stays that way
    # until the operator closes it by hand (test_exec_failure_open pins the lever).
    assert await repo.session_status(run["session_id"]) == "AWAITING_OPERATOR"
    session = await repo.get_session(run["session_id"])
    assert "404" in (session["exec_failure"] or "")

    # The log tells the whole story, in order: decided → failed.
    events = await repo.list_events(ref_id=run["proposal_id"])
    kinds = [e["kind"] for e in events]
    assert kinds.index("proposal.decided") < kinds.index("proposal.failed")
    failed = next(e for e in events if e["kind"] == "proposal.failed")
    assert failed["payload"]["completed_actions"] == []


async def test_partial_execution_records_what_completed(monkeypatch):
    """Jira succeeds, the graph write fails — the record must say the Jira
    half of the world DID change."""
    done = []

    async def jira_ok(issue_key, due_date):
        done.append((issue_key, due_date))

    async def graph_broken(*a, **k):
        raise RuntimeError("neo4j unreachable")

    monkeypatch.setattr(executor.jira, "set_due_date", jira_ok)
    monkeypatch.setattr(graphiti, "add_episode", graph_broken)

    model = make_sc1_model()
    run = await ingest_and_propose("Dana: DEMO-1 slipped, due Aug 3.", model=model)
    out = await gateway.approve_and_execute(run["session_id"], run["proposal_id"], model=model)

    assert out["ok"] is False
    assert out["failed_capability"] == "graph.add_episode"
    assert done == [("DEMO-1", "2026-08-03")]
    assert out["completed_actions"] == ["DEMO-1 due_date set to 2026-08-03"]
    assert (await repo.load_proposal(run["proposal_id"]))["status"] == "FAILED"


async def test_failed_proposal_cannot_be_approved_again(monkeypatch):
    await _jira_down(monkeypatch)
    model = make_spike_model()
    run = await ingest_and_propose("Dana: DEMO-1 slipped, due Aug 3.", model=model)
    await gateway.approve_and_execute(run["session_id"], run["proposal_id"], model=model)

    with pytest.raises(gateway.GatewayError):
        await gateway.approve_and_execute(run["session_id"], run["proposal_id"], model=model)


async def test_failed_execution_releases_folds(monkeypatch):
    """A failed covering proposal covered nothing: FOLD_PENDING siblings must
    return to the queue, exactly as on rejection."""
    await _jira_down(monkeypatch)
    model = make_spike_model()
    run = await ingest_and_propose("Dana: DEMO-1 slipped, due Aug 3.", model=model)

    conn = await repo._conn()
    try:
        await conn.execute(
            """insert into work_item (id, message_id, feed, source, payload, state, folded_into)
               values ('wi_test_fold', '<cc-test-fold@x>', 'live', 'api', '{}'::jsonb,
                       'FOLD_PENDING', $1)""",
            run["proposal_id"],
        )
        await gateway.approve_and_execute(run["session_id"], run["proposal_id"], model=model)
        state = await conn.fetchval(
            "select state from work_item where id = 'wi_test_fold'"
        )
        assert state == "UNPROCESSED"
    finally:
        await conn.execute("delete from work_item where id = 'wi_test_fold'")
        await conn.close()


async def test_final_failure_flips_the_email_off_processed(monkeypatch):
    """Ledger honesty: the email went PROCESSED when its proposal parked; if
    the approved proposal then fails to execute and the session ends, PROCESSED
    is a lie — the row must flip to FAILED with the error (the charter-v2
    incident left two real emails looking handled with nothing done)."""
    await _jira_down(monkeypatch)
    model = make_spike_model()
    run = await ingest_and_propose("Dana: DEMO-1 slipped, due Aug 3.", model=model)

    conn = await repo._conn()
    try:
        await conn.execute(
            """insert into work_item (id, message_id, feed, source, payload, state, session_id)
               values ('wi_test_flip', '<cc-test-flip@x>', 'live', 'api', '{}'::jsonb,
                       'PROCESSED', $1)""",
            run["session_id"],
        )
        await gateway.approve_and_execute(run["session_id"], run["proposal_id"], model=model)
        row = await conn.fetchrow(
            "select state, last_error from work_item where id = 'wi_test_flip'"
        )
        assert row["state"] == "FAILED"
        assert "jira.set_due_date" in row["last_error"]

        # The operator's redo: requeue puts it back for a fresh run.
        assert await repo.requeue_failed_work_item("wi_test_flip") is True
        assert await conn.fetchval(
            "select state from work_item where id = 'wi_test_flip'"
        ) == "UNPROCESSED"
    finally:
        await conn.execute("delete from work_item where id = 'wi_test_flip'")
        await conn.close()


async def test_requeue_reparses_stale_handfed_payloads():
    """The wi_68bb23613065 incident: an item enrolled under the old bare-text
    parser bug stored text '\\n' — every requeue replayed the broken payload
    into Claude for a fresh 400. Requeue now re-parses from the stored raw."""
    from central_command.api import routes

    conn = await repo._conn()
    try:
        await conn.execute(
            """insert into work_item (id, message_id, feed, source, payload, state, last_error)
               values ('wi_test_reparse', '<cc-test-reparse@x>', 'live', 'api',
                       '{"raw": "Dana: the deadline slipped to Aug 9.", "text": "\\n", "failure_ack": true}'::jsonb,
                       'FAILED', 'claude 400')""",
        )
        out = await routes.requeue_work_item("wi_test_reparse")
        assert out["reparsed"] is True
        row = await conn.fetchrow(
            "select state, payload from work_item where id = 'wi_test_reparse'"
        )
        assert row["state"] == "UNPROCESSED"
        import json as _json

        payload = _json.loads(row["payload"])
        assert "deadline slipped" in payload["text"]  # healed, not the stale '\n'
        assert "failure_ack" not in payload  # a requeue withdraws the ack
    finally:
        await conn.execute("delete from work_item where id = 'wi_test_reparse'")
        await conn.close()


async def test_acknowledge_closes_a_failure_without_rewriting_history():
    from fastapi import HTTPException

    from central_command.api import routes

    conn = await repo._conn()
    try:
        await conn.execute(
            """insert into work_item (id, message_id, feed, source, payload, state)
               values ('wi_test_ack', '<cc-test-ack@x>', 'live', 'api', '{}'::jsonb, 'FAILED')""",
        )
        assert (await routes.acknowledge_failure("wi_test_ack"))["ok"] is True
        row = await conn.fetchrow(
            "select state, payload from work_item where id = 'wi_test_ack'"
        )
        import json as _json

        assert row["state"] == "FAILED"  # the record is the truth — state unchanged
        assert _json.loads(row["payload"])["failure_ack"] is True
        with pytest.raises(HTTPException):  # double-ack refuses
            await routes.acknowledge_failure("wi_test_ack")
    finally:
        await conn.execute("delete from work_item where id = 'wi_test_ack'")
        await conn.close()
