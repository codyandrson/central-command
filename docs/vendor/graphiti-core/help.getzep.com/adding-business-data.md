> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Adding Business Data

## Overview

Requests to add data to the same graph are completed sequentially to ensure the graph is built correctly, and processing may be slow for large datasets. For large historical datasets, use [batch ingestion](/adding-batch-data).

#### [Backfilling from files on disk? Use an ingestion pipeline](/zep-ingest)

[`zep-ingest`](/zep-ingest) is the recommended path for backfills and recurring exports that land files on disk. It adds preparation, ordering, preview, and monitoring on top of the `graph.add` calls described below. For individual writes or data your application already holds in memory (webhook payloads, API responses), call `graph.add` directly.

In addition to persisting chat history to Zep's Context Graph, Zep offers the capability to add data directly to the graph.
Zep supports three distinct data types: message, text, and JSON.

The message type is ideal for adding data in the form of chat messages that are not directly associated with a Zep [Thread's](/threads) chat history. This encompasses any communication with a designated speaker, such as emails or previous chat logs.

The text type is designed for raw text data without a specific speaker attribution. This category includes content from internal documents, wiki articles, or company handbooks. It's important to note that Zep does not process text directly from links or files.

The JSON type may be used to add any JSON document to Zep. This may include REST API responses or JSON-formatted business data.

You can add data to a graph by specifying a `graph_id`, or to a user graph by providing a `user_id`.

## Adding Message Data

Here's an example demonstrating how to add message data to the graph:

```python Python
from zep_cloud.client import Zep

client = Zep(
    api_key=API_KEY,
)

message = "Paul (user): I went to Eric Clapton concert last night"

new_episode = client.graph.add(
    user_id="user123",    # Optional: You can use graph_id instead of user_id
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
    userId: "user123",  // Optional: You can use graphId instead of userId
    type: "message",
    data: message
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

message := "Paul (user): I went to Eric Clapton concert last night"
userID := "user123"

newEpisode, err := client.Graph.Add(context.TODO(), &zep.AddDataRequest{
    UserID: &userID,  // Optional: You can use GraphID instead of UserID
    Type:   zep.GraphDataTypeMessage,
    Data:   message,
})
if err != nil {
    log.Fatalf("Failed to add message data: %v", err)
}
```

## Adding Text Data

Here's an example demonstrating how to add text data to the graph:

```python Python
from zep_cloud.client import Zep

client = Zep(
    api_key=API_KEY,
)

new_episode = client.graph.add(
    user_id="user123",  # Optional: You can use graph_id instead of user_id
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
    userId: "user123",  // Optional: You can use graphId instead of userId
    type: "text",
    data: "The user is interested in machine learning and artificial intelligence"
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

newEpisode, err := client.Graph.Add(context.TODO(), &zep.AddDataRequest{
    UserID: &userID,  // Optional: You can use GraphID instead of UserID
    Type:   zep.GraphDataTypeText,
    Data:   "The user is an avid fan of Eric Clapton",
})
if err != nil {
    log.Fatalf("Failed to add text data: %v", err)
}
```

## Adding JSON Data

Before ingesting JSON, [name and contextualize each record](/prepare-data-for-ingestion#name-and-contextualize-json-records) so Zep builds an accurate graph from it.

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
    user_id=user_id,  # Optional: You can use graph_id instead of user_id
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
    userId: userId,  // Optional: You can use graphId instead of userId
    type: "json",
    data: jsonString,
});
```

```go Go
import (
    "context"
    "encoding/json"
    "log"

    "github.com/getzep/zep-go/v3"
    zepclient "github.com/getzep/zep-go/v3/client"
    "github.com/getzep/zep-go/v3/option"
)

client := zepclient.NewClient(
    option.WithAPIKey(apiKey),
)

jsonData := map[string]interface{}{
    "name":  "Eric Clapton",
    "age":   78,
    "genre": "Rock",
}
jsonBytes, err := json.Marshal(jsonData)
if err != nil {
    log.Fatalf("Failed to marshal JSON: %v", err)
}
jsonString := string(jsonBytes)

userID := "user123"

newEpisode, err := client.Graph.Add(context.TODO(), &zep.AddDataRequest{
    UserID: &userID,  // Optional: You can use GraphID instead of UserID
    Type:   zep.GraphDataTypeJSON,
    Data:   jsonString,
})
if err != nil {
    log.Fatalf("Failed to add JSON data: %v", err)
}
```

## Setting data timestamps

When adding data via the `graph.add` method, you can provide the `created_at` timestamp in RFC3339 format. The `created_at` timestamp represents the time when the data was originally created. For messages, this would be when the message was originally sent. For events represented as JSON, this would be when the event occurred. Setting the `created_at` timestamp ensures the user's Context Graph has accurate temporal understanding of user history (since this time is used in our fact invalidation process).

```python Python
from zep_cloud.client import Zep
import json

client = Zep(
    api_key=API_KEY,
)

# Example: Adding a JSON event with its original timestamp
event_data = {"event": "purchase", "item": "laptop", "amount": 1299.99}
json_string = json.dumps(event_data)

new_episode = client.graph.add(
    user_id="user123",
    type="json",
    data=json_string,
    created_at="2025-06-01T13:11:12Z"  # Time the event originally occurred
)
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({
  apiKey: API_KEY,
});

// Example: Adding a JSON event with its original timestamp
const eventData = JSON.stringify({ event: "purchase", item: "laptop", amount: 1299.99 });

