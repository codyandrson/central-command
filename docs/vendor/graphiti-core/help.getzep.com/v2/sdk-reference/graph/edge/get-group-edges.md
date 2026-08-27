> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Get Group Edges

POST https://api.getzep.com/api/v2/graph/edge/group/{group_id}
Content-Type: application/json

Returns all edges for a group.

Reference: https://help.getzep.com/v2/sdk-reference/graph/edge/get-group-edges

## Request

### Path parameters

- `group_id` (string, required) — Group ID

### Body (application/json)

- `limit` (integer, optional) — Maximum number of items to return
- `uuid_cursor` (string, optional) — UUID based cursor, used for pagination. Should be the UUID of the last item in the previous page

## Response

### 200

Edges

- `list of object`
  - `created_at` (string, required) — Creation time of the edge
  - `fact` (string, required) — Fact representing the edge and nodes that it connects
  - `name` (string, required) — Name of the edge, relation name
  - `source_node_uuid` (string, required) — UUID of the source node
  - `target_node_uuid` (string, required) — UUID of the target node
  - `uuid` (string, required) — UUID of the edge
  - `attributes` (map from string to any, optional) — Additional attributes of the edge. Dependent on edge types
  - `episodes` (list of string, optional) — List of episode ids that reference these entity edges
  - `expired_at` (string, optional) — Datetime of when the node was invalidated
  - `invalid_at` (string, optional) — Datetime of when the fact stopped being true
  - `valid_at` (string, optional) — Datetime of when the fact became true

## Examples

**Request**

```json
{}
```

**Response**

```json
[
  {
    "created_at": "2024-05-20T10:15:30Z",
    "fact": "User A follows User B",
    "name": "follows",
    "source_node_uuid": "a3f1c9e2-4b7d-4f8a-9c2e-1d2f3b4a5c6d",
    "target_node_uuid": "b4d2e3f4-5a6b-7c8d-9e0f-1a2b3c4d5e6f",
    "uuid": "c7d8e9f0-1a2b-3c4d-5e6f-7a8b9c0d1e2f",
    "attributes": {},
    "episodes": [
      "episode-1234",
      "episode-5678"
    ],
    "expired_at": "2024-06-20T10:15:30Z",
    "invalid_at": "2024-06-20T10:15:30Z",
    "valid_at": "2024-05-20T10:15:30Z"
  }
]
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.graph.edge.getByGroupId("group_id", {});
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.graph.edge.get_by_group_id(
    group_id="group_id",
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
    request := &zep.GraphEdgesRequest{}
    client.Graph.Edge.GetByGroupID(
        context.TODO(),
        "group_id",
        request,
    )
}

```