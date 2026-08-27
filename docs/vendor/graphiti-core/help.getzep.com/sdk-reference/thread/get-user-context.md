> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Get user context

GET https://api.getzep.com/api/v2/threads/{threadId}/context

Returns most relevant context from the user graph (including memory from any/all past threads) based on the content of the past few messages of the given thread.

Reference: https://help.getzep.com/sdk-reference/thread/get-user-context

## Request

### Path parameters

- `threadId` (string, required) — The ID of the current thread (for which context is being retrieved).

### Query parameters

- `template_id` (string, optional) — Optional template ID to use for custom context rendering.

## Response

### 200

OK

- `context` (string, optional) — Context block containing relevant facts, entities, and messages/episodes from the user graph. Meant to be replaced in the system prompt on every chat turn.

## Examples

**Request**

```json
{}
```

**Response**

```json
{
  "context": "User has recently inquired about project deadlines and shared updates on the marketing campaign progress. Previous threads include discussions on budget allocation and team assignments."
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.thread.getUserContext("threadId", {});
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.thread.get_user_context(
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
    request := &zep.ThreadGetUserContextRequest{}
    client.Thread.GetUserContext(
        context.TODO(),
        "threadId",
        request,
    )
}

```