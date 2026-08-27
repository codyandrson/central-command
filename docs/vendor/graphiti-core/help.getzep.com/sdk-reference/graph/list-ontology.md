> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# List graph ontology

GET https://api.getzep.com/api/v2/graph/list-ontology

Retrieves the current entity and edge types configured for your graph.

See the [full documentation](/customizing-graph-structure) for details.


Reference: https://help.getzep.com/sdk-reference/graph/list-ontology

## Response

### 200

Current ontology

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
  - `identity_properties` (list of string, optional)
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
      "description": "Represents a friendship relationship between two users in the social graph.",
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
      "description": "A user entity representing a person in the social network.",
      "name": "User",
      "identity_properties": [
        "user_id"
      ],
      "properties": [
        {
          "description": "The unique identifier for the user.",
          "name": "user_id",
          "type": "Text"
        },
        {
          "description": "The user's full name.",
          "name": "full_name",
          "type": "Text"
        },
        {
          "description": "The user's age in years.",
          "name": "age",
          "type": "Int"
        },
        {
          "description": "Indicates if the user has a verified account.",
          "name": "is_verified",
          "type": "Boolean"
        }
      ]
    }
  ]
}
```

**SDK Code**

```python
from zep_cloud.client import Zep

client = Zep(
    api_key="YOUR_API_KEY",
)
ontology = client.graph.list_ontology()
print("Entity types:", ontology.entity_types)
print("Edge types:", ontology.edge_types)

```

```python
from zep_cloud import Zep

client = Zep()

client.graph.list_ontology()

```

```typescript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({ apiKey: "YOUR_API_KEY" });
const ontology = await client.graph.listOntology();
console.log("Entity types:", ontology.entityTypes);
console.log("Edge types:", ontology.edgeTypes);

```

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.graph.listOntology();
}
main();

```

```go
import (
	context "context"
	fmt "fmt"
	option "github.com/getzep/zep-go/v3/option"
	v3client "github.com/getzep/zep-go/v3/client"
)

client := v3client.NewClient(
	option.WithAPIKey(
		"<YOUR_APIKey>",
	),
)
ontology, err := client.Graph.ListOntology(context.TODO())
if err != nil {
    panic(err)
}
fmt.Printf("Entity types: %+v\n", ontology.EntityTypes)
fmt.Printf("Edge types: %+v\n", ontology.EdgeTypes)

```

```go
package example

import (
    context "context"

    client "github.com/getzep/zep-go/client"
)

func do() {
    client := client.NewClient()
    client.Graph.ListOntology(
        context.TODO(),
    )
}

```