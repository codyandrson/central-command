> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# List Batch Items

GET https://api.getzep.com/api/v2/batches/{batchId}/items

List items in a batch, including derived runtime status when the batch has been processed.

Reference: https://help.getzep.com/sdk-reference/batch/list-batch-items

## Request

### Path parameters

- `batchId` (string, required) — The batch ID.

### Query parameters

- `limit` (integer, optional) — Maximum number of batch items to return.
- `cursor` (integer, optional) — Pagination cursor from a previous response.
- `status` (string, optional) — Batch item status filter.

## Response

### 200

Batch item list

- `items` (list of object, optional)
  - `created_at` (string, optional)
  - `document_id` (string, optional)
  - `episode_uuid` (string, optional) — EpisodeUUID is the UUID of the episode that will be (or has been) created for this batch item. Populated for every item kind and always equal to SourceUUID — the underlying source row's UUID is reused as the episode UUID during processing.
  - `error` (map from string to any, optional)
  - `graph_id` (string, optional)
  - `graph_uuid` (string, optional)
  - `item_id` (string, optional)
  - `kind` (enum, optional)
    - Allowed values: `graph_episode`, `thread_message`
  - `sequence_index` (integer, optional)
  - `source_uuid` (string, optional)
  - `status` (enum, optional)
    - Allowed values: `pending`, `queued`, `processing`, `succeeded`, `failed`, `skipped`, `canceled`
  - `thread_id` (string, optional)
  - `updated_at` (string, optional)
  - `user_id` (string, optional)
  - `user_uuid` (string, optional)
- `next_cursor` (integer, optional)

## Examples

**Response**

```json
{
  "items": [
    {
      "created_at": "string",
      "document_id": "string",
      "episode_uuid": "string",
      "error": {},
      "graph_id": "string",
      "graph_uuid": "string",
      "item_id": "string",
      "kind": "graph_episode",
      "sequence_index": 1,
      "source_uuid": "string",
      "status": "pending",
      "thread_id": "string",
      "updated_at": "string",
      "user_id": "string",
      "user_uuid": "string"
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
    await client.batch.listItems("batchId", {});
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.batch.list_items(
    batch_id="batchId",
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
    request := &zep.BatchListItemsRequest{}
    client.Batch.ListItems(
        context.TODO(),
        "batchId",
        request,
    )
}

```