"""The tier-3 stall classifier is pure, so it is tested without a database.

The sweep that uses it is not: its whole job is reading real `operator_item` /
`session` rows and stamping them, so those tests need a real Postgres and skip
without one (same rule as `tests/test_ledger.py` — mocking the rows would test
nothing).
"""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from central_command.config import settings
from central_command.db import repo
from central_command.runtime.discussions import classify_discussion_stall
from tests.conftest import needs_pg

NOW = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)


def _case(*, quiet_hours=48, last_turn_by="agent", stalled_at=None, nudged_at=None):
    moved = NOW - timedelta(hours=quiet_hours)
    return {
        "item": {"id": "item_x", "status": "OPEN", "stalled_at": stalled_at,
                 "nudged_at": nudged_at},
        "session": {"id": "sess_x", "updated_at": moved},
        "last_turn_by": last_turn_by,
        "now": NOW,
        "stall_hours": 24,
    }


def test_not_quiet_long_enough_is_not_a_stall():
    assert classify_discussion_stall(**_case(quiet_hours=2)) is None


def test_agent_spoke_last_is_s1_waiting_on_the_operator():
    assert classify_discussion_stall(**_case(last_turn_by="agent")) == "S1"


def test_operator_spoke_last_is_s2_waiting_on_the_agent():
    assert classify_discussion_stall(**_case(last_turn_by="operator")) == "S2"


def test_already_flagged_in_this_quiet_window_is_not_reflagged():
    """Idempotence: the sweep runs every tick and must flag once per window."""
    moved = NOW - timedelta(hours=48)
    assert classify_discussion_stall(
        **_case(last_turn_by="agent", stalled_at=moved + timedelta(minutes=1))
    ) is None


def test_flagged_before_the_session_last_moved_is_flagged_again():
    """Quiet -> movement -> quiet again is a NEW stall, not one already handled."""
    moved = NOW - timedelta(hours=48)
    assert classify_discussion_stall(
        **_case(last_turn_by="agent", stalled_at=moved - timedelta(days=3))
    ) == "S1"


def test_s2_idempotence_uses_the_nudge_stamp_not_the_stall_stamp():
    moved = NOW - timedelta(hours=48)
    assert classify_discussion_stall(
        **_case(last_turn_by="operator", nudged_at=moved + timedelta(minutes=1))
    ) is None
    assert classify_discussion_stall(
        **_case(last_turn_by="operator", stalled_at=moved + timedelta(minutes=1))
    ) == "S2"


def test_an_answered_item_is_never_a_stall():
    case = _case(last_turn_by="agent")
    case["item"]["status"] = "ANSWERED"
    assert classify_discussion_stall(**case) is None


# --- the sweep (needs Postgres) ----------------------------------------------


# Applied per-test, NOT as a module-level `pytestmark`: the classifier tests
# above are pure and must keep running on a machine with no Postgres.

AGENT = "jira-expert"
PREFIX = "gvtest-disc-"
PARKED = "PARKED_FOR_TEST"


def _transcript(last_turn: str) -> dict:
    """A discussion transcript ending with the named speaker's turn.

    Built through the real message adapter, not hand-rolled JSON, because
    `last_turn_by` is read with `durable.simplify_messages` off the persisted
    shape — hand-rolled dicts would test a shape nothing produces.
    """
    from pydantic_ai.messages import (
        ModelMessagesTypeAdapter,
        ModelRequest,
        ModelResponse,
        TextPart,
        UserPromptPart,
    )

    messages = [ModelResponse(parts=[TextPart(content="Which project should this land in?")])]
    if last_turn == "operator":
        messages.append(ModelRequest(parts=[UserPromptPart(content="TASKS, please.")]))
    return {"messages": ModelMessagesTypeAdapter.dump_python(messages, mode="json")}


