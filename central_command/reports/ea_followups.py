"""The dated commitments the team is tracking — a read of the issue tracker.

A follow-up is STATE: it opens, it has an owner, it closes. Until v2.59.0 the
EA kept these as private graph episodes and this module read them back with a
semantic search. The graph cannot close anything — a "closing episode" only
retires the old fact if the extractor happens to invalidate it — and the first
thing the feature recorded on a live instance was the agent's own run state.
So follow-ups live where they can be closed: a question is an operator item, a
teammate's work is a task (both already in `ea_report.snapshot()`), and a dated
commitment is an issue carrying `FOLLOW_UP_LABEL`. This is the read of the last.

A SIBLING of `ea_calendar.day()`, deliberately, for the same reason: an
outside read over HTTP that can time out must degrade the EA's contact, not
take the whole snapshot with it. Do NOT put this in `ea_report` (pure
Postgres — see its docstring). Like `day()`, this function does not catch its
own failures; the caller (`heartbeat/actions.py`) degrades it exactly like
the calendar block, and omits it when the tracker is not configured.
"""

from __future__ import annotations

from central_command.integrations import jira
from central_command.runtime.ea_profile import FOLLOW_UP_LABEL

_JQL = f'labels = "{FOLLOW_UP_LABEL}" AND statusCategory != Done ORDER BY duedate ASC'


async def tracked(max_issues: int = 15) -> dict:
    """Open issues labelled as follow-ups, soonest due first. Facts only, no
    judgment — the agent narrates, never invents (same doctrine as the
    calendar block)."""
    if not jira.configured():
        return {"available": False, "reason": "not configured"}
    out = await jira.search_issues(_JQL, limit=max_issues)
    return {
        "available": True,
        "count": out["count"],
        "truncated": out["truncated"],
        "issues": [
            {"issue_key": i["issue_key"], "summary": i["summary"],
             "status": i["status"], "due_date": i["due_date"],
             "assignee": i["assignee"], "url": i["url"]}
            for i in out["issues"]
        ],
    }
