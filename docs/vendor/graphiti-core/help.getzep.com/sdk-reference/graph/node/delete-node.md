> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Delete Node

DELETE https://api.getzep.com/api/v2/graph/node/{uuid}

Deletes a node by UUID.

Reference: https://help.getzep.com/sdk-reference/graph/node/delete-node

## Request

### Path parameters

- `uuid` (string, required) — Node UUID

## Response

### 200

Node deleted

- `message` (string, optional)

## Examples

**Request**

```json
{}
```

**Response**

```json
{
  "message": "Node with UUID 3fa85f64-5717-4562-b3fc-2c963f66afa6 successfully deleted."
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.graph.node.delete("uuid");
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.graph.node.delete(
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
    client.Graph.Node.Delete(
        context.TODO(),
        "uuid",
    )
}

```