async def _purge() -> None:
    conn = await repo._conn()
    try:
        await conn.execute(f"delete from audit_event where ref_id like '{PREFIX}%'")
        await conn.execute(f"delete from operator_item where id like '{PREFIX}%'")
        await conn.execute(f"delete from session where id like '{PREFIX}%'")
    finally:
        await conn.close()


@pytest.fixture()
async def discussion_lab():
    """Own the sweep's whole input for the duration of one test.

    The sweep reads EVERY open discussion, so a leftover row from another test
    would show up in its results. Foreign open discussions are parked and given
    back afterwards (the same treatment `conftest.isolated_queue` gives queued
    work), and this file's own rows are deleted at both ends.
    """
    await _purge()
    conn = await repo._conn()
    try:
        await conn.execute(
            f"""update operator_item set status = '{PARKED}'
                 where status = 'OPEN' and discussion_session_id is not null"""
        )
    finally:
        await conn.close()
    try:
        yield
    finally:
        await _purge()
        conn = await repo._conn()
        try:
            await conn.execute(
                f"update operator_item set status = 'OPEN' where status = '{PARKED}'"
            )
        finally:
            await conn.close()


async def _make_discussion(*, quiet_hours: float, last_turn: str) -> dict:
    """One OPEN item whose clarification lane last moved `quiet_hours` ago."""
    suffix = uuid.uuid4().hex[:8]
    ids = {
        "item_id": f"{PREFIX}item-{suffix}",
        "task_session": f"{PREFIX}task-{suffix}",
        "discussion_session": f"{PREFIX}disc-{suffix}",
        "task_id": f"{PREFIX}task-card-{suffix}",
    }
    await repo.create_running_session(ids["task_session"], AGENT)
    await repo.create_conversation_session(ids["discussion_session"], AGENT)
    await repo.update_session_run_state(ids["discussion_session"], _transcript(last_turn))
    await repo.mark_session_awaiting_operator(ids["discussion_session"])
    await repo.create_operator_item(
        ids["item_id"], "question", ids["task_session"], AGENT,
        "Which project should this land in?", task_id=ids["task_id"],
    )
    await repo.link_operator_item_discussion(ids["item_id"], ids["discussion_session"])
    conn = await repo._conn()
    try:
        await conn.execute(
            "update session set updated_at = now() - ($2 || ' hours')::interval"
            " where id = $1",
            ids["discussion_session"], str(quiet_hours),
        )
    finally:
        await conn.close()
    return ids


async def _events(kind: str, ref_id: str) -> list[dict]:
    conn = await repo._conn()
    try:
        rows = await conn.fetch(
            "select * from audit_event where kind = $1 and ref_id = $2 order by id",
            kind, ref_id,
        )
        return [dict(r) for r in rows]
    finally:
        await conn.close()


async def _sweep() -> dict:
    from central_command.heartbeat.actions import ACTIONS

    return await ACTIONS["discussion.sweep"].run("sched-test", {})


@needs_pg
async def test_a_fresh_discussion_is_not_swept(discussion_lab):
    ids = await _make_discussion(quiet_hours=0.5, last_turn="agent")
    result = await _sweep()
    assert result["stalled"] == [] and result["nudged"] == []
    assert await _events("discussion.stalled", ids["item_id"]) == []
    item = await repo.get_operator_item(ids["item_id"])
    assert item["stalled_at"] is None and item["nudged_at"] is None


@needs_pg
async def test_a_stalled_discussion_is_flagged_once(discussion_lab):
    """S1: the agent spoke last, so the operator is the missing party. The
    sweep runs every tick — flagging twice would be the nag this mechanism
    exists to avoid."""
    ids = await _make_discussion(quiet_hours=48, last_turn="agent")

    first = await _sweep()
    assert ids["item_id"] in first["stalled"]
    events = await _events("discussion.stalled", ids["item_id"])
    assert len(events) == 1
    payload = events[0]["payload"]
    if isinstance(payload, str):
        import json
        payload = json.loads(payload)
    assert payload["task_id"] == ids["task_id"]
    assert payload["agent_id"] == AGENT
    assert payload["discussion_session_id"] == ids["discussion_session"]
    assert payload["waiting_hours"] >= 48
    assert (await repo.get_operator_item(ids["item_id"]))["stalled_at"] is not None

    second = await _sweep()
    assert second["stalled"] == []
    assert len(await _events("discussion.stalled", ids["item_id"])) == 1


