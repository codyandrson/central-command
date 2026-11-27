"""mail.draft (DL-238): an agent proposes a Gmail DRAFT, and once the operator
approves, the credentialed Executor creates it in the operator's Gmail through
Google's Gmail MCP server. Nothing here sends mail: the operator opens the draft
and sends it personally. Offline: the façade, the repo and the model are stubbed;
the wire itself is `test_google_mcp.py`, the offer per provider is
`test_exchange_cutover.py`.
"""

from __future__ import annotations

import pytest
from pydantic_ai import CallDeferred, ModelRetry

from central_command.config import settings
from central_command.contract import ARG_SPECS, validate_action_args
from central_command.contract.dependencies import N8N, dependencies_for_capability
from central_command.gateway import executor
from central_command.integrations import email_facade
from central_command.runtime import durable, packs, tools
from central_command.runtime.deps import TriageDeps
from tests.conftest import needs_pg
from tests.test_google_mcp import DRAFT, FakeMcp, mcp, ok  # noqa: F401  (fixture)


class _Ctx:
    def __init__(self, item_id=None):
        self.deps = TriageDeps(agent_id="ea", session_id="sess_test_draft", item_id=item_id)


def _action(exc: pytest.ExceptionInfo[CallDeferred]) -> dict:
    return exc.value.metadata["proposal"]["actions"][0]


# --- the proposal tool ---------------------------------------------------------


async def test_a_draft_is_proposed_reversible_with_what_the_operator_reads():
    with pytest.raises(CallDeferred) as exc:
        await tools.propose_mail_draft(
            _Ctx(),
            subject="Thursday",
            body="Thursday works for me.",
            rationale="Jane asked for a time.",
            to=[" jane.doe@example.com "],
            cc=["john.rivers@example.com"],
            bcc=["dana.doe@example.com"],
        )
    proposal = exc.value.metadata["proposal"]
    [action] = proposal["actions"]
    assert action["capability"] == "mail.draft@v1"
    assert action["arguments"] == {
        "subject": "Thursday",
        "body": "Thursday works for me.",
        "to": ["jane.doe@example.com"],
        "cc": ["john.rivers@example.com"],
        "bcc": ["dana.doe@example.com"],
    }
    # Reversible (the operator deletes the draft), unlike mail.send.
    assert action["reversibility"] == "reversible"
    assert "nothing is sent" in proposal["expected_effect"]
    # A tool-built proposal carries no confidence and is never given one.
    assert proposal["confidence"] is None
    # A description built from headers, never a quote: neither checked nor flagged.
    assert [e["kind"] for e in proposal["evidence"]] == ["mailbox"]


async def test_a_reply_pins_the_provider_uuid_the_mailbox_answered(monkeypatch):
    from central_command.db import repo

    async def item(ref):
        assert ref == "wi_1"
        return {
            "id": "wi_1",
            "provider_uuid": "18c0ffee00aa1111",
            "sender": "Jane Doe <jane.doe@example.com>",
            "subject": "Thursday?",
        }

    monkeypatch.setattr(repo, "mail_item", item)
    with pytest.raises(CallDeferred) as exc:
        await tools.propose_mail_draft(
            _Ctx(),
            subject="Re: Thursday?",
            body="Yes.",
            rationale="She asked.",
            reply_to_ref="wi_1",
        )
    action = _action(exc)
    assert action["arguments"] == {
        "subject": "Re: Thursday?",
        "body": "Yes.",
        "reply_to": "18c0ffee00aa1111",
    }
    assert action["target_ref"]["id"] == "18c0ffee00aa1111"
    evidence = exc.value.metadata["proposal"]["evidence"][0]
    assert evidence["claim"] == "reply to Jane Doe <jane.doe@example.com>: Thursday?"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"subject": "Hi", "body": "Hello."},
        {"subject": " ", "body": "Hello.", "to": ["jane.doe@example.com"]},
        {"subject": "Hi", "body": "  ", "to": ["jane.doe@example.com"]},
    ],
)
async def test_a_thin_draft_is_handed_back_to_the_model(kwargs):
    with pytest.raises(ModelRetry):
        await tools.propose_mail_draft(_Ctx(), rationale="r", **kwargs)


