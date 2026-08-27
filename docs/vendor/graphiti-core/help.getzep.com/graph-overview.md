> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Graph Overview

Zep's temporal knowledge graph powers its context engineering capabilities, including agent memory and Graph RAG. Zep's graph is built on [Graphiti](/graphiti/graphiti/overview), Zep's open-source temporal graph library, which is fully integrated into Zep. Developers do not need to interact directly with Graphiti or understand its underlying implementation.

#### What is a Knowledge Graph?

A knowledge graph is a network of interconnected facts, such as *"Kendra loves
Adidas shoes."* Each fact is a *"triplet"* represented by two entities, or
nodes (*"Kendra", "Adidas shoes"*), and their relationship, or edge
(*"loves"*).

<br />

Knowledge Graphs have been explored extensively for information retrieval.
Zep autonomously builds temporal knowledge graphs, handling changing relationships
and maintaining historical context.

Zep automatically constructs a temporal knowledge graph for each of your users. The knowledge graph contains entities, relationships, and facts related to your user, while automatically handling changing relationships and facts over time.

Here's an example of how Zep might extract graph data from a chat message, and then update the graph once new information is available:

<img src="https://fdr-prod-docs-files-public.s3.us-east-1.amazonaws.com/zep.docs.buildwithfern.com/204428cea99ae5c2c2055235e6cab2c0b5bc5aad3d7fdfbb1e3792cf4cd9e8fb/images/graphiti-graph-intro.gif?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=AKIA6KXJSKKNFOCF7G4B%2F20260825%2Fus-east-1%2Fs3%2Faws4_request&X-Amz-Date=20260825T130151Z&X-Amz-Expires=604800&X-Amz-Signature=fb09c612bbe59cb5409cd2694b3f764b460d103f0e1ae4179ff544839795e856&X-Amz-SignedHeaders=host&x-amz-checksum-mode=ENABLED&x-id=GetObject" alt="graphiti intro slides" />

Each node and edge contains certain attributes - notably, a fact is always stored as an edge attribute. There are also datetime attributes for when the fact becomes valid and when it becomes invalid.

## Graph Data Structure

Zep's graph database stores data in three main types:

1. Entity edges (edges): Represent relationships between nodes and include semantic facts representing the relationship between the edge's nodes.
2. Entity nodes (nodes): Represent entities extracted from episodes, containing summaries of relevant information.
3. Episodic nodes (episodes): Represent raw data stored in Zep, either through chat history or the `graph.add` endpoint.

## Working with the Graph

To learn more about interacting with Zep's graph, refer to the following sections:

* [How Graph Creation Works](/how-graph-creation-works): How Zep turns episodes into entities, relationships, and facts in the graph.
* [Documents](/documents): Group episodes of the same source the way threads group messages.
* [Adding Data to the Graph](/adding-context): Learn how to add new data to the graph.
* [Searching the Graph](/searching-the-graph): Explore techniques for efficiently searching the graph.

These guides will help you use Zep's knowledge graph in your applications.