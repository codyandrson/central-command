"""The team's load and the operator's delegated work — reads of the issue tracker.

The EA planning design of 2026-11-18, D10 and D2's `delegated` block
(`docs/superpowers/specs/2026-11-18-ea-planning-design.md`), on the doctrine of
the 2026-08-21 staffing record (DL-176): capacity is READ live from the tracker
at decision time, never tracked. Two reads, each with its arithmetic in a pure
function so the counts are computed here and never by the model:

- `team_load()` (pure) and `load()` (its read, for the `jira_team_load` tool the
  staffing agent holds): per assignee, the open issues, how many are in
  progress, overdue, and due in the next 7 and 14 days, the estimates where
  issues carry them, and the oldest overdue issue; plus the unassigned count
  from a second bounded search when the team JQL can be turned round cheaply.
- `delegated_rows()` (pure) and `delegated()` (its read, for the EA's brief):
  the issues the operator is waiting on from other people.

A SIBLING of `ea_backlog`, for its reason: an outside read over HTTP that can
time out must degrade one key of the EA's brief, never the contact. Neither
read catches its own failures; the tool parks on an outage and the heartbeat
degrades the block.

THE RULES. "Today" is the operator's local date (`ea_backlog._today()`). An
issue is OVERDUE when its due date is before today (an issue due today is
not). It is due in the next N days when today <= due date <= today + N, the
`deadline_coverage` window. In progress is the status CATEGORY
`indeterminate`, the category both Jira products give a started status. An
estimate is a finite JSON number above zero, the ranking's own rule
(`ea_backlog._number`, zero treated as absent); an issue without one is counted
under `unestimated`, never as zero hours. A row of the read whose status
category is `done` is not open work and is counted under `done_in_read` only;
a row with no assignee is counted under `unassigned_in_read` and attributed to
nobody. The numbers are a prompt for a question, never a measurement: work
that is not in the tracker is invisible here, and every result says so.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from central_command.config import settings
from central_command.integrations import jira
from central_command.reports.ea_backlog import _clip, _due, _number, _today

SUMMARY_CHARS = 200

# What every team-load result says it cannot show. Stated in the result, not
# only in a charter, so the reader cannot lose it between the number and its use.
CANNOT_SHOW = (
    "These counts are only what the tracker holds. Work that is not in the "
    "tracker (meetings, support, reviews, anything unticketed) is invisible "
    "here, and an estimate is somebody's guess. Treat a number as a prompt for "
    "a question to the person, never as a measurement of how busy they are."
)

# The one clause the unassigned search turns round. Anything else in the team
# JQL is kept as it is, so the unassigned count reads the same slice of the
# tracker the per-person counts do.
_ASSIGNED_CLAUSE = re.compile(r"\bassignee\s+is\s+not\s+empty\b", re.IGNORECASE)
_ORDER_BY = re.compile(r"\border\s+by\b", re.IGNORECASE)


def _estimate(raw) -> float | None:
    value = _number(raw)
    return value if value is not None and value > 0 else None


def _oldest_key(row: dict) -> tuple:
    return (row["due_date"], str(row["issue_key"] or ""))


def team_load(issues: list[dict], today: date) -> dict:
    """PURE: per-assignee counts over search rows (see the module doc).

    `issues` are the Jira client's search rows (issue_key, summary, assignee,
    status_category, due_date, estimate_hours). People are ordered by open
    issues, most first, then by name.
    """
    week, fortnight = today + timedelta(days=7), today + timedelta(days=14)
    people: dict[str, dict] = {}
    done_in_read = unassigned_in_read = 0
    for issue in issues:
        if issue.get("status_category") == "done":
            done_in_read += 1
            continue
        who = issue.get("assignee")
        if not isinstance(who, str) or not who.strip():
            unassigned_in_read += 1
            continue
        p = people.setdefault(who, {
            "assignee": who, "open": 0, "in_progress": 0, "overdue": 0,
            "due_next_7": 0, "due_next_14": 0, "estimate_hours": 0.0,
            "estimated": 0, "unestimated": 0, "_overdue_rows": [],
        })
        p["open"] += 1
        if issue.get("status_category") == "indeterminate":
            p["in_progress"] += 1
        due = _due(issue.get("due_date"))
        if due is not None:
            if due < today:
                p["overdue"] += 1
                p["_overdue_rows"].append({
                    "issue_key": issue.get("issue_key"),
                    "summary": _clip(issue.get("summary"), SUMMARY_CHARS),
                    "due_date": due.isoformat(),
                    "days_overdue": (today - due).days,
                })
            else:
                if due <= week:
                    p["due_next_7"] += 1
                if due <= fortnight:
                    p["due_next_14"] += 1
        estimate = _estimate(issue.get("estimate_hours"))
        if estimate is None:
            p["unestimated"] += 1
        else:
            p["estimated"] += 1
            p["estimate_hours"] += estimate

    rows = []
    for p in people.values():
        overdue_rows = p.pop("_overdue_rows")
        p["estimate_hours"] = round(p["estimate_hours"], 2)
        p["oldest_overdue"] = min(overdue_rows, key=_oldest_key) if overdue_rows else None
        rows.append(p)
    rows.sort(key=lambda p: (-p["open"], p["assignee"]))

    def total(name: str):
        return sum(p[name] for p in rows)

    return {
        "people": rows,
        "totals": {
            "people": len(rows),
            "open": total("open"),
            "in_progress": total("in_progress"),
            "overdue": total("overdue"),
            "due_next_7": total("due_next_7"),
            "due_next_14": total("due_next_14"),
            "estimate_hours": round(sum(p["estimate_hours"] for p in rows), 2),
            "estimated": total("estimated"),
            "unestimated": total("unestimated"),
            "unassigned_in_read": unassigned_in_read,
            "done_in_read": done_in_read,
        },
    }


def unassigned_jql(team_jql: str) -> tuple[str | None, str | None]:
    """PURE: the team JQL with its one `assignee is not EMPTY` clause turned to
    `assignee is EMPTY`, or (None, the reason it cannot be turned round)."""
    if _ORDER_BY.search(team_jql):
        return None, "plan_team_jql has an ORDER BY"
    found = _ASSIGNED_CLAUSE.findall(team_jql)
    if len(found) != 1:
        return None, ("plan_team_jql has no single `assignee is not EMPTY` "
                      "clause to turn round")
    return _ASSIGNED_CLAUSE.sub("assignee is EMPTY", team_jql), None


async def load() -> dict:
    """The team's load, for the `jira_team_load` tool. Raises on an outage."""
    if not jira.configured():
        return {"available": False, "reason": "not configured"}
    jql = settings.plan_team_jql
    limit = max(1, int(settings.plan_team_limit))
    out = await jira.search_issues(jql, limit=limit)
    today = _today()
    result = team_load(out["issues"], today)

    unassigned_query, why_not = unassigned_jql(jql)
    if unassigned_query is not None:
        found = await jira.search_issues(unassigned_query, limit=limit)
        unassigned = {"count": len(found["issues"]),
                      "truncated": bool(found.get("truncated")),
                      "asked": unassigned_query}
    else:
        unassigned = None
    return {
        "available": True,
        "today": today.isoformat(),
        "asked": jql,
        "limit": limit,
        "truncated": bool(out.get("truncated")),
        **result,
        **({"unassigned": unassigned} if unassigned is not None
           else {"unassigned_omitted": why_not}),
        "cannot_show": CANNOT_SHOW,
    }


