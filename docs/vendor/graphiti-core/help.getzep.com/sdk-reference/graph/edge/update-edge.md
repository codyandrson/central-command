> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Update Edge

PATCH https://api.getzep.com/api/v2/graph/edge/{uuid}
Content-Type: application/json

Updates an entity edge by UUID.

Reference: https://help.getzep.com/sdk-reference/graph/edge/update-edge

## Request

### Path parameters

- `uuid` (string, required) — Edge UUID

### Body (application/json)

- `attributes` (map from string to any, optional) — Updated attributes. Merged with existing attributes. Set a key to null to delete it.
- `expired_at` (string, optional) — Updated time at which the edge expires
- `fact` (string, optional) — Updated fact for the edge
- `invalid_at` (string, optional) — Updated time at which the fact stopped being true
- `name` (string, optional) — Updated name (relationship type) for the edge
- `valid_at` (string, optional) — Updated time at which the fact becomes true

## Response

### 200

Updated edge

- `created_at` (string, required) — Creation time of the edge
- `fact` (string, required) — Fact representing the edge and nodes that it connects
- `name` (string, required) — Name of the edge, relation name
- `source_node_uuid` (string, required) — UUID of the source node
- `target_node_uuid` (string, required) — UUID of the target node
- `uuid` (string, required) — UUID of the edge
- `attributes` (map from string to any, optional) — Additional attributes of the edge. Dependent on edge types
- `episodes` (list of string, optional) — List of episode ids that reference these entity edges
- `expired_at` (string, optional) — Datetime of when the node was invalidated
- `invalid_at` (string, optional) — Datetime of when the fact stopped being true
- `relevance` (double, optional) — Relevance is an experimental rank-aligned score in [0,1] derived from Score via logit transformation. Only populated when using cross_encoder reranker; omitted for other reranker types (e.g., RRF).
- `scope` (string, optional) — Scope of the edge (e.g. "entity", "maybe_related")
- `score` (double, optional) — Score is the reranker output: sigmoid-distributed logits [0,1] when using cross_encoder reranker, or RRF ordinal rank when using rrf reranker
- `selection_rank` (integer, optional) — SelectionRank is the global cross-scope rank assigned by auto scope selection.
- `source_node_labels` (list of string, optional) — SourceNodeLabels are the labels of the source node at read time. Same read-time-projection semantics as SourceNodeName.
- `source_node_name` (string, optional) — SourceNodeName is the name of the source node at read time. It is a read-time projection of current node state, not a stored edge attribute: a subsequent node rename is reflected on the next read. Omitted (the edge is still returned) if the source node cannot be resolved, for example if it was deleted concurrently.
- `target_node_labels` (list of string, optional) — TargetNodeLabels are the labels of the target node at read time. Same read-time-projection semantics as SourceNodeName.
- `target_node_name` (string, optional) — TargetNodeName is the name of the target node at read time. Same read-time-projection semantics as SourceNodeName.
- `valid_at` (string, optional) — Datetime of when the fact became true

## Examples

**Request**

```json
{}
```

**Response**

```json
{
  "created_at": "string",
  "fact": "string",
  "name": "string",
  "source_node_uuid": "string",
  "target_node_uuid": "string",
  "uuid": "string",
  "attributes": {},
  "episodes": [
    "string"
  ],
  "expired_at": "string",
  "invalid_at": "string",
  "relevance": 1.1,
  "scope": "string",
  "score": 1.1,
  "selection_rank": 1,
  "source_node_labels": [
    "string"
  ],
  "source_node_name": "string",
  "target_node_labels": [
    "string"
  ],
  "target_node_name": "string",
  "valid_at": "string"
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.graph.edge.update("uuid", {});
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.graph.edge.update(
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
    request := &graph.UpdateEdgeRequest{}
    client.Graph.Edge.Update(
        context.TODO(),
        "uuid",
        request,
    )
}

```