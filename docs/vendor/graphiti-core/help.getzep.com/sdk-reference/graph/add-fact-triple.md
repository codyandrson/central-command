> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Add Fact Triple

POST https://api.getzep.com/api/v2/graph/add-fact-triple
Content-Type: application/json

Add a fact triple for a user or group

Reference: https://help.getzep.com/sdk-reference/graph/add-fact-triple

## Request

### Body (application/json)

- `fact` (string, required) — The fact relating the two nodes that this edge represents
- `fact_name` (string, required) — The name of the edge to add. Should be all caps using snake case (eg RELATES_TO)
- `created_at` (string, optional) — The timestamp of the message
- `edge_attributes` (map from string to any, optional) — Additional attributes of the edge. Values must be scalar types (string, number, boolean, or null). Nested objects and arrays are not allowed.
- `expired_at` (string, optional) — The time (if any) at which the edge expires
- `graph_id` (string, optional)
- `invalid_at` (string, optional) — The time (if any) at which the fact stops being true
- `metadata` (map from string to any, optional) — Optional metadata key-value pairs for the shadow episode created for this fact triple. Max 10 keys. Values must be strings, numbers, or booleans.
- `source_node_attributes` (map from string to any, optional) — Additional attributes of the source node. Values must be scalar types (string, number, boolean, or null). Nested objects and arrays are not allowed.
- `source_node_labels` (list of string, optional) — The labels for the source node. At most one entity-type label may be provided so that manually-added triples remain consistent with automatic episode extraction, which assigns one best-match entity type per node. The base "Entity" label is added implicitly by the graph layer on save and does not need to be supplied here.
- `source_node_name` (string, optional) — The name of the source node to add
- `source_node_summary` (string, optional) — The summary of the source node to add
- `source_node_uuid` (string, optional) — The source node uuid
- `target_node_attributes` (map from string to any, optional) — Additional attributes of the target node. Values must be scalar types (string, number, boolean, or null). Nested objects and arrays are not allowed.
- `target_node_labels` (list of string, optional) — The labels for the target node. At most one entity-type label may be provided so that manually-added triples remain consistent with automatic episode extraction, which assigns one best-match entity type per node. The base "Entity" label is added implicitly by the graph layer on save and does not need to be supplied here.
- `target_node_name` (string, optional) — The name of the target node to add
- `target_node_summary` (string, optional) — The summary of the target node to add
- `target_node_uuid` (string, optional) — The target node uuid
- `user_id` (string, optional)
- `valid_at` (string, optional) — The time at which the fact becomes true

## Response

### 200

Resulting triple

- `edge` (object, optional)
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
- `source_node` (object, optional)
  - `created_at` (string, required) — Creation time of the node
  - `name` (string, required) — Name of the node
  - `summary` (string, required) — Regional summary of surrounding edges
  - `uuid` (string, required) — UUID of the node
  - `attributes` (map from string to any, optional) — Additional attributes of the node. Dependent on node labels
  - `labels` (list of string, optional) — Labels associated with the node
  - `relevance` (double, optional) — Relevance is an experimental rank-aligned score in [0,1] derived from Score via logit transformation. Only populated when using cross_encoder reranker; omitted for other reranker types (e.g., RRF).
  - `score` (double, optional) — Score is the reranker output: sigmoid-distributed logits [0,1] when using cross_encoder reranker, or RRF ordinal rank when using rrf reranker
  - `selection_rank` (integer, optional) — SelectionRank is the global cross-scope rank assigned by auto scope selection.
- `target_node` (object, optional)
  - `created_at` (string, required) — Creation time of the node
  - `name` (string, required) — Name of the node
  - `summary` (string, required) — Regional summary of surrounding edges
  - `uuid` (string, required) — UUID of the node
  - `attributes` (map from string to any, optional) — Additional attributes of the node. Dependent on node labels
  - `labels` (list of string, optional) — Labels associated with the node
  - `relevance` (double, optional) — Relevance is an experimental rank-aligned score in [0,1] derived from Score via logit transformation. Only populated when using cross_encoder reranker; omitted for other reranker types (e.g., RRF).
  - `score` (double, optional) — Score is the reranker output: sigmoid-distributed logits [0,1] when using cross_encoder reranker, or RRF ordinal rank when using rrf reranker
  - `selection_rank` (integer, optional) — SelectionRank is the global cross-scope rank assigned by auto scope selection.
- `task_id` (string, optional) — Task ID of the add triple task

## Examples

**Request**

```json
{
  "fact": "string",
  "fact_name": "string"
}
```

**Response**

```json
{
  "edge": {
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
  },
  "source_node": {
    "created_at": "string",
    "name": "string",
    "summary": "string",
    "uuid": "string",
    "attributes": {},
    "labels": [
      "string"
    ],
    "relevance": 1.1,
    "score": 1.1,
    "selection_rank": 1
  },
  "target_node": {
    "created_at": "string",
    "name": "string",
    "summary": "string",
    "uuid": "string",
    "attributes": {},
    "labels": [
      "string"
    ],
    "relevance": 1.1,
    "score": 1.1,
    "selection_rank": 1
  },
  "task_id": "string"
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.graph.addFactTriple({
        fact: "string",
        factName: "string",
    });
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.graph.add_fact_triple(
    fact="string",
    fact_name="string",
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
    request := &zep.AddTripleRequest{
        Fact: "string",
        FactName: "string",
    }
    client.Graph.AddFactTriple(
        context.TODO(),
        request,
    )
}

```