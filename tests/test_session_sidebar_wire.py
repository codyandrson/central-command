"""The agents sidebar can only render what `sessions.list` actually sends.

Same defence as `test_agent_initiated`: the cockpit hand-declares its interface
over an untyped payload, so a missing field is invisible to `tsc` and to every
frontend test — the sidebar just shows a raw uuid, an empty context bar and an
IDLE badge forever. Assert the exact camelCase keys here, on the backend.

The one pg-backed test is about the LIST QUERY: `label` and `totalTokens` come
from columns computed in SQL (`task_title`, `token_estimate`), and a wrong
expression there passes every stubbed test.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from central_command.api import nerve_gateway
from central_command.config import settings
from central_command.db import repo
from central_command.runtime import roster as roster_mod
from tests.conftest import needs_pg

NOW = datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    # `contextTokens` resolves a model window; demo mode keeps that off the proxy.
    monkeypatch.setattr(settings, "demo_mode", True)


def _row(sess_id: str, **over) -> dict:
    row = {
        "id": sess_id,
        "agent_id": "jira-expert",
        "status": "RUNNING",
        "mode": "oneshot",
        "created_at": NOW,
        "updated_at": NOW,
        "parent_session_id": None,
        "parent_agent_id": None,
        "closed_reason": None,
        "model_override": None,
        "latest_intent": None,
        "proposal_count": 0,
        "task_title": "Reconcile the sprint board",
        "token_estimate": 12_000,
    }
    row.update(over)
    return row


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


async def test_a_session_row_carries_label_tokens_and_a_running_state(monkeypatch):
    _wire(monkeypatch, [_row("sess_live")])

    row = (await nerve_gateway._sessions_list({}))["sessions"][0]

    assert row["label"] == "Reconcile the sprint board", (
        "without a label the sidebar falls back to the session-key tail — a uuid"
    )
    assert row["totalTokens"] == 12_000, "the context bar reads totalTokens"
    assert row["contextTokens"] == settings.context_window, "…over this maximum"
    assert row["state"] == "running", (
        "the badge only recognises lowercase state/status — uppercase RUNNING "
        "renders as IDLE"
    )
    assert row["status"] == "RUNNING", (
        "the uppercase vocabulary must survive: SessionList filters terminal "
        "statuses and chips AWAITING_OPERATOR off exactly this field"
    )


async def test_a_waiting_session_is_not_running_and_still_gets_a_label(monkeypatch):
    _wire(monkeypatch, [_row("sess_wait", status="AWAITING_HUMAN", task_title=None)])

    row = (await nerve_gateway._sessions_list({}))["sessions"][0]

    assert row["state"] == "idle"
    assert row["status"] == "AWAITING_HUMAN"
    assert "sess_wait" not in row["label"] and row["label"], (
        "a task-less lane still needs a human-readable fallback, never the id"
    )
    assert "jira-expert" in row["label"]


async def test_an_exec_failed_rest_carries_its_reason_to_the_sidebar(monkeypatch):
    _wire(monkeypatch, [_row(
        "sess_exec", status="AWAITING_OPERATOR",
        exec_failure="Jira 400: no project SUPP",
    )])

    row = (await nerve_gateway._sessions_list({}))["sessions"][0]

    assert row["execFailure"] == "Jira 400: no project SUPP", (
        "the red chip and the row's ✕ both key off execFailure — absent from "
        "the wire, the failure rests open and INVISIBLE, which is the silent "
        "close this field exists to end (operator's rule, 2026-08-20)"
    )


async def test_a_retired_agent_root_row_is_history_not_an_open_lane(monkeypatch):
    """The sidebar's history filter hides rows by TERMINAL status only. A
    retired agent's synthetic root must therefore carry one — with 'IDLE' it
    sits in the default view forever, which is the roster page's job."""
    from central_command.runtime.roster import AgentDef

    _wire(monkeypatch, [])

    async def fake_roster(include_retired=False):
        return (
            AgentDef(id="jira-expert", name="Jira Expert", role="jira",
                     conversational=True),
            AgentDef(id="old-timer", name="Old Timer", role="ops",
                     conversational=True, status="RETIRED"),
        )

    monkeypatch.setattr(roster_mod, "roster", fake_roster)

    rows = {r["sessionKey"]: r for r in (await nerve_gateway._sessions_list({}))["sessions"]}

    active = rows["agent:jira-expert:main"]
    assert active["status"] == "IDLE" and active["conversational"] is True

    retired = rows["agent:old-timer:main"]
    assert retired["status"] in {"DONE", "FAILED", "CANCELLED"}, (
        "a non-terminal status keeps the retired agent in the default sidebar"
    )
    assert retired["conversational"] is False
    assert "(retired)" in retired["label"]


async def test_the_sidebar_asks_list_sessions_to_exclude_terminal_oneshots(monkeypatch):
    """Live DB has 3408 terminal oneshot dispatch runs vs 50 closed
    conversations — an unfiltered fetch pushes conversations out of the
    `limit` window before the client-side history toggle ever sees them. The
    sidebar branch (not spawnedBy, not Activity/Runs) must opt in."""
    captured: dict = {}

    async def fake_list_sessions(_limit=50, **kw):
        captured.update(kw)
        return []

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

    await nerve_gateway._sessions_list({})
    assert captured.get("exclude_terminal_oneshot") is True


