> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Update a managed UserGroup

PATCH https://api.getzep.com/api/v2/user-groups/{groupUUID}
Content-Type: application/json

Reference: https://help.getzep.com/sdk-reference/user-groups/update

## Request

### Path parameters

- `groupUUID` (string, required) — UserGroup UUID

### Query parameters

- `projectId` (string, required) — Project UUID

### Body (application/json)

- `expected_version` (integer, required)
- `description` (string, optional)
- `name` (string, optional)

## Response

### 200

OK

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
{
  "expected_version": 3
}
```

**Response**

```json
{
  "attached_policy_set_count": 5,
  "created_at": "2023-11-10T09:15:30Z",
  "description": "Group for managing marketing team access",
  "kind": "managed",
  "member_count": 42,
  "name": "Marketing Team",
  "project_uuid": "a1b2c3d4-e5f6-7890-ab12-cd34ef567890",
  "updated_at": "2024-04-20T16:45:00Z",
  "uuid": "123e4567-e89b-12d3-a456-426614174000",
  "version": 3
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.userGroup.update("groupUUID", {
        projectId: "projectId",
        expectedVersion: 3,
    });
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.user_group.update(
    group_uuid="groupUUID",
    project_id="projectId",
    expected_version=3,
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
    request := &zep.UpdateUserGroupRequest{
        ProjectID: "projectId",
        ExpectedVersion: 3,
    }
    client.UserGroup.Update(
        context.TODO(),
        "groupUUID",
        request,
    )
}

```