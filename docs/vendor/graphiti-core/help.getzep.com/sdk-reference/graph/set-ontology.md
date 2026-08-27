> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Set graph ontology

PUT https://api.getzep.com/api/v2/graph/set-ontology
Content-Type: application/json

Sets custom entity and edge types for your graph. This wrapper method
provides a clean interface for defining your graph schema with custom
entity and edge types.

See the [full documentation](/customizing-graph-structure#setting-entity-and-edge-types) for details.


Reference: https://help.getzep.com/sdk-reference/graph/set-ontology

## Request

### Body (application/json)

- `entities` (object, optional) — Dictionary mapping entity type names to their definitions
- `edges` (object, optional) — Dictionary mapping edge type names to their definitions with source/target constraints
- `user_ids` (list of string, optional) — Optional list of user IDs to apply ontology to
- `graph_ids` (list of string, optional) — Optional list of graph IDs to apply ontology to

## Response

### 200

Ontology set successfully

- `message` (string, optional)

## Examples

**Request**

```json
{}
```

**Response**

```json
{
  "message": "Ontology set successfully"
}
```

**SDK Code**

```python
from zep_cloud.client import Zep
from zep_cloud.external_clients.ontology import EntityModel, EntityText, EdgeModel
from zep_cloud import EntityEdgeSourceTarget
from pydantic import Field

class Restaurant(EntityModel):
    cuisine_type: EntityText = Field(description="The cuisine type", default=None)

class RestaurantVisit(EdgeModel):
    restaurant_name: EntityText = Field(description="Restaurant name", default=None)

client = Zep(
    api_key="YOUR_API_KEY",
)
client.graph.set_ontology(
    entities={
        "Restaurant": Restaurant,
    },
    edges={
        "RESTAURANT_VISIT": (
            RestaurantVisit,
            [EntityEdgeSourceTarget(source="User", target="Restaurant")]
        ),
    }
)

```

```python
from zep_cloud import Zep

client = Zep()

client.graph.set_ontology()

```

```typescript
import { ZepClient, entityFields, EntityType, EdgeType } from "@getzep/zep-cloud";

const RestaurantSchema: EntityType = {
    description: "Represents a restaurant.",
    fields: {
        cuisine_type: entityFields.text("The cuisine type"),
    },
};

const RestaurantVisit: EdgeType = {
    description: "User visited a restaurant.",
    fields: {
        restaurant_name: entityFields.text("Restaurant name"),
    },
    sourceTargets: [
        { source: "User", target: "Restaurant" },
    ],
};

const client = new ZepClient({ apiKey: "YOUR_API_KEY" });
await client.graph.setOntology(
    {
        Restaurant: RestaurantSchema,
    },
    {
        RESTAURANT_VISIT: RestaurantVisit,
    }
);

```

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.graph.setOntology({});
}
main();

```

```go
import (
	context "context"
	option "github.com/getzep/zep-go/v3/option"
	v3 "github.com/getzep/zep-go/v3"
	v3client "github.com/getzep/zep-go/v3/client"
)

type Restaurant struct {
    v3.BaseEntity `name:"Restaurant" description:"Represents a restaurant."`
    CuisineType string `description:"The cuisine type" json:"cuisine_type,omitempty"`
}

type RestaurantVisit struct {
    v3.BaseEdge `name:"RESTAURANT_VISIT" description:"User visited a restaurant."`
    RestaurantName string `description:"Restaurant name" json:"restaurant_name,omitempty"`
}

client := v3client.NewClient(
	option.WithAPIKey(
		"<YOUR_APIKey>",
	),
)
_, err := client.Graph.SetOntology(
    context.TODO(),
    []v3.EntityDefinition{
        Restaurant{},
    },
    []v3.EdgeDefinitionWithSourceTargets{
        {
            EdgeModel: RestaurantVisit{},
            SourceTargets: []v3.EntityEdgeSourceTarget{
                {Source: v3.String("User"), Target: v3.String("Restaurant")},
            },
        },
    },
)
if err != nil {
    panic(err)
}

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
    request := &zep.GraphSetOntologyRequest{}
    client.Graph.SetOntology(
        context.TODO(),
        request,
    )
}

```