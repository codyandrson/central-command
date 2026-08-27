> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Delete Custom Instructions

DELETE https://api.getzep.com/api/v2/custom-instructions
Content-Type: application/json

Deletes custom instructions for graphs or project wide defaults.

Reference: https://help.getzep.com/sdk-reference/graph/custom-instructions/delete-custom-instructions

## Request

### Body (application/json)

- `graph_ids` (list of string, optional) — Determines which group graphs will have their custom instructions deleted. If no graphs are provided, the project-wide custom instructions will be affected.
- `instruction_names` (list of string, optional) — Unique identifier for the instructions to be deleted. If empty deletes all instructions.
- `user_ids` (list of string, optional) — Determines which user graphs will have their custom instructions deleted. If no users are provided, the project-wide custom instructions will be affected.

## Response

### 200

Instructions deleted successfully

- `message` (string, optional)

## Examples

**Request**

```json
{}
```

**Response**

```json
{
  "message": "Custom instructions deleted successfully."
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.graph.deleteCustomInstructions({});
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.graph.delete_custom_instructions()

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
    request := &zep.DeleteCustomInstructionsRequest{}
    client.Graph.DeleteCustomInstructions(
        context.TODO(),
        request,
    )
}

```