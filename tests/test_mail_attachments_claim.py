"""Mail attachments at claim time, against the real ledger (DL-216).

Enrollment marks a row; the claim reads the attachments AFTER the standing
rules and the relatedness checks; the stored text is the body plus the block
the agent reads; the body alone stays available to the rules; and the event
log records what was attached without its text.
"""

from __future__ import annotations

import json

import pytest

from central_command.config import settings
from central_command.contract.failures import is_transient
from central_command.db import repo
from central_command.ingest import dispatcher, ledger
from central_command.integrations import email_facade
from central_command.runtime.spike_model import make_spike_model
from tests.conftest import needs_pg
from tests.fake_world import fake_world

pytestmark = needs_pg

INVITE = (
    "BEGIN:VCALENDAR\r\nMETHOD:REQUEST\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:att-1\r\n"
    "SUMMARY:Planning review\r\nDTSTART:20260310T163000Z\r\nDTEND:20260310T173000Z\r\n"
    "LOCATION:Room 4, Example House\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
)


@pytest.fixture(autouse=True)
async def clean(monkeypatch):
    monkeypatch.setattr(settings, "test_model", True)
    fake_world(monkeypatch)
    monkeypatch.setattr(settings, "dispatch_approval_limit", 10_000)
    await _wipe()
    yield
    await _wipe()


async def _wipe():
    conn = await repo._conn()
    try:
        await conn.execute("delete from work_item where message_id like '<gmail-msg-attc%'")
    finally:
        await conn.close()


def _message(uuid: str, attachments: list[dict]) -> dict:
    return {
        "uuid": uuid, "conversation_id": f"thread{uuid}",
        "from": "Jane Doe <jane.doe@example.com>", "subject": "Planning review",
        "date": "2026-03-02T18:00:00.000Z", "snippet": "",
        "body_text": "", "body_html": "", "attachments": attachments,
        "has_attachments": bool(attachments),
    }


def _invite_att(attachment_id: str = "ATT1") -> dict:
    return {"filename": "invite.ics", "mime_type": "text/calendar", "size": len(INVITE),
            "attachment_id": attachment_id, "is_inline": False}


async def _row(uuid: str) -> dict:
    conn = await repo._conn()
    try:
        row = await conn.fetchrow(
            "select id, state, payload from work_item where message_id = $1",
            ledger.provider_message_id(uuid),
        )
    finally:
        await conn.close()
    out = dict(row)
    out["payload"] = out["payload"] if isinstance(out["payload"], dict) else json.loads(out["payload"])
    return out


def _mailbox(monkeypatch, message: dict, files: dict[str, bytes | Exception]):
    calls = {"messages": 0, "attachments": []}

    async def get_message(uuid):
        calls["messages"] += 1
        return message

    async def get_attachment(uuid, attachment_id):
        calls["attachments"].append(attachment_id)
        value = files[attachment_id]
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(email_facade, "get_message", get_message)
    monkeypatch.setattr(email_facade, "get_attachment", get_attachment)
    return calls


async def test_a_live_mail_with_an_attachment_is_marked_at_enrollment_and_read_at_claim(monkeypatch):
    """The shape that started this: a subject, an empty body, and invite.ics."""
    message = _message("attclive", [_invite_att()])
    calls = _mailbox(monkeypatch, message, {"ATT1": INVITE.encode()})

    await ledger.enroll_provider_message(message)
    enrolled = await _row("attclive")
    assert enrolled["payload"]["attachments_pending"] is True
    assert calls["attachments"] == [], "the poll enrolls; it reads no attachment"

    item = await repo.get_work_item(enrolled["id"])
    await ledger.hydrate_attachments(item)

    stored = (await _row("attclive"))["payload"]
    assert "attachments_pending" not in stored
    assert stored["text_body"].startswith("From: Jane Doe") and "ATTACHMENTS" not in stored["text_body"]
    assert "--- ATTACHMENTS (1)" in stored["text"]
    assert "Title: Planning review" in stored["text"] and "Where: Room 4, Example House" in stored["text"]
    assert stored["attachments"][0]["status"] == "read"
    assert stored["attachments"][0]["reader"] == "icalendar"
    assert calls["attachments"] == ["ATT1"]
    assert calls["messages"] == 0, "the claim needs no second read of the message"
    assert "attachment_refs" not in stored

    events = await repo.list_events_of_kinds(["work.attachments_read"])
    mine = [e for e in events if e["ref_id"] == enrolled["id"]]
    assert len(mine) == 1
    listed = mine[0]["payload"]["attachments"]
    assert listed[0]["name"] == "invite.ics" and listed[0]["status"] == "read"
    assert "text" not in listed[0], "the event log names the file, never its content"

    # A second pass is a no-op: nothing is fetched twice.
    await ledger.hydrate_attachments(await repo.get_work_item(enrolled["id"]))
    assert calls["attachments"] == ["ATT1"]


