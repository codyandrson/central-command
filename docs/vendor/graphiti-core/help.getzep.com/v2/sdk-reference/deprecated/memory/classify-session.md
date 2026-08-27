> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Deprecated: Classify Session

POST https://api.getzep.com/api/v2/sessions/{sessionId}/classify
Content-Type: application/json

Deprecated: Classifies a session.

Reference: https://help.getzep.com/v2/sdk-reference/deprecated/memory/classify-session

## Request

### Path parameters

- `sessionId` (string, required) — Session ID

### Body (application/json)

- `classes` (list of string, required) — The classes to use for classification.
- `name` (string, required) — The name of the classifier.
- `instruction` (string, optional) — Custom instruction to use for classification.
- `last_n` (integer, optional, default: 4) — The number of session messages to consider for classification. Defaults to 4.
- `persist` (boolean, optional, default: true) — Deprecated

## Response

### 200

A response object containing the name and classification result.

- `class` (string, optional)
- `label` (string, optional)

## Examples

**Request**

```json
{
  "classes": [
    "urgent",
    "follow-up",
    "information"
  ],
  "name": "Support Ticket Classifier"
}
```

**Response**

```json
{
  "class": "urgent",
  "label": "High Priority"
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.memory.classifySession("sessionId", {
        classes: [
            "urgent",
            "follow-up",
            "information",
        ],
        name: "Support Ticket Classifier",
    });
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.memory.classify_session(
    session_id="sessionId",
    classes=[
        "urgent",
        "follow-up",
        "information"
    ],
    name="Support Ticket Classifier",
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
    request := &zep.ClassifySessionRequest{
        Classes: []string{
            "urgent",
            "follow-up",
            "information",
        },
        Name: "Support Ticket Classifier",
    }
    client.Memory.ClassifySession(
        context.TODO(),
        "sessionId",
        request,
    )
}

```