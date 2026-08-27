> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Connecting a client

This page is for end users connecting an MCP client to their own memory. If you administer the project, see [Configuring authentication](/memory-mcp-server/authentication) first.

## What you need

* An MCP client that supports remote servers over HTTP and OAuth — Claude, ChatGPT, Claude Code, Codex, Cursor, and others. For Claude, Claude Cowork, ChatGPT, Claude Code, Codex, and Cursor, use the [Zep Memory plugin](/use-zep-in-claude-chatgpt) (skill plus MCP) rather than wiring the endpoint alone.

* The universal Zep MCP endpoint:

  ```
  https://api.getzep.com/mcp
  ```

  The host above is for Zep's managed cloud. BYOC deployments use the same `/mcp` path on their own API host.

* An account with the identity provider your organization uses. You sign in with the same credentials you use elsewhere; Zep never sees your password.

## Connect

The endpoint URL stays the same across Zep projects. Zep uses your work email to find your organization's identity provider, then determines which projects your verified identity can access.

#### Add the server to your client

Install the Zep Memory plugin. If you are using another MCP client, add the endpoint URL as a remote MCP server.

#### Claude

Install the **Zep Memory** plugin from the organization marketplace so you get the skill and the Memory MCP connector together. Then open **Zep Memory** plugin settings, open the **Connectors** tab, and **Connect** the Zep Memory MCP connector. See [Use Zep in Claude / ChatGPT / Cursor](/use-zep-in-claude-chatgpt#provision-on-claude).

#### ChatGPT

Install the **Zep Memory** plugin from the workspace catalog after an administrator imports it. See [Use Zep in Claude / ChatGPT / Cursor](/use-zep-in-claude-chatgpt#provision-on-chatgpt).

#### Claude Code

After an administrator adds Zep Memory to shared Claude Code settings, trust the project folder if the marketplace is in `.claude/settings.json`. Then install **Zep Memory** once from `/plugin`, or run:

```bash
claude plugin install zep-memory@zep-memory
```

On BYOC, the shared settings point at the edited plugin copy your administrator prepared, not the public Zep repository.

Start a **new** Claude Code session. The first Memory MCP call opens Zep OAuth.

See [Use Zep in Claude / ChatGPT / Cursor](/use-zep-in-claude-chatgpt#claude-code).

#### Codex

These commands are for the Codex CLI. After an administrator shares them, run them once from your terminal (not inside a Codex session):

```bash
codex plugin marketplace add getzep/zep-memory-plugin
codex plugin add zep-memory@zep-memory
```

On BYOC, add the edited plugin copy your administrator prepared, not the public Zep repository.

Start a **new** Codex session. The first Memory MCP call opens Zep OAuth.

If you use Codex in the ChatGPT desktop app, install **Zep Memory** from the workspace catalog instead. The Codex IDE extension does not support plugins.

See [Use Zep in Claude / ChatGPT / Cursor](/use-zep-in-claude-chatgpt#codex).

#### Cursor

Requires Cursor 3.15.6 or later. After an administrator adds a team marketplace, open **Customize**. If the plugin is **Required** or **Default On**, confirm **Zep Memory** is already present. If it is **Default Off**, install it so you get the skill and the Memory MCP connector together.

Start a **new** chat. The first Memory MCP call opens Zep OAuth.

See [Use Zep in Claude / ChatGPT / Cursor](/use-zep-in-claude-chatgpt#cursor).

#### Other clients

Add a remote MCP server (Streamable HTTP transport) and paste the endpoint URL. Consult your client's documentation for where remote servers are configured.

#### Enter your work email

The client opens a Zep page. Enter your work email so Zep can find your organization's identity provider. Your work email domain must already be set up for MCP, through Google Workspace or your organization's Custom OIDC configuration.

#### Sign in

Zep opens your organization's sign-in page. Authenticate as you normally would.

#### Select a project when prompted

If your identity can access more than one project, choose the account and project to connect. Zep selects it automatically when only one project is available and refuses the connection when none are available.

#### Approve the connection

Zep shows a consent screen naming the client and the access it requests: reading your memory, and writing to it if your administrator allows writes. Approve to finish connecting.

After sign-in, the client lists Zep's tools and can work with your memory. Sessions are short-lived and renew automatically while your access remains valid.

The selected project is fixed in the signed token. To use another project, reconnect and choose it during sign-in. A client cannot switch the project attached to an existing token.

## What you can do

Your client always has access to **your own memory** in this project. The user-graph tools operate on your graph implicitly. You never pass a user or project identifier, and you cannot select anyone else's user graph or another project.

Read access is always available:

* Search your memory for context relevant to a query: a ready-to-use context block by default, or raw observations, thread summaries, episodes, entities, or facts.
* Get a narrative summary of who you are, drawn from your memory.
* Explore how your memory connects: get an entity's direct neighbors with `get_node_neighbors`, or a bounded multi-hop neighborhood with `get_subgraph`. Search with `scope=nodes` first to find the entity UUIDs these tools need.
* List your raw ingested episodes, most recent first, with `list_episodes`.

If your administrator has enabled standalone graph access, you can also:

* Read `zep://graphs/directory` for a paginated overview of the other graphs you
  can access.
* List the other graphs you can access with `list_graphs`.
* Search one by passing its `graph_id` to `search_graph_in`.
* Explore or list one with `get_subgraph_in`, `get_node_neighbors_in`, and `list_episodes_in`.
* Add information to one with `add_memory_to_graph` when writes are enabled.

[Graph directory](/graph-directory) describes how directory listing and metadata
search relate to the SDKs and to content search.

Which standalone graphs appear depends on the connection's
[authorization mode](/memory-mcp-server/standalone-graph-authorization).
Project-wide mode exposes every standalone graph in the selected project.
UserGroup ABAC mode exposes only graphs granted to your effective UserGroups.
An unauthorized graph is indistinguishable from one that does not exist.

Write access is available when your administrator has enabled writes for the connection (the default). When it is enabled, you can add new memory as text, JSON, or a message. If writes are disabled, the write tool does not appear, and a write attempt is refused.

## Troubleshooting

* **Sign-in does not start or the client reports no authorization server.** Confirm that the client uses the `/mcp` URL for your Zep deployment.
* **Zep cannot find your identity provider.** Confirm that you entered your work email. Ask your administrator whether Google Workspace is connected for your domain, or whether your domain is assigned on the account's Custom OIDC configuration.
* **No projects are available after sign-in.** Your verified identity is not eligible for any enabled MCP connection. Ask your administrator to check the connection's admission gates, user-mapping, and provisioning settings.
* **You are signed in but cannot connect.** Your identity may not be admitted: your email domain or group may be outside the connection's allowed list, or your user may not exist yet and just-in-time provisioning is off. Ask your administrator to admit you or provision your account.
* **Write tools are missing.** The connection is read-only, or the account-level writes kill switch is engaged. In UserGroup ABAC mode, your UserGroups must also grant the selected graph's write action.
* **Standalone graph tools are missing.** **Allow standalone graphs** is off on the project connection.
* **The connection stops working after a while.** Tokens are short-lived and renew automatically up to the connection's grant maximum lifetime. If renewal fails, the grant has reached that ceiling or your administrator disabled the connection; reconnect to sign in again.