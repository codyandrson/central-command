> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# List Context Templates

GET https://api.getzep.com/api/v2/context-templates

Lists all context templates.

Reference: https://help.getzep.com/sdk-reference/context/list-context-templates

## Response

### 200

The list of context templates.

- `templates` (list of object, optional)
  - `template` (string, optional) — The template content.
  - `template_id` (string, optional) — Unique identifier for the template (max 100 characters).
  - `uuid` (string, optional) — Unique identifier for the template.

## Examples

**Request**

```json
{}
```

**Response**

```json
{
  "templates": [
    {
      "template": "Dear {{user_name}}, your appointment is scheduled for {{appointment_date}} at {{appointment_time}}.",
      "template_id": "appointment_reminder_v1",
      "uuid": "a3f1c9d2-4b7e-4f8a-9c3d-2e5f7b8a1c9d"
    }
  ]
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.context.listContextTemplates();
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.context.list_context_templates()

```

```go
package example

import (
    context "context"

    client "github.com/getzep/zep-go/client"
)

func do() {
    client := client.NewClient()
    client.Context.ListContextTemplates(
        context.TODO(),
    )
}

```