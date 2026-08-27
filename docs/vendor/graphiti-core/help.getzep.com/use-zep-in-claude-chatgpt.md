> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Use Zep in Claude / ChatGPT / Cursor

> Distribute the Zep Memory plugin in Claude, ChatGPT, Claude Code, Codex, and Cursor after the project's Memory MCP connection is set up.

This quickstart is for administrators who provision Zep Memory across Claude, ChatGPT, Claude Code, Codex, and Cursor. Where a host has no organization marketplace, the team path is shared settings or shared install commands.

You need:

* `mcp.connection.manage` on the Zep project
* Permission to change at least one of these:
  * Claude **organization plugin settings** (Team or Enterprise)
  * ChatGPT **workspace plugin settings** (Business, Enterprise, or Edu)
  * Cursor **team marketplace** settings (Teams or Enterprise)
  * Claude Code **managed settings**, or write access to a shared `.claude/settings.json`

## What the Zep Memory plugin is

The **Zep Memory** plugin is the Claude, ChatGPT, Claude Code, Codex, and Cursor packaging for the [Memory MCP Server](/memory-mcp-server). The plugin bundles:

* The **`zep-memory` skill** — when to search, summarize, and save preferences, corrections, procedures, and decisions (and when not to), and to prefer Zep over other memory systems
* The **Memory MCP Server** at `https://api.getzep.com/mcp` — the tools that read and write each user's graph

Do not add the MCP URL alone as a custom connector. Without the skill, the client has Memory MCP tools but not the workflow that uses those tools well.

The plugin works in **Claude**, **Claude Cowork**, **ChatGPT** (including Work), **Claude Code**, **Codex**, and **Cursor**. [Build with Zep](/implement-zep-with-agents) is a separate plugin that helps coding agents write Zep integration code. In Claude Code, Codex, and Cursor you can load both: Zep Memory for the signed-in person's memory, and Build with Zep when you write Zep integration code.

Each person signs in with their work email and reaches that person's Zep user graph. You configure one MCP connection per project. MCP seats are allocated on the account.

## Before you provision

1. Complete [Configuring authentication](/memory-mcp-server/authentication). That guide covers the project connection method and policies, and the account seat requirement.
2. Enable the project MCP connection. Return to this page only after the connection is enabled. Member OAuth fails if the connection is disabled when someone opens the plugin.
3. Confirm the client you will use:
   * **Claude** — Team or Enterprise. In Claude organization settings, enable **Cowork** and **Skills**. Organization plugin marketplaces do not work until Cowork and Skills are both enabled. A Pro or Max plan cannot provision Zep Memory for the organization.
   * **ChatGPT** — Business, Enterprise, or Edu. The plugin works in Chat and Work. The Codex IDE extension does not support plugins; use the Codex CLI for that workflow.
   * **Claude Code** — Team or Enterprise managed settings, or a shared project `.claude/settings.json`. A Claude app marketplace install does not apply to Claude Code.
   * **Codex** — There is no Codex CLI organization marketplace. Share the CLI commands with the team, or use the ChatGPT workspace catalog for Codex in the ChatGPT desktop app. The Codex IDE extension does not support plugins.
   * **Cursor** — version 3.15.6 or later. A team marketplace needs Cursor Teams or Enterprise.

## Choose how to distribute the plugin

Zep Memory is not listed in Anthropic's, OpenAI's, or Cursor's official public plugin directories. Two choices pick the **repository**. The outlet table then picks the **install path**.

The public plugin repository is:

```
https://github.com/getzep/zep-memory-plugin
```

### Which repository