const newEpisode = await client.graph.add({
    userId: "user123",
    type: "json",
    data: eventData,
    createdAt: "2025-06-01T13:11:12Z"  // Time the event originally occurred
});
```

```go Go
import (
    "context"
    "encoding/json"
    "log"

    "github.com/getzep/zep-go/v3"
    zepclient "github.com/getzep/zep-go/v3/client"
    "github.com/getzep/zep-go/v3/option"
)

client := zepclient.NewClient(
    option.WithAPIKey(apiKey),
)

// Example: Adding a JSON event with its original timestamp
eventData := map[string]interface{}{
    "event":  "purchase",
    "item":   "laptop",
    "amount": 1299.99,
}
jsonBytes, err := json.Marshal(eventData)
if err != nil {
    log.Fatalf("Failed to marshal JSON: %v", err)
}
jsonString := string(jsonBytes)

userID := "user123"
createdAt := "2025-06-01T13:11:12Z"  // Time the event originally occurred

newEpisode, err := client.Graph.Add(context.TODO(), &zep.AddDataRequest{
    UserID:    &userID,
    Type:      zep.GraphDataTypeJSON,
    Data:      jsonString,
    CreatedAt: &createdAt,
})
if err != nil {
    log.Fatalf("Failed to add JSON data: %v", err)
}
```

## Grouping episodes with a document ID

When several `graph.add` calls are chunks of the same source, pass the same `document_id` on each call. Typical sources are pages of a PDF, sections of a handbook, or messages from one Slack channel.

Zep scopes prior-episode context to that ID so extraction can resolve pronouns and references across chunks. Zep also generates an incremental [document summary](/documents).

`document_id` is optional and 1 to 100 characters. Use a stable identifier from the source system.

## Data Size Limit and Chunking

The `graph.add` endpoint has a data size limit of 10,000 characters when adding data to the graph. If you need to add a document which is more than 10,000 characters, see our [Chunking Large Documents](/chunking-large-documents) cookbook for a complete implementation with contextualized retrieval and best practices for chunking for Zep. Pass the same [`document_id`](/documents) on every chunk so Zep treats them as one source.

## Episode metadata

You can attach key-value metadata to episodes when adding data. Metadata is useful for tagging episodes with their data source, category, priority, or other attributes. Zep [projects this metadata onto every graph artifact derived from the episode](/episode-metadata-projection), which is what lets you [filter graph search results](/searching-the-graph#episode-metadata-filtering) so that only facts derived from matching episodes are returned.

Metadata values must be scalars (string, number (int/float), or boolean) or non-empty arrays of such scalars, such as `{"tags": ["red", "blue"]}`. A maximum of 10 keys are allowed per episode. Nested objects are not supported, and empty arrays or arrays containing null are rejected.

```python Python
from zep_cloud.client import Zep

client = Zep(
    api_key=API_KEY,
)

new_episode = client.graph.add(
    user_id="user123",
    type="text",
    data="Patient blood glucose level was 95 mg/dL, within normal range.",
    metadata={"source": "lab_report", "priority": 5, "reviewed": True},
)
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({
  apiKey: API_KEY,
});

const newEpisode = await client.graph.add({
    userId: "user123",
    type: "text",
    data: "Patient blood glucose level was 95 mg/dL, within normal range.",
    metadata: { source: "lab_report", priority: 5, reviewed: true },
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

newEpisode, err := client.Graph.Add(context.TODO(), &zep.AddDataRequest{
    UserID: &userID,
    Type:   zep.GraphDataTypeText,
    Data:   "Patient blood glucose level was 95 mg/dL, within normal range.",
    Metadata: map[string]interface{}{
        "source":   "lab_report",
        "priority": 5,
        "reviewed": true,
    },
})
if err != nil {
    log.Fatalf("Failed to add data: %v", err)
}
```

### Updating episode metadata

You can update an episode's metadata after creation using merge semantics: new keys are added, existing keys are overwritten, and keys set to `null` are removed.

```python Python
from zep_cloud.client import Zep

client = Zep(
    api_key=API_KEY,
)

# Original metadata: {"source": "lab_report", "priority": 5, "reviewed": True}
updated_episode = client.graph.episode.update(
    uuid_=episode_uuid,
    metadata={"priority": 10, "department": "endocrinology", "reviewed": None},
)
# Result: {"source": "lab_report", "priority": 10, "department": "endocrinology"}
# "priority" was overwritten, "department" was added, "reviewed" was removed
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({
  apiKey: API_KEY,
});

// Original metadata: { source: "lab_report", priority: 5, reviewed: true }
const updatedEpisode = await client.graph.episode.update(episodeUuid, {
    metadata: { priority: 10, department: "endocrinology", reviewed: null },
});
// Result: { source: "lab_report", priority: 10, department: "endocrinology" }
// "priority" was overwritten, "department" was added, "reviewed" was removed
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

// Original metadata: {"source": "lab_report", "priority": 5, "reviewed": true}
updatedEpisode, err := client.Graph.Episode.Update(context.TODO(), episodeUUID, &zep.EpisodeUpdateRequest{
    Metadata: map[string]interface{}{
        "priority":   10,
        "department": "endocrinology",
        "reviewed":   nil,
    },
})
if err != nil {
    log.Fatalf("Failed to update episode: %v", err)
}
// Result: {"source": "lab_report", "priority": 10, "department": "endocrinology"}
// "priority" was overwritten, "department" was added, "reviewed" was removed
```

## Managing Your Data on the Graph

The `graph.add` method returns the [episode](/graphiti/adding-episodes) that was created in the graph from adding that data. You can use this to maintain a mapping between your data and its corresponding episode in the graph and to delete specific data from the graph using the [delete episode](/deleting-data-from-the-graph#delete-an-episode) method.