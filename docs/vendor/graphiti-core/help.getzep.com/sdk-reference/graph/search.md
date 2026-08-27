> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Search Graph

POST https://api.getzep.com/api/v2/graph/search
Content-Type: application/json

Perform a graph search query.

Reference: https://help.getzep.com/sdk-reference/graph/search

## Request

### Body (application/json)

- `query` (string, required) — The string to search for (required)
- `bfs_origin_node_uuids` (list of string, optional) — Nodes that are the origins of the BFS searches
- `center_node_uuid` (string, optional) — Node to rerank around for node distance reranking
- `graph_id` (string, optional) — The graph_id to search in. When searching user graph, please use user_id instead.
- `limit` (integer, optional) — The maximum number of facts to retrieve for non-auto scopes. Defaults to 10. Limited to 50. Ignored when scope=auto.
- `max_characters` (integer, optional) — Maximum total characters across all selected results when scope=auto. Defaults to 2500. Limited to 50000.
- `mmr_lambda` (double, optional) — weighting for maximal marginal relevance
- `reranker` (enum, optional) — Defaults to RRF. Ignored when scope=auto except node_distance and episode_mentions are rejected; auto search always uses RRF retrieval and applies its own internal rerank after retrieval. episode_mentions ranks edge candidates by how many of the episodes listed in search_filters.episode_uuids mention them; without episode_uuids it has no effect and results are ranked as if no reranker were specified.
  - Allowed values: `rrf`, `mmr`, `node_distance`, `episode_mentions`, `cross_encoder`
- `return_raw_results` (boolean, optional) — When scope=auto, include the selected raw graph results alongside the materialized context block. For graph-service-backed auto mode, selected raw results may include episodes, edges, nodes, observations, and thread_summaries.
- `scope` (enum, optional) — Defaults to Edges.
  - Allowed values: `edges`, `nodes`, `episodes`, `thread_summaries`, `observations`, `auto`
- `search_filters` (object, optional) — Search filters to apply to the search
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
- `user_id` (string, optional) — The user_id when searching user graph. If not searching user graph, please use graph_id instead.

## Response

### 200

Graph search results or auto-context block

- `context` (string, optional)
- `edges` (list of object, optional)
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
- `episodes` (list of object, optional)
  - `content` (string, required)
  - `created_at` (string, required)
  - `uuid` (string, required)
  - `document_id` (string, optional) — Optional document ID, will be present if the episode is part of a document
  - `metadata` (map from string to any, optional)
  - `processed` (boolean, optional)
  - `relevance` (double, optional) — Relevance is an experimental rank-aligned score in [0,1] derived from Score via logit transformation. Only populated when using cross_encoder reranker; omitted for other reranker types (e.g., RRF).
  - `role` (string, optional) — Optional role, will only be present if the episode was created using memory.add API
  - `role_type` (enum, optional) — Optional role_type, will only be present if the episode was created using memory.add API
    - Allowed values: `norole`, `system`, `assistant`, `user`, `function`, `tool`
  - `score` (double, optional) — Score is the reranker output: sigmoid-distributed logits [0,1] when using cross_encoder reranker, or RRF ordinal rank when using rrf reranker
  - `selection_rank` (integer, optional) — SelectionRank is the global cross-scope rank assigned by auto scope selection.
  - `source` (enum, optional)
    - Allowed values: `text`, `json`, `message`, `fact_triple`
  - `source_description` (string, optional)
  - `task_id` (string, optional) — Optional task ID to poll episode processing status. Currently only available for batch ingestion.
  - `thread_id` (string, optional) — Optional thread ID, will be present if the episode is part of a thread
- `nodes` (list of object, optional)
  - `created_at` (string, required) — Creation time of the node
  - `name` (string, required) — Name of the node
  - `summary` (string, required) — Regional summary of surrounding edges
  - `uuid` (string, required) — UUID of the node
  - `attributes` (map from string to any, optional) — Additional attributes of the node. Dependent on node labels
  - `labels` (list of string, optional) — Labels associated with the node
  - `relevance` (double, optional) — Relevance is an experimental rank-aligned score in [0,1] derived from Score via logit transformation. Only populated when using cross_encoder reranker; omitted for other reranker types (e.g., RRF).
  - `score` (double, optional) — Score is the reranker output: sigmoid-distributed logits [0,1] when using cross_encoder reranker, or RRF ordinal rank when using rrf reranker
  - `selection_rank` (integer, optional) — SelectionRank is the global cross-scope rank assigned by auto scope selection.