@needs_pg
async def test_a_stalled_discussion_awaiting_the_agent_is_nudged_once(
    discussion_lab, monkeypatch
):
    """S2: the operator replied and the agent never concluded. The nudge is a
    real agent turn, so the test monkeypatches it — a test must never call
    Claude."""
    from central_command.runtime import converse

    calls: list[tuple[str, str]] = []

    async def _fake_turn(session_id, message, model=None):
        calls.append((session_id, message))
        return {"session_id": session_id, "reply": "ok"}

    monkeypatch.setattr(converse, "conversation_turn", _fake_turn)

    ids = await _make_discussion(quiet_hours=48, last_turn="operator")

    first = await _sweep()
    assert ids["item_id"] in first["nudged"] and first["stalled"] == []
    assert len(calls) == 1
    assert calls[0][0] == ids["discussion_session"]
    assert "conclude_discussion" in calls[0][1]
    assert (await repo.get_operator_item(ids["item_id"]))["nudged_at"] is not None

    await _sweep()
    assert len(calls) == 1, "the nudge repeated inside one quiet window"


@needs_pg
async def test_a_sweep_that_finds_nothing_writes_no_event_at_all(discussion_lab):
    """The quiet rule, end to end through the ONE firing path: with nothing
    stalled, `fire` must leave the append-only log byte-for-byte unchanged.

    The watermark assertion is deliberately global — "no row was written" is
    exactly the claim, and it is only meaningful if nothing at all appeared.
    """
    from central_command.heartbeat.engine import fire

    await _make_discussion(quiet_hours=0.5, last_turn="agent")
    before = await repo.latest_event_id()
    out = await fire(
        {"id": "gvtest-sched", "action_kind": "discussion.sweep", "action_params": {}}
    )
    assert out["quiet"] is True, "a no-op sweep was recorded as material"
    assert await repo.latest_event_id() == before, "a no-op sweep wrote to the log"

    # ...and the same firing path IS loud the moment the sweep does something.
    # Without this half, the assertion above would pass just as happily against
    # a sweep that never writes anything under any circumstances.
    stalled = await _make_discussion(quiet_hours=48, last_turn="agent")
    out = await fire(
        {"id": "gvtest-sched", "action_kind": "discussion.sweep", "action_params": {}}
    )
    assert "quiet" not in out and out["ok"]
    assert stalled["item_id"] in out["result"]["stalled"]
    assert await repo.latest_event_id() > before


# --- concluding a discussion closes it, visibly (Task 3) ----------------------


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    """Nothing in this file may reach a real model. `conclude_discussion`
    schedules a task resume, which would run an agent for real."""
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "executor_mode", "dry_run")


@pytest.fixture()
def no_task_resume(monkeypatch):
    """Concluding kicks off `_resume_task_question` in the background — a full
    agent turn on the blocked task. These tests are about the CLOSE, so the
    resume is replaced with a recorder."""
    from central_command.api import orchestration

    resumed: list[tuple[str, str]] = []

    async def _fake_resume(item_id, answer):
        resumed.append((item_id, answer))

    monkeypatch.setattr(orchestration, "_resume_task_question", _fake_resume)
    return resumed


