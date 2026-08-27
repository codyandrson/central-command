> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Add messages to a thread

POST https://api.getzep.com/api/v2/threads/{threadId}/messages
Content-Type: application/json

Add messages to a thread.

Reference: https://help.getzep.com/sdk-reference/thread/add-messages-to-a-thread

## Request

### Path parameters

- `threadId` (string, required) — The ID of the thread to which messages should be added.

### Body (application/json)

- `messages` (list of object, required) — A list of message objects, where each message contains a role and content.
  - `content` (string, required) — The content of the message.
  - `role` (enum, required) — The role of message sender (e.g., "user", "system").
    - Allowed values: `norole`, `system`, `assistant`, `user`, `function`, `tool`
  - `created_at` (string, optional) — The timestamp of when the message was created.
  - `metadata` (map from string to any, optional) — The metadata associated with the message.
  - `name` (string, optional) — Customizable name of the sender of the message (e.g., "john", "sales_agent").
  - `processed` (boolean, optional) — Whether the message has been processed.
  - `uuid` (string, optional) — The unique identifier of the message.
- `ignore_roles` (list of enum, optional) — Optional list of role types to ignore when adding messages to graph memory. The message itself will still be added, retained and used as context for messages that are added to a user's graph.
  - Allowed values: `norole`, `system`, `assistant`, `user`, `function`, `tool`
- `return_context` (boolean, optional) — Optionally return context block relevant to the most recent messages.
- `strict_ontology` (boolean, optional) — When true, prevents extraction of generic Entity nodes that do not match the configured ontology.

## Response

### 200

An object, optionally containing user context retrieved for the last thread message

- `context` (string, optional)
- `message_uuids` (list of string, optional)
- `task_id` (string, optional)

## Examples

**Request**

```json
{
  "messages": [
    {
      "content": "Can you provide an update on the project status?",
      "role": "user"
    }
  ]
}
```

**Response**

```json
{
  "context": "The project is currently on track with all milestones met as scheduled.",
  "message_uuids": [
    "a3f1c9e2-7b4d-4f8a-9d3e-2b5f6c7d8e9f"
  ],
  "task_id": "task-7890ab12-cdef-3456-7890-abcdef123456"
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.thread.addMessages("threadId", {
        messages: [
            {
                content: "Can you provide an update on the project status?",
                role: "user",
            },
        ],
    });
}
main();

```

```python
from zep_cloud import Zep, Message

client = Zep()

client.thread.add_messages(
    thread_id="threadId",
    messages=[
        Message(
            content="Can you provide an update on the project status?",
            role="user",
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
    request := &zep.AddThreadMessagesRequest{
        Messages: []*zep.Message{
            &zep.Message{
                Content: "Can you provide an update on the project status?",
                Role: zep.RoleTypeUserRole,
            },
        },
    }
    client.Thread.AddMessages(
        context.TODO(),
        "threadId",
        request,
    )
}

```