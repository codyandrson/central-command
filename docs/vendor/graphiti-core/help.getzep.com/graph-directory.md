> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Graph directory

Agents that can reach more than one standalone graph need a bounded catalog of
what each graph contains. The graph directory is that catalog: project-scoped
metadata for live standalone graphs, with pagination and lexical search over
`graph_id`, `name`, and `description`.

The directory does not search facts, entities, episodes, or other graph
contents. Use [graph search](/searching-the-graph) after you select a
`graph_id`.

## What the directory returns

Each entry is one live standalone graph in the authenticated project.

| Field         | Role                                                                            |
| ------------- | ------------------------------------------------------------------------------- |
| `graph_id`    | Stable selector for later graph operations                                      |
| `name`        | Optional short label                                                            |
| `description` | Optional plain-text explanation of subject matter, intended use, and boundaries |
| `created_at`  | Creation time                                                                   |
| `updated_at`  | Last metadata update time                                                       |

Names and descriptions are customer-authored routing metadata. Zep does not
generate them from graph contents. Graphs without a name or description still
appear; clients should fall back to `graph_id` for display.

The directory excludes user graphs, soft-deleted graphs, and graphs outside the
authenticated project. For how to create standalone graphs and set routing
metadata, see [Create graph](/create-graph).

## List and search with the SDKs

The SDK entry point is `graph.list_all` (`GET /graph/list-all`). Omit `search`
for an unfiltered page. Pass `search` to filter by `graph_id`, `name`, and
`description`: every whitespace-separated term must substring-match at least
one of those three fields (case-insensitive).

| Parameter                    | Behavior                                                                                                                                                                                                                                                          |
| ---------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `page_number` / `pageNumber` | Starts at 1                                                                                                                                                                                                                                                       |
| `page_size` / `pageSize`     | Default 50 when omitted; valid range 1–100; explicit `0` is invalid                                                                                                                                                                                               |
| `search`                     | Optional metadata filter; empty or whitespace-only is an unfiltered list; every whitespace-separated term must match at least one of `graph_id`, `name`, or `description`; queries longer than 200 Unicode code points after whitespace normalization are invalid |
| `order_by`                   | `created_at`, `graph_id`, or `name`. With `search`, an explicit `order_by` replaces relevance ordering                                                                                                                                                            |
| `asc`                        | Sort direction when using column order (unfiltered list, or search with `order_by`)                                                                                                                                                                               |

Default unfiltered order is newest first (`created_at` descending), with
`graph_id` as the tie-breaker. When `search` is set and `order_by` is omitted,
results order by lexical relevance, then `name` and `graph_id`. Passing
`order_by` with `search` sorts by that column and `asc` instead. Relevance
scores are internal and are not part of the public contract.

Responses include `graphs`, `page_number`, `page_size`, `row_count` (entries on
this page), and `total_count` (visible entries matching the filter). A page past
the end returns an empty `graphs` array with the correct `total_count`.

```python Python
from zep_cloud.client import Zep

client = Zep(
    api_key=API_KEY,
)

# First page of standalone graphs in the project
page = client.graph.list_all(
    page_number=1,
    page_size=50,
)
print(f"Page {page.page_number}: {page.row_count} of {page.total_count} graphs")

# Narrow by routing metadata when you know the kind of context you need
matches = client.graph.list_all(
    search="EMEA support",
    page_number=1,
    page_size=20,
)

for graph in matches.graphs or []:
    print(graph.graph_id, graph.name, graph.description)
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({
  apiKey: API_KEY,
});

// First page of standalone graphs in the project
const page = await client.graph.listAll({
  pageNumber: 1,
  pageSize: 50,
});
console.log(`Page ${page.pageNumber}: ${page.rowCount} of ${page.totalCount} graphs`);

// Narrow by routing metadata when you know the kind of context you need
const matches = await client.graph.listAll({
  search: "EMEA support",
  pageNumber: 1,
  pageSize: 20,
});

for (const graph of matches.graphs ?? []) {
  console.log(graph.graphId, graph.name, graph.description);
}
```

