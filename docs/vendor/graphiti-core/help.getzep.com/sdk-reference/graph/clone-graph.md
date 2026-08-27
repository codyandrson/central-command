> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Clone graph

POST https://api.getzep.com/api/v2/graph/clone
Content-Type: application/json

Clone a user or group graph.

Reference: https://help.getzep.com/sdk-reference/graph/clone-graph

## Request

### Body (application/json)

- `source_graph_id` (string, optional) — source_graph_id is the ID of the graph to be cloned. Required if source_user_id is not provided
- `source_user_id` (string, optional) — user_id of the user whose graph is being cloned. Required if source_graph_id is not provided
- `target_graph_id` (string, optional) — target_graph_id is the ID to be set on the cloned graph. Must not point to an existing graph. Required if target_user_id is not provided.
- `target_user_id` (string, optional) — user_id to be set on the cloned user. Must not point to an existing user. Required if target_graph_id is not provided.

## Response

### 202

Response object containing graph_id or user_id pointing to the new graph

- `graph_id` (string, optional) — graph_id is the ID of the cloned graph
- `task_id` (string, optional) — Task ID of the clone graph task
- `user_id` (string, optional)

## Examples

**Request**

```json
{}
```

**Response**

```json
{
  "graph_id": "a1b2c3d4-e5f6-7890-ab12-cd34ef567890",
  "task_id": "task-9876543210",
  "user_id": "user-1234567890"
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.graph.clone({});
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.graph.clone()

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
    request := &zep.CloneGraphRequest{}
    client.Graph.Clone(
        context.TODO(),
        request,
    )
}

```