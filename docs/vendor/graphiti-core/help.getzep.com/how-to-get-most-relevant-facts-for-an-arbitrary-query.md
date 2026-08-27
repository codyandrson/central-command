> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Get Most Relevant Facts for an Arbitrary Query

In this recipe, we demonstrate how to retrieve the most relevant facts from the knowledge graph using an arbitrary search query.

## Search the graph

First, we perform a [search](/searching-the-graph) on the knowledge graph using a sample query:

```python
from zep_cloud.client import Zep

zep_client = Zep(api_key=API_KEY)
results = zep_client.graph.search(user_id="some user_id", query="Some search query", scope="edges")
```

```typescript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({ apiKey: process.env.ZEP_API_KEY || "" });
const results = await client.graph.search({
    userId: "some user_id",
    query: "Some search query",
    scope: "edges"
});
```

```go
package main

import (
    "context"
    "fmt"
    "log"
    "os"

    "github.com/getzep/zep-go/v3"
    zepclient "github.com/getzep/zep-go/v3/client"
    "github.com/getzep/zep-go/v3/option"
)

func main() {
    ctx := context.Background()

    client := zepclient.NewClient(
        option.WithAPIKey(os.Getenv("ZEP_API_KEY")),
    )

    results, err := client.Graph.Search(ctx, &zep.GraphSearchQuery{
        UserID: zep.String("some user_id"),
        Query:  "Some search query",
        Scope:  zep.GraphSearchScopeEdges.Ptr(),
    })
    if err != nil {
        log.Fatalf("Error: %v", err)
    }
```

## Build the fact list

Then, we get the edges from the search results and construct our fact list. We also include the temporal validity data to each fact string:

```python
# Build list of formatted facts
relevant_edges = results.edges
formatted_facts = []
for edge in relevant_edges:
    valid_at = edge.valid_at if edge.valid_at is not None else "date unknown"
    invalid_at = edge.invalid_at if edge.invalid_at is not None else "present"
    formatted_fact = f"{edge.fact} (Date range: {valid_at} - {invalid_at})"
    formatted_facts.append(formatted_fact)

# Print the results
print("\nFound facts:")
for fact in formatted_facts:
    print(f"- {fact}")
```

```typescript
// Build list of formatted facts
const relevantEdges = results.edges || [];
const formattedFacts: string[] = [];

for (const edge of relevantEdges) {
    const validAt = edge.validAt ?? "date unknown";
    const invalidAt = edge.invalidAt ?? "present";
    const formattedFact = `${edge.fact} (Date range: ${validAt} - ${invalidAt})`;
    formattedFacts.push(formattedFact);
}

// Print the results
console.log("\nFound facts:");
for (const fact of formattedFacts) {
    console.log(`- ${fact}`);
}
```

```go
    // Build list of formatted facts
    relevantEdges := results.Edges
    var formattedFacts []string

    for _, edge := range relevantEdges {
        validAt := "date unknown"
        if edge.ValidAt != nil {
            validAt = *edge.ValidAt
        }

        invalidAt := "present"
        if edge.InvalidAt != nil {
            invalidAt = *edge.InvalidAt
        }

        formattedFact := fmt.Sprintf("%s (Date range: %s - %s)", edge.Fact, validAt, invalidAt)
        formattedFacts = append(formattedFacts, formattedFact)
    }

    // Print the results
    fmt.Println("\nFound facts:")
    for _, fact := range formattedFacts {
        fmt.Printf("- %s\n", fact)
    }
}
```

## Summary

We demonstrated how to retrieve the most relevant facts for an arbitrary query using the Zep client. Adjust the query and parameters as needed to tailor the search for your specific use case.