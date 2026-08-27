> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Get Observation

GET https://api.getzep.com/api/v2/graph/observation/{uuid}

Returns a specific observation node by UUID. Observation nodes are read-only.

Reference: https://help.getzep.com/sdk-reference/graph/observations/get-observation

## Request

### Path parameters

- `uuid` (string, required) — Observation UUID

## Response

### 200

Observation

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

## Examples

**Request**

```json
{}
```

**Response**

```json
{
  "created_at": "2024-06-10T09:15:00Z",
  "name": "Greenhouse Temperature Observation",
  "uuid": "d290f1ee-6c54-4b01-90e6-d701748f0851",
  "attributes": {
    "temperature": "22.5°C",
    "humidity": "45%",
    "location": "Greenhouse 3"
  },
  "end_at": null,
  "episode_ids": [
    "a3f1c9d2-4b7e-4f8a-9c2d-1e2f3a4b5c6d",
    "b7d2e3f4-5a6b-7c8d-9e0f-1a2b3c4d5e6f"
  ],
  "labels": [
    "temperature",
    "humidity",
    "environment"
  ],
  "latest_evidence_at": "2024-06-10T09:00:00Z",
  "relevance": 0.87,
  "score": 0.92,
  "selection_rank": 3,
  "start_at": "2024-06-10T08:00:00Z",
  "summary": "Temperature and humidity readings from Greenhouse 3 between 8 AM and 9 AM."
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.graph.observation.get("uuid");
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.graph.observation.get(
    uuid_="uuid",
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
    client.Graph.Observation.Get(
        context.TODO(),
        "uuid",
    )
}

```