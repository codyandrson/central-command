> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Get User Node

GET https://api.getzep.com/api/v2/users/{userId}/node

Returns a user's node.

Reference: https://help.getzep.com/sdk-reference/user/get-user-node

## Request

### Path parameters

- `userId` (string, required) — The user_id of the user to get the node for.

## Response

### 200

Response object containing the User node.

- `node` (object, optional)
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
  "node": {
    "created_at": "2023-11-05T14:22:30Z",
    "name": "Jane Doe",
    "summary": "Active admin user with recent login and full permissions.",
    "uuid": "a3f1c9e2-7b4d-4f8a-9c2e-5d6f7a8b9c0d",
    "attributes": {
      "email": "jane.doe@example.com",
      "role": "admin",
      "last_login": "2024-06-10T09:15:00Z"
    },
    "labels": [
      "User",
      "Admin"
    ],
    "relevance": 0.87,
    "score": 0.92,
    "selection_rank": 3
  }
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.user.getNode("userId");
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.user.get_node(
    user_id="userId",
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
    client.User.GetNode(
        context.TODO(),
        "userId",
    )
}

```