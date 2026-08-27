> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Update User

PATCH https://api.getzep.com/api/v2/users/{userId}
Content-Type: application/json

Updates a user.

Reference: https://help.getzep.com/sdk-reference/user/update

## Request

### Path parameters

- `userId` (string, required) — User ID

### Body (application/json)

- `disable_default_ontology` (boolean, optional) — When true, disables the use of default/fallback ontology for the user's graph.
- `email` (string, optional) — The email address of the user.
- `first_name` (string, optional) — The first name of the user.
- `last_name` (string, optional) — The last name of the user.
- `metadata` (map from string to any, optional) — The metadata to update
- `time_zone` (string, optional, nullable) — The user's IANA time zone. Null clears the existing value.

## Response

### 200

The user that was updated.

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
  "created_at": "2023-11-01T09:15:30Z",
  "deleted_at": "",
  "disable_default_ontology": false,
  "email": "jane.doe@example.com",
  "first_name": "Jane",
  "id": 12345,
  "last_name": "Doe",
  "metadata": {},
  "project_uuid": "a3f1c9e2-7b4d-4f8a-9c2e-1d2f3b4a5c6d",
  "session_count": 42,
  "time_zone": "America/New_York",
  "updated_at": "2024-05-20T16:45:00Z",
  "user_id": "user_987654321",
  "uuid": "d290f1ee-6c54-4b01-90e6-d701748f0851"
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.user.update("userId", {});
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.user.update(
    user_id="userId",
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
    request := &zep.UpdateUserRequest{}
    client.User.Update(
        context.TODO(),
        "userId",
        request,
    )
}

```