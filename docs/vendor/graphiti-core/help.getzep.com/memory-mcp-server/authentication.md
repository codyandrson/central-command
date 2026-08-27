> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Configuring authentication

The Memory MCP Server uses per-account **MCP seats**; seat counts vary by plan. Custom OIDC requires the [Enterprise plan](https://www.getzep.com/pricing) on Zep managed cloud. Google Workspace sign-in does not.

This page is for administrators. It covers connecting a project's Memory MCP Server so end users can sign in and reach their own memory. For the end-user side, see [Connecting a client](/memory-mcp-server/connect).

## Before you start

* On Zep managed cloud, the account needs **MCP seats**. Provision enough for everyone who will connect, including future hires. Seat counts vary by plan. With zero seats, **Settings ▸ MCP** cannot create or enable a connection, and client OAuth (Claude, ChatGPT, Claude Code, Codex, Cursor) will fail. When seats are exhausted, **new** users cannot connect (403); people who already hold a seat keep working.
* In BYOC, the **MCP Server** deployment parameter is enabled by default and can be disabled by an operator.
* You need the `mcp.connection.manage` capability on the project. This is a dedicated capability, separate from member OIDC configuration. See [Managing team access](/role-based-access-control).
* Each project has one active connection, which is one authentication method.

Zep sends users to your identity provider to log in; it never stores end-user credentials. Clients use one universal MCP endpoint for every project; Zep discovers the provider from the user's work email and selects the project after sign-in.

## Choose an authentication method

| Method           | Who can use it                                                                                                                             | How users sign in                                                                                                                                                                                                                                                                                          |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Google Workspace | Zep managed cloud accounts with the Memory MCP Server enabled, when the work domain is **not** already assigned to an Enterprise SSO realm | Google Workspace. Zep derives and verifies the Workspace domain during administrator setup; you do not enter a domain list. Not available in BYOC, while customer-managed encryption keys (BYOK) are active on the account, or when Enterprise SSO already owns the domain in the account domain registry. |
| Custom OIDC      | Enterprise                                                                                                                                 | Your own OIDC-compatible identity provider (Entra ID, Okta, and others). Requires [enterprise SSO](/enterprise-sso) enabled on the account with at least one approved email domain. Required when Enterprise SSO already owns the work domain, and in BYOC / BYOK.                                         |

Google Workspace is the default on Zep managed cloud when the domain is free for Workspace MCP. Choose Custom OIDC when you need a customer-owned IdP, when Enterprise SSO already assigns the domain, and in BYOC, where Custom OIDC is the only option.

## Configure Google Workspace

You configure the connection in the Zep Dashboard under your project's **Settings ▸ MCP** page.

A Google Workspace domain can be claimed by only one Zep account. Projects in that account may share the domain; after sign-in, the user chooses the project when more than one is eligible.

#### Start Google Workspace setup

On **Settings ▸ MCP**, choose **Connect Google Workspace**. Decide whether to create users on first sign-in, whether to allow writes (on by default), and whether to allow standalone graph access (on by default) before you authorize.

#### Authorize with Google

Complete the Google Workspace sign-in. Zep captures the signed hosted domain from Google and finishes the connection automatically, with no second confirmation screen. The connection is created enabled, so it starts accepting sign-ins immediately. An unfinished setup resumes after a page reload until it expires or you restart it.

#### Set access controls

After the connection exists, toggle just-in-time provisioning, allow writes, allow standalone graphs, grant maximum lifetime, and whether the connection is enabled. Enabling just-in-time provisioning claims your Workspace domain exclusively for this account. See [Provisioning new users](#provisioning-new-users).

#### Share the endpoint

The page displays the universal MCP endpoint. Share it with end users; they enter their work email when connecting. Disabled connections are excluded from discovery and project selection.

## Configure Custom OIDC

Custom OIDC is available on the Enterprise plan. The account must already have [enterprise SSO](/enterprise-sso) enabled, with at least one approved email domain. Otherwise the connection is undiscoverable. An Account Owner sets that up from **Account ▸ Settings ▸ Enterprise SSO**. Zep approves the domains it uses, so allow time for that before you configure this connection.

Register a confidential OIDC application in your identity provider for the Memory MCP Server surface. Its client ID and secret are distinct from any dashboard SSO application. You will need the client ID, client secret, and Zep's OAuth callback URL from the MCP settings page.

#### Create an OIDC application in your IdP

Register a confidential OIDC client. Set its redirect URI to Zep's OAuth callback, shown on the MCP settings page. Note the client ID and client secret.

#### Create the connection in Zep

On **Settings ▸ MCP**, choose **Custom OIDC** and enter that application's client ID and client secret into Zep. The secret is stored securely and is never displayed again. Issuer URL and signing keys come from the account's identity provider configuration; you do not enter them on this page.

#### Set identity mapping and admission gates

Choose the claim that maps to a Zep user, set allowed email domains to a subset of the domains already assigned to your account, and configure any group restrictions. See the field reference below.

#### Decide on writes, grant lifetime, and provisioning

Leave writes and standalone graph access enabled (the defaults) or restrict either capability, set the grant maximum lifetime, and decide whether new users are provisioned automatically. See [Writes](#writes), [Standalone graph access](#standalone-graph-access), [Grant lifetime](#grant-lifetime), and [Provisioning new users](#provisioning-new-users).

#### Enable the connection

The connection is created disabled. Verify the settings, then enable it. The page displays the universal MCP endpoint. Share it with end users; they enter their work email when connecting. Disabled connections are excluded from discovery and project selection.

## Connection settings

| Setting                         | Applies to       | Description                                                                                                                                                                                                                                                                                                 |
| ------------------------------- | ---------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Workspace domain                | Google Workspace | Captured from Google during setup. Not customer-editable.                                                                                                                                                                                                                                                   |
| Client ID and client secret     | Custom OIDC      | Credentials for the OIDC application you registered for MCP. The secret is write-only; Zep never displays it again.                                                                                                                                                                                         |
| User ID claim                   | Custom OIDC      | The claim mapped to the Zep user. Defaults to `sub`. Confirm your chosen claim is a stable identifier for each user, and set it before users connect. Changing it later breaks the mapping, and existing users return as new ones.                                                                          |
| Email claim                     | Custom OIDC      | Optional claim used to populate the user's email.                                                                                                                                                                                                                                                           |
| Allowed domains                 | Custom OIDC      | Narrows sign-in to a subset of the email domains already assigned to your account's identity provider. It does not choose which provider Zep routes to. Required; values outside the account's assigned domains are rejected.                                                                               |
| Groups claim and allowed groups | Custom OIDC      | Optional claim and list restricting access to members of specific groups.                                                                                                                                                                                                                                   |
| Just-in-time provisioning       | Both             | Whether a new user is created on first sign-in. Off by default for Custom OIDC. On by default in the Google Workspace setup form; you can turn it off before or after connecting.                                                                                                                           |
| Allow writes                    | Both             | Whether connected clients may write to memory. On by default.                                                                                                                                                                                                                                               |
| Allow standalone graphs         | Both             | Whether connected clients can list, search, or write to standalone graphs in the project. On by default. Turn it off to limit clients to each user's own graph.                                                                                                                                             |
| Standalone graph authorization  | Both             | `project_wide` makes every standalone graph in the project available. In Zep managed cloud, Enterprise accounts with policy-based access control can move a connection to default-deny `user_group_abac` after preflight. In BYOC, the same transition also requires the deployment UserGroup ABAC setting. |
| Access token lifetime           | Custom OIDC      | How long an issued access token stays valid, from 60 seconds to 24 hours. Defaults to about five minutes.                                                                                                                                                                                                   |
| Grant maximum lifetime          | Both             | Absolute ceiling on an OAuth grant lineage, independent of refreshes. Default 24 hours; selectable 1 hour, 24 hours, 7 days, or 30 days.                                                                                                                                                                    |

### How Zep finds the identity provider

Clients connect to one `/mcp` URL. Zep asks for the user's work email and uses its domain only to discover where to send them to sign in. Discovery failures are generic and do not reveal account or project names.

* **Custom OIDC**: the work-email domain must be assigned to your account's identity provider configuration. A connection's allowed domains list only narrows which of those assigned domains this project admits; it is never the discovery key.
* **Google Workspace**: Zep matches the domain against the Workspace hosted domain captured during administrator setup. Independent teams in the same Workspace can each run their own account unless just-in-time provisioning claims the domain exclusively (see below). If the domain is already **assigned** to any Enterprise SSO realm in the account domain registry, Google Workspace MCP connections for that domain are excluded from discovery — even when Workspace JIT is off. Member sign-in will not find the Workspace connection. Use Custom OIDC for MCP on domains Enterprise SSO already owns.

After the IdP validates the user, Zep recomputes every eligible enabled connection, applies that connection's admission gates, and either auto-selects the only eligible project, shows an account and project chooser, or denies access.

### Admission gates

Allowed domains (Custom OIDC) and allowed groups are **admission** gates checked at login against the signed claims from the identity provider. Group membership comes from the OIDC `groups` claim, which is only as fresh as the user's last login. Treat these gates as coarse admission control, not live revocation. To stop access immediately, revoke access or disable the connection (see [Revoking access](#revoking-access)).

Widening an admission gate (adding domains or groups, or clearing a domain restriction) requires an explicit re-confirmation step in the dashboard.

### Writes

A connection allows writes by default: connected clients can read memory and add to it with the `add_memory` tool. If standalone graph access is also enabled, it exposes `add_memory_to_graph` for standalone graphs in the project. Turning writes off hides those tools and makes the connection read-only, immediately and for clients already signed in.

Turning writes back on applies to new sign-ins. A client that authorized while the connection was read-only keeps the read-only permission it consented to, so its user must reconnect it to get the write tools.

An account-level **writes kill switch** overrides every connection's write setting. It is off by default; when you engage it for incident response, all writes are blocked regardless of per-connection configuration.

### Grant lifetime

Access tokens are short-lived and renew automatically, but renewal does not re-check the identity provider. **Grant maximum lifetime** caps how long a grant lineage can keep renewing before the user must sign in again. Tightening the setting applies to grants already outstanding. Use a shorter ceiling when you need suspensions at the IdP to take effect sooner on MCP clients.

### Standalone graph access

Standalone graph access is controlled by the project connection's **Allow standalone graphs** switch. The switch is on by default. Turning it off removes `list_graphs`, `search_graph_in`, `add_memory_to_graph`, and the `zep://graphs/directory` resource from user MCP clients while preserving access to each user's own graph.

In BYOC, an operator can disable the **MCP Standalone Graph Access** or **MCP Server** deployment parameter. The token's signed connection binding fixes the project, so a client cannot select another project in an MCP request.

Connections initially use project-wide authorization: every authenticated MCP
user can read every standalone graph in the token-bound project. In Zep managed
cloud, Enterprise accounts with
[policy-based access control](/policy-based-access-control) can instead use
[UserGroup ABAC](/memory-mcp-server/standalone-graph-authorization) to grant
specific graphs. In BYOC, that transition also requires the deployment
UserGroup ABAC setting. Accounts without ABAC keep project-wide reads; their
writes use the `graph:write` scope, **Allow writes**, and the account-level
writes kill switch without requiring an ABAC grant.

### Provisioning new users

Just-in-time provisioning is off by default on Custom OIDC. When it is off, only existing Zep users can connect, and an unknown user is turned away. When it is on, a user who signs in successfully and passes the admission gates is created on first connect. Provisioning is rate-limited per account. When the identity provider supplies given and family name claims, those names are stored on the new user.

For Google Workspace, the setup form turns provisioning on by default. Enabling it claims the Workspace domain exclusively for your account; other accounts are refused, and a domain already assigned to your account's own OIDC realm cannot be claimed by Workspace JIT either. Projects in the same account may share the domain. Turning provisioning off or disabling the connection does not release that claim; deleting the connection does, unless another project in the same account still has a just-in-time Google Workspace connection on that domain.

A domain already assigned to Enterprise SSO also blocks **discovery** of Google Workspace MCP connections for that domain, whether or not JIT is on. Setup with JIT off can still create a Workspace connection that members never reach at sign-in. Prefer Custom OIDC for MCP when Enterprise SSO already owns the domain.

Deleting a Zep user is a data-deletion operation, not MCP revocation. If an MCP identity still points at a deleted user, Zep recreates the user on the next sign-in or request (same user ID, new graph) after the deletion finishes. To stop MCP access, disable the connection. See [Revoking access](#revoking-access).

## Revoking access

* **Everyone on the connection:** disable the connection. New sign-ins are refused immediately, and tokens already issued stop working within a few minutes.
* **All writes:** engage the account-level writes kill switch.

Lower the connection's access token lifetime or grant maximum lifetime to shorten how long outstanding grants can linger after you disable the connection or after a user is suspended at the identity provider.

## Auditing

Every connection change (create, update, enable, disable, and delete) is recorded in an audit log with the member who made it and a before-and-after snapshot. Review it on the MCP settings page. Changes to writes, provisioning, and the admission gates are the ones to watch.