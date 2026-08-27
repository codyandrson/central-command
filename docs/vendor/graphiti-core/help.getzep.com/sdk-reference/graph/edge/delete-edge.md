> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Delete Edge

DELETE https://api.getzep.com/api/v2/graph/edge/{uuid}

Deletes an edge by UUID.

Reference: https://help.getzep.com/sdk-reference/graph/edge/delete-edge

## Request

### Path parameters

- `uuid` (string, required) — Edge UUID

## Response

### 200

Edge deleted

- `message` (string, optional)

## Examples

**Request**

```json
{}
```

**Response**

```json
{
  "message": "Edge with UUID 3fa85f64-5717-4562-b3fc-2c963f66afa6 successfully deleted."
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.graph.edge.delete("uuid");
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.graph.edge.delete(
    uuid_="uuid",
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
    client.Graph.Edge.Delete(
        context.TODO(),
        "uuid",
    )
}

```