> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Delete User

DELETE https://api.getzep.com/api/v2/users/{userId}

Deletes a user.

Reference: https://help.getzep.com/sdk-reference/user/delete

## Request

### Path parameters

- `userId` (string, required) — User ID

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
  "message": "User with ID 123e4567-e89b-12d3-a456-426614174000 has been successfully deleted."
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.user.delete("userId");
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.user.delete(
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
    client.User.Delete(
        context.TODO(),
        "userId",
    )
}

```