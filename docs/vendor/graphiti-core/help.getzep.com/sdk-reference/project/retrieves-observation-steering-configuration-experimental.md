> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Retrieves observation steering configuration (Experimental)

GET https://api.getzep.com/api/v2/projects/observation-steering

Returns project steering or the effective user/graph steering with project fallback. This API is experimental and may change in future releases.

Reference: https://help.getzep.com/sdk-reference/project/retrieves-observation-steering-configuration-experimental

## Request

### Query parameters

- `user_id` (string, optional) — User ID for user-specific steering
- `graph_id` (string, optional) — Graph ID for graph-specific steering

## Response

### 200

Retrieved

- `instruction` (string, optional, nullable)
- `types` (list of object, optional)
  - `description` (string, required)
  - `name` (string, required)

## Examples

**Request**

```json
{}
```

**Response**

```json
{
  "instruction": "Prioritize observations related to customer sentiment and product feedback to improve feature development.",
  "types": [
    {
      "description": "User feedback collected from in-app surveys and support tickets.",
      "name": "User Feedback"
    },
    {
      "description": "System logs capturing error rates and performance metrics.",
      "name": "System Logs"
    },
    {
      "description": "Usage data tracking feature adoption and engagement levels.",
      "name": "Usage Data"
    }
  ]
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.project.getObservationSteering({});
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.project.get_observation_steering()

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
    request := &zep.GetObservationSteeringRequest{}
    client.Project.GetObservationSteering(
        context.TODO(),
        request,
    )
}

```