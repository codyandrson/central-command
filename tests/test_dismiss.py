"""The third decision verdict — DISMISS (2026-08-04).

Distinct from `test_dismissal.py`'s M14 DISMISS_PENDING work-item review (an
agent's own "no action needed" claim, confirmed by the operator). This is the
operator's THIRD verdict on a proposal already sitting in the Decisions
Inbox: neither approve nor reject, just "no action needed, close it out,
don't ask again" — WITHDRAWN, folds released, session closed, agent never
resumed.
"""

from __future__ import annotations

import pytest

from central_command.config import settings
from central_command.db import repo
from central_command.gateway import gateway
from central_command.ingest import dispatcher, ledger
from central_command.runtime.spike_model import make_folding_model, make_spike_model
from tests.conftest import needs_pg

pytestmark = needs_pg

RAW = (
    "Message-ID: <cc-vd-1@example.com>\nFrom: dana@example.com\n"
    "Subject: Sprint planning moved\nDate: Fri, 17 Jul 2026 08:00:00 +0000\n\n"
    "DEMO-1's deadline slipped to Aug 3.\n"
)
ROOT = "<cc-vd-root@example.com>"
REPLY = "<cc-vd-reply@example.com>"


def _thread_msg(message_id: str, body: str, in_reply_to: str | None = None) -> str:
    headers = [
        f"Message-ID: {message_id}",
        "From: dana@example.com",
        "Subject: Sprint planning moved",
        "Date: Thu, 16 Jul 2026 11:02:00 +0000",
    ]
    if in_reply_to:
        headers += [f"In-Reply-To: {in_reply_to}", f"References: {in_reply_to}"]
    return "\n".join(headers) + f"\n\n{body}\n"


@pytest.fixture(autouse=True)
async def clean(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)  # keep offline: no live Claude call
    monkeypatch.setattr(settings, "executor_mode", "dry_run")
    monkeypatch.setattr(settings, "dispatch_approval_limit", 10_000)
    monkeypatch.setattr(settings, "dispatch_concurrency", 1)
    monkeypatch.setattr(settings, "auditor_enabled", False)
    await _wipe()
    yield
    await _wipe()


async def _wipe():
    conn = await repo._conn()
    try:
        await conn.execute("delete from work_item where message_id like '<cc-vd-%'")
    finally:
        await conn.close()


async def _enroll_and_dispatch(model):
    await ledger.enroll_email(RAW)
    return await dispatcher.dispatch_once(model=model)


async def _proposal_count_for_session(session_id: str) -> int:
    conn = await repo._conn()
    try:
        return await conn.fetchval(
            "select count(*) from proposal where session_id = $1", session_id
        )
    finally:
        await conn.close()


async def test_dismiss_withdraws_closes_session_and_never_resumes_the_agent(monkeypatch):
    result = await _enroll_and_dispatch(make_spike_model())
    proposal_id, session_id = result["proposal_id"], result["session_id"]

    # THE regression that matters most: dismiss must not reach for the resume
    # factory at all. Boobytrap it — if dismiss_proposal ever tries to build an
    # agent to resume, this raises instead of silently redrafting.
    def _boom(*a, **k):
        raise AssertionError("dismiss_proposal must not resume the agent")
    monkeypatch.setattr(gateway, "build_agent_for", _boom)

    out = await gateway.dismiss_proposal(session_id, proposal_id, "Close, don't ask again.")
    assert out == {"ok": True, "proposal_id": proposal_id, "status": "WITHDRAWN"}

    row = await repo.load_proposal(proposal_id)
    assert row["status"] == "WITHDRAWN"

    session = await repo.get_session(session_id)
    assert session["status"] == "DONE"
    assert session["closed_reason"] == "dismissed"

    # No redraft landed — the session's proposal count is unchanged.
    assert await _proposal_count_for_session(session_id) == 1

    events = await repo.list_events(kind="proposal.decided", ref_id=proposal_id)
    decided = [e for e in events if e["payload"].get("verdict") == "dismissed"]
    assert len(decided) == 1
    assert decided[0]["actor"] == "operator"
    assert decided[0]["payload"]["reason"] == "Close, don't ask again."
    assert decided[0]["payload"]["session_id"] == session_id


