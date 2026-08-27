> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Get thread summary

GET https://api.getzep.com/api/v2/threads/{threadId}/summary

Returns the incremental summary generated from messages in the thread. Returns 404 if no summary exists for the thread.

Reference: https://help.getzep.com/sdk-reference/thread/get-thread-summary

## Request

### Path parameters

- `threadId` (string, required) — The thread ID.

## Response

### 200

OK

- `created_at` (string, optional) — CreatedAt is when the summary node was first created.
- `last_summarized_at` (string, optional) — LastSummarizedAt is the wall-clock timestamp of the most recent summary update. This is an ingestion-time watermark; for the event-time recency of the summary's content, use LastSummarizedEpisodeValidAt instead.
- `last_summarized_episode_valid_at` (string, optional) — LastSummarizedEpisodeValidAt is the maximum episode reference time (valid_at) covered by the most recent summary. Use this when answering "how recent is this summary's content in event-time?".
- `summary` (string, optional) — Summary is the incremental summary content.
- `thread_id` (string, optional) — ThreadID is the ID of the thread this summary belongs to. When a thread was created without an explicit thread_id, this field falls back to the thread's UUID. Clients should treat it as an opaque identifier.
- `uuid` (string, optional) — UUID of the derived thread summary node.

## Examples

**Response**

```json
{
  "created_at": "string",
  "last_summarized_at": "string",
  "last_summarized_episode_valid_at": "string",
  "summary": "string",
  "thread_id": "string",
  "uuid": "string"
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.thread.getSummary("threadId");
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.thread.get_summary(
    thread_id="threadId",
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
    client.Thread.GetSummary(
        context.TODO(),
        "threadId",
    )
}

```