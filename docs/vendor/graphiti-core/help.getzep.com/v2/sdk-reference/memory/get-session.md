> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Get Session.

GET https://api.getzep.com/api/v2/sessions/{sessionId}

Returns a session.

Reference: https://help.getzep.com/v2/sdk-reference/memory/get-session

## Request

### Path parameters

- `sessionId` (string, required) — The unique identifier of the session.

## Response

### 200

The session with the specified ID.

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
{}
```

**Response**

```json
{
  "classifications": {
    "topic": "technology",
    "priority": "high"
  },
  "created_at": "2024-06-01T09:15:00Z",
  "deleted_at": null,
  "ended_at": "2024-06-01T10:00:00Z",
  "fact_rating_instruction": {
    "examples": {
      "high": "The session contains detailed user activity logs relevant to troubleshooting.",
      "low": "The session data is mostly empty or unrelated to current user activity.",
      "medium": "The session includes some user preferences but lacks recent updates."
    },
    "instruction": "Rate facts based on their relevance to user session activity and troubleshooting."
  },
  "facts": [
    "User logged in from IP 192.168.1.10",
    "Session timeout set to 30 minutes"
  ],
  "id": 12345,
  "metadata": {
    "device": "iPhone 13",
    "location": "New York, USA"
  },
  "project_uuid": "a3f1c9e2-4b7d-4f8a-9c2e-1d2f3b4a5c6d",
  "session_id": "sess-7890abcd1234",
  "updated_at": "2024-06-01T09:45:00Z",
  "user_id": "user-5678efgh",
  "uuid": "d290f1ee-6c54-4b01-90e6-d701748f0851"
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.memory.getSession("sessionId");
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.memory.get_session(
    session_id="sessionId",
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
    client.Memory.GetSession(
        context.TODO(),
        "sessionId",
    )
}

```