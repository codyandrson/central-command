> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Remove one member from a managed UserGroup

DELETE https://api.getzep.com/api/v2/user-groups/{groupUUID}/members/{userUUID}

Reference: https://help.getzep.com/sdk-reference/user-groups/members/remove-member

## Request

### Path parameters

- `groupUUID` (string, required) — UserGroup UUID
- `userUUID` (string, required) — User UUID

### Query parameters

- `projectId` (string, required) — Project UUID

## Response

### 200

OK

- `added_count` (integer, optional)
- `no_op_count` (integer, optional)
- `removed_count` (integer, optional)

## Examples

**Request**

```json
{}
```

**Response**

```json
{
  "added_count": 0,
  "no_op_count": 0,
  "removed_count": 1
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.userGroup.removeMember("groupUUID", "userUUID", {
        projectId: "projectId",
    });
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.user_group.remove_member(
    group_uuid="groupUUID",
    user_uuid="userUUID",
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
    request := &zep.UserGroupRemoveMemberRequest{
        ProjectID: "projectId",
    }
    client.UserGroup.RemoveMember(
        context.TODO(),
        "groupUUID",
        "userUUID",
        request,
    )
}

```