> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Update Node

PATCH https://api.getzep.com/api/v2/graph/node/{uuid}
Content-Type: application/json

Updates an entity node by UUID.

Reference: https://help.getzep.com/sdk-reference/graph/node/update-node

## Request

### Path parameters

- `uuid` (string, required) — Node UUID

### Body (application/json)

- `attributes` (map from string to any, optional) — Updated attributes. Merged with existing attributes. Set a key to null to delete it.
- `labels` (list of string, optional) — Updated labels for the node
- `name` (string, optional) — Updated name for the node
- `summary` (string, optional) — Updated summary for the node

## Response

### 200

Updated node

- `created_at` (string, required) — Creation time of the node
- `name` (string, required) — Name of the node
- `summary` (string, required) — Regional summary of surrounding edges
- `uuid` (string, required) — UUID of the node
- `attributes` (map from string to any, optional) — Additional attributes of the node. Dependent on node labels
- `labels` (list of string, optional) — Labels associated with the node
- `relevance` (double, optional) — Relevance is an experimental rank-aligned score in [0,1] derived from Score via logit transformation. Only populated when using cross_encoder reranker; omitted for other reranker types (e.g., RRF).
- `score` (double, optional) — Score is the reranker output: sigmoid-distributed logits [0,1] when using cross_encoder reranker, or RRF ordinal rank when using rrf reranker
- `selection_rank` (integer, optional) — SelectionRank is the global cross-scope rank assigned by auto scope selection.

## Examples

**Request**

```json
{}
```

**Response**

```json
{
  "created_at": "2024-06-01T12:00:00Z",
  "name": "John Doe",
  "summary": "Key team member in the marketing department with expertise in digital campaigns.",
  "uuid": "123e4567-e89b-12d3-a456-426614174000",
  "attributes": {},
  "labels": [
    "person",
    "employee"
  ],
  "relevance": 0.85,
  "score": 0.92,
  "selection_rank": 3
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.graph.node.update("uuid", {});
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.graph.node.update(
    uuid_="uuid",
)

```

```go
package example

import (
    context "context"

    client "github.com/getzep/zep-go/client"
    graph "github.com/getzep/zep-go/graph"
)

func do() {
    client := client.NewClient()
    request := &graph.UpdateNodeRequest{}
    client.Graph.Node.Update(
        context.TODO(),
        "uuid",
        request,
    )
}

```