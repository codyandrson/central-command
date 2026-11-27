"""The one-time approval command for the Google MCP client (DL-237): it puts
Google's question to the operator and sends only what the operator typed. It
never chooses, never runs without a terminal, prints nothing a mail holds, and
calls no write tool except aimed at a target that does not exist. Google is the
local stub of `test_google_mcp.py`; nothing leaves the process.
"""

from __future__ import annotations

import io
import json

import pytest

pytest.importorskip("google_auth_httplib2")

from central_command import google_mcp_grant as grant
from tests.test_google_mcp import INPUT_REQUIRED, FakeMcp, mcp, ok  # noqa: F401  (fixture)

SEARCH = ok(
    {
        "threads": [
            {
                "id": "t1",
                "messageCount": 1,
                "messages": [
                    {
                        "id": "m1",
                        "threadId": "t1",
                        "subject": "Quarterly secret",
                        "snippet": "the vault code",
                    }
                ],
            }
        ]
    }
)


def _answers(*replies):
    """An `ask` that answers in order, then reports end of input."""
    it = iter(replies)
    asked = []

    def ask(prompt):
        asked.append(prompt)
        try:
            return next(it)
        except StopIteration:
            raise EOFError from None

    ask.asked = asked
    return ask


def _all_reads(fake: FakeMcp, search=SEARCH):
    fake.on("search_threads", search)
    fake.on("get_thread", ok({"id": "t1", "messages": [{"id": "m1", "threadId": "t1"}]}))
    fake.on("get_message", ok({"raw": "eA"}))
    fake.on("list_events", ok({"events": [{"id": "ev1", "summary": "Board meeting"}]}))
    fake.on("get_event", ok({"id": "ev1", "summary": "Board meeting"}))


def test_it_refuses_to_run_without_a_terminal_and_sends_nothing(mcp):
    fake, _ = mcp
    err = io.StringIO()
    assert grant.run([], out=io.StringIO(), err=err, ask=_answers(), interactive=False) == 2
    assert "terminal" in err.getvalue() and fake.requests == []


def test_the_default_detects_a_missing_terminal(mcp, monkeypatch):
    fake, _ = mcp
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert grant.run([], out=io.StringIO(), err=io.StringIO(), ask=_answers()) == 2
    assert fake.requests == []


def test_each_read_tool_is_called_once_and_nothing_read_is_printed(mcp):
    fake, _ = mcp
    _all_reads(fake)
    out = io.StringIO()
    assert grant.run([], out=out, err=io.StringIO(), ask=_answers(), interactive=True) == 0
    assert [b["params"]["name"] for b in fake.bodies()] == [
        "search_threads",
        "get_thread",
        "get_message",
        "list_events",
        "get_event",
    ]
    # The calendar probe is the provider's widest list: a one-event probe
    # passed unasked on 2026-11-27 while the real 7-day list was refused.
    listing = next(b for b in fake.bodies() if b["params"]["name"] == "list_events")
    assert listing["params"]["arguments"]["pageSize"] == 250
    # Never RAW: Google refuses it for a message with an attachment.
    reading = next(b for b in fake.bodies() if b["params"]["name"] == "get_message")
    assert reading["params"]["arguments"]["messageFormat"] == "FULL_CONTENT"
    text = out.getvalue()
    for leaked in ("Quarterly secret", "vault code", "Board meeting", "m1", "t1", "ev1"):
        assert leaked not in text
    for write in grant.WRITES:
        assert write in text
    assert not any(b["params"]["name"] in grant.WRITES for b in fake.bodies())


