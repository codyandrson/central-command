> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Understanding the Graph

Zep's temporal knowledge graph powers its context engineering capabilities, including agent memory and Graph RAG. Zep's graph is built on [Graphiti](/graphiti/graphiti/overview), Zep's open-source temporal graph library, which is fully integrated into Zep. Developers do not need to interact directly with Graphiti or understand its underlying implementation.

Zep's graph database stores data in three main types:

1. Entity edges (edges): Represent relationships between nodes and include semantic facts representing the relationship between the edge's nodes.
2. Entity nodes (nodes): Represent entities extracted from episodes, containing summaries of relevant information.
3. Episodic nodes (episodes): Represent raw data stored in Zep, either through chat history or the `graph.add` endpoint.

## Working with the Graph

To learn more about interacting with Zep's graph, refer to the following sections:

* [Adding Data to the Graph](/v2/adding-data-to-the-graph): Learn how to add new data to the graph.
* [Reading Data from the Graph](/v2/reading-data-from-the-graph): Discover how to retrieve information from the graph.
* [Searching the Graph](/v2/searching-the-graph): Explore techniques for efficiently searching the graph.

These guides will help you leverage the full power of Zep's knowledge graph in your applications.