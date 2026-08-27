> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Detect Patterns (Experimental)

POST https://api.getzep.com/api/v2/graph/patterns
Content-Type: application/json

Detects structural patterns in a knowledge graph including relationship frequencies,
multi-hop paths, co-occurrences, hubs, and clusters.
When a query is provided, uses hybrid search to discover seed nodes,
detects triple-frequency patterns, and returns resolved edges ranked by relevance.

Reference: https://help.getzep.com/sdk-reference/graph/detect-patterns-experimental

## Request

### Body (application/json)

- `detect` (object, optional) — Which pattern types to detect with type-specific configuration. Omit to detect all types with defaults. Ignored when query is set.
  - `clusters` (object, optional) — Detect tightly interconnected groups (triangle topology)
  - `co_occurrences` (object, optional) — Detect node types that co-occur within k hops
    - `max_hops` (integer, optional) — Max hops within which to detect co-occurring node types. Default: 3, Max: 5
  - `hubs` (object, optional) — Detect highly connected hub nodes (star topology)
    - `min_degree` (integer, optional) — Minimum number of connections for a node to be considered a hub. Default: 3, Min: 2
  - `paths` (object, optional) — Detect frequent multi-hop connection paths
    - `max_hops` (integer, optional) — Max hops from seed nodes for path detection. Default: 3, Max: 5
  - `relationships` (object, optional) — Detect common (source_label, edge_type, target_label) relationship triples
- `edge_limit` (integer, optional) — Max resolved edges per pattern. Default: 10, Max: 100. Only used with query.
- `graph_id` (string, optional) — Graph ID when detecting patterns on a named graph
- `limit` (integer, optional) — Max patterns to return. Default: 50, Max: 200
- `min_occurrences` (integer, optional) — Minimum occurrence count to report a pattern. Default: 2
- `query` (string, optional) — Search query for discovering seed nodes via hybrid search. When set, forces triple-frequency detection only and enables edge resolution with cross-encoder reranking. Mutually exclusive with seeds.
- `query_limit` (integer, optional) — Max seed nodes from search. Default: 10, Max: 50. Only used with query.
- `recency_weight` (enum, optional) — Exponential half-life decay applied to edge created_at timestamps. Valid values: none, 7_days, 30_days, 90_days. Default: none
  - Allowed values: `none`, `7_days`, `30_days`, `90_days`
- `search_filters` (object, optional) — Filters which edges/nodes participate in pattern detection. Reuses the same filter format as /graph/search.
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
- `seeds` (object, optional) — Seed selection. If omitted, analyzes the entire graph. Mutually exclusive with query.
  - `edge_types` (list of string, optional) — All endpoints of these edge types become seeds
  - `node_labels` (list of string, optional) — All nodes with these labels become seeds
  - `node_uuids` (list of string, optional) — Specific node UUIDs to analyze around. Max 10000 to align with pattern detection seed limits.
- `user_id` (string, optional) — User ID when detecting patterns on a user graph

## Response

### 200

Detected patterns

- `metadata` (object, optional) — Statistics about the detection run
  - `edges_analyzed` (integer, optional) — Number of edges analyzed
  - `elapsed_ms` (integer, optional) — Elapsed time in milliseconds
  - `nodes_analyzed` (integer, optional) — Number of unique nodes analyzed
- `nodes` (list of object, optional) — Resolved nodes referenced by pattern edges (deduplicated). Only populated when query is set.
  - `created_at` (string, required) — Creation time of the node
  - `name` (string, required) — Name of the node
  - `summary` (string, required) — Regional summary of surrounding edges
  - `uuid` (string, required) — UUID of the node
  - `attributes` (map from string to any, optional) — Additional attributes of the node. Dependent on node labels
  - `labels` (list of string, optional) — Labels associated with the node
  - `relevance` (double, optional) — Relevance is an experimental rank-aligned score in [0,1] derived from Score via logit transformation. Only populated when using cross_encoder reranker; omitted for other reranker types (e.g., RRF).
  - `score` (double, optional) — Score is the reranker output: sigmoid-distributed logits [0,1] when using cross_encoder reranker, or RRF ordinal rank when using rrf reranker
  - `selection_rank` (integer, optional) — SelectionRank is the global cross-scope rank assigned by auto scope selection.
- `patterns` (list of object, optional) — Detected patterns, sorted by weighted_score descending
  - `description` (string, optional) — Human-readable structural description of the pattern (e.g. "Person -[KNOWS]-> Person"). Omitted in query mode in favor of Summary.
  - `edge_types` (list of string, optional) — Edge types in the pattern structure
  - `edges` (list of object, optional) — Resolved edges for this pattern, sorted by cross-encoder relevance. Only populated when query is set.
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
  - `node_labels` (list of string, optional) — Node labels in the pattern structure
  - `occurrences` (integer, optional) — Raw structural occurrence count (always unweighted). Reflects pattern frequency in the graph, not the number of resolved edges after filtering.
  - `summary` (string, optional) — Fact-derived summary from top reranked edges. Only populated when query is set. This is the primary display field for QA consumers.
  - `type` (string, optional) — Pattern type: relationship, path, co_occurrence, hub, cluster
  - `weighted_score` (double, optional) — Weighted structural support — equals occurrences when recency_weight is "none". Reflects graph-level support, not post-enrichment edge count.

## Examples

**Request**

```json
{}
```

**Response**

```json
{
  "metadata": {
    "edges_analyzed": 1,
    "elapsed_ms": 1,
    "nodes_analyzed": 1
  },
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
  "patterns": [
    {
      "description": "string",
      "edge_types": [
        "string"
      ],
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
      "node_labels": [
        "string"
      ],
      "occurrences": 1,
      "summary": "string",
      "type": "string",
      "weighted_score": 1.1
    }
  ]
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.graph.detectPatterns({});
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.graph.detect_patterns()

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
    request := &zep.DetectPatternsRequest{}
    client.Graph.DetectPatterns(
        context.TODO(),
        request,
    )
}

```