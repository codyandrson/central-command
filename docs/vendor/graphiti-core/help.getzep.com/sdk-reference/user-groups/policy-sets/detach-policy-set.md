> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Detach a policy set from a UserGroup

DELETE https://api.getzep.com/api/v2/abac/user-groups/{groupUUID}/policy-sets/{policySetUUID}

Reference: https://help.getzep.com/sdk-reference/user-groups/policy-sets/detach-policy-set

## Request

### Path parameters

- `groupUUID` (string, required) — UserGroup UUID
- `policySetUUID` (string, required) — Policy set UUID

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
      "name": "Data Access Policy Set",
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
    await client.userGroup.detachPolicySet("groupUUID", "policySetUUID", {
        projectId: "projectId",
    });
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.user_group.detach_policy_set(
    group_uuid="groupUUID",
    policy_set_uuid="policySetUUID",
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
    request := &zep.UserGroupDetachPolicySetRequest{
        ProjectID: "projectId",
    }
    client.UserGroup.DetachPolicySet(
        context.TODO(),
        "groupUUID",
        "policySetUUID",
        request,
    )
}

```