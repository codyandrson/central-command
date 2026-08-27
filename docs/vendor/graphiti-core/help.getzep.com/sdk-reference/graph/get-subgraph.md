> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Get Subgraph

POST https://api.getzep.com/api/v2/graph/subgraph
Content-Type: application/json

Returns the bounded neighborhood of a set of seed nodes as a single \{nodes, edges} payload: breadth-first expansion up to a caller-specified depth, subject to explicit budgets, with explicit truncation reporting.

Reference: https://help.getzep.com/sdk-reference/graph/get-subgraph

## Request

### Body (application/json)

- `seed_node_uuids` (list of string, required) — Seed node UUIDs to expand from, in traversal-priority order: seeds are admitted before any expansion, in this order, and count toward max_nodes first. 1-20 entries, required. Seeds that do not exist in the target graph are ignored, not an error.
- `depth` (integer, optional) — Maximum traversal depth from the seeds. 1-3. Defaults to 1.
- `direction` (string, optional) — Edge orientation followed during expansion, relative to each frontier node: "in" | "out" | "both". Defaults to "both".
- `graph_id` (string, optional) — graph_id identifies the target named graph. Exactly one of user_id or graph_id is required.
- `max_edges` (integer, optional) — Maximum number of edges in the response. 1-1000. Defaults to 200.
- `max_nodes` (integer, optional) — Maximum number of nodes in the response, including admitted seeds. 1-500. Defaults to 100.
- `search_filters` (object, optional) — Filters constraining traversed edges and included nodes. Reuses the graph.search filter type. search_filters.episode_metadata_filters is rejected: it cannot be enforced during graph traversal.
  - `connected_node_uuids` (list of string, optional) — List of node UUIDs to filter edges on: an edge matches if its source OR target node UUID is in this list. Applies to edges only; rejected on requests whose result type contains no edges. Max 256 entries.
  - `created_at` (list of list of object, optional) — 2D array of date filters for the created\_at field. The outer array elements are combined with OR logic. The inner array elements are combined with AND logic. Example: `[[{">", date1}, {"<", date2}], [{"=", date3}]]` This translates to: `(created_at > date1 AND created_at < date2) OR (created_at = date3)`
    - `comparison_operator` (enum, required) — Comparison operator for date filter
      - Allowed values: `=`, `<>`, `>`, `<`, `>=`, `<=`, `IS NULL`, `is_null`, `IS NOT NULL`, `CONTAINS`
    - `date` (string, optional) — Date to filter on. Required for non-null operators (`=`, `<>`, `>`, `<`, `>=`, `<=`). Should be omitted for IS NULL (or is\_null) and IS NOT NULL operators.
  - `edge_types` (list of string, optional) — List of edge types to filter on
  - `edge_uuids` (list of string, optional) — List of edge UUIDs to filter on. Max 256 to align with graph-service filter limits.
  - `episode_metadata_filters` (object, optional) — [Experimental] Episode metadata filter. Restricts results to edges/nodes derived from episodes matching the metadata predicates. Uses explicit AND/OR groups. This feature is experimental and may change in future releases.
    - `type` (enum, required) — Logical operator: "and" or "or"
      - Allowed values: `and`, `or`
    - `filters` (list of object, optional) — Leaf filters (predicates on metadata key-value pairs)
      - `comparison_operator` (enum, required) — Comparison operator: =, \<>, >, \<, >=, \<=, IS NULL, IS NOT NULL, IN, CONTAINS
        - Allowed values: `=`, `<>`, `>`, `<`, `>=`, `<=`, `IS NULL`, `is_null`, `IS NOT NULL`, `CONTAINS`
      - `property_name` (string, required) — Metadata key to filter on
      - `property_value` (any, optional) — Value to compare against. Not required for IS NULL / IS NOT NULL operators.
    - `groups` (list of object, optional) — Nested sub-groups for composing complex boolean expressions
  - `episode_uuids` (list of string, optional) — List of episode UUIDs to filter on. An edge matches if it was derived from any listed episode; a node matches if it is mentioned by any listed episode. Valid for both edge and node result types. Max 256 entries.
  - `exclude_edge_types` (list of string, optional) — List of edge types to exclude from results
  - `exclude_node_labels` (list of string, optional) — List of node labels to exclude from results
  - `expired_at` (list of list of object, optional) — 2D array of date filters for the expired\_at field. The outer array elements are combined with OR logic. The inner array elements are combined with AND logic. Example: `[[{">", date1}, {"<", date2}], [{"=", date3}]]` This translates to: `(expired_at > date1 AND expired_at < date2) OR (expired_at = date3)`
    - `comparison_operator` (enum, required) — Comparison operator for date filter
      - Allowed values: `=`, `<>`, `>`, `<`, `>=`, `<=`, `IS NULL`, `is_null`, `IS NOT NULL`, `CONTAINS`
    - `date` (string, optional) — Date to filter on. Required for non-null operators (`=`, `<>`, `>`, `<`, `>=`, `<=`). Should be omitted for IS NULL (or is\_null) and IS NOT NULL operators.
  - `invalid_at` (list of list of object, optional) — 2D array of date filters for the invalid\_at field. The outer array elements are combined with OR logic. The inner array elements are combined with AND logic. Example: `[[{">", date1}, {"<", date2}], [{"=", date3}]]` This translates to: `(invalid_at > date1 AND invalid_at < date2) OR (invalid_at = date3)`
    - `comparison_operator` (enum, required) — Comparison operator for date filter
      - Allowed values: `=`, `<>`, `>`, `<`, `>=`, `<=`, `IS NULL`, `is_null`, `IS NOT NULL`, `CONTAINS`
    - `date` (string, optional) — Date to filter on. Required for non-null operators (`=`, `<>`, `>`, `<`, `>=`, `<=`). Should be omitted for IS NULL (or is\_null) and IS NOT NULL operators.
  - `node_labels` (list of string, optional) — List of node labels to filter on
  - `property_filters` (list of object, optional) — List of property filters to apply to nodes and edges
    - `comparison_operator` (enum, required) — Comparison operator for property filter
      - Allowed values: `=`, `<>`, `>`, `<`, `>=`, `<=`, `IS NULL`, `is_null`, `IS NOT NULL`, `CONTAINS`
    - `property_name` (string, required) — Property name to filter on
    - `property_value` (any, optional) — Property value to match on. Accepted types: string, int, float64, bool, or nil. Invalid types (e.g., arrays, objects) will be rejected by validation. Must be non-nil for non-null operators (`=`, `<>`, `>`, `<`, `>=`, `<=`).
  - `source_node_uuids` (list of string, optional) — List of node UUIDs to filter edges on: an edge matches if its source node UUID is in this list. Applies to edges only; rejected on requests whose result type contains no edges. Max 256 entries.
  - `target_node_uuids` (list of string, optional) — List of node UUIDs to filter edges on: an edge matches if its target node UUID is in this list. Applies to edges only; rejected on requests whose result type contains no edges. Max 256 entries.
  - `valid_at` (list of list of object, optional) — 2D array of date filters for the valid\_at field. The outer array elements are combined with OR logic. The inner array elements are combined with AND logic. Example: `[[{">", date1}, {"<", date2}], [{"=", date3}]]` This translates to: `(valid_at > date1 AND valid_at < date2) OR (valid_at = date3)`
    - `comparison_operator` (enum, required) — Comparison operator for date filter
      - Allowed values: `=`, `<>`, `>`, `<`, `>=`, `<=`, `IS NULL`, `is_null`, `IS NOT NULL`, `CONTAINS`
    - `date` (string, optional) — Date to filter on. Required for non-null operators (`=`, `<>`, `>`, `<`, `>=`, `<=`). Should be omitted for IS NULL (or is\_null) and IS NOT NULL operators.
