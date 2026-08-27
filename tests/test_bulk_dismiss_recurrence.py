"""Recurrence context for triage (2026-08-17): supply the FACT that a sender
has recurred, no directive language — Myprotein was dismissed individually
152 times with no way for the agent to know it recurred. Real Postgres, like
test_bulk_dismiss.py: the count query's exclusion of bulk-dismissal rows and
the state/column filters live in SQL, so mocking would test nothing.
"""

from __future__ import annotations

import json

import pytest

from central_command.db import repo
from central_command.ingest import dispatcher
from tests.conftest import needs_pg

pytestmark = needs_pg

_SENDER = "recur@example.com"
_OTHER_SENDER = "other@example.com"


@pytest.fixture(autouse=True)
async def clean():
    await _wipe()
    yield
    await _wipe()


async def _wipe():
    conn = await repo._conn()
    try:
        await conn.execute("delete from work_item where message_id like '<recur-%'")
    finally:
        await conn.close()


async def _seed_processed_dismissal(n: int, sender: str, rationale: str) -> None:
    """A PROCESSED row carrying a dismissal_rationale, the shape
    `mark_dismiss_pending` + confirm leaves behind."""
    conn = await repo._conn()
    try:
        await conn.execute(
            """
            insert into work_item
              (id, message_id, feed, source, subject, payload, state, terminal_at)
            values ($1, $2, 'backlog', 'fixture', 'sub', $3::jsonb, 'PROCESSED', now())
            """,
            f"wi_recur_{n}",
            f"<recur-{n}@example.com>",
            json.dumps({"from": sender, "text": "body", "dismissal_rationale": rationale}),
        )
    finally:
        await conn.close()


async def test_count_excludes_bulk_rationale_and_other_senders():
    await _seed_processed_dismissal(1, _SENDER, "not interested")
    await _seed_processed_dismissal(2, _SENDER, "not interested")
    await _seed_processed_dismissal(3, _SENDER, "operator bulk dismissal: from:promo")
    await _seed_processed_dismissal(4, _OTHER_SENDER, "not interested")

    n = await repo.count_prior_individual_dismissals(_SENDER)
    assert n == 2


async def test_context_line_present_at_threshold_absent_below():
    await _seed_processed_dismissal(1, _SENDER, "no")
    await _seed_processed_dismissal(2, _SENDER, "no")

    item = {"payload": {"from": _SENDER, "text": "hello"}}
    prompt = await dispatcher._with_recurrence_context("hello", item)
    assert prompt == "hello"  # only 2 prior — below the >=3 threshold

    await _seed_processed_dismissal(3, _SENDER, "no")
    prompt = await dispatcher._with_recurrence_context("hello", item)
    assert prompt == (
        "hello\n\n[queue context] 3 earlier emails from this sender were "
        "dismissed individually, one decision each."
    )
    for banned in ("prefer", "should", "consider", "must"):
        assert banned not in prompt.lower()


async def test_context_line_absent_without_sender():
    item = {"payload": {"text": "hello"}}
    prompt = await dispatcher._with_recurrence_context("hello", item)
    assert prompt == "hello"


async def test_stored_payload_text_is_unchanged():
    """The ledger row is the durable record of the email as received — the
    recurrence line rides only the in-memory prompt handed to the run."""
    await _seed_processed_dismissal(1, _SENDER, "no")
    await _seed_processed_dismissal(2, _SENDER, "no")
    await _seed_processed_dismissal(3, _SENDER, "no")

    item = {"payload": {"from": _SENDER, "text": "hello"}}
    await dispatcher._with_recurrence_context("hello", item)
    assert item["payload"]["text"] == "hello"

    conn = await repo._conn()
    try:
        stored = await conn.fetchval(
            "select payload->>'text' from work_item where id = 'wi_recur_1'"
        )
    finally:
        await conn.close()
    assert stored == "body"
