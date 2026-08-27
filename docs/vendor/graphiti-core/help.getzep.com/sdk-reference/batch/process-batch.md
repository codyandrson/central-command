> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Process Batch

POST https://api.getzep.com/api/v2/batches/{batchId}/process

Start processing a filled batch. Repeated calls return a conflict.

Reference: https://help.getzep.com/sdk-reference/batch/process-batch

## Request

### Path parameters

- `batchId` (string, required) — The batch ID.

## Response

### 200

Batch processing state

- `batch_id` (string, optional)
- `completed_at` (string, optional)
- `created_at` (string, optional)
- `ignore_roles` (list of enum, optional)
  - Allowed values: `norole`, `system`, `assistant`, `user`, `function`, `tool`
- `item_count` (integer, optional)
- `metadata` (map from string to any, optional)
- `processed_at` (string, optional)
- `progress` (object, optional)
  - `canceled_items` (integer, optional)
  - `failed_items` (integer, optional)
  - `percent_complete` (double, optional)
  - `processing_items` (integer, optional)
  - `queued_items` (integer, optional)
  - `skipped_items` (integer, optional)
  - `succeeded_items` (integer, optional)
  - `total_items` (integer, optional)
- `status` (enum, optional)
  - Allowed values: `draft`, `invalid`, `queued`, `processing`, `succeeded`, `partial`, `failed`, `canceled`
- `strict_ontology` (boolean, optional)
- `updated_at` (string, optional)

## Examples

**Response**

```json
{
  "batch_id": "string",
  "completed_at": "string",
  "created_at": "string",
  "ignore_roles": [
    "norole"
  ],
  "item_count": 1,
  "metadata": {},
  "processed_at": "string",
  "progress": {
    "canceled_items": 1,
    "failed_items": 1,
    "percent_complete": 1.1,
    "processing_items": 1,
    "queued_items": 1,
    "skipped_items": 1,
    "succeeded_items": 1,
    "total_items": 1
  },
  "status": "draft",
  "strict_ontology": true,
  "updated_at": "string"
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.batch.process("batchId");
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.batch.process(
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
    client.Batch.Process(
        context.TODO(),
        "batchId",
    )
}

```