@needs_pg
async def test_concluding_a_discussion_emits_conversation_ended(
    discussion_lab, no_task_resume
):
    """The regression test for the live bug: `conclude_discussion` flipped the
    session to DONE and emitted nothing, so the cockpit's session panel — which
    refreshes on `cc.conversation.ended` — kept a stale open row. Push the
    frames, or the UI lies."""
    from central_command.runtime.questions import conclude_discussion

    ids = await _make_discussion(quiet_hours=1, last_turn="operator")
    await conclude_discussion(ids["discussion_session"], "Land it in TASKS.", AGENT)

    events = await _events("conversation.ended", ids["discussion_session"])
    assert len(events) == 1, "concluding a discussion pushed no close frame"
    payload = events[0]["payload"]
    if isinstance(payload, str):
        import json

        payload = json.loads(payload)
    assert payload["agent_id"] == AGENT
    assert payload["reason"] == "concluded"
    assert events[0]["actor"] == f"agent:{AGENT}"


@needs_pg
async def test_conclude_records_closed_reason_concluded(discussion_lab, no_task_resume):
    """The reason is STORED, not merely announced: one column serves the lock
    rule, the divider text and the event payload."""
    from central_command.runtime.questions import conclude_discussion

    ids = await _make_discussion(quiet_hours=1, last_turn="operator")
    await conclude_discussion(ids["discussion_session"], "Land it in TASKS.", AGENT)

    session = await repo.get_session(ids["discussion_session"])
    assert session["status"] == "DONE"
    assert session["closed_reason"] == "concluded"


def test_a_concluded_discussion_is_read_only():
    from central_command.runtime.converse import why_not_sendable

    blocked = why_not_sendable(
        {"id": "sess_c", "mode": "conversation", "status": "DONE",
         "closed_reason": "concluded"}
    )
    assert blocked is not None
    assert blocked[0] == "discussion_concluded"


def test_an_ordinary_closed_conversation_is_still_writable():
    """Deliberate existing behaviour (M15): closing is a status flip, so a
    closed conversation reopens by simply being written to. ONLY a concluded
    discussion locks."""
    from central_command.runtime.converse import why_not_sendable

    assert why_not_sendable(
        {"id": "sess_o", "mode": "conversation", "status": "DONE",
         "closed_reason": "operator"}
    ) is None
    assert why_not_sendable(
        {"id": "sess_o", "mode": "conversation", "status": "DONE",
         "closed_reason": None}
    ) is None
    assert why_not_sendable(
        {"id": "sess_o", "mode": "conversation", "status": "DONE"}
    ) is None


def test_a_locked_discussion_also_disables_the_cockpit_composer():
    """The composer state is an ALLOWLIST of codes, so a new refusal code that
    is not on it leaves an enabled message box over a send that always 409s —
    the exact composer/send drift `_send_target` was written to end."""
    from central_command.api.nerve_gateway import _COMPOSER_DISABLING

    assert "discussion_concluded" in _COMPOSER_DISABLING


async def test_composer_state_carries_closed_reason_to_the_cockpit(monkeypatch):
    """The cockpit's end-of-transcript divider names WHY a conversation ended,
    and 'operator' vs 'concluded' are different endings — one of which is still
    writable. The frontend declares its own interface over this payload, so a
    field the gateway never sends is invisible to typecheck AND to every
    frontend test: the divider would simply never appear for an
    operator-closed lane. Found 2026-07-27, adding the divider — `session_block`
    carried id/mode/status/agentId and nothing else.
    """
    from central_command.api import nerve_gateway
    from central_command.db import repo

    row = {
        "id": "sess_cr", "agent_id": "jira-expert", "mode": "conversation",
        "status": "DONE", "closed_reason": "operator",
    }

    async def fake_get(_sid):
        return row

    monkeypatch.setattr(repo, "get_session", fake_get)
    state = await nerve_gateway._composer_state("agent:jira-expert:sess_cr")

    assert state["session"]["closedReason"] == "operator", (
        "the divider cannot name an ending the server never reports"
    )
    # And the writable-when-operator-closed rule still holds: closing a plain
    # conversation does not lock it (writing reopens it), which is exactly why
    # the divider must key off closed_reason rather than status alone.
    assert state["enabled"] is True


