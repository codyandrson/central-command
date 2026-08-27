> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# List policy sets attached to a UserGroup

GET https://api.getzep.com/api/v2/abac/user-groups/{groupUUID}/policy-sets

Reference: https://help.getzep.com/sdk-reference/user-groups/policy-sets/list-policy-sets

## Request

### Path parameters

- `groupUUID` (string, required) — UserGroup UUID

### Query parameters

- `projectId` (string, required) — Project UUID

## Response

### 200

OK

- `policy_sets` (list of object, optional)
  - `mode` (string, optional)
  - `name` (string, optional)
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
  "policy_sets": [
    {
      "mode": "enforced",
      "name": "Data Access Control",
      "uuid": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
      "version": 4
    }
  ]
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.userGroup.listPolicySets("groupUUID", {
        projectId: "projectId",
    });
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.user_group.list_policy_sets(
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
    request := &zep.UserGroupListPolicySetsRequest{
        ProjectID: "projectId",
    }
    client.UserGroup.ListPolicySets(
        context.TODO(),
        "groupUUID",
        request,
    )
}

```