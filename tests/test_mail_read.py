"""mail-read (2026-09-02): the search cross-reads the façade's matches against
the ledger and reports what the queue knows; the read returns the message as
fed plus its decision locators. Offline — façade and repo are stubbed."""

import pytest

from central_command.ingest.ledger import provider_message_id
from central_command.runtime import tools


@pytest.mark.asyncio
async def test_mail_search_reports_queue_state_per_match(monkeypatch):
    from central_command.db import repo
    from central_command.integrations import email_facade

    async def list_refs(query):
        return [{"uuid": "u1"}, {"uuid": "u2"}, {"uuid": "u3"}]

    async def items(message_ids):
        assert message_ids == [provider_message_id(u) for u in ("u1", "u2", "u3")]
        return [
            {"id": "wi_1", "state": "PROCESSED", "received_at": "2026-03-01",
             "sender": "Jane Doe <jane@example.com>", "subject": "Data",
             "session_id": "sess_a", "folded_into": None,
             "dismissal_rationale": "nothing to do"},
            {"id": "wi_2", "state": "UNPROCESSED", "received_at": "2026-03-02",
             "sender": "Jane Doe <jane@example.com>", "subject": "Code",
             "session_id": None, "folded_into": None, "dismissal_rationale": None},
        ]

    monkeypatch.setattr(email_facade, "list_refs", list_refs)
    monkeypatch.setattr(repo, "mail_items_for_message_ids", items)
    out = await tools.mail_search(None, "from:jane@example.com")
    assert "3 message(s) match" in out and "2 of them are in the queue" in out
    assert "1 never enrolled" in out
    assert "wi_1 [PROCESSED]" in out and "session=sess_a" in out
    assert "dismissed: “nothing to do”" in out
    assert "wi_2 [UNPROCESSED]" in out


@pytest.mark.asyncio
async def test_mail_read_returns_the_message_as_fed_with_locators(monkeypatch):
    from central_command.db import repo

    async def item(ref):
        assert ref == "wi_1"
        return {"id": "wi_1", "message_id": "<m1>", "state": "PROCESSED",
                "received_at": "2026-03-01", "sender": "Jane Doe <jane@example.com>",
                "subject": "Data", "session_id": "sess_a", "folded_into": None,
                "terminal_at": "2026-03-01", "provider_uuid": "u1",
                "dismissal_rationale": None, "operator_note": None,
                "audit": {"verdict": "concur", "rationale": "casual share"},
                "text": "From: Jane Doe\nSubject: Data\n\nhello"}

    monkeypatch.setattr(repo, "mail_item", item)
    out = await tools.mail_read(None, "wi_1")
    assert "session=sess_a" in out and "provider_uuid=u1" in out
    assert "audit: concur — casual share" in out
    assert out.endswith("\n\nhello")


@pytest.mark.asyncio
async def test_mail_read_falls_back_to_the_mailbox_and_says_so(monkeypatch):
    from central_command.db import repo
    from central_command.integrations import email_facade

    async def item(ref):
        return None

    async def get_message(uuid):
        return {"from": "x@example.com", "subject": "s", "date": "d", "body_text": "b"}

    monkeypatch.setattr(repo, "mail_item", item)
    monkeypatch.setattr(email_facade, "get_message", get_message)
    out = await tools.mail_read(None, "u9")
    assert out.startswith("NOT ENROLLED")
