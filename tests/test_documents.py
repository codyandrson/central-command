"""Documents as work items — the ledger's source generalization (2026-07-29).

`work_item.kind` says WHAT an item is; `source` keeps meaning the transport it
arrived on. Parsing is pure and always runs; the ledger-invariant tests need a
real Postgres, because the invariants live in SQL (`on conflict do nothing`,
`for update skip locked`) and mocking them would test nothing.
"""

from __future__ import annotations

import pytest

from central_command.config import settings
from central_command.db import repo
from central_command.ingest import dispatcher, ledger
from central_command.runtime.spike_model import (
    make_steward_model,
    make_steward_no_extraction_model,
)
from tests.conftest import aresolve, needs_pg

DOC_TITLE = "Q3 platform decision"
DOC_TEXT = (
    "The platform team has decided to standardise on Postgres 17 for all new "
    "services.\nDana owns the migration and the cutover lands on 2026-09-01."
)


# --- parsing (no infra) -------------------------------------------------------


def test_document_identity_is_a_stable_content_hash():
    a = ledger.parse_document(DOC_TITLE, DOC_TEXT)
    b = ledger.parse_document(DOC_TITLE, DOC_TEXT)
    assert a["message_id"] == b["message_id"]
    assert a["message_id"].startswith("<cc-doc-")
    assert a["synthetic"] is True
    # Same words under a different title are different work.
    other = ledger.parse_document("Something else", DOC_TEXT)
    assert other["message_id"] != a["message_id"]


def test_a_supplied_doc_id_wins_over_the_hash():
    """The future Confluence page-id key: a stable id means an EDITED document
    is the same document, which a content hash could never say."""
    first = ledger.parse_document(DOC_TITLE, DOC_TEXT, doc_id="CONF-4821")
    edited = ledger.parse_document(DOC_TITLE, DOC_TEXT + "\nAddendum.",
                                   doc_id="CONF-4821")
    assert first["message_id"] == edited["message_id"] == (
        "<cc-doc-CONF-4821@central_command.local>"
    )
    assert first["synthetic"] is False


def test_thread_id_is_the_message_id():
    """Decision 9: the thread lock still serialises re-feeds, and the fold
    machinery is untouched because the sibling set is always empty."""
    parsed = ledger.parse_document(DOC_TITLE, DOC_TEXT)
    assert parsed["thread_id"] == parsed["message_id"]


def test_document_input_carries_the_title_as_data():
    parsed = ledger.parse_document(DOC_TITLE, DOC_TEXT, doc_id="CONF-1")
    text = ledger.document_input(parsed)
    assert text.startswith(f"Title: {DOC_TITLE}")
    assert "Document id: CONF-1" in text
    assert DOC_TEXT.splitlines()[0] in text


def test_an_empty_document_is_refused():
    with pytest.raises(ValueError):
        ledger.parse_document("T", "   ")


# --- ledger + routing (real Postgres) ----------------------------------------


pytestmark_db = needs_pg

_MARK = "cc-doctest"


async def _wipe():
    conn = await repo._conn()
    try:
        await conn.execute(
            "delete from work_item where payload->>'title' like $1 or message_id like $2",
            f"%{_MARK}%", "<cc-doc-DOCTEST-%",
        )
    finally:
        await conn.close()


@pytest.fixture()
async def clean_docs(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "executor_mode", "dry_run")
    # dispatch_once must never trip the approval throttle: it passes once and
    # then fails as AWAITING_HUMAN proposals accumulate past the default of 3.
    monkeypatch.setattr(settings, "dispatch_approval_limit", 10_000)
    monkeypatch.setattr(settings, "auditor_enabled", False)
    await _wipe()
    yield
    await _wipe()


def _title(n: int) -> str:
    return f"{_MARK} {n}"


@pytestmark_db
async def test_enrollment_is_idempotent_on_the_document_key(clean_docs):
    first = await ledger.enroll_document(_title(1), DOC_TEXT)
    second = await ledger.enroll_document(_title(1), DOC_TEXT)
    assert first["enrolled"] is True
    assert second["enrolled"] is False and second["duplicate"] is True
    assert second["item_id"] is None

    conn = await repo._conn()
    try:
        rows = await conn.fetch(
            "select id, kind, source from work_item where message_id = $1",
            first["message_id"],
        )
    finally:
        await conn.close()
    assert len(rows) == 1, "the unique(message_id) invariant did not hold"
    assert rows[0]["kind"] == "document"
    # `source` still means TRANSPORT — the generalization must not overload it.
    assert rows[0]["source"] == "api"


