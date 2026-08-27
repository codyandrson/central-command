"""The kanban board's `/api/tasks` route resolves each task's source email
from its `parent_session_id` — the session that proposed or fanned it out —
via the same `work_items_for_sessions` batch lookup the Decisions Inbox uses
for asks (see test_discussion_stall.py's sibling test). The cockpit
hand-declares this field, so only a backend test catches it going silent.
"""
from __future__ import annotations

from central_command.api import routes
from central_command.db import repo


async def _async(value):
    return value


async def test_tasks_list_carries_source_email_from_the_parent_session(monkeypatch):
    task_with_parent = {
        "id": "task_1", "title": "Follow up", "instructions": "…",
        "agent_id": "jira-expert", "status": "ASSIGNED", "session_id": None,
        "parent_session_id": "sess_email", "outcome": None,
        "created_at": None, "started_at": None, "terminal_at": None,
        "session_status": None, "stopped": False,
    }
    task_without_parent = {
        "id": "task_2", "title": "Operator ask", "instructions": "…",
        "agent_id": "jira-expert", "status": "NEW", "session_id": None,
        "parent_session_id": None, "outcome": None,
        "created_at": None, "started_at": None, "terminal_at": None,
        "session_status": None, "stopped": False,
    }
    email = {
        "id": "wi_1", "kind": "email", "session_id": "sess_email",
        "subject": "Reminder: renew the domain",
        "payload": {"from": "billing@example.com", "text": "please renew"},
    }

    monkeypatch.setattr(
        repo, "list_tasks", lambda **k: _async([task_with_parent, task_without_parent])
    )
    monkeypatch.setattr(repo, "task_counts", lambda: _async({}))

    captured: dict = {}

    def fake_sources(ids):
        captured["ids"] = ids
        return _async({"sess_email": email})

    monkeypatch.setattr(repo, "work_items_for_sessions", fake_sources)

    from central_command.runtime import roster

    monkeypatch.setattr(roster, "taskable_agent_ids", lambda: _async(set()))

    out = await routes.list_tasks()

    assert captured["ids"] == ["sess_email"], (
        "the lookup must be keyed by the tasks' own parent session ids, batched once"
    )
    by_id = {t["id"]: t for t in out["tasks"]}
    assert by_id["task_1"]["source_email"] == {
        "subject": "Reminder: renew the domain",
        "from": "billing@example.com",
        "text": "please renew",
    }
    assert "source_email" not in by_id["task_2"], (
        "a task with no parent session must render nothing, not a broken block"
    )
