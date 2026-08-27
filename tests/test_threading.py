"""Thread-aware batching tests (M9).

The property that matters: **folding can delay an email but never drop one.**
A fold is a claim of coverage, not an outcome — siblings sit in FOLD_PENDING
until the covering proposal is approved (→ FOLDED) or rejected (→ back to
UNPROCESSED). These tests drive that lifecycle with a deterministic folding
model, so no API key is needed.
"""

from __future__ import annotations

import pytest

from central_command.config import settings
from central_command.db import repo
from central_command.gateway import gateway
from central_command.ingest import dispatcher, ledger
from central_command.runtime.deps import ThreadContext, TriageDeps
from central_command.runtime.spike_model import make_folding_model, make_spike_model
from central_command.runtime.tools import record_thread_decision
from tests.conftest import needs_pg

pytestmark = needs_pg

ROOT = "<cc-thr-root@example.com>"
REPLY = "<cc-thr-reply@example.com>"


def _msg(message_id: str, body: str, in_reply_to: str | None = None) -> str:
    headers = [
        f"Message-ID: {message_id}",
        "From: dana@example.com",
        "Subject: Sprint planning moved",
        "Date: Thu, 2 Jul 2026 11:02:00 +0000",
    ]
    if in_reply_to:
        headers += [f"In-Reply-To: {in_reply_to}", f"References: {in_reply_to}"]
    return "\n".join(headers) + f"\n\n{body}\n"


@pytest.fixture(autouse=True)
async def clean(monkeypatch):
    monkeypatch.setattr(settings, "executor_mode", "dry_run")  # never touch real Jira
    # `feed_email` resolves its own model, so without this the offline suite
    # would call the real Claude API (and spend money) whenever a key is present
    # in .env. Tests must stay offline no matter how the environment is set up.
    monkeypatch.setattr(settings, "demo_mode", True)
    # These tests drive dispatch_once, which consults the approval throttle. Left
    # to the default limit they would pass or fail depending on how many
    # proposals happen to be sitting in the inbox from earlier runs — so pin the
    # valve open and let the throttle have its own dedicated tests.
    monkeypatch.setattr(settings, "dispatch_approval_limit", 10_000)
    monkeypatch.setattr(settings, "dispatch_concurrency", 1)
    await _wipe()
    yield
    await _wipe()


async def _wipe():
    conn = await repo._conn()
    try:
        # Hand-fed tests mint <cc-manual-…> ids; header-less text hashes to
        # <cc-synthetic-…>. Clear all three or row counts leak between tests.
        await conn.execute(
            """delete from work_item
                where message_id like '<cc-thr-%'
                   or message_id like '<cc-manual-%'
                   or message_id like '<cc-synthetic-%'"""
        )
    finally:
        await conn.close()


async def _enroll_thread():
    """Root, then a reply that supersedes it — the canonical fold case."""
    await ledger.enroll_email(_msg(ROOT, "Sprint planning moves to Thursday."))
    await ledger.enroll_email(
        _msg(REPLY, "Correction: Thursday is out, let's do Friday 10:00.", in_reply_to=ROOT)
    )


async def _rows_like(pattern: str) -> list[dict]:
    conn = await repo._conn()
    try:
        return [
            dict(r)
            for r in await conn.fetch(
                "select id, message_id, state, source from work_item where message_id like $1",
                pattern,
            )
        ]
    finally:
        await conn.close()


async def _state(message_id: str) -> str:
    conn = await repo._conn()
    try:
        return await conn.fetchval(
            "select state from work_item where message_id = $1", message_id
        )
    finally:
        await conn.close()


# --- the survey --------------------------------------------------------------


async def test_siblings_are_surveyed_and_locked():
    await _enroll_thread()
    claimed = await repo.claim_next_work_item("sess_a")

    siblings = await repo.thread_siblings(claimed["thread_id"], claimed["id"])
    assert len(siblings) == 1, "the other thread message should be surveyed"
    assert siblings[0]["payload"]["text"]  # full body, not just a subject

    # And the thread stays locked while one item is in flight.
    assert await repo.claim_next_work_item("sess_b") is None


async def test_bundle_prompt_includes_sibling_bodies():
    await _enroll_thread()
    claimed = await repo.claim_next_work_item("sess_a")
    siblings = await repo.thread_siblings(claimed["thread_id"], claimed["id"])

    prompt = dispatcher._bundle_prompt(claimed, siblings)
    assert siblings[0]["message_id"] in prompt
    assert "Friday" in prompt or "Thursday" in prompt
    assert "record_thread_decision" in prompt


# --- the fold lifecycle ------------------------------------------------------


async def test_fold_is_pending_until_approved_then_committed():
    await _enroll_thread()
    result = await dispatcher.dispatch_once(model=make_folding_model())
    assert result and result["ok"]
    assert result["folded_message_ids"], "the folding model should have folded the sibling"

    folded_id = result["folded_message_ids"][0]
    assert await _state(folded_id) == "FOLD_PENDING", "not terminal before approval"

    await gateway.approve_and_execute(
        result["session_id"], result["proposal_id"], model=make_spike_model()
    )
    assert await _state(folded_id) == "FOLDED"


async def test_rejecting_the_covering_proposal_returns_siblings_to_the_queue():
    """The safety property: a wrong fold costs a delay, never a dropped email."""
    await _enroll_thread()
    result = await dispatcher.dispatch_once(model=make_folding_model())
    folded_id = result["folded_message_ids"][0]
    assert await _state(folded_id) == "FOLD_PENDING"

    await gateway.reject_with_feedback(
        result["session_id"], result["proposal_id"], "Wrong — handle these separately.",
        model=make_spike_model(),
    )
    assert await _state(folded_id) == "UNPROCESSED", "released back for its own turn"

    # And it is genuinely claimable again, not just relabelled.
    assert await repo.claim_next_work_item("sess_next") is not None


