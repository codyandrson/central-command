> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Facts

> Facts are precise, time-stamped relationships on a Context Graph. Bi-temporal valid_at/invalid_at timestamps let Zep track change and answer point-in-time queries.

## Overview

Facts are precise and time-stamped information stored on edges that capture detailed relationships about specific events. They include `valid_at` and `invalid_at` timestamps, ensuring temporal accuracy and preserving a clear history of changes over time.

## How Zep updates facts

When incorporating new data, Zep looks for existing nodes and edges in the graph and decides whether to add new nodes/edges or to update existing ones. An update could mean updating an edge (for example, indicating the previous fact is no longer valid).

Here's an example of how Zep might extract graph data from a chat message, and then update the graph once new information is available:

<img src="https://fdr-prod-docs-files-public.s3.us-east-1.amazonaws.com/zep.docs.buildwithfern.com/204428cea99ae5c2c2055235e6cab2c0b5bc5aad3d7fdfbb1e3792cf4cd9e8fb/images/graphiti-graph-intro.gif?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=AKIA6KXJSKKNFOCF7G4B%2F20260825%2Fus-east-1%2Fs3%2Faws4_request&X-Amz-Date=20260825T142111Z&X-Amz-Expires=604800&X-Amz-Signature=a109e76a6804c65757491cee9270a2d96960a355b143934c0b95aa17b8b882be&X-Amz-SignedHeaders=host&x-amz-checksum-mode=ENABLED&x-id=GetObject" alt="graphiti intro slides" />

As shown in the example above, when Kendra initially loves Adidas shoes but later is angry that the shoes broke and states a preference for Puma shoes, Zep attempts to invalidate the fact that Kendra loves Adidas shoes and creates two new facts: "Kendra's Adidas shoes broke" and "Kendra likes Puma shoes".

Zep also looks for dates in all ingested data, such as the timestamp on a chat message or an article's publication date, informing how Zep sets the edge attributes. This assists your agent in reasoning with time.

## The four fact timestamps

Each fact stored on an edge includes four different timestamp attributes that track the lifecycle of that information:

| Edge attribute  | Example                                         |
| :-------------- | :---------------------------------------------- |
| **created\_at** | The time Zep learned that the user got married  |
| **valid\_at**   | The time the user got married                   |
| **invalid\_at** | The time the user got divorced                  |
| **expired\_at** | The time Zep learned that the user got divorced |

The `valid_at` and `invalid_at` attributes for each fact are then included in Zep's Context Block which is given to your agent:

```text
# format: FACT (Date range: from - to)
User account Emily0e62 has a suspended status due to payment failure. (2024-11-14 02:03:58+00:00 - present)
```

## Edge names

In addition to the fact body and timestamps, each edge has a **name** that identifies the relationship type. Edge names are SCREAMING\_SNAKE\_CASE labels like `WORKS_AT`, `LIVES_IN`, `OWNS`, or `PURCHASED`. Zep generates a name for each edge it extracts from your data.

If you use Zep's [custom ontology feature](/customizing-graph-structure) to define custom edge types, the custom edge type is expressed through the edge name: when Zep classifies an edge as one of your custom types, the edge's `name` is set to the type key you registered. For example, if you register an edge type with key `RESTAURANT_VISIT`, every edge classified as that type will have `name = "RESTAURANT_VISIT"`. This is how you can tell — given an edge — whether it was matched to a custom type and which one.

## Adding or deleting facts

Facts are generated as part of the ingestion process. If you follow the directions for [adding data to the graph](/adding-business-data), new facts will be created.

Deleting facts is handled by deleting data from the graph. Facts will be deleted when you [delete the edge](/working-with-graphs/deleting-data-from-the-graph) they exist on.

## Retrieving facts

Facts live on edges, so the SDK exposes them under `graph.edge`. List edges for a user or graph, fetch an edge by UUID, or search a graph specifically for facts.

### List and fetch

```python Python
from zep_cloud.client import Zep

client = Zep(api_key=API_KEY)

# List the most recent edges (facts) on a user's graph.
edges = client.graph.edge.get_by_user_id(user_id="emily-painter")

for edge in edges:
    print(f"[{edge.name}] {edge.fact}")

# Fetch a single edge by UUID.
single = client.graph.edge.get(uuid_=edges[0].uuid_)
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({ apiKey: API_KEY });

const edges = await client.graph.edge.getByUserId("emily-painter");

for (const edge of edges) {
  console.log(`[${edge.name}] ${edge.fact}`);
}

const single = await client.graph.edge.get(edges[0].uuid);
```

```go Go
import (
    "context"
    "fmt"
    v3client "github.com/getzep/zep-go/v3/client"
    "github.com/getzep/zep-go/v3/option"
)

client := v3client.NewClient(option.WithAPIKey(API_KEY))

edges, err := client.Graph.Edge.GetByUserID(context.TODO(), "emily-painter")

for _, edge := range edges {
    fmt.Printf("[%s] %s\n", edge.Name, edge.Fact)
}

single, err := client.Graph.Edge.Get(context.TODO(), edges[0].UUID)
```

### Search

Facts are the default search target for [graph search](/searching-the-graph). Calling `graph.search` without a `scope` returns the facts most relevant to your query:

```python Python
results = client.graph.search(
    user_id="emily-painter",
    query="payment failures",
    limit=5,
)

for edge in results.edges or []:
    print(edge.name, edge.fact, edge.score)
```

```typescript TypeScript
const results = await client.graph.search({
  userId: "emily-painter",
  query: "payment failures",
  limit: 5,
});

for (const edge of results.edges ?? []) {
  console.log(edge.name, edge.fact, edge.score);
}
```

```go Go
results, err := client.Graph.Search(context.TODO(), &v3.GraphSearchQuery{
    UserID: v3.String("emily-painter"),
    Query:  "payment failures",
    Limit:  v3.Int(5),
})

for _, edge := range results.Edges {
    fmt.Println(edge.Name, edge.Fact, edge.Score)
}
```

## Related context types

Facts are one of several types of context Zep produces from a user's graph. Each fact lives on an edge between two [entities](/entities) and is extracted from one or more [episodes](/episodes). For durable, cross-entity patterns derived from many facts, see [observations](/observations).