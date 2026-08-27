> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Delete User Instructions

DELETE https://api.getzep.com/api/v2/user-summary-instructions
Content-Type: application/json

Deletes user summary/instructions for users or project wide defaults.

Reference: https://help.getzep.com/sdk-reference/user/delete-user-instructions

## Request

### Body (application/json)

- `instruction_names` (list of string, optional) — Unique identifier for the instructions to be deleted. If empty deletes all instructions.
- `user_ids` (list of string, optional) — Determines which users will have their custom instructions deleted. If no users are provided, the project-wide custom instructions will be effected.

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
  "message": "User summary instructions deleted successfully."
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.user.deleteUserSummaryInstructions({});
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.user.delete_user_summary_instructions()

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
    request := &zep.DeleteUserInstructionsRequest{}
    client.User.DeleteUserSummaryInstructions(
        context.TODO(),
        request,
    )
}

```