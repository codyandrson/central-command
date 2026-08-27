> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Get User Thread Summaries

POST https://api.getzep.com/api/v2/graph/thread-summary/user/{user_id}
Content-Type: application/json

Returns incremental thread summaries generated from messages in each thread associated with the user's graph.

Reference: https://help.getzep.com/sdk-reference/graph/thread-summaries/get-user-thread-summaries

## Request

### Path parameters

- `user_id` (string, required) — User ID

### Body (application/json)

- `cursor` (string, optional) — Opaque cursor for pagination, obtained from the Zep-Next-Cursor response header of the previous page. Encodes the sort field, direction, and continuation position.
- `direction` (string, optional) — Sort direction. One of "asc" or "desc" (default "desc").
- `filters` (object, optional) — Optional filters applied to the listed artifacts. Reuses the graph.search filter type.
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
- `limit` (integer, optional) — Maximum number of items to return
- `order_by` (string, optional) — Field to sort by. One of "created_at", "valid_at", or "uuid" (default "uuid").
- `uuid_cursor` (string, optional) — UUID based cursor, used for pagination. Should be the UUID of the last item in the previous page. Deprecated: prefer Cursor, the opaque cursor returned via the Zep-Next-Cursor response header.

## Response

### 200

Thread summaries

- `list of object`
  - `created_at` (string, optional) — CreatedAt is when the summary node was first created.
  - `last_summarized_at` (string, optional) — LastSummarizedAt is the wall-clock timestamp of the most recent summary update. This is an ingestion-time watermark; for the event-time recency of the summary's content, use LastSummarizedEpisodeValidAt instead.
  - `last_summarized_episode_valid_at` (string, optional) — LastSummarizedEpisodeValidAt is the maximum episode reference time (valid_at) covered by the most recent summary. Use this when answering "how recent is this summary's content in event-time?".
  - `summary` (string, optional) — Summary is the incremental summary content.
  - `thread_id` (string, optional) — ThreadID is the ID of the thread this summary belongs to. When a thread was created without an explicit thread_id, this field falls back to the thread's UUID. Clients should treat it as an opaque identifier.
  - `uuid` (string, optional) — UUID of the derived thread summary node.

## Examples

**Request**

```json
{}
```

**Response**

```json
[
  {
    "created_at": "string",
    "last_summarized_at": "string",
    "last_summarized_episode_valid_at": "string",
    "summary": "string",
    "thread_id": "string",
    "uuid": "string"
  }
]
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.graph.threadSummary.getByUserId("user_id", {});
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.graph.thread_summary.get_by_user_id(
    user_id="user_id",
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
    request := &zep.GraphThreadSummariesRequest{}
    client.Graph.ThreadSummary.GetByUserID(
        context.TODO(),
        "user_id",
        request,
    )
}

```