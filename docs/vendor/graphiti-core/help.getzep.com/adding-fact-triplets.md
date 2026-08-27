> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Manually Updating the Graph

## Overview

Zep provides several ways to manually change a graph:

* Use `graph.add_nodes` to add one or more nodes without creating relationships.
* Use `graph.add_fact_triple` to add a relationship and its source and target nodes together.
* Use `graph.node.update` to update an existing node by UUID.
* Use `graph.edge.update` to update an existing edge by UUID.

#### [Seed canonical graph data with zep-ingest](/zep-ingest#build-the-pipeline-step-by-step)

Use `ingest_nodes` and `ingest_fact_triples` to seed known entities and relationships before ingesting source material.

## Adding Nodes

Use `add_nodes` when you already have structured entity data and do not need Zep to extract nodes from an episode or create relationships between them. Zep stores the node contents you provide and derives the search representation from each node's name.

The same method handles both single-node and batch creation. Each request must contain between 1 and 100 nodes and target one `user_id` or `graph_id`.

### Adding a Single Node

Pass one item in the `nodes` list to add a single node:

```python Python
from zep_cloud import AddNodeItem
from zep_cloud.client import Zep

client = Zep(api_key=API_KEY)

result = client.graph.add_nodes(
    user_id=user_id,  # Optional: You can use graph_id instead of user_id
    nodes=[
        AddNodeItem(
            name="Alice Johnson",
            summary="An engineer on the platform team.",
            label="Person",
            attributes={"role": "Engineer", "active": True},
            metadata={"source": "crm"},
        )
    ],
)

# UUIDs are assigned before the asynchronous task begins.
node_uuid = result.nodes[0].uuid_
task_id = result.task_id
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({ apiKey: API_KEY });

const result = await client.graph.addNodes({
  userId: userId, // Optional: You can use graphId instead of userId
  nodes: [
    {
      name: "Alice Johnson",
      summary: "An engineer on the platform team.",
      label: "Person",
      attributes: { role: "Engineer", active: true },
      metadata: { source: "crm" },
    },
  ],
});

// UUIDs are assigned before the asynchronous task begins.
const nodeUuid = result.nodes?.[0]?.uuid;
const taskId = result.taskId;
```

```go Go
import (
    "context"
    "log"

    "github.com/getzep/zep-go/v3"
    zepclient "github.com/getzep/zep-go/v3/client"
    "github.com/getzep/zep-go/v3/option"
)

client := zepclient.NewClient(option.WithAPIKey(apiKey))

userID := "user123"
summary := "An engineer on the platform team."
label := "Person"

result, err := client.Graph.AddNodes(context.TODO(), &zep.AddNodesRequest{
    UserID: &userID, // Optional: You can use GraphID instead of UserID
    Nodes: []*zep.AddNodeItem{
        {
            Name:       "Alice Johnson",
            Summary:    &summary,
            Label:      &label,
            Attributes: map[string]interface{}{"role": "Engineer", "active": true},
            Metadata:   map[string]interface{}{"source": "crm"},
        },
    },
})
if err != nil {
    log.Fatalf("Failed to add node: %v", err)
}

// UUIDs are assigned before the asynchronous task begins.
if len(result.Nodes) == 0 || result.Nodes[0].UUID == nil || result.TaskID == nil {
    log.Fatal("The add node response did not include the expected UUID and task ID")
}
log.Printf("Node UUID: %s", *result.Nodes[0].UUID)
log.Printf("Task ID: %s", *result.TaskID)
```

### Adding Nodes in a Batch

Include multiple items in the same `nodes` list to create up to 100 nodes in one request. The nodes may use different entity types, but they must all belong to the same user or graph.

