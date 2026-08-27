> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Create Context Template

POST https://api.getzep.com/api/v2/context-templates
Content-Type: application/json

Creates a new context template.

Reference: https://help.getzep.com/sdk-reference/context/create-context-template

## Request

### Body (application/json)

- `template` (string, required) — The template content (max 1200 characters).
- `template_id` (string, required) — Unique identifier for the template (max 100 characters).

## Response

### 200

The created context template.

- `template` (string, optional) — The template content.
- `template_id` (string, optional) — Unique identifier for the template (max 100 characters).
- `uuid` (string, optional) — Unique identifier for the template.

## Examples

**Request**

```json
{
  "template": "Dear {{user_name}}, your appointment is scheduled for {{appointment_date}} at {{appointment_time}}. Please arrive 10 minutes early.",
  "template_id": "appointment_reminder_v1"
}
```

**Response**

```json
{
  "template": "Dear {{user_name}}, your appointment is scheduled for {{appointment_date}} at {{appointment_time}}. Please arrive 10 minutes early.",
  "template_id": "appointment_reminder_v1",
  "uuid": "3fa85f64-5717-4562-b3fc-2c963f66afa6"
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.context.createContextTemplate({
        template: "Dear {{user_name}}, your appointment is scheduled for {{appointment_date}} at {{appointment_time}}. Please arrive 10 minutes early.",
        templateId: "appointment_reminder_v1",
    });
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.context.create_context_template(
    template="Dear {{user_name}}, your appointment is scheduled for {{appointment_date}} at {{appointment_time}}. Please arrive 10 minutes early.",
    template_id="appointment_reminder_v1",
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
    request := &zep.CreateContextTemplateRequest{
        Template: "Dear {{user_name}}, your appointment is scheduled for {{appointment_date}} at {{appointment_time}}. Please arrive 10 minutes early.",
        TemplateID: "appointment_reminder_v1",
    }
    client.Context.CreateContextTemplate(
        context.TODO(),
        request,
    )
}

```