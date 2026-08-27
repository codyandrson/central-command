> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Synthesize a question

GET https://api.getzep.com/api/v2/sessions/{sessionId}/synthesize_question

Deprecated API: Synthesize a question from the last N messages in the chat history.

Reference: https://help.getzep.com/v2/sdk-reference/deprecated/memory/synthesize-question

## Request

### Path parameters

- `sessionId` (string, required) — The ID of the session.

### Query parameters

- `lastNMessages` (integer, optional) — The number of messages to use for question synthesis.

## Response

### 200

The synthesized question.

- `question` (string, optional)

## Examples

**Request**

```json
{}
```

**Response**

```json
{
  "question": "What are the key points discussed in the last few messages?"
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.memory.synthesizeQuestion("sessionId", {});
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.memory.synthesize_question(
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
    request := &zep.MemorySynthesizeQuestionRequest{}
    client.Memory.SynthesizeQuestion(
        context.TODO(),
        "sessionId",
        request,
    )
}

```