async def test_fold_pending_keeps_the_thread_locked():
    await _enroll_thread()
    await dispatcher.dispatch_once(model=make_folding_model())
    # The claimed item is now PROCESSED and the sibling FOLD_PENDING — nothing on
    # this thread should be handed out while the fold awaits a decision.
    assert await repo.claim_next_work_item("sess_b") is None


async def test_single_email_path_is_unchanged():
    """No siblings → no thread context, no fold, exactly the Phase-1 behaviour."""
    await ledger.enroll_email(_msg("<cc-thr-solo@example.com>", "Please move DEMO-1."))
    result = await dispatcher.dispatch_once(model=make_spike_model())
    assert result["ok"] and result["deferred"]
    assert result["folded_message_ids"] == []


async def test_the_email_path_announces_its_proposal_exactly_once():
    """The emit moved into `runtime/proposals.park_proposal` (2026-07-25) so the
    coach and task paths would stop forgetting it. The email path is the one
    that already worked, so it is the one a refactor could silently break —
    either losing the event or, with the dispatcher's old emit left behind,
    double-logging it. Scoped to the proposal this test created."""
    await ledger.enroll_email(_msg("<cc-thr-emit@example.com>", "Please move DEMO-1."))
    since = await repo.latest_event_id()
    result = await dispatcher.dispatch_once(model=make_spike_model())
    assert result["deferred"]

    created = [
        e for e in await repo.list_events(since_id=since, kind="proposal.created")
        if e["ref_id"] == result["proposal_id"]
    ]
    assert len(created) == 1, "exactly one — not zero, and not two"
    assert created[0]["actor"] == "agent:inbox-triage"
    # The email path's own context must survive the move.
    assert created[0]["payload"]["item_id"] == result["item_id"]
    assert created[0]["payload"]["session_id"] == result["session_id"]


# --- the hand-fed path goes through the ledger too ---------------------------


async def test_hand_fed_email_lands_in_the_ledger():
    """`POST /api/emails` used to bypass the ledger entirely — hand-fed mail got
    no durable row, no thread awareness, no provenance of having been seen."""
    from central_command.api import routes

    result = await routes.feed_email(routes.EmailIn(text="Please move DEMO-1 to Aug 3."))
    assert result["deferred"] and result["proposal_id"]

    rows = await _rows_like("<cc-manual-%")
    assert len(rows) == 1, "hand-fed email must leave exactly one ledger row"
    assert rows[0]["state"] == "PROCESSED"
    assert rows[0]["source"] == "api"


async def test_hand_feeding_the_same_text_twice_is_allowed():
    """The demo button feeds identical text repeatedly. Header-less text has no
    real identity, so a content hash would wrongly swallow the second click."""
    from central_command.api import routes

    a = await routes.feed_email(routes.EmailIn(text="Same words every time."))
    b = await routes.feed_email(routes.EmailIn(text="Same words every time."))
    assert a["proposal_id"] != b["proposal_id"]

    # Scope to the rows this test made — asserting on a global count would break
    # on any unrelated row left in the dev database.
    manual = await _rows_like("<cc-manual-%")
    assert len(manual) == 2, "identical hand-fed text should create two work items"


async def test_hand_fed_email_with_a_real_message_id_still_dedupes():
    """But when the text *does* carry an identity, honour it."""
    from central_command.api import routes

    raw = _msg("<cc-thr-realid@example.com>", "Move DEMO-1.")
    first = await routes.feed_email(routes.EmailIn(text=raw))
    second = await routes.feed_email(routes.EmailIn(text=raw))
    assert first["deferred"] is True
    assert second.get("duplicate") is True, "a known Message-ID must not reprocess"
    assert len(await _rows_like("<cc-thr-realid@%")) == 1


async def test_hand_fed_email_respects_the_thread_lock():
    """Skipping the queue is the operator's call; running two sessions on one
    thread is a correctness bug no matter who asked."""
    await _enroll_thread()
    claimed = await repo.claim_next_work_item("sess_a")
    assert claimed is not None

    sibling = (await repo.thread_siblings(claimed["thread_id"], claimed["id"]))[0]
    conn = await repo._conn()
    try:
        sibling_item_id = await conn.fetchval(
            "select id from work_item where message_id = $1", sibling["message_id"]
        )
    finally:
        await conn.close()

    assert await dispatcher.dispatch_item(sibling_item_id) is None


# --- the guard ---------------------------------------------------------------


async def test_agent_cannot_fold_a_message_it_was_not_shown():
    """Folding an unsurveyed message would remove it from the queue without
    anyone having read it — the tool must refuse."""

    class _Ctx:
        deps = TriageDeps(thread=ThreadContext(thread_id="t", sibling_message_ids=["<a>"]))

    ctx = _Ctx()
    out = await record_thread_decision(
        ctx, fold=True, covered_message_ids=["<a>", "<not-surveyed>"], rationale="x"
    )
    assert "Rejected" in out
    assert ctx.deps.decision is None, "a refused fold must not be recorded"

    ok = await record_thread_decision(
        ctx, fold=True, covered_message_ids=["<a>"], rationale="supersedes"
    )
    assert "Rejected" not in ok
    assert ctx.deps.decision.fold is True


async def test_fold_false_records_no_coverage():
    class _Ctx:
        deps = TriageDeps(thread=ThreadContext(thread_id="t", sibling_message_ids=["<a>"]))

    ctx = _Ctx()
    await record_thread_decision(ctx, fold=False, covered_message_ids=["<a>"], rationale="distinct")
    assert ctx.deps.decision.fold is False
    assert ctx.deps.decision.covered_message_ids == []