async def test_decisions_list_carries_discussion_session_id(monkeypatch):
    """The Inbox pane branches on `discussion_session_id` to tell the operator
    a lane exists AND to label its answer box honestly. The cockpit declares
    that interface itself, so a gateway that stops sending the field is
    invisible to tsc and to every frontend test — the pane would silently
    render as a plain question (same failure mode as
    `test_composer_state_carries_closed_reason_to_the_cockpit`)."""
    from central_command.api import nerve_gateway
    from central_command.db import repo

    item = {
        "id": "oi_1", "kind": "question", "session_id": "sess_q",
        "agent_id": "ea", "task_id": "task_1", "body": "which board?",
        "status": "OPEN", "verdict": None, "answer": None,
        "created_at": None, "answered_at": None,
        "discussion_session_id": "sess_lane",
    }

    async def fake_items(_status):
        return [item]

    monkeypatch.setattr(repo, "list_operator_items", fake_items)
    monkeypatch.setattr(repo, "task_titles", lambda ids: _async({"task_1": "T"}))
    monkeypatch.setattr(repo, "list_proposals", lambda *a, **k: _async([]))
    monkeypatch.setattr(repo, "list_work_items", lambda *a, **k: _async([]))
    monkeypatch.setattr(repo, "work_items_for_sessions", lambda ids: _async({}))

    out = await nerve_gateway._decisions_list()
    assert out["operatorItems"][0]["discussion_session_id"] == "sess_lane", (
        "the pane cannot mention a lane the server never reports"
    )


async def test_decisions_list_carries_whether_the_lane_is_still_open(monkeypatch):
    """The Inbox pane cannot tell "Join the discussion" apart from "the lane
    already closed, answer here" without knowing the lane's session status —
    `discussion_session_id` alone says a lane once existed, not that it still
    does. Same wire-shape defence as the sibling test above: a gateway that
    stops sending this leaves the pane guessing, or worse, always offering a
    dead Join button."""
    from central_command.api import nerve_gateway
    from central_command.db import repo

    open_item = {
        "id": "oi_open", "kind": "question", "session_id": "sess_q1",
        "agent_id": "ea", "task_id": None, "body": "which board?",
        "status": "OPEN", "verdict": None, "answer": None,
        "created_at": None, "answered_at": None,
        "discussion_session_id": "sess_lane_open",
    }
    closed_item = {
        "id": "oi_closed", "kind": "question", "session_id": "sess_q2",
        "agent_id": "ea", "task_id": None, "body": "which board again?",
        "status": "OPEN", "verdict": None, "answer": None,
        "created_at": None, "answered_at": None,
        "discussion_session_id": "sess_lane_closed",
    }
    plain_item = {
        "id": "oi_plain", "kind": "question", "session_id": "sess_q3",
        "agent_id": "ea", "task_id": None, "body": "plain question",
        "status": "OPEN", "verdict": None, "answer": None,
        "created_at": None, "answered_at": None,
        "discussion_session_id": None,
    }

    monkeypatch.setattr(repo, "list_operator_items",
                         lambda _s: _async([open_item, closed_item, plain_item]))
    monkeypatch.setattr(repo, "task_titles", lambda ids: _async({}))
    monkeypatch.setattr(repo, "list_proposals", lambda *a, **k: _async([]))
    monkeypatch.setattr(repo, "list_work_items", lambda *a, **k: _async([]))
    monkeypatch.setattr(repo, "work_items_for_sessions", lambda ids: _async({}))

    def fake_statuses(ids):
        assert set(ids) == {"sess_lane_open", "sess_lane_closed"}
        return _async({"sess_lane_open": "AWAITING_OPERATOR", "sess_lane_closed": "DONE"})

    monkeypatch.setattr(repo, "session_statuses", fake_statuses)

    out = await nerve_gateway._decisions_list()
    by_id = {i["id"]: i for i in out["operatorItems"]}
    assert by_id["oi_open"]["lane_open"] is True, (
        "a non-terminal lane session must read as still joinable"
    )
    assert by_id["oi_closed"]["lane_open"] is False, (
        "a DONE lane session must not offer a dead Join button"
    )
    assert by_id["oi_plain"]["lane_open"] is None, (
        "an item with no lane at all has nothing to report as open or closed"
    )


