> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Update Graph.

PATCH https://api.getzep.com/api/v2/graph/{graphId}
Content-Type: application/json

Updates information about a graph.

Reference: https://help.getzep.com/sdk-reference/graph/update-graph

## Request

### Path parameters

- `graphId` (string, required) — Graph ID

### Body (application/json)

- `description` (string, optional, nullable)
- `name` (string, optional, nullable)
- `time_zone` (string, optional, nullable) — The graph's IANA time zone. Stored on its group-backed subject.

## Response

### 201

The updated graph object

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
  "description": "Monthly sales data for North America region",
  "graph_id": "graph-7890",
  "id": 12345,
  "name": "North America Sales Q2",
  "project_uuid": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "time_zone": "America/New_York",
  "updated_at": "2024-06-01T12:00:00Z",
  "uuid": "550e8400-e29b-41d4-a716-446655440000"
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.graph.update("graphId", {});
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.graph.update(
    graph_id="graphId",
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
    request := &zep.UpdateGraphRequest{}
    client.Graph.Update(
        context.TODO(),
        "graphId",
        request,
    )
}

```