> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Returns all facts for a session by ID

GET https://api.getzep.com/api/v2/sessions/{sessionId}/facts

Deprecated API: get facts for a session

Reference: https://help.getzep.com/v2/sdk-reference/deprecated/memory/get-session-facts

## Request

### Path parameters

- `sessionId` (string, required) — Session ID

### Query parameters

- `minRating` (double, optional) — Minimum rating by which to filter facts

## Response

### 200

The facts for the session.

- `facts` (list of object, optional)
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

## Examples

**Request**

```json
{}
```

**Response**

```json
{
  "facts": [
    {
      "content": "The Eiffel Tower was completed in 1889 and stands 324 meters tall.",
      "created_at": "2023-11-10T09:15:00Z",
      "fact": "Deprecated",
      "uuid": "a3f1c9d2-7b4e-4f8a-9c3d-2e5b6f7a8c9d",
      "expired_at": "2025-11-10T09:15:00Z",
      "invalid_at": "2024-11-10T09:15:00Z",
      "name": "Eiffel Tower Height",
      "rating": 4.7,
      "source_node_name": "Historical Records",
      "target_node_name": "Eiffel Tower",
      "valid_at": "2023-01-01T00:00:00Z"
    }
  ]
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.memory.getSessionFacts("sessionId", {});
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.memory.get_session_facts(
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
    request := &zep.MemoryGetSessionFactsRequest{}
    client.Memory.GetSessionFacts(
        context.TODO(),
        "sessionId",
        request,
    )
}

```