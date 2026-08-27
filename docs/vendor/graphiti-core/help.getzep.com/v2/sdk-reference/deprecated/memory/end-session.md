> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# End a session

POST https://api.getzep.com/api/v2/sessions/{sessionId}/end
Content-Type: application/json

Deprecated API: End a session by ID.

Reference: https://help.getzep.com/v2/sdk-reference/deprecated/memory/end-session

## Request

### Path parameters

- `sessionId` (string, required) — Session ID

### Body (application/json)

- `classify` (object, optional)
  - `classes` (list of string, required) — The classes to use for classification.
  - `name` (string, required) — The name of the classifier.
  - `instruction` (string, optional) — Custom instruction to use for classification.
  - `last_n` (integer, optional, default: 4) — The number of session messages to consider for classification. Defaults to 4.
  - `persist` (boolean, optional, default: true) — Deprecated
- `instruction` (string, optional)

## Response

### 200

OK

- `classification` (object, optional)
  - `class` (string, optional)
  - `label` (string, optional)
- `session` (object, optional)
  - `classifications` (map from string to string, optional)
  - `created_at` (string, optional)
  - `deleted_at` (string, optional)
  - `ended_at` (string, optional)
  - `fact_rating_instruction` (object, optional) — Deprecated
    - `examples` (object, optional) — Examples is a list of examples that demonstrate how facts might be rated based on your instruction. You should provide an example of a highly rated example, a low rated example, and a medium (or in between example). For example, if you are rating based on relevance to a trip planning application, your examples might be: High: "Joe's dream vacation is Bali" Medium: "Joe has a fear of flying", Low: "Joe's favorite food is Japanese",
      - `high` (string, optional)
      - `low` (string, optional)
      - `medium` (string, optional)
    - `instruction` (string, optional) — A string describing how to rate facts as they apply to your application. A trip planning application may use something like "relevancy to planning a trip, the user's preferences when traveling, or the user's travel history."
  - `facts` (list of string, optional) — Deprecated
  - `id` (integer, optional)
  - `metadata` (map from string to any, optional) — Deprecated
  - `project_uuid` (string, optional)
  - `session_id` (string, optional)
  - `updated_at` (string, optional) — Deprecated
  - `user_id` (string, optional)
  - `uuid` (string, optional)

## Examples

**Request**

```json
{}
```

**Response**

```json
{
  "classification": {
    "class": "support_ticket",
    "label": "Customer Support"
  },
  "session": {
    "classifications": {},
    "created_at": "2024-06-10T09:15:00Z",
    "deleted_at": "2024-06-15T12:00:00Z",
    "ended_at": "2024-06-15T11:45:00Z",
    "fact_rating_instruction": {
      "examples": {
        "high": "The customer reported an issue with login failures.",
        "low": "The customer mentioned their favorite color is blue.",
        "medium": "The customer asked about password reset procedures."
      },
      "instruction": "Rate facts based on their relevance to resolving customer support issues."
    },
    "facts": [
      "User experienced login errors on multiple devices."
    ],
    "id": 12345,
    "metadata": {},
    "project_uuid": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "session_id": "sess-9876543210",
    "updated_at": "2024-06-15T11:50:00Z",
    "user_id": "user-54321",
    "uuid": "550e8400-e29b-41d4-a716-446655440000"
  }
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.memory.endSession("sessionId", {});
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.memory.end_session(
    session_id="sessionId",
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
    request := &zep.EndSessionRequest{}
    client.Memory.EndSession(
        context.TODO(),
        "sessionId",
        request,
    )
}

```