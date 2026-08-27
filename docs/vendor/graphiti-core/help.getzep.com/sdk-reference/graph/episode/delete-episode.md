> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Delete Episode

DELETE https://api.getzep.com/api/v2/graph/episodes/{uuid}

Deletes an episode by its UUID.

Reference: https://help.getzep.com/sdk-reference/graph/episode/delete-episode

## Request

### Path parameters

- `uuid` (string, required) — Episode UUID

## Response

### 200

Episode deleted

- `message` (string, optional)

## Examples

**Request**

```json
{}
```

**Response**

```json
{
  "message": "Episode with UUID 3fa85f64-5717-4562-b3fc-2c963f66afa6 successfully deleted."
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.graph.episode.delete("uuid");
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.graph.episode.delete(
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
    client.Graph.Episode.Delete(
        context.TODO(),
        "uuid",
    )
}

```