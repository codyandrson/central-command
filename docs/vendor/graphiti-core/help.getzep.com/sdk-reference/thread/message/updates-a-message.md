> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Updates a message.

PATCH https://api.getzep.com/api/v2/messages/{messageUUID}
Content-Type: application/json

Updates a message.

Reference: https://help.getzep.com/sdk-reference/thread/message/updates-a-message

## Request

### Path parameters

- `messageUUID` (string, required) — The UUID of the message.

### Body (application/json)

- `metadata` (map from string to any, required)

## Response

### 200

The updated message.

- `content` (string, required) — The content of the message.
- `role` (enum, required) — The role of message sender (e.g., "user", "system").
  - Allowed values: `norole`, `system`, `assistant`, `user`, `function`, `tool`
- `created_at` (string, optional) — The timestamp of when the message was created.
- `metadata` (map from string to any, optional) — The metadata associated with the message.
- `name` (string, optional) — Customizable name of the sender of the message (e.g., "john", "sales_agent").
- `processed` (boolean, optional) — Whether the message has been processed.
- `uuid` (string, optional) — The unique identifier of the message.

## Examples

**Request**

```json
{
  "metadata": {}
}
```

**Response**

```json
{
  "content": "Looking forward to our meeting tomorrow at 10 AM.",
  "role": "user",
  "created_at": "2024-06-10T09:15:00Z",
  "metadata": {},
  "name": "alice",
  "processed": true,
  "uuid": "3fa85f64-5717-4562-b3fc-2c963f66afa6"
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.thread.message.update("messageUUID", {});
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.thread.message.update(
    message_uuid="messageUUID",
)

```

```go
package example

import (
    context "context"

    client "github.com/getzep/zep-go/client"
    thread "github.com/getzep/zep-go/thread"
)

func do() {
    client := client.NewClient()
    request := &thread.ThreadMessageUpdate{}
    client.Thread.Message.Update(
        context.TODO(),
        "messageUUID",
        request,
    )
}

```