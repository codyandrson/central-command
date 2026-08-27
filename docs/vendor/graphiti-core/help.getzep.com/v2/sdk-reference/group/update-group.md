> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Update Group

PATCH https://api.getzep.com/api/v2/groups/{groupId}
Content-Type: application/json

Updates information about a group.

Reference: https://help.getzep.com/v2/sdk-reference/group/update-group

## Request

### Path parameters

- `groupId` (string, required) — Group ID

### Body (application/json)

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
{}
```

**Response**

```json
{
  "created_at": "2024-05-20T10:15:30Z",
  "description": "Marketing team responsible for campaign analytics and strategy.",
  "external_id": "deprecated-12345",
  "fact_rating_instruction": {
    "examples": {
      "high": "The campaign increased user engagement by 30%.",
      "low": "The office has a coffee machine.",
      "medium": "The team met weekly to discuss project progress."
    },
    "instruction": "Rate facts based on their relevance to marketing campaign performance and team productivity."
  },
  "group_id": "grp-7890",
  "id": 1024,
  "name": "Marketing Analytics Group",
  "project_uuid": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "uuid": "550e8400-e29b-41d4-a716-446655440000"
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.group.update("groupId", {});
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.group.update(
    group_id="groupId",
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
    request := &zep.UpdateGroupRequest{}
    client.Group.Update(
        context.TODO(),
        "groupId",
        request,
    )
}

```