def test_googles_question_is_shown_and_only_the_typed_choice_is_sent(mcp):
    fake, _ = mcp
    _all_reads(fake, search=None)
    fake.on("search_threads", INPUT_REQUIRED, SEARCH)
    out = io.StringIO()
    ask = _answers("", "nonsense", "2")
    assert grant.run(["search_threads"], out=out, err=io.StringIO(), ask=ask, interactive=True) == 0
    first, retry = fake.bodies("search_threads")
    assert retry["params"]["arguments"] == first["params"]["arguments"]
    assert retry["params"]["requestState"] == INPUT_REQUIRED["requestState"]
    assert retry["params"]["inputResponses"] == {
        "approval_choice": {"action": "accept", "content": {"approval_choice": "ALLOW_TOOL_ALWAYS"}}
    }
    assert "requestState" not in first["params"]
    text = out.getvalue()
    assert "Allow Central Command to use the tool?" in text
    assert "[1] Allow once (ALLOW_ONCE)" in text and "[3] Don't allow (DENY_ONCE)" in text
    assert "secret plans" not in text, "Google's preview can quote mail and is not printed"
    assert len(ask.asked) == 3, "an empty or unknown answer asks again; it is never a default"


@pytest.mark.parametrize("replies", [("c",), ()])
def test_cancel_or_end_of_input_sends_nothing(mcp, replies):
    fake, _ = mcp
    fake.on("search_threads", INPUT_REQUIRED)
    out = io.StringIO()
    grant.run(
        ["search_threads"], out=out, err=io.StringIO(), ask=_answers(*replies), interactive=True
    )
    assert len(fake.bodies("search_threads")) == 1
    assert all("inputResponses" not in b["params"] for b in fake.bodies())
    assert "nothing was sent" in out.getvalue()


def test_a_write_tool_is_asked_about_only_by_name_and_aimed_at_nothing(mcp):
    fake, _ = mcp
    fake.on("mark_message_spam", INPUT_REQUIRED, ok({}))
    fake.on("delete_event", ok({}))
    ask = _answers("1")
    assert (
        grant.run(
            ["mark_message_spam", "delete_event"],
            out=io.StringIO(),
            err=io.StringIO(),
            ask=ask,
            interactive=True,
        )
        == 0
    )
    names = [b["params"]["name"] for b in fake.bodies()]
    assert names == ["mark_message_spam", "mark_message_spam", "delete_event"]
    spam = fake.bodies("mark_message_spam")
    assert all(b["params"]["arguments"] == {"messageId": grant.NO_MESSAGE} for b in spam)
    assert spam[1]["params"]["inputResponses"]["approval_choice"]["content"] == {
        "approval_choice": "ALLOW_ONCE"
    }
    delete = fake.bodies("delete_event")[0]["params"]["arguments"]
    assert delete["eventId"] == grant.NO_EVENT and delete["calendarId"].endswith("@example.invalid")


def test_every_write_probe_targets_something_that_cannot_exist():
    for tool in grant.WRITES:
        args = grant._write_probe(tool)
        assert args.get("messageId") == grant.NO_MESSAGE or args["calendarId"].endswith(
            "@example.invalid"
        ), tool
        assert args.get("notificationLevel", "NONE") == "NONE"


def test_an_unknown_tool_is_refused(mcp):
    fake, _ = mcp
    err = io.StringIO()
    assert grant.run(["send_message"], out=io.StringIO(), err=err, interactive=True) == 2
    assert "not a tool" in err.getvalue() and fake.requests == []
    with pytest.raises(SystemExit):
        grant.main(["--tool", "send_message"])


def test_missing_settings_are_named_before_any_request(mcp, monkeypatch):
    from central_command.config import settings

    fake, _ = mcp
    monkeypatch.setattr(settings, "google_refresh_token", "")
    err = io.StringIO()
    assert grant.run([], out=io.StringIO(), err=err, interactive=True) == 2
    assert "CC_GOOGLE_REFRESH_TOKEN" in err.getvalue() and fake.requests == []


def test_a_bearer_token_is_never_printed(mcp):
    fake, _ = mcp
    _all_reads(fake)
    out = io.StringIO()
    grant.run([], out=out, err=io.StringIO(), interactive=True)
    assert "ya29." not in out.getvalue()
    assert all(json.loads(r.content)["params"]["_meta"] for r in fake.requests)