- `user_id` (string, optional) — user_id identifies the target user graph. Exactly one of user_id or graph_id is required.

## Response

### 200

Subgraph

- `edges` (list of object, optional) — Every traversed edge that passed the request filters. Both endpoints of every edge are present in Nodes (edge-endpoint closure).
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
- `nodes` (list of object, optional) — Every admitted seed and every node reached within budget.
  - `created_at` (string, required) — Creation time of the node
  - `name` (string, required) — Name of the node
  - `summary` (string, required) — Regional summary of surrounding edges
  - `uuid` (string, required) — UUID of the node
  - `attributes` (map from string to any, optional) — Additional attributes of the node. Dependent on node labels
  - `labels` (list of string, optional) — Labels associated with the node
  - `relevance` (double, optional) — Relevance is an experimental rank-aligned score in [0,1] derived from Score via logit transformation. Only populated when using cross_encoder reranker; omitted for other reranker types (e.g., RRF).
  - `score` (double, optional) — Score is the reranker output: sigmoid-distributed logits [0,1] when using cross_encoder reranker, or RRF ordinal rank when using rrf reranker
  - `selection_rank` (integer, optional) — SelectionRank is the global cross-scope rank assigned by auto scope selection.
- `truncated` (boolean, optional) — True whenever any budget or internal limit reduced the result.
- `truncation_reason` (string, optional) — Names the binding limit (for example "max_nodes", "max_edges") when Truncated is true; nil otherwise.

## Examples

**Request**

```json
{
  "seed_node_uuids": [
    "string"
  ]
}
```

**Response**

```json
{
  "edges": [
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
  ],
  "nodes": [
    {
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
    }
  ],
  "truncated": true,
  "truncation_reason": "string"
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.graph.getSubgraph({
        seedNodeUuids: [
            "string",
        ],
    });
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.graph.get_subgraph(
    seed_node_uuids=[
        "string"
    ],
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
    request := &zep.GraphSubgraphRequest{
        SeedNodeUUIDs: []string{
            "string",
        },
    }
    client.Graph.GetSubgraph(
        context.TODO(),
        request,
    )
}

```