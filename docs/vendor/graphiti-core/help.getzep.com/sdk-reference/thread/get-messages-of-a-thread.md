> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Get messages of a thread

GET https://api.getzep.com/api/v2/threads/{threadId}/messages

Returns messages for a thread.

Reference: https://help.getzep.com/sdk-reference/thread/get-messages-of-a-thread

## Request

### Path parameters

- `threadId` (string, required) — Thread ID

### Query parameters

- `limit` (integer, optional) — Limit the number of results returned
- `cursor` (long, optional) — Cursor for pagination
- `lastn` (integer, optional) — Number of most recent messages to return (overrides limit and cursor)

## Response

### 200

OK

- `messages` (list of object, optional) — A list of message objects.
  - `content` (string, required) — The content of the message.
  - `role` (enum, required) — The role of message sender (e.g., "user", "system").
    - Allowed values: `norole`, `system`, `assistant`, `user`, `function`, `tool`
  - `created_at` (string, optional) — The timestamp of when the message was created.
  - `metadata` (map from string to any, optional) — The metadata associated with the message.
  - `name` (string, optional) — Customizable name of the sender of the message (e.g., "john", "sales_agent").
  - `processed` (boolean, optional) — Whether the message has been processed.
  - `uuid` (string, optional) — The unique identifier of the message.
- `row_count` (integer, optional) — The number of messages returned.
- `thread_created_at` (string, optional) — The thread creation timestamp.
- `total_count` (integer, optional) — The total number of messages.
- `user_id` (string, optional) — The user ID associated with this thread.
- `user_uuid` (string, optional) — The opaque user identifier used by dashboard routes.

## Examples

**Response**

```json
{
  "messages": [
    {
      "content": "string",
      "role": "norole",
      "created_at": "string",
      "metadata": {},
      "name": "string",
      "processed": true,
      "uuid": "string"
    }
  ],
  "row_count": 1,
  "thread_created_at": "string",
  "total_count": 1,
  "user_id": "string",
  "user_uuid": "string"
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.thread.get("threadId", {});
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.thread.get(
    thread_id="threadId",
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
    request := &zep.ThreadGetRequest{}
    client.Thread.Get(
        context.TODO(),
        "threadId",
        request,
    )
}

```