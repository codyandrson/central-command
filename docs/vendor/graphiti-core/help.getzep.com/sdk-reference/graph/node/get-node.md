> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Get Node

GET https://api.getzep.com/api/v2/graph/node/{uuid}

Returns a specific node by its UUID.

Reference: https://help.getzep.com/sdk-reference/graph/node/get-node

## Request

### Path parameters

- `uuid` (string, required) — Node UUID

## Response

### 200

Node

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
  "created_at": "2024-05-20T09:15:00Z",
  "name": "John Doe",
  "summary": "Key user node representing an active customer in the New York region.",
  "uuid": "123e4567-e89b-12d3-a456-426614174000",
  "attributes": {
    "category": "Person",
    "age": 34,
    "location": "New York"
  },
  "labels": [
    "user",
    "active"
  ],
  "relevance": 0.87,
  "score": 0.92,
  "selection_rank": 3
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.graph.node.get("uuid");
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.graph.node.get(
    uuid_="uuid",
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
    client.Graph.Node.Get(
        context.TODO(),
        "uuid",
    )
}

```