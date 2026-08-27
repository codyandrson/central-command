> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Add Batch Items

POST https://api.getzep.com/api/v2/batches/{batchId}/items
Content-Type: application/json

Add graph episodes and thread messages to a draft batch. Items are appended in request order.

Reference: https://help.getzep.com/sdk-reference/batch/add-batch-items

## Request

### Path parameters

- `batchId` (string, required) — The batch ID.

### Body (application/json)

- `items` (list of object, required)
  - `type` (enum, required)
    - Allowed values: `graph_episode`, `thread_message`
  - `content` (string, optional)
  - `created_at` (string, optional)
  - `data` (string, optional)
  - `data_type` (enum, optional)
    - Allowed values: `text`, `json`, `message`, `fact_triple`
  - `document_id` (string, optional) — Optional document ID for graph_episode items. Groups episodes as document chunks. Ignored for thread_message items.
  - `graph_id` (string, optional)
  - `metadata` (map from string to any, optional)
  - `name` (string, optional)
  - `role` (enum, optional)
    - Allowed values: `norole`, `system`, `assistant`, `user`, `function`, `tool`
  - `source_description` (string, optional)
  - `thread_id` (string, optional)
  - `user_id` (string, optional)

## Response

### 200

Added batch items

- `list of object`
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

## Examples

**Request**

```json
{
  "items": [
    {
      "type": "graph_episode"
    }
  ]
}
```

**Response**

```json
[
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
]
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.batch.add("batchId", {
        items: [
            {
                type: "graph_episode",
            },
        ],
    });
}
main();

```

```python
from zep_cloud import Zep, BatchAddItem

client = Zep()

client.batch.add(
    batch_id="batchId",
    items=[
        BatchAddItem(
            type="graph_episode",
        )
    ],
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
    request := &zep.ApidataAddBatchItemsRequest{
        Items: []*zep.BatchAddItem{
            &zep.BatchAddItem{
                Type: zep.ApidataBatchAddItemTypeGraphEpisode,
            },
        },
    }
    client.Batch.Add(
        context.TODO(),
        "batchId",
        request,
    )
}

```