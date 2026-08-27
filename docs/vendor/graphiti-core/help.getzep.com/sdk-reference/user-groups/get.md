> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Get a UserGroup

GET https://api.getzep.com/api/v2/user-groups/{groupUUID}

Reference: https://help.getzep.com/sdk-reference/user-groups/get

## Request

### Path parameters

- `groupUUID` (string, required) — UserGroup UUID

### Query parameters

- `projectId` (string, required) — Project UUID

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
{}
```

**Response**

```json
{
  "attached_policy_set_count": 3,
  "created_at": "2024-05-10T09:15:00Z",
  "description": "Administrators group with full access to all project resources",
  "kind": "managed",
  "member_count": 12,
  "name": "Project Admins",
  "project_uuid": "7b9e1d2a-4c3f-4d2a-9f1e-8a2b3c4d5e6f",
  "updated_at": "2024-06-01T16:45:00Z",
  "uuid": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "version": 5
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.userGroup.get("3fa85f64-5717-4562-b3fc-2c963f66afa6", {
        projectId: "7b9e1d2a-4c3f-4d2a-9f1e-8a2b3c4d5e6f",
    });
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.user_group.get(
    group_uuid="3fa85f64-5717-4562-b3fc-2c963f66afa6",
    project_id="7b9e1d2a-4c3f-4d2a-9f1e-8a2b3c4d5e6f",
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
    request := &zep.UserGroupGetRequest{
        ProjectID: "7b9e1d2a-4c3f-4d2a-9f1e-8a2b3c4d5e6f",
    }
    client.UserGroup.Get(
        context.TODO(),
        "3fa85f64-5717-4562-b3fc-2c963f66afa6",
        request,
    )
}

```