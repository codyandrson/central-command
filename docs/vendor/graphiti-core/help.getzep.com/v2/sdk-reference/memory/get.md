> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Get Session Memory

GET https://api.getzep.com/api/v2/sessions/{sessionId}/memory

Returns a memory for a given session.

Reference: https://help.getzep.com/v2/sdk-reference/memory/get

## Request

### Path parameters

- `sessionId` (string, required) — The ID of the session for which to retrieve memory.

### Query parameters

- `lastn` (integer, optional) — The number of most recent memory entries to retrieve.
- `minRating` (double, optional) — The minimum rating by which to filter relevant facts.

## Response

### 200

OK

- `context` (string, optional) — Memory context containing relevant facts and entities for the session. Can be put into the prompt directly.
- `facts` (list of string, optional) — Deprecated: Use relevant_facts instead.
- `messages` (list of object, optional) — A list of message objects, where each message contains a role and content. Only last_n messages will be returned
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
- `metadata` (map from string to any, optional) — Deprecated
- `relevant_facts` (list of object, optional) — Most relevant facts to the recent messages in the session.
  - `content` (string, required)
  - `created_at` (string, required)
  - `fact` (string, required) — Deprecated
  - `uuid` (string, required)
  - `expired_at` (string, optional)
  - `invalid_at` (string, optional)
  - `name` (string, optional)
  - `rating` (double, optional)
  - `source_node_name` (string, optional)
  - `target_node_name` (string, optional)
  - `valid_at` (string, optional)
- `summary` (object, optional) — Deprecated: Use context string instead.
  - `content` (string, optional) — The content of the summary.
  - `created_at` (string, optional) — The timestamp of when the summary was created.
  - `metadata` (map from string to any, optional)
  - `related_message_uuids` (list of string, optional)
  - `token_count` (integer, optional) — The number of tokens in the summary.
  - `uuid` (string, optional) — The unique identifier of the summary.

## Examples

**Request**

```json
{}
```

**Response**

```json
{
  "context": "The session memory contains recent conversation history and relevant facts about the user's preferences and previous interactions.",
  "facts": [
    "User prefers concise answers.",
    "User is interested in technology and AI."
  ],
  "messages": [
    {
      "content": "Hello! How can I assist you today?",
      "role_type": "assistant",
      "created_at": "2024-06-10T09:15:00Z",
      "metadata": {
        "language": "en",
        "sentiment": "neutral"
      },
      "processed": true,
      "role": "assistant",
      "token_count": 8,
      "updated_at": "2024-06-10T09:15:01Z",
      "uuid": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    }
  ],
  "metadata": {
    "session_start": "2024-06-10T09:00:00Z",
    "session_owner": "user123"
  },
  "relevant_facts": [
    {
      "content": "User has a preference for brief and direct responses.",
      "created_at": "2024-06-09T18:00:00Z",
      "fact": "User prefers concise answers.",
      "uuid": "f1234567-89ab-4cde-9012-3456789abcde",
      "expired_at": "2024-12-31T23:59:59Z",
      "invalid_at": "2025-01-01T00:00:00Z",
      "name": "ConciseAnswerPreference",
      "rating": 4.7,
      "source_node_name": "UserProfile",
      "target_node_name": "ResponseStyle",
      "valid_at": "2024-06-01T00:00:00Z"
    }
  ],
  "summary": {
    "content": "The user prefers concise answers and is interested in technology topics.",
    "created_at": "2024-06-10T09:20:00Z",
    "metadata": {
      "summary_type": "session_overview"
    },
    "related_message_uuids": [
      "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    ],
    "token_count": 15,
    "uuid": "d4e5f6a7-b890-1234-cdef-56789abcdef0"
  }
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.memory.get("sessionId", {});
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.memory.get(
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
    request := &zep.MemoryGetRequest{}
    client.Memory.Get(
        context.TODO(),
        "sessionId",
        request,
    )
}

```