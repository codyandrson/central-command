> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Check Data Ingestion Status

For production use cases, we recommend [webhooks](/webhooks) instead of polling. Zep pushes an `episode.processed` event (and `ingest.batch.completed` for batch operations) as soon as processing finishes, so your application reacts immediately without the latency and wasted requests of polling in a loop. The polling approach shown in this recipe is best suited to testing and development.

Data added to Zep is processed asynchronously and can take a few seconds to a few minutes to finish processing. This recipe shows how to check whether data upload operations are finished processing.

Zep provides these methods for checking data ingestion status:

* **Task polling**: Use `client.task.get()` to check the status of clone operations, direct node additions, and fact triple additions
* **Episode polling**: Use `graph.episode.get()` to check individual episode processing status
* **Batch status**: Use `batch.get()` and `batch.list_items()` for Batch API imports

For tracking large historical ingestions, see the [Batch API](/adding-batch-data), which has its own progress reporting via `batch.get` and per-item status via `batch.list_items`.

## Monitor zep-ingest with `IngestResult`

#### [Create an Ingestion Pipeline](/zep-ingest)

Prepare, preview, submit, and monitor an ordered import using one Python pipeline.

`zep-ingest` returns an `IngestResult` for Batch, episode, and task-backed operations:

```python
result.status
result.refresh()
result.wait(timeout=3600)
result.failed_items()
result.raise_for_status()
```

The result records the identifiers needed to continue monitoring in another process:

```python
print(result.batch_ids)
print(result.episode_uuids)
print(result.task_ids)
```

Persist the appropriate identifiers and reconstruct the result later:

```python
from zep_ingest import IngestResult

batch_result = IngestResult.from_batch_ids(client, saved_batch_ids)
batch_result.wait(timeout=3600)

task_result = IngestResult.from_task_ids(client, saved_task_ids)
task_result.wait(timeout=3600)
```

`wait()` polls the Batch, episode, or task handles in the result until they reach a terminal state. When Zep accepts a write but returns no handle for it, the result counts those items in `untracked_items`, `status` reports `untracked`, and `wait()` raises `IngestUntrackedError` rather than polling indefinitely. The submission succeeded in that case; only server-side extraction cannot be tracked, so confirm the data with a read of your own.

Search indexing can take additional time. `search_when_ready()` retries until a query returns any result or reaches the timeout; it does not verify that a specific imported record produced the result. Use a query unique to the imported data when checking indexing readiness.

## Checking Operation Status with Task Polling

When using operations that return a `task_id`, you can poll for completion status using `client.task.get()`. The following operations return a `task_id`:

* `graph.clone()` - Graph cloning operations
* `graph.add_nodes()` - Direct node additions and upserts
* `graph.add_fact_triple()` - Custom fact/node triplet additions

The pattern is the same for each operation: capture the `task_id` returned by the operation, then poll `client.task.get(task_id=task_id)` until `status` is `succeeded` or `failed`.

## Checking Individual Episode Status with Episode Polling

For single episode operations or when you need to check the status of individual episodes, you can use the `graph.episode.get()` method. This approach is useful when adding data one episode at a time.

First, let's create a user:

```python
import os
import uuid
import time
from dotenv import find_dotenv, load_dotenv
from zep_cloud.client import Zep

load_dotenv(dotenv_path=find_dotenv())

client = Zep(api_key=os.environ.get("ZEP_API_KEY"))
uuid_value = uuid.uuid4().hex[:4]
user_id = "-" + uuid_value
client.user.add(
    user_id=user_id,
    first_name = "John",
    last_name = "Doe",
    email="john.doe@example.com"
)
```

```typescript
import { ZepClient } from "@getzep/zep-cloud";
import * as dotenv from "dotenv";
import { v4 as uuidv4 } from 'uuid';

// Load environment variables
dotenv.config();

const client = new ZepClient({ apiKey: process.env.ZEP_API_KEY || "" });
const uuidValue = uuidv4().substring(0, 4);
const userId = "-" + uuidValue;

async function main() {
  // Add user
  await client.user.add({
    userId: userId,
    firstName: "John",
    lastName: "Doe",
    email: "john.doe@example.com"
  });
```

```go
package main

import (
	"context"
	"fmt"
	"os"
	"strings"
	"time"

	"github.com/getzep/zep-go/v3"
	zepclient "github.com/getzep/zep-go/v3/client"
	"github.com/getzep/zep-go/v3/option"
	"github.com/google/uuid"
	"github.com/joho/godotenv"
)

func main() {
	// Load .env file
	err := godotenv.Load()
	if err != nil {
		fmt.Println("Warning: Error loading .env file:", err)
		// Continue execution as environment variables might be set in the system
	}

	// Get API key from environment variable
	apiKey := os.Getenv("ZEP_API_KEY")
	if apiKey == "" {
		fmt.Println("ZEP_API_KEY environment variable is not set")
		return
	}

	// Initialize Zep client
	client := zepclient.NewClient(
		option.WithAPIKey(apiKey),
	)

	// Create a UUID
	uuidValue := strings.ToLower(uuid.New().String()[:4])

	// Create user ID
	userID := "-" + uuidValue

	// Create context
	ctx := context.Background()

	// Add a user
	userRequest := &zep.CreateUserRequest{
		UserID:    zep.String(userID),
		FirstName: zep.String("John"),
		LastName:  zep.String("Doe"),
		Email:     zep.String("john.doe@example.com"),
	}
	_, err = client.User.Add(ctx, userRequest)
	if err != nil {
		fmt.Printf("Error creating user: %v\n", err)
		return
	}
```

