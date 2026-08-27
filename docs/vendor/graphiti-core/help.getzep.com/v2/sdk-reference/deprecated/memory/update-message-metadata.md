> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Updates the metadata of a message.

PATCH https://api.getzep.com/api/v2/sessions/{sessionId}/messages/{messageUUID}
Content-Type: application/json

Updates the metadata of a message.

Reference: https://help.getzep.com/v2/sdk-reference/deprecated/memory/update-message-metadata

## Request

### Path parameters

- `sessionId` (string, required) — The ID of the session.
- `messageUUID` (string, required) — The UUID of the message.

### Body (application/json)

- `metadata` (map from string to any, required) — Deprecated

## Response

### 200

The updated message.

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
  "content": "Hello, how can I assist you today?",
  "role_type": "assistant",
  "created_at": "2024-06-01T10:15:30Z",
  "metadata": {},
  "processed": true,
  "role": "support_agent",
  "token_count": 42,
  "updated_at": "2024-06-01T10:20:00Z",
  "uuid": "3fa85f64-5717-4562-b3fc-2c963f66afa6"
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.memory.updateMessageMetadata("sessionId", "messageUUID", {});
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.memory.update_message_metadata(
    session_id="sessionId",
    message_uuid="messageUUID",
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
    request := &zep.ModelsMessageMetadataUpdate{}
    client.Memory.UpdateMessageMetadata(
        context.TODO(),
        "sessionId",
        "messageUUID",
        request,
    )
}

```