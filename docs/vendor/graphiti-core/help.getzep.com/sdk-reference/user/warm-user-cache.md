> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Warm User Cache

GET https://api.getzep.com/api/v2/users/{userId}/warm

Hints Zep to warm a user's graph for low-latency search

Reference: https://help.getzep.com/sdk-reference/user/warm-user-cache

## Request

### Path parameters

- `userId` (string, required) — User ID

## Response

### 200

Warm hint accepted

- `message` (string, optional)

## Examples

**Request**

```json
{}
```

**Response**

```json
{
  "message": "User graph warm-up initiated successfully."
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.user.warm("userId");
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.user.warm(
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
    client.User.Warm(
        context.TODO(),
        "userId",
    )
}

```