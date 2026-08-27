> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Find Facts Relevant to a Specific Node

Below, we will go through how to retrieve facts which are related to a specific node in a Zep knowledge graph. First, we will go through some methods for determining the UUID of the node you are interested in. Then, we will go through some methods for retrieving the facts related to that node.

## Find the node's UUID

If you are interested in the user's node specifically, we have a convenience method that [returns the user's node](/users#get-the-user-node) which includes the UUID.

An easy way to determine the UUID for other nodes is to use the graph explorer in the [Zep Web app](https://app.getzep.com/).

You can also programmatically retrieve all the nodes for a given user using our [get nodes by user API](/sdk-reference/graph/node/get-by-user-id), and then manually examine the nodes and take note of the UUID of the node of interest:

```python Python
# Initialize the Zep client
zep_client = Zep(api_key=API_KEY)
nodes = zep_client.graph.node.get_by_user_id(user_id="some user ID")
print(nodes)
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

// Initialize the Zep client
const client = new ZepClient({ apiKey: API_KEY });
const nodes = await client.graph.node.getByUserId("some user ID", {});
console.log(nodes);
```

```go Go
import (
    "context"
    "fmt"
    "log"

    "github.com/getzep/zep-go/v3"
    "github.com/getzep/zep-go/v3/client"
    "github.com/getzep/zep-go/v3/option"
)

// Initialize the Zep client
zepClient := client.NewClient(option.WithAPIKey(API_KEY))
nodes, err := zepClient.Graph.Node.GetByUserID(
    context.TODO(),
    "some user ID",
    &zep.GraphNodesRequest{},
)
if err != nil {
    log.Fatalf("Error: %v", err)
}
fmt.Println(nodes)
```

```python Python
center_node_uuid = "your chosen center node UUID"
```

```typescript TypeScript
const centerNodeUuid = "your chosen center node UUID";
```

```go Go
centerNodeUUID := "your chosen center node UUID"
```

Lastly, if your user has a lot of nodes to look through, you can narrow down the search by only looking at the nodes relevant to a specific query, using our [graph search API](/searching-the-graph):

```python Python
results = zep_client.graph.search(
    user_id="some user ID",
    query="shoe", # To help narrow down the nodes you have to manually search
    scope="nodes"
)
relevant_nodes = results.nodes
print(relevant_nodes)
```

```typescript TypeScript
const results = await client.graph.search({
    userId: "some user ID",
    query: "shoe", // To help narrow down the nodes you have to manually search
    scope: "nodes"
});
const relevantNodes = results.nodes;
console.log(relevantNodes);
```

```go Go
results, err := zepClient.Graph.Search(
    context.TODO(),
    &zep.GraphSearchQuery{
        UserID: zep.String("some user ID"),
        Query:  "shoe", // To help narrow down the nodes you have to manually search
        Scope:  zep.GraphSearchScopeNodes.Ptr(),
    },
)
if err != nil {
    log.Fatalf("Error: %v", err)
}
relevantNodes := results.Nodes
fmt.Println(relevantNodes)
```

```python Python
center_node_uuid = "your chosen center node UUID"
```

```typescript TypeScript
const centerNodeUuid = "your chosen center node UUID";
```

```go Go
centerNodeUUID := "your chosen center node UUID"
```

## Retrieve facts related to the node

Use [edge listing](/reading-data-from-the-graph#listing-edges-with-filters) with the `connected_node_uuids` filter to retrieve only the facts connected to your chosen node. The server applies the filter, so you don't fetch and discard every other edge in the graph:

```python Python
from zep_cloud.types import SearchFilters

edges = zep_client.graph.edge.get_by_user_id(
    user_id="some user ID",
    filters=SearchFilters(connected_node_uuids=[center_node_uuid]),
)
relevant_facts = [edge.fact for edge in edges]
```

```typescript TypeScript
const edges = await client.graph.edge.getByUserId("some user ID", {
    filters: { connectedNodeUuids: [centerNodeUuid] },
});
const relevantFacts = edges.map(edge => edge.fact);
```

```go Go
edges, err := zepClient.Graph.Edge.GetByUserID(
    context.TODO(),
    "some user ID",
    &zep.GraphEdgesRequest{
        Filters: &zep.SearchFilters{
            ConnectedNodeUUIDs: []string{centerNodeUUID},
        },
    },
)
if err != nil {
    log.Fatalf("Error: %v", err)
}

var relevantFacts []string
for _, edge := range edges {
    relevantFacts = append(relevantFacts, edge.Fact)
}
```

If you also need the neighboring nodes themselves — not just the facts — use [`get_neighbors`](/sdk-reference/graph/node/get-neighbors), which returns each node connected to `center_node_uuid` together with the edges to it, in one paginated call.

Fetching every edge in the graph and filtering by `source_node_uuid`/`target_node_uuid` client-side, or the older `graph.node.get_edges` convenience endpoint, both work but scale poorly and — for `get_edges` — silently cap the result set. Prefer edge listing with `connected_node_uuids` for any graph beyond toy size.

You can also retrieve facts relevant to your node by using the [graph search API](/searching-the-graph) with the node distance re-ranker:

```python Python
results = zep_client.graph.search(
    user_id="some user ID",
    query="some query",
    reranker="node_distance",
    center_node_uuid=center_node_uuid,
)
relevant_edges = results.edges
relevant_facts = [edge.fact for edge in relevant_edges]
```

```typescript TypeScript
const results = await client.graph.search({
    userId: "some user ID",
    query: "some query",
    reranker: "node_distance",
    centerNodeUuid: centerNodeUuid,
});
const relevantEdges = results.edges;
const relevantFacts = relevantEdges?.map(edge => edge.fact) || [];
```

```go Go
results, err := zepClient.Graph.Search(
    context.TODO(),
    &zep.GraphSearchQuery{
        UserID:         zep.String("some user ID"),
        Query:          "some query",
        Reranker:       zep.GraphSearchRerankerNodeDistance.Ptr(),
        CenterNodeUUID: zep.String(centerNodeUUID),
    },
)
if err != nil {
    log.Fatalf("Error: %v", err)
}

relevantEdges := results.Edges
var relevantFacts []string
for _, edge := range relevantEdges {
    relevantFacts = append(relevantFacts, edge.Fact)
}
```

## Summary

In this recipe, we went through how to retrieve facts which are related to a specific node in a Zep knowledge graph. We first went through some methods for determining the UUID of the node you are interested in. Then, we went through some methods for retrieving the facts related to that node.