> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Documentation MCP server

Zep's Documentation MCP server enables coding agents to search and retrieve information from Zep's complete documentation in real-time.

Building with Claude Code, Codex, or Cursor? The [Build with Zep plugin](/implement-zep-with-agents) bundles this server with a skill for building on Zep — install that instead of configuring the server by hand. The setup below is for other MCP clients, or for Cursor if you want the documentation server without the skill.

**Server details:**

* URL: `https://docs-mcp.getzep.com/mcp`
* Transport: Streamable HTTP. No API key required.
* Capabilities: documentation search, and whole-page reads as MCP resources

The `/sse` endpoint is deprecated and will be removed soon. Please update to the new `/mcp` endpoint with HTTP transport.

## Setting up the MCP server

#### Claude Code

Add the HTTP server using the CLI:

```bash
claude mcp add zep-docs --transport http https://docs-mcp.getzep.com/mcp
```

#### Cursor

Create `.cursor/mcp.json` in your project or `~/.cursor/mcp.json` globally:

```json
{
  "mcpServers": {
    "zep-docs": {
      "url": "https://docs-mcp.getzep.com/mcp"
    }
  }
}
```

Cursor reads the file automatically. Toggle the server on or off from **Customize** in the sidebar.

#### Other MCP clients

Configure your MCP client with HTTP transport:

```
URL: https://docs-mcp.getzep.com/mcp
```

## Using the MCP server

Once configured, coding agents can automatically:

* Search Zep concepts and features
* Find code examples and tutorials
* Access current API documentation
* Retrieve troubleshooting information