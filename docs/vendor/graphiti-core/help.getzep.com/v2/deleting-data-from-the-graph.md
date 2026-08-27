> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Deleting Data from the Graph

## Delete an Edge

Here's how to delete an edge from a graph:

```python Python
from zep_cloud.client import Zep

client = Zep(
    api_key=API_KEY,
)

client.graph.edge.delete(uuid_="your_edge_uuid")
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({
  apiKey: API_KEY,
});

await client.graph.edge.delete("your_edge_uuid");
```

Note that when you delete an edge, it never deletes the associated nodes, even if it means there will be a node with no edges.

## Delete an Episode

Deleting an episode does not regenerate the names or summaries of nodes shared with other episodes. This episode information may still exist within these nodes. If an episode invalidates a fact, and the episode is deleted, the fact will remain marked as invalidated.

When you delete an [episode](/graphiti/graphiti/adding-episodes), it will delete all the edges associated with it, and it will delete any nodes that are only attached to that episode. Nodes that are also attached to another episode will not be deleted.

Here's how to delete an episode from a graph:

```python Python
from zep_cloud.client import Zep

client = Zep(
    api_key=API_KEY,
)

client.graph.episode.delete(uuid_="episode_uuid")
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({
  apiKey: API_KEY,
});

await client.graph.episode.delete("episode_uuid");
```

## Delete a Node

This feature is coming soon.