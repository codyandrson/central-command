> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Get Group Episodes

GET https://api.getzep.com/api/v2/graph/episodes/group/{group_id}

Returns episodes by group id.

Reference: https://help.getzep.com/v2/sdk-reference/graph/episode/get-group-episodes

## Request

### Path parameters

- `group_id` (string, required) — Group ID

### Query parameters

- `lastn` (integer, optional) — The number of most recent episodes to retrieve.

## Response

### 200

Episodes

- `episodes` (list of object, optional)
  - `content` (string, required)
  - `created_at` (string, required)
  - `uuid` (string, required)
  - `processed` (boolean, optional)
  - `role` (string, optional) — Optional role, will only be present if the episode was created using memory.add API
  - `role_type` (enum, optional) — Optional role_type, will only be present if the episode was created using memory.add API
    - Allowed values: `norole`, `system`, `assistant`, `user`, `function`, `tool`
  - `session_id` (string, optional)
  - `source` (enum, optional)
    - Allowed values: `text`, `json`, `message`
  - `source_description` (string, optional)

## Examples

**Request**

```json
{}
```

**Response**

```json
{
  "episodes": [
    {
      "content": "Discussed project milestones and assigned tasks for next sprint.",
      "created_at": "2024-06-10T09:15:00Z",
      "uuid": "e7a1c9d2-3f4b-4a6d-9c8e-1b2f3d4e5a6b",
      "processed": true,
      "role": "project_manager",
      "role_type": "user",
      "session_id": "session-8f3b2c7d-4a1e-4d9a-bf3e-2a7c9d5e6f12",
      "source": "text",
      "source_description": "Meeting transcript from Zoom call"
    }
  ]
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.graph.episode.getByGroupId("group_id", {});
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.graph.episode.get_by_group_id(
    group_id="group_id",
)

```

```go
package example

import (
    context "context"

    client "github.com/getzep/zep-go/client"
    graph "github.com/getzep/zep-go/graph"
)

func do() {
    client := client.NewClient()
    request := &graph.EpisodeGetByGroupIDRequest{}
    client.Graph.Episode.GetByGroupID(
        context.TODO(),
        "group_id",
        request,
    )
}

```