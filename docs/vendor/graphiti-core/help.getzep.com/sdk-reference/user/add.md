> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Add User

POST https://api.getzep.com/api/v2/users
Content-Type: application/json

Adds a user.

Reference: https://help.getzep.com/sdk-reference/user/add

## Request

### Body (application/json)

- `user_id` (string, required) — The unique identifier of the user.
- `disable_default_ontology` (boolean, optional) — When true, disables the use of default/fallback ontology for the user's graph.
- `email` (string, optional) — The email address of the user.
- `first_name` (string, optional) — The first name of the user.
- `last_name` (string, optional) — The last name of the user.
- `metadata` (map from string to any, optional) — The metadata associated with the user.
- `time_zone` (string, optional, nullable) — The user's IANA time zone. Null or omission leaves it unset at creation.

## Response

### 201

The user that was added.

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
{
  "user_id": "user_12345"
}
```

**Response**

```json
{
  "created_at": "2024-06-01T12:00:00Z",
  "deleted_at": "",
  "disable_default_ontology": false,
  "email": "jane.doe@example.com",
  "first_name": "Jane",
  "id": 1024,
  "last_name": "Doe",
  "metadata": {},
  "project_uuid": "a3f1c9e2-7b4d-4f8a-9c2e-123456789abc",
  "session_count": 5,
  "time_zone": "America/New_York",
  "updated_at": "2024-06-10T15:30:00Z",
  "user_id": "user_12345",
  "uuid": "550e8400-e29b-41d4-a716-446655440000"
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.user.add({
        userId: "user_12345",
    });
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.user.add(
    user_id="user_12345",
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
    request := &zep.CreateUserRequest{
        UserID: "user_12345",
    }
    client.User.Add(
        context.TODO(),
        request,
    )
}

```