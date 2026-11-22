"""Renaming a conversation from the cockpit's sessions panel (DL-229).

`sessions.patch` with `label` writes `session.operator_title` and nothing else:
the transcript (`run_state`), the proposals and the recorded events keep what
they said, and `updated_at` is untouched (a rename is not activity). An agent's
root row carries the roster's name and a run is named by its task, so both are
refused loudly instead of being accepted and ignored.

The wire half is pinned here too, on the backend, because the cockpit
hand-declares its interface over the untyped RPC payload: a `label` or
`operatorTitle` the gateway never sends is invisible to `tsc` and to every
frontend test.
"""

from __future__ import annotations

import json
import uuid

import pytest
from fastapi import HTTPException

from central_command.api import nerve_gateway
from central_command.config import settings
from central_command.db import repo
from central_command.runtime import roster as roster_mod
from tests.conftest import needs_pg


async def _noop(_frame):
    return None


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(settings, "test_model", True)


async def _conversation(agent_id: str = "inbox-triage") -> str:
    session_id = "sess_" + uuid.uuid4().hex[:12]
    await repo.create_conversation_session(session_id, agent_id)
    return session_id


async def _rename(key: str, label):
    return await nerve_gateway._dispatch(
        "sessions.patch", {"key": key, "label": label}, _noop,
    )


# --- the wire (stubbed list, no database) ---------------------------------------


def _wire(monkeypatch, rows: list[dict]) -> None:
    async def fake_list_sessions(_limit=50, **_kw):
        return list(rows)

    async def fake_list_operator_items(status="OPEN"):
        return []

    async def fake_roster(include_retired=False):
        return []

    async def fake_work_items_for_sessions(_ids):
        return {}

    monkeypatch.setattr(repo, "list_sessions", fake_list_sessions)
    monkeypatch.setattr(repo, "list_operator_items", fake_list_operator_items)
    monkeypatch.setattr(roster_mod, "roster", fake_roster)
    monkeypatch.setattr(repo, "work_items_for_sessions", fake_work_items_for_sessions)


def _row(sess_id: str, **over) -> dict:
    row = {
        "id": sess_id, "agent_id": "inbox-triage", "status": "AWAITING_OPERATOR",
        "mode": "conversation", "created_at": None, "updated_at": None,
        "parent_session_id": None, "parent_agent_id": None, "task_title": None,
        "token_estimate": 0, "proposal_count": 0,
    }
    row.update(over)
    return row


async def test_a_renamed_conversation_is_labelled_with_the_operators_name(monkeypatch):
    _wire(monkeypatch, [
        _row("sess_named", operator_title="Budget questions", task_title="Some task"),
        _row("sess_plain"),
    ])

    rows = {r["sessionKey"]: r for r in (await nerve_gateway._sessions_list({}))["sessions"]}
    named = rows["agent:inbox-triage:sess_named"]
    plain = rows["agent:inbox-triage:sess_plain"]

    assert named["label"] == "Budget questions", "the operator's name beats the task title"
    assert named["operatorTitle"] == "Budget questions"
    assert named["labelStamped"] is False, (
        "a name the operator chose is shown as typed, with no start time appended"
    )
    assert plain["operatorTitle"] is None
    assert plain["labelStamped"] is True


# --- the lever (Postgres) --------------------------------------------------------


@needs_pg
async def test_renaming_a_conversation_stores_the_name_and_records_it():
    session_id = await _conversation()
    before = await repo.get_session(session_id)
    key = f"agent:inbox-triage:{session_id}"

    out = await _rename(key, "  Budget\n questions  ")

    assert out == {"key": key, "sessionId": session_id, "label": "Budget questions"}
    after = await repo.get_session(session_id)
    assert after["operator_title"] == "Budget questions"
    assert after["updated_at"] == before["updated_at"], "a rename is not activity"
    assert after["status"] == before["status"]

    events = [e for e in await repo.list_events_of_kinds(["session.renamed"])
              if e["ref_id"] == session_id]
    assert len(events) == 1
    payload = events[0]["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    assert events[0]["actor"] == "operator"
    assert payload == {"session_id": session_id, "agent_id": "inbox-triage",
                       "old": None, "new": "Budget questions"}


@needs_pg
async def test_a_rename_leaves_the_transcript_untouched():
    session_id = await _conversation()
    conn = await repo._conn()
    try:
        await conn.execute(
            "update session set run_state = $2::jsonb where id = $1",
            session_id, json.dumps({"messages": [{"kind": "request", "text": "hello"}]}),
        )
        before = await conn.fetchval("select run_state::text from session where id = $1", session_id)
        await _rename(f"agent:inbox-triage:{session_id}", "Hello thread")
        after = await conn.fetchval("select run_state::text from session where id = $1", session_id)
    finally:
        await conn.close()
    assert after == before


@needs_pg
async def test_an_empty_name_clears_it_and_the_same_name_records_nothing():
    session_id = await _conversation()
    key = f"agent:inbox-triage:{session_id}"
    await _rename(key, "First")
    await _rename(key, "First")
    out = await _rename(key, "   ")

    assert out["label"] is None
    assert (await repo.get_session(session_id))["operator_title"] is None
    kinds = [e for e in await repo.list_events_of_kinds(["session.renamed"])
             if e["ref_id"] == session_id]
    assert len(kinds) == 2, "set, then clear; the unchanged repeat is not recorded"


@needs_pg
async def test_an_agents_root_row_cannot_be_renamed_here():
    with pytest.raises(HTTPException) as exc:
        await _rename("agent:inbox-triage:main", "Not the roster name")
    assert exc.value.status_code == 409
    assert "roster" in exc.value.detail


@needs_pg
async def test_a_run_cannot_be_renamed():
    session_id = "sess_" + uuid.uuid4().hex[:12]
    await repo.create_running_session(session_id, "inbox-triage")
    with pytest.raises(HTTPException) as exc:
        await _rename(f"agent:inbox-triage:{session_id}", "A run")
    assert exc.value.status_code == 409
    assert (await repo.get_session(session_id))["operator_title"] is None


@needs_pg
async def test_a_name_too_long_or_not_a_string_is_refused():
    session_id = await _conversation()
    key = f"agent:inbox-triage:{session_id}"
    for bad, status in (("x" * (nerve_gateway.SESSION_TITLE_MAX + 1), 422), (42, 422)):
        with pytest.raises(HTTPException) as exc:
            await _rename(key, bad)
        assert exc.value.status_code == status
    assert (await repo.get_session(session_id))["operator_title"] is None


@needs_pg
async def test_the_list_query_carries_the_name_to_the_wire():
    """`label` is computed from a column the LIST QUERY must select; a stubbed
    list proves nothing about the SQL."""
    session_id = await _conversation()
    await _rename(f"agent:inbox-triage:{session_id}", "Renamed lane")
    rows = await repo.list_sessions(1000, exclude_terminal_oneshot=True)
    row = next(r for r in rows if r["id"] == session_id)
    assert row["operator_title"] == "Renamed lane"
    assert nerve_gateway._row_label(row, None) == "Renamed lane"
