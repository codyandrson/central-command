"""An agent-opened conversation lane must be visible AS SUCH to the cockpit.

`runtime/questions.py:_open_discussion` already creates an answerable
`AWAITING_OPERATOR` lane and links it from the OPEN `operator_item` — the
runtime side has worked end-to-end since the discussion tier landed. What was
missing is the WIRE: `sessions.list` rows said nothing about who started the
conversation, so an agent's question looked exactly like a lane the operator
had opened themselves.

These are wire-shape tests, modelled on
`test_composer_state_carries_closed_reason_to_the_cockpit`: the cockpit
hand-declares its own interfaces over these untyped payloads, so a field the
gateway never sends is invisible to `tsc` AND to every frontend test — the
chip and the toast would simply never appear. Assert the exact camelCase keys
here, on the backend, where it can actually fail.
"""
from datetime import datetime, timezone

from central_command.api import nerve_gateway
from central_command.db import repo
from central_command.runtime import roster as roster_mod

NOW = datetime(2026, 7, 29, 9, 0, tzinfo=timezone.utc)


def _session(sess_id: str, agent_id: str = "jira-expert", status: str = "AWAITING_OPERATOR"):
    return {
        "id": sess_id,
        "agent_id": agent_id,
        "status": status,
        "mode": "conversation",
        "created_at": NOW,
        "updated_at": NOW,
        "parent_session_id": None,
        "parent_agent_id": None,
        "closed_reason": None,
        "latest_intent": None,
        "proposal_count": 0,
    }


def _wire(monkeypatch, *, sessions, operator_items, titles=None):
    """Stub every store `_sessions_list` reads, so the test asserts the SHAPE
    of the payload rather than the contents of the dev database."""
    async def fake_list_sessions(_limit=50, **_kw):
        return list(sessions)

    async def fake_list_operator_items(status="OPEN"):
        return [i for i in operator_items if i["status"] == status]

    async def fake_task_titles(ids):
        return {k: v for k, v in (titles or {}).items() if k in set(ids)}

    async def fake_roster(include_retired=False):
        return []

    async def fake_work_items_for_sessions(_ids):
        return {}

    monkeypatch.setattr(repo, "list_sessions", fake_list_sessions)
    monkeypatch.setattr(repo, "list_operator_items", fake_list_operator_items)
    monkeypatch.setattr(repo, "task_titles", fake_task_titles)
    monkeypatch.setattr(roster_mod, "roster", fake_roster)
    monkeypatch.setattr(repo, "work_items_for_sessions", fake_work_items_for_sessions)


async def test_sessions_list_marks_an_agent_opened_lane(monkeypatch):
    """A lane an OPEN operator_item points at was opened BY THE AGENT."""
    _wire(
        monkeypatch,
        sessions=[_session("sess_agent"), _session("sess_operator")],
        operator_items=[{
            "id": "item_1", "status": "OPEN", "session_id": "sess_task",
            "discussion_session_id": "sess_agent", "task_id": "task_9",
        }],
        titles={"task_9": "Reconcile the sprint board"},
    )

    rows = (await nerve_gateway._sessions_list({}))["sessions"]
    by_key = {r["key"]: r for r in rows}

    asked = by_key["agent:jira-expert:sess_agent"]
    assert asked["openedBy"] == "agent", (
        "without this field the cockpit cannot tell an agent's question from a "
        "lane the operator opened — no chip, no toast, no unread"
    )
    assert asked["blockedTaskTitle"] == "Reconcile the sprint board"

    plain = by_key["agent:jira-expert:sess_operator"]
    assert plain["openedBy"] == "operator"
    assert plain["blockedTaskTitle"] is None


async def test_an_answered_ask_no_longer_marks_its_lane(monkeypatch):
    """Only OPEN asks count. Once answered, the lane is an ordinary
    conversation again — otherwise the chip would never clear."""
    _wire(
        monkeypatch,
        sessions=[_session("sess_agent")],
        operator_items=[{
            "id": "item_1", "status": "ANSWERED", "session_id": "sess_task",
            "discussion_session_id": "sess_agent", "task_id": None,
        }],
    )

    rows = (await nerve_gateway._sessions_list({}))["sessions"]
    assert rows[0]["openedBy"] == "operator"


async def test_an_ask_without_a_task_still_marks_the_lane(monkeypatch):
    """`blockedTaskTitle` is optional context; `openedBy` is the fact. An ask
    raised outside a task must still announce itself."""
    _wire(
        monkeypatch,
        sessions=[_session("sess_agent")],
        operator_items=[{
            "id": "item_1", "status": "OPEN", "session_id": "sess_task",
            "discussion_session_id": "sess_agent", "task_id": None,
        }],
    )

    row = (await nerve_gateway._sessions_list({}))["sessions"][0]
    assert row["openedBy"] == "agent"
    assert row["blockedTaskTitle"] is None


async def test_composer_is_enabled_on_an_agent_opened_lane(monkeypatch):
    """The whole point of an agent-opened lane is that the operator can ANSWER
    in it. `_composer_state` must not refuse an `AWAITING_OPERATOR`
    conversation — a chip pointing at a read-only box would be worse than no
    chip at all. (`_COMPOSER_DISABLING` is an allowlist; nothing here belongs
    on it.)"""
    async def fake_get(_sid):
        return {"id": "sess_agent", "agent_id": "jira-expert",
                "mode": "conversation", "status": "AWAITING_OPERATOR",
                "closed_reason": None}

    monkeypatch.setattr(repo, "get_session", fake_get)
    state = await nerve_gateway._composer_state("agent:jira-expert:sess_agent")

    assert state["enabled"] is True
    # No refusal code at all — not merely one that happens to be off the
    # allowlist. (Asserting on a key the payload never carries would pass
    # forever; `_composer_state` reports refusals as `code`.)
    assert "code" not in state, state
