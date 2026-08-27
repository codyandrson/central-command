> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Audit logging

Available to [Enterprise Plan](https://www.getzep.com/pricing) customers only.

Audit logging records actions performed by team members through the Zep web dashboard. This feature helps enterprises meet compliance requirements, investigate security incidents, and maintain visibility into team activity.

Audit logs track member actions in the web application only. API requests and SDK calls made by your applications are not included in audit logs. For API activity, see [API logging](/api-logging).

## What events are logged

Zep automatically captures events across several categories:

| Category            | Events                                                                   |
| ------------------- | ------------------------------------------------------------------------ |
| **Authentication**  | Login, logout, session created, failed login attempts                    |
| **Members**         | Invitations sent, members joined, members removed, role changes          |
| **API Keys**        | Keys created, revoked, or updated                                        |
| **Projects**        | Projects created, updated, or deleted                                    |
| **Access Control**  | RBAC bindings created or deleted, permission denied events               |
| **Data Operations** | Threads, users, and graphs created, deleted, or viewed via the dashboard |
| **Settings**        | Account or project settings updated                                      |

## Viewing audit logs

Access audit logs from your account settings:

1. Navigate to **Account Settings** in the Zep dashboard
2. Select **Audit Logs** from the sidebar
3. Browse the chronological list of events

Each log entry displays:

* **Timestamp** - When the event occurred (with relative and absolute time)
* **Actor** - The team member who performed the action
* **Action** - The type of event that occurred
* **Resource** - The affected resource (project, user, thread, etc.)

Click any row to expand it and view additional details including request ID, IP address, and user agent.

## Filtering and searching

Use the filter controls to narrow down audit logs:

* **Time range** - Filter by predefined ranges (1 hour, 24 hours, 7 days, 30 days) or set a custom range
* **Actor** - Filter by the team member who performed the action
* **Action** - Filter by specific event types
* **Resource type** - Filter by the type of resource affected

Filters can be combined to locate specific events. The results update automatically as you apply filters.

## Event details

Each audit event captures:

| Field            | Description                                                |
| ---------------- | ---------------------------------------------------------- |
| `timestamp`      | When the event occurred (millisecond precision)            |
| `actor_email`    | Email of the user who performed the action                 |
| `actor_role`     | Role of the actor at the time of the event                 |
| `principal_type` | Type of actor (typically `member` for team member actions) |
| `action`         | The specific action performed                              |
| `resource_type`  | Type of resource affected                                  |
| `resource_id`    | Unique identifier of the affected resource                 |
| `project_uuid`   | Project context (if applicable)                            |
| `request_id`     | Unique identifier for the request                          |
| `client_ip`      | IP address of the client                                   |
| `user_agent`     | Client user agent string                                   |

## Data retention

Audit logs are retained according to the following schedule:

* **Dashboard access**: 30 days of audit logs are available for viewing and filtering in the web dashboard
* **Cold storage (Enterprise)**: 1 year of audit logs are retained in cold storage for all Enterprise customers
* **Extended retention (HIPAA BAA)**: 7 years of audit logs are retained for Enterprise customers who have signed a HIPAA Business Associate Agreement

Events are available immediately after they occur with no delay.