- `observations` (list of object, optional)
  - `created_at` (string, required) — Creation time of the node
  - `name` (string, required) — Name of the node
  - `uuid` (string, required) — UUID of the node
  - `attributes` (map from string to any, optional) — Additional attributes of the derived node.
  - `end_at` (string, optional) — EndAt is the close timestamp of the evidence window. Set when the underlying pattern is no longer supported (closed observations); nil for active observations.
  - `episode_ids` (list of string, optional) — Episode UUIDs that support this observation. Only populated for observation nodes in web API responses.
  - `labels` (list of string, optional) — Labels associated with the node
  - `latest_evidence_at` (string, optional) — LatestEvidenceAt is the most recent source-episode timestamp from which this observation drew evidence.
  - `relevance` (double, optional) — Relevance is an experimental rank-aligned score in [0,1] derived from Score via logit transformation. Only populated when using cross_encoder reranker; omitted for other reranker types (e.g., RRF).
  - `score` (double, optional) — Score is the reranker output: sigmoid-distributed logits [0,1] when using cross_encoder reranker, or RRF ordinal rank when using rrf reranker
  - `selection_rank` (integer, optional) — SelectionRank is the global cross-scope rank assigned by auto scope selection.
  - `start_at` (string, optional) — StartAt is the earliest source-episode timestamp from which this observation was derived. Only populated for observation nodes.
  - `summary` (string, optional) — Region summary of member nodes
- `response` (object, optional)
  - `server_latency_ms` (integer, optional) — Server-side processing latency in milliseconds.
- `thread_summaries` (list of object, optional)
  - `created_at` (string, required) — Creation time of the node
  - `name` (string, required) — Name of the node
  - `uuid` (string, required) — UUID of the node
  - `labels` (list of string, optional) — Labels associated with the node
  - `last_summarized_at` (string, optional) — Wall-clock timestamp of the most recent summary update. Used internally as the watermark for filtering new episodes by ingestion time.
  - `last_summarized_episode_valid_at` (string, optional) — Maximum episode reference time (valid_at) covered by the most recent summary. Use this field — not LastSummarizedAt — when answering "how recent is this summary's content in event-time?".
  - `relevance` (double, optional) — Relevance is an experimental rank-aligned score in [0,1] derived from Score via logit transformation. Only populated when using cross_encoder reranker; omitted for other reranker types (e.g., RRF).
  - `score` (double, optional) — Score is the reranker output: sigmoid-distributed logits [0,1] when using cross_encoder reranker, or RRF ordinal rank when using rrf reranker
  - `selection_rank` (integer, optional) — SelectionRank is the global cross-scope rank assigned by auto scope selection.
  - `summary` (string, optional) — Incremental summary of the thread.

## Examples

**Request**

```json
{
  "query": "string"
}
```

**Response**

```json
{
  "context": "string",
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
  "episodes": [
    {
      "content": "string",
      "created_at": "string",
      "uuid": "string",
      "document_id": "string",
      "metadata": {},
      "processed": true,
      "relevance": 1.1,
      "role": "string",
      "role_type": "norole",
      "score": 1.1,
      "selection_rank": 1,
      "source": "text",
      "source_description": "string",
      "task_id": "string",
      "thread_id": "string"
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
  "observations": [
    {
      "created_at": "string",
      "name": "string",
      "uuid": "string",
      "attributes": {},
      "end_at": "string",
      "episode_ids": [
        "string"
      ],
      "labels": [
        "string"
      ],
      "latest_evidence_at": "string",
      "relevance": 1.1,
      "score": 1.1,
      "selection_rank": 1,
      "start_at": "string",
      "summary": "string"
    }
  ],
  "response": {
    "server_latency_ms": 1
  },
  "thread_summaries": [
    {
      "created_at": "string",
      "name": "string",
      "uuid": "string",
      "labels": [
        "string"
      ],
      "last_summarized_at": "string",
      "last_summarized_episode_valid_at": "string",
      "relevance": 1.1,
      "score": 1.1,
      "selection_rank": 1,
      "summary": "string"
    }
  ]
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.graph.search({
        query: "string",
    });
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.graph.search(
    query="string",
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
    request := &zep.GraphSearchQuery{
        Query: "string",
    }
    client.Graph.Search(
        context.TODO(),
        request,
    )
}

```