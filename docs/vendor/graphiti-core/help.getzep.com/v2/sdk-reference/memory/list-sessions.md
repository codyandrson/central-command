> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Get Sessions

GET https://api.getzep.com/api/v2/sessions-ordered

Returns all sessions.

Reference: https://help.getzep.com/v2/sdk-reference/memory/list-sessions

## Request

### Query parameters

- `page_number` (integer, optional) — Page number for pagination, starting from 1
- `page_size` (integer, optional) — Number of sessions to retrieve per page.
- `order_by` (string, optional) — Field to order the results by: created_at, updated_at, user_id, session_id.
- `asc` (boolean, optional) — Order direction: true for ascending, false for descending.

## Response

### 200

List of sessions

- `response_count` (integer, optional)
- `sessions` (list of object, optional)
  - `classifications` (map from string to string, optional)
  - `created_at` (string, optional)
  - `deleted_at` (string, optional)
  - `ended_at` (string, optional)
  - `fact_rating_instruction` (object, optional) — Deprecated
    - `examples` (object, optional) — Examples is a list of examples that demonstrate how facts might be rated based on your instruction. You should provide an example of a highly rated example, a low rated example, and a medium (or in between example). For example, if you are rating based on relevance to a trip planning application, your examples might be: High: "Joe's dream vacation is Bali" Medium: "Joe has a fear of flying", Low: "Joe's favorite food is Japanese",
      - `high` (string, optional)
      - `low` (string, optional)
      - `medium` (string, optional)
    - `instruction` (string, optional) — A string describing how to rate facts as they apply to your application. A trip planning application may use something like "relevancy to planning a trip, the user's preferences when traveling, or the user's travel history."
  - `facts` (list of string, optional) — Deprecated
  - `id` (integer, optional)
  - `metadata` (map from string to any, optional) — Deprecated
  - `project_uuid` (string, optional)
  - `session_id` (string, optional)
  - `updated_at` (string, optional) — Deprecated
  - `user_id` (string, optional)
  - `uuid` (string, optional)
- `total_count` (integer, optional)

## Examples

**Request**

```json
{}
```

**Response**

```json
{
  "response_count": 1,
  "sessions": [
    {
      "classifications": {
        "topic": "customer_support",
        "priority": "high"
      },
      "created_at": "2024-06-10T09:15:00Z",
      "deleted_at": null,
      "ended_at": "2024-06-10T10:00:00Z",
      "fact_rating_instruction": {
        "examples": {
          "high": "The user requested a refund for order #12345",
          "low": "The user mentioned the weather",
          "medium": "The user asked about shipping times"
        },
        "instruction": "Rate facts based on their relevance to customer support issue resolution."
      },
      "facts": [
        "User reported a defective product",
        "User requested expedited shipping"
      ],
      "id": 987654,
      "metadata": {
        "platform": "web",
        "agent_id": "agent_42"
      },
      "project_uuid": "a3f1c9d2-4b7e-4f8a-9c3d-2e5f7b8a1c9d",
      "session_id": "sess_20240610_0915",
      "updated_at": "2024-06-10T09:45:00Z",
      "user_id": "user_123456",
      "uuid": "f47ac10b-58cc-4372-a567-0e02b2c3d479"
    }
  ],
  "total_count": 1
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.memory.listSessions({});
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.memory.list_sessions()

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
    request := &zep.MemoryListSessionsRequest{}
    client.Memory.ListSessions(
        context.TODO(),
        request,
    )
}

```