Now, let's add some data and immediately try to search for that data; because data added to Zep is processed asynchronously and can take a few seconds to a few minutes to finish processing, our search results do not have the data we just added:

```python
episode = client.graph.add(
    user_id=user_id,
    type="text", 
    data="The user is an avid fan of Eric Clapton"
)

search_results = client.graph.search(
    user_id=user_id,
    query="Eric Clapton",
    scope="nodes",
    limit=1,
    reranker="cross_encoder",
)

print(search_results.nodes)
```

```typescript
  // Add episode to graph
  const episode = await client.graph.add({
    userId: userId,
    type: "text",
    data: "The user is an avid fan of Eric Clapton"
  });

  // Search for nodes related to Eric Clapton
  const searchResults = await client.graph.search({
    userId: userId,
    query: "Eric Clapton",
    scope: "nodes",
    limit: 1,
    reranker: "cross_encoder"
  });

  console.log(searchResults.nodes);
```

```go
	// Add a new episode to the graph
	episode, err := client.Graph.Add(ctx, &zep.AddDataRequest{
		GraphID: zep.String(userID),
		Type:    zep.GraphDataTypeText.Ptr(),
		Data:    zep.String("The user is an avid fan of Eric Clapton"),
	})
	if err != nil {
		fmt.Printf("Error adding episode to graph: %v\n", err)
		return
	}

	// Search for the data
	searchResults, err := client.Graph.Search(ctx, &zep.GraphSearchQuery{
		UserID:  zep.String(userID),
		Query:   "Eric Clapton",
		Scope:   zep.GraphSearchScopeNodes.Ptr(),
		Limit:   zep.Int(1),
		Reranker: zep.RerankerCrossEncoder.Ptr(),
	})
	if err != nil {
		fmt.Printf("Error searching graph: %v\n", err)
		return
	}

	fmt.Println(searchResults.Nodes)
```

```text
None
```

We can check the status of the episode to see when it has finished processing, using the episode returned from the `graph.add` method and the `graph.episode.get` method:

```python
while True:
    episode = client.graph.episode.get(
        uuid_=episode.uuid_,
    )
    if episode.processed:
        print("Episode processed successfully")
        break
    print("Waiting for episode to process...")
    time.sleep(10)
```

```typescript
  // Check if episode is processed
  const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));
  
  let processedEpisode = await client.graph.episode.get(episode.uuid);
  
  while (!processedEpisode.processed) {
    console.log("Waiting for episode to process...");
    await sleep(10000); // Sleep for 10 seconds
    processedEpisode = await client.graph.episode.get(episode.uuid);
  }
  
  console.log("Episode processed successfully");
```

```go
	// Wait for the episode to be processed
	for {
		episodeStatus, err := client.Graph.Episode.Get(
			ctx,
			episode.UUID,
		)
		if err != nil {
			fmt.Printf("Error getting episode: %v\n", err)
			return
		}

		if episodeStatus.Processed != nil && *episodeStatus.Processed {
			fmt.Println("Episode processed successfully")
			break
		}

		fmt.Println("Waiting for episode to process...")
		time.Sleep(10 * time.Second)
	}
```

```text
Waiting for episode to process...
Waiting for episode to process...
Waiting for episode to process...
Waiting for episode to process...
Waiting for episode to process...
Episode processed successfully
```

Now that the episode has finished processing, we can search for the data we just added, and this time we get a result:

```python
search_results = client.graph.search(
    user_id=user_id,
    query="Eric Clapton",
    scope="nodes",
    limit=1,
    reranker="cross_encoder",
)

print(search_results.nodes)
```

```typescript
  // Search again after processing
  const finalSearchResults = await client.graph.search({
    userId: userId,
    query: "Eric Clapton",
    scope: "nodes",
    limit: 1,
    reranker: "cross_encoder"
  });

  console.log(finalSearchResults.nodes);
}

// Execute the main function
main().catch(error => console.error("Error:", error));
```

```go
	// Search again after processing
	searchResults, err = client.Graph.Search(ctx, &zep.GraphSearchQuery{
		UserID:  zep.String(userID),
		Query:   "Eric Clapton",
		Scope:   zep.GraphSearchScopeNodes.Ptr(),
		Limit:   zep.Int(1),
		Reranker: zep.RerankerCrossEncoder.Ptr(),
	})
	if err != nil {
		fmt.Printf("Error searching graph: %v\n", err)
		return
	}

	fmt.Println(searchResults.Nodes)
}
```

```text
[EntityNode(attributes={'category': 'Music', 'labels': ['Entity', 'Preference']}, created_at='2025-04-05T00:17:59.66565Z', labels=['Entity', 'Preference'], name='Eric Clapton', summary='The user is an avid fan of Eric Clapton.', uuid_='98808054-38ad-4cba-ba07-acd5f7a12bc0', graph_id='6961b53f-df05-48bb-9b8d-b2702dd72045')]
```