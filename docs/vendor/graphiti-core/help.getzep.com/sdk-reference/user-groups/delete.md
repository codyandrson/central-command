> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Delete a managed UserGroup

DELETE https://api.getzep.com/api/v2/user-groups/{groupUUID}

Reference: https://help.getzep.com/sdk-reference/user-groups/delete

## Request

### Path parameters

- `groupUUID` (string, required) — UserGroup UUID

### Query parameters

- `projectId` (string, required) — Project UUID

## Response

### 204

No Content

## Examples

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.userGroup.delete("groupUUID", {
        projectId: "projectId",
    });
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.user_group.delete(
    group_uuid="groupUUID",
    project_id="projectId",
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
    request := &zep.UserGroupDeleteRequest{
        ProjectID: "projectId",
    }
    client.UserGroup.Delete(
        context.TODO(),
        "groupUUID",
        request,
    )
}

```