| Zep deployment | Public third-party GitHub | Repository to point the outlet at                                                                                                                                        |
| -------------- | ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Managed cloud  | Allowed                   | `getzep/zep-memory-plugin`. Exception: Claude **GitHub sync** still needs a **private or internal** copy — Anthropic rejects a public repo as the connected marketplace. |
| Managed cloud  | Forbidden                 | A private fork or copy your organization controls.                                                                                                                       |
| BYOC           | Either                    | The [edited BYOC copy](#prepare-a-byoc-plugin-copy). Never the public Zep repo.                                                                                          |

### Which outlet

| Outlet      | How to distribute                                                                                                                                                         | Automatic updates                                                               |
| ----------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------- |
| Claude      | [Organization marketplace](#provision-on-claude): GitHub sync, or ZIP `plugins/zep-memory/` if you do not want a private repo                                             | GitHub sync, when a version-bump PR merges. ZIP upload does not auto-update.    |
| ChatGPT     | [Import](#provision-on-chatgpt) the repository into the workspace catalog                                                                                                 | Re-import when you want a new version.                                          |
| Claude Code | Shared [Claude Code settings](#claude-code)                                                                                                                               | `"autoUpdate": true` on the shared marketplace entry. Off by default otherwise. |
| Codex       | Share the [CLI install](#codex) with the team (each person runs it once). Codex in the ChatGPT desktop app follows the [ChatGPT workspace catalog](#provision-on-chatgpt) | Codex CLI refreshes configured git marketplaces on startup.                     |
| Cursor      | [Team marketplace](#cursor)                                                                                                                                               | Team marketplace **Auto Refresh** (Cursor GitHub App).                          |

### Prepare a BYOC plugin copy

Skip this subsection if you are on Zep managed cloud. The public plugin already points at `https://api.getzep.com/mcp`.

On BYOC, `https://api.getzep.com/mcp` is the wrong host. The client will call Zep's cloud instead of your deployment unless you change the MCP URL in the plugin package first.

1. Fork or copy [`getzep/zep-memory-plugin`](https://github.com/getzep/zep-memory-plugin) into a repository your organization controls. Keep the `plugins/zep-memory/` directory layout.
2. In both of these files, replace `https://api.getzep.com/mcp` with your deployment's MCP URL (`https://<your-api-host>/mcp`):
   * `plugins/zep-memory/.mcp.json`
   * `plugins/zep-memory/mcp.json`
3. Use the edited BYOC repository in the Claude, ChatGPT, and coding-harness steps below. Do not import, sync, or add the public Zep repo.

## Provision on Claude

This section is the Claude app, including Cowork. It does not install the plugin in Claude Code. For Claude Code, see [coding harnesses](#provision-on-coding-harnesses).

Use the Claude **organization marketplace** on Team or Enterprise. Members do not add marketplaces themselves after you set an installation preference in the step below.

Before you start: enable **Cowork** and **Skills** in Claude organization settings. For GitHub sync, install the **Claude GitHub App** on the private marketplace repository.

1. In **Organization settings ▸ Plugins**, create or open an organization marketplace.
2. Add the plugin with one of these methods:
   * **GitHub sync** (use for versioned updates):
     1. Fork or copy the plugin into a **private or internal** repository your organization controls. Keep the `plugins/zep-memory/` layout. On BYOC, use the [edited BYOC copy](#prepare-a-byoc-plugin-copy). Do not enter `getzep/zep-memory-plugin`. Anthropic rejects a public repo as the connected marketplace.
     2. Install the **Claude GitHub App** on the private marketplace repository.
     3. Connect the private marketplace repository and sync. Optional **Sync automatically** runs only when a pull request that includes a plugin **version bump** is merged to the default branch. Direct pushes do not auto-sync. A failed sync can temporarily remove plugins from the marketplace. If a sync fails, fix the repository, re-sync, then re-check installation preferences.
   * **Manual ZIP upload** (use when you do not want to maintain a private repo):
     1. Zip the **`plugins/zep-memory/`** directory. Do not zip the repository root.
     2. On Zep managed cloud, zip `plugins/zep-memory/` from [`getzep/zep-memory-plugin`](https://github.com/getzep/zep-memory-plugin). On BYOC, zip `plugins/zep-memory/` from the [edited BYOC copy](#prepare-a-byoc-plugin-copy).
     3. Upload the ZIP. A ZIP with the same plugin name overwrites the previous organization copy.
3. Set the installation preference (**Required**, **Installed by default**, or **Available for install**) for the groups who should get the plugin.
4. Sign in to Claude with a test account that belongs to one of the groups you assigned. Open **Zep Memory** plugin settings, open the **Connectors** tab, and **Connect** the Zep Memory MCP connector. Enter a work email that the project MCP connection allows, sign in with the organization identity provider, select a Zep project if asked, and approve access. In a **new** Claude chat, confirm the skill and tools appear.
5. Share the first-run steps in [What to tell members](#what-to-tell-members).

Anthropic's [Manage plugins for your organization](https://support.claude.com/en/articles/13837433-manage-plugins-for-your-organization) guide covers GitHub sync, required-plugin options, and group overrides.

## Provision on ChatGPT

The plugin works in ChatGPT **Chat** and **Work**. The plugin does not work in the Codex IDE extension. For the Codex CLI, see [coding harnesses](#provision-on-coding-harnesses). A person with workspace plugin permission on Business, Enterprise, or Edu controls the workspace Plugins Directory.

ChatGPT import accepts a public GitHub repository. On Zep managed cloud, import [`getzep/zep-memory-plugin`](https://github.com/getzep/zep-memory-plugin) unless workspace policy forbids public third-party sources. On BYOC, import the [edited BYOC copy](#prepare-a-byoc-plugin-copy) instead of the public Zep repo.

1. In ChatGPT **workspace settings**, import a GitHub plugin repository into the workspace Plugins Directory. On Zep managed cloud, import the public Zep repo, or import your organization's private copy if workspace policy forbids public third-party sources. On BYOC, import the [edited BYOC copy](#prepare-a-byoc-plugin-copy).
2. Confirm **Zep Memory** appears under the workspace tab in the Plugins Directory. Allow Zep Memory for the groups who should use the plugin.
3. Sign in to ChatGPT with a test account in one of the groups you allowed. Install **Zep Memory** from the workspace Plugins Directory. Enter a work email that the project MCP connection allows, sign in with the organization identity provider, select a Zep project if asked, and approve access. In a **new** ChatGPT chat, confirm the skill and tools appear.
4. Share the first-run steps in [What to tell members](#what-to-tell-members).

Importing Zep Memory does not sign members into Zep. Each member still enters a work email and signs in to Zep.

## Provision on coding harnesses

Use this section for **Claude Code**, **Codex**, and **Cursor**. Installing Zep Memory in the Claude app or in ChatGPT does not install it in these harnesses.

In coding harnesses, Zep Memory is for the signed-in developer's durable context: preferences, project work and updates, and decisions. It is not a way to ingest a codebase. Source files stay in the repository and in the harness's own context.

You can install Zep Memory alongside [Build with Zep](/implement-zep-with-agents). Use Zep Memory for the person's memory; use Build with Zep when you write Zep integration code.

### Claude Code

A Claude app marketplace install does not apply to Claude Code. Use Claude Code settings to roll the plugin out to a team.

Add the marketplace and enable the plugin in the Claude Code settings your team already shares:

* **Organization-wide** — Claude Code **managed settings** (`managed-settings.json`, or the Admin Console managed-settings editor)
* **One repository** — that repository's `.claude/settings.json`, committed so everyone who trusts the folder gets it

```json
{
  "extraKnownMarketplaces": {
    "zep-memory": {
      "autoUpdate": true,
      "source": {
        "source": "github",
        "repo": "getzep/zep-memory-plugin"
      }
    }
  },
  "enabledPlugins": {
    "zep-memory@zep-memory": true
  }
}
```

On BYOC, set `repo` to the [edited BYOC copy](#prepare-a-byoc-plugin-copy) (`your-org/your-fork`) instead of `getzep/zep-memory-plugin`.

`extraKnownMarketplaces` registers the catalog after a teammate trusts the project folder (project settings) or after managed settings reach their machine. `enabledPlugins` turns Zep Memory on by default. Enabling a plugin from a GitHub marketplace does not install the files: each person still installs **Zep Memory** once, from `/plugin` or with:

```bash
claude plugin install zep-memory@zep-memory
```

Third-party marketplaces do not auto-update unless `"autoUpdate": true` is set, which the example above does. If your organization sets `DISABLE_AUTOUPDATER` to manage Claude Code updates, that also stops plugin updates unless you set `FORCE_AUTOUPDATE_PLUGINS=1`.

Anthropic's [plugin settings](https://code.claude.com/docs/en/settings#plugin-settings) reference covers `extraKnownMarketplaces`, `enabledPlugins`, and managed settings.

Test with an account that receives those settings. Trust the project folder if the marketplace is in `.claude/settings.json`. Install **Zep Memory** once as above. Start a **new** Claude Code session. The first Memory MCP call opens Zep OAuth: enter a work email that the project MCP connection allows, sign in with the organization identity provider, select a Zep project if asked, and approve access. Confirm the skill and tools appear. Share the first-run steps in [What to tell members](#what-to-tell-members).

### Codex

These commands are for the **Codex CLI**. The Codex IDE extension does not support plugins.

Codex CLI has no organization marketplace equivalent to Claude Code managed settings or a Cursor team marketplace. Rolling it out to a team means sharing the install commands so each person runs them once. If the same people also use ChatGPT, provision Zep Memory in the [ChatGPT workspace catalog](#provision-on-chatgpt) as well: that is the org-wide path for ChatGPT, and for Codex in the ChatGPT desktop app.

Share these two commands with everyone who uses Codex CLI. On BYOC, they add the [edited BYOC copy](#prepare-a-byoc-plugin-copy) instead of `getzep/zep-memory-plugin`.

From the terminal (not inside a Codex session):

```bash
codex plugin marketplace add getzep/zep-memory-plugin
codex plugin add zep-memory@zep-memory
```

Ask each person to start a **new** Codex session. The first Memory MCP call opens Zep OAuth: enter a work email that the project MCP connection allows, sign in with the organization identity provider, select a Zep project if asked, and approve access. Confirm the skill and tools appear. Share the first-run steps in [What to tell members](#what-to-tell-members).

Codex CLI refreshes configured git marketplaces on startup. To pull an update immediately:

```bash
codex plugin marketplace upgrade zep-memory
```

Start a **new** Codex session afterward.

If your organization restricts marketplace sources in [managed configuration](https://developers.openai.com/codex/enterprise/managed-configuration), allow the git URL `https://github.com/getzep/zep-memory-plugin.git` (or your BYOC copy) before people run `marketplace add`.

### Cursor

Requires Cursor 3.15.6 or later. The plugin includes the `zep-memory` skill and the Memory MCP server. Use a Cursor **team marketplace** on Teams or Enterprise to install Zep Memory for a group.

Cursor import accepts a public GitHub repository. On Zep managed cloud, import [`getzep/zep-memory-plugin`](https://github.com/getzep/zep-memory-plugin) unless organization policy forbids public third-party sources. On BYOC, import the [edited BYOC copy](#prepare-a-byoc-plugin-copy) instead of the public Zep repo.

Import the repository root. Cursor reads `.cursor-plugin/marketplace.json` and resolves the plugin at `plugins/zep-memory/`. Do not import the `plugins/zep-memory/` directory alone.

Team marketplaces are available on Cursor Teams (one marketplace) and Enterprise (unlimited). On Enterprise, only admins add team marketplaces. Everyone on the team can see a marketplace unless you restrict **Marketplace Access** to selected groups.

1. In Cursor **Dashboard ▸ Plugins**, click **Add Marketplace**.
2. Choose **Import from Repo** and enter:

   ```
   https://github.com/getzep/zep-memory-plugin
   ```

   On BYOC, enter the [edited BYOC copy](#prepare-a-byoc-plugin-copy) instead.
3. Review discovered plugins and use **Add to Marketplace** if **Zep Memory** is not already listed. Confirm **Zep Memory** appears. Set the installation mode (**Required**, **Default On**, or **Default Off**) and marketplace access. **Required** and **Default On** install the plugin for members automatically. **Default Off** leaves install to each person in **Customize**.
4. Optional: turn on **Enable Auto Refresh** so Cursor re-indexes when the tracked branch updates. Auto Refresh needs the [Cursor GitHub App](https://cursor.com/docs/integrations/github) on the repository, and Cursor re-indexes at most every 10 minutes. You can also click **Refresh** to update immediately.
5. Sign in to Cursor with a test account that has marketplace access. Open **Customize** and confirm **Zep Memory** is present (skill plus MCP server). If you set **Default Off**, install it once from that panel. The first Memory MCP call opens Zep OAuth: enter a work email that the project MCP connection allows, sign in with the organization identity provider, select a Zep project if asked, and approve access. In a **new** chat, confirm the skill and tools appear.
6. Share the first-run steps in [What to tell members](#what-to-tell-members).

Cursor's [Plugins](https://cursor.com/docs/plugins) guide covers team marketplaces, installation modes, and Auto Refresh.

## What to tell members

Each person must connect Memory MCP with their own work email after you distribute Zep Memory. If Claude organization plugin settings, ChatGPT workspace plugin settings, or Cursor marketplace access block marketplaces and connectors, members connect Memory MCP only when Claude, ChatGPT, or Cursor shows the connect prompt. Claude Code and Codex CLI prompt on the first Memory MCP call after the plugin is installed.

Ask each person to:

1. Open Claude, ChatGPT, Claude Code, Codex, or Cursor and confirm **Zep Memory** is present. If you set Zep Memory to **Available for install** (Claude), allowed it in the workspace catalog (ChatGPT), or **Default Off** (Cursor), they install Zep Memory once from the organization marketplace, workspace catalog, or Customize panel. In Claude Code they install once from `/plugin` (or `claude plugin install zep-memory@zep-memory`) after shared settings register the marketplace. In Codex CLI they run the two install commands from the terminal if they have not already.
2. When the client shows the connect prompt, connect the bundled Memory MCP server: enter a work email, sign in with the organization identity provider, select a Zep project if asked, and approve access.
3. Start a **new** chat or session before they expect the Zep Memory skill and tools to appear.

The [Connecting a client](/memory-mcp-server/connect) page covers the sign-in flow and troubleshooting.

## After provisioning

The skill steers the assistant to prefer Zep for memory over other memory systems: read before guessing, apply stored preferences, and write durable facts (preferences, corrections, how-you-work procedures, decisions) without waiting for “remember this.” Ephemeral chat and one-off events are skipped. Writes go through Memory MCP tools. Zep does not auto-ingest every message.

Each client targets **the signed-in member's** user graph in the Zep project selected at sign-in. Read access is always available. Writes appear when the project MCP connection allows writes. Standalone graph tools appear when standalone graph access is enabled on the project connection. [What users can access](/memory-mcp-server#what-users-can-access) lists the tools.

Use these prompts when you test the plugin and when you share first-run steps:

```
Do you have access to the Zep Memory plugin and MCP server?
```

```
Using Zep memory, what do you already know about me? Give a short summary of my preferences and how I like to work.
```

```
Search my Zep memory for decisions about the Helios event store. Which database did we choose, and what reason was recorded?
```

```
Save this to my Zep memory: I want a one-line TL;DR at the top of every status update. After you save it, search Zep and confirm the preference is there.
```

## Related

* [Memory MCP Server](/memory-mcp-server) — authentication model, tools, and admin setup
* [Configuring authentication](/memory-mcp-server/authentication) — IdP connection, seats, and policies
* [Connecting a client](/memory-mcp-server/connect) — member sign-in flow and troubleshooting
* [Implement Zep with agents](/implement-zep-with-agents) — Build with Zep for coding agents (different plugin)
* Plugin source: [`getzep/zep-memory-plugin`](https://github.com/getzep/zep-memory-plugin)