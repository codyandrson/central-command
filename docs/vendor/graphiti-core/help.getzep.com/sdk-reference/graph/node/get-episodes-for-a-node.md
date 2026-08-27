> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Get Episodes for a node

GET https://api.getzep.com/api/v2/graph/node/{node_uuid}/episodes

Deprecated. Use episode listing with `mentioned_node_uuids` (`POST /graph/episodes/graph/{graph_id}` or `POST /graph/episodes/user/{user_id}`) instead. Returns episodes that mentioned a given node, subject to an internal cap; responses reduced by that cap set the Zep-Truncated header.

Reference: https://help.getzep.com/sdk-reference/graph/node/get-episodes-for-a-node

## Request

### Path parameters

- `node_uuid` (string, required) — Node UUID

## Response

### 200

Episodes

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

## Examples

**Response**

```json
{
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
  ]
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.graph.node.getEpisodes("node_uuid");
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.graph.node.get_episodes(
    node_uuid="node_uuid",
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
    client.Graph.Node.GetEpisodes(
        context.TODO(),
        "node_uuid",
    )
}

```