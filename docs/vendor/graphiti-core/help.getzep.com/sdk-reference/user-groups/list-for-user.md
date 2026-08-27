> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# List effective UserGroups for a User

GET https://api.getzep.com/api/v2/user-groups/users/{userUUID}

Reference: https://help.getzep.com/sdk-reference/user-groups/list-for-user

## Request

### Path parameters

- `userUUID` (string, required) — User UUID

### Query parameters

- `projectId` (string, required) — Project UUID

## Response

### 200

OK

- `user_groups` (list of object, optional)
  - `attached_policy_set_count` (integer, optional)
  - `created_at` (string, optional)
  - `description` (string, optional)
  - `kind` (enum, optional)
    - Allowed values: `managed`, `virtual`
  - `member_count` (integer, optional)
  - `name` (string, optional)
  - `project_uuid` (string, optional)
  - `updated_at` (string, optional)
  - `uuid` (string, optional)
  - `version` (integer, optional)

## Examples

**Request**

```json
{}
```

**Response**

```json
{
  "user_groups": [
    {
      "attached_policy_set_count": 3,
      "created_at": "2023-11-15T09:27:00Z",
      "description": "Administrators with full access to project resources",
      "kind": "managed",
      "member_count": 12,
      "name": "Project Admins",
      "project_uuid": "a3f1c9d2-4b7e-4f8a-9c3d-2e5b7f1a9d8c",
      "updated_at": "2024-04-10T16:45:00Z",
      "uuid": "b7e3f1a9-8c4d-2e5b-7f1a-9d8ca3f1c9d2",
      "version": 5
    }
  ]
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.userGroup.listForUser("d9f8a7b6-c5e4-3d2c-1b0a-9f8e7d6c5b4a", {
        projectId: "a3f1c9d2-4b7e-4f8a-9c3d-2e5b7f1a9d8c",
    });
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.user_group.list_for_user(
    user_uuid="d9f8a7b6-c5e4-3d2c-1b0a-9f8e7d6c5b4a",
    project_id="a3f1c9d2-4b7e-4f8a-9c3d-2e5b7f1a9d8c",
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
    request := &zep.UserGroupListForUserRequest{
        ProjectID: "a3f1c9d2-4b7e-4f8a-9c3d-2e5b7f1a9d8c",
    }
    client.UserGroup.ListForUser(
        context.TODO(),
        "d9f8a7b6-c5e4-3d2c-1b0a-9f8e7d6c5b4a",
        request,
    )
}

```