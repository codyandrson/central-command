> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Governance

> Manage team access with RBAC, limit agent access to context with ABAC policies, and review activity with audit and API logs.

Governance in Zep lives in the substrate, not bolted on. Authorization and audit apply across every Context Graph, every query, and every layer of the Context Lake — so policy holds as you scale to thousands of agents, users, and context sources.

Zep separates two problems:

* **Who can manage the account and projects** — teammates in the dashboard. Solved with [managing team access](/role-based-access-control) (role-based access control, RBAC), and, for how those teammates authenticate, [enterprise SSO](/enterprise-sso).
* **What context each agent and Memory MCP user can reach** — agents and other callers. Solved with [policy-based access control](/policy-based-access-control) (attribute-based access control, ABAC), applied to API keys for [agent access](/attribute-based-access-control) and to UserGroups for [UserGroup access](/usergroup-access).

Use RBAC for humans. Use policies when you need least-privilege access to context for agents and Memory MCP users. Encryption, compliance certifications, and deployment trust boundaries live under [Security & Compliance](/security-compliance).

## Access and policy

#### [Managing team access](/role-based-access-control)

Grant dashboard permissions with account- and project-scoped roles (RBAC).

#### [Enterprise SSO](/enterprise-sso)

Make your identity provider the source of truth for member sign-in.

#### [Policy-based access control](/policy-based-access-control)

Limit which actions and context each agent and Memory MCP user can reach with ABAC policies attached to API keys and UserGroups.

## Visibility

#### [Audit logging](/audit-logging)

Track dashboard member actions including logins, member management, API key changes, and data operations.

#### [API logging](/api-logging)

Monitor SDK and API requests, including outcomes from agent access.

## Related

* [Episode metadata projection](/episode-metadata-projection) — metadata attached at ingestion is what source-based policies evaluate.
* [Security & Compliance](/security-compliance) — SOC 2, HIPAA, BYOK, BYOM, and deployment models.