> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Add Data

POST https://api.getzep.com/api/v2/graph
Content-Type: application/json

Add data to the graph.

Reference: https://help.getzep.com/sdk-reference/graph/add-data

## Request

### Body (application/json)

- `data` (string, required)
- `type` (enum, required)
  - Allowed values: `text`, `json`, `message`, `fact_triple`
- `created_at` (string, optional)
- `document_id` (string, optional) — Optional document ID that groups episodes as chunks of the same document on a graph. Parallel to thread_id for message threads.
- `graph_id` (string, optional) — graph_id is the ID of the graph to which the data will be added. If adding to the user graph, please use user_id field instead.
- `metadata` (map from string to any, optional) — Optional metadata key-value pairs. Max 10 keys. Values must be strings, numbers, booleans, or arrays of scalars.
- `source_description` (string, optional)
- `strict_ontology` (boolean, optional) — When true, prevents extraction of generic Entity nodes that do not match the configured ontology.
- `user_id` (string, optional) — User ID is the ID of the user to which the data will be added. If not adding to a user graph, please use graph_id field instead.

## Response

### 202

Added episode

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

## Examples

**Request**

```json
{
  "data": "string",
  "type": "text"
}
```

**Response**

```json
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
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.graph.add({
        data: "string",
        type: "text",
    });
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.graph.add(
    data="string",
    type="text",
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
    request := &zep.AddDataRequest{
        Data: "string",
        Type: zep.GraphDataTypeText,
    }
    client.Graph.Add(
        context.TODO(),
        request,
    )
}

```