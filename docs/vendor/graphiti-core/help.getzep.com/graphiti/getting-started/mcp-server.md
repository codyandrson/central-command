> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Graphiti MCP Server

> Connect Graphiti's Context Graphs to Claude, Cursor, and other MCP clients via the Graphiti MCP server.

#### What is the Graphiti MCP Server?

The Graphiti MCP Server is an experimental implementation that exposes Graphiti's key functionality through the Model Context Protocol (MCP). This enables AI assistants like Claude Desktop, Cursor, and VS Code with Copilot to interact with Graphiti's Context Graph capabilities, providing persistent context and contextual awareness.

The Graphiti MCP Server bridges AI assistants with Graphiti's temporally-aware knowledge graphs, allowing assistants to maintain persistent context across conversations and sessions. Unlike traditional RAG methods, it continuously integrates user interactions, structured and unstructured data, and external information into a coherent, queryable graph.

## Key Features

The MCP server exposes Graphiti's core capabilities:

* **Episode Management**: Add, retrieve, and delete episodes (text, messages, or JSON data)
* **Entity Management**: Search and manage entity nodes and relationships
* **Search Capabilities**: Semantic and hybrid search for facts and node summaries
* **Group Management**: Organize data with group\_id filtering for multi-user scenarios
* **Graph Maintenance**: Clear graphs and rebuild indices as needed
* **Pre-configured Entity Types**: Structured entity extraction for domain-specific use cases
* **Multiple Database Support**: FalkorDB (Redis-based, default) and Neo4j
* **Flexible LLM Providers**: OpenAI, Anthropic, Gemini, Groq, and Azure OpenAI
* **Multiple Embedding Options**: OpenAI, Voyage, Sentence Transformers, and Gemini

## Quick Start

This quick start uses OpenAI and FalkorDB (default). The server supports multiple LLM providers (OpenAI, Anthropic, Gemini, Groq, Azure OpenAI) and databases (FalkorDB, Neo4j). For detailed configuration options, see the [MCP Server README](https://github.com/getzep/graphiti/blob/main/mcp_server/README.md).

### Prerequisites

Before getting started, ensure you have:

1. **Python 3.10+** installed on your system
2. **Database** - Either FalkorDB (default, Redis-based) or Neo4j (5.26+) running locally or accessible remotely
3. **LLM API key** - For OpenAI, Anthropic, Gemini, Groq, or Azure OpenAI

### Installation

1. Clone the Graphiti repository:

```bash
git clone https://github.com/getzep/graphiti.git
cd graphiti
```

2. Navigate to the MCP server directory and install dependencies:

```bash
cd mcp_server
uv sync
```

### Configuration

Configuration follows a precedence hierarchy: command-line arguments override environment variables, which override `config.yaml` settings.

Set up your environment variables in a `.env` file:

```bash
# Required LLM Configuration
OPENAI_API_KEY=your_openai_api_key_here
MODEL_NAME=gpt-5-mini

# Database Configuration (FalkorDB is default, or use Neo4j)
# For FalkorDB (Redis-based):
# REDIS_HOST=localhost
# REDIS_PORT=6379
# REDIS_PASSWORD=your_redis_password

# For Neo4j:
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=your_neo4j_password

# Optional: Disable telemetry
# GRAPHITI_TELEMETRY_ENABLED=false
```

### Running the Server

Start the MCP server:

```bash
uv run graphiti_mcp_server.py
```

For development with custom options:

```bash
uv run graphiti_mcp_server.py --model gpt-5-mini --transport sse --group-id my-project
```

## MCP Client Integration

The MCP server supports integration with multiple AI assistants through different transport protocols.

### Claude Desktop

Configure Claude Desktop to connect via the stdio transport:

```json
{
  "mcpServers": {
    "graphiti-context": {
      "transport": "stdio",
      "command": "/path/to/uv",
      "args": [
        "run",
        "--directory",
        "/path/to/graphiti/mcp_server",
        "graphiti_mcp_server.py",
        "--transport",
        "stdio"
      ],
      "env": {
        "OPENAI_API_KEY": "your_api_key",
        "MODEL_NAME": "gpt-5-mini",
        "NEO4J_URI": "bolt://localhost:7687",
        "NEO4J_USER": "neo4j",
        "NEO4J_PASSWORD": "your_password"
      }
    }
  }
}
```

### Cursor IDE

For Cursor, use the SSE transport configuration:

```json
{
  "mcpServers": {
    "graphiti-context": {
      "url": "http://localhost:8000/sse"
    }
  }
}
```

### VS Code with Copilot

VS Code with Copilot can connect to the MCP server using HTTP endpoints. Configure your VS Code settings to point to the running MCP server.

## Available Tools

Once connected, AI assistants have access to these Graphiti tools:

* `add_episode` - Store episodes and interactions in the knowledge graph
* `search_facts` - Find relevant facts and relationships
* `search_nodes` - Search for entity summaries and information
* `get_episodes` - Retrieve recent episodes for context
* `delete_episode` - Remove episodes from the graph
* `clear_graph` - Reset the knowledge graph entirely

## Docker Deployment

For containerized deployment, use the provided Docker Compose setup:

```bash
docker compose up
```

This starts both the database (FalkorDB or Neo4j) and the MCP server with SSE transport enabled. Docker Compose can launch services in unified or separate containers with sensible defaults for immediate use.

## Performance and Privacy

### Performance Tuning

Episode processing uses asynchronous queuing with concurrency controlled by `SEMAPHORE_LIMIT`. The MCP server README provides tier-specific guidelines for major LLM providers to prevent rate-limiting while maximizing throughput.

### Telemetry

The framework includes optional anonymous telemetry collection that captures only system information. Telemetry never exposes API keys or graph content. Disable telemetry by setting:

```bash
GRAPHITI_TELEMETRY_ENABLED=false
```

## Next Steps

For comprehensive configuration options, advanced features, and troubleshooting:

* **Full Documentation**: See the complete [MCP Server README](https://github.com/getzep/graphiti/blob/main/mcp_server/README.md)
* **Integration Examples**: Explore client-specific setup guides for Claude Desktop, Cursor, and VS Code
* **Custom Entity Types**: Configure pre-configured entity types for domain-specific extraction
* **Multi-tenant Setup**: Use group IDs for organizing data across different contexts
* **Alternative LLM Providers**: Configure Anthropic, Gemini, Groq, or Azure OpenAI
* **Database Options**: Switch between FalkorDB and Neo4j based on your needs

The MCP server is experimental and under active development. Features and APIs may change between releases.