@needs_pg
async def test_a_display_name_address_is_handed_back_with_every_problem():
    with pytest.raises(ModelRetry, match="not a plain address"):
        await tools.propose_mail_draft(
            _Ctx(),
            subject="Hi",
            body="Hello.",
            rationale="r",
            to=["Jane Doe <jane.doe@example.com>"],
        )


def test_the_draft_tool_is_classified_everywhere_mail_send_is():
    """A tool added to a pack must also be a propose tool the runtime classifies
    (`durable.PROPOSE_TOOLS`), kept out of consults like the other mail writes
    (`packs.NON_ADVISORY_TOOLS`), and offered on the one provider that serves it."""
    assert "propose_mail_draft" in durable.PROPOSE_TOOLS
    assert "propose_mail_draft" in packs.NON_ADVISORY_TOOLS
    assert "propose_mail_draft" not in packs.AGENT_AUTHORED_PROPOSE_TOOLS
    assert packs.TOOL_PROVIDERS["propose_mail_draft"] == ("google-mcp",)
    assert packs.PACKS["mail-draft-propose"].tool_names == ("propose_mail_draft",)


def test_the_charter_lists_the_draft_capability_only_where_it_is_offered(monkeypatch):
    """The capability line (the authority on what the agent may propose) follows
    `_offered`. The pack's prose guidance is printed for any grant, as
    mail-send-propose's is on Gmail."""
    for value, listed in (("n8n", False), ("native", False), ("mcp", True)):
        monkeypatch.setattr(settings, "google_provider", value)
        text = packs.charter_section(("mail-draft-propose",))
        assert ("capability = 'mail.draft'" in text) is listed, value


def test_a_granted_draft_is_a_granted_capability_only_on_google_mcp(monkeypatch):
    monkeypatch.setattr(settings, "google_provider", "n8n")
    assert "mail.draft" not in packs.granted_capability_names(("mail-draft-propose",))
    monkeypatch.setattr(settings, "google_provider", "mcp")
    assert "mail.draft" in packs.granted_capability_names(("mail-draft-propose",))
    assert "propose_mail_draft" in packs.toolset_for(("mail-draft-propose",)).tools


# --- the shape both tiers check ------------------------------------------------

GOOD = {"to": ["jane.doe@example.com"], "subject": "Hi", "body": "Hello."}


@pytest.mark.parametrize(
    "arguments",
    [
        GOOD,
        {**GOOD, "cc": ["john.rivers@example.com"], "bcc": []},
        {"subject": "Re: Hi", "body": "Hello.", "reply_to": "18c0ffee00aa1111"},
        {**GOOD, "to": ["jane+tag@example.com"], "reply_to": "m_1-2"},
    ],
)
def test_a_well_formed_draft_passes(arguments):
    assert validate_action_args("mail.draft@v1", arguments) == []


@pytest.mark.parametrize(
    "arguments,fragment",
    [
        ({"subject": "Hi", "body": "Hello."}, "at least one of to, reply_to"),
        ({**GOOD, "to": []}, "at least one of to, reply_to"),
        ({**GOOD, "subject": ""}, "missing required argument(s) subject"),
        ({**GOOD, "body": ""}, "missing required argument(s) body"),
        ({**GOOD, "to": "jane.doe@example.com"}, "to must be a list"),
        ({**GOOD, "to": ["Jane <jane.doe@example.com>"]}, "not a plain address"),
        ({**GOOD, "cc": ["jane.doe@example.com, john.rivers@example.com"]}, "cc[0]"),
        ({**GOOD, "bcc": ["jane.doe@example.com\\r\\nBcc: x@example.com"]}, "bcc[0]"),
        ({**GOOD, "to": ["jane.doe"]}, "not a plain address"),
        ({**GOOD, "to": [42]}, "not a plain address"),
        ({**GOOD, "body": 7}, "body must be text"),
        ({**GOOD, "reply_to": "a b"}, "reply_to"),
        ({**GOOD, "reply_to": "<id@example.com>"}, "reply_to"),
        ({**GOOD, "htmlBody": "<b>hi</b>"}, "unknown argument(s) htmlBody"),
        ({**GOOD, "attachments": []}, "unknown argument(s) attachments"),
        ({**GOOD, "includeSignature": True}, "unknown argument(s) includeSignature"),
    ],
)
def test_a_malformed_draft_is_refused_with_the_reason(arguments, fragment):
    problems = validate_action_args("mail.draft@v1", arguments)
    assert any(fragment in p for p in problems), problems


