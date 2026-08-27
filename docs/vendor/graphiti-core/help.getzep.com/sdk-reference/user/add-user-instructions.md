> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Add User Instructions

POST https://api.getzep.com/api/v2/user-summary-instructions
Content-Type: application/json

Adds new summary instructions for users graphs without removing existing ones. If user_ids is empty, adds to project-wide default instructions.

Reference: https://help.getzep.com/sdk-reference/user/add-user-instructions

## Request

### Body (application/json)

- `instructions` (list of object, required) — Instructions to add to the user summary generation.
  - `name` (string, required)
  - `text` (string, required)
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
      "name": "Highlight Key Metrics",
      "text": "Emphasize the most important performance indicators in the user summary."
    }
  ]
}
```

**Response**

```json
{
  "message": "User summary instructions added successfully."
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.user.addUserSummaryInstructions({
        instructions: [
            {
                name: "Highlight Key Metrics",
                text: "Emphasize the most important performance indicators in the user summary.",
            },
        ],
    });
}
main();

```

```python
from zep_cloud import Zep, UserInstruction

client = Zep()

client.user.add_user_summary_instructions(
    instructions=[
        UserInstruction(
            name="Highlight Key Metrics",
            text="Emphasize the most important performance indicators in the user summary.",
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
    request := &zep.AddUserInstructionsRequest{
        Instructions: []*zep.UserInstruction{
            &zep.UserInstruction{
                Name: "Highlight Key Metrics",
                Text: "Emphasize the most important performance indicators in the user summary.",
            },
        },
    }
    client.User.AddUserSummaryInstructions(
        context.TODO(),
        request,
    )
}

```