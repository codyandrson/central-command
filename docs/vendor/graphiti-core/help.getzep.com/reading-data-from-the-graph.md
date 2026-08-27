> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Reading Data from the Graph

Zep provides APIs to read Edges, Nodes, and Episodes from the graph. These elements can be retrieved individually using their `UUID`, or as lists associated with a specific `user_id` or `graph_id`. The latter method returns all objects in that user's or graph's data.

Examples of each retrieval method are provided below.

## Reading Edges

Alongside `source_node_uuid` and `target_node_uuid`, edge responses can include `source_node_name`, `target_node_name`, `source_node_labels`, and `target_node_labels`. These are projections of current node state, so a node rename shows up on the next read. Zep omits the corresponding fields when an endpoint node cannot be resolved, and omits all four when the API key has active attribute constraints. The edge is still returned with its endpoint UUIDs.

```python Python
from zep_cloud.client import Zep

client = Zep(
    api_key=API_KEY,
)

edge = client.graph.edge.get(edgeUuid)
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({
  apiKey: API_KEY,
});

const edge = await client.graph.edge.get(edgeUuid);
```

```go Go
package main

import (
    "context"
    "fmt"
    "log"

    "github.com/getzep/zep-go/v3/client"
    "github.com/getzep/zep-go/v3/option"
)

func main() {
    ctx := context.Background()

    zepClient := client.NewClient(option.WithAPIKey(apiKey))

    edge, err := zepClient.Graph.Edge.Get(ctx, edgeUUID)
    if err != nil {
        log.Fatal(err)
    }

    fmt.Printf("Edge: %+v\n", edge)
}
```

## Reading Nodes

```python Python
from zep_cloud.client import Zep

client = Zep(
    api_key=API_KEY,
)

node = client.graph.node.get_by_user(userUuid)
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({
  apiKey: API_KEY,
});

const node = await client.graph.node.getByUser(userUuid);
```

```go Go
package main

import (
    "context"
    "fmt"
    "log"

    "github.com/getzep/zep-go/v3/client"
    "github.com/getzep/zep-go/v3/option"
)

func main() {
    ctx := context.Background()

    zepClient := client.NewClient(option.WithAPIKey(apiKey))

    nodes, err := zepClient.Graph.Node.GetByUserID(ctx, userUUID, nil)
    if err != nil {
        log.Fatal(err)
    }

    fmt.Printf("Nodes: %+v\n", nodes)
}
```

## Reading Episodes

```python Python
from zep_cloud.client import Zep

client = Zep(
    api_key=API_KEY,
)

episode = client.graph.episode.get_by_graph_id(graph_uuid)
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({
  apiKey: API_KEY,
});

const episode = client.graph.episode.getByGraphId(graph_uuid);
```

```go Go
package main

import (
    "context"
    "fmt"
    "log"

    "github.com/getzep/zep-go/v3/client"
    "github.com/getzep/zep-go/v3/option"
)

func main() {
    ctx := context.Background()

    zepClient := client.NewClient(option.WithAPIKey(apiKey))

    episodes, err := zepClient.Graph.Episode.GetByGraphID(ctx, graphUUID, nil)
    if err != nil {
        log.Fatal(err)
    }

    fmt.Printf("Episodes: %+v\n", episodes)
}
```

## Listing artifacts in bulk

The methods above return a single artifact by its `UUID`. To enumerate artifacts of a given type in bulk, use the list methods. Where [searching the graph](/searching-the-graph) ranks results by relevance to a query, the list methods return everything of a given type in a user or graph, with filtering, sorting, and pagination applied server-side.

Reach for these methods when you are not answering a question but enumerating data: rendering an entity browser or a facts table in a UI, exporting a graph, or auditing what a graph contains. Sorting and cursor pagination let you walk large graphs in stable, predictable pages instead of pulling everything into memory at once.

The same parameters — `filters`, `order_by`, `direction`, `limit`, and `cursor` — are shared across four artifact types:

* **Nodes** (entities) — see [Entities](/entities).
* **Edges** (facts) — see [Facts](/facts).
* **Observations** — see [Observations](/observations).
* **Thread summaries** — see [Thread summaries](/thread-summaries).

Each type exposes two list methods: `get_by_user_id` for a user's graph and `get_by_graph_id` for a named graph. Both return a flat array of typed objects.

[Document summaries](/documents) list through `graph.document_summary.get_by_graph_id` on a named graph.

