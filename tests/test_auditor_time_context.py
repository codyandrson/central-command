"""The dismissal auditor gets triage's time context and treats the triage
agent's reason as a claim, not a fact (DL-231, 2026-11-22).

A sample of the auditor's concurrences found two reasoning defects: on old
mail it read the mail's own date as today and "corrected" a triage agent that
had the age right (triage was given the age line in v2.82.6/v2.82.9; the
auditor never was), and it concurred on facts no mail contained and a
tool-less agent could not check, taken from the triage agent's stated reason.
Every text here is synthetic.
"""

from __future__ import annotations

from datetime import date

from pydantic_ai import capture_run_messages

from central_command.config import settings
from central_command.db import repo
from central_command.gateway import auditor
from central_command.ingest import dispatcher, ledger
from central_command.runtime.auditor_profile import CHARTER
from tests.conftest import needs_pg
from tests.fake_world import fake_world

TODAY = date(2026, 11, 22)

OLD_MAIL = (
    "Message-ID: <cc-audtime-1@example.com>\nFrom: claims@example-audtime.com\n"
    "Subject: Your claim\nDate: Mon, 13 Jan 2025 09:00:00 +0000\n\n"
    "We will review your claim next week and write to you with a decision.\n"
)


def test_the_charter_states_the_claim_not_fact_rule_and_the_time_rule():
    low = " ".join(CHARTER.lower().split())
    # The triage agent's reason is a claim to test, never evidence.
    assert "claim to test" in low and "never evidence" in low
    # A concur rests on the mail itself; an unverifiable fact is challenged,
    # and the challenge names it.
    assert "what the email itself shows" in low
    assert "does not contain" in low and "challenge" in low
    assert "name the fact you could not verify" in low
    # The mail's own dates are in the past relative to the reference time.
    assert "reference time" in low and "past" in low
    assert "never read the email's date as today" in low


async def test_the_age_comes_from_the_shared_helper(monkeypatch):
    """One age calculation: the auditor calls the dispatcher's own
    `email_age_line` on the dispatcher's own clock seam."""
    seen = {}

    def fake_line(item, today=None):
        seen["item"], seen["today"] = item, today
        return "AGE-FROM-THE-SHARED-HELPER"

    async def fake_item(item_id):
        return {"id": item_id, "received_at": "2025-01-13T09:00:00+00:00"}

    monkeypatch.setattr(dispatcher, "email_age_line", fake_line)
    monkeypatch.setattr(dispatcher, "_today_local", lambda: TODAY)
    monkeypatch.setattr(repo, "get_work_item", fake_item)
    block = await auditor.time_context("wi_x")
    assert "AGE-FROM-THE-SHARED-HELPER" in block
    assert seen["today"] == TODAY and seen["item"]["id"] == "wi_x"
    assert "today is 2026-11-22" in block


async def test_the_input_labels_the_reason_as_a_claim(monkeypatch):
    async def no_item(item_id):
        return None

    monkeypatch.setattr(repo, "get_work_item", no_item)
    monkeypatch.setattr(dispatcher, "_today_local", lambda: TODAY)
    prompt = await auditor.audit_prompt(
        "wi_x", "Your claim", "We will review your claim.", "the claim was closed"
    )
    low = prompt.lower()
    assert "claim" in low and "not a fact" in low and "nothing in it is verified" in low
    assert '"the claim was closed"' in prompt
    assert "a fact the email does not contain" in low and "challenge" in low
    assert "which fact you could not verify" in low
    # No date known: said so, never guessed.
    assert "received date is not recorded" in low
    assert "never read them as today" in low


@needs_pg
async def test_an_old_mails_audit_input_carries_the_reference_time_and_age(monkeypatch):
    monkeypatch.setattr(settings, "test_model", True)
    fake_world(monkeypatch)
    monkeypatch.setattr(settings, "auditor_enabled", True)
    monkeypatch.setattr(settings, "auditor_mode", "shadow")
    monkeypatch.setattr(dispatcher, "_today_local", lambda: TODAY)
    conn = await repo._conn()
    try:
        await conn.execute("delete from work_item where message_id like '<cc-audtime-%'")
    finally:
        await conn.close()

    enrolled = await ledger.enroll_email(OLD_MAIL)
    item_id = enrolled["item_id"]
    row = await repo.get_work_item(item_id)
    expected_age = dispatcher.email_age_line(row, TODAY)
    assert expected_age and "2025-01-13" in expected_age and "days before today" in expected_age

    with capture_run_messages() as messages:
        record = await auditor.audit_dismissal(
            item_id,
            "Your claim",
            row["payload"].get("text") or OLD_MAIL,
            "the claim was already closed last month",
        )
    assert record is not None and record["verdict"] in ("concur", "challenge")
    prompt = "\n".join(
        str(p.content)
        for m in messages
        for p in m.parts
        if getattr(p, "part_kind", "") == "user-prompt"
    )
    # The same sentence triage is given, against the same reference day.
    assert expected_age in prompt
    assert "today is 2026-11-22" in prompt
    assert "in the PAST relative to the reference time" in prompt
    assert "NOT a fact" in prompt
    assert '"the claim was already closed last month"' in prompt
