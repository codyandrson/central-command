> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Cloning Graphs

## Overview

The `graph.clone` method allows you to create complete copies of graphs with new identifiers. This is useful for scenarios like creating test copies of user data, migrating user graphs to new identifiers, or setting up template graphs for new users.

The clone operation returns a `task_id` that can be used to track the cloning progress. See the [Check Data Ingestion Status](/cookbook/check-data-ingestion-status#checking-operation-status-with-task-polling) recipe for how to poll task status.

The target graph must not exist - they will be created as part of the cloning operation. If no target ID is provided, one will be auto-generated and returned in the response.

## Clone a Graph

#### User Graph

```python Python
from zep_cloud.client import Zep

client = Zep(
    api_key=API_KEY,
)

# Clone a user graph to a new user ID
result = client.graph.clone(
    source_user_id="user_123",
    target_user_id="user_123_copy"  # Optional - will be auto-generated if not provided
)

print(f"Cloned graph to user: {result.user_id}")
print(f"Clone task ID: {result.task_id}")
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({
  apiKey: API_KEY,
});

// Clone a user graph to a new user ID
const result = await client.graph.clone({
    sourceUserId: "user_123",
    targetUserId: "user_123_copy"  // Optional - will be auto-generated if not provided
});

console.log(`Cloned graph to user: ${result.userId}`);
console.log(`Clone task ID: ${result.taskId}`);
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

// Clone a user graph to a new user ID
sourceUserID := "user_123"
targetUserID := "user_123_copy"  // Optional - will be auto-generated if not provided

result, err := client.Graph.Clone(context.TODO(), &v3.CloneGraphRequest{
    SourceUserID: &sourceUserID,
    TargetUserID: &targetUserID,
})
if err != nil {
    log.Fatalf("Failed to clone graph: %v", err)
}

fmt.Printf("Cloned graph to user: %s\n", *result.UserID)
fmt.Printf("Clone task ID: %s\n", *result.TaskID)
```

#### Standalone Graph

```python Python
from zep_cloud.client import Zep

client = Zep(
    api_key=API_KEY,
)

# Clone a graph to a new graph ID
result = client.graph.clone(
    source_graph_id="graph_456",
    target_graph_id="graph_456_copy"  # Optional - will be auto-generated if not provided
)

print(f"Cloned graph to graph: {result.graph_id}")
print(f"Clone task ID: {result.task_id}")
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({
  apiKey: API_KEY,
});

// Clone a graph to a new graph ID
const result = await client.graph.clone({
    sourceGraphId: "graph_456",
    targetGraphId: "graph_456_copy"  // Optional - will be auto-generated if not provided
});

console.log(`Cloned graph to graph: ${result.graphId}`);
console.log(`Clone task ID: ${result.taskId}`);
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

// Clone a graph to a new graph ID
sourceGraphID := "graph_456"
targetGraphID := "graph_456_copy"  // Optional - will be auto-generated if not provided

result, err := client.Graph.Clone(context.TODO(), &v3.CloneGraphRequest{
    SourceGraphID: &sourceGraphID,
    TargetGraphID: &targetGraphID,
})
if err != nil {
    log.Fatalf("Failed to clone graph: %v", err)
}

fmt.Printf("Cloned graph to graph: %s\n", *result.GraphID)
fmt.Printf("Clone task ID: %s\n", *result.TaskID)
```

## Key Behaviors and Limitations

* **Target Requirements**: The target user or graph must not exist and will be created during the cloning operation
* **Auto-generation**: If no target ID is provided, Zep will auto-generate one and return it in the response
* **Node Modification**: The central user entity node in the cloned graph is updated with the new user ID, and all references in the node summary are updated accordingly