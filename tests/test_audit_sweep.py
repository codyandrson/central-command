"""The audit sweep — the guarantee behind the inline audit hooks.

The inline hooks fire once, at park time; a restart's CancelledError (a
BaseException their `except Exception` never sees) or a transient 5xx leaves
the item DISMISS_PENDING / the bulk proposal AWAITING_HUMAN with no verdict
and nothing ever retried (found live 2026-08-18: 8 orphans across 5 restarts).
The sweep re-audits exactly those; a recorded CHALLENGE is never re-rolled,
and an operator-reopened item is audited but never auto-confirmed.

Orphans are made honestly: dispatch with the auditor DISABLED, which parks
the same no-verdict shape a cancelled audit leaves behind.
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
from tests.test_bulk_dismiss_audit import _propose  # noqa: F401 — reuses its facade patch

pytestmark = needs_pg

CLEAN = (
    "Message-ID: <cc-swp-1@example.com>\nFrom: news@example.com\n"
    "Subject: Weekly digest\nDate: Fri, 17 Jul 2026 08:00:00 +0000\n\n"
    "Nothing to see this week.\n"
)
ACTIONABLE = (
    "Message-ID: <cc-swp-2@example.com>\nFrom: dana@example.com\n"
    "Subject: Deadline\nDate: Fri, 17 Jul 2026 09:00:00 +0000\n\n"
    "ACTION REQUIRED: move the launch review to next Friday.\n"
)


@pytest.fixture(autouse=True)
async def clean(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "executor_mode", "dry_run")
    monkeypatch.setattr(settings, "dispatch_approval_limit", 10_000)
    monkeypatch.setattr(settings, "auditor_mode", "active")
    await _wipe()
    yield
    await _wipe()


async def _wipe():
    conn = await repo._conn()
    try:
        await conn.execute("delete from work_item where message_id like '<cc-swp-%'")
        await conn.execute("delete from work_item where message_id like '<gmail-msg-bdaud-%'")
    finally:
        await conn.close()


async def _park_orphan(monkeypatch, raw: str) -> dict:
    """Dispatch to DISMISS_PENDING with the auditor off — the orphan shape."""
    monkeypatch.setattr(settings, "auditor_enabled", False)
    await ledger.enroll_email(raw)
    result = await dispatcher.dispatch_once(model=make_no_action_model())
    assert result and result.get("dismiss_pending"), result
    monkeypatch.setattr(settings, "auditor_enabled", True)
    return result


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


async def test_sweep_audits_and_confirms_an_orphaned_dismissal(monkeypatch):
    await _park_orphan(monkeypatch, CLEAN)

    swept = await auditor.audit_sweep()
    assert swept["items"] >= 1

    row = await _row("<cc-swp-1@example.com>")
    assert row["state"] == "PROCESSED"
    assert row["payload"]["audit"]["auto_confirmed"] is True


async def test_sweep_never_rerolls_a_recorded_challenge(monkeypatch):
    monkeypatch.setattr(settings, "auditor_enabled", True)
    await ledger.enroll_email(ACTIONABLE)
    await dispatcher.dispatch_once(model=make_no_action_model())
    row = await _row("<cc-swp-2@example.com>")
    assert row["state"] == "DISMISS_PENDING"
    assert row["payload"]["audit"]["verdict"] == "challenge"
    verdicts_before = len(await repo.list_events(kind="audit.verdict", ref_id=row["id"]))

    await auditor.audit_sweep()
    after = await _row("<cc-swp-2@example.com>")
    assert after["state"] == "DISMISS_PENDING"  # the challenge IS the verdict
    assert len(await repo.list_events(kind="audit.verdict", ref_id=row["id"])) == verdicts_before


async def test_sweep_audits_but_never_confirms_a_reopened_item(monkeypatch):
    result = await _park_orphan(monkeypatch, CLEAN)
    conn = await repo._conn()
    try:
        await conn.execute(
            """update work_item
                  set payload = payload || '{"operator_note": "look closer"}'::jsonb
                where id = $1""",
            result["item_id"],
        )
    finally:
        await conn.close()

    swept = await auditor.audit_sweep()
    assert swept["items"] >= 1

    row = await _row("<cc-swp-1@example.com>")
    assert row["state"] == "DISMISS_PENDING"  # opined, never closed
    assert row["payload"]["audit"]["operator_hold"] is True
    assert row["payload"]["audit"]["auto_confirmed"] is False


async def test_sweep_audits_and_approves_an_orphaned_bulk_proposal(monkeypatch):
    monkeypatch.setattr(settings, "executor_mode", "live")  # the fold commit IS the effect
    monkeypatch.setattr(settings, "dispatch_concurrency", 1)
    monkeypatch.setattr(settings, "auditor_enabled", False)
    result, _ = await _propose(monkeypatch)
    monkeypatch.setattr(settings, "auditor_enabled", True)
    assert (await repo.load_proposal(result["proposal_id"]))["status"] == "AWAITING_HUMAN"

    swept = await auditor.audit_sweep()
    assert swept["proposals"] >= 1

    prop = await repo.load_proposal(result["proposal_id"])
    assert prop["status"] == "EXECUTED"
    assert prop["provenance"]["approver"] == "auditor"


async def test_sweep_is_a_noop_when_the_auditor_is_off(monkeypatch):
    await _park_orphan(monkeypatch, CLEAN)
    monkeypatch.setattr(settings, "auditor_enabled", False)
    assert await auditor.audit_sweep() == {"items": 0, "proposals": 0}
    assert (await _row("<cc-swp-1@example.com>"))["state"] == "DISMISS_PENDING"
