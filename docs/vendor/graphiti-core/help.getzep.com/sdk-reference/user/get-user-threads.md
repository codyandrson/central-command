> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Get User Threads

GET https://api.getzep.com/api/v2/users/{userId}/threads

Returns all threads for a user.

Reference: https://help.getzep.com/sdk-reference/user/get-user-threads

## Request

### Path parameters

- `userId` (string, required) — User ID

## Response

### 200

OK

- `list of object`
  - `created_at` (string, optional)
  - `project_uuid` (string, optional)
  - `thread_id` (string, optional)
  - `user_id` (string, optional)
  - `user_uuid` (string, optional)
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
    "created_at": "2024-06-10T09:15:00Z",
    "project_uuid": "a3f1c9d2-4b7e-4f8a-9c2d-1e5b7f3a9c8e",
    "thread_id": "thread-987654321",
    "user_id": "user-12345",
    "user_uuid": "d2f4e6a8-9b7c-4d3e-8f1a-2b3c4d5e6f7a",
    "uuid": "f1e2d3c4-b5a6-7d8e-9f0a-b1c2d3e4f5a6"
  }
]
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.user.getThreads("userId");
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.user.get_threads(
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
    client.User.GetThreads(
        context.TODO(),
        "userId",
    )
}

```