> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Get Group Nodes

POST https://api.getzep.com/api/v2/graph/node/group/{group_id}
Content-Type: application/json

Returns all nodes for a group.

Reference: https://help.getzep.com/v2/sdk-reference/graph/node/get-group-nodes

## Request

### Path parameters

- `group_id` (string, required) — Group ID

### Body (application/json)

- `limit` (integer, optional) — Maximum number of items to return
- `uuid_cursor` (string, optional) — UUID based cursor, used for pagination. Should be the UUID of the last item in the previous page

## Response

### 200

Nodes

- `list of object`
  - `created_at` (string, required) — Creation time of the node
  - `name` (string, required) — Name of the node
  - `summary` (string, required) — Regional summary of surrounding edges
  - `uuid` (string, required) — UUID of the node
  - `attributes` (map from string to any, optional) — Additional attributes of the node. Dependent on node labels
  - `labels` (list of string, optional) — Labels associated with the node

## Examples

**Request**

```json
{}
```

**Response**

```json
[
  {
    "created_at": "2024-06-01T12:00:00Z",
    "name": "Temperature Sensor A1",
    "summary": "Node connected to 3 temperature sensors in the northern region",
    "uuid": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
    "attributes": {},
    "labels": [
      "sensor",
      "temperature"
    ]
  }
]
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.graph.node.getByGroupId("group_id", {});
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.graph.node.get_by_group_id(
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
    request := &zep.GraphNodesRequest{}
    client.Graph.Node.GetByGroupID(
        context.TODO(),
        "group_id",
        request,
    )
}

```