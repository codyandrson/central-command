> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Add Nodes

POST https://api.getzep.com/api/v2/graph/nodes
Content-Type: application/json

Add entity nodes to a user or graph directly, without episode ingestion. Up to 100 nodes per request.

Reference: https://help.getzep.com/sdk-reference/graph/node/add-nodes

## Request

### Body (application/json)

- `nodes` (list of object, required) — The nodes to add. 1 to 100 items.
  - `name` (string, required) — The name of the node. Used to derive the node's search embedding.
  - `attributes` (map from string to any, optional) — Additional attributes of the node. Values must be scalar types (string, number, boolean, or null). Nested objects and arrays are not allowed.
  - `created_at` (string, optional) — The node creation time. Defaults to the request time when absent.
  - `label` (string, optional) — The node's entity type. At most one; the base "Entity" label is added implicitly by the graph layer on save and does not need to be supplied.
  - `metadata` (map from string to any, optional) — Optional metadata attached to the node's shadow episode. Max 10 scalar key-value pairs.
  - `summary` (string, optional) — A regional summary of the node.
- `graph_id` (string, optional)
- `user_id` (string, optional)

## Response

### 202

Accepted

- `nodes` (list of object, optional) — The accepted nodes, each carrying the UUID Zep assigned to it, in request order.
  - `name` (string, required) — The name of the node.
  - `attributes` (map from string to any, optional) — Additional attributes of the node.
  - `created_at` (string, optional) — The node creation time.
  - `label` (string, optional) — The node's entity type.
  - `metadata` (map from string to any, optional) — Metadata attached to the node's shadow episode.
  - `summary` (string, optional) — A regional summary of the node.
  - `uuid` (string, optional) — The node UUID, assigned by Zep.
- `task_id` (string, optional) — Task ID of the async add-nodes task.

## Examples

**Request**

```json
{
  "nodes": [
    {
      "name": "Project Apollo"
    }
  ]
}
```

**Response**

```json
{
  "nodes": [
    {
      "name": "Project Apollo",
      "attributes": {},
      "created_at": "2024-06-01T10:15:30Z",
      "label": "Project",
      "metadata": {},
      "summary": "A major initiative to develop the next-generation space exploration technology.",
      "uuid": "a3f1c9d2-4b7e-4f8a-9c3d-2e5f7b8a9c1d"
    }
  ],
  "task_id": "task-9f8b7c6d-1234-5678-9abc-def012345678"
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.graph.addNodes({
        nodes: [
            {
                name: "Project Apollo",
            },
        ],
    });
}
main();

```

```python
from zep_cloud import Zep, AddNodeItem

client = Zep()

client.graph.add_nodes(
    nodes=[
        AddNodeItem(
            name="Project Apollo",
        )
    ],
)

```

```go
package example

import (
    context "context"

    zep "github.com/getzep/zep-go"
    client "github.com/getzep/zep-go/client"
)

func do() {
    client := client.NewClient()
    request := &zep.AddNodesRequest{
        Nodes: []*zep.AddNodeItem{
            &zep.AddNodeItem{
                Name: "Project Apollo",
            },
        },
    }
    client.Graph.AddNodes(
        context.TODO(),
        request,
    )
}

```