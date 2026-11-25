"""The email's AGE rides into the triage prompt as a fact (DL-208, 2026-11-06).

The Date header was in the prompt and today's date at the end of the charter,
but nothing did the subtraction: in ten days of backlog triage, 22 of 27 task
proposals from mail older than six months were dismissed by the operator, and
18 of 78 graph episodes carried the proposal's own date as their reference
time. One deterministic line, same convention as the recurrence note: a fact
for the agent to weigh, no directive.
"""
from __future__ import annotations

from datetime import UTC, date, datetime

from central_command.db import repo
from central_command.ingest import dispatcher, ledger
from central_command.integrations import email_facade
from tests.conftest import needs_pg

TODAY = date(2026, 11, 6)


def test_a_months_old_email_is_dated_and_aged():
    item = {"received_at": datetime(2026, 1, 20, 23, 5, 26, tzinfo=UTC)}
    out = dispatcher._with_age_context("From: x\n\nbody", item, today=TODAY)
    assert out.startswith("From: x\n\nbody\n\n[queue context] This email was received on 2026-01-20, ")
    assert "days before today (2026-11-06)." in out
    # The count is the calendar difference in the host's zone (the same zone
    # the charter's "Today's date" stamp uses), so the two agree.
    sent_local = item["received_at"].astimezone().date()
    assert f"{(TODAY - sent_local).days} days before today" in out


def test_today_yesterday_and_a_future_date_read_honestly():
    one = {"received_at": datetime(2026, 11, 6, 12, 0, tzinfo=UTC)}
    assert "received on 2026-11-06, today (2026-11-06)." in dispatcher._with_age_context(
        "p", one, today=TODAY)
    yesterday = {"received_at": datetime(2026, 11, 5, 12, 0, tzinfo=UTC)}
    assert "1 day before today" in dispatcher._with_age_context("p", yesterday, today=TODAY)
    future = {"received_at": datetime(2026, 11, 9, 12, 0, tzinfo=UTC)}
    assert "3 days AFTER today (a clock disagrees)" in dispatcher._with_age_context(
        "p", future, today=TODAY)


def test_strings_and_naive_datetimes_are_accepted_and_nothing_else_adds_a_line():
    as_text = {"received_at": "2026-01-20T23:05:26+00:00"}
    assert "received on 2026-01-20" in dispatcher._with_age_context("p", as_text, today=TODAY)
    naive = {"received_at": datetime(2026, 1, 20, 23, 5, 26)}  # read as UTC, like Graphiti
    assert "received on 2026-01-20" in dispatcher._with_age_context("p", naive, today=TODAY)
    # No date (a hand-fed, header-less text; a refs-only backlog row): no line,
    # never a guess.
    assert dispatcher._with_age_context("p", {}, today=TODAY) == "p"
    assert dispatcher._with_age_context("p", {"received_at": None}, today=TODAY) == "p"
    assert dispatcher._with_age_context("p", {"received_at": "not a date"}, today=TODAY) == "p"


@needs_pg
async def test_the_claimed_row_carries_received_at_for_the_age_line():
    """The claim's RETURNING list is what the dispatcher sees; without
    `received_at` on it the line could never be written."""
    raw = (
        "From: Jane Doe <jane@example.com>\n"
        "To: operator@example.com\n"
        "Subject: Confirm your hostname\n"
        "Date: Tue, 20 Jan 2026 23:05:26 +0000\n"
        "Message-ID: <age-line-test@example.com>\n"
        "\n"
        "Please confirm within 30 days.\n"
    )
    enrolled = await ledger.enroll_email(raw, feed="live", allow_repeat=True)
    assert enrolled["enrolled"]
    row = await repo.claim_specific_work_item(enrolled["item_id"], "sess_age_line")
    assert row is not None
    assert row["received_at"] is not None
    assert row["received_at"].date() == date(2026, 1, 20)
    framed = dispatcher._with_age_context(row["payload"]["text"], row, today=TODAY)
    assert "[queue context] This email was received on 2026-01-20," in framed
    # Leave nothing in flight: a claimed item counts against the dispatcher's
    # concurrency, and the next test's claim would answer None.
    await repo.finish_work_item(row["id"], "PROCESSED", session_id="sess_age_line")


async def test_a_refs_only_row_hydrated_after_the_claim_gets_the_age_line(monkeypatch):
    """A backlog row is a refs-only stub when the claim's SQL runs — received_at
    NULL — and is hydrated afterwards from the provider. The dispatcher frames
    the prompt from the in-memory item, so the hydration must fill the blank
    there too: the row carried the date and the prompt still had no line
    (2026-11-06, 65 of 76 backlog sessions)."""
    async def fake_get_message(uuid):
        assert uuid == "provider-uuid-1"
        return {
            "uuid": uuid,
            "conversation_id": uuid,
            "from": "Jane Doe <jane@example.com>",
            "to": "operator@example.com",
            "subject": "Confirm your hostname",
            "date": "2025-09-02T15:46:45+00:00",
            "body_text": "Please confirm within 30 days.",
        }
    monkeypatch.setattr(email_facade, "get_message", fake_get_message)

    async def fake_persist(item_id, payload, subject, received_at):
        assert received_at is not None and received_at.date() == date(2025, 9, 2)
    monkeypatch.setattr(repo, "hydrate_work_item", fake_persist)

    item = {"id": "wi_refs_only", "received_at": None, "subject": None,
            "payload": {"provider_uuid": "provider-uuid-1", "deferred_fetch": True}}
    await ledger.hydrate_work_item(item)
    assert item["received_at"] is not None
    assert item["received_at"].date() == date(2025, 9, 2)
    framed = dispatcher._with_age_context(item["payload"]["text"], item, today=TODAY)
    assert "[queue context] This email was received on 2025-09-02," in framed
    assert "days before today (2026-11-06)." in framed

    # An enrollment-time date is never overwritten by the provider's.
    kept = {"id": "wi_dated", "received_at": datetime(2026, 1, 20, tzinfo=UTC),
            "subject": "s", "payload": {"provider_uuid": "provider-uuid-1"}}
    await ledger.hydrate_work_item(kept)
    assert kept["received_at"].date() == date(2026, 1, 20)