async def test_the_spawnedby_child_lookup_does_not_exclude_terminal_oneshots(monkeypatch):
    """The child-session branch needs every row a parent spawned, terminal or
    not — it must not pick up the sidebar's filter."""
    captured: dict = {}

    async def fake_list_sessions(_limit=50, **kw):
        captured.update(kw)
        return []

    async def fake_roster(include_retired=False):
        return []

    monkeypatch.setattr(repo, "list_sessions", fake_list_sessions)
    monkeypatch.setattr(roster_mod, "roster", fake_roster)
    monkeypatch.setattr(repo, "work_items_for_sessions", lambda _ids: _empty())

    await nerve_gateway._sessions_list({"spawnedBy": "agent:orch:sess_parent"})
    assert "exclude_terminal_oneshot" not in captured


async def _empty():
    return {}


async def test_the_row_model_follows_the_operator_override(monkeypatch):
    _wire(monkeypatch, [_row("sess_pinned", model_override="qwen3-coder")])

    row = (await nerve_gateway._sessions_list({}))["sessions"][0]
    assert row["model"] == "qwen3-coder"


async def test_a_session_spawned_from_an_email_carries_its_source(monkeypatch):
    """The dispatcher stamps `work_item.session_id` when it claims an email —
    the sidebar chip needs the whole work item (subject, sender, body) in the
    SAME payload, resolved in one batched call for the whole page."""
    _wire(monkeypatch, [_row("sess_from_email"), _row("sess_plain")])

    email = {
        "id": "wi_1", "kind": "email", "session_id": "sess_from_email",
        "subject": "Reminder: Budget Ratification Meeting",
        "payload": {"from": "hoa@example.com", "text": "please attend"},
    }
    captured: dict = {}

    async def fake_work_items_for_sessions(ids):
        captured["ids"] = list(ids)
        return {"sess_from_email": email}

    monkeypatch.setattr(repo, "work_items_for_sessions", fake_work_items_for_sessions)

    rows = {
        r["sessionKey"]: r for r in (await nerve_gateway._sessions_list({}))["sessions"]
    }
    assert sorted(captured["ids"]) == ["sess_from_email", "sess_plain"], (
        "must resolve for the whole page in one call, not per row"
    )
    assert rows["agent:jira-expert:sess_from_email"]["sourceEmail"] == email, (
        "the cockpit cannot show an email the server never sends"
    )
    assert rows["agent:jira-expert:sess_plain"]["sourceEmail"] is None


@needs_pg
async def test_exclude_terminal_oneshot_drops_done_but_keeps_running_and_conversations():
    agent_id = "sidebar-filter-agent"
    await repo.upsert_agent(agent_id, "Sidebar Filter", "sidebar", "")

    done_oneshot = "sess_" + uuid.uuid4().hex[:12]
    running_oneshot = "sess_" + uuid.uuid4().hex[:12]
    closed_conversation = "sess_" + uuid.uuid4().hex[:12]

    conn = await repo._conn()
    try:
        await conn.execute(
            "insert into session (id, agent_id, status, mode) values "
            "($1, $2, 'DONE', 'oneshot'), "
            "($3, $2, 'RUNNING', 'oneshot'), "
            "($4, $2, 'DONE', 'conversation')",
            done_oneshot, agent_id, running_oneshot, closed_conversation,
        )
    finally:
        await conn.close()

    default_ids = {r["id"] for r in await repo.list_sessions(200, agent_id=agent_id)}
    assert default_ids == {done_oneshot, running_oneshot, closed_conversation}, (
        "default behavior (no flag) must be unchanged"
    )

    filtered_ids = {
        r["id"] for r in
        await repo.list_sessions(200, agent_id=agent_id, exclude_terminal_oneshot=True)
    }
    assert filtered_ids == {running_oneshot, closed_conversation}, (
        "a terminal oneshot is dropped; a running oneshot and a closed "
        "conversation both stay"
    )


@needs_pg
async def test_the_list_query_computes_a_title_and_a_token_estimate():
    """The SQL, not the shape: `task_title` joins the run's task and
    `token_estimate` sizes the LIVE window (chars/4), matching
    `context.estimate_tokens`."""
    agent_id = "sidebar-test-agent"
    await repo.upsert_agent(agent_id, "Sidebar Test", "sidebar", "")
    session_id = "sess_" + uuid.uuid4().hex[:12]
    await repo.create_running_session(session_id, agent_id)
    await repo.save_paused_session(session_id, agent_id, {
        "messages": [{"kind": "request", "parts": [
            {"part_kind": "user-prompt", "content": "x" * 4_000}]}],
        "archived": [{"kind": "request", "parts": [
            {"part_kind": "user-prompt", "content": "y" * 40_000}]}],
    })
    task_id = "task_" + uuid.uuid4().hex[:12]
    await repo.create_task(task_id, "Rename the widgets", "do it", agent_id)
    await repo.start_task(task_id, session_id)

    rows = {r["id"]: r for r in await repo.list_sessions(200)}
    row = rows[session_id]

    assert row["task_title"] == "Rename the widgets"
    assert 900 < row["token_estimate"] < 1_400, (
        f"~4000 chars of live window is ~1000 tokens, got {row['token_estimate']}"
        " — the archive must not count, it is never sent"
    )
