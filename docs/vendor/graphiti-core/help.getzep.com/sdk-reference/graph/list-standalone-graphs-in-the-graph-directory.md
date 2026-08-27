> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# List standalone graphs in the graph directory

GET https://api.getzep.com/api/v2/graph/list-all

Returns a paginated directory of live standalone graphs in the
authenticated project. Optional `search` matches `graph_id`, `name`, and
`description` (metadata only; not graph contents).

Default `pageSize` is 50 (range 1–100). To list users, use
`user.list_ordered` instead. See the
[graph directory guide](/graph-directory) for pagination, relevance
ordering, and Memory MCP exposure.


Reference: https://help.getzep.com/sdk-reference/graph/list-standalone-graphs-in-the-graph-directory

## Request

### Query parameters

- `pageNumber` (integer, optional) — Page number for pagination, starting from 1.
- `pageSize` (integer, optional) — Number of graphs to retrieve per page (default 50, range 1-100; explicit 0 is invalid).
- `search` (string, optional) — Search term for filtering graphs by graph_id, name, or description. Queries longer than 200 Unicode code points after whitespace normalization are invalid.
- `order_by` (string, optional) — Column to sort by (created_at, graph_id, name).
- `asc` (boolean, optional) — Sort in ascending order.

## Response

### 200

Successfully retrieved list of graphs.

- `graphs` (list of object, optional)
  - `created_at` (string, optional)
  - `description` (string, optional)
  - `graph_id` (string, optional)
  - `id` (integer, optional)
  - `name` (string, optional)
  - `project_uuid` (string, optional)
  - `time_zone` (string, optional, nullable)
  - `updated_at` (string, optional)
  - `uuid` (string, optional)
- `page_number` (integer, optional)
- `page_size` (integer, optional)
- `row_count` (integer, optional)
- `total_count` (integer, optional)

## Examples

**Response**

```json
{
  "graphs": [
    {
      "created_at": "string",
      "description": "string",
      "graph_id": "string",
      "id": 1,
      "name": "string",
      "project_uuid": "string",
      "time_zone": "string",
      "updated_at": "string",
      "uuid": "string"
    }
  ],
  "page_number": 1,
  "page_size": 1,
  "row_count": 1,
  "total_count": 1
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.graph.listAll({});
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.graph.list_all()

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
    request := &zep.GraphListAllRequest{}
    client.Graph.ListAll(
        context.TODO(),
        request,
    )
}

```