async def test_dismiss_reason_is_optional():
    result = await _enroll_and_dispatch(make_spike_model())
    proposal_id, session_id = result["proposal_id"], result["session_id"]

    out = await gateway.dismiss_proposal(session_id, proposal_id)
    assert out["status"] == "WITHDRAWN"

    events = await repo.list_events(kind="proposal.decided", ref_id=proposal_id)
    decided = [e for e in events if e["payload"].get("verdict") == "dismissed"]
    assert decided[0]["payload"]["reason"] == ""


async def test_dismissing_a_non_awaiting_proposal_refuses():
    result = await _enroll_and_dispatch(make_spike_model())
    proposal_id, session_id = result["proposal_id"], result["session_id"]

    await gateway.approve_and_execute(session_id, proposal_id, model=make_spike_model())

    with pytest.raises(gateway.GatewayError):
        await gateway.dismiss_proposal(session_id, proposal_id, "too late")


async def test_composer_state_stays_sendable_after_a_dismissal_close(monkeypatch):
    """Wire-shape check for the composer, not a DB test: `dismiss_proposal`
    closes the session with `closed_reason='dismissed'`, and the cockpit's
    `why_not_sendable` allowlist only locks a closed conversation for
    `closed_reason == 'concluded'` — everything else (including this new
    value) reopens by being written to, same as an operator-closed lane. The
    frontend hand-declares this interface itself (`sessionEnd.ts` /
    `_COMPOSER_DISABLING`), so a backend value the rule doesn't recognise would
    be invisible to `tsc` and to every frontend test — asserted here on the
    real wire the cockpit reads, per
    `test_composer_state_carries_closed_reason_to_the_cockpit`."""
    from central_command.api import nerve_gateway

    row = {
        "id": "sess_dismiss_wire", "agent_id": "jira-expert", "mode": "conversation",
        "status": "DONE", "closed_reason": "dismissed",
    }

    async def fake_get(_sid):
        return row

    monkeypatch.setattr(repo, "get_session", fake_get)
    state = await nerve_gateway._composer_state("agent:jira-expert:sess_dismiss_wire")

    assert state["session"]["closedReason"] == "dismissed"
    assert state["enabled"] is True, (
        "a dismissed conversation is a closed one, not a locked one — the "
        "composer must not report otherwise"
    )


async def test_dismissing_a_task_bound_proposal_resolves_the_task_done():
    """A coach task whose deliverable was the dismissed proposal must not sit
    in REVIEW forever — dismiss never resumes the drafter, so nothing else
    will ever land it (2026-08-16 fix)."""
    result = await _enroll_and_dispatch(make_spike_model())
    proposal_id, session_id = result["proposal_id"], result["session_id"]

    task_id = "task_" + session_id[-12:]
    await repo.create_task(task_id, "t", "Demo task bound to this session.", "auditor")
    await repo.start_task(task_id, session_id)

    await gateway.dismiss_proposal(session_id, proposal_id, "Not needed.")

    task = await repo.get_task(task_id)
    assert task["status"] == "DONE"
    assert task["outcome"] == (
        "operator dismissed the drafted proposal; no action needed"
    )

    events = await repo.list_events(kind="task.completed", ref_id=task_id)
    assert events, "task.completed must fire the same as any other resolution"


async def test_dismissing_a_covering_proposal_releases_its_folds():
    """Same safety property as rejection: a fold is a claim of coverage, never
    an outcome, so dismissing the covering proposal must not drop the sibling
    it claimed to cover."""
    await ledger.enroll_email(_thread_msg(ROOT, "Sprint planning moves to Thursday."))
    await ledger.enroll_email(
        _thread_msg(REPLY, "Correction: Thursday is out, let's do Friday 10:00.",
                    in_reply_to=ROOT)
    )
    result = await dispatcher.dispatch_once(model=make_folding_model())
    assert result and result["folded_message_ids"]
    folded_id = result["folded_message_ids"][0]

    conn = await repo._conn()
    try:
        state_before = await conn.fetchval(
            "select state from work_item where message_id = $1", folded_id
        )
    finally:
        await conn.close()
    assert state_before == "FOLD_PENDING"

    await gateway.dismiss_proposal(
        result["session_id"], result["proposal_id"], "Not needed."
    )

    conn = await repo._conn()
    try:
        state_after = await conn.fetchval(
            "select state from work_item where message_id = $1", folded_id
        )
    finally:
        await conn.close()
    assert state_after == "UNPROCESSED", "released back to the queue, not dropped"
    assert await repo.claim_next_work_item("sess_after_dismiss") is not None
