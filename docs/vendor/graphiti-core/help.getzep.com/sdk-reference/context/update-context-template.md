> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Update Context Template

PUT https://api.getzep.com/api/v2/context-templates/{template_id}
Content-Type: application/json

Updates an existing context template by template_id.

Reference: https://help.getzep.com/sdk-reference/context/update-context-template

## Request

### Path parameters

- `template_id` (string, required) — Template ID

### Body (application/json)

- `template` (string, required) — The template content (max 1200 characters).

## Response

### 200

The updated context template.

- `template` (string, optional) — The template content.
- `template_id` (string, optional) — Unique identifier for the template (max 100 characters).
- `uuid` (string, optional) — Unique identifier for the template.

## Examples

**Request**

```json
{
  "template": "Dear {{user_name}}, your appointment is confirmed for {{appointment_date}} at {{appointment_time}}."
}
```

**Response**

```json
{
  "template": "Dear {{user_name}}, your appointment is confirmed for {{appointment_date}} at {{appointment_time}}.",
  "template_id": "appt-confirm-2024",
  "uuid": "a3f1c9e2-7b4d-4f8a-9c2e-1d2f3b4a5c6d"
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.context.updateContextTemplate("template_id", {
        template: "Dear {{user_name}}, your appointment is confirmed for {{appointment_date}} at {{appointment_time}}.",
    });
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.context.update_context_template(
    template_id="template_id",
    template="Dear {{user_name}}, your appointment is confirmed for {{appointment_date}} at {{appointment_time}}.",
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
    request := &zep.UpdateContextTemplateRequest{
        Template: "Dear {{user_name}}, your appointment is confirmed for {{appointment_date}} at {{appointment_time}}.",
    }
    client.Context.UpdateContextTemplate(
        context.TODO(),
        "template_id",
        request,
    )
}

```