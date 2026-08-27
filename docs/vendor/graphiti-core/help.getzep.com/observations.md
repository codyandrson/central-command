> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Observations

> Observations are durable, evidence-backed patterns Zep derives from a Context Graph — recurring loops, preferences, commitments, and state transitions across many facts.

Available to [Flex Plus and Enterprise](https://www.getzep.com/pricing) customers.

## Overview

An **observation** is a durable, evidence-backed piece of context that Zep automatically derives from a graph. Each observation captures a meaningful change, decision, commitment, constraint, preference, state transition, recurring pattern, or stable relationship involving one or more entities.

Observations sit alongside [facts](/facts) and [entity summaries](/entities) as a derived construct on the graph, but they answer a different question:

* **Facts** are granular, time-stamped claims stored on a single edge between two entities.
* **Entity summaries** are entity-centered narratives that describe the history of a single node.
* **Observations** are cross-entity context that captures *why* something matters — the persistent decisions, behaviors, and relationships that span multiple facts and entities.

This makes observations especially useful for surfacing context that would otherwise be diluted across many facts: a user's evolving preferences, a recurring failure mode, a long-running commitment, or the way two entities consistently interact over time.

## How Zep creates observations

Observations are detected automatically by analyzing structural patterns in the graph — clusters of facts and entities that together describe the same underlying behavior or relationship. Zep then synthesizes a name and summary for each observation it detects.

Observations are deduplicated and merged: when new evidence fits an existing observation, the existing observation is regenerated with the new evidence merged in. When a newer observation supersedes an older one, the older observation is retired so the graph reflects the current state of what is known.

Observations are read-only — they cannot be created, edited, or deleted directly. They follow the evidence in the graph.

## Steering observations

By default, Zep decides how each observation is worded and assigns it a generic type. With [observation steering](/steering-observations), you can guide that generation — shaping the wording and relevance of new observations and assigning custom types you can filter on during search. Steering changes how observations are generated, not the evidence behind them.

## Retrieving observations

Use the SDK to list observations for a user or graph, fetch a single observation by UUID, or search a graph specifically for observations.

### List and fetch

```python Python
from zep_cloud.client import Zep

client = Zep(api_key=API_KEY)

# List the observations Zep has materialized for a user's graph.
observations = client.graph.observation.get_by_user_id(
    user_id="emily-painter",
    limit=20,
)

for obs in observations:
    print(f"{obs.name}: {obs.summary}")

# Fetch a single observation by UUID.
single = client.graph.observation.get(uuid_=observations[0].uuid_)
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({ apiKey: API_KEY });

// List the observations Zep has materialized for a user's graph.
const observations = await client.graph.observation.getByUserId(
  "emily-painter",
  { limit: 20 },
);

for (const obs of observations) {
  console.log(`${obs.name}: ${obs.summary}`);
}

// Fetch a single observation by UUID.
const single = await client.graph.observation.get(observations[0].uuid);
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

// List the observations Zep has materialized for a user's graph.
observations, err := client.Graph.Observation.GetByUserID(
    context.TODO(),
    "emily-painter",
    &v3.GraphObservationsRequest{Limit: v3.Int(20)},
)

for _, obs := range observations {
    fmt.Printf("%s: %s\n", obs.Name, obs.Summary)
}

// Fetch a single observation by UUID.
single, err := client.Graph.Observation.Get(context.TODO(), observations[0].UUID)
```

For graph-scoped observations, use `get_by_graph_id` (Python), `getByGraphId` (TypeScript), or `GetByGraphID` (Go) with a `graph_id`.

List endpoints support UUID-cursor pagination. Pass the UUID of the last item from the previous page as `uuid_cursor` to fetch the next page.

### Search

```python Python
results = client.graph.search(
    user_id="emily-painter",
    query="account suspension and recovery",
    scope="observations",
    limit=5,
)

for obs in results.observations or []:
    print(obs.name, obs.score)
```

```typescript TypeScript
const results = await client.graph.search({
  userId: "emily-painter",
  query: "account suspension and recovery",
  scope: "observations",
  limit: 5,
});

for (const obs of results.observations ?? []) {
  console.log(obs.name, obs.score);
}
```

```go Go
results, err := client.Graph.Search(context.TODO(), &v3.GraphSearchQuery{
    UserID: v3.String("emily-painter"),
    Query:  "account suspension and recovery",
    Scope:  v3.GraphSearchScopeObservations.Ptr(),
    Limit:  v3.Int(5),
})

for _, obs := range results.Observations {
    fmt.Println(obs.Name, obs.Score)
}
```

## Observations and the Context Block

Observations may be included in the default [Context Block](/retrieving-context) when Smart Context Assembly selects them as relevant to the current conversation. To always include them — or to pin a specific count — use a [context template](/context-templates). For full control over which observations appear and how they're formatted, retrieve them directly with the SDK methods above, or build a custom block via [advanced context block construction](/advanced-context-block-construction).

## Related

* [Steering Observations](/steering-observations) — guide how Zep words and categorizes new observations.
* [Facts](/facts) — granular, edge-level claims that often serve as evidence for observations.
* [Entities](/entities) — entity-level summaries on the Context Graph.
* [Episodes](/episodes) — the supporting evidence that observations are grounded in.
* [Searching the Graph](/searching-the-graph) — how to surface observations in graph search.
* [Context Types](/context-types) — overview of the other context types.