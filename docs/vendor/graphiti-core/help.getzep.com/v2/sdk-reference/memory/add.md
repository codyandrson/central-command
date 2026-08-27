> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Add Memory to Session

POST https://api.getzep.com/api/v2/sessions/{sessionId}/memory
Content-Type: application/json

Add memory to the specified session.

Reference: https://help.getzep.com/v2/sdk-reference/memory/add

## Request

### Path parameters

- `sessionId` (string, required) — The ID of the session to which memory should be added.

### Body (application/json)

- `messages` (list of object, required) — A list of message objects, where each message contains a role and content.
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
- `fact_instruction` (string, optional) — Deprecated
- `ignore_roles` (list of enum, optional) — Optional list of role types to ignore when adding messages to graph memory. The message itself will still be added, retained and used as context for messages that are added to a user's graph.
  - Allowed values: `norole`, `system`, `assistant`, `user`, `function`, `tool`
- `return_context` (boolean, optional) — Optionally return memory context relevant to the most recent messages.
- `summary_instruction` (string, optional) — Deprecated

## Response

### 200

An object, optionally containing memory context retrieved for the last message

- `context` (string, optional)

## Examples

**Request**

```json
{
  "messages": [
    {
      "content": "Remember to follow up with the client about the project deadline next week.",
      "role_type": "user"
    }
  ]
}
```

**Response**

```json
{
  "context": "The memory has been updated with the latest client follow-up reminder."
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.memory.add("sessionId", {
        messages: [
            {
                content: "Remember to follow up with the client about the project deadline next week.",
                roleType: "user",
            },
        ],
    });
}
main();

```

```python
from zep_cloud import Zep, Message

client = Zep()

client.memory.add(
    session_id="sessionId",
    messages=[
        Message(
            content="Remember to follow up with the client about the project deadline next week.",
            role_type="user",
        )
    ],
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
    request := &zep.AddMemoryRequest{
        Messages: []*zep.Message{
            &zep.Message{
                Content: "Remember to follow up with the client about the project deadline next week.",
                RoleType: zep.RoleTypeUserRole,
            },
        },
    }
    client.Memory.Add(
        context.TODO(),
        "sessionId",
        request,
    )
}

```