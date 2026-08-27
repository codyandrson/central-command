> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Updates project time-zone information

PATCH https://api.getzep.com/api/v2/projects/info
Content-Type: application/json

Sets or clears the project-level fallback time zone for the API key's project.

Reference: https://help.getzep.com/sdk-reference/project/updates-project-time-zone-information

## Request

### Body (application/json)

- `default_time_zone` (string, optional, nullable) — The project's IANA fallback time zone. Null clears the existing value.

## Response

### 200

Updated

- `project` (object, optional)
  - `created_at` (string, optional)
  - `default_time_zone` (string, optional, nullable)
  - `description` (string, optional)
  - `name` (string, optional)
  - `uuid` (string, optional)

## Examples

**Request**

```json
{}
```

**Response**

```json
{
  "project": {
    "created_at": "2023-11-20T09:45:00Z",
    "default_time_zone": "America/New_York",
    "description": "Primary project for customer engagement analytics",
    "name": "Customer Analytics",
    "uuid": "a3f1c9e2-7b4d-4f8a-9c2e-123456789abc"
  }
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.project.update({});
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.project.update()

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
    request := &zep.UpdateProjectInfoRequest{}
    client.Project.Update(
        context.TODO(),
        request,
    )
}

```