> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Add messages to a thread in batch

POST https://api.getzep.com/api/v2/threads/{threadId}/messages-batch
Content-Type: application/json

Deprecated. Use the [Batch API](/adding-batch-data) (`client.batch.*` with `type: "thread_message"`) instead.

Adds messages to a thread in batch mode, processing messages concurrently.


Reference: https://help.getzep.com/sdk-reference/thread/add-messages-to-a-thread-in-batch

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
      "content": "Hello, can you provide an update on the project status?",
      "role": "user"
    },
    {
      "content": "The project is on track for completion by the end of the month.",
      "role": "assistant"
    }
  ]
}
```

**Response**

```json
{
  "context": "Project status updated successfully. Next milestone is the final review scheduled for next week.",
  "message_uuids": [
    "a3f1c9e2-4b7d-4f8a-9c2e-1d2f3b4a5c6d",
    "b7e2d3f4-5a6b-4c7d-8e9f-0a1b2c3d4e5f"
  ],
  "task_id": "task-7890abcd-1234-5678-efgh-9012ijklmnop"
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.thread.addMessagesBatch("threadId", {
        messages: [
            {
                content: "Hello, can you provide an update on the project status?",
                role: "user",
            },
            {
                content: "The project is on track for completion by the end of the month.",
                role: "assistant",
            },
        ],
    });
}
main();

```

```python
from zep_cloud import Zep, Message

client = Zep()

client.thread.add_messages_batch(
    thread_id="threadId",
    messages=[
        Message(
            content="Hello, can you provide an update on the project status?",
            role="user",
        ),
        Message(
            content="The project is on track for completion by the end of the month.",
            role="assistant",
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
                Content: "Hello, can you provide an update on the project status?",
                Role: zep.RoleTypeUserRole,
            },
            &zep.Message{
                Content: "The project is on track for completion by the end of the month.",
                Role: zep.RoleTypeAssistantRole,
            },
        },
    }
    client.Thread.AddMessagesBatch(
        context.TODO(),
        "threadId",
        request,
    )
}

```