async def test_decisions_list_carries_the_asks_source_email(monkeypatch):
    """An ask parked from the email path is ABOUT an email; the pane shows it
    the way a dismissal card does. Same wire-shape defence as the test above:
    the cockpit hand-declares `source_email`, so a gateway that stops sending
    it silently renders a bare question and nothing else fails."""
    from central_command.api import nerve_gateway
    from central_command.db import repo

    item = {
        "id": "oi_2", "kind": "question", "session_id": "sess_email",
        "agent_id": "inbox-triage", "task_id": None,
        "body": "is Goodwin & Co. your HOA?",
        "status": "OPEN", "verdict": None, "answer": None,
        "created_at": None, "answered_at": None,
        "discussion_session_id": None,
    }
    email = {
        "id": "wi_1", "kind": "email", "session_id": "sess_email",
        "subject": "Reminder: Budget Ratification Meeting",
        "payload": {"from": "hoa@example.com", "text": "please attend"},
    }

    monkeypatch.setattr(repo, "list_operator_items", lambda _s: _async([item]))
    monkeypatch.setattr(repo, "task_titles", lambda ids: _async({}))
    monkeypatch.setattr(repo, "list_proposals", lambda *a, **k: _async([]))
    monkeypatch.setattr(repo, "list_work_items", lambda *a, **k: _async([]))
    captured: dict = {}

    def fake_sources(ids):
        captured["ids"] = ids
        return _async({"sess_email": email})

    monkeypatch.setattr(repo, "work_items_for_sessions", fake_sources)

    out = await nerve_gateway._decisions_list()
    assert captured["ids"] == ["sess_email"], (
        "the lookup must be keyed by the asks' own session ids"
    )
    assert out["operatorItems"][0]["source_email"] == email, (
        "the pane cannot show an email the server never sends"
    )


async def _async(value):
    return value


# --- answering the ITEM closes its lane (seam 1) -------------------------------
# A tier-3 item and its lane are 1:1, and the question can be answered from
# either end. Concluding (the agent's end) already closed both; answering from
# the Decisions Inbox left the lane AWAITING_OPERATOR forever — an undead row
# whose question had an answer somewhere else.


ANSWER = "TASKS, please."


@needs_pg
async def test_answering_a_linked_question_closes_its_lane(
    discussion_lab, no_task_resume
):
    from central_command.api.orchestration import answer_item
    from central_command.runtime.durable import simplify_messages

    ids = await _make_discussion(quiet_hours=1, last_turn="agent")
    out = await answer_item(ids["item_id"], ANSWER)
    assert out["kind"] == "question"

    session = await repo.get_session(ids["discussion_session"])
    assert session["status"] == "DONE"
    assert session["closed_reason"] == "concluded"

    ended = await _events("conversation.ended", ids["discussion_session"])
    assert len(ended) == 1, "the lane closed without the frame the cockpit believes"
    payload = ended[0]["payload"]
    if isinstance(payload, str):
        import json

        payload = json.loads(payload)
    assert payload["agent_id"] == AGENT and payload["reason"] == "concluded"
    assert ended[0]["actor"] == "operator"

    # The lane shows WHY it ended: the operator's answer, verbatim, as the last
    # turn — a lane that goes terminal on its own question reads as truncated.
    turns = simplify_messages(await repo.get_session_run_state(ids["discussion_session"]))
    assert turns[-1]["kind"] == "user-prompt"
    assert turns[-1]["content"] == ANSWER

    assert (await repo.get_operator_item(ids["item_id"]))["status"] == "ANSWERED"
    await asyncio.sleep(0)
    assert no_task_resume == [(ids["item_id"], ANSWER)], "the task resume was dropped"


