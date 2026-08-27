> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# List UserGroup members

GET https://api.getzep.com/api/v2/user-groups/{groupUUID}/members

Reference: https://help.getzep.com/sdk-reference/user-groups/members/list-members

## Request

### Path parameters

- `groupUUID` (string, required) — UserGroup UUID

### Query parameters

- `projectId` (string, required) — Project UUID
- `pageNumber` (integer, required) — Page number
- `pageSize` (integer, required) — Page size
- `search` (string, optional) — User search

## Response

### 200

OK

- `row_count` (integer, optional)
- `total_count` (integer, optional)
- `users` (list of object, optional)
  - `created_at` (string, optional)
  - `deleted_at` (string, optional)
  - `disable_default_ontology` (boolean, optional)
  - `email` (string, optional)
  - `first_name` (string, optional)
  - `id` (integer, optional)
  - `last_name` (string, optional)
  - `metadata` (map from string to any, optional) — Deprecated
  - `project_uuid` (string, optional)
  - `session_count` (integer, optional) — Deprecated
  - `time_zone` (string, optional, nullable)
  - `updated_at` (string, optional) — Deprecated
  - `user_id` (string, optional)
  - `uuid` (string, optional)

## Examples

**Request**

```json
{}
```

**Response**

```json
{
  "row_count": 1,
  "total_count": 1,
  "users": [
    {
      "created_at": "2024-05-10T09:15:00Z",
      "deleted_at": null,
      "disable_default_ontology": false,
      "email": "jane.doe@example.com",
      "first_name": "Jane",
      "id": 1024,
      "last_name": "Doe",
      "metadata": {},
      "project_uuid": "a3f1c9d2-7b4e-4f8a-9c3d-2e5f6a7b8c9d",
      "session_count": 42,
      "time_zone": "America/New_York",
      "updated_at": "2024-05-15T12:00:00Z",
      "user_id": "user-7890",
      "uuid": "d290f1ee-6c54-4b01-90e6-d701748f0851"
    }
  ]
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.userGroup.listMembers("groupUUID", {
        projectId: "projectId",
        pageNumber: 1,
        pageSize: 1,
    });
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.user_group.list_members(
    group_uuid="groupUUID",
    project_id="projectId",
    page_number=1,
    page_size=1,
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
    request := &zep.UserGroupListMembersRequest{
        ProjectID: "projectId",
        PageNumber: 1,
        PageSize: 1,
    }
    client.UserGroup.ListMembers(
        context.TODO(),
        "groupUUID",
        request,
    )
}

```