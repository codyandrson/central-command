> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Get threads

GET https://api.getzep.com/api/v2/threads

Returns all threads.

Reference: https://help.getzep.com/sdk-reference/thread/get-threads

## Request

### Query parameters

- `page_number` (integer, optional) — Page number for pagination, starting from 1
- `page_size` (integer, optional) — Number of threads to retrieve per page.
- `order_by` (string, optional) — Field to order the results by: created_at, updated_at, user_id, thread_id.
- `asc` (boolean, optional) — Order direction: true for ascending, false for descending.

## Response

### 200

List of threads

- `response_count` (integer, optional)
- `threads` (list of object, optional)
  - `created_at` (string, optional)
  - `project_uuid` (string, optional)
  - `thread_id` (string, optional)
  - `user_id` (string, optional)
  - `user_uuid` (string, optional)
  - `uuid` (string, optional)
- `total_count` (integer, optional)

## Examples

**Request**

```json
{}
```

**Response**

```json
{
  "response_count": 1,
  "threads": [
    {
      "created_at": "2024-06-10T09:15:00Z",
      "project_uuid": "a3f1c9d2-4b7e-4f8a-9c3d-2e5b7f6a1d9e",
      "thread_id": "thread-12345",
      "user_id": "user-67890",
      "user_uuid": "d4e5f6a7-b8c9-4d0e-9f1a-2b3c4d5e6f7g",
      "uuid": "e7f8a9b0-c1d2-4e3f-8a9b-0c1d2e3f4a5b"
    }
  ],
  "total_count": 1
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.thread.listAll({
        pageNumber: 1,
        pageSize: 20,
        orderBy: "created_at",
        asc: false,
    });
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.thread.list_all(
    page_number=1,
    page_size=20,
    order_by="created_at",
    asc=False,
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
    request := &zep.ThreadListAllRequest{
        PageNumber: zep.Int(
            1,
        ),
        PageSize: zep.Int(
            20,
        ),
        OrderBy: zep.String(
            "created_at",
        ),
        Asc: zep.Bool(
            false,
        ),
    }
    client.Thread.ListAll(
        context.TODO(),
        request,
    )
}

```