@needs_pg
async def test_answering_an_unlinked_question_is_unchanged(
    discussion_lab, no_task_resume
):
    """Tier 2 has no lane; the close is strictly additive to that path."""
    from central_command.api.orchestration import answer_item

    suffix = uuid.uuid4().hex[:8]
    item_id, session_id = f"{PREFIX}item-{suffix}", f"{PREFIX}task-{suffix}"
    await repo.create_running_session(session_id, AGENT)
    await repo.create_operator_item(item_id, "question", session_id, AGENT, "A or B?")

    before = await repo.latest_event_id()
    await answer_item(item_id, ANSWER)

    assert (await repo.get_operator_item(item_id))["status"] == "ANSWERED"
    kinds = [e["kind"] for e in await repo.list_events(since_id=before)]
    assert "conversation.ended" not in kinds
    await asyncio.sleep(0)
    assert no_task_resume == [(item_id, ANSWER)]


@needs_pg
async def test_answering_never_closes_a_lane_mid_turn(discussion_lab, no_task_resume):
    """RUNNING means a turn owns the session and will write its own status —
    closing under it would race that write. Skipping is the safe half."""
    from central_command.api.orchestration import answer_item

    ids = await _make_discussion(quiet_hours=1, last_turn="operator")
    conn = await repo._conn()
    try:
        await conn.execute(
            "update session set status = 'RUNNING' where id = $1",
            ids["discussion_session"],
        )
    finally:
        await conn.close()

    await answer_item(ids["item_id"], ANSWER)

    assert (await repo.session_status(ids["discussion_session"])) == "RUNNING"
    assert await _events("conversation.ended", ids["discussion_session"]) == []
    assert (await repo.get_operator_item(ids["item_id"]))["status"] == "ANSWERED"


@needs_pg
async def test_answering_an_already_closed_lane_does_not_re_close_it(
    discussion_lab, no_task_resume
):
    """Idempotent, and it must not overwrite the reason the lane really ended
    with — closing twice would announce an ending that already happened."""
    from central_command.api.orchestration import answer_item
    from central_command.runtime.converse import close_session

    ids = await _make_discussion(quiet_hours=1, last_turn="operator")
    await close_session(
        ids["discussion_session"], agent_id=AGENT, reason="operator", actor="operator",
    )

    await answer_item(ids["item_id"], ANSWER)

    session = await repo.get_session(ids["discussion_session"])
    assert session["closed_reason"] == "operator"
    assert len(await _events("conversation.ended", ids["discussion_session"])) == 1


@needs_pg
async def test_an_inbox_closed_lane_reaches_the_composer_as_concluded(
    discussion_lab, no_task_resume, monkeypatch
):
    """The wire, on the REAL row this path produces: no new `closed_reason`
    value was introduced, so the cockpit's existing rendering applies — the
    divider names the ending and the composer is disabled. A lane answered
    elsewhere must not stay writable, or writing reopens it and the undead row
    is back."""
    from central_command.api import nerve_gateway
    from central_command.api.orchestration import answer_item

    ids = await _make_discussion(quiet_hours=1, last_turn="agent")
    await answer_item(ids["item_id"], ANSWER)
    row = await repo.get_session(ids["discussion_session"])

    # The session key resolves only ids shaped `sess_…`; the row under test is
    # the real one this path just closed.
    async def fake_get(_sid):
        return row

    monkeypatch.setattr(repo, "get_session", fake_get)
    state = await nerve_gateway._composer_state(f"agent:{AGENT}:sess_answered")

    assert state["session"]["closedReason"] == "concluded"
    assert state["enabled"] is False and state["code"] == "discussion_concluded"
