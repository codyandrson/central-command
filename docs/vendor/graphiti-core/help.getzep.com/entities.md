> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Entities

## Overview

An **entity** is one of the nouns that appear in the data Zep ingests — a person, an account, a product, a place, a concept. Zep extracts entities from the [episodes](/episodes) you add to a graph, links them with [facts](/facts), and exposes them as nodes you can list, fetch, and search.

Each entity has two pieces of information that matter most:

* A **name** — a short, human-readable label like `Emily Painter`, `Adidas shoes`, or `Account Emily0e62`.
* A **summary** — a narrative description of everything the graph knows about this entity, regenerated as new facts arrive.

## Deduplication

Zep automatically deduplicates and merges entities when it determines that two refer to the same thing. This happens transparently as new data is ingested — there is no developer-facing knob to tune or disable.

## Entity summaries

The summary on an entity is a narrative version of all the facts involving that entity. Every time a new fact is added to the graph, the summary for each affected entity is updated using the previous summary plus the new information. This incremental update process keeps summaries current and contextually relevant as the graph evolves.

### Summaries vs. facts

Entities and [facts](/facts) capture different kinds of information, and Zep's [Context Block](/retrieving-context#zeps-context-block) includes both:

* **Facts** are granular knowledge snippets. Each one captures a specific, discrete claim with precise temporal validity.
* **Entity summaries** are aggregated narratives. They roll up an entity's involvement across many facts and relationships into a single description.

Using both gives an agent breadth and depth at the same time: the precise dated claims it needs to be correct, plus the contextualized history it needs to be coherent.

For durable patterns that span multiple entities — recurring loops, decisions, commitments — see [observations](/observations).

The summaries described here are entity-level. For per-thread summaries of a conversation's messages, see [Thread summaries](/thread-summaries).

#### Duplicate information across entity summaries is expected

You may see the same relationship appear in the summaries of multiple entities. Each entity summary is meant to stand on its own and describe that entity's relationships, so shared relationships naturally show up in both ends.

## Retrieving entities

Use the SDK to list entities for a user or graph, fetch a single entity by UUID, or search a graph specifically for entities.

### List and fetch

```python Python
from zep_cloud.client import Zep

client = Zep(api_key=API_KEY)

# List the entities Zep has extracted into a user's graph.
entities = client.graph.node.get_by_user_id(
    user_id="emily-painter",
    limit=20,
)

for entity in entities:
    print(f"{entity.name}: {entity.summary}")

# Fetch a single entity by UUID.
single = client.graph.node.get(uuid_=entities[0].uuid_)
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({ apiKey: API_KEY });

// List the entities Zep has extracted into a user's graph.
const entities = await client.graph.node.getByUserId(
  "emily-painter",
  { limit: 20 },
);

for (const entity of entities) {
  console.log(`${entity.name}: ${entity.summary}`);
}

// Fetch a single entity by UUID.
const single = await client.graph.node.get(entities[0].uuid);
```

```go Go
import (
    "context"
    "fmt"
    v3 "github.com/getzep/zep-go/v3"
    v3client "github.com/getzep/zep-go/v3/client"
    "github.com/getzep/zep-go/v3/option"
)

client := v3client.NewClient(
    option.WithAPIKey(API_KEY),
)

// List the entities Zep has extracted into a user's graph.
entities, err := client.Graph.Node.GetByUserID(
    context.TODO(),
    "emily-painter",
    &v3.GraphNodesRequest{Limit: v3.Int(20)},
)

for _, entity := range entities {
    fmt.Printf("%s: %s\n", entity.Name, entity.Summary)
}

// Fetch a single entity by UUID.
single, err := client.Graph.Node.Get(context.TODO(), entities[0].UUID)
```

For graph-scoped entities, use `get_by_graph_id` (Python), `getByGraphId` (TypeScript), or `GetByGraphID` (Go) with a `graph_id`.

List endpoints support UUID-cursor pagination. Pass the UUID of the last item from the previous page as `uuid_cursor` to fetch the next page.

### Search

To search a graph specifically for entities, pass `scope="nodes"` to `graph.search`:

```python Python
results = client.graph.search(
    user_id="emily-painter",
    query="favorite running shoes",
    scope="nodes",
    limit=5,
)

for entity in results.nodes or []:
    print(entity.name, entity.score)
```

```typescript TypeScript
const results = await client.graph.search({
  userId: "emily-painter",
  query: "favorite running shoes",
  scope: "nodes",
  limit: 5,
});

for (const entity of results.nodes ?? []) {
  console.log(entity.name, entity.score);
}
```

```go Go
results, err := client.Graph.Search(context.TODO(), &v3.GraphSearchQuery{
    UserID: v3.String("emily-painter"),
    Query:  "favorite running shoes",
    Scope:  v3.GraphSearchScopeNodes.Ptr(),
    Limit:  v3.Int(5),
})

for _, entity := range results.Nodes {
    fmt.Println(entity.Name, entity.Score)
}
```

See [Searching the Graph](/searching-the-graph) for the full search API.

## Entities and the Context Block

Entities are included in the default [Context Block](/retrieving-context): the entities most relevant to the user's recent messages are rendered alongside facts and episodes. To customize which entities appear or how they are formatted, define a [context template](/context-templates) that uses the `%{entities}` variable, or build a custom block via [advanced context block construction](/advanced-context-block-construction).