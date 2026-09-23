"""Mail rules (2026-09-22, v2.40.0): standing inbox rules matched at claim
time, before any model call.

Pure semantics first (no database): the matcher is a function of a rule and
an item. Then real Postgres, because the SQL twin (`repo.mail_rule_matches`,
the sweep) is what previews and sweeps actually run, and the two matchers
must agree row for row — that parity is the property this file pins.
"""

from __future__ import annotations

import pytest
from pydantic_ai import ModelRetry
from pydantic_ai.exceptions import CallDeferred

from central_command.config import settings
from central_command.db import repo
from central_command.gateway import executor
from central_command.ingest import dispatcher, mail_rules
from central_command.runtime import tools as tools_mod
from tests.conftest import needs_pg


def _item(from_, subject, body, *, exempt=False, item_id="wi_x"):
    payload = {"from": from_, "text": f"From: {from_}\nSubject: {subject}\n\n{body}"}
    if exempt:
        payload["rule_exempt"] = True
    return {"id": item_id, "subject": subject, "payload": payload}


def _rule(criteria, exceptions=None, **extra):
    return {"id": "rule_t", "criteria": criteria, "exceptions": exceptions or {},
            "description": "d", "reason": "r", "position": 1, **extra}


# --- pure semantics ----------------------------------------------------------

def test_validate_requires_a_criterion_and_refuses_a_bare_provider_domain():
    with pytest.raises(ValueError, match="at least one criterion"):
        mail_rules.validate({}, {})
    with pytest.raises(ValueError, match="mailbox provider"):
        mail_rules.validate({"from_domain": "gmail.com"}, {})
    # ...but a provider domain WITH a sender-identifying phrase is fine.
    crit, _ = mail_rules.validate({"from_domain": "@Gmail.com", "subject_contains": "Weekly"}, {})
    assert crit == {"from_domain": "gmail.com", "subject_contains": "weekly"}
    with pytest.raises(ValueError, match="identify the sender"):
        mail_rules.validate({"subject_contains": "Weekly digest"}, {})
    with pytest.raises(ValueError, match="at least 3"):
        mail_rules.validate({"from_domain": "shop.example", "subject_contains": "ab"}, {})
    with pytest.raises(ValueError, match="unknown rule field"):
        mail_rules.validate({"sender": "x@y.z"}, {})


def test_describe_is_generated_from_the_criteria():
    text = mail_rules.describe(
        {"from_domain": "akvis.com", "subject_contains": "AliveColors"},
        {"subject_contains": "invoice"},
    )
    assert text == (
        'Auto-dismiss mail from anyone at akvis.com (or a subdomain) and whose '
        'subject contains "alivecolors", except when the subject contains "invoice".'
    )


def test_matching_semantics():
    rule = _rule({"from_domain": "akvis.com"})
    assert mail_rules.matches(rule, _item("AliveColors <subscribe@akvis.com>", "Hi", "b"))
    assert mail_rules.matches(rule, _item("news@mail.akvis.com", "Hi", "b"))  # subdomain
    assert not mail_rules.matches(rule, _item("x@notakvis.com", "Hi", "b"))
    assert not mail_rules.matches(rule, _item("x@akvis.com.evil.example", "Hi", "b"))

    exact = _rule({"from_address": "Subscribe@AKVIS.com"})
    assert mail_rules.matches(exact, _item("AliveColors <subscribe@akvis.com>", "s", "b"))
    assert not mail_rules.matches(exact, _item("other@akvis.com", "s", "b"))

    both = _rule({"from_domain": "akvis.com", "subject_contains": "alivecolors"})
    assert mail_rules.matches(both, _item("a@akvis.com", "Meet AliveColors 11", "b"))
    assert not mail_rules.matches(both, _item("a@akvis.com", "Your invoice", "b"))  # AND

    body = _rule({"body_contains": "from:"})
    # The header block the prompt prepends is NOT the body.
    assert not mail_rules.matches(body, _item("a@b.c", "s", "plain text"))
    assert mail_rules.matches(body, _item("a@b.c", "s", "quoted: From: someone"))

    excepted = _rule({"from_domain": "akvis.com"}, {"subject_contains": "invoice"})
    assert not mail_rules.matches(excepted, _item("a@akvis.com", "Your INVOICE", "b"))

    assert not mail_rules.matches(rule, _item("a@akvis.com", "s", "b", exempt=True))
    assert not mail_rules.matches({**rule, "revoked_at": "2026-09-22"}, _item("a@akvis.com", "s", "b"))


