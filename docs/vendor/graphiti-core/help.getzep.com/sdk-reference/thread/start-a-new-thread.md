> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Start a new thread.

POST https://api.getzep.com/api/v2/threads
Content-Type: application/json

Start a new thread.

Reference: https://help.getzep.com/sdk-reference/thread/start-a-new-thread

## Request

### Body (application/json)

- `thread_id` (string, required) — The unique identifier of the thread.
- `user_id` (string, required) — The unique identifier of the user associated with the thread

## Response

### 201

The thread object.

- `created_at` (string, optional)
- `project_uuid` (string, optional)
- `thread_id` (string, optional)
- `user_id` (string, optional)
- `user_uuid` (string, optional)
- `uuid` (string, optional)

## Examples

**Request**

```json
{
  "thread_id": "thread-12345",
  "user_id": "user-67890"
}
```

**Response**

```json
{
  "created_at": "2024-06-01T12:00:00Z",
  "project_uuid": "a3f1c9e2-4b7d-4f8a-9c2e-123456789abc",
  "thread_id": "thread-12345",
  "user_id": "user-67890",
  "user_uuid": "d4e5f6a7-b8c9-4d0e-9f1a-abcdef123456",
  "uuid": "e7f8g9h0-1a2b-3c4d-5e6f-7890abcdef12"
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.thread.create({
        threadId: "thread-12345",
        userId: "user-67890",
    });
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.thread.create(
    thread_id="thread-12345",
    user_id="user-67890",
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
    request := &zep.CreateThreadRequest{
        ThreadID: "thread-12345",
        UserID: "user-67890",
    }
    client.Thread.Create(
        context.TODO(),
        request,
    )
}

```