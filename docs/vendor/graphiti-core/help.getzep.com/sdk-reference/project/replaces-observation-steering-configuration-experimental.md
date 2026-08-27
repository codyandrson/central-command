> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Replaces observation steering configuration (Experimental)

PUT https://api.getzep.com/api/v2/projects/observation-steering
Content-Type: application/json

Replaces project, user, or graph steering. An empty configuration clears the project default or removes the user/graph override. Changes affect later materializer runs only. This API is experimental and may change in future releases.

Reference: https://help.getzep.com/sdk-reference/project/replaces-observation-steering-configuration-experimental

## Request

### Query parameters

- `user_id` (string, optional) — User ID for user-specific steering
- `graph_id` (string, optional) — Graph ID for graph-specific steering

### Body (application/json)

- `instruction` (string, optional, nullable)
- `types` (list of object, optional)
  - `description` (string, required)
  - `name` (string, required)

## Response

### 200

Updated

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
  "instruction": "Prioritize observations related to user engagement and error rates for improved materialization accuracy.",
  "types": [
    {
      "description": "Observations capturing user interaction events such as clicks, scrolls, and form submissions.",
      "name": "user_interaction"
    }
  ]
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.project.setObservationSteering({
        body: {},
    });
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.project.set_observation_steering()

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
    request := &zep.SetObservationSteeringRequest{
        Body: &zep.ObservationSteeringConfig{},
    }
    client.Project.SetObservationSteering(
        context.TODO(),
        request,
    )
}

```