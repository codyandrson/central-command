"""The auditor on BULK dismissals — the trust-tier rung the operator flipped
on 2026-08-17 (design: docs/superpowers/specs/2026-08-17-bulk-dismissal-design.md,
where "auto-confirm via auditor" was listed deferred).

The properties, one per test: agreement executes through the ONE approval seam
with the auditor named as approver; disagreement, an auditor error and shadow
mode all leave the proposal exactly where it was — AWAITING_HUMAN with its
folds still merely RESERVED; and the audit re-derives from the rows the
proposal actually reserved (FOLD_PENDING), never from the samples the agent
pinned into its own args.

Real Postgres for the same reason the sibling suite uses it (the reservation is
SQL); the email façade is always monkeypatched, and the auditor's verdict comes
from the deterministic demo model — "ACTION REQUIRED" in the hydrated body is
what makes it challenge.
"""

from __future__ import annotations

import pytest

from central_command.api import routes
from central_command.config import settings
from central_command.db import repo
from central_command.gateway import auditor
from central_command.ingest import dispatcher, ledger
from central_command.ingest.ledger import provider_message_id
from central_command.integrations import email_facade
from tests.conftest import needs_pg
from tests.test_bulk_dismiss_agent import _bulk_model

pytestmark = needs_pg

_UUIDS = ["bdaud-1", "bdaud-2", "bdaud-3", "bdaud-4"]
_QUERY = "from:news@shop.example"


@pytest.fixture(autouse=True)
async def clean(monkeypatch):
    monkeypatch.setattr(settings, "executor_mode", "live")  # the fold commit IS the effect
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "dispatch_approval_limit", 10_000)
    monkeypatch.setattr(settings, "dispatch_concurrency", 1)
    monkeypatch.setattr(settings, "auditor_enabled", True)
    monkeypatch.setattr(settings, "auditor_mode", "active")
    await _wipe()
    yield
    await _wipe()


async def _wipe():
    conn = await repo._conn()
    try:
        await conn.execute("delete from work_item where message_id like '<gmail-msg-bdaud-%'")
    finally:
        await conn.close()


def _patch_facade(monkeypatch, body: str = "Deals inside. Unsubscribe at the bottom."):
    """Records every hydration, so a test can assert WHICH messages the auditor
    re-derived from."""
    hydrated: list[str] = []

    async def fake_list_refs(query):
        return [{"uuid": u, "conversation_id": u} for u in _UUIDS]

    async def fake_get_message(uuid):
        hydrated.append(uuid)
        return {
            "uuid": uuid, "conversation_id": uuid,
            "from": "news@shop.example", "subject": "Your weekly digest",
            "date": "2026-08-01T09:00:00+00:00", "body_text": body,
        }

    monkeypatch.setattr(email_facade, "list_refs", fake_list_refs)
    monkeypatch.setattr(email_facade, "get_message", fake_get_message)
    return hydrated


async def _propose(monkeypatch, **kw) -> tuple[dict, list[str]]:
    for u in _UUIDS:
        await ledger.enroll_provider_ref({"uuid": u, "conversation_id": u})
    hydrated = _patch_facade(monkeypatch, **kw)
    result = await dispatcher.dispatch_once(model=_bulk_model(_QUERY))
    assert result and result["deferred"], result
    return result, hydrated


async def _states(message_ids: list[str]) -> list[str]:
    conn = await repo._conn()
    try:
        return [
            await conn.fetchval(
                "select state from work_item where message_id = $1", mid)
            for mid in message_ids
        ]
    finally:
        await conn.close()


async def test_concur_auto_approves_through_the_gateway_and_commits_the_folds(monkeypatch):
    since = await repo.latest_event_id()
    result, _ = await _propose(monkeypatch)

    prop = await repo.load_proposal(result["proposal_id"])
    assert prop["status"] == "EXECUTED"
    # The mechanism is named honestly on the record — not "human:lee".
    assert prop["provenance"]["approver"] == "auditor"

    assert set(await _states(result["folded_message_ids"])) == {"FOLDED"}

    events = await repo.list_events(since_id=since, ref_id=result["proposal_id"])
    kinds = [e["kind"] for e in events]
    # The verdict precedes the decision it authorises; the decision precedes
    # the execution (the M8 ordering rule, on both hops).
    assert kinds.index("audit.verdict") < kinds.index("proposal.decided")
    assert kinds.index("proposal.decided") < kinds.index("proposal.executed")
    decided = next(e for e in events if e["kind"] == "proposal.decided")
    assert decided["actor"] == "auditor" and decided["payload"]["verdict"] == "approved"
    verdict = next(e for e in events if e["kind"] == "audit.verdict")
    assert verdict["payload"]["action_class"] == "work.bulk_dismiss"
    # Emitted BEFORE the approval, so the event cannot carry the outcome —
    # the field is absent, never a permanently-false lie; proposal.executed's
    # approver is the record of auto-confirmation.
    assert "auto_confirmed" not in verdict["payload"]