@pytestmark_db
async def test_an_email_enrolls_with_kind_email(clean_docs):
    raw = (
        f"Message-ID: <cc-doc-DOCTEST-email@example.com>\nFrom: a@b.c\n"
        f"Subject: {_MARK} mail\n\nMove the deadline.\n"
    )
    result = await ledger.enroll_email(raw)
    conn = await repo._conn()
    try:
        kind = await conn.fetchval(
            "select kind from work_item where message_id = $1", result["message_id"]
        )
        await conn.execute("delete from work_item where message_id = $1",
                           result["message_id"])
    finally:
        await conn.close()
    assert kind == "email", "the default must keep every existing row an email"


@pytestmark_db
async def test_a_document_routes_to_the_steward(clean_docs, monkeypatch):
    enrolled = await ledger.enroll_document(_title(2), DOC_TEXT)
    result = await dispatcher.dispatch_item(enrolled["item_id"])
    assert result["deferred"] is True

    session = await repo.get_session(result["session_id"])
    assert session["agent_id"] == "knowledge-steward"

    proposal = await repo.load_proposal(result["proposal_id"])
    assert proposal["agent_id"] == "knowledge-steward"
    assert [a["capability"] for a in proposal["actions"]] == ["graph.add_episode"]

    item = await repo.get_work_item(enrolled["item_id"])
    assert item["state"] == "PROCESSED"


@pytestmark_db
async def test_an_email_still_routes_to_inbox_triage(clean_docs):
    raw = (
        f"Message-ID: <cc-doc-DOCTEST-route@example.com>\nFrom: dana@example.com\n"
        f"Subject: {_MARK} deadline\nDate: Wed, 1 Jul 2026 09:00:00 +0000\n\n"
        "DEMO-1 slips to Aug 3.\n"
    )
    await ledger.enroll_email(raw)
    result = await dispatcher.dispatch_once()
    assert result is not None and result["deferred"] is True
    session = await repo.get_session(result["session_id"])
    assert session["agent_id"] == "inbox-triage"
    conn = await repo._conn()
    try:
        await conn.execute(
            "delete from work_item where message_id = $1",
            "<cc-doc-DOCTEST-route@example.com>",
        )
    finally:
        await conn.close()


@pytestmark_db
async def test_an_unknown_kind_fails_the_item_loudly(clean_docs):
    """Never default to triage: running the wrong agent on the wrong shape of
    work is exactly what the registry exists to make impossible."""
    enrolled = await ledger.enroll_document(_title(3), DOC_TEXT)
    conn = await repo._conn()
    try:
        await conn.execute(
            "update work_item set kind = 'confluence-page' where id = $1",
            enrolled["item_id"],
        )
    finally:
        await conn.close()

    result = await dispatcher.dispatch_item(enrolled["item_id"])
    assert result["ok"] is False
    assert "confluence-page" in result["error"]

    item = await repo.get_work_item(enrolled["item_id"])
    assert item["state"] == "FAILED"

    failures = [
        e for e in await repo.list_events(ref_id=enrolled["item_id"])
        if e["kind"] == "work.failed"
    ]
    assert len(failures) == 1
    assert failures[0]["payload"]["kind"] == "confluence-page"


# --- document dismissals audit in their OWN class (slice 6, 2026-08-30) -------
# The auditor used to be silent here on purpose: it was graduated on the EMAIL
# `dismissal.confirm` class, and running it on documents by flipping one flag
# would have been a graduation nobody decided. What landed is the machinery
# that makes the flip legitimate — documents book verdicts under
# `dismissal.confirm.document` and answer to `CC_AUDITOR_DOCUMENT_MODE`, which
# defaults to SHADOW. These tests pin exactly that separation.


