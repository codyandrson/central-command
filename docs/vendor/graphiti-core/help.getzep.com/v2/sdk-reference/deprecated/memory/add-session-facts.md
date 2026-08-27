> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Adds facts to a session

POST https://api.getzep.com/api/v2/sessions/{sessionId}/facts
Content-Type: application/json

Deprecated API: Adds facts to a session

Reference: https://help.getzep.com/v2/sdk-reference/deprecated/memory/add-session-facts

## Request

### Path parameters

- `sessionId` (string, required) — Session ID

### Body (application/json)

- `facts` (list of object, required)
  - `fact` (string, required)

## Response

### 200

OK

- `message` (string, optional)

## Examples

**Request**

```json
{
  "facts": [
    {
      "fact": "The session was initiated by user ID 12345 at 2024-06-01T10:15:30Z."
    }
  ]
}
```

**Response**

```json
{
  "message": "Facts successfully added to session."
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.memory.addSessionFacts("sessionId", {
        facts: [
            {
                fact: "The session was initiated by user ID 12345 at 2024-06-01T10:15:30Z.",
            },
        ],
    });
}
main();

```

```python
from zep_cloud import Zep, NewFact

client = Zep()

client.memory.add_session_facts(
    session_id="sessionId",
    facts=[
        NewFact(
            fact="The session was initiated by user ID 12345 at 2024-06-01T10:15:30Z.",
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
    request := &zep.AddFactsRequest{
        Facts: []*zep.NewFact{
            &zep.NewFact{
                Fact: "The session was initiated by user ID 12345 at 2024-06-01T10:15:30Z.",
            },
        },
    }
    client.Memory.AddSessionFacts(
        context.TODO(),
        "sessionId",
        request,
    )
}

```