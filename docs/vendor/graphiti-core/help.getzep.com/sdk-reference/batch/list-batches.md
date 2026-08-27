> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# List Batches

GET https://api.getzep.com/api/v2/batches

List batches for the current project, optionally filtered by batch status.

Reference: https://help.getzep.com/sdk-reference/batch/list-batches

## Request

### Query parameters

- `limit` (integer, optional) — Maximum number of batches to return.
- `cursor` (integer, optional) — Pagination cursor from a previous response.
- `status` (string, optional) — Batch status filter.

## Response

### 200

Batch list

- `batches` (list of object, optional)
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
- `next_cursor` (integer, optional)

## Examples

**Response**

```json
{
  "batches": [
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
  ],
  "next_cursor": 1
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.batch.list({});
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.batch.list()

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
    request := &zep.BatchListRequest{}
    client.Batch.List(
        context.TODO(),
        request,
    )
}

```