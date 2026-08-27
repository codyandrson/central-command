> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Get All Groups

GET https://api.getzep.com/api/v2/groups-ordered

Returns all groups.

Reference: https://help.getzep.com/v2/sdk-reference/group/get-all-groups

## Request

### Query parameters

- `pageNumber` (integer, optional) — Page number for pagination, starting from 1.
- `pageSize` (integer, optional) — Number of groups to retrieve per page.

## Response

### 200

Successfully retrieved list of groups.

- `groups` (list of object, optional)
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
- `row_count` (integer, optional)
- `total_count` (integer, optional)

## Examples

**Request**

```json
{}
```

**Response**

```json
{
  "groups": [
    {
      "created_at": "2024-05-20T10:15:30Z",
      "description": "Group focused on AI research and development.",
      "external_id": "deprecated-12345",
      "fact_rating_instruction": {
        "examples": {
          "high": "This group specializes in natural language processing advancements.",
          "low": "This group occasionally meets for social events.",
          "medium": "This group has members interested in machine learning."
        },
        "instruction": "Rate facts based on their relevance to AI research projects and contributions."
      },
      "group_id": "grp-001",
      "id": 101,
      "name": "AI Research Team",
      "project_uuid": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
      "uuid": "550e8400-e29b-41d4-a716-446655440000"
    }
  ],
  "row_count": 1,
  "total_count": 1
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.group.getAllGroups({});
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.group.get_all_groups()

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
    request := &zep.GetGroupsOrderedRequest{}
    client.Group.GetAllGroups(
        context.TODO(),
        request,
    )
}

```