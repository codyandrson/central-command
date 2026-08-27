> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Remove members from a managed UserGroup

POST https://api.getzep.com/api/v2/user-groups/{groupUUID}/members/bulk-remove
Content-Type: application/json

Reference: https://help.getzep.com/sdk-reference/user-groups/members/remove-members

## Request

### Path parameters

- `groupUUID` (string, required) — UserGroup UUID

### Query parameters

- `projectId` (string, required) — Project UUID

### Body (application/json)

- `user_uuids` (list of string, required)

## Response

### 200

OK

- `added_count` (integer, optional)
- `no_op_count` (integer, optional)
- `removed_count` (integer, optional)

## Examples

**Request**

```json
{
  "user_uuids": [
    "3fa85f64-5717-4562-b3fc-2c963f66afa6",
    "7c9e6679-7425-40de-944b-e07fc1f90ae7"
  ]
}
```

**Response**

```json
{
  "added_count": 0,
  "no_op_count": 2,
  "removed_count": 2
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.userGroup.removeMembers("groupUUID", {
        projectId: "projectId",
        body: {
            userUuids: [
                "3fa85f64-5717-4562-b3fc-2c963f66afa6",
                "7c9e6679-7425-40de-944b-e07fc1f90ae7",
            ],
        },
    });
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.user_group.remove_members(
    group_uuid="groupUUID",
    project_id="projectId",
    user_uuids=[
        "3fa85f64-5717-4562-b3fc-2c963f66afa6",
        "7c9e6679-7425-40de-944b-e07fc1f90ae7"
    ],
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
    request := &zep.UserGroupRemoveMembersRequest{
        ProjectID: "projectId",
        Body: &zep.MutateUserGroupMembersRequest{
            UserUUIDs: []string{
                "3fa85f64-5717-4562-b3fc-2c963f66afa6",
                "7c9e6679-7425-40de-944b-e07fc1f90ae7",
            },
        },
    }
    client.UserGroup.RemoveMembers(
        context.TODO(),
        "groupUUID",
        request,
    )
}

```