> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Warm Graph Cache

GET https://api.getzep.com/api/v2/graph/{graphId}/warm

Hints Zep to warm a graph for low-latency search

Reference: https://help.getzep.com/sdk-reference/graph/warm-graph-cache

## Request

### Path parameters

- `graphId` (string, required) — The graph_id of the graph to warm.

## Response

### 200

Warm hint accepted

- `message` (string, optional)

## Examples

**Request**

```json
{}
```

**Response**

```json
{
  "message": "Graph warm-up initiated successfully for low-latency search."
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.graph.warm("graphId");
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.graph.warm(
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
    client.Graph.Warm(
        context.TODO(),
        "graphId",
    )
}

```