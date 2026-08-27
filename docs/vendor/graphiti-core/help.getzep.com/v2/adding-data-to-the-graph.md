> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Adding Data to the Graph

## Overview

Requests to add data to the same graph are completed sequentially to ensure the graph is built correctly, and processing may be slow for large datasets. Use [batch ingestion](#add-batch-data) when adding large datasets such as backfills or document collections.

In addition to incorporating memory through chat history, Zep offers the capability to add data directly to the graph.
Zep supports three distinct data types: message, text, and JSON.

The message type is ideal for adding data in the form of chat messages that are not directly associated with a Zep [Session's](/sessions) chat history. This encompasses any communication with a designated speaker, such as emails or previous chat logs.

The text type is designed for raw text data without a specific speaker attribution. This category includes content from internal documents, wiki articles, or company handbooks. It's important to note that Zep does not process text directly from links or files.

The JSON type may be used to add any JSON document to Zep. This may include REST API responses or JSON-formatted business data.

You can add data to either a user graph by providing a `user_id`, or to a [group graph](/groups) by specifying a `group_id`.

## Adding Message Data

Here's an example demonstrating how to add message data to the graph:

```python Python
from zep_cloud.client import Zep

client = Zep(
    api_key=API_KEY,
)

message = "Paul (user): I went to Eric Clapton concert last night"

new_episode = client.graph.add(
    user_id="user123",    # Optional user ID
    type="message",       # Specify type as "message"
    data=message
)
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({
  apiKey: API_KEY,
});

const message = "User: I really enjoy working with TypeScript and React";

const newEpisode = await client.graph.add({
    userId: "user123",
    type: "message",
    data: message
});
```

## Adding Text Data

Here's an example demonstrating how to add text data to the graph:

```python Python
from zep_cloud.client import Zep

client = Zep(
    api_key=API_KEY,
)

new_episode = client.graph.add(
    user_id="user123",  # Optional user ID
    type="text",        # Specify type as "text" 
    data="The user is an avid fan of Eric Clapton"
)
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({
  apiKey: API_KEY,
});

const newEpisode = await client.graph.add({
    userId: "user123",  // Required: either userId or groupId
    type: "text",
    data: "The user is interested in machine learning and artificial intelligence"
});
```

## Adding JSON Data

Here's an example demonstrating how to add JSON data to the graph:

```python Python
from zep_cloud.client import Zep
import json

client = Zep(
    api_key=API_KEY,
)

json_data = {"name": "Eric Clapton", "age": 78, "genre": "Rock"}
json_string = json.dumps(json_data)
new_episode = client.graph.add(
    user_id=user_id,
    type="json",
    data=json_string,
)
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({
  apiKey: API_KEY,
});

const jsonString = '{"name": "Eric Clapton", "age": 78, "genre": "Rock"}';
const newEpisode = await client.graph.add({
    userId: userId,
    type: "json",
    data: jsonString,
});
```

## Data Size Limit and Chunking

The `graph.add` endpoint has a data size limit of 10,000 characters when adding data to the graph. If you need to add a document which is more than 10,000 characters, we recommend chunking the document as well as using Anthropic's contextualized retrieval technique. We have an example of this [here](https://blog.getzep.com/building-a-russian-election-interference-knowledge-graph/#:~:text=Chunking%20articles%20into%20multiple%20Episodes%20improved%20our%20results%20compared%20to%20treating%20each%20article%20as%20a%20single%20Episode.%20This%20approach%20generated%20more%20detailed%20knowledge%20graphs%20with%20richer%20node%20and%20edge%20extraction%2C%20while%20single%2DEpisode%20processing%20produced%20only%20high%2Dlevel%2C%20sparse%20graphs.). This example uses Graphiti, but the same patterns apply to Zep as well.

Additionally, we recommend using relatively small chunk sizes, so that Zep is able to capture all of the entities and relationships within a chunk. Using a larger chunk size may result in some entities and relationships not being captured.

## Adding Custom Fact/Node Triplets

You can also add manually specified fact/node triplets to the graph. You need only specify the fact, the target node name, and the source node name. Zep will then create a new corresponding edge and nodes, or use an existing edge/nodes if they exist and seem to represent the same nodes or edge you send as input. And if this new fact invalidates an existing fact, it will mark the existing fact as invalid and add the new fact triplet.

```python Python
from zep_cloud.client import Zep

client = Zep(
    api_key=API_KEY,
)

client.graph.add_fact_triple(
    user_id=user_id,
    fact="Paul met Eric",
    fact_name="MET",
    target_node_name="Eric Clapton",
    source_node_name="Paul",
)
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({
  apiKey: API_KEY,
});

await client.graph.addFactTriple({
  userId: userId,
  fact: "Paul met Eric",
  factName: "MET",
  targetNodeName: "Eric Clapton",
  sourceNodeName: "Paul",
});
```

You can also specify the node summaries, edge temporal data, and UUIDs. See the [associated SDK reference](/sdk-reference/graph/add-fact-triple).

## Add Batch Data

The batch add method is designed for efficiently adding a large amount of data to a user or group graph concurrently. This method is suitable when the data does not have a significant temporal dimension, such as static documents or bulk imports.

Normally, data added to the graph is processed sequentially. This allows the knowledge graph to capture temporal relationships and understand when one event occurs after another. When using the batch add method, each episode is processed concurrently. As a result, temporal relationships between episodes are not captured.

The batch add method is best used for static data where the order of events is not important. It is not recommended for evolving chat histories or scenarios where temporal relationships are meaningful.

You can only batch up to 20 episodes at a time. You can group episodes of different types (text, json, message) in the same batch.

See the [SDK reference](/sdk-reference/graph/add-batch) for details.

```python Python
from zep_cloud.client import Zep
from zep_cloud import EpisodeData
import json

client = Zep(
    api_key=API_KEY,
)

episodes = [
    EpisodeData(
        data="This is an example text episode.",
        type="text"
    ),
    EpisodeData(
        data=json.dumps({"name": "Eric Clapton", "age": 78, "genre": "Rock"}),
        type="json"
    )
]

client.graph.add_batch(episodes=episodes, group_id=group_id)
```

```typescript TypeScript
const episodes: EpisodeData[] = [
    {
        data: "This is an example text episode.",
        type: "text"
    },
    {
        data: JSON.stringify({ foo: "bar", count: 42 }),
        type: "json"
    }
];
await client.graph.addBatch({ groupId, episodes });
```

```go Go
jsonData, _ := json.Marshal(map[string]interface{}{
    "foo": "bar",
    "baz": 42,
})

batchReq := &v2.AddDataBatchRequest{
    Episodes: []*v2.EpisodeData{
        {
            Data: "This is a text episode.",
            Type: v2.GraphDataTypeText,
        },
        {
            Data: string(jsonData),
            Type: v2.GraphDataTypeJSON,
        },
    },
    GroupID: &groupID,
}

resp, err := client.Graph.AddBatch(context.TODO(), batchReq)
if err != nil {
    log.Fatalf("Failed to add batch episodes: %v", err)
}
```

## Cloning Graphs

The `graph.clone` method allows you to create complete copies of user or group graphs with new identifiers. This is useful for scenarios like creating test copies of user data, migrating user graphs to new identifiers, or setting up template graphs for new users.

The cloning process does not copy fact ratings from the original graph. Fact ratings will not be present in the cloned graph.

The target user or group must not exist - they will be created as part of the cloning operation. If no target ID is provided, one will be auto-generated and returned in the response.

### Cloning User Graphs

Here's an example demonstrating how to clone a user graph:

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
```

```go Go
import (
    "context"
    "fmt"
    "log"
    
    "github.com/getzep/zep-go/v2"
    zepclient "github.com/getzep/zep-go/v2/client"
    "github.com/getzep/zep-go/v2/option"
)

client := zepclient.NewClient(
    option.WithAPIKey(apiKey),
)

// Clone a user graph to a new user ID
sourceUserID := "user_123"
targetUserID := "user_123_copy"  // Optional - will be auto-generated if not provided

result, err := client.Graph.Clone(context.TODO(), &v2.CloneGraphRequest{
    SourceUserID: &sourceUserID,
    TargetUserID: &targetUserID,
})
if err != nil {
    log.Fatalf("Failed to clone graph: %v", err)
}

fmt.Printf("Cloned graph to user: %s\n", *result.UserID)
```

### Cloning Group Graphs

You can also clone group graphs using the same method:

```python Python
from zep_cloud.client import Zep

client = Zep(
    api_key=API_KEY,
)

# Clone a group graph to a new group ID
result = client.graph.clone(
    source_group_id="group_456",
    target_group_id="group_456_copy"  # Optional - will be auto-generated if not provided
)

print(f"Cloned graph to group: {result.group_id}")
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({
  apiKey: API_KEY,
});

// Clone a group graph to a new group ID
const result = await client.graph.clone({
    sourceGroupId: "group_456",
    targetGroupId: "group_456_copy"  // Optional - will be auto-generated if not provided
});

console.log(`Cloned graph to group: ${result.groupId}`);
```

```go Go
import (
    "context"
    "fmt"
    "log"
    
    "github.com/getzep/zep-go/v2"
    zepclient "github.com/getzep/zep-go/v2/client"
    "github.com/getzep/zep-go/v2/option"
)

client := zepclient.NewClient(
    option.WithAPIKey(apiKey),
)

// Clone a group graph to a new group ID
sourceGroupID := "group_456"
targetGroupID := "group_456_copy"  // Optional - will be auto-generated if not provided

result, err := client.Graph.Clone(context.TODO(), &v2.CloneGraphRequest{
    SourceGroupID: &sourceGroupID,
    TargetGroupID: &targetGroupID,
})
if err != nil {
    log.Fatalf("Failed to clone graph: %v", err)
}

fmt.Printf("Cloned graph to group: %s\n", *result.GroupID)
```

### Key Behaviors and Limitations

* **Target Requirements**: The target user or group must not exist and will be created during the cloning operation
* **Auto-generation**: If no target ID is provided, Zep will auto-generate one and return it in the response
* **Node Modification**: The central user entity node in the cloned graph is updated with the new user ID, and all references in the node summary are updated accordingly

## Managing Your Data on the Graph

The `graph.add` method returns the [episode](/graphiti/graphiti/adding-episodes) that was created in the graph from adding that data. You can use this to maintain a mapping between your data and its corresponding episode in the graph and to delete specific data from the graph using the [delete episode](/deleting-data-from-the-graph#delete-an-episode) method.