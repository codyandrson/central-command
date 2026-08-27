> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Delete a fact for the given UUID

DELETE https://api.getzep.com/api/v2/facts/{factUUID}

Deprecated API: delete a fact

Reference: https://help.getzep.com/v2/sdk-reference/deprecated/memory/delete-fact

## Request

### Path parameters

- `factUUID` (string, required) — Fact UUID

## Response

### 200

Deleted

- `message` (string, optional)

## Examples

**Request**

```json
{}
```

**Response**

```json
{
  "message": "Fact successfully deleted."
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.memory.deleteFact("factUUID");
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.memory.delete_fact(
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
    client.Memory.DeleteFact(
        context.TODO(),
        "factUUID",
    )
}

```