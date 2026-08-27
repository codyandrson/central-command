> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Coding with LLMs

Zep provides tools that give coding agents direct access to Zep's documentation: a real-time MCP server and standardized llms.txt files for enhanced code generation and troubleshooting.

## MCP Server

Zep's Model Context Protocol (MCP) server gives coding agents real-time access to search Zep's complete documentation.

**Server details:**

* URL: `docs-mcp.getzep.com`
* Type: Search-based with HTTP transport
* Capabilities: Real-time documentation search and retrieval

The `/sse` endpoint is deprecated and will be removed soon. Please update to the new `/mcp` endpoint with HTTP transport.

### Setting up the MCP server

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

Enable MCP servers in Cursor settings, then add and enable the zep-docs server.

#### Other MCP clients

Configure your MCP client with HTTP transport:

```
URL: https://docs-mcp.getzep.com/mcp
```

### Using the MCP server

Once configured, coding agents can automatically:

* Search Zep concepts and features
* Find code examples and tutorials
* Access current API documentation
* Retrieve troubleshooting information

## llms.txt

Zep publishes standardized `llms.txt` files containing essential information for coding agents:

* Core concepts and architecture
* Usage patterns and examples
* API reference summaries
* Best practices and troubleshooting
* Framework integration examples

### Accessing llms.txt

Zep provides two versions of the llms.txt file:

**Standard version** (recommended for most use cases):

```
https://help.getzep.com/llms.txt
```

**Comprehensive version** (for advanced use cases):

```
https://help.getzep.com/llms-full.txt
```

The standard version contains curated essentials, while the comprehensive version includes complete documentation but is much larger. Most coding agents work better with the standard version due to context limitations.