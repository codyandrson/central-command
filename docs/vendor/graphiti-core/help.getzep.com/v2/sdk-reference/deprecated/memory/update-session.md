> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Update a session.

PATCH https://api.getzep.com/api/v2/sessions/{sessionId}
Content-Type: application/json

Update Session Metadata.

Reference: https://help.getzep.com/v2/sdk-reference/deprecated/memory/update-session

## Request

### Path parameters

- `sessionId` (string, required) — The unique identifier of the session.

### Body (application/json)

- `metadata` (map from string to any, required) — Deprecated
- `fact_rating_instruction` (object, optional) — Optional instruction to use for fact rating. Fact rating instructions can not be unset.
  - `examples` (object, optional) — Examples is a list of examples that demonstrate how facts might be rated based on your instruction. You should provide an example of a highly rated example, a low rated example, and a medium (or in between example). For example, if you are rating based on relevance to a trip planning application, your examples might be: High: "Joe's dream vacation is Bali" Medium: "Joe has a fear of flying", Low: "Joe's favorite food is Japanese",
    - `high` (string, optional)
    - `low` (string, optional)
    - `medium` (string, optional)
  - `instruction` (string, optional) — A string describing how to rate facts as they apply to your application. A trip planning application may use something like "relevancy to planning a trip, the user's preferences when traveling, or the user's travel history."

## Response

### 200

The updated session.

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
  "metadata": {}
}
```

**Response**

```json
{
  "classifications": {},
  "created_at": "2024-06-01T09:15:30Z",
  "deleted_at": "",
  "ended_at": "2024-06-01T10:45:00Z",
  "fact_rating_instruction": {
    "examples": {
      "high": "The user prefers beach destinations for vacations.",
      "low": "The user likes Italian cuisine.",
      "medium": "The user has a moderate interest in hiking activities."
    },
    "instruction": "Rate facts based on their relevance to the user's travel preferences and history."
  },
  "facts": [
    "User booked a trip to Hawaii in 2023."
  ],
  "id": 12345,
  "metadata": {},
  "project_uuid": "a3f1c9e2-7b4d-4f8a-9c2e-1d2f3b4a5c6d",
  "session_id": "session_987654321",
  "updated_at": "2024-06-01T09:45:00Z",
  "user_id": "user_abc123",
  "uuid": "550e8400-e29b-41d4-a716-446655440000"
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.memory.updateSession("sessionId", {});
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.memory.update_session(
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
    request := &zep.UpdateSessionRequest{}
    client.Memory.UpdateSession(
        context.TODO(),
        "sessionId",
        request,
    )
}

```