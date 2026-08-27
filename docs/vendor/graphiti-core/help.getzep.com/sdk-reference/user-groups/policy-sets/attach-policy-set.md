> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Attach a policy set to a UserGroup

POST https://api.getzep.com/api/v2/abac/user-groups/{groupUUID}/policy-sets
Content-Type: application/json

Reference: https://help.getzep.com/sdk-reference/user-groups/policy-sets/attach-policy-set

## Request

### Path parameters

- `groupUUID` (string, required) — UserGroup UUID

### Query parameters

- `projectId` (string, required) — Project UUID

### Body (application/json)

- `policy_set_uuid` (string, required)

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
{
  "policy_set_uuid": "a3f1c9d2-4b7e-4f8a-9c3d-2e5b7f6a1d9e"
}
```

**Response**

```json
{
  "policy_sets": [
    {
      "mode": "enforce",
      "name": "Default Access Policy",
      "uuid": "b7e2d1f4-8c3a-4d9b-9f7e-1a2c3d4e5f6b",
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
    await client.userGroup.attachPolicySet("groupUUID", {
        projectId: "projectId",
        policySetUuid: "a3f1c9d2-4b7e-4f8a-9c3d-2e5b7f6a1d9e",
    });
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.user_group.attach_policy_set(
    group_uuid="groupUUID",
    project_id="projectId",
    policy_set_uuid="a3f1c9d2-4b7e-4f8a-9c3d-2e5b7f6a1d9e",
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
    request := &zep.AttachPolicySetRequest{
        ProjectID: "projectId",
        PolicySetUUID: "a3f1c9d2-4b7e-4f8a-9c3d-2e5b7f6a1d9e",
    }
    client.UserGroup.AttachPolicySet(
        context.TODO(),
        "groupUUID",
        request,
    )
}

```