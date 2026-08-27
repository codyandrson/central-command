> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Search sessions for the specified query.

POST https://api.getzep.com/api/v2/sessions/search
Content-Type: application/json

Deprecated API: Search sessions for the specified query.

Reference: https://help.getzep.com/v2/sdk-reference/deprecated/memory/search-sessions

## Request

### Query parameters

- `limit` (integer, optional) — The maximum number of search results to return. Defaults to None (no limit).

### Body (application/json)

- `text` (string, required) — The search text.
- `min_fact_rating` (double, optional) — The minimum fact rating to filter on.
- `min_score` (double, optional) — The minimum score for search results.
- `mmr_lambda` (double, optional) — The lambda parameter for the MMR Reranking Algorithm.
- `record_filter` (map from string to any, optional) — Record filter on the metadata.
- `search_scope` (enum, optional) — Search scope.
  - Allowed values: `messages`, `summary`, `facts`
- `search_type` (enum, optional) — Search type.
  - Allowed values: `similarity`, `mmr`
- `session_ids` (list of string, optional) — the session ids to search
- `user_id` (string, optional) — User ID used to determine which sessions to search.

## Response

### 200

A SessionSearchResponse object representing the search results.

- `results` (list of object, optional)
  - `fact` (object, optional)
    - `content` (string, required)
    - `created_at` (string, required)
    - `fact` (string, required) — Deprecated
    - `uuid` (string, required)
    - `expired_at` (string, optional)
    - `invalid_at` (string, optional)
    - `name` (string, optional)
    - `rating` (double, optional)
    - `source_node_name` (string, optional)
    - `target_node_name` (string, optional)
    - `valid_at` (string, optional)
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
  - `score` (double, optional)
  - `session_id` (string, optional)
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
{
  "text": "Find sessions discussing machine learning advancements"
}
```

**Response**

```json
{
  "results": [
    {
      "fact": {
        "content": "Machine learning models have improved accuracy by 15% in the last year.",
        "created_at": "2024-05-10T09:30:00Z",
        "fact": "Deprecated",
        "uuid": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "expired_at": "2025-05-10T09:30:00Z",
        "invalid_at": "2024-06-01T00:00:00Z",
        "name": "ML Accuracy Improvement",
        "rating": 4.7,
        "source_node_name": "Research Paper A",
        "target_node_name": "Model Evaluation",
        "valid_at": "2024-05-10T09:30:00Z"
      },
      "message": {
        "content": "The latest research shows significant improvements in model accuracy.",
        "role_type": "user",
        "created_at": "2024-05-10T09:35:00Z",
        "metadata": {},
        "processed": true,
        "role": "data_scientist",
        "token_count": 25,
        "updated_at": "2024-05-10T09:40:00Z",
        "uuid": "123e4567-e89b-12d3-a456-426614174000"
      },
      "score": 0.95,
      "session_id": "session-9876543210",
      "summary": {
        "content": "Summary of recent machine learning model accuracy improvements.",
        "created_at": "2024-05-10T10:00:00Z",
        "metadata": {},
        "related_message_uuids": [
          "123e4567-e89b-12d3-a456-426614174000"
        ],
        "token_count": 15,
        "uuid": "summary-1234-5678-9012-3456"
      }
    }
  ]
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.memory.searchSessions({
        text: "Find sessions discussing machine learning advancements",
    });
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.memory.search_sessions(
    text="Find sessions discussing machine learning advancements",
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
    request := &zep.SessionSearchQuery{
        Text: "Find sessions discussing machine learning advancements",
    }
    client.Memory.SearchSessions(
        context.TODO(),
        request,
    )
}

```