> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Deprecated: Use search_sessions method instead

POST https://api.getzep.com/api/v2/sessions/{sessionId}/search
Content-Type: application/json

Reference: https://help.getzep.com/v2/sdk-reference/deprecated/memory/search

## Request

### Path parameters

- `sessionId` (string, required) — The ID of the session for which memory should be searched.

### Query parameters

- `limit` (integer, optional) — The maximum number of search results to return. Defaults to None (no limit).

### Body (application/json)

- `metadata` (map from string to any, optional) — Metadata Filter
- `min_fact_rating` (double, optional)
- `min_score` (double, optional)
- `mmr_lambda` (double, optional)
- `search_scope` (enum, optional)
  - Allowed values: `messages`, `summary`, `facts`
- `search_type` (enum, optional)
  - Allowed values: `similarity`, `mmr`
- `text` (string, optional)

## Response

### 200

A list of SearchResult objects representing the search results.

- `list of object`
  - `message` (object, optional)
    - `content` (string, required) — The content of the message.
    - `role_type` (enum, required) — The type of the role (e.g., "user", "system").
      - Allowed values: `norole`, `system`, `assistant`, `user`, `function`, `tool`
    - `created_at` (string, optional) — The timestamp of when the message was created.
    - `metadata` (map from string to any, optional) — The metadata associated with the message.
    - `processed` (boolean, optional) — Whether the message has been processed.
    - `role` (string, optional) — Customizable role of the sender of the message (e.g., "john", "sales_agent").
    - `token_count` (integer, optional) — Deprecated
    - `updated_at` (string, optional) — Deprecated
    - `uuid` (string, optional) — The unique identifier of the message.
  - `metadata` (map from string to any, optional)
  - `score` (double, optional)
  - `summary` (object, optional)
    - `content` (string, optional) — The content of the summary.
    - `created_at` (string, optional) — The timestamp of when the summary was created.
    - `metadata` (map from string to any, optional)
    - `related_message_uuids` (list of string, optional)
    - `token_count` (integer, optional) — The number of tokens in the summary.
    - `uuid` (string, optional) — The unique identifier of the summary.

## Examples

**Request**

```json
{}
```

**Response**

```json
[
  {
    "message": {
      "content": "The quarterly sales report shows a 15% increase in revenue compared to the previous quarter.",
      "role_type": "user",
      "created_at": "2024-06-10T09:15:00Z",
      "metadata": {},
      "processed": true,
      "role": "sales_agent",
      "token_count": 28,
      "updated_at": "2024-06-10T09:20:00Z",
      "uuid": "a3f1c9e2-7b4d-4f8a-9c2e-1d2f3b4a5c6d"
    },
    "metadata": {},
    "score": 0.87,
    "summary": {
      "content": "Summary: Sales increased by 15% this quarter.",
      "created_at": "2024-06-10T09:25:00Z",
      "metadata": {},
      "related_message_uuids": [
        "a3f1c9e2-7b4d-4f8a-9c2e-1d2f3b4a5c6d"
      ],
      "token_count": 9,
      "uuid": "b7e2d4f3-8c5a-4d9b-8e3f-2a4b5c6d7e8f"
    }
  }
]
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.memory.search("sessionId", {});
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.memory.search(
    session_id="sessionId",
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
    request := &zep.MemorySearchPayload{}
    client.Memory.Search(
        context.TODO(),
        "sessionId",
        request,
    )
}

```