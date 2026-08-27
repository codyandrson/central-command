> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Returns a session's summaries by ID

GET https://api.getzep.com/api/v2/sessions/{sessionId}/summary

Deprecated API: Get session summaries by ID

Reference: https://help.getzep.com/v2/sdk-reference/deprecated/memory/get-summaries

## Request

### Path parameters

- `sessionId` (string, required) — Session ID

## Response

### 200

OK

- `row_count` (integer, optional)
- `summaries` (list of object, optional)
  - `content` (string, optional) — The content of the summary.
  - `created_at` (string, optional) — The timestamp of when the summary was created.
  - `metadata` (map from string to any, optional)
  - `related_message_uuids` (list of string, optional)
  - `token_count` (integer, optional) — The number of tokens in the summary.
  - `uuid` (string, optional) — The unique identifier of the summary.
- `total_count` (integer, optional)

## Examples

**Request**

```json
{}
```

**Response**

```json
{
  "row_count": 1,
  "summaries": [
    {
      "content": "Summary of the key discussion points and decisions made during the session.",
      "created_at": "2024-06-10T15:45:00Z",
      "metadata": {
        "author": "AI summarizer",
        "session_topic": "Project Kickoff"
      },
      "related_message_uuids": [
        "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
      ],
      "token_count": 120,
      "uuid": "9f8e7d6c-5b4a-3210-9fed-cba987654321"
    }
  ],
  "total_count": 1
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.memory.getSummaries("sessionId");
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.memory.get_summaries(
    session_id="sessionId",
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
    client.Memory.GetSummaries(
        context.TODO(),
        "sessionId",
    )
}

```