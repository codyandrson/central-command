> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Returns a fact by UUID

GET https://api.getzep.com/api/v2/facts/{factUUID}

Deprecated API: get fact by uuid

Reference: https://help.getzep.com/v2/sdk-reference/deprecated/memory/get-fact

## Request

### Path parameters

- `factUUID` (string, required) — Fact UUID

## Response

### 200

The fact with the specified UUID.

- `fact` (object, optional)
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
  "fact": {
    "content": "The honeybee can recognize human faces.",
    "created_at": "2023-11-10T09:15:00Z",
    "fact": "Deprecated",
    "uuid": "a3f1c9e2-7b4d-4f8a-9c2e-1d2f3b4a5c6d",
    "expired_at": "2025-11-10T09:15:00Z",
    "invalid_at": "2024-12-01T00:00:00Z",
    "name": "Honeybee Face Recognition",
    "rating": 4.7,
    "source_node_name": "Insect Behavior Studies",
    "target_node_name": "Cognitive Science",
    "valid_at": "2023-11-10T09:15:00Z"
  }
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.memory.getFact("factUUID");
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.memory.get_fact(
    fact_uuid="factUUID",
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
    client.Memory.GetFact(
        context.TODO(),
        "factUUID",
    )
}

```