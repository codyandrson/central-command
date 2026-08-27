> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Retrieves project information

GET https://api.getzep.com/api/v2/projects/info

Retrieve project info based on the provided api key.

Reference: https://help.getzep.com/sdk-reference/project/retrieves-project-information

## Response

### 200

Retrieved

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
    "created_at": "2023-11-10T09:45:00Z",
    "default_time_zone": "America/New_York",
    "description": "Project for managing customer engagement workflows",
    "name": "Customer Engagement",
    "uuid": "a3f1c9d2-7b4e-4f8a-9c2d-5e6f7a8b9c0d"
  }
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.project.get();
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.project.get()

```

```go
package example

import (
    context "context"

    client "github.com/getzep/zep-go/client"
)

func do() {
    client := client.NewClient()
    client.Project.Get(
        context.TODO(),
    )
}

```