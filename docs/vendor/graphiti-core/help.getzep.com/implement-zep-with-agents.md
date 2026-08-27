> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Implement Zep with agents

Coding agents build on Zep faster when they understand [how Zep works](/concepts) and can read the current documentation as they write code. The **Build with Zep plugin** gives Claude Code, Codex, and Cursor both: a skill that encodes how to design, build, and evaluate a Zep integration, and the Zep documentation MCP server for real-time search over the docs. One plugin installs in all three ecosystems.

The plugin is for building *with* Zep in your own codebase. You'll still need a Zep project and API key — start with the [quick start guide](/quick-start-guide).

The [Zep Memory plugin](/use-zep-in-claude-chatgpt) gives Claude, ChatGPT, Claude Code, Codex, and Cursor the signed-in person's agent memory. You can install it alongside this plugin.

## What the plugin includes

The plugin bundles two things that work together:

* **The `building-with-zep` skill** — the decision-and-workflow layer for building on Zep: scoping graphs, ingesting data, retrieving context, and evaluating whether Zep delivers your use case. Your agent invokes it automatically when you write or design Zep integration code. It indexes the docs rather than duplicating them, so its guidance stays current.
* **The Zep documentation MCP server (`zep-docs`)** — real-time search and whole-page access over Zep's documentation, served at `https://docs-mcp.getzep.com/mcp` (remote HTTP, no API key). The skill queries it for exact, current details — method names, parameters, limits — and prefers reading a full page over a search snippet.

