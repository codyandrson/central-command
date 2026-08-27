> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Delete Batch

DELETE https://api.getzep.com/api/v2/batches/{batchId}

Delete a draft or invalid unprocessed batch. Processed batches cannot be deleted.

Reference: https://help.getzep.com/sdk-reference/batch/delete-batch

## Request

### Path parameters

- `batchId` (string, required) — The batch ID.

## Response

### 200

Deleted batch

- `message` (string, optional)

## Examples

**Request**

```json
{}
```

**Response**

```json
{
  "message": "Batch 3f9a1b7c-8d4e-4f2a-9c3e-2b7d5f6a1e4c successfully deleted."
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.batch.delete("batchId");
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.batch.delete(
    batch_id="batchId",
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
    client.Batch.Delete(
        context.TODO(),
        "batchId",
    )
}

```