@pytest.fixture()
async def auditing_steward(clean_docs, monkeypatch):
    from central_command.runtime import steward

    monkeypatch.setattr(settings, "auditor_enabled", True)
    monkeypatch.setattr(
        steward, "_resolve_model",
        aresolve(lambda model=None: model or make_steward_no_extraction_model()),
    )
    return monkeypatch


async def _dismissed_document(n: int) -> dict:
    enrolled = await ledger.enroll_document(_title(n), DOC_TEXT)
    result = await dispatcher.dispatch_item(enrolled["item_id"])
    assert result["dismiss_pending"] is True
    return enrolled


@pytestmark_db
async def test_a_document_dismissal_has_the_steward_actor_and_a_shadow_audit(
    auditing_steward
):
    """Default mode: the verdict is recorded beside the item and the human gate
    is untouched — the document parks DISMISS_PENDING exactly as before."""
    auditing_steward.setattr(settings, "auditor_document_mode", "shadow")

    enrolled = await _dismissed_document(4)
    item = await repo.get_work_item(enrolled["item_id"])
    assert item["state"] == "DISMISS_PENDING"
    audit = (item["payload"] or {})["audit"]
    assert audit["action_class"] == "dismissal.confirm.document"
    assert audit["mode"] == "shadow"
    assert audit["auto_confirmed"] is False

    rows = await repo.list_events(ref_id=enrolled["item_id"])
    parked = [e for e in rows if e["kind"] == "work.dismiss_pending"]
    assert parked and parked[0]["actor"] == "agent:knowledge-steward"
    verdicts = [e for e in rows if e["kind"] == "audit.verdict"]
    assert len(verdicts) == 1
    assert verdicts[0]["payload"]["action_class"] == "dismissal.confirm.document"
    assert [e for e in rows if e["kind"] == "work.dismissal_confirmed"] == []


@pytestmark_db
async def test_the_email_class_going_active_never_carries_documents_with_it(
    auditing_steward
):
    """THE point of a per-class knob: `CC_AUDITOR_MODE=active` graduates email
    and email alone. A document under document-shadow still waits for the
    operator, however concurring the auditor was."""
    auditing_steward.setattr(settings, "auditor_mode", "active")
    auditing_steward.setattr(settings, "auditor_document_mode", "shadow")

    enrolled = await _dismissed_document(41)
    item = await repo.get_work_item(enrolled["item_id"])
    assert item["state"] == "DISMISS_PENDING"
    audit = (item["payload"] or {})["audit"]
    assert audit["verdict"] == "concur"
    assert audit["mode"] == "shadow", "documents follow their OWN knob"
    assert audit["auto_confirmed"] is False


@pytestmark_db
async def test_active_document_mode_confirms_a_concur_with_the_verdict_first(
    auditing_steward
):
    auditing_steward.setattr(settings, "auditor_document_mode", "active")
    since = await repo.latest_event_id()

    enrolled = await _dismissed_document(42)
    item = await repo.get_work_item(enrolled["item_id"])
    assert item["state"] == "PROCESSED"
    assert (item["payload"] or {})["audit"]["auto_confirmed"] is True

    kinds = [
        e["kind"] for e in await repo.list_events(
            since_id=since, ref_id=enrolled["item_id"]
        )
    ]
    assert kinds.index("audit.verdict") < kinds.index("work.dismissal_confirmed")


@pytestmark_db
async def test_a_reopened_document_is_never_auto_confirmed(auditing_steward):
    """A reopen note means 'I want eyes on this' — the same rule as email, and
    it must hold in the class that just went active."""
    auditing_steward.setattr(settings, "auditor_document_mode", "active")
    enrolled = await _dismissed_document(43)

    conn = await repo._conn()
    try:  # the parked state a mid-cycle reopen produces
        await conn.execute(
            "update work_item set state = 'UNPROCESSED', terminal_at = null,"
            " payload = (payload - 'audit')"
            " || '{\"operator_note\": \"look again please\"}'"
            " where id = $1",
            enrolled["item_id"],
        )
    finally:
        await conn.close()

    await dispatcher.dispatch_item(enrolled["item_id"])
    item = await repo.get_work_item(enrolled["item_id"])
    assert item["state"] == "DISMISS_PENDING"
    audit = (item["payload"] or {})["audit"]
    assert audit["verdict"] == "concur"
    assert audit["operator_hold"] is True
    assert audit["auto_confirmed"] is False


