> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Delete Session

DELETE https://api.getzep.com/api/v2/sessions/{sessionId}/memory

Deletes a session.

Reference: https://help.getzep.com/v2/sdk-reference/memory/delete

## Request

### Path parameters

- `sessionId` (string, required) — The ID of the session for which memory should be deleted.

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
  "message": "Session memory successfully deleted."
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.memory.delete("sessionId");
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.memory.delete(
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
    client.Memory.Delete(
        context.TODO(),
        "sessionId",
    )
}

```