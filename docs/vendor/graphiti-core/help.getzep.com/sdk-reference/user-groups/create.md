> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Create a managed UserGroup

POST https://api.getzep.com/api/v2/user-groups
Content-Type: application/json

Reference: https://help.getzep.com/sdk-reference/user-groups/create

## Request

### Query parameters

- `projectId` (string, required) — Project UUID

### Body (application/json)

- `name` (string, required)
- `description` (string, optional)

## Response

### 201

Created

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
  "name": "Engineering Team"
}
```

**Response**

```json
{
  "attached_policy_set_count": 3,
  "created_at": "2024-06-01T09:15:00Z",
  "description": "Group for all engineering department members",
  "kind": "managed",
  "member_count": 42,
  "name": "Engineering Team",
  "project_uuid": "a3f1c9d2-4b7e-4f8a-9c2d-123456789abc",
  "updated_at": "2024-06-10T11:30:00Z",
  "uuid": "d9b2d63d-a233-4123-847a-7a7a7a7a7a7a",
  "version": 5
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.userGroup.create({
        projectId: "projectId",
        name: "Engineering Team",
    });
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.user_group.create(
    project_id="projectId",
    name="Engineering Team",
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
    request := &zep.CreateUserGroupRequest{
        ProjectID: "projectId",
        Name: "Engineering Team",
    }
    client.UserGroup.Create(
        context.TODO(),
        request,
    )
}

```