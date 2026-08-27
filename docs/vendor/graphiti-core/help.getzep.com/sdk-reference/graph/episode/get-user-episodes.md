> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Get User Episodes

GET https://api.getzep.com/api/v2/graph/episodes/user/{user_id}

Returns episodes by user id.

Reference: https://help.getzep.com/sdk-reference/graph/episode/get-user-episodes

## Request

### Path parameters

- `user_id` (string, required) — User ID

### Query parameters

- `lastn` (integer, optional) — The number of most recent episodes entries to retrieve.

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
    await client.graph.episode.getByUserId("user_id", {});
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.graph.episode.get_by_user_id(
    user_id="user_id",
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
    request := &graph.EpisodeGetByUserIDRequest{}
    client.Graph.Episode.GetByUserID(
        context.TODO(),
        "user_id",
        request,
    )
}

```