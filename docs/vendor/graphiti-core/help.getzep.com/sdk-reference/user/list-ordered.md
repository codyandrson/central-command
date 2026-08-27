> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Get Users

GET https://api.getzep.com/api/v2/users-ordered

Returns all users.

Reference: https://help.getzep.com/sdk-reference/user/list-ordered

## Request

### Query parameters

- `pageNumber` (integer, optional) — Page number for pagination, starting from 1
- `pageSize` (integer, optional) — Number of users to retrieve per page
- `search` (string, optional) — Search term for filtering users by user_id, name, or email
- `order_by` (string, optional) — Column to sort by (created_at, user_id, email)
- `asc` (boolean, optional) — Sort in ascending order

## Response

### 200

Successfully retrieved list of users

- `row_count` (integer, optional)
- `total_count` (integer, optional)
- `users` (list of object, optional)
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
  "row_count": 1,
  "total_count": 1,
  "users": [
    {
      "created_at": "2024-05-20T09:15:00Z",
      "deleted_at": null,
      "disable_default_ontology": false,
      "email": "jane.doe@example.com",
      "first_name": "Jane",
      "id": 1024,
      "last_name": "Doe",
      "metadata": {},
      "project_uuid": "a3f1c9e2-7b4d-4f8a-9c3e-2d5f6a7b8c9d",
      "session_count": 42,
      "time_zone": "America/New_York",
      "updated_at": "2024-06-01T12:00:00Z",
      "user_id": "user_1024",
      "uuid": "550e8400-e29b-41d4-a716-446655440000"
    }
  ]
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.user.listOrdered({});
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.user.list_ordered()

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
    request := &zep.UserListOrderedRequest{}
    client.User.ListOrdered(
        context.TODO(),
        request,
    )
}

```