> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Get User Sessions

GET https://api.getzep.com/api/v2/users/{userId}/sessions

Returns all sessions for a user.

Reference: https://help.getzep.com/v2/sdk-reference/user/get-sessions

## Request

### Path parameters

- `userId` (string, required) — User ID

## Response

### 200

OK

- `list of object`
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
[
  {
    "classifications": {
      "topic": "technology",
      "sentiment": "neutral"
    },
    "created_at": "2024-05-10T09:15:00Z",
    "deleted_at": null,
    "ended_at": "2024-05-10T10:00:00Z",
    "fact_rating_instruction": {
      "examples": {
        "high": "The user frequently discusses cloud computing technologies.",
        "low": "The user likes to watch cooking shows on weekends.",
        "medium": "The user mentioned attending a tech conference last year."
      },
      "instruction": "Rate facts based on their relevance to the user's technology interests and activities."
    },
    "facts": [
      "User has a subscription to a cloud service provider."
    ],
    "id": 1024,
    "metadata": {
      "device": "laptop",
      "location": "New York"
    },
    "project_uuid": "a3f1c9d2-7b4e-4f8a-9c3d-2e5b6f7a8c9d",
    "session_id": "sess-20240510-0915",
    "updated_at": "2024-05-10T09:45:00Z",
    "user_id": "user-7890",
    "uuid": "d290f1ee-6c54-4b01-90e6-d701748f0851"
  }
]
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.user.getSessions("userId");
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.user.get_sessions(
    user_id="userId",
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
    client.User.GetSessions(
        context.TODO(),
        "userId",
    )
}

```