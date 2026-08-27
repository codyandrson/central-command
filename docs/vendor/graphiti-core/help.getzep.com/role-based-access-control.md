> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Managing team access

> Role-based access control (RBAC): grant account- and project-scoped dashboard permissions to teammates.

Available to [Enterprise Plan](https://www.getzep.com/pricing) customers only.

## Overview

Managing team access uses role-based access control (RBAC) to grant the right level of dashboard access to each teammate while keeping sensitive account actions limited to trusted users. RBAC grants permissions through roles, and every member can hold multiple assignments across the account and individual projects.

RBAC covers humans in the web application. To limit what agents and Memory MCP users can do with context, use [policy-based access control](/policy-based-access-control) (ABAC). Both live under [Governance](/governance).

## Scopes and authorizations

RBAC permissions are evaluated at two scopes:

* **Account scope:** Covers organization-wide settings such as member management, billing, and account-level API keys, along with full access to every project.
* **Project scope:** Grants permissions for a single project, including its data plane, collaborators, and project-specific API keys, without exposing other projects or global settings.

Authorizations are grouped into the following capability areas. These appear in the dashboard when you review role details.

* `account.view.readonly` — View account-level configuration, billing status, and usage.
* `rbac.account.manage` — Create, update, or delete account-scoped role assignments, including promoting additional Account Owners.
* `rbac.project.manage` — Manage project-scoped assignments and project-level resources (API keys, data ingestion, deletion) for the projects a member administers.
* `sso.manage` — Submit or rotate the account's enterprise SSO credentials: the identity provider issuer, client ID, and client secret. Granted to Account Owner only. These values decide which identity provider can issue sign-in tokens for the email domains assigned to your account, so the capability is separate from `account.admin` rather than bundled into it.

## Roles

The role catalog includes account-wide roles and project-scoped roles. Assignments can be combined so that, for example, a teammate can be an Account Admin and a Project Viewer on a sensitive project.

### Account-level roles

| Role                | Scope   | Intended for                          | Key authorizations                                                                                                                                                                                                                                                                                                                |
| ------------------- | ------- | ------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Account Owner**   | Account | Founders, security administrators     | `account.view.readonly`, `rbac.account.manage`, `rbac.project.manage`, `sso.manage`<br />Manage billing and plan settings.<br />Create, update, and archive projects.<br />Rotate account and project API keys.<br />Submit or rotate enterprise SSO credentials.<br />Assign or revoke any role, including other Account Owners. |
| **Account Admin**   | Account | Day-to-day operators who run projects | `account.view.readonly`, `rbac.project.manage`<br />Create and manage projects and API keys.<br />Ingest or delete context, documents, and graph data.<br />Assign and revoke project-scoped roles for any project.<br />Cannot remove the last Account Owner, change billing ownership, or submit enterprise SSO credentials.    |
| **Billing Admin**   | Account | Finance or procurement partners       | `billing.manage`<br />View invoices and update payment details.<br />No access to project data or member management.                                                                                                                                                                                                              |
| **Account Viewer**  | Account | Compliance and audit reviewers        | `account.view.readonly`<br />Read account metadata.<br />Requires a separate project role to access project data or API keys.                                                                                                                                                                                                     |
| **Project Creator** | Account | Builders who bootstrap new projects   | `project.create`<br />Create new projects from the dashboard.<br />No access to existing projects unless separately assigned.                                                                                                                                                                                                     |

### Project-level roles

| Role               | Scope   | Intended for                                    | Key authorizations                                                                                                                                                                                                               |
| ------------------ | ------- | ----------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Project Admin**  | Project | Team leads who manage a single project          | `member.invite` and `rbac.project.manage` for the assigned project only.<br />Add existing account members or invite new teammates to the project.<br />Create and rotate project API keys.<br />Ingest and delete project data. |
| **Project Editor** | Project | Data engineers or agents that need write access | Read and write all project data, including context, documents, and graph content.<br />Use project API keys to ingest or delete data.<br />Cannot assign roles or manage API keys.                                               |
| **Project Viewer** | Project | Analysts, auditors, or embedded stakeholders    | View project configuration, usage, threads, documents, and graph content.<br />Run read-only queries and exports.<br />Cannot ingest, delete, or manage API keys.                                                                |

## Managing role assignments

* Account Owners and Account Admins use **Account ▸ Members** to manage account membership and account-scoped roles.
* Project Admins use **Project ▸ Members** to add an existing account member or invite a new teammate. This path offers only project roles for the current project and never grants account-level access.
* Removing project access revokes the member's project role bindings. It does not remove their account membership or roles in other projects.
* Every member must have at least one Account Owner assigned. Attempts to delete the final Account Owner are rejected.
* The dashboard prevents duplicate assignments for the same member, scope, and project.
* Removing a role hides it from the active list but keeps the history available; you can restore access later by adding the role again.