> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Delete Context Template

DELETE https://api.getzep.com/api/v2/context-templates/{template_id}

Deletes a context template by template_id.

Reference: https://help.getzep.com/sdk-reference/context/delete-context-template

## Request

### Path parameters

- `template_id` (string, required) — Template ID

## Response

### 200

Template deleted successfully

- `message` (string, optional)

## Examples

**Request**

```json
{}
```

**Response**

```json
{
  "message": "Context template deleted successfully."
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.context.deleteContextTemplate("template_id");
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.context.delete_context_template(
    template_id="template_id",
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
    client.Context.DeleteContextTemplate(
        context.TODO(),
        "template_id",
    )
}

```