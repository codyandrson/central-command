> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Get User

GET https://api.getzep.com/api/v2/users/{userId}

Returns a user.

Reference: https://help.getzep.com/sdk-reference/user/get

## Request

### Path parameters

- `userId` (string, required) — The user_id of the user to get.

## Response

### 200

The user that was retrieved.

- `created_at` (string, optional)
- `deleted_at` (string, optional)
- `disable_default_ontology` (boolean, optional)
- `email` (string, optional)
- `first_name` (string, optional)
- `id` (integer, optional)
- `last_name` (string, optional)
- `metadata` (map from string to any, optional) — Deprecated
- `project_uuid` (string, optional)
- `session_count` (integer, optional) — Deprecated
- `time_zone` (string, optional, nullable)
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
  "created_at": "2023-11-10T09:15:00Z",
  "deleted_at": null,
  "disable_default_ontology": false,
  "email": "jane.doe@example.com",
  "first_name": "Jane",
  "id": 12345,
  "last_name": "Doe",
  "metadata": {
    "preferred_language": "en",
    "subscription_level": "premium"
  },
  "project_uuid": "a3f1c9e2-7b4d-4f8a-9c2e-1d2f3b4a5c6d",
  "session_count": 42,
  "time_zone": "America/New_York",
  "updated_at": "2024-05-20T16:45:00Z",
  "user_id": "user_987654321",
  "uuid": "f47ac10b-58cc-4372-a567-0e02b2c3d479"
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.user.get("userId");
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.user.get(
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
    client.User.Get(
        context.TODO(),
        "userId",
    )
}

```