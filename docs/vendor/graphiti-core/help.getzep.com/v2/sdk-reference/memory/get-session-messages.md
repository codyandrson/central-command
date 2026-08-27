> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Get Messages for Session

GET https://api.getzep.com/api/v2/sessions/{sessionId}/messages

Returns messages for a session.

Reference: https://help.getzep.com/v2/sdk-reference/memory/get-session-messages

## Request

### Path parameters

- `sessionId` (string, required) — Session ID

### Query parameters

- `limit` (integer, optional) — Limit the number of results returned
- `cursor` (integer, optional) — Cursor for pagination

## Response

### 200

OK

- `messages` (list of object, optional) — A list of message objects.
  - `content` (string, required) — The content of the message.
  - `role_type` (enum, required) — The type of the role (e.g., "user", "system").
    - Allowed values: `norole`, `system`, `assistant`, `user`, `function`, `tool`
  - `created_at` (string, optional) — The timestamp of when the message was created.
  - `metadata` (map from string to any, optional) — The metadata associated with the message.
  - `processed` (boolean, optional) — Whether the message has been processed.
  - `role` (string, optional) — Customizable role of the sender of the message (e.g., "john", "sales_agent").
  - `token_count` (integer, optional) — Deprecated
  - `updated_at` (string, optional) — Deprecated
  - `uuid` (string, optional) — The unique identifier of the message.
- `row_count` (integer, optional) — The number of messages returned.
- `total_count` (integer, optional) — The total number of messages.

## Examples

**Request**

```json
{}
```

**Response**

```json
{
  "messages": [
    {
      "content": "Hello, how can I assist you with your account today?",
      "role_type": "assistant",
      "created_at": "2024-06-10T09:15:30Z",
      "metadata": {
        "language": "en",
        "sentiment": "neutral"
      },
      "processed": true,
      "role": "customer_support_agent",
      "token_count": 42,
      "updated_at": "2024-06-10T09:16:00Z",
      "uuid": "3fa85f64-5717-4562-b3fc-2c963f66afa6"
    }
  ],
  "row_count": 1,
  "total_count": 1
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.memory.getSessionMessages("sessionId", {});
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.memory.get_session_messages(
    session_id="sessionId",
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
    request := &zep.MemoryGetSessionMessagesRequest{}
    client.Memory.GetSessionMessages(
        context.TODO(),
        "sessionId",
        request,
    )
}

```