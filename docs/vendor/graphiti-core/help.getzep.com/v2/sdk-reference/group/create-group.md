> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Create Group

POST https://api.getzep.com/api/v2/groups
Content-Type: application/json

Creates a new group.

Reference: https://help.getzep.com/v2/sdk-reference/group/create-group

## Request

### Body (application/json)

- `group_id` (string, required)
- `description` (string, optional)
- `fact_rating_instruction` (object, optional)
  - `examples` (object, optional) — Examples is a list of examples that demonstrate how facts might be rated based on your instruction. You should provide an example of a highly rated example, a low rated example, and a medium (or in between example). For example, if you are rating based on relevance to a trip planning application, your examples might be: High: "Joe's dream vacation is Bali" Medium: "Joe has a fear of flying", Low: "Joe's favorite food is Japanese",
    - `high` (string, optional)
    - `low` (string, optional)
    - `medium` (string, optional)
  - `instruction` (string, optional) — A string describing how to rate facts as they apply to your application. A trip planning application may use something like "relevancy to planning a trip, the user's preferences when traveling, or the user's travel history."
- `name` (string, optional)

## Response

### 201

The added group

- `created_at` (string, optional)
- `description` (string, optional)
- `external_id` (string, optional) — Deprecated
- `fact_rating_instruction` (object, optional)
  - `examples` (object, optional) — Examples is a list of examples that demonstrate how facts might be rated based on your instruction. You should provide an example of a highly rated example, a low rated example, and a medium (or in between example). For example, if you are rating based on relevance to a trip planning application, your examples might be: High: "Joe's dream vacation is Bali" Medium: "Joe has a fear of flying", Low: "Joe's favorite food is Japanese",
    - `high` (string, optional)
    - `low` (string, optional)
    - `medium` (string, optional)
  - `instruction` (string, optional) — A string describing how to rate facts as they apply to your application. A trip planning application may use something like "relevancy to planning a trip, the user's preferences when traveling, or the user's travel history."
- `group_id` (string, optional)
- `id` (integer, optional)
- `name` (string, optional)
- `project_uuid` (string, optional)
- `uuid` (string, optional)

## Examples

**Request**

```json
{
  "group_id": "grp-202406"
}
```

**Response**

```json
{
  "created_at": "2024-06-01T12:00:00Z",
  "description": "Group for managing customer support tickets",
  "external_id": "deprecated-12345",
  "fact_rating_instruction": {
    "examples": {
      "high": "Customer reported issue with login failure",
      "low": "Customer's favorite color is blue",
      "medium": "Customer occasionally uses mobile app"
    },
    "instruction": "Rate facts based on their relevance to customer support and issue resolution."
  },
  "group_id": "grp-202406",
  "id": 1024,
  "name": "Customer Support Group",
  "project_uuid": "a3f1c9d2-7b4e-4f8a-9c3d-2e5f7a1b9c8d",
  "uuid": "d9f8e7c6-b5a4-4321-9f8e-7d6c5b4a3f2e"
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.group.add({
        groupId: "grp-202406",
    });
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.group.add(
    group_id="grp-202406",
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
    request := &zep.CreateGroupRequest{
        GroupID: "grp-202406",
    }
    client.Group.Add(
        context.TODO(),
        request,
    )
}

```