The plugin is open source — the skill, its MCP configuration, and the Claude Code / Codex / Cursor marketplace catalogs live in the [`getzep/building-with-zep-plugin`](https://github.com/getzep/building-with-zep-plugin) repository.

## Install

#### Claude Code

From your terminal (not inside a Claude Code session), add the plugin repository as a marketplace and install the plugin:

```bash
claude plugin marketplace add getzep/building-with-zep-plugin
claude plugin install building-with-zep@building-with-zep
```

#### Codex

From your terminal (not inside a Codex session), add the plugin repository as a marketplace and install the plugin:

```bash
codex plugin marketplace add getzep/building-with-zep-plugin
codex plugin add building-with-zep@building-with-zep
```

#### Cursor

Requires Cursor 3.15.6 or later. Add the Build with Zep marketplace from GitHub, then install the plugin from the Agents window:

#### Open Customize

In the Agents window, open **Customize** in the left sidebar.

#### Browse the marketplace

Click **Browse marketplace**.

#### Add the marketplace from GitHub

Open the dropdown next to the search bar, choose **+ Add Marketplace**, then **Import from Github**.

<img src="https://fdr-prod-docs-files-public.s3.us-east-1.amazonaws.com/zep.docs.buildwithfern.com/0697b330db34352ec0c9d29c904d96e68467216ca62b7e70789dfd487267ca41/images/cursor-add-marketplace-from-github.png?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=AKIA6KXJSKKNFOCF7G4B%2F20260825%2Fus-east-1%2Fs3%2Faws4_request&X-Amz-Date=20260825T142126Z&X-Amz-Expires=604800&X-Amz-Signature=503dbb048746fe7d843affaa558b1347e684bed972862eba3bf1d7866d2fe168&X-Amz-SignedHeaders=host&x-amz-checksum-mode=ENABLED&x-id=GetObject" alt="Cursor marketplace source dropdown with Add Marketplace and Import from Github selected" />

Enter the Build with Zep plugin repository URL:

```
https://github.com/getzep/building-with-zep-plugin
```

#### Install the plugin

On the marketplace page, find **Build with Zep** and add it. Approve the `zep-docs` server the first time your agent calls it.

Verify the install under **Settings → Plugins**, where `building-with-zep` lists both its skill and the Zep Docs MCP server.

#### Other agents

There's no plugin format outside Claude Code, Codex, and Cursor, but the two components install separately in any agent that supports [Agent Skills](https://agentskills.io) and remote MCP servers. GitHub Copilot, VS Code, and Gemini CLI are among them.

The skill is a single file. Many agents read `.agents/skills/` in a project, or `~/.agents/skills/` for every project:

```bash
curl -fsSL https://raw.githubusercontent.com/getzep/building-with-zep-plugin/main/skills/building-with-zep/SKILL.md \
  --create-dirs -o ~/.agents/skills/building-with-zep/SKILL.md
```

The Agent Skills standard defines the skill's own layout but not where agents look for skills, so some read their own directory instead. Check your agent's documentation, or the [Agent Skills client list](https://agentskills.io/clients).

Then add the `zep-docs` server to that agent, following the [Zep documentation MCP server](/docs-mcp-server) setup.

## Keep the plugin up to date

The `zep-docs` MCP server is remote, so your agent always searches the current documentation. The skill ships as a file on disk, so it goes stale: new guidance, workflows, and corrections reach you only when the plugin updates. Claude Code needs updating switched on; Codex does it by default. Without that, your agent keeps building against whatever the skill said the day you installed it. The plugin's [changelog](https://github.com/getzep/building-with-zep-plugin/blob/main/CHANGELOG.md) lists what each release changed.

#### Claude Code

Claude Code enables auto-update by default only for Anthropic's own marketplaces. `building-with-zep` is third-party, so updates stay off until you turn them on: run `/plugin`, select **Marketplaces**, choose **building-with-zep**, then select **Enable auto-update**.

Claude Code then refreshes the marketplace and updates the plugin shortly after each session starts, with a random delay of up to ten minutes. Your running session keeps the version it launched with. If the plugin changed, Claude Code prompts you to run `/reload-plugins`; otherwise the new version loads the next time you start it.

To update once without enabling auto-update, refresh the marketplace and then the plugin:

```bash
claude plugin marketplace update building-with-zep
claude plugin update building-with-zep@building-with-zep
```

Administrators rolling the plugin out to a team can skip the per-user toggle by setting `"autoUpdate": true` on the marketplace's [`extraKnownMarketplaces`](https://code.claude.com/docs/en/settings#extraknownmarketplaces) entry in managed settings.

If you set `DISABLE_AUTOUPDATER` to manage Claude Code's own updates yourself, it disables plugin updates too. Set `FORCE_AUTOUPDATE_PLUGINS=1` alongside it to keep plugin auto-update running.

#### Codex

Codex currently refreshes configured git marketplaces on startup, so there's nothing to enable. To pull an update immediately:

```bash
codex plugin marketplace upgrade building-with-zep
```

That refreshes the marketplace and reinstalls the plugin in one step, including when the version number hasn't changed. Start a new Codex session afterward to load it.

#### Cursor

To pick up a new plugin version from the marketplace, remove `building-with-zep` under **Settings → Plugins** and add it again from the marketplace you imported.

To track the latest version from a local clone instead, remove the marketplace install under **Settings → Plugins** first so the skill doesn't load twice, then:

```bash
git clone --depth 1 https://github.com/getzep/building-with-zep-plugin.git ~/src/building-with-zep-plugin
mkdir -p ~/.cursor/plugins/local
ln -s ~/src/building-with-zep-plugin ~/.cursor/plugins/local/building-with-zep
```

Run **Developer: Reload Window** to load it. From then on, `git -C ~/src/building-with-zep-plugin pull` followed by another window reload updates the skill and its MCP server together.

#### Other agents

Re-run the `curl` command you installed with. It overwrites the skill in place:

```bash
curl -fsSL https://raw.githubusercontent.com/getzep/building-with-zep-plugin/main/skills/building-with-zep/SKILL.md \
  --create-dirs -o ~/.agents/skills/building-with-zep/SKILL.md
```

## How your agent uses it

Once installed, the skill activates on its own when you work on Zep: adding memory to an agent, ingesting chat or business data, [searching the graph](/searching-the-graph), choosing between user and standalone graphs, or debugging retrieval. As it works, the agent reads the current documentation through the `zep-docs` MCP server instead of relying on its training data, so its code and recommendations track the live docs — and when the two disagree, the docs win.

## Just the documentation MCP server

In a client that supports MCP but not skills, set up the [Zep documentation MCP server](/docs-mcp-server) on its own. Your agent gets the current documentation, without the design and evaluation guidance the skill carries.