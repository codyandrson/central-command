> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Delete Group

DELETE https://api.getzep.com/api/v2/groups/{groupId}

Deletes a group.

Reference: https://help.getzep.com/v2/sdk-reference/group/delete-group

## Request

### Path parameters

- `groupId` (string, required) — Group ID

## Response

### 200

Deleted

- `message` (string, optional)

## Examples

**Request**

```json
{}
```

**Response**

```json
{
  "message": "Group successfully deleted."
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.group.delete("groupId");
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.group.delete(
    group_id="groupId",
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
    client.Group.Delete(
        context.TODO(),
        "groupId",
    )
}

```