> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# List Custom Instructions

GET https://api.getzep.com/api/v2/custom-instructions

Lists all custom instructions for a project, user, or graph.

Reference: https://help.getzep.com/sdk-reference/graph/custom-instructions/list-custom-instructions

## Request

### Query parameters

- `user_id` (string, optional) — User ID to get user-specific instructions
- `graph_id` (string, optional) — Graph ID to get graph-specific instructions

## Response

### 200

The list of instructions.

- `instructions` (list of object, optional)
  - `name` (string, required)
  - `text` (string, required)

## Examples

**Request**

```json
{}
```

**Response**

```json
{
  "instructions": [
    {
      "name": "User Onboarding Guide",
      "text": "Welcome new users with a friendly introduction and provide step-by-step instructions to get started with the platform."
    }
  ]
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.graph.listCustomInstructions({});
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.graph.list_custom_instructions()

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
    request := &zep.GraphListCustomInstructionsRequest{}
    client.Graph.ListCustomInstructions(
        context.TODO(),
        request,
    )
}

```