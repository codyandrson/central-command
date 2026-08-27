> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Delete Graph

DELETE https://api.getzep.com/api/v2/graph/{graphId}

Deletes a graph. If you would like to delete a user graph, make sure to use user.delete instead.

Reference: https://help.getzep.com/sdk-reference/graph/delete-graph

## Request

### Path parameters

- `graphId` (string, required) — Graph ID

## Response

### 200

Deleted

- `message` (string, optional)

## Examples

**Request**

```json
{}
```

**Response**

```json
{
  "message": "Graph with ID 8f14e45f-ceea-4d3a-9f2b-1a2b3c4d5e6f successfully deleted."
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.graph.delete("graphId");
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.graph.delete(
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
    client.Graph.Delete(
        context.TODO(),
        "graphId",
    )
}

```