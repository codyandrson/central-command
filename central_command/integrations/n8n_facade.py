"""Client for the n8n Jira tool façade.

The façade is a token-authenticated n8n webhook that wraps the credential-holding
`lib-jira` workflow. Jira credentials live only inside n8n — Central Command never sees
them. Read operations back agent evidence; write operations are called by the
Executor *after* approval (never handed to a thinker agent).

Contract (from lib-jira):
  getIssue(issue_key)
  searchIssues(jql, limit?)
  updateLabels(issue_key, labels)
  createIssue(summary, issue_type, description?, labels?, project_key?)
  addComment(issue_key, body)
  setDueDate(issue_key, due_date="YYYY-MM-DD")
  updateAttributes(issue_key, priority, due_date=None|"YYYY-MM-DD", labels)
  getTransitions(issue_key)
  transitionIssue(issue_key, transition=<name>)
  linkIssues(from_key, to_key, link_type=Blocks|Relates|Duplicate)
    — direction: from_key is the OUTWARD side (from BLOCKS to)
"""

from __future__ import annotations

import httpx

from central_command.config import settings

# Operations that mutate Jira — only the Executor may call these, post-approval.
WRITE_OPS = {"updateLabels", "createIssue", "addComment", "setDueDate",
             "updateAttributes", "linkIssues", "transitionIssue"}
READ_OPS = {"getIssue", "searchIssues", "getTransitions"}


class JiraFacadeError(Exception):
    pass


async def call_jira(operation: str, **params) -> dict:
    """Call the façade. Returns lib-jira's normalized envelope; raises on failure."""
    payload = {"operation": operation, **params}
    async with httpx.AsyncClient(timeout=45) as client:
        resp = await client.post(
            settings.n8n_jira_url,
            headers={"x-cc-token": settings.jira_facade_token},
            json=payload,
        )
    if resp.status_code != 200:
        raise JiraFacadeError(
            f"jira façade {operation} failed: HTTP {resp.status_code} {resp.text[:200]}"
        )
    data = resp.json()
    if not data.get("ok", True):
        raise JiraFacadeError(f"jira façade {operation} error: {data}")
    return data


# --- read helpers (safe; back agent evidence) -------------------------------

async def get_issue(issue_key: str) -> dict:
    return await call_jira("getIssue", issue_key=issue_key)


async def search_issues(jql: str, limit: int | None = None) -> dict:
    params: dict = {"jql": jql}
    if limit is not None:
        params["limit"] = limit
    return await call_jira("searchIssues", **params)


async def get_transitions(issue_key: str) -> dict:
    return await call_jira("getTransitions", issue_key=issue_key)


# --- write helpers (Executor only, post-approval) ---------------------------

async def set_due_date(issue_key: str, due_date: str) -> dict:
    return await call_jira("setDueDate", issue_key=issue_key, due_date=due_date)


async def create_issue(
    project_key: str, summary: str, description: str | None = None,
    issue_type: str = "Task", due_date: str | None = None,
    labels: list[str] | None = None, parent: str | None = None,
    custom_fields: dict | None = None,
) -> dict:
    if parent is not None:
        raise JiraFacadeError(
            "createIssue — 'parent' (epic/issue hierarchy) cannot be expressed "
            "through the n8n façade; native Jira credentials are required "
            "(CC_JIRA_EMAIL/CC_JIRA_API_TOKEN)"
        )
    if custom_fields is not None:
        raise JiraFacadeError(
            "createIssue — 'custom_fields' cannot be expressed through the "
            "n8n façade; native Jira credentials are required "
            "(CC_JIRA_EMAIL/CC_JIRA_API_TOKEN)"
        )
    return await call_jira(
        "createIssue", project_key=project_key, summary=summary,
        description=description, issue_type=issue_type, due_date=due_date,
        labels=labels or [],
    )


async def add_comment(issue_key: str, body: str) -> dict:
    return await call_jira("addComment", issue_key=issue_key, body=body)


async def update_attributes(
    issue_key: str, priority: str, due_date: str | None, labels: list[str]
) -> dict:
    return await call_jira(
        "updateAttributes",
        issue_key=issue_key,
        priority=priority,
        due_date=due_date,
        labels=labels,
    )


async def link_issues(from_key: str, to_key: str, link_type: str) -> dict:
    """Typed link; from_key is the OUTWARD side (for Blocks: from BLOCKS to)."""
    return await call_jira(
        "linkIssues", from_key=from_key, to_key=to_key, link_type=link_type
    )


async def transition_issue(issue_key: str, transition: str) -> dict:
    """Move an issue by transition NAME — the lib resolves it against the
    issue's available transitions and refuses unavailable ones."""
    return await call_jira("transitionIssue", issue_key=issue_key, transition=transition)
