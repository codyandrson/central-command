> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Create Batch

POST https://api.getzep.com/api/v2/batches
Content-Type: application/json

Create a draft batch that can be filled with graph episodes and thread messages.

Reference: https://help.getzep.com/sdk-reference/batch/create-batch

## Request

### Body (application/json)

- `ignore_roles` (list of enum, optional) — Optional list of message role types to skip during graph ingestion for thread_message items in this batch. The messages are still stored and retained as context, but no graph extraction is performed for them. Has no effect on graph_episode items.
  - Allowed values: `norole`, `system`, `assistant`, `user`, `function`, `tool`
- `metadata` (map from string to any, optional)
- `strict_ontology` (boolean, optional) — When true, prevents extraction of generic Entity nodes that do not match the configured ontology.

## Response

### 200

Created batch

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

**Request**

```json
{}
```

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
    await client.batch.create({});
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.batch.create()

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
    request := &zep.ApidataCreateBatchRequest{}
    client.Batch.Create(
        context.TODO(),
        request,
    )
}

```