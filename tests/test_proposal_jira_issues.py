"""Wire-shape test for the Jira snapshot `proposals.get` attaches
(`jira_issues`). The cockpit hand-declares this interface, so a route that
stops sending it is invisible to tsc and to every frontend test — the issue
cards would silently vanish and the operator would be back to reading
`jira:TASKS-48`. Same pattern as tests/test_decisions_history.py: the repo
and the Jira client are faked, the route is called directly."""

from central_command.api import routes
from central_command.db import repo
from central_command.integrations import jira


async def _async(value):
    return value


ACTIONS = [
    {"capability": "jira.add_comment@v1", "target_ref": {"system": "jira", "id": "TASKS-48"},
     "arguments": {"issue_key": "TASKS-48", "body": "dup"}},
    # The link's other end lives only in the arguments — a reviewer needs
    # BOTH issues to judge a duplicate claim.
    {"capability": "jira.link_issues@v1", "target_ref": {"system": "jira", "id": "TASKS-48"},
     "arguments": {"from_key": "TASKS-48", "to_key": "TASKS-50", "link_type": "Duplicate"}},
    {"capability": "graph.add_episode@v1", "target_ref": {"system": "graphiti", "id": "central_command"},
     "arguments": {"episode_body": "x"}},
]


def test_keys_are_deduped_and_include_argument_counterparts():
    assert routes._jira_keys_in(ACTIONS) == ["TASKS-48", "TASKS-50"]


async def test_get_proposal_attaches_live_jira_snapshot(monkeypatch):
    row = {"id": "prop_1", "session_id": "sess_1", "agent_id": "jira-expert",
           "intent": "sweep", "status": "EXECUTED", "actions": ACTIONS, "evidence": []}
    monkeypatch.setattr(repo, "load_proposal", lambda _id: _async(dict(row)))
    monkeypatch.setattr(repo, "work_items_for_session", lambda _s: _async([]))
    monkeypatch.setattr(repo, "work_items_folded_into", lambda _p: _async([]))

    async def fake_get_issue(key):
        if key == "TASKS-50":
            raise jira.JiraError("getIssue TASKS-50 — 401")
        return {"ok": True, "issue": {
            "issue_key": key, "summary": "MyIDCare dark-web alert", "status": "To Do",
            "issue_type": "Task", "priority": "Medium", "assignee": "Jane Doe",
            "due_date": None, "labels": ["personal"], "updated": "2026-09-01T00:00:00.000+0000",
            "url": "https://example.atlassian.net/browse/" + key, "description": "body",
            "custom_fields": {"secret": "not for the card"}, "links": [],
        }}

    monkeypatch.setattr(jira, "get_issue", fake_get_issue)

    out = await routes.get_proposal("prop_1")

    got = out["jira_issues"]["TASKS-48"]
    assert got["summary"] == "MyIDCare dark-web alert"
    assert got["status"] == "To Do"
    assert got["url"].endswith("/browse/TASKS-48")
    assert got["description"] == "body"
    assert "custom_fields" not in got
    # One dead issue never takes the detail down with it.
    assert "401" in out["jira_issues"]["TASKS-50"]["error"]


async def test_get_proposal_without_jira_targets_sends_empty_map(monkeypatch):
    row = {"id": "prop_2", "session_id": "sess_2", "agent_id": "ea", "intent": "reply",
           "status": "EXECUTED", "actions": [ACTIONS[2]], "evidence": []}
    monkeypatch.setattr(repo, "load_proposal", lambda _id: _async(dict(row)))
    monkeypatch.setattr(repo, "work_items_for_session", lambda _s: _async([]))
    monkeypatch.setattr(repo, "work_items_folded_into", lambda _p: _async([]))

    async def never(_key):
        raise AssertionError("no Jira read for a proposal that touches no issue")

    monkeypatch.setattr(jira, "get_issue", never)
    out = await routes.get_proposal("prop_2")
    assert out["jira_issues"] == {}