async def test_a_backlog_reference_gets_its_body_and_its_attachments_in_one_claim(monkeypatch):
    message = _message("attcbacklog", [_invite_att()])
    calls = _mailbox(monkeypatch, message, {"ATT1": INVITE.encode()})

    await ledger.enroll_provider_ref({"uuid": "attcbacklog", "conversation_id": "threadattcbacklog"})
    out = await dispatcher.dispatch_once(model=make_spike_model())
    assert out["ok"] is True

    row = await _row("attcbacklog")
    assert row["state"] == "PROCESSED"
    assert "Title: Planning review" in row["payload"]["text"]
    assert row["payload"]["attachments"][0]["name"] == "invite.ics"
    assert calls["attachments"] == ["ATT1"]
    assert calls["messages"] == 1, "a backlog row is read from the mailbox once, not twice"


async def test_a_mailbox_outage_during_the_read_leaves_the_row_marked_for_the_retry(monkeypatch):
    message = _message("attcdown", [_invite_att()])
    down = email_facade.EmailFacadeError("email façade attachment failed: HTTP 503", status_code=503)
    assert is_transient(down)
    _mailbox(monkeypatch, message, {"ATT1": down})

    await ledger.enroll_provider_message(message)
    enrolled = await _row("attcdown")
    with pytest.raises(email_facade.EmailFacadeError):
        await ledger.hydrate_attachments(await repo.get_work_item(enrolled["id"]))
    stored = (await _row("attcdown"))["payload"]
    assert stored["attachments_pending"] is True and "attachments" not in stored
    assert stored["attachment_refs"], "the list survives for the retry"


async def test_a_mail_without_attachments_is_untouched(monkeypatch):
    message = _message("attcnone", [])
    calls = _mailbox(monkeypatch, message, {})
    await ledger.enroll_provider_message(message)
    enrolled = await _row("attcnone")
    assert "attachments_pending" not in enrolled["payload"]
    await ledger.hydrate_attachments(await repo.get_work_item(enrolled["id"]))
    assert calls["messages"] == 0, "no second read of a mail that has nothing attached"
    assert (await _row("attcnone"))["payload"] == enrolled["payload"]


async def test_a_body_rule_matches_the_body_alone_in_sql_too(monkeypatch):
    """`repo.mail_rule_matches` is the SQL twin of `mail_rules.item_fields`:
    a preview or a sweep must not fold a mail on words that are only in its
    attachment."""
    message = _message("attcrule", [_invite_att()])
    _mailbox(monkeypatch, message, {"ATT1": INVITE.encode()})
    await ledger.enroll_provider_message(message)
    enrolled = await _row("attcrule")
    await ledger.hydrate_attachments(await repo.get_work_item(enrolled["id"]))
    stored = (await _row("attcrule"))["payload"]
    assert "Room 4, Example House" in stored["text"] and "Room 4" not in stored["text_body"]

    async def matched(phrase: str) -> bool:
        conn = await repo._conn()
        try:
            params: list = []
            where = repo._rule_where({"body_contains": phrase}, {}, params)
            return bool(await conn.fetchval(
                f"select count(*) from work_item w where w.id = ${len(params) + 1} and {where}",
                *params, enrolled["id"]))
        finally:
            await conn.close()

    assert not await matched("room 4, example house"), "only the attachment says that"


async def test_a_thread_sibling_is_named_as_carrying_files_and_keeps_its_mark(monkeypatch):
    """A sibling's attachments are not fetched inside another mail's claim."""
    first = _message("attcsiba", [])
    first["conversation_id"] = "threadattcsib"
    first["body_text"] = "Agenda attached in my next mail."
    second = _message("attcsibb", [_invite_att()])
    second["conversation_id"] = "threadattcsib"
    second["body_text"] = "Here it is."
    calls = _mailbox(monkeypatch, second, {"ATT1": INVITE.encode()})
    await ledger.enroll_provider_message(first)
    await ledger.enroll_provider_message(second)

    seen: dict = {}
    real = dispatcher._bundle_prompt

    def spy(item, siblings, **kwargs):
        seen["siblings"] = [s["payload"]["text"] for s in siblings]
        return real(item, siblings, **kwargs)

    monkeypatch.setattr(dispatcher, "_bundle_prompt", spy)
    out = await dispatcher.dispatch_once(model=make_spike_model())
    assert out["ok"] is True
    if seen.get("siblings"):  # whichever of the two was claimed first
        joined = "\n".join(seen["siblings"])
        if "Here it is." in joined:
            assert "listed but NOT READ" in joined and "- invite.ics (text/calendar" in joined
            assert calls["attachments"] == [], "the sibling's file was not fetched in this claim"
