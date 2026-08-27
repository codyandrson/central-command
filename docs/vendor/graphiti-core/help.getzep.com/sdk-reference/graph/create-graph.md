> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Create Graph

POST https://api.getzep.com/api/v2/graph/create
Content-Type: application/json

Creates a new graph.

Reference: https://help.getzep.com/sdk-reference/graph/create-graph

## Request

### Body (application/json)

- `graph_id` (string, required)
- `description` (string, optional)
- `name` (string, optional)
- `time_zone` (string, optional, nullable) — The graph's IANA time zone. Stored on its group-backed subject.

## Response

### 201

The added graph

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
{
  "graph_id": "sales_performance_2024"
}
```

**Response**

```json
{
  "created_at": "2024-06-01T09:15:00Z",
  "description": "Graph showing monthly sales performance for 2024",
  "graph_id": "sales_performance_2024",
  "id": 1024,
  "name": "Sales Performance 2024",
  "project_uuid": "a3f1c9d2-7b4e-4f8a-9c3d-2e5f7a1b6c8d",
  "time_zone": "America/New_York",
  "updated_at": "2024-06-01T09:15:00Z",
  "uuid": "b7e9f8a1-3c4d-4e5f-9a2b-7c8d9e0f1a2b"
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.graph.create({
        graphId: "sales_performance_2024",
    });
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.graph.create(
    graph_id="sales_performance_2024",
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
    request := &zep.CreateGraphRequest{
        GraphID: "sales_performance_2024",
    }
    client.Graph.Create(
        context.TODO(),
        request,
    )
}

```