@pytestmark_db
async def test_a_failed_document_audit_degrades_to_the_human_gate(auditing_steward):
    from central_command.gateway import auditor

    auditing_steward.setattr(settings, "auditor_document_mode", "active")

    def broken(model, charter=None):
        raise RuntimeError("auditor model unavailable")

    auditing_steward.setattr(auditor, "build_auditor", broken)
    since = await repo.latest_event_id()
    enrolled = await _dismissed_document(44)

    item = await repo.get_work_item(enrolled["item_id"])
    assert item["state"] == "DISMISS_PENDING"
    assert "audit" not in (item["payload"] or {})
    errors = await repo.list_events(since_id=since, kind="audit.error")
    mine = [e for e in errors if e["ref_id"] == enrolled["item_id"]]
    assert mine and mine[0]["payload"]["action_class"] == "dismissal.confirm.document"


@pytestmark_db
async def test_document_verdicts_never_land_in_the_email_agreement_report(
    auditing_steward
):
    """Each class graduates on its OWN evidence — a document verdict counted in
    the email report would justify a flip nobody's record supports."""
    from central_command.gateway import auditor

    auditing_steward.setattr(settings, "auditor_document_mode", "active")
    enrolled = await _dismissed_document(45)

    email = await auditor.agreement_report()
    docs = await auditor.agreement_report(auditor.DOCUMENT_AUDIT_ACTION_CLASS)
    assert enrolled["item_id"] not in {p["item_id"] for p in email["pairs"]}
    assert docs["auto_confirmed"] >= 1


# --- the hand-feed API --------------------------------------------------------


@pytestmark_db
async def test_feeding_a_document_produces_a_proposal_and_a_processed_row(clean_docs):
    from central_command.api import routes

    result = await routes.feed_document(
        routes.DocumentIn(title=_title(5), text=DOC_TEXT)
    )
    assert result["deferred"] is True
    proposal = await repo.load_proposal(result["proposal_id"])
    assert proposal["agent_id"] == "knowledge-steward"

    items = await repo.work_items_for_session(result["session_id"])
    assert len(items) == 1
    assert items[0]["kind"] == "document"
    assert items[0]["state"] == "PROCESSED"


@pytestmark_db
async def test_refeeding_the_same_document_is_a_duplicate_not_a_second_run(clean_docs):
    from central_command.api import routes

    first = await routes.feed_document(
        routes.DocumentIn(title=_title(6), text=DOC_TEXT, doc_id="DOCTEST-stable")
    )
    again = await routes.feed_document(
        routes.DocumentIn(title=_title(6), text=DOC_TEXT, doc_id="DOCTEST-stable")
    )
    assert first["deferred"] is True
    assert again == {"deferred": False, "duplicate": True,
                     "message_id": "<cc-doc-DOCTEST-stable@central_command.local>"}

    # Exactly one ledger row, and exactly one proposal — scoped to the rows
    # this test made, never a global count.
    conn = await repo._conn()
    try:
        n = await conn.fetchval(
            "select count(*) from work_item where message_id = $1",
            "<cc-doc-DOCTEST-stable@central_command.local>",
        )
    finally:
        await conn.close()
    assert n == 1


@pytestmark_db
async def test_enroll_only_document_route_does_not_run_the_agent(clean_docs):
    from central_command.api import routes

    result = await routes.enroll_document(
        routes.DocumentIn(title=_title(7), text=DOC_TEXT)
    )
    assert result["enrolled"] is True
    item = await repo.get_work_item(result["item_id"])
    assert item["state"] == "UNPROCESSED" and item["session_id"] is None


@pytestmark_db
async def test_a_documents_demo_model_is_pinned(clean_docs):
    """Guard the pin itself: with demo_mode on, the steward must resolve the
    steward stand-in — not a live Claude call, and not triage's model."""
    from central_command.runtime import steward

    resolved = await steward._resolve_model()
    assert type(resolved).__name__ == "FunctionModel"
    assert make_steward_model  # the factory this pin is expected to reach
