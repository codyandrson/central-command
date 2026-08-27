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

```go Go
import (
    "context"
    "log"

    "github.com/getzep/zep-go/v3/client"
    "github.com/getzep/zep-go/v3/option"
)

ctx := context.TODO()

zepClient := client.NewClient(option.WithAPIKey(apiKey))

_, err := zepClient.Graph.Edge.Delete(ctx, "your_edge_uuid")
if err != nil {
    log.Fatal("Error deleting edge:", err)
}
```

Note that when you delete an edge, it never deletes the associated nodes, even if it means there will be a node with no edges.

## Delete a Node

Here's how to delete a node from a graph:

```python Python
from zep_cloud.client import Zep

client = Zep(
    api_key=API_KEY,
)

client.graph.node.delete(uuid_="your_node_uuid")
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({
  apiKey: API_KEY,
});

await client.graph.node.delete("your_node_uuid");
```

```go Go
import (
    "context"
    "log"

    "github.com/getzep/zep-go/v3/client"
    "github.com/getzep/zep-go/v3/option"
)

ctx := context.TODO()

zepClient := client.NewClient(option.WithAPIKey(apiKey))

_, err := zepClient.Graph.Node.Delete(ctx, "your_node_uuid")
if err != nil {
    log.Fatal("Error deleting node:", err)
}
```

Deleting a node will also delete all edges connected to that node. This is a cascading delete operation - the node and all its relationships are permanently removed from the graph.

## Delete an Episode

Deleting an episode does not regenerate the names or summaries of nodes shared with other episodes. This episode information may still exist within these nodes. If an episode invalidates a fact, and the episode is deleted, the fact will remain marked as invalidated.

When you delete an [episode](/graphiti/graphiti/adding-episodes):

* **Edges** are deleted only if no other episodes are associated with them. An edge associated with other episodes will be preserved.
* **Nodes** are deleted only if no other episodes are associated with them. A node associated with other episodes will be preserved.
* **User entity exception**: For user graphs, the user entity node is never deleted when an episode is deleted. The user entity is created automatically when the user is created, before any episodes are added, so it is always preserved.

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

```go Go
import (
    "context"
    "log"

    "github.com/getzep/zep-go/v3/client"
    "github.com/getzep/zep-go/v3/option"
)

ctx := context.TODO()

zepClient := client.NewClient(option.WithAPIKey(apiKey))

_, err := zepClient.Graph.Episode.Delete(ctx, "episode_uuid")
if err != nil {
    log.Fatal("Error deleting episode:", err)
}
```

### How episodes become associated with edges and nodes

Whether an edge or node survives an episode deletion depends on its episode associations — the links between an episode and the artifacts Zep derived from it. For a full explanation of how episodes associate with edges, nodes, and the other context types, how to inspect those associations, and how episode metadata projects across them, see [Episode metadata projection](/episode-metadata-projection#how-episodes-associate-with-each-context-type).

## Delete a Thread

Deleting a thread removes all episodes associated with that thread. This triggers a cascading effect on the graph.

When a thread is deleted, each associated episode is removed. For each episode:

* **Edges** are deleted only if no other episodes are associated with them
* **Nodes** are deleted only if no other episodes are associated with them
* **User entity exception**: For user graphs, the user entity node is never deleted, even if all episodes are removed. The user entity is created when the user is created, before any episodes are added, so it is always preserved.

This design preserves graph integrity. If multiple conversations mention the same entity or establish the same relationship, deleting one thread will not remove data that other threads contributed to the graph.

Here's how to delete a thread:

```python Python
from zep_cloud.client import Zep

client = Zep(
    api_key=API_KEY,
)

client.thread.delete(thread_id="your_thread_id")
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({
  apiKey: API_KEY,
});

await client.thread.delete("your_thread_id");
```

```go Go
import (
    "context"
    "log"

    "github.com/getzep/zep-go/v3/client"
    "github.com/getzep/zep-go/v3/option"
)

ctx := context.TODO()

zepClient := client.NewClient(option.WithAPIKey(apiKey))

_, err := zepClient.Thread.Delete(ctx, "your_thread_id")
if err != nil {
    log.Fatal("Error deleting thread:", err)
}
```