def test_first_match_honours_position_order():
    narrow = _rule({"from_domain": "akvis.com", "subject_contains": "invoice"}, position=1, id="narrow")
    broad = _rule({"from_domain": "akvis.com"}, position=2, id="broad")
    item = _item("a@akvis.com", "Your invoice", "b")
    assert mail_rules.first_match([broad, narrow], item)["id"] == "narrow"
    assert mail_rules.first_match([broad], _item("a@akvis.com", "promo", "b"))["id"] == "broad"
    assert mail_rules.first_match([narrow], _item("a@akvis.com", "promo", "b")) is None


# --- real Postgres -------------------------------------------------------------

pytestmark_pg = needs_pg

_ROWS = [
    # id, from, subject, body, state
    ("wi_mr_match1", "AliveColors <subscribe@akvis.com>", "Meet AliveColors 11", "promo", "UNPROCESSED"),
    ("wi_mr_match2", "news@mail.akvis.com", "AliveColors sale 50%_off", "promo", "UNPROCESSED"),
    ("wi_mr_invoice", "billing@akvis.com", "Your AliveColors invoice", "pay", "UNPROCESSED"),
    ("wi_mr_other", "someone@example.com", "AliveColors review", "x", "UNPROCESSED"),
    ("wi_mr_claimed", "a@akvis.com", "AliveColors news", "x", "CLAIMED"),
]
_CRIT = {"from_domain": "akvis.com", "subject_contains": "AliveColors"}
_EXC = {"subject_contains": "invoice"}


async def _wipe():
    conn = await repo._conn()
    try:
        await conn.execute("delete from work_item where id like 'wi_mr_%'")
        await conn.execute("delete from mail_rule where reason like 'test-mr:%'")
    finally:
        await conn.close()


async def _seed():
    for item_id, from_, subject, body, state in _ROWS:
        await repo.enroll_work_item(
            item_id, f"<{item_id}@test.example>",
            {"from": from_, "text": f"From: {from_}\nSubject: {subject}\n\n{body}"},
            thread_id=f"<thread-{item_id}>", feed="backlog", source="fixture", subject=subject,
        )
        if state != "UNPROCESSED":
            conn = await repo._conn()
            try:
                await conn.execute("update work_item set state=$2 where id=$1", item_id, state)
            finally:
                await conn.close()


@pytest.fixture
async def seeded():
    await _wipe()
    await _seed()
    yield
    await _wipe()


@needs_pg
async def test_sql_preview_agrees_with_the_python_matcher(seeded):
    crit, exc = mail_rules.validate(_CRIT, _EXC)
    out = await repo.mail_rule_matches(crit, exc)
    # Only UNPROCESSED rows count, and the Python matcher over the same rows
    # must pick exactly the same set.
    conn = await repo._conn()
    try:
        rows = await conn.fetch(
            "select id, subject, payload from work_item where id like 'wi_mr_%' and state='UNPROCESSED'"
        )
    finally:
        await conn.close()
    rule = _rule(crit, exc)
    expected = sorted(r["id"] for r in rows if mail_rules.matches(rule, {
        "id": r["id"], "subject": r["subject"],
        "payload": r["payload"] if isinstance(r["payload"], dict) else __import__("json").loads(r["payload"]),
    }))
    assert expected == ["wi_mr_match1", "wi_mr_match2"]
    assert out["queued_matches"] == 2
    assert {s["subject"] for s in out["samples"]} == {"Meet AliveColors 11", "AliveColors sale 50%_off"}


@needs_pg
async def test_create_and_sweep_folds_only_unprocessed_matches(seeded):
    crit, exc = mail_rules.validate(_CRIT, _EXC)
    rule = await repo.create_mail_rule(crit, exc, mail_rules.describe(crit, exc), "test-mr: sweep", "operator")
    swept = await repo.sweep_mail_rule(rule, mail_rules.dismissal_rationale(rule))
    assert swept == 2
    conn = await repo._conn()
    try:
        states = {r["id"]: r["state"] for r in await conn.fetch(
            "select id, state from work_item where id like 'wi_mr_%'")}
        folded = await conn.fetch(
            "select id, payload->>'rule_id' rid from work_item where id like 'wi_mr_match%'")
    finally:
        await conn.close()
    assert states["wi_mr_match1"] == states["wi_mr_match2"] == "FOLDED"
    assert states["wi_mr_invoice"] == "UNPROCESSED"      # exception held
    assert states["wi_mr_other"] == "UNPROCESSED"        # domain did not match
    assert states["wi_mr_claimed"] == "CLAIMED"          # never yanked from a run
    assert all(r["rid"] == rule["id"] for r in folded)
    listed = {r["id"]: r for r in await repo.list_mail_rules()}
    assert listed[rule["id"]]["matched_count"] == 2
    assert listed[rule["id"]]["reopened_count"] == 0

    # Reopen puts the row back, rule-exempt, and the rule no longer takes it.
    assert await repo.reopen_rule_folded("wi_mr_match1", "look again") == rule["id"]
    again = await repo.mail_rule_matches(crit, exc)
    assert again["queued_matches"] == 0
    assert await repo.reopen_rule_folded("wi_mr_other", None) is None  # not a rule fold

    # Revocation is a timestamp; the rule leaves the active set but not the list.
    assert await repo.revoke_mail_rule(rule["id"], "wrong")
    assert not await repo.revoke_mail_rule(rule["id"], "twice")
    assert rule["id"] not in {r["id"] for r in await repo.active_mail_rules()}
    assert rule["id"] in {r["id"] for r in await repo.list_mail_rules(include_revoked=True)}


