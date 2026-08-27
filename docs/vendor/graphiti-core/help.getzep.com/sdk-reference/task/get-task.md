> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Get Task

GET https://api.getzep.com/api/v2/tasks/{task_id}

Gets a task by its ID

Reference: https://help.getzep.com/sdk-reference/task/get-task

## Request

### Path parameters

- `task_id` (string, required) — Task ID

## Response

### 200

Task

- `completed_at` (string, optional)
- `created_at` (string, optional)
- `error` (object, optional)
  - `code` (string, optional)
  - `details` (map from string to any, optional)
  - `message` (string, optional)
- `params` (map from string to any, optional)
- `progress` (object, optional)
  - `message` (string, optional)
  - `stage` (string, optional)
- `started_at` (string, optional)
- `status` (string, optional)
- `task_id` (string, optional)
- `type` (string, optional)
- `updated_at` (string, optional)

## Examples

**Request**

```json
{}
```

**Response**

```json
{
  "completed_at": "2024-06-10T15:45:00Z",
  "created_at": "2024-06-10T14:00:00Z",
  "error": {
    "code": "TASK_TIMEOUT",
    "details": {
      "timeout_seconds": 3600,
      "attempt": 3
    },
    "message": "The task exceeded the maximum allowed execution time."
  },
  "params": {
    "user_id": "user_789",
    "priority": "high",
    "retry": false
  },
  "progress": {
    "message": "Processing data batch 4 of 10",
    "stage": "in_progress"
  },
  "started_at": "2024-06-10T14:05:00Z",
  "status": "failed",
  "task_id": "task_1234567890abcdef",
  "type": "data_processing",
  "updated_at": "2024-06-10T15:40:00Z"
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.task.get("task_id");
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.task.get(
    task_id="task_id",
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
    client.Task.Get(
        context.TODO(),
        "task_id",
    )
}

```