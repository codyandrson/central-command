> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# List UserGroups

GET https://api.getzep.com/api/v2/user-groups

Reference: https://help.getzep.com/sdk-reference/user-groups/list

## Request

### Query parameters

- `projectId` (string, required) — Project UUID
- `pageNumber` (integer, required) — Page number
- `pageSize` (integer, required) — Page size
- `search` (string, optional) — Name search

## Response

### 200

OK

- `quota` (object, optional)
  - `active_managed` (integer, optional)
  - `allocation` (integer, optional)
  - `has_feature` (boolean, optional)
  - `resolved` (boolean, optional)
  - `unlimited` (boolean, optional)
- `row_count` (integer, optional)
- `total_count` (integer, optional)
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
  "quota": {
    "active_managed": 5,
    "allocation": 10,
    "has_feature": true,
    "resolved": true,
    "unlimited": false
  },
  "row_count": 2,
  "total_count": 50,
  "user_groups": [
    {
      "attached_policy_set_count": 3,
      "created_at": "2024-05-10T09:15:00Z",
      "description": "Engineering team user group",
      "kind": "managed",
      "member_count": 25,
      "name": "Engineering Team",
      "project_uuid": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
      "updated_at": "2024-06-01T12:00:00Z",
      "uuid": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
      "version": 3
    }
  ]
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.userGroup.list({
        projectId: "3fa85f64-5717-4562-b3fc-2c963f66afa6",
        pageNumber: 1,
        pageSize: 20,
        search: "engineering",
    });
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.user_group.list(
    project_id="3fa85f64-5717-4562-b3fc-2c963f66afa6",
    page_number=1,
    page_size=20,
    search="engineering",
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
    request := &zep.UserGroupListRequest{
        ProjectID: "3fa85f64-5717-4562-b3fc-2c963f66afa6",
        PageNumber: 1,
        PageSize: 20,
        Search: zep.String(
            "engineering",
        ),
    }
    client.UserGroup.List(
        context.TODO(),
        request,
    )
}

```