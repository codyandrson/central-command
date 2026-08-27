"""Work Ledger + dispatcher tests (M6/M7).

Parsing tests are pure and always run. The ledger-invariant tests need a real
Postgres (the invariants live in SQL — `on conflict do nothing`, `for update
skip locked` — so mocking them would test nothing), and skip without one.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from central_command.db import repo
from central_command.ingest import ledger
from tests.conftest import needs_pg

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "emails"


# --- parsing (no infra) ------------------------------------------------------


# Parsing tests own their input. They used to read fixtures/emails/*.eml, which
# coupled them to demo data — rewriting a fixture's prose for a live run broke
# two tests that had nothing to do with the change.
_ROOT = """Message-ID: <root@example.com>
From: dana@example.com
Subject: Deadline moving
Date: Thu, 2 Jul 2026 11:02:00 +0000

Please move the deadline to 6 August 2026.
"""

_REPLY = """Message-ID: <reply@example.com>
In-Reply-To: <root@example.com>
References: <root@example.com>
From: dana@example.com
Subject: Re: Deadline moving

Correction: make it 7 August 2026.
"""


def test_parses_headers_and_body():
    parsed = ledger.parse_email(_ROOT)
    assert parsed["message_id"] == "<root@example.com>"
    assert parsed["subject"] == "Deadline moving"
    assert parsed["received_at"].year == 2026
    assert "6 August 2026" in parsed["body"]


def test_reply_joins_the_root_thread():
    root = ledger.parse_email(_ROOT)
    reply = ledger.parse_email(_REPLY)
    assert reply["message_id"] != root["message_id"]
    assert reply["thread_id"] == root["thread_id"] == root["message_id"]


def test_bare_text_gets_a_stable_synthetic_message_id():
    """The hand-fed path has no headers — enrollment must still be idempotent."""
    a = ledger.parse_email("please set the due date on TASKS-12")
    b = ledger.parse_email("please set the due date on TASKS-12")
    c = ledger.parse_email("something else entirely")
    assert a["message_id"] == b["message_id"]
    assert a["message_id"] != c["message_id"]


def test_bare_text_starting_like_a_header_keeps_its_body():
    """'Dana: the deadline slipped' parses as a header named 'Dana' with an empty
    body — which sent real Claude an empty prompt (400) on the hand-fed path.
    When parsing eats everything, the raw text is the body."""
    text = "Dana: the DEMO-1 deadline slipped a week — new due date Aug 3."
    parsed = ledger.parse_email(text)
    assert parsed["body"] == text
    assert text in ledger.agent_input(parsed)


def test_normalized_subject_strips_reply_prefixes():
    assert ledger.normalized_subject("Re: Fwd: Budget") == "budget"
    assert ledger.normalized_subject(None) == ""


def test_agent_input_carries_headers_as_data():
    text = ledger.agent_input(ledger.parse_email(_ROOT))
    assert "Subject: Deadline moving" in text
    assert "From: dana@example.com" in text
    assert "6 August 2026" in text


# --- ledger invariants (needs Postgres) --------------------------------------


pytestmark_db = needs_pg


@pytest.fixture()
async def clean_ledger():
    conn = await repo._conn()
    try:
        await conn.execute(
            "delete from work_item where message_id like '<cc-test-%' or source = 'fixture'"
        )
    finally:
        await conn.close()
    yield
    conn = await repo._conn()
    try:
        await conn.execute(
            "delete from work_item where message_id like '<cc-test-%' or source = 'fixture'"
        )
    finally:
        await conn.close()


def _raw(
    n: int,
    thread: str | None = None,
    subject: str | None = None,
    sender: str | None = None,
) -> str:
    # Distinct sender domain per item by default: the sender_key lock is real
    # now, and a shared example.com would serialize every test item.
    headers = [
        f"Message-ID: <cc-test-{n}@example.com>",
        f"From: {sender or f'sender@example{n}.com'}",
        f"Subject: {subject or f'Test item {n}'}",
        "Date: Wed, 1 Jul 2026 09:00:00 +0000",
    ]
    if thread:
        headers.append(f"References: <cc-test-{thread}@example.com>")
    return "\n".join(headers) + f"\n\nBody of test item {n}.\n"


@pytestmark_db
@pytest.mark.asyncio
async def test_enrollment_is_idempotent(clean_ledger):
    first = await ledger.enroll_email(_raw(1))
    second = await ledger.enroll_email(_raw(1))
    assert first["enrolled"] is True
    assert second["enrolled"] is False and second["duplicate"] is True


@pytestmark_db
@pytest.mark.asyncio
async def test_claim_is_exclusive(clean_ledger):
    """Two dispatchers racing the same queue must never get the same row."""
    await ledger.enroll_email(_raw(1))
    await ledger.enroll_email(_raw(2))

    a, b = await asyncio.gather(
        repo.claim_next_work_item("sess_a"), repo.claim_next_work_item("sess_b")
    )
    assert a is not None and b is not None
    assert a["id"] != b["id"]


@pytestmark_db
@pytest.mark.asyncio
async def test_one_session_per_thread(clean_ledger):
    """A thread with an item in flight is not handed out again (D17 groundwork)."""
    await ledger.enroll_email(_raw(1))
    await ledger.enroll_email(_raw(2, thread="1"))

    first = await repo.claim_next_work_item("sess_a")
    second = await repo.claim_next_work_item("sess_b")
    assert first is not None
    assert second is None, "sibling of an in-flight thread should not be claimable"

    await repo.finish_work_item(first["id"], "PROCESSED")
    assert await repo.claim_next_work_item("sess_c") is not None


@pytestmark_db
@pytest.mark.asyncio
async def test_release_returns_the_item_to_the_queue(clean_ledger):
    await ledger.enroll_email(_raw(1))
    claimed = await repo.claim_next_work_item("sess_a")
    await repo.release_work_item(claimed["id"], error="transient")

    again = await repo.claim_next_work_item("sess_b")
    assert again is not None and again["id"] == claimed["id"]
    assert again["attempts"] == 2, "attempts accumulate across retries"


@pytestmark_db
@pytest.mark.asyncio
async def test_stale_claims_are_reclaimed(clean_ledger):
    """A crashed process leaves rows CLAIMED; they must come back, not vanish."""
    await ledger.enroll_email(_raw(1))
    claimed = await repo.claim_next_work_item("sess_dead")
    assert claimed is not None
    assert await repo.claim_next_work_item("sess_b") is None

    recovered = await repo.reclaim_stale_work_items(older_than_seconds=0)
    assert recovered >= 1
    assert await repo.claim_next_work_item("sess_b") is not None


@pytestmark_db
@pytest.mark.asyncio
async def test_same_subject_locks_across_threads(clean_ledger):
    """Relatedness lock (2026-08-19): two UNRELATED threads with the same
    normalized subject serialize — the newest runs, the other waits."""
    await ledger.enroll_email(_raw(1, subject="Budget Q3"))
    await ledger.enroll_email(_raw(2, subject="Re: Fwd: budget q3"))

    first = await repo.claim_next_work_item("sess_a")
    assert first is not None
    assert await repo.claim_next_work_item("sess_b") is None, (
        "same normalized subject must not run concurrently"
    )

    await repo.finish_work_item(first["id"], "PROCESSED")
    assert await repo.claim_next_work_item("sess_c") is not None


@pytestmark_db
@pytest.mark.asyncio
async def test_subjectless_items_never_lock_each_other(clean_ledger):
    """An absent subject normalizes to NULL, never to a shared '' key."""
    await ledger.enroll_email(_raw(1, subject=" "))
    await ledger.enroll_email(_raw(2, subject=" "))

    a = await repo.claim_next_work_item("sess_a")
    b = await repo.claim_next_work_item("sess_b")
    assert a is not None and b is not None and a["id"] != b["id"]


@pytestmark_db
@pytest.mark.asyncio
async def test_dismiss_pending_holds_related_items(clean_ledger):
    """DISMISS_PENDING is an undecided operator verdict — it holds the lock."""
    await ledger.enroll_email(_raw(1))
    await ledger.enroll_email(_raw(2, thread="1"))

    first = await repo.claim_next_work_item("sess_a")
    await repo.mark_dismiss_pending(first["id"], "no action needed", None)
    assert await repo.claim_next_work_item("sess_b") is None

    await repo.finish_work_item(first["id"], "PROCESSED")
    assert await repo.claim_next_work_item("sess_b") is not None


@pytestmark_db
@pytest.mark.asyncio
async def test_parked_proposal_holds_related_items(clean_ledger):
    """A PROCESSED item whose proposal is not yet EXECUTED holds the lock — the
    park-to-decision AND the approve-to-execute windows are covered: related
    items start only after the world change LANDS (2026-08-19)."""
    await ledger.enroll_email(_raw(1))
    await ledger.enroll_email(_raw(2, thread="1"))

    agent_id = "cc-test-lock-agent"
    sess_id = "cc-test-lock-sess"
    prop_id = "cc-test-lock-prop"
    first = await repo.claim_next_work_item(sess_id)
    assert first is not None

    await repo.upsert_agent(agent_id, agent_id, "test-model", "test charter")
    conn = await repo._conn()
    try:
        await conn.execute(
            "insert into session (id, agent_id, status) values ($1, $2, 'AWAITING_HUMAN')"
            " on conflict (id) do nothing",
            sess_id, agent_id,
        )
    finally:
        await conn.close()
    try:
        await repo.save_proposal(
            prop_id, sess_id, agent_id, "test intent", [], [],
            "no effect", "cc-test-lock-idem",
        )
        await repo.finish_work_item(first["id"], "PROCESSED", session_id=sess_id)

        assert await repo.claim_next_work_item("sess_b") is None, (
            "a parked proposal must hold its thread until the operator decides"
        )

        # Approved is not executed: the lock must survive the whole window in
        # which the world change is still owed (incl. RETRY_PENDING outage parks).
        for status in ("APPROVED", "EXECUTING", "RETRY_PENDING"):
            await repo.set_proposal_status(prop_id, status)
            assert await repo.claim_next_work_item("sess_b") is None, (
                f"an unexecuted proposal ({status}) must still hold the thread"
            )

        await repo.set_proposal_status(prop_id, "EXECUTED")
        assert await repo.claim_next_work_item("sess_b") is not None
    finally:
        conn = await repo._conn()
        try:
            await conn.execute("delete from proposal where id = $1", prop_id)
            await conn.execute("delete from session where id = $1", sess_id)
            await conn.execute("delete from agent where id = $1", agent_id)
        finally:
            await conn.close()


@pytestmark_db
@pytest.mark.asyncio
async def test_subject_key_column_matches_python_normalization(clean_ledger):
    """The schema's GENERATED subject_key and ledger.normalized_subject are the
    same function in two languages — this pins them together."""
    subject = "Re: Fwd: re:  Budget  Q3"
    await ledger.enroll_email(_raw(1, subject=subject))
    conn = await repo._conn()
    try:
        key = await conn.fetchval(
            "select subject_key from work_item where message_id = '<cc-test-1@example.com>'"
        )
    finally:
        await conn.close()
    assert key == ledger.normalized_subject(subject) == "budget  q3"


@pytestmark_db
@pytest.mark.asyncio
async def test_same_sender_domain_locks_across_threads(clean_ledger):
    """The sender family (registrable domain) is a lock key: healthplan mail
    from two mailboxes on two subdomains serializes (2026-08-19)."""
    await ledger.enroll_email(_raw(1, sender="Healthplan <no-reply@notices.healthplan-test.org>"))
    await ledger.enroll_email(_raw(2, sender="Pharmacy <refills@rx.healthplan-test.org>"))

    first = await repo.claim_next_work_item("sess_a")
    assert first is not None
    assert await repo.claim_next_work_item("sess_b") is None, (
        "same sender domain must not run concurrently"
    )

    await repo.finish_work_item(first["id"], "PROCESSED")
    assert await repo.claim_next_work_item("sess_c") is not None


@pytestmark_db
@pytest.mark.asyncio
async def test_generic_domains_lock_on_full_address_only(clean_ledger):
    """Two strangers on gmail.com are unrelated; the same gmail ADDRESS locks."""
    await ledger.enroll_email(_raw(1, sender="A <alice-cc-test@gmail.com>"))
    await ledger.enroll_email(_raw(2, sender="B <bob-cc-test@gmail.com>"))
    await ledger.enroll_email(_raw(3, sender="A <alice-cc-test@gmail.com>"))

    a = await repo.claim_next_work_item("sess_a")
    b = await repo.claim_next_work_item("sess_b")
    assert a is not None and b is not None, "different gmail senders must not lock"
    assert await repo.claim_next_work_item("sess_c") is None, (
        "the SAME gmail address must lock"
    )


@pytestmark_db
@pytest.mark.asyncio
async def test_post_hydration_recheck_sees_keys_the_claim_could_not(clean_ledger):
    """A refs-only stub has no subject/sender keys at claim time; hydration
    materializes them, and `work_item_related_busy` re-asks the same predicate
    (2026-08-19 — the backlog-stub gap, closed without an eager sweep)."""
    await ledger.enroll_email(_raw(1, subject="Kaiser physical therapy referral"))
    first = await repo.claim_next_work_item("sess_a")
    assert first is not None

    # Simulate a stub: no subject at enroll (→ NULL subject_key), so the SQL
    # lock lets it through despite the in-flight relative.
    await ledger.enroll_email(_raw(2, subject=" "))
    second = await repo.claim_next_work_item("sess_b")
    assert second is not None
    assert await repo.work_item_related_busy(second["id"]) is False

    # "Hydration" reveals the shared subject — the re-check must now freeze it.
    conn = await repo._conn()
    try:
        await conn.execute(
            "update work_item set subject = $2 where id = $1",
            second["id"], "Re: Kaiser physical therapy referral",
        )
    finally:
        await conn.close()
    assert await repo.work_item_related_busy(second["id"]) is True

    await repo.finish_work_item(first["id"], "PROCESSED")
    assert await repo.work_item_related_busy(second["id"]) is False


def test_cosine_similarity():
    from central_command.ingest.dispatcher import _cosine

    assert _cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert _cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert _cosine([1.0, 1.0], [1.0, 0.0]) == pytest.approx(0.7071, abs=1e-3)
    assert _cosine([0.0, 0.0], [1.0, 0.0]) == 0.0  # zero vector never divides


@pytestmark_db
@pytest.mark.asyncio
async def test_semantic_freeze_matches_in_flight_item(clean_ledger, monkeypatch):
    """The fuzzy layer: a claimed item embedding-close to an in-flight one is
    frozen; an orthogonal one proceeds; demo mode skips entirely."""
    from central_command.ingest import dispatcher
    from central_command.integrations import neo4j_writer

    await ledger.enroll_email(_raw(1, subject="Kaiser physical therapy referral"))
    await ledger.enroll_email(_raw(2, subject="Amazon order: resistance bands"))

    first = await repo.claim_next_work_item("sess_a")
    await repo.set_work_item_embedding(first["id"], [1.0, 0.0, 0.0])
    second = await repo.claim_next_work_item("sess_b")
    assert second is not None, "no exact key overlaps — the hard lock stays out of the way"

    monkeypatch.setattr(dispatcher.settings, "demo_mode", False)
    monkeypatch.setattr(dispatcher.settings, "semantic_freeze_enabled", True)
    monkeypatch.setattr(dispatcher.settings, "semantic_freeze_threshold", 0.70)

    async def similar(text):
        return [0.95, 0.05, 0.0]

    monkeypatch.setattr(neo4j_writer, "embed", similar)
    freeze = await dispatcher._semantic_freeze_check(second)
    assert freeze is not None and freeze["related_item_id"] == first["id"]
    assert freeze["score"] >= 0.70

    # The vector persisted at first check is reused; an orthogonal one proceeds.
    await repo.set_work_item_embedding(second["id"], [0.0, 1.0, 0.0])
    second["embedding"] = [0.0, 1.0, 0.0]
    assert await dispatcher._semantic_freeze_check(second) is None

    monkeypatch.setattr(dispatcher.settings, "demo_mode", True)
    second["embedding"] = None
    assert await dispatcher._semantic_freeze_check(second) is None, (
        "demo mode must skip the gate"
    )


@pytestmark_db
@pytest.mark.asyncio
async def test_semantic_freeze_fails_open(clean_ledger, monkeypatch):
    """An unreachable embedder must never block dispatch."""
    from central_command.ingest import dispatcher
    from central_command.integrations import neo4j_writer

    await ledger.enroll_email(_raw(1))
    item = await repo.claim_next_work_item("sess_a")

    monkeypatch.setattr(dispatcher.settings, "demo_mode", False)
    monkeypatch.setattr(dispatcher.settings, "semantic_freeze_enabled", True)

    async def down(text):
        return None

    monkeypatch.setattr(neo4j_writer, "embed", down)
    assert await dispatcher._semantic_freeze_check(item) is None

    async def boom(text):
        raise RuntimeError("proxy exploded")

    monkeypatch.setattr(neo4j_writer, "embed", boom)
    assert await dispatcher._semantic_freeze_check(item) is None


@pytestmark_db
@pytest.mark.asyncio
async def test_backlog_enrollment_is_resumable(clean_ledger):
    first = await ledger.enroll_fixture_dir(FIXTURES)
    second = await ledger.enroll_fixture_dir(FIXTURES)
    assert first["enrolled"] + first["duplicates"] == second["duplicates"]
    assert second["enrolled"] == 0, "re-running a sweep must add nothing"