```python Python
from zep_cloud import AddNodeItem
from zep_cloud.client import Zep

client = Zep(api_key=API_KEY)

result = client.graph.add_nodes(
    graph_id=graph_id,  # Optional: You can use user_id instead of graph_id
    nodes=[
        AddNodeItem(
            name="Acme Corporation",
            summary="An enterprise software company.",
            label="Organization",
            attributes={"tier": "enterprise"},
        ),
        AddNodeItem(
            name="Alice Johnson",
            label="Person",
            attributes={"role": "Engineer"},
        ),
    ],
)

node_uuids = [node.uuid_ for node in result.nodes]
task_id = result.task_id
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({ apiKey: API_KEY });

const result = await client.graph.addNodes({
  graphId: graphId, // Optional: You can use userId instead of graphId
  nodes: [
    {
      name: "Acme Corporation",
      summary: "An enterprise software company.",
      label: "Organization",
      attributes: { tier: "enterprise" },
    },
    {
      name: "Alice Johnson",
      label: "Person",
      attributes: { role: "Engineer" },
    },
  ],
});

const nodeUuids = result.nodes?.map((node) => node.uuid);
const taskId = result.taskId;
```

```go Go
import (
    "context"
    "log"

    "github.com/getzep/zep-go/v3"
    zepclient "github.com/getzep/zep-go/v3/client"
    "github.com/getzep/zep-go/v3/option"
)

client := zepclient.NewClient(option.WithAPIKey(apiKey))

graphID := "crm-accounts"
organizationSummary := "An enterprise software company."
organizationLabel := "Organization"
personLabel := "Person"

result, err := client.Graph.AddNodes(context.TODO(), &zep.AddNodesRequest{
    GraphID: &graphID, // Optional: You can use UserID instead of GraphID
    Nodes: []*zep.AddNodeItem{
        {
            Name:       "Acme Corporation",
            Summary:    &organizationSummary,
            Label:      &organizationLabel,
            Attributes: map[string]interface{}{"tier": "enterprise"},
        },
        {
            Name:       "Alice Johnson",
            Label:      &personLabel,
            Attributes: map[string]interface{}{"role": "Engineer"},
        },
    },
})
if err != nil {
    log.Fatalf("Failed to add nodes: %v", err)
}

nodeUUIDs := make([]string, 0, len(result.Nodes))
for _, node := range result.Nodes {
    if node.UUID == nil {
        log.Fatal("The add nodes response included a node without a UUID")
    }
    nodeUUIDs = append(nodeUUIDs, *node.UUID)
}
if result.TaskID == nil {
    log.Fatal("The add nodes response did not include a task ID")
}
log.Printf("Node UUIDs: %v", nodeUUIDs)
log.Printf("Task ID: %s", *result.TaskID)
```

### Node Creation Behavior

