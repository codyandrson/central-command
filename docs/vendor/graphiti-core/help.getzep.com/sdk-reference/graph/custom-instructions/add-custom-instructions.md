> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Add Custom Instructions

POST https://api.getzep.com/api/v2/custom-instructions
Content-Type: application/json

Adds new custom instructions for graphs without removing existing ones. If user_ids or graph_ids is empty, adds to project-wide default instructions.

Reference: https://help.getzep.com/sdk-reference/graph/custom-instructions/add-custom-instructions

## Request

### Body (application/json)

- `instructions` (list of object, required) — Instructions to add to the graph.
  - `name` (string, required)
  - `text` (string, required)
- `graph_ids` (list of string, optional) — Graph IDs to add the instructions to. If empty, the instructions are added to the project-wide default.
- `user_ids` (list of string, optional) — User IDs to add the instructions to. If empty, the instructions are added to the project-wide default.

## Response

### 200

Instructions added successfully

- `message` (string, optional)

## Examples

**Request**

```json
{
  "instructions": [
    {
      "name": "Data Privacy Guidelines",
      "text": "Ensure all user data is anonymized before processing and never store sensitive information without encryption."
    }
  ]
}
```

**Response**

```json
{
  "message": "Custom instructions added successfully to the project-wide defaults."
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.graph.addCustomInstructions({
        instructions: [
            {
                name: "Data Privacy Guidelines",
                text: "Ensure all user data is anonymized before processing and never store sensitive information without encryption.",
            },
        ],
    });
}
main();

```

```python
from zep_cloud import Zep, CustomInstruction

client = Zep()

client.graph.add_custom_instructions(
    instructions=[
        CustomInstruction(
            name="Data Privacy Guidelines",
            text="Ensure all user data is anonymized before processing and never store sensitive information without encryption.",
        )
    ],
)

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
    request := &zep.AddCustomInstructionsRequest{
        Instructions: []*zep.CustomInstruction{
            &zep.CustomInstruction{
                Name: "Data Privacy Guidelines",
                Text: "Ensure all user data is anonymized before processing and never store sensitive information without encryption.",
            },
        },
    }
    client.Graph.AddCustomInstructions(
        context.TODO(),
        request,
    )
}

```