"""Auditor scaffold tests (Phase 4, D8/D12).

The properties: the auditor re-derives the no-action claim from the raw email;
shadow mode records verdicts without touching any gate; active mode may close
only CONCURRED dismissals and never an operator-reopened item; verdicts hit
the log before the state changes they authorise; and an auditor failure
degrades to the human gate instead of wedging the drain.
"""

from __future__ import annotations

import json

import pytest

from central_command.config import settings
from central_command.db import repo
from central_command.gateway import auditor
from central_command.ingest import dispatcher, ledger
from central_command.runtime.spike_model import make_no_action_model
from tests.conftest import needs_pg

pytestmark = needs_pg

# The demo auditor challenges on "TASKS-\d+" / "action required", else concurs.
CLEAN = (
    # Distinct sender domains: a shared one would sender-lock item 2 behind
    # item 1's DISMISS_PENDING (the relatedness lock, 2026-08-19).
    "Message-ID: <cc-aud-1@example.com>\nFrom: news@example-news.com\n"
    "Subject: Weekly digest\nDate: Fri, 17 Jul 2026 08:00:00 +0000\n\n"
    "Nothing to see this week.\n"
)
ACTIONABLE = (
    "Message-ID: <cc-aud-2@example.com>\nFrom: dana@example-dana.com\n"
    "Subject: Deadline\nDate: Fri, 17 Jul 2026 09:00:00 +0000\n\n"
    "ACTION REQUIRED: move the launch review to next Friday.\n"
)


@pytest.fixture(autouse=True)
async def clean(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "executor_mode", "dry_run")
    monkeypatch.setattr(settings, "dispatch_approval_limit", 10_000)
    monkeypatch.setattr(settings, "auditor_enabled", True)
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
        await conn.execute("delete from work_item where message_id like '<cc-aud-%'")
    finally:
        await conn.close()


async def _row(message_id: str) -> dict:
    conn = await repo._conn()
    try:
        r = await conn.fetchrow(
            "select id, state, payload from work_item where message_id = $1", message_id
        )
        d = dict(r)
        if isinstance(d["payload"], str):
            d["payload"] = json.loads(d["payload"])
        return d
    finally:
        await conn.close()


async def test_shadow_mode_records_but_never_confirms(monkeypatch):
    monkeypatch.setattr(settings, "auditor_mode", "shadow")
    await ledger.enroll_email(CLEAN)
    await dispatcher.dispatch_once(model=make_no_action_model())

    row = await _row("<cc-aud-1@example.com>")
    assert row["state"] == "DISMISS_PENDING"  # the human gate is untouched
    audit = row["payload"]["audit"]
    assert audit["verdict"] == "concur" and audit["mode"] == "shadow"
    assert audit["auto_confirmed"] is False


async def test_active_mode_confirms_a_concur_with_the_verdict_logged_first(monkeypatch):
    monkeypatch.setattr(settings, "auditor_mode", "active")
    since = await repo.latest_event_id()
    await ledger.enroll_email(CLEAN)
    await dispatcher.dispatch_once(model=make_no_action_model())

    row = await _row("<cc-aud-1@example.com>")
    assert row["state"] == "PROCESSED"
    assert row["payload"]["audit"]["auto_confirmed"] is True

    events = await repo.list_events(since_id=since, ref_id=row["id"])
    kinds = [e["kind"] for e in events]
    assert kinds.index("audit.verdict") < kinds.index("work.dismissal_confirmed")
    confirmed = next(e for e in events if e["kind"] == "work.dismissal_confirmed")
    assert confirmed["actor"] == "auditor"
    assert confirmed["payload"]["by"] == "auditor"


async def test_active_mode_parks_a_challenge_for_the_operator(monkeypatch):
    monkeypatch.setattr(settings, "auditor_mode", "active")
    await ledger.enroll_email(ACTIONABLE)
    await dispatcher.dispatch_once(model=make_no_action_model())

    row = await _row("<cc-aud-2@example.com>")
    assert row["state"] == "DISMISS_PENDING"  # a challenge always reaches the human
    assert row["payload"]["audit"]["verdict"] == "challenge"


