> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Add members to a managed UserGroup

POST https://api.getzep.com/api/v2/user-groups/{groupUUID}/members
Content-Type: application/json

Reference: https://help.getzep.com/sdk-reference/user-groups/members/add-members

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
    "a3f1c9e2-7b4d-4f8a-9c2e-123456789abc",
    "b7d2e4f0-1a3c-4d5e-8f9b-987654321def"
  ]
}
```

**Response**

```json
{
  "added_count": 2,
  "no_op_count": 0,
  "removed_count": 0
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.userGroup.addMembers("groupUUID", {
        projectId: "projectId",
        body: {
            userUuids: [
                "a3f1c9e2-7b4d-4f8a-9c2e-123456789abc",
                "b7d2e4f0-1a3c-4d5e-8f9b-987654321def",
            ],
        },
    });
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.user_group.add_members(
    group_uuid="groupUUID",
    project_id="projectId",
    user_uuids=[
        "a3f1c9e2-7b4d-4f8a-9c2e-123456789abc",
        "b7d2e4f0-1a3c-4d5e-8f9b-987654321def"
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
    request := &zep.UserGroupAddMembersRequest{
        ProjectID: "projectId",
        Body: &zep.MutateUserGroupMembersRequest{
            UserUUIDs: []string{
                "a3f1c9e2-7b4d-4f8a-9c2e-123456789abc",
                "b7d2e4f0-1a3c-4d5e-8f9b-987654321def",
            },
        },
    }
    client.UserGroup.AddMembers(
        context.TODO(),
        "groupUUID",
        request,
    )
}

```