def test_the_draft_spec_is_the_one_list_both_tiers_run():
    assert "mail.draft" in ARG_SPECS
    assert dependencies_for_capability("mail.draft@v1") == (N8N,)


# --- the Executor --------------------------------------------------------------


@pytest.fixture
def drafted(monkeypatch):
    calls = []

    async def draft(**kwargs):
        calls.append(kwargs)
        return {
            "ok": True,
            "draft_id": "r-8821",
            "thread_id": "18c0ffee00aa0000",
            "view_url": "https://mail.google.com/mail/u/0/#drafts/18c0ffee",
        }

    monkeypatch.setattr(email_facade, "draft", draft)
    return calls


async def test_the_executor_creates_the_draft_and_records_how_to_open_it(drafted):
    out = await executor.HANDLERS["mail.draft"](
        {
            "to": ["jane.doe@example.com"],
            "cc": ["john.rivers@example.com"],
            "subject": "Thursday",
            "body": "A private body that must not reach the record.",
            "reply_to": "18c0ffee00aa1111",
        },
        "human:operator",
        "ea",
    )
    assert drafted == [
        {
            "to": ["jane.doe@example.com"],
            "subject": "Thursday",
            "body": "A private body that must not reach the record.",
            "cc": ["john.rivers@example.com"],
            "bcc": [],
            "reply_to": "18c0ffee00aa1111",
        }
    ]
    assert "NOT sent" in out
    assert "draft id r-8821" in out
    assert "thread 18c0ffee00aa0000" in out
    assert "https://mail.google.com/mail/u/0/#drafts/18c0ffee" in out
    assert "private body" not in out


async def test_a_reply_with_no_recipient_is_described_as_going_to_its_sender(drafted):
    out = await executor.HANDLERS["mail.draft"](
        {"subject": "Re: Thursday", "body": "Yes.", "reply_to": "m1"}, "human:operator", "ea"
    )
    assert "the sender of the mail it answers" in out
    assert drafted[0]["to"] == [] and drafted[0]["reply_to"] == "m1"


async def test_the_executor_refuses_a_draft_the_shape_rules_refuse_before_any_handler(
    monkeypatch, drafted
):
    """`execute()` runs `validate_action_args` over every action first, so a
    proposal arriving by API with an HTML body never reaches the façade."""
    problems = validate_action_args("mail.draft@v1", {**GOOD, "htmlBody": "<b>x</b>"})
    assert problems and drafted == []


async def test_the_facade_refuses_a_draft_outside_google_mcp_before_any_socket(monkeypatch):
    for value in ("n8n", "native"):
        monkeypatch.setattr(settings, "google_provider", value)
        with pytest.raises(email_facade.EmailFacadeError, match="CC_GOOGLE_PROVIDER=mcp"):
            await email_facade.draft(to=["jane.doe@example.com"], subject="Hi", body="Hello.")


async def test_an_approved_draft_reaches_google_as_one_create_draft_and_nothing_else(mcp):
    """The whole path below the gate against the stub of Google's server: the
    handler, the façade and the MCP client, and the one tool they call."""
    fake: FakeMcp = mcp[0]
    fake.on("create_draft", ok(DRAFT))
    out = await executor.HANDLERS["mail.draft"](
        {
            "to": ["jane.doe@example.com"],
            "subject": "Thursday",
            "body": "Thursday works for me.",
            "reply_to": "18c0ffee00aa1111",
        },
        "human:operator",
        "ea",
    )
    assert [b["params"]["name"] for b in fake.bodies()] == ["create_draft"]
    assert fake.bodies()[0]["params"]["arguments"]["replyToMessageId"] == "18c0ffee00aa1111"
    assert "draft id r-8821" in out and "NOT sent" in out
