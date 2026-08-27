> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Get Context Template

GET https://api.getzep.com/api/v2/context-templates/{template_id}

Retrieves a context template by template_id.

Reference: https://help.getzep.com/sdk-reference/context/get-context-template

## Request

### Path parameters

- `template_id` (string, required) — Template ID

## Response

### 200

The context template.

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
  "template": "Dear {{user.firstName}}, your appointment is scheduled for {{appointment.date}} at {{appointment.time}}.",
  "template_id": "appointment-reminder-001",
  "uuid": "a3f1c9e2-7b4d-4f8a-9c2e-5d6f7a8b9c0d"
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.context.getContextTemplate("template_id");
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.context.get_context_template(
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
    client.Context.GetContextTemplate(
        context.TODO(),
        "template_id",
    )
}

```