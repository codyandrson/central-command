> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Get user facts.

GET https://api.getzep.com/api/v2/users/{userId}/facts

Deprecated: Use Get User Edges instead.

Reference: https://help.getzep.com/v2/sdk-reference/deprecated/user/get-user-facts

## Request

### Path parameters

- `userId` (string, required) — The user_id of the user to get.

## Response

### 200

The user facts.

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
      "content": "The user has completed 150 tasks this month.",
      "created_at": "2024-05-20T10:15:30Z",
      "fact": "Completed tasks count",
      "uuid": "a3f1c9e2-7b4d-4f8a-9c2e-1d2f3b4a5c6d",
      "expired_at": "2024-06-20T10:15:30Z",
      "invalid_at": "2024-06-21T00:00:00Z",
      "name": "TaskCompletionFact",
      "rating": 4.7,
      "source_node_name": "TaskManagerService",
      "target_node_name": "UserProfileService",
      "valid_at": "2024-05-20T00:00:00Z"
    }
  ]
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.user.getFacts("userId");
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.user.get_facts(
    user_id="userId",
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
    client.User.GetFacts(
        context.TODO(),
        "userId",
    )
}

```