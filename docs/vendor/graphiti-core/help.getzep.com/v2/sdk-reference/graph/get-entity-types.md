> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Get Entity Types

GET https://api.getzep.com/api/v2/entity-types

Returns all entity types for a project.

Reference: https://help.getzep.com/v2/sdk-reference/graph/get-entity-types

## Response

### 200

The list of entity types.

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

## Examples

**Request**

```json
{}
```

**Response**

```json
{
  "edge_types": [
    {
      "description": "Represents a friendship relationship between two user entities.",
      "name": "Friendship",
      "properties": [
        {
          "description": "The date when the friendship was established.",
          "name": "since",
          "type": "Text"
        }
      ],
      "source_targets": [
        {
          "source": "User",
          "target": "User"
        }
      ]
    }
  ],
  "entity_types": [
    {
      "description": "A user of the application with profile and account details.",
      "name": "User",
      "properties": [
        {
          "description": "The unique username chosen by the user.",
          "name": "username",
          "type": "Text"
        },
        {
          "description": "The age of the user in years.",
          "name": "age",
          "type": "Int"
        },
        {
          "description": "Indicates if the user has verified their email address.",
          "name": "emailVerified",
          "type": "Boolean"
        }
      ]
    }
  ]
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.graph.listEntityTypes();
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.graph.list_entity_types()

```

```go
package example

import (
    context "context"

    client "github.com/getzep/zep-go/client"
)

func do() {
    client := client.NewClient()
    client.Graph.ListEntityTypes(
        context.TODO(),
    )
}

```