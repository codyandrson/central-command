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
            {"id": "wi_1", "message_id": provider_message_id("u1"),
             "state": "PROCESSED", "received_at": "2026-03-01",
             "sender": "Jane Doe <jane@example.com>", "subject": "Data",
             "session_id": "sess_a", "folded_into": None,
             "dismissal_rationale": "nothing to do"},
            {"id": "wi_2", "message_id": provider_message_id("u2"),
             "state": "UNPROCESSED", "received_at": "2026-03-02",
             "sender": "Jane Doe <jane@example.com>", "subject": "Code",
             "session_id": None, "folded_into": None, "dismissal_rationale": None},
        ]

    fetched = []

    async def get_message(uuid):
        fetched.append(uuid)
        return {"date": "2026-03-03", "from": "Sam Rivers <sam@example.com>",
                "to": "Jane Doe <jane@example.com>", "subject": "Re: Data",
                "snippet": "Sent  it\nyesterday."}

    monkeypatch.setattr(email_facade, "list_refs", list_refs)
    monkeypatch.setattr(email_facade, "get_message", get_message)
    monkeypatch.setattr(repo, "mail_items_for_message_ids", items)
    out = await tools.mail_search(None, "from:jane@example.com")
    # A match the queue never enrolled (the operator's own reply, here) is
    # listed from the mailbox with the id `mail_read` opens, not only counted:
    # an agent that can see a reply exists and cannot read it asks the
    # operator what the mailbox already says (replay 2026-11-19).
    assert fetched == ["u3"]
    assert "Not in the queue (1 of 1 shown):" in out
    assert ("- u3 [not in the queue] 2026-03-03 from Sam Rivers <sam@example.com> "
            "to Jane Doe <jane@example.com>: Re: Data — Sent it yesterday.") in out
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


@pytest.mark.asyncio
async def test_mail_read_fetches_an_unhydrated_backlog_row_from_the_mailbox(monkeypatch):
    """The backlog is enrolled as refs only (deferred_fetch); the body arrives
    at claim time. A read of such a row must open the mailbox, not hand back
    "(no message text recorded)" — that is what made a 2026-09-09 bulk
    dismissal an unread set."""
    from central_command.db import repo
    from central_command.integrations import email_facade

    async def item(ref):
        return {"id": "wi_7", "message_id": "<m7>", "state": "UNPROCESSED",
                "received_at": None, "sender": None, "subject": None,
                "session_id": None, "folded_into": None, "terminal_at": None,
                "provider_uuid": "u7", "dismissal_rationale": None,
                "operator_note": None, "audit": None, "text": None}

    async def get_message(uuid):
        assert uuid == "u7"
        return {"uuid": "u7", "conversation_id": "c7", "from": "Jane Doe <jane@example.com>",
                "subject": "Session", "date": "2026-09-08T22:31:35Z",
                "body_text": "Here's the link"}

    monkeypatch.setattr(repo, "mail_item", item)
    monkeypatch.setattr(email_facade, "get_message", get_message)
    out = await tools.mail_read(None, "wi_7")
    assert "wi_7 [UNPROCESSED] not yet fetched" in out
    assert "ENROLLED, not yet fetched by the queue" in out
    assert "Subject: Session" in out and out.endswith("Here's the link")
    assert "(no message text recorded)" not in out


@pytest.mark.asyncio
async def test_a_provider_refusal_reaches_the_agent_as_text_and_an_outage_still_parks(monkeypatch):
    """DL-205: the façade answers a refusal the provider states in words (a
    search matching more than the page cap, an unparseable query) with 422,
    and `mail_search` hands the agent the narrowing advice it always carried;
    a 503 (auth, rate limit, transport, anything unknown) is still an outage
    and still parks the read. Before 2026-11-04 both arrived as HTTP 500
    "Error in workflow", so every broad `from:` search was an 'n8n outage'."""
    from central_command.integrations import email_facade

    async def refused(query):
        raise email_facade.EmailFacadeError(
            'email façade list failed: HTTP 422 {"message":"lib-email-provider: list '
            'pagination cap hit (5 pages fetched, more remain) - refusing to silently '
            'truncate; narrow scope_query"}', status_code=422)

    monkeypatch.setattr(email_facade, "list_refs", refused)
    out = await tools.mail_search(None, "from:myprotein")
    assert out.startswith("mail search failed (")
    assert "pagination cap hit" in out and "narrow it" in out

    async def down(query):
        raise email_facade.EmailFacadeError(
            'email façade list failed: HTTP 503 {"message":"lib-email-provider: '
            'provider auth failed (401) - check the shared email credential"}',
            status_code=503)

    monkeypatch.setattr(email_facade, "list_refs", down)
    # Outside a parkable run (no deps on the ctx) the wrapper re-raises the
    # ORIGINAL error, tagged with the dependency the unit's own DOWN path
    # parks on — never the narrowing text.
    with pytest.raises(email_facade.EmailFacadeError) as info:
        await tools.mail_search(None, "from:myprotein")
    assert "n8n" in getattr(info.value, tools._DEPENDENCIES_ATTR, ())


@pytest.mark.asyncio
async def test_mail_search_bounds_the_matches_it_lists_from_outside_the_queue(monkeypatch):
    """Each listed match outside the queue costs one mailbox call, so a search
    lists the first few and says how many there are; a fetch the provider
    refuses is a line, and an outage still parks the read."""
    from central_command.db import repo
    from central_command.integrations import email_facade

    async def list_refs(query):
        return [{"uuid": f"u{i}"} for i in range(12)]

    async def items(message_ids):
        return []

    fetched = []

    async def get_message(uuid):
        fetched.append(uuid)
        if uuid == "u1":
            raise email_facade.EmailFacadeError("message gone", status_code=422)
        return {"date": "2026-03-03", "from": "a@example.com", "to": "b@example.com",
                "subject": None, "snippet": ""}

    monkeypatch.setattr(email_facade, "list_refs", list_refs)
    monkeypatch.setattr(email_facade, "get_message", get_message)
    monkeypatch.setattr(repo, "mail_items_for_message_ids", items)
    out = await tools.mail_search(None, "in:sent to:b@example.com")
    assert len(fetched) == tools._MAIL_OUTSIDE_SHOWN == 8
    assert "Not in the queue (8 of 12 shown):" in out
    assert "- u1 [not in the queue] could not be fetched (message gone)" in out
    assert "- u0 [not in the queue] 2026-03-03 from a@example.com to b@example.com: (no subject)" in out

    async def down(uuid):
        raise email_facade.EmailFacadeError("façade down", status_code=503)

    monkeypatch.setattr(email_facade, "get_message", down)
    with pytest.raises(email_facade.EmailFacadeError):
        await tools.mail_search(None, "in:sent to:b@example.com")