@needs_pg
async def test_dispatcher_folds_a_matching_claim_without_a_run(seeded, monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    crit, exc = mail_rules.validate(_CRIT, _EXC)
    await repo.create_mail_rule(crit, exc, mail_rules.describe(crit, exc), "test-mr: claim", "operator")

    ran = []

    async def never(*a, **k):
        ran.append(a)
        raise AssertionError("the model must not run for a rule-matched item")

    monkeypatch.setattr(dispatcher, "ingest_and_propose", never)
    # The CLAIMED sibling shares the sender family, and the relatedness lock
    # refuses a claim while it is in flight — park it so this test is about
    # rules, not the lock.
    conn = await repo._conn()
    try:
        await conn.execute("update work_item set state='FAILED' where id='wi_mr_claimed'")
    finally:
        await conn.close()
    item = await repo.claim_specific_work_item("wi_mr_match1", "sess_test")
    assert item is not None
    out = await dispatcher.process_claimed(item)
    assert out["rule_dismissed"] is True and not ran
    conn = await repo._conn()
    try:
        state = await conn.fetchval("select state from work_item where id='wi_mr_match1'")
    finally:
        await conn.close()
    assert state == "FOLDED"
    kinds = [e["kind"] for e in await repo.list_events_of_kinds(["work.rule_dismissed"])
             if e["ref_id"] == "wi_mr_match1"]
    assert kinds == ["work.rule_dismissed"]

    # An exception-matching row is NOT taken: it goes on to the run.
    item = await repo.claim_specific_work_item("wi_mr_invoice", "sess_test2")
    out = await dispatcher.process_claimed(item)  # the stub's error is caught and the row released
    assert ran and out["ok"] is False and "must not run" in out["error"]


@needs_pg
async def test_executor_revalidates_and_sweeps(seeded, monkeypatch):
    monkeypatch.setattr(settings, "executor_mode", "live")
    text = await executor._mail_create_rule(
        {"criteria": _CRIT, "exceptions": _EXC, "reason": "test-mr: executor",
         "description": "an agent-written description that must be re-derived",
         "apply_to_queued": True},
        approver="human:test", proposer="inbox-triage",
    )
    assert "2 queued item(s) dismissed" in text
    rules = [r for r in await repo.list_mail_rules() if r["reason"] == "test-mr: executor"]
    assert len(rules) == 1
    assert rules[0]["description"].startswith("Auto-dismiss mail from anyone at akvis.com")
    assert rules[0]["created_by"].endswith("(inbox-triage)")
    with pytest.raises(ValueError, match="mailbox provider"):
        await executor._mail_create_rule(
            {"criteria": {"from_domain": "gmail.com"}, "reason": "test-mr: bad", "description": "x"},
            approver="human:test", proposer=None,
        )


@needs_pg
async def test_propose_tool_pins_the_preview(seeded):
    ctx = type("Ctx", (), {"deps": None})()
    with pytest.raises(ModelRetry, match="identify the sender"):
        await tools_mod.propose_mail_rule(ctx, reason="r", subject_contains="AliveColors")
    with pytest.raises(CallDeferred) as info:
        await tools_mod.propose_mail_rule(
            ctx, reason="test-mr: tool", from_domain="akvis.com",
            subject_contains="AliveColors", except_subject_contains="invoice",
        )
    proposal = info.value.metadata["proposal"]
    action = proposal["actions"][0]
    assert action["capability"] == "mail.create_rule@v1"
    assert action["arguments"]["queued_matches"] == 2
    assert action["arguments"]["criteria"] == {"from_domain": "akvis.com", "subject_contains": "alivecolors"}
    assert action["arguments"]["description"].startswith("Auto-dismiss mail from anyone at akvis.com")
    assert len(action["arguments"]["samples"]) == 2
    assert proposal["evidence"][0]["kind"] == "queue"
