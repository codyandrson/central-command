> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Get Graph

GET https://api.getzep.com/api/v2/graph/{graphId}

Returns a graph.

Reference: https://help.getzep.com/sdk-reference/graph/get-graph

## Request

### Path parameters

- `graphId` (string, required) — The graph_id of the graph to get.

## Response

### 200

The graph that was retrieved.

- `created_at` (string, optional)
- `description` (string, optional)
- `graph_id` (string, optional)
- `id` (integer, optional)
- `name` (string, optional)
- `project_uuid` (string, optional)
- `time_zone` (string, optional, nullable)
- `updated_at` (string, optional)
- `uuid` (string, optional)

## Examples

**Request**

```json
{}
```

**Response**

```json
{
  "created_at": "2024-05-10T09:15:00Z",
  "description": "Sales data graph for Q1 2024",
  "graph_id": "graph-9f8b7c6d",
  "id": 42,
  "name": "Q1 Sales Overview",
  "project_uuid": "a3f1c9e2-7b4d-4f6a-9c3e-2d5f7b8a1e4c",
  "time_zone": "America/New_York",
  "updated_at": "2024-05-15T16:45:00Z",
  "uuid": "d2f4e6a8-1b3c-4d5e-9f7a-8b9c0d1e2f3a"
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.graph.get("graphId");
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.graph.get(
    graph_id="graphId",
)

```

```go
package example

import (
    context "context"

    client "github.com/getzep/zep-go/client"
)

func do() {
    client := client.NewClient()
    client.Graph.Get(
        context.TODO(),
        "graphId",
    )
}

```