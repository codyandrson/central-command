> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Delete thread

DELETE https://api.getzep.com/api/v2/threads/{threadId}

Deletes a thread.

Reference: https://help.getzep.com/sdk-reference/thread/delete-thread

## Request

### Path parameters

- `threadId` (string, required) — The ID of the thread for which memory should be deleted.

## Response

### 200

OK

- `message` (string, optional)

## Examples

**Request**

```json
{}
```

**Response**

```json
{
  "message": "Thread successfully deleted."
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.thread.delete("threadId");
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.thread.delete(
    thread_id="threadId",
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
    client.Thread.Delete(
        context.TODO(),
        "threadId",
    )
}

```