# --- the operator's delegated work (D2's `delegated` block) --------------------


def _updated_day(raw, tz: ZoneInfo) -> date | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        when = datetime.fromisoformat(raw.strip())
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return when.astimezone(tz).date()


def delegated_rows(issues: list[dict], today: date, tz: ZoneInfo) -> list[dict]:
    """PURE: each issue the operator waits on, with its assignee, due date,
    status and days since its last update (in the operator's time zone),
    ordered by due date (so overdue first, then soonest due; none last), then
    issue key."""
    rows = []
    for issue in issues:
        due = _due(issue.get("due_date"))
        updated = _updated_day(issue.get("updated"), tz)
        overdue = due is not None and due < today
        rows.append({
            "issue_key": issue.get("issue_key"),
            "summary": _clip(issue.get("summary"), SUMMARY_CHARS),
            "assignee": issue.get("assignee"),
            "status": issue.get("status"),
            "due_date": due.isoformat() if due else None,
            "overdue": overdue,
            **({"days_overdue": (today - due).days} if overdue else {}),
            "days_since_update": (today - updated).days if updated else None,
            **({"url": issue["url"]} if issue.get("url") else {}),
        })
    rows.sort(key=lambda r: (r["due_date"] is None, r["due_date"] or "",
                             str(r["issue_key"] or "")))
    return rows


async def delegated() -> dict:
    """The `delegated` block for the EA's brief. `available: false` with a
    `reason` when the tracker is not configured or the setting is empty (the
    caller omits the block); `count: 0` when there is nothing delegated (the
    caller omits it too). Raises on an outage."""
    if not jira.configured():
        return {"available": False, "reason": "not configured"}
    jql = (settings.plan_delegated_jql or "").strip()
    if not jql:
        return {"available": False, "reason": "disabled"}
    out = await jira.search_issues(jql, limit=max(1, int(settings.plan_backlog_limit)))
    today = _today()
    rows = delegated_rows(out["issues"], today, ZoneInfo(settings.ea_timezone))
    shown = rows[:max(0, settings.plan_brief_items)]
    return {
        "available": True,
        "count": len(rows),
        "overdue": sum(1 for r in rows if r["overdue"]),
        "truncated": bool(out.get("truncated")),
        "items": shown,
        "items_omitted": len(rows) - len(shown),
    }
