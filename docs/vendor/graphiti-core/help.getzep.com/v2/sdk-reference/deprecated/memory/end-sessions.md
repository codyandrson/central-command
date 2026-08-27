> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# End multiple sessions.

POST https://api.getzep.com/api/v2/sessions/end
Content-Type: application/json

Deprecated API: End multiple sessions by their IDs.

Reference: https://help.getzep.com/v2/sdk-reference/deprecated/memory/end-sessions

## Request

### Body (application/json)

- `session_ids` (list of string, required)
- `instruction` (string, optional)

## Response

### 200

OK

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

## Examples

**Request**

```json
{
  "session_ids": [
    "a1b2c3d4-e5f6-7890-ab12-cd34ef567890",
    "b2c3d4e5-f678-9012-ab34-cd56ef789012"
  ]
}
```

**Response**

```json
{
  "sessions": [
    {
      "classifications": {},
      "created_at": "2024-05-20T10:15:30Z",
      "deleted_at": "",
      "ended_at": "2024-05-20T11:00:00Z",
      "fact_rating_instruction": {
        "examples": {
          "high": "User's preference for Italian restaurants in Rome",
          "low": "User's favorite color is blue",
          "medium": "User has visited Rome twice before"
        },
        "instruction": "Rate facts based on their relevance to travel planning and user preferences."
      },
      "facts": [
        "User booked a flight to Rome",
        "User searched for Italian restaurants"
      ],
      "id": 12345,
      "metadata": {},
      "project_uuid": "d4e5f678-9012-3456-ab78-cd90ef123456",
      "session_id": "a1b2c3d4-e5f6-7890-ab12-cd34ef567890",
      "updated_at": "2024-05-20T10:45:00Z",
      "user_id": "user_7890",
      "uuid": "f1234567-89ab-4cde-f012-3456789abcde"
    }
  ]
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.memory.endSessions({
        sessionIds: [
            "a1b2c3d4-e5f6-7890-ab12-cd34ef567890",
            "b2c3d4e5-f678-9012-ab34-cd56ef789012",
        ],
    });
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.memory.end_sessions(
    session_ids=[
        "a1b2c3d4-e5f6-7890-ab12-cd34ef567890",
        "b2c3d4e5-f678-9012-ab34-cd56ef789012"
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
    request := &zep.EndSessionsRequest{
        SessionIDs: []string{
            "a1b2c3d4-e5f6-7890-ab12-cd34ef567890",
            "b2c3d4e5-f678-9012-ab34-cd56ef789012",
        },
    }
    client.Memory.EndSessions(
        context.TODO(),
        request,
    )
}

```