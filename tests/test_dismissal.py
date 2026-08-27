"""Dismissal review tests (M14).

The property, per the operator: **"no action needed" is a claim, not an outcome** —
the same rule folds follow. An agent's dismissal parks DISMISS_PENDING with
its rationale; only operator confirmation makes it terminal; reopening returns
it to the queue with the operator's note riding into the next run's prompt;
and unconfirmed dismissals count toward the approval throttle, so a drain can
never silently out-run review by dismissing everything.
"""

from __future__ import annotations

import json

import pytest

from central_command.config import settings
from central_command.db import repo
from central_command.ingest import dispatcher, ledger
from central_command.runtime.spike_model import make_no_action_model, make_spike_model
from tests.conftest import needs_pg

pytestmark = needs_pg

RAW = (
    "Message-ID: <cc-dis-1@example.com>\nFrom: news@example.com\n"
    "Subject: Weekly digest\nDate: Fri, 17 Jul 2026 08:00:00 +0000\n\n"
    "Nothing actionable here.\n"
)


@pytest.fixture(autouse=True)
async def clean(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "executor_mode", "dry_run")
    monkeypatch.setattr(settings, "dispatch_approval_limit", 10_000)
    # These tests are about the HUMAN dismissal gate. With the live .env now
    # carrying CC_AUDITOR_MODE=active (D8 graduation), an unpinned auditor
    # auto-confirms the very items these tests park — pin it off, like
    # demo_mode. The auditor has its own suites.
    monkeypatch.setattr(settings, "auditor_enabled", False)
    try:
        await _wipe()
    except Exception:  # noqa: BLE001
        yield
        return
    yield
    await _wipe()


async def _wipe():
    conn = await repo._conn()
    try:
        await conn.execute("delete from work_item where message_id like '<cc-dis-%'")
    finally:
        await conn.close()


async def _enroll_and_dispatch(model):
    await ledger.enroll_email(RAW)
    return await dispatcher.dispatch_once(model=model)


async def _row():
    conn = await repo._conn()
    try:
        r = await conn.fetchrow(
            "select id, state, payload from work_item where message_id = '<cc-dis-1@example.com>'"
        )
        d = dict(r)
        if isinstance(d["payload"], str):
            d["payload"] = json.loads(d["payload"])
        return d
    finally:
        await conn.close()


async def test_no_action_parks_dismiss_pending_with_rationale():
    out = await _enroll_and_dispatch(make_no_action_model())
    assert out["ok"] and out.get("dismiss_pending")

    row = await _row()
    assert row["state"] == "DISMISS_PENDING"
    assert "newsletter" in row["payload"]["dismissal_rationale"]


async def test_confirm_makes_it_terminal():
    await _enroll_and_dispatch(make_no_action_model())
    row = await _row()
    assert await repo.confirm_dismissal(row["id"]) is True
    assert (await _row())["state"] == "PROCESSED"
    # Confirming twice is a no-op refusal, not a crash.
    assert await repo.confirm_dismissal(row["id"]) is False


async def test_reopen_requeues_and_the_note_reaches_the_next_prompt():
    await _enroll_and_dispatch(make_no_action_model())
    row = await _row()
    assert await repo.reopen_work_item(row["id"], "This IS actionable — check TASKS-12.")

    reopened = await _row()
    assert reopened["state"] == "UNPROCESSED"

    # Next pass: a proposing model this time; the paused session's prompt must
    # carry the operator's note.
    out = await dispatcher.dispatch_once(model=make_spike_model())
    assert out["ok"] and out.get("deferred")
    state = await repo.load_paused_session(out["session_id"])
    dumped = json.dumps(state["messages"])
    assert "OPERATOR NOTE" in dumped
    assert "This IS actionable" in dumped


async def test_unconfirmed_dismissals_throttle_the_drain(monkeypatch):
    await _enroll_and_dispatch(make_no_action_model())

    # Assert relative to the DB's actual totals (never global counts — the dev
    # DB is shared): with the limit at the current total, the valve is closed;
    # confirming OUR dismissal drops the total below it.
    total = await repo.count_awaiting_human() + await repo.count_dismiss_pending()
    monkeypatch.setattr(settings, "dispatch_approval_limit", total)

    ok, reason = await dispatcher.check_valves()
    assert ok is False and "awaiting review" in reason

    row = await _row()
    await repo.confirm_dismissal(row["id"])
    ok, _ = await dispatcher.check_valves()
    assert ok is True


async def test_confirm_all_is_bulk_and_scoped_to_pending():
    await _enroll_and_dispatch(make_no_action_model())
    ids = await repo.confirm_all_dismissals()
    row = await _row()
    assert row["id"] in ids and row["state"] == "PROCESSED"
    assert await repo.confirm_all_dismissals() == []