Episodes paginate the same way but take a smaller parameter set of their own — see [Listing episodes](#listing-episodes).

### Shared parameters

All four artifact types accept the same parameters on both `get_by_user_id` and `get_by_graph_id`.

| Parameter     | Type            | Description                                                                                                                                                                                                                                                           | Default  |
| ------------- | --------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------- |
| `filters`     | `SearchFilters` | Restrict which artifacts are returned. Same type as `graph.search`, supporting date filters (`created_at`, `valid_at`, `expired_at`, `invalid_at` as 2D OR/AND arrays), `edge_types`/`exclude_edge_types`, `node_labels`/`exclude_node_labels`, and property filters. | –        |
| `order_by`    | string          | Field to sort by: `"created_at"` or `"uuid"`.                                                                                                                                                                                                                         | `"uuid"` |
| `direction`   | string          | Sort direction: `"asc"` or `"desc"`.                                                                                                                                                                                                                                  | `"desc"` |
| `limit`       | integer         | Maximum number of items returned per call. Explicit values are capped at `50`; omitting the parameter uses a page size of `100`.                                                                                                                                      | `100`    |
| `cursor`      | string          | Opaque forward cursor for the next page. Read it from the `Zep-Next-Cursor` response header (see [Pagination](#pagination)).                                                                                                                                          | –        |
| `uuid_cursor` | string          | Deprecated legacy cursor. Pass the UUID of the last item from the previous page. Prefer `cursor`.                                                                                                                                                                     | –        |

The `filters` object is the same `SearchFilters` type documented under [Search Filters](/searching-the-graph#search-filters), so date, type, and property filters behave identically here. Refer to that section for the full filter reference.

### Listing nodes

List the entities in a user's graph, most recently created first.

```python Python
from zep_cloud.client import Zep

client = Zep(api_key=API_KEY)

# The 20 most recently created entities in a user's graph.
nodes = client.graph.node.get_by_user_id(
    user_id="emily-painter",
    order_by="created_at",
    direction="desc",
    limit=20,
)

for node in nodes:
    print(node.name, node.created_at)
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({ apiKey: API_KEY });

// The 20 most recently created entities in a user's graph.
const nodes = await client.graph.node.getByUserId("emily-painter", {
  orderBy: "created_at",
  direction: "desc",
  limit: 20,
});

for (const node of nodes) {
  console.log(node.name, node.createdAt);
}
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

// The 20 most recently created entities in a user's graph.
nodes, err := client.Graph.Node.GetByUserID(
    context.TODO(),
    "emily-painter",
    &v3.GraphNodesRequest{
        OrderBy:   v3.String("created_at"),
        Direction: v3.String("desc"),
        Limit:     v3.Int(20),
    },
)

for _, node := range nodes {
    fmt.Println(node.Name, node.CreatedAt)
}
```

To list entities in a named (non-user) graph, use `get_by_graph_id` with a `graph_id`. See [Entities](/entities) for per-type detail.

### Listing edges with filters

Pass a `SearchFilters` object to narrow the results. The example below lists facts on a named graph that were created in July 2025 and use specific edge types.

```python Python
from zep_cloud.client import Zep
from zep_cloud.types import SearchFilters, DateFilter

client = Zep(api_key=API_KEY)

edges = client.graph.edge.get_by_graph_id(
    graph_id="my-graph",
    filters=SearchFilters(
        edge_types=["WORKS_WITH", "COLLABORATES_ON"],
        created_at=[
            [
                DateFilter(comparison_operator=">=", date="2025-07-01T00:00:00Z"),
                DateFilter(comparison_operator="<", date="2025-08-01T00:00:00Z"),
            ],
        ],
    ),
    order_by="created_at",
    direction="desc",
    limit=50,
)

for edge in edges:
    print(edge.fact, edge.created_at)
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({ apiKey: API_KEY });

const edges = await client.graph.edge.getByGraphId("my-graph", {
  filters: {
    edgeTypes: ["WORKS_WITH", "COLLABORATES_ON"],
    createdAt: [
      [
        { comparisonOperator: ">=", date: "2025-07-01T00:00:00Z" },
        { comparisonOperator: "<", date: "2025-08-01T00:00:00Z" },
      ],
    ],
  },
  orderBy: "created_at",
  direction: "desc",
  limit: 50,
});

for (const edge of edges) {
  console.log(edge.fact, edge.createdAt);
}
```

```go Go
edges, err := client.Graph.Edge.GetByGraphID(
    context.TODO(),
    "my-graph",
    &v3.GraphEdgesRequest{
        Filters: &v3.SearchFilters{
            EdgeTypes: []string{"WORKS_WITH", "COLLABORATES_ON"},
            CreatedAt: [][]*v3.DateFilter{
                {
                    {ComparisonOperator: v3.ComparisonOperatorGreaterThanEqual, Date: "2025-07-01T00:00:00Z"},
                    {ComparisonOperator: v3.ComparisonOperatorLessThan, Date: "2025-08-01T00:00:00Z"},
                },
            },
        },
        OrderBy:   v3.String("created_at"),
        Direction: v3.String("desc"),
        Limit:     v3.Int(50),
    },
)

for _, edge := range edges {
    fmt.Println(edge.Fact, edge.CreatedAt)
}
```

The date filter uses the same 2D OR/AND array structure as `graph.search`: the outer array is OR, the inner array is AND. See [Datetime Filtering](/searching-the-graph#datetime-filtering) for the full semantics.

### Pagination

The list methods return a flat array, not a paginated envelope. The forward cursor for the next page comes back in the **`Zep-Next-Cursor` response header**. To read it, use the SDK's raw response accessor — `with_raw_response` in Python, `withRawResponse` in TypeScript, and `WithRawResponse` in Go — which returns both the parsed data and the raw HTTP response.

Pass the cursor from one page as the `cursor` argument of the next call. When the header is empty, there are no more pages.

```python Python
from zep_cloud.client import Zep

client = Zep(api_key=API_KEY)

all_nodes = []
cursor = None

while True:
    resp = client.graph.node.with_raw_response.get_by_user_id(
        user_id="emily-painter",
        order_by="created_at",
        direction="desc",
        limit=100,
        cursor=cursor,
    )
    all_nodes.extend(resp.data)

    cursor = resp.headers.get("Zep-Next-Cursor")
    if not cursor:
        break
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({ apiKey: API_KEY });

const allNodes = [];
let cursor: string | undefined = undefined;

while (true) {
  const { data, rawResponse } = await client.graph.node
    .getByUserId("emily-painter", {
      orderBy: "created_at",
      direction: "desc",
      limit: 100,
      cursor,
    })
    .withRawResponse();

  allNodes.push(...data);

  cursor = rawResponse.headers.get("Zep-Next-Cursor") ?? undefined;
  if (!cursor) break;
}
```

```go Go
var allNodes []*v3.EntityNode
var cursor string

for {
    req := &v3.GraphNodesRequest{
        OrderBy:   v3.String("created_at"),
        Direction: v3.String("desc"),
        Limit:     v3.Int(100),
    }
    if cursor != "" {
        req.Cursor = v3.String(cursor)
    }

    resp, err := client.Graph.Node.WithRawResponse.GetByUserID(
        context.TODO(),
        "emily-painter",
        req,
    )
    if err != nil {
        log.Fatal(err)
    }

    allNodes = append(allNodes, resp.Body...)

    cursor = resp.Header.Get("Zep-Next-Cursor")
    if cursor == "" {
        break
    }
}
```

`uuid_cursor` is the deprecated legacy pagination path: pass the UUID of the last item from the previous page to fetch the next one. Prefer the opaque `cursor` from the `Zep-Next-Cursor` header, which encodes the sort field, direction, and continuation position.

### Listing observations and thread summaries

Observations and thread summaries use the same shared parameters. The examples below list each for a user, newest first.

```python Python
# Observations for a user, newest first.
observations = client.graph.observation.get_by_user_id(
    user_id="emily-painter",
    order_by="created_at",
    direction="desc",
    limit=20,
)

# Thread summaries across a user's threads.
summaries = client.graph.thread_summary.get_by_user_id(
    user_id="emily-painter",
    limit=20,
)
```

```typescript TypeScript
// Observations for a user, newest first.
const observations = await client.graph.observation.getByUserId("emily-painter", {
  orderBy: "created_at",
  direction: "desc",
  limit: 20,
});

// Thread summaries across a user's threads.
const summaries = await client.graph.threadSummary.getByUserId("emily-painter", {
  limit: 20,
});
```

```go Go
// Observations for a user, newest first.
observations, err := client.Graph.Observation.GetByUserID(
    context.TODO(),
    "emily-painter",
    &v3.GraphObservationsRequest{
        OrderBy:   v3.String("created_at"),
        Direction: v3.String("desc"),
        Limit:     v3.Int(20),
    },
)

// Thread summaries across a user's threads.
summaries, err := client.Graph.ThreadSummary.GetByUserID(
    context.TODO(),
    "emily-painter",
    &v3.GraphThreadSummariesRequest{
        Limit: v3.Int(20),
    },
)
```

See [Observations](/observations) and [Thread summaries](/thread-summaries) for per-type detail. Use `get_by_graph_id` for a named graph. [Document summaries](/documents) are the analogue for episodes grouped by `document_id` and list through `graph.document_summary.get_by_graph_id`.

### Listing episodes

Episodes list through `graph.episode.list_by_user_id` and `graph.episode.list_by_graph_id`, which take the same `order_by`, `direction`, `limit`, and `cursor` parameters as the other artifact types. Explicit `limit` values are capped at `50`; omitting `limit` uses a page size of `100`. Use these methods to walk every episode in a graph in stable pages — see [Pagination](#pagination).

Episodes do not accept a `SearchFilters` object. Their one filter is `mentioned_node_uuids`, which restricts results to episodes mentioning any of the listed entities — up to 256 UUIDs.

```python Python
# Episodes that mention a given entity, newest first.
episodes = client.graph.episode.list_by_user_id(
    user_id="emily-painter",
    mentioned_node_uuids=[node_uuid],
    order_by="created_at",
    direction="desc",
    limit=50,
)

for episode in episodes:
    print(episode.uuid_, episode.created_at)
```

```typescript TypeScript
// Episodes that mention a given entity, newest first.
const episodes = await client.graph.episode.listByUserId("emily-painter", {
  mentionedNodeUuids: [nodeUuid],
  orderBy: "created_at",
  direction: "desc",
  limit: 50,
});

for (const episode of episodes) {
  console.log(episode.uuid, episode.createdAt);
}
```

```go Go
// Episodes that mention a given entity, newest first.
episodes, err := client.Graph.Episode.ListByUserID(
    context.TODO(),
    "emily-painter",
    &v3.GraphEpisodeListRequest{
        MentionedNodeUUIDs: []string{nodeUUID},
        OrderBy:            v3.String("created_at"),
        Direction:          v3.String("desc"),
        Limit:              v3.Int(50),
    },
)

for _, episode := range episodes {
    fmt.Println(episode.UUID, episode.CreatedAt)
}
```

`graph.episode.get_by_user_id` and `graph.episode.get_by_graph_id` remain the most-recent-`lastn` convenience reads. They accept only `lastn` and return a `GraphEpisodeResponse` envelope rather than a paginated array, so prefer the list methods whenever you need ordering, filtering, or more than one page.

## Navigating the graph

Listing walks a graph by artifact type. Navigation walks it by connection: start from a node and pull what it is attached to. When the API key has no active attribute constraints, both endpoints include the connecting edges' endpoint names and labels (`source_node_name`, `target_node_name`, `source_node_labels`, `target_node_labels`), avoiding separate endpoint-node reads. With active attribute constraints, Zep omits these four fields and retains the endpoint UUIDs.

### Neighbors of a node

`graph.node.get_neighbors` returns each distinct node connected to an anchor node, together with every edge that connects it to the anchor. Results paginate by neighbor node with the same `cursor` parameter as the list methods (see [Pagination](#pagination)).

| Parameter        | Type            | Description                                                                                                                                                                                             | Default  |
| ---------------- | --------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------- |
| `direction`      | string          | Orientation of the connecting edge relative to the anchor: `"out"`, `"in"`, or `"both"`.                                                                                                                | `"both"` |
| `filters`        | `SearchFilters` | Constrains the connecting edges (edge types, dates, and the [node and episode filters](/searching-the-graph#node-and-episode-filtering)) and the neighbor nodes (`node_labels`, `exclude_node_labels`). | –        |
| `order_by`       | string          | Field to sort neighbor nodes by: `"created_at"` or `"uuid"`.                                                                                                                                            | `"uuid"` |
| `direction_sort` | string          | Sort direction for `order_by`: `"asc"` or `"desc"`. Named separately so it does not clash with the traversal `direction`.                                                                               | `"desc"` |
| `limit`          | integer         | Maximum neighbor nodes per page. Explicit values are capped at `50`; omitting the parameter uses a page size of `100`.                                                                                  | `100`    |
| `cursor`         | string          | Opaque forward cursor from the previous page.                                                                                                                                                           | –        |

```python Python
neighbors = client.graph.node.get_neighbors(
    node_uuid=node_uuid,
    direction="both",
    limit=25,
)

for neighbor in neighbors:
    print(neighbor.node.name, len(neighbor.edges))
```

```typescript TypeScript
const neighbors = await client.graph.node.getNeighbors(nodeUuid, {
  direction: "both",
  limit: 25,
});

for (const neighbor of neighbors) {
  console.log(neighbor.node?.name, neighbor.edges?.length);
}
```

```go Go
neighbors, err := client.Graph.Node.GetNeighbors(
    context.TODO(),
    nodeUUID,
    &v3.GraphNodeNeighborsRequest{
        Direction: v3.String("both"),
        Limit:     v3.Int(25),
    },
)

for _, neighbor := range neighbors {
    fmt.Println(neighbor.Node.Name, len(neighbor.Edges))
}
```

### Bounded subgraphs

`graph.get_subgraph` expands breadth-first from up to 20 seed nodes and returns the resulting neighborhood as a single `{nodes, edges}` payload. Every edge's endpoints are present in `nodes`, so the response is a self-contained graph you can render directly. It is built for agent exploration and visualization, not for exporting a graph — use the list methods for that.

| Parameter              | Type            | Description                                                                                                                                                                                      | Default  |
| ---------------------- | --------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | -------- |
| `user_id` / `graph_id` | string          | Target graph. Exactly one is required.                                                                                                                                                           | –        |
| `seed_node_uuids`      | array           | Nodes to expand from, 1–20 entries. Seeds are admitted in request order. Seeds that do not exist are ignored.                                                                                    | –        |
| `depth`                | integer         | Maximum hops from the seeds, 1–3.                                                                                                                                                                | `1`      |
| `direction`            | string          | Edge orientation followed during expansion: `"out"`, `"in"`, or `"both"`.                                                                                                                        | `"both"` |
| `search_filters`       | `SearchFilters` | Constrains traversed edges and included nodes. A node excluded by a filter is not expanded through. `episode_metadata_filters` is rejected here, because it cannot be enforced during traversal. | –        |
| `max_nodes`            | integer         | Node budget, 1–500. Seeds count against it.                                                                                                                                                      | `100`    |
| `max_edges`            | integer         | Edge budget, 1–1000.                                                                                                                                                                             | `200`    |

When a budget stops the expansion, the response sets `truncated` to `true` and names the binding limit in `truncation_reason` (for example `max_nodes` or `max_edges`), so a partial neighborhood is never mistaken for a complete one.

```python Python
subgraph = client.graph.get_subgraph(
    user_id="emily-painter",
    seed_node_uuids=[node_uuid],
    depth=2,
    max_nodes=200,
)

print(len(subgraph.nodes), len(subgraph.edges))
if subgraph.truncated:
    print("truncated by:", subgraph.truncation_reason)
```

```typescript TypeScript
const subgraph = await client.graph.getSubgraph({
  userId: "emily-painter",
  seedNodeUuids: [nodeUuid],
  depth: 2,
  maxNodes: 200,
});

console.log(subgraph.nodes?.length, subgraph.edges?.length);
if (subgraph.truncated) {
  console.log("truncated by:", subgraph.truncationReason);
}
```

```go Go
subgraph, err := client.Graph.GetSubgraph(
    context.TODO(),
    &v3.GraphSubgraphRequest{
        UserID:        v3.String("emily-painter"),
        SeedNodeUUIDs: []string{nodeUUID},
        Depth:         v3.Int(2),
        MaxNodes:      v3.Int(200),
    },
)

fmt.Println(len(subgraph.Nodes), len(subgraph.Edges))
if subgraph.Truncated != nil && *subgraph.Truncated {
    fmt.Println("truncated by:", *subgraph.TruncationReason)
}
```

### Deprecated by-node reads

Three convenience endpoints predate the filters and endpoints above. They still work with unchanged behavior, but each caps its result set internally, cannot paginate past that cap, and does not indicate truncation in its standard SDK response. Prefer the replacements for any graph beyond toy size:

| Deprecated method                   | Replacement                                                              |
| ----------------------------------- | ------------------------------------------------------------------------ |
| `graph.node.get_edges`              | Edge listing with `connected_node_uuids`, or `graph.node.get_neighbors`. |
| `graph.node.get_episodes`           | Episode listing with `mentioned_node_uuids`.                             |
| `graph.episode.get_nodes_and_edges` | Node and edge listing with `episode_uuids`.                              |

## Related

* [Searching the graph](/searching-the-graph) — rank artifacts by relevance to a query, including the full `SearchFilters` reference.
* [Entities](/entities), [Facts](/facts), [Observations](/observations), and [Thread summaries](/thread-summaries) — per-type detail for each artifact.