async def test_operator_reopened_items_are_never_auto_confirmed(monkeypatch):
    """A reopen note means 'I want eyes on this' — even a concurring auditor in
    active mode must hand the item back to the operator."""
    monkeypatch.setattr(settings, "auditor_mode", "active")
    await ledger.enroll_email(CLEAN)
    await dispatcher.dispatch_once(model=make_no_action_model())
    # Active + concur just auto-confirmed it; the operator disagrees and reopens.
    row = await _row("<cc-aud-1@example.com>")
    conn = await repo._conn()
    try:  # restore the parked state the reopen path would produce mid-cycle
        await conn.execute(
            "update work_item set state = 'UNPROCESSED', terminal_at = null,"
            " payload = payload || '{\"operator_note\": \"look again please\"}'"
            " where id = $1",
            row["id"],
        )
    finally:
        await conn.close()

    await dispatcher.dispatch_once(model=make_no_action_model())
    row = await _row("<cc-aud-1@example.com>")
    assert row["state"] == "DISMISS_PENDING"  # concur recorded, but held for the human
    assert row["payload"]["audit"]["verdict"] == "concur"
    assert row["payload"]["audit"]["operator_hold"] is True
    assert row["payload"]["audit"]["auto_confirmed"] is False


async def test_auditor_failure_degrades_to_the_human_gate(monkeypatch):
    monkeypatch.setattr(settings, "auditor_mode", "active")

    def broken(model):
        raise RuntimeError("auditor model unavailable")

    monkeypatch.setattr(auditor, "build_auditor", broken)
    since = await repo.latest_event_id()
    await ledger.enroll_email(CLEAN)
    result = await dispatcher.dispatch_once(model=make_no_action_model())

    assert result["ok"] is True  # the drain is not wedged
    row = await _row("<cc-aud-1@example.com>")
    assert row["state"] == "DISMISS_PENDING"  # no verdict -> the human gate holds
    assert "audit" not in row["payload"]
    errors = [e for e in await repo.list_events(since_id=since, kind="audit.error")]
    assert errors and errors[0]["ref_id"] == row["id"]


async def test_disabled_auditor_changes_nothing(monkeypatch):
    monkeypatch.setattr(settings, "auditor_enabled", False)
    await ledger.enroll_email(CLEAN)
    await dispatcher.dispatch_once(model=make_no_action_model())

    row = await _row("<cc-aud-1@example.com>")
    assert row["state"] == "DISMISS_PENDING"
    assert "audit" not in row["payload"]


async def test_backlog_audit_route_covers_parked_dismissals(monkeypatch):
    """Items parked before the auditor was enabled get verdicts on demand."""
    from central_command.api import routes

    monkeypatch.setattr(settings, "auditor_enabled", False)
    await ledger.enroll_email(CLEAN)
    await ledger.enroll_email(ACTIONABLE)
    await dispatcher.dispatch_once(model=make_no_action_model())
    await dispatcher.dispatch_once(model=make_no_action_model())

    monkeypatch.setattr(settings, "auditor_enabled", True)
    monkeypatch.setattr(settings, "auditor_mode", "active")
    out = await routes.audit_parked_dismissals()

    # Our two items audited: the clean one confirmed, the actionable one parked.
    # (Counts may include other parked rows in a shared dev DB, so assert on
    # our rows, never on the totals.)
    assert out["audited"] >= 2
    assert (await _row("<cc-aud-1@example.com>"))["state"] == "PROCESSED"
    challenged = await _row("<cc-aud-2@example.com>")
    assert challenged["state"] == "DISMISS_PENDING"
    assert challenged["payload"]["audit"]["verdict"] == "challenge"