`add_nodes` returns a `task_id` and the accepted nodes immediately. Each returned node includes the UUID Zep assigned to it, but the node is not available to read or search until the asynchronous task succeeds. See [Check Data Ingestion Status](/cookbook/check-data-ingestion-status#checking-operation-status-with-task-polling) for how to poll the task.

Zep assigns node UUIDs. You cannot supply one: a request that includes a node `uuid` is rejected with a `400`. Every accepted node is a new node, and nodes are not deduplicated by name or content, so two identical requests create two sets of nodes. Keep the UUIDs from the response if you need to reference the nodes later.

To change a node after it is created — rename it, replace its summary, edit attributes, or clear its entity type — use `graph.node.update` with a UUID from the `add_nodes` response.

Each node supports the following fields:

| Field        | Required | Description                                                                                                                                                                  |
| ------------ | -------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `name`       | Yes      | Node name, up to 50 characters.                                                                                                                                              |
| `summary`    | No       | Node summary, up to 500 characters.                                                                                                                                          |
| `label`      | No       | A single entity type, up to 100 characters. The base `Entity` label is implicit. If the label matches an ontology type with properties, attributes are validated against it. |
| `attributes` | No       | Up to 10 custom attributes. When `label` matches an ontology type with properties, the attributes are validated against it.                                                  |
| `metadata`   | No       | Up to 10 metadata fields attached to the node's provenance episode.                                                                                                          |
| `created_at` | No       | RFC 3339 creation time for the node. Defaults to the request time.                                                                                                           |

## Updating a Node

Use `graph.node.update` to update an existing node by UUID. Only the fields you provide are changed, and the updated node is returned synchronously.

```python Python
from zep_cloud.client import Zep

client = Zep(api_key=API_KEY)

updated_node = client.graph.node.update(
    uuid_=node_uuid,
    name="Alice Smith",
    summary="A principal engineer on the platform team.",
    labels=["Person"],
    attributes={
        "role": "Principal Engineer",
        "legacy_field": None,  # Deletes this attribute
    },
)
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({ apiKey: API_KEY });

const updatedNode = await client.graph.node.update(nodeUuid, {
  name: "Alice Smith",
  summary: "A principal engineer on the platform team.",
  labels: ["Person"],
  attributes: {
    role: "Principal Engineer",
    legacy_field: null, // Deletes this attribute
  },
});
```

```go Go
import (
    "context"
    "log"

    zepclient "github.com/getzep/zep-go/v3/client"
    graph "github.com/getzep/zep-go/v3/graph"
    "github.com/getzep/zep-go/v3/option"
)

client := zepclient.NewClient(option.WithAPIKey(apiKey))

name := "Alice Smith"
summary := "A principal engineer on the platform team."

updatedNode, err := client.Graph.Node.Update(
    context.TODO(),
    nodeUUID,
    &graph.UpdateNodeRequest{
        Name:    &name,
        Summary: &summary,
        Labels:  []string{"Person"},
        Attributes: map[string]interface{}{
            "role":         "Principal Engineer",
            "legacy_field": nil, // Deletes this attribute
        },
    },
)
if err != nil {
    log.Fatalf("Failed to update node: %v", err)
}
log.Printf("Updated node: %s", updatedNode.UUID)
```

Node attributes are merged with the existing attributes. Set an attribute to `null` to delete it. If you provide `labels`, the list replaces the existing labels. Python and TypeScript callers can use an empty list to clear the node's entity type.

The current Go SDK omits an empty `Labels` slice from the request. To clear a node's entity type from Go, send `{"labels": []}` directly to `PATCH /graph/node/{uuid}`.

## Updating an Edge

Use `graph.edge.update` to update an existing entity edge by UUID. You can change its relationship name, fact, temporal fields, and attributes. The source and target node UUIDs cannot be changed.

```python Python
from zep_cloud.client import Zep

client = Zep(api_key=API_KEY)

updated_edge = client.graph.edge.update(
    uuid_=edge_uuid,
    name="WORKS_AT",
    fact="Alice Smith is a principal engineer at Acme Corporation",
    valid_at="2025-01-15T00:00:00Z",
    attributes={
        "role": "Principal Engineer",
        "legacy_field": None,  # Deletes this attribute
    },
)
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({ apiKey: API_KEY });

const updatedEdge = await client.graph.edge.update(edgeUuid, {
  name: "WORKS_AT",
  fact: "Alice Smith is a principal engineer at Acme Corporation",
  validAt: "2025-01-15T00:00:00Z",
  attributes: {
    role: "Principal Engineer",
    legacy_field: null, // Deletes this attribute
  },
});
```

```go Go
import (
    "context"
    "log"

    zepclient "github.com/getzep/zep-go/v3/client"
    graph "github.com/getzep/zep-go/v3/graph"
    "github.com/getzep/zep-go/v3/option"
)

client := zepclient.NewClient(option.WithAPIKey(apiKey))

name := "WORKS_AT"
fact := "Alice Smith is a principal engineer at Acme Corporation"
validAt := "2025-01-15T00:00:00Z"

updatedEdge, err := client.Graph.Edge.Update(
    context.TODO(),
    edgeUUID,
    &graph.UpdateEdgeRequest{
        Name:    &name,
        Fact:    &fact,
        ValidAt: &validAt,
        Attributes: map[string]interface{}{
            "role":         "Principal Engineer",
            "legacy_field": nil, // Deletes this attribute
        },
    },
)
if err != nil {
    log.Fatalf("Failed to update edge: %v", err)
}
log.Printf("Updated edge: %s", updatedEdge.UUID)
```

Edge attributes are merged with the existing attributes. Set an attribute to `null` to delete it. Temporal fields use RFC 3339 / ISO 8601 timestamps.

## Adding a Fact Triplet

You can add manually specified fact/node triplets to the graph. Provide the fact, a relationship name, and the source and target node names or UUIDs. Zep will then create a new corresponding edge and nodes, or use existing nodes and an edge if they represent the same entities and relationship. If the new fact invalidates an existing fact, Zep marks the existing fact as invalid and adds the new fact triplet.

The `add_fact_triple` method returns a `task_id` that can be used to track the processing status of the operation. See the [Check Data Ingestion Status](/cookbook/check-data-ingestion-status#checking-operation-status-with-task-polling) recipe for how to poll task status.

```python Python
from zep_cloud.client import Zep

client = Zep(
    api_key=API_KEY,
)

result = client.graph.add_fact_triple(
    user_id=user_id,  # Optional: You can use graph_id instead of user_id
    fact="Paul met Eric",
    fact_name="MET",
    target_node_name="Eric Clapton",
    source_node_name="Paul",
)

# The result includes a task_id for tracking processing status
task_id = result.task_id
print(f"Fact triple task ID: {task_id}")
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({
  apiKey: API_KEY,
});

const result = await client.graph.addFactTriple({
  userId: userId,  // Optional: You can use graphId instead of userId
  fact: "Paul met Eric",
  factName: "MET",
  targetNodeName: "Eric Clapton",
  sourceNodeName: "Paul",
});

// The result includes a task_id for tracking processing status
const taskId = result.taskId;
console.log(`Fact triple task ID: ${taskId}`);
```

```go Go
import (
    "context"
    "log"

    "github.com/getzep/zep-go/v3"
    zepclient "github.com/getzep/zep-go/v3/client"
    "github.com/getzep/zep-go/v3/option"
)

client := zepclient.NewClient(
    option.WithAPIKey(apiKey),
)

userID := "user123"
sourceNodeName := "Paul"
targetNodeName := "Eric Clapton"

result, err := client.Graph.AddFactTriple(context.TODO(), &zep.AddTripleRequest{
    UserID:         &userID,  // Optional: You can use GraphID instead of UserID
    Fact:           "Paul met Eric",
    FactName:       "MET",
    TargetNodeName: &targetNodeName,
    SourceNodeName: &sourceNodeName,
})
if err != nil {
    log.Fatalf("Failed to add fact triple: %v", err)
}

// The result includes a task_id for tracking processing status
if result.TaskID == nil {
    log.Fatal("The add fact triple response did not include a task ID")
}
log.Printf("Fact triple task ID: %s", *result.TaskID)
```

### Retrieving Created UUIDs

Because fact triple creation is asynchronous, the UUIDs for the edge and nodes are not returned immediately. After the task completes, you can retrieve them by calling `client.task.get()` with the `task_id` returned from `add_fact_triple`. The task's `params` field will contain `edge_uuid`, `source_node_uuid`, and `target_node_uuid`.

```python Python
import time

from zep_cloud.client import Zep

client = Zep(api_key=API_KEY)

# Add a fact triple
result = client.graph.add_fact_triple(
    user_id=user_id,
    fact="Paul met Eric",
    fact_name="MET",
    target_node_name="Eric Clapton",
    source_node_name="Paul",
)

task_id = result.task_id
if task_id is None:
    raise RuntimeError("The add fact triple response did not include a task ID")

# Poll until the task completes
while True:
    task = client.task.get(task_id=task_id)

    if task.status == "succeeded":
        break
    elif task.status == "failed":
        raise Exception(f"Task failed: {task.error}")

    time.sleep(10)

# Retrieve the created UUIDs from task.params
params = task.params or {}
edge_uuid = params.get("edge_uuid")
source_node_uuid = params.get("source_node_uuid")
target_node_uuid = params.get("target_node_uuid")

print(f"Edge UUID: {edge_uuid}")
print(f"Source Node UUID: {source_node_uuid}")
print(f"Target Node UUID: {target_node_uuid}")
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({ apiKey: API_KEY });

// Add a fact triple
const result = await client.graph.addFactTriple({
  userId: userId,
  fact: "Paul met Eric",
  factName: "MET",
  targetNodeName: "Eric Clapton",
  sourceNodeName: "Paul",
});

const taskId = result.taskId;
if (!taskId) {
  throw new Error("The add fact triple response did not include a task ID");
}

// Poll until the task completes
const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

let task;
while (true) {
  task = await client.task.get(taskId);

  if (task.status === "succeeded") {
    break;
  } else if (task.status === "failed") {
    throw new Error(`Task failed: ${task.error?.message ?? "Unknown error"}`);
  }

  await sleep(10000);
}

// Retrieve the created UUIDs from task.params
const edgeUuid = task.params?.["edge_uuid"];
const sourceNodeUuid = task.params?.["source_node_uuid"];
const targetNodeUuid = task.params?.["target_node_uuid"];

console.log(`Edge UUID: ${edgeUuid}`);
console.log(`Source Node UUID: ${sourceNodeUuid}`);
console.log(`Target Node UUID: ${targetNodeUuid}`);
```

```go Go
import (
    "context"
    "fmt"
    "log"
    "time"

    "github.com/getzep/zep-go/v3"
    zepclient "github.com/getzep/zep-go/v3/client"
    "github.com/getzep/zep-go/v3/option"
)

client := zepclient.NewClient(
    option.WithAPIKey(apiKey),
)

userID := "user123"
sourceNodeName := "Paul"
targetNodeName := "Eric Clapton"

// Add a fact triple
result, err := client.Graph.AddFactTriple(context.TODO(), &zep.AddTripleRequest{
    UserID:         &userID,
    Fact:           "Paul met Eric",
    FactName:       "MET",
    TargetNodeName: &targetNodeName,
    SourceNodeName: &sourceNodeName,
})
if err != nil {
    log.Fatalf("Failed to add fact triple: %v", err)
}

if result.TaskID == nil {
    log.Fatal("The add fact triple response did not include a task ID")
}
taskID := *result.TaskID

// Poll until the task completes
var task *zep.GetTaskResponse
for {
    task, err = client.Task.Get(context.TODO(), taskID)
    if err != nil {
        log.Fatalf("Failed to get task: %v", err)
    }

    if task.Status != nil && *task.Status == "succeeded" {
        break
    } else if task.Status != nil && *task.Status == "failed" {
        log.Fatalf("Task failed: %v", task.Error)
    }

    time.Sleep(10 * time.Second)
}

// Retrieve the created UUIDs from task.Params
edgeUUID := task.Params["edge_uuid"]
sourceNodeUUID := task.Params["source_node_uuid"]
targetNodeUUID := task.Params["target_node_uuid"]

fmt.Printf("Edge UUID: %v\n", edgeUUID)
fmt.Printf("Source Node UUID: %v\n", sourceNodeUUID)
fmt.Printf("Target Node UUID: %v\n", targetNodeUUID)
```

### Custom types and attributes

You can attach custom scalar attributes to nodes and edges, and optionally reference [custom entity and edge types](/customizing-graph-structure) from your ontology to enforce a schema.

* `source_node_labels` / `target_node_labels` — specify a single **entity type name**. When it matches an ontology type with properties, the node attributes are validated against it.
* `fact_name` — serves as both the relationship display name and the **edge type name** looked up in your ontology.
* `source_node_attributes` / `target_node_attributes` / `edge_attributes` — custom scalar properties on those nodes and edges, usable for [filtering in graph searches](/searching-the-graph#property-filtering).

**Attribute validation** — specifying types is optional. If a label or `fact_name` is not found in your ontology, attributes pass through without validation:

| Situation                                          | Result                                        |
| -------------------------------------------------- | --------------------------------------------- |
| Label not in ontology                              | All `node_attributes` pass through — no error |
| `fact_name` not in ontology                        | All `edge_attributes` pass through — no error |
| Label in ontology, matching type has no properties | All attributes pass through                   |
| Label in ontology and type HAS properties defined  | Attributes validated strictly                 |

When validation activates, every attribute key and value type must match the schema — otherwise the call returns HTTP 400.

```python Python
from zep_cloud.client import Zep

client = Zep(api_key=API_KEY)

# Assumes your ontology defines:
# - Entity type "Person" with properties: { "age": int, "role": text }
# - Edge type "WORKS_AT" with properties: { "start_year": int, "department": text }

result = client.graph.add_fact_triple(
    user_id=user_id,
    fact="Alice works at Acme Corp",
    fact_name="WORKS_AT",                     # matches edge type in ontology
    source_node_name="Alice",
    source_node_labels=["Person"],            # matches entity type "Person" in ontology
    source_node_attributes={
        "age": 30,                            # validated: must be int
        "role": "Engineer",                   # validated: must be text
    },
    target_node_name="Acme Corp",
    target_node_labels=["Organization"],      # not in ontology — passes through freely
    edge_attributes={
        "start_year": 2021,                   # validated against WORKS_AT schema
        "department": "Engineering",
    },
)
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({ apiKey: API_KEY });

// Assumes your ontology defines:
// - Entity type "Person" with properties: { "age": int, "role": text }
// - Edge type "WORKS_AT" with properties: { "start_year": int, "department": text }

const result = await client.graph.addFactTriple({
  userId: userId,
  fact: "Alice works at Acme Corp",
  factName: "WORKS_AT",                       // matches edge type in ontology
  sourceNodeName: "Alice",
  sourceNodeLabels: ["Person"],               // matches entity type "Person" in ontology
  sourceNodeAttributes: {
    age: 30,                                  // validated: must be int
    role: "Engineer",                         // validated: must be text
  },
  targetNodeName: "Acme Corp",
  targetNodeLabels: ["Organization"],         // not in ontology — passes through freely
  edgeAttributes: {
    start_year: 2021,                         // validated against WORKS_AT schema
    department: "Engineering",
  },
});
```

```go Go
import (
    "context"
    "log"

    "github.com/getzep/zep-go/v3"
    zepclient "github.com/getzep/zep-go/v3/client"
    "github.com/getzep/zep-go/v3/option"
)

client := zepclient.NewClient(
    option.WithAPIKey(apiKey),
)

userID := "user123"
sourceNodeName := "Alice"
targetNodeName := "Acme Corp"

// Assumes your ontology defines:
// - Entity type "Person" with properties: { "age": int, "role": text }
// - Edge type "WORKS_AT" with properties: { "start_year": int, "department": text }

result, err := client.Graph.AddFactTriple(context.TODO(), &zep.AddTripleRequest{
    UserID:           &userID,
    Fact:             "Alice works at Acme Corp",
    FactName:         "WORKS_AT",             // matches edge type in ontology
    SourceNodeName:   &sourceNodeName,
    SourceNodeLabels: []string{"Person"},     // matches entity type "Person" in ontology
    SourceNodeAttributes: map[string]interface{}{
        "age":  30,                           // validated: must be int
        "role": "Engineer",                   // validated: must be text
    },
    TargetNodeName:   &targetNodeName,
    TargetNodeLabels: []string{"Organization"}, // not in ontology — passes through freely
    EdgeAttributes: map[string]interface{}{
        "start_year":  2021,                  // validated against WORKS_AT schema
        "department":  "Engineering",
    },
})
if err != nil {
    log.Fatalf("Failed to add fact triple: %v", err)
}
if result.TaskID == nil {
    log.Fatal("The add fact triple response did not include a task ID")
}
log.Printf("Fact triple task ID: %s", *result.TaskID)
```

Attribute values must be scalar types: string, number, boolean, or null. Nested objects and arrays are not supported.

### Fact triplet metadata

You can attach metadata to a fact triple, which makes the resulting edge and nodes filterable via [episode metadata filters](/searching-the-graph#episode-metadata-filtering) in graph search. See [Episode metadata](/adding-business-data#episode-metadata) for full details on metadata constraints and update semantics.

Metadata values must be scalars (string, number, boolean, or null) or non-empty arrays of scalars. A maximum of 10 keys are allowed.

```python Python
from zep_cloud.client import Zep

client = Zep(api_key=API_KEY)

result = client.graph.add_fact_triple(
    user_id=user_id,
    fact="Alice works at Acme Corp",
    fact_name="WORKS_AT",
    source_node_name="Alice",
    target_node_name="Acme Corp",
    metadata={"source": "crm_import", "confidence": 0.95},
)
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({ apiKey: API_KEY });

const result = await client.graph.addFactTriple({
  userId: userId,
  fact: "Alice works at Acme Corp",
  factName: "WORKS_AT",
  sourceNodeName: "Alice",
  targetNodeName: "Acme Corp",
  metadata: { source: "crm_import", confidence: 0.95 },
});
```

```go Go
import (
    "context"
    "log"

    "github.com/getzep/zep-go/v3"
    zepclient "github.com/getzep/zep-go/v3/client"
    "github.com/getzep/zep-go/v3/option"
)

client := zepclient.NewClient(
    option.WithAPIKey(apiKey),
)

userID := "user123"
sourceNodeName := "Alice"
targetNodeName := "Acme Corp"

result, err := client.Graph.AddFactTriple(context.TODO(), &zep.AddTripleRequest{
    UserID:         &userID,
    Fact:           "Alice works at Acme Corp",
    FactName:       "WORKS_AT",
    SourceNodeName: &sourceNodeName,
    TargetNodeName: &targetNodeName,
    Metadata: map[string]interface{}{
        "source":     "crm_import",
        "confidence": 0.95,
    },
})
if err != nil {
    log.Fatalf("Failed to add fact triple: %v", err)
}
if result.TaskID == nil {
    log.Fatal("The add fact triple response did not include a task ID")
}
log.Printf("Fact triple task ID: %s", *result.TaskID)
```

### Specifying Node UUIDs

You can optionally specify `source_node_uuid` and/or `target_node_uuid` to control which nodes are used. The behavior depends on whether you provide a UUID:

| Scenario                                 | Behavior                                                                                                     |
| ---------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| **UUID provided and node exists**        | Uses the existing node with that UUID                                                                        |
| **UUID provided but node doesn't exist** | Returns a 404 error. You must omit the UUID to create a new node.                                            |
| **UUID omitted**                         | Searches for an existing node by name. If no match is found, creates a new node with an auto-generated UUID. |

See the [associated SDK reference](/sdk-reference/graph/add-fact-triple) for complete details on all available parameters.

### Field Limits

| Field                                                | Limit                                                     |
| ---------------------------------------------------- | --------------------------------------------------------- |
| `fact`                                               | Maximum 250 characters                                    |
| `fact_name`                                          | Maximum 50 characters; must be `SCREAMING_SNAKE_CASE`     |
| `source_node_name`, `target_node_name`               | Maximum 50 characters each                                |
| `source_node_summary`, `target_node_summary`         | Maximum 500 characters each                               |
| `created_at`, `valid_at`, `invalid_at`, `expired_at` | RFC 3339 / ISO 8601 format (e.g., `2024-01-15T10:30:00Z`) |