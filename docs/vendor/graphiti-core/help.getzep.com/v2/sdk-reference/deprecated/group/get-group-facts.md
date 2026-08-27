> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Get Group Facts

GET https://api.getzep.com/api/v2/groups/{groupId}/facts

Deprecated: Use Get Group Edges instead.

Reference: https://help.getzep.com/v2/sdk-reference/deprecated/group/get-group-facts

## Request

### Path parameters

- `groupId` (string, required) — The group_id of the group to get.

## Response

### 200

The group facts.

- `facts` (list of object, optional)
  - `content` (string, required)
  - `created_at` (string, required)
  - `fact` (string, required) — Deprecated
  - `uuid` (string, required)
  - `expired_at` (string, optional)
  - `invalid_at` (string, optional)
  - `name` (string, optional)
  - `rating` (double, optional)
  - `source_node_name` (string, optional)
  - `target_node_name` (string, optional)
  - `valid_at` (string, optional)

## Examples

**Request**

```json
{}
```

**Response**

```json
{
  "facts": [
    {
      "content": "The group has completed 150 projects in the last year.",
      "created_at": "2024-04-10T09:15:00Z",
      "fact": "Completed projects count",
      "uuid": "a3f1c9e2-7b4d-4f8a-9c2e-1d2f3b4a5c6d",
      "expired_at": "2025-04-10T09:15:00Z",
      "invalid_at": "2025-04-10T09:15:00Z",
      "name": "ProjectCompletionFact",
      "rating": 4.7,
      "source_node_name": "ProjectManagementSystem",
      "target_node_name": "GroupAlpha",
      "valid_at": "2024-04-10T00:00:00Z"
    }
  ]
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.group.getFacts("groupId");
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.group.get_facts(
    group_id="groupId",
)

```

```go
package example

import (
    context "context"

    client "github.com/getzep/zep-go/client"
)

func do() {
    client := client.NewClient()
    client.Group.GetFacts(
        context.TODO(),
        "groupId",
    )
}

```