```go Go
import (
    "context"
    "fmt"
    "log"

    "github.com/getzep/zep-go/v3"
    zepclient "github.com/getzep/zep-go/v3/client"
    "github.com/getzep/zep-go/v3/option"
)

client := zepclient.NewClient(
    option.WithAPIKey(apiKey),
)

// First page of standalone graphs in the project
page, err := client.Graph.ListAll(context.TODO(), &zep.GraphListAllRequest{
    PageNumber: zep.Int(1),
    PageSize:   zep.Int(50),
})
if err != nil {
    log.Fatalf("Failed to list graphs: %v", err)
}
rowCount, totalCount := 0, 0
if page.RowCount != nil {
    rowCount = *page.RowCount
}
if page.TotalCount != nil {
    totalCount = *page.TotalCount
}
fmt.Printf("Listed %d of %d graphs\n", rowCount, totalCount)

// Narrow by routing metadata when you know the kind of context you need
matches, err := client.Graph.ListAll(context.TODO(), &zep.GraphListAllRequest{
    Search:     zep.String("EMEA support"),
    PageNumber: zep.Int(1),
    PageSize:   zep.Int(20),
})
if err != nil {
    log.Fatalf("Failed to search graph directory: %v", err)
}

for _, graph := range matches.Graphs {
    graphID, name, description := "", "", ""
    if graph.GraphID != nil {
        graphID = *graph.GraphID
    }
    if graph.Name != nil {
        name = *graph.Name
    }
    if graph.Description != nil {
        description = *graph.Description
    }
    fmt.Println(graphID, name, description)
}
```

After you select a `graph_id`, search its contents with
[`graph.search`](/searching-the-graph) (or add data with
[`graph.add`](/adding-business-data)).

Full request and response fields are in the
[List all graphs](/sdk-reference/graph/list-all) SDK reference.

## Memory MCP exposure

When [standalone graph access](/memory-mcp-server/authentication#standalone-graph-access)
is enabled on the project connection, Memory MCP exposes the same directory
contract through two surfaces:

| Surface                  | Role                                                                                                                                                                                                                                                                                                                       |
| ------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `zep://graphs/directory` | Read-only resource for a paginated overview (`graph_id`, name, description). The template `zep://graphs/directory{?page,page_size}` requests later pages. Default `page_size` is 50; maximum is 100 (same as the API).                                                                                                     |
| `list_graphs`            | Directory search tool. Optional `search` (omit or empty for an unfiltered list; every whitespace-separated term must match at least one of `graph_id`, `name`, or `description`; longer than 200 Unicode code points after whitespace normalization is invalid), `page` (default 1), and `limit` (default 20, maximum 50). |

Use the returned `graph_id` with `search_graph_in` (and the other `_in` tools)
to work with one selected standalone graph. Directory metadata search is not
the same as content search: `list_graphs` matches routing metadata;
`search_graph` and `search_graph_in` search graph contents.

Suggested agent flow:

1. Read `zep://graphs/directory` for an overview, or call `list_graphs` with
   `search` when you know the kind of context you need.
2. Pick a matching `graph_id`.
3. Call `search_graph_in` (or another `_in` tool) on that graph.
4. Do not query every accessible graph when one or a few clearly match.

Which graphs appear depends on the connection's
[standalone graph authorization](/memory-mcp-server/standalone-graph-authorization)
mode. Project-wide mode lists every live standalone graph in the token-bound
project. UserGroup ABAC mode applies the same `graph.search` grants as
`search_graph_in` before search, sort, count, and pagination. Hidden graphs do
not appear in results, counts, or ranking.

See [Memory MCP Server](/memory-mcp-server) and
[Connecting a client](/memory-mcp-server/connect) for setup and tool lists.

## Limits

* The directory is metadata-only. It does not list user graphs or search graph
  contents.
* Descriptions are optional and customer-authored. Missing metadata does not
  hide a graph.
* Offset pagination can shift if graphs are created, updated, or deleted
  between page requests. Restart from page 1 when you need a coherent view
  after metadata changes.
* API and MCP share the directory contract. Page-size defaults: API and the
  `zep://graphs/directory` resource default to 50 (max 100); MCP `list_graphs`
  defaults to 20 (max 50).