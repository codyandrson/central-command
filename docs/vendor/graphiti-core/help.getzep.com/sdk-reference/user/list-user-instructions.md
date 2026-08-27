> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# List User Instructions

GET https://api.getzep.com/api/v2/user-summary-instructions

Lists all user summary instructions for a project, user.

Reference: https://help.getzep.com/sdk-reference/user/list-user-instructions

## Request

### Query parameters

- `user_id` (string, optional) — User ID to get user-specific instructions

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
      "name": "Welcome Message",
      "text": "Please review your project summary carefully before submission."
    },
    {
      "name": "Data Privacy Notice",
      "text": "Ensure all user data is anonymized in the summary report."
    },
    {
      "name": "Formatting Guidelines",
      "text": "Use bullet points for key findings and keep text under 100 characters."
    }
  ]
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.user.listUserSummaryInstructions({});
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.user.list_user_summary_instructions()

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
    request := &zep.UserListUserSummaryInstructionsRequest{}
    client.User.ListUserSummaryInstructions(
        context.TODO(),
        request,
    )
}

```