async def test_a_challenge_leaves_the_proposal_and_its_folds_exactly_as_they_were(monkeypatch):
    result, _ = await _propose(
        monkeypatch, body="ACTION REQUIRED: confirm your delivery address today."
    )

    prop = await repo.load_proposal(result["proposal_id"])
    assert prop["status"] == "AWAITING_HUMAN"
    assert set(await _states(result["folded_message_ids"])) == {"FOLD_PENDING"}

    verdicts = await repo.list_events(
        kind="audit.verdict", ref_id=result["proposal_id"])
    assert verdicts and verdicts[-1]["payload"]["verdict"] == "challenge"


async def test_an_auditor_error_degrades_to_the_human_gate(monkeypatch):
    async def boom(model=None):
        raise RuntimeError("auditor model unavailable")

    monkeypatch.setattr(auditor, "_resolve_model", boom)
    result, _ = await _propose(monkeypatch)

    prop = await repo.load_proposal(result["proposal_id"])
    assert prop["status"] == "AWAITING_HUMAN"
    assert set(await _states(result["folded_message_ids"])) == {"FOLD_PENDING"}
    errors = await repo.list_events(kind="audit.error", ref_id=result["proposal_id"])
    assert errors, "an auditor failure must be loud on the log"


async def test_shadow_mode_records_the_verdict_but_approves_nothing(monkeypatch):
    monkeypatch.setattr(settings, "auditor_mode", "shadow")
    result, _ = await _propose(monkeypatch)

    prop = await repo.load_proposal(result["proposal_id"])
    assert prop["status"] == "AWAITING_HUMAN"
    verdict = (await repo.list_events(
        kind="audit.verdict", ref_id=result["proposal_id"]))[-1]["payload"]
    assert verdict["verdict"] == "concur" and verdict["mode"] == "shadow"
    assert "auto_confirmed" not in verdict


async def test_the_audit_samples_the_reserved_rows_not_the_proposals_own_samples(monkeypatch):
    """The proposal pins its samples at propose time; the auditor must judge the
    set that is ACTUALLY reserved right now. Release one reserved row and the
    audit must not re-derive from it."""
    monkeypatch.setattr(settings, "auditor_mode", "shadow")  # keep the rows put
    result, hydrated = await _propose(monkeypatch)
    released = result["folded_message_ids"][0]
    still_reserved = result["folded_message_ids"][1:]

    conn = await repo._conn()
    try:
        await conn.execute(
            """update work_item set state = 'UNPROCESSED', folded_into = null
                where message_id = $1""", released)
    finally:
        await conn.close()

    hydrated.clear()
    record = await auditor.audit_bulk_dismissal(result["proposal_id"])
    assert record is not None and record["covered"] == len(still_reserved)
    assert record["sampled_message_ids"] == still_reserved
    assert [provider_message_id(u) for u in hydrated] == still_reserved
    assert provider_message_id(hydrated[0]) != released


async def test_the_verdict_reaches_the_cockpit_on_the_proposal_row(monkeypatch):
    """Wire shape, asserted on the BACKEND: the cockpit hand-declares its own
    interfaces, so a field the gateway never sends is invisible to tsc and to
    every frontend test (CLAUDE.md's untyped-RPC bite mark)."""
    monkeypatch.setattr(settings, "auditor_mode", "shadow")
    result, _ = await _propose(monkeypatch)

    row = await routes.get_proposal(result["proposal_id"])
    assert row["audit"]["verdict"] == "concur"
    assert row["audit"]["action_class"] == "work.bulk_dismiss"
    assert row["audit"]["rationale"]
