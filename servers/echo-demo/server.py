#!/usr/bin/env python3
"""Echo demo MCP server using mcp==2.0.0 API."""

import asyncio
from mcp.server.mcpserver import MCPServer

# Create the MCP server
server = MCPServer(
    name="echo-demo",
    title="Echo Demo",
    description="A simple echo demo server",
    version="1.0.0"
)

# Define the echo tool
@server.tool(name="echo", description="Echo back the input text")
def echo_tool(text: str) -> str:
    """Echo the input text with prefix."""
    return f"echo: {text}"

# Run the server on streamable-http transport
if __name__ == "__main__":
    # Use run_streamable_http_async to serve on 0.0.0.0:8000
    # The MCP convention is to serve on /mcp path for streamable-http
    asyncio.run(
        server.run_streamable_http_async(
            host="0.0.0.0",
            port=8000,
            streamable_http_path="/mcp"
        )
    )
