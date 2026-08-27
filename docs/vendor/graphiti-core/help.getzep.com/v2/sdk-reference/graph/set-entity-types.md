> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Set Entity Types

PUT https://api.getzep.com/api/v2/entity-types
Content-Type: application/json

Sets the entity types for a project, replacing any existing ones.

Reference: https://help.getzep.com/v2/sdk-reference/graph/set-entity-types

## Request

### Body (application/json)

- `edge_types` (list of object, optional)
  - `description` (string, required)
  - `name` (string, required)
  - `properties` (list of object, optional)
    - `description` (string, required)
    - `name` (string, required)
    - `type` (enum, required)
      - Allowed values: `Text`, `Int`, `Float`, `Boolean`
  - `source_targets` (list of object, optional)
    - `source` (string, optional) — Source represents the originating node identifier in the edge type relationship. (optional)
    - `target` (string, optional) — Target represents the target node identifier in the edge type relationship. (optional)
- `entity_types` (list of object, optional)
  - `description` (string, required)
  - `name` (string, required)
  - `properties` (list of object, optional)
    - `description` (string, required)
    - `name` (string, required)
    - `type` (enum, required)
      - Allowed values: `Text`, `Int`, `Float`, `Boolean`

## Response

### 200

Entity types set successfully

- `message` (string, optional)

## Examples

**Request**

```json
{}
```

**Response**

```json
{
  "message": "Entity types have been successfully updated for project ID 12345."
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.graph.setEntityTypesInternal({});
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.graph.set_entity_types_internal()

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
    request := &zep.EntityTypeRequest{}
    client.Graph.SetEntityTypesInternal(
        context.TODO(),
        request,
    )
}

```