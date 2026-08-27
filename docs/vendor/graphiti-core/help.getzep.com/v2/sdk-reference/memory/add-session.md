> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Add Session

POST https://api.getzep.com/api/v2/sessions
Content-Type: application/json

Creates a new session.

Reference: https://help.getzep.com/v2/sdk-reference/memory/add-session

## Request

### Body (application/json)

- `session_id` (string, required) — The unique identifier of the session.
- `user_id` (string, required) — The unique identifier of the user associated with the session
- `fact_rating_instruction` (object, optional) — Deprecated
  - `examples` (object, optional) — Examples is a list of examples that demonstrate how facts might be rated based on your instruction. You should provide an example of a highly rated example, a low rated example, and a medium (or in between example). For example, if you are rating based on relevance to a trip planning application, your examples might be: High: "Joe's dream vacation is Bali" Medium: "Joe has a fear of flying", Low: "Joe's favorite food is Japanese",
    - `high` (string, optional)
    - `low` (string, optional)
    - `medium` (string, optional)
  - `instruction` (string, optional) — A string describing how to rate facts as they apply to your application. A trip planning application may use something like "relevancy to planning a trip, the user's preferences when traveling, or the user's travel history."
- `metadata` (map from string to any, optional) — Deprecated

## Response

### 201

The added session.

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
  "session_id": "session_9f8b7c6d5e4a3b2c1d0e",
  "user_id": "user_1234567890abcdef"
}
```

**Response**

```json
{
  "classifications": {},
  "created_at": "2024-06-01T12:00:00Z",
  "deleted_at": "",
  "ended_at": "",
  "fact_rating_instruction": {
    "examples": {
      "high": "User's travel preferences align perfectly with the recommended itinerary.",
      "low": "User's favorite color is blue.",
      "medium": "User has visited similar destinations in the past."
    },
    "instruction": "Rate facts based on their relevance to the user's travel planning and preferences."
  },
  "facts": [
    "User prefers beach destinations during summer."
  ],
  "id": 1024,
  "metadata": {},
  "project_uuid": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "session_id": "session_9f8b7c6d5e4a3b2c1d0e",
  "updated_at": "2024-06-01T12:30:00Z",
  "user_id": "user_1234567890abcdef",
  "uuid": "550e8400-e29b-41d4-a716-446655440000"
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.memory.addSession({
        sessionId: "session_9f8b7c6d5e4a3b2c1d0e",
        userId: "user_1234567890abcdef",
    });
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.memory.add_session(
    session_id="session_9f8b7c6d5e4a3b2c1d0e",
    user_id="user_1234567890abcdef",
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
    request := &zep.CreateSessionRequest{
        SessionID: "session_9f8b7c6d5e4a3b2c1d0e",
        UserID: "user_1234567890abcdef",
    }
    client.Memory.AddSession(
        context.TODO(),
        request,
    )
}

```