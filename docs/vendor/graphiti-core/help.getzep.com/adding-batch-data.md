> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Batch ingestion

> Load large historical datasets into Zep agent memory with the Batch API — backfill, migrate, and archive episodes and messages into your Context Graphs at scale.

The Batch API loads large historical datasets — backfills, document collections, archived conversations, migrations from another system — into your Context Graphs. It is the fastest transport for bulk ingestion, and it does not compete with the live `graph.add` and `thread.add_messages` traffic serving your agents.

[`zep-ingest`](/zep-ingest) is the recommended wrapper around the Batch API: it handles preparation, ordering, and monitoring on top. Use the Batch API directly when you want to manage batches yourself or already have ingestion code.

#### [Create an Ingestion Pipeline](/zep-ingest)

Prepare and preview exports, submit through the Batch API, and monitor the complete import.

## Why use the Batch API

Calling `graph.add` or `thread.add_messages` once per item works for live data but becomes hard to manage at scale. Compared to issuing those calls one at a time, the Batch API gives you:

* **Faster processing.** It ingests large datasets faster than the same operations sent one at a time.
* **No interference with live traffic.** It is designed not to slow the real-time `graph.add` and `thread.add_messages` ingestion serving your agents, so a large backfill can run alongside production.
* **Progress you can watch.** Monitor each batch's status, item counts, and errors in the [batch dashboard](#viewing-batches-in-the-dashboard), or poll programmatically.
* **One batch instead of many calls.** Group items into a single batch — splitting across batches when needed (see [Batch limits](#batch-limits)) — and hand it off to Zep to process as one job.

## How batches work

A batch follows a three-step lifecycle:

#### Create

Create an empty batch with optional metadata and an optional `strict_ontology` flag.

#### Add

Add items to the batch across one or more `batch.add` calls. Items can be graph episodes or thread messages, and may target different graphs, users, or threads.

#### Process

Start processing. Zep returns immediately and processes the batch asynchronously. You can poll for progress or watch it in the dashboard.

Items in a batch are grouped by destination graph and processed in the order they were added. Episodes and messages added through the Batch API are priced the same as those added through `graph.add` or `thread.add_messages`.

### Batch limits

* A single batch can contain up to **50,000 items**.
* Each call to `batch.add` accepts up to **350 items**.

To ingest more than 350 items, make multiple `batch.add` calls against the same batch ID before calling `batch.process`.

### Strict ontology

Set `strict_ontology` once on `batch.create`. Processing copies that value onto every graph episode and every thread message in the batch. You cannot set the flag per item.

```python Python
batch = client.batch.create(
    strict_ontology=True,
    metadata={"description": "Customer support backfill"},
)
```

```typescript TypeScript
const batch = await client.batch.create({
    strictOntology: true,
    metadata: { description: "Customer support backfill" },
});
```

```go Go
batch, err := client.Batch.Create(ctx, &v3.ApidataCreateBatchRequest{
    StrictOntology: v3.Bool(true),
    Metadata: map[string]interface{}{
        "description": "Customer support backfill",
    },
})
```

See [Setting Strict Ontology](/customizing-graph-structure#setting-strict-ontology) for what the flag does.

## Quickstart

The example below creates a batch, adds a mix of graph episodes and thread messages, starts processing, and polls until the batch finishes.

```python Python
import time
from zep_cloud.client import Zep
from zep_cloud import BatchAddItem

client = Zep(api_key=API_KEY)

# 1. Create the batch
batch = client.batch.create(
    metadata={"description": "Customer support backfill"},
)
batch_id = batch.batch_id

# 2. Add items to the batch
items = [
    BatchAddItem(
        type="graph_episode",
        user_id="alice",
        data="Alice signed up for the Pro plan on 2024-06-15.",
        data_type="text",
    ),
    BatchAddItem(
        type="graph_episode",
        graph_id="company_kb",
        data="Refund policy: orders may be refunded within 30 days of purchase.",
        data_type="text",
    ),
    BatchAddItem(
        type="thread_message",
        thread_id="alice_support_thread_42",
        content="My dashboard isn't loading.",
        role="user",
        name="Alice",
    ),
]

client.batch.add(batch_id=batch_id, items=items)

# 3. Start processing
client.batch.process(batch_id=batch_id)

# 4. Poll until the batch finishes
TERMINAL_STATUSES = ("succeeded", "partial", "failed", "invalid", "canceled")
while True:
    summary = client.batch.get(batch_id=batch_id)
    if summary.status in TERMINAL_STATUSES:
        break
    print(f"Status: {summary.status} ({summary.progress.percent_complete:.0f}%)")
    time.sleep(5)

print(f"Final status: {summary.status}")
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";
import type { BatchAddItem } from "@getzep/zep-cloud/api";

const client = new ZepClient({ apiKey: API_KEY });

// 1. Create the batch
const batch = await client.batch.create({
    metadata: { description: "Customer support backfill" },
});
const batchId = batch.batchId!;

// 2. Add items to the batch
const items: BatchAddItem[] = [
    {
        type: "graph_episode",
        userId: "alice",
        data: "Alice signed up for the Pro plan on 2024-06-15.",
        dataType: "text",
    },
    {
        type: "graph_episode",
        graphId: "company_kb",
        data: "Refund policy: orders may be refunded within 30 days of purchase.",
        dataType: "text",
    },
    {
        type: "thread_message",
        threadId: "alice_support_thread_42",
        content: "My dashboard isn't loading.",
        role: "user",
        name: "Alice",
    },
];

await client.batch.add(batchId, { items });

// 3. Start processing
await client.batch.process(batchId);

// 4. Poll until the batch finishes
const TERMINAL_STATUSES = ["succeeded", "partial", "failed", "invalid", "canceled"];
const sleep = (ms: number) => new Promise(r => setTimeout(r, ms));
let summary = await client.batch.get(batchId);
while (!TERMINAL_STATUSES.includes(summary.status!)) {
    console.log(`Status: ${summary.status} (${summary.progress?.percentComplete?.toFixed(0)}%)`);
    await sleep(5000);
    summary = await client.batch.get(batchId);
}

console.log(`Final status: ${summary.status}`);
```

```go Go
import (
    "context"
    "fmt"
    "time"

    v3 "github.com/getzep/zep-go/v3"
    zepclient "github.com/getzep/zep-go/v3/client"
    "github.com/getzep/zep-go/v3/option"
)

ctx := context.Background()
client := zepclient.NewClient(option.WithAPIKey(apiKey))

// 1. Create the batch
batch, err := client.Batch.Create(ctx, &v3.ApidataCreateBatchRequest{
    Metadata: map[string]interface{}{
        "description": "Customer support backfill",
    },
})
if err != nil {
    panic(err)
}
batchID := *batch.BatchID

// 2. Add items to the batch
items := []*v3.BatchAddItem{
    {
        Type:     v3.ApidataBatchAddItemTypeGraphEpisode,
        UserID:   v3.String("alice"),
        Data:     v3.String("Alice signed up for the Pro plan on 2024-06-15."),
        DataType: v3.GraphDataTypeText.Ptr(),
    },
    {
        Type:     v3.ApidataBatchAddItemTypeGraphEpisode,
        GraphID:  v3.String("company_kb"),
        Data:     v3.String("Refund policy: orders may be refunded within 30 days of purchase."),
        DataType: v3.GraphDataTypeText.Ptr(),
    },
    {
        Type:     v3.ApidataBatchAddItemTypeThreadMessage,
        ThreadID: v3.String("alice_support_thread_42"),
        Content:  v3.String("My dashboard isn't loading."),
        Role:     v3.ApidataBatchAddItemRoleUser.Ptr(),
        Name:     v3.String("Alice"),
    },
}

_, err = client.Batch.Add(ctx, batchID, &v3.ApidataAddBatchItemsRequest{Items: items})
if err != nil {
    panic(err)
}

// 3. Start processing
if _, err := client.Batch.Process(ctx, batchID); err != nil {
    panic(err)
}

// 4. Poll until the batch finishes
terminalStatuses := map[v3.BatchStatus]bool{
    v3.BatchStatusSucceeded: true,
    v3.BatchStatusPartial:   true,
    v3.BatchStatusFailed:    true,
    v3.BatchStatusInvalid:   true,
    v3.BatchStatusCanceled:  true,
}
for {
    summary, err := client.Batch.Get(ctx, batchID)
    if err != nil {
        panic(err)
    }
    if terminalStatuses[*summary.Status] {
        fmt.Printf("Final status: %s\n", *summary.Status)
        break
    }
    fmt.Printf("Status: %s (%.0f%%)\n", *summary.Status, *summary.Progress.PercentComplete)
    time.Sleep(5 * time.Second)
}
```

## Adding items to a batch

Each item in a batch is one of two types:

* **`graph_episode`** — equivalent to a single `graph.add` call. Targets a graph by `graph_id` or a user graph by `user_id`.
* **`thread_message`** — equivalent to one message inside a `thread.add_messages` call. Targets a thread by `thread_id`.

The fields below mirror the equivalent fields on `graph.add` and `thread.add_messages`. See [Adding business data](/adding-business-data) and [Adding messages](/adding-messages) for the underlying semantics.

A batch item is a single SDK type covering both kinds, so every field is settable on every item. Fields that do not apply to an item's `type` are still validated, but not stored — `source_description` on a `thread_message` is rejected above 500 characters, and a valid value is discarded.

### Common fields

| Field        | Description                                                                                                                                                                                       |
| ------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `type`       | Required. `graph_episode` or `thread_message`.                                                                                                                                                    |
| `metadata`   | Optional. Up to 10 key-value pairs. See [Episode metadata](/adding-business-data#episode-metadata) for constraints and search filtering.                                                          |
| `created_at` | Optional. ISO 8601 timestamp marking when the original event occurred. Used by Zep's fact invalidation process for both item types. See [Setting timestamps](#setting-timestamps-on-batch-items). |

### Graph episode fields (`type: "graph_episode"`)

| Field                     | Description                                                                                                                                 |
| ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| `data`                    | Required. The episode content. Subject to the same 10,000-character limit as `graph.add`.                                                   |
| `data_type`               | Required. `text`, `json`, or `message`.                                                                                                     |
| `graph_id` *or* `user_id` | One of the two is required to identify the destination graph.                                                                               |
| `source_description`      | Optional. Human-readable description of where the episode came from. Maximum 500 characters.                                                |
| `document_id`             | Optional. Groups this episode with other chunks of the same [document](/documents). Ignored on `thread_message` items. 1 to 100 characters. |

### Thread message fields (`type: "thread_message"`)

| Field       | Description                                                                   |
| ----------- | ----------------------------------------------------------------------------- |
| `thread_id` | Required. The destination thread.                                             |
| `content`   | Required. The message body.                                                   |
| `role`      | Required. One of `user`, `assistant`, `system`, `function`, `tool`, `norole`. |
| `name`      | Optional. Speaker name.                                                       |

## Setting timestamps on batch items

Pass `created_at` on each item to give Zep accurate temporal information for historical data. This is important for backfills — Zep uses these timestamps in its fact invalidation process to determine the `valid_at` and `invalid_at` values on extracted facts (edges).

The `created_at` value should be in RFC3339 format (e.g., `"2024-06-15T10:30:00Z"`).

Both item types honor the value you supply: a `thread_message` item's `created_at` dates the episode Zep extracts from that message, exactly as it does for a `graph_episode` item, so a batch backfill and a direct `thread.add_messages` call place the same message at the same point in the timeline. An item without a `created_at` is dated at ingestion time.

```python Python
from zep_cloud import BatchAddItem

items = [
    BatchAddItem(
        type="graph_episode",
        user_id="alice",
        data="Alice joined the engineering team as a senior developer.",
        data_type="text",
        created_at="2024-06-15T10:30:00Z",
    ),
    BatchAddItem(
        type="graph_episode",
        user_id="alice",
        data="Alice was promoted to tech lead of the engineering team.",
        data_type="text",
        created_at="2024-09-01T09:00:00Z",
    ),
]

client.batch.add(batch_id=batch_id, items=items)
```

```typescript TypeScript
import type { BatchAddItem } from "@getzep/zep-cloud/api";

const items: BatchAddItem[] = [
    {
        type: "graph_episode",
        userId: "alice",
        data: "Alice joined the engineering team as a senior developer.",
        dataType: "text",
        createdAt: "2024-06-15T10:30:00Z",
    },
    {
        type: "graph_episode",
        userId: "alice",
        data: "Alice was promoted to tech lead of the engineering team.",
        dataType: "text",
        createdAt: "2024-09-01T09:00:00Z",
    },
];

await client.batch.add(batchId, { items });
```

```go Go
items := []*v3.BatchAddItem{
    {
        Type:      v3.ApidataBatchAddItemTypeGraphEpisode,
        UserID:    v3.String("alice"),
        Data:      v3.String("Alice joined the engineering team as a senior developer."),
        DataType:  v3.GraphDataTypeText.Ptr(),
        CreatedAt: v3.String("2024-06-15T10:30:00Z"),
    },
    {
        Type:      v3.ApidataBatchAddItemTypeGraphEpisode,
        UserID:    v3.String("alice"),
        Data:      v3.String("Alice was promoted to tech lead of the engineering team."),
        DataType:  v3.GraphDataTypeText.Ptr(),
        CreatedAt: v3.String("2024-09-01T09:00:00Z"),
    },
}

client.Batch.Add(ctx, batchID, &v3.ApidataAddBatchItemsRequest{Items: items})
```

## Tracking progress

Two methods report on a running or completed batch:

* **`batch.get(batch_id)`** returns a summary of the whole batch, including a `progress` object with counts for `total_items`, `queued_items`, `processing_items`, `succeeded_items`, `failed_items`, `skipped_items`, `canceled_items`, and `percent_complete`. Before `batch.process` is called the batch is in `draft` and the `progress` counts are unpopulated; once processing starts the counts begin to update.
* **`batch.list_items(batch_id)`** returns each item with its individual status (`pending`, `queued`, `processing`, `succeeded`, `failed`, `skipped`, `canceled`).

When polling `batch.get`, a few-second interval (e.g., 5 seconds) is appropriate for small batches. For batches with thousands of items or more, polling becomes impractical — subscribe to the [`ingest.batch.completed` webhook](/webhooks#batch-completion-payloads) instead to be notified when a batch reaches a terminal state. The payload includes the `batch_id` so you can match it back to the batch you submitted.

### Batch statuses

The `status` field on `BatchSummary` is one of:

| Status       | Meaning                                                                                                                                                                   |
| ------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `draft`      | The batch was just created. Items can still be added with `batch.add`. Processing has not started. Can be deleted.                                                        |
| `invalid`    | `batch.process` was called, but one or more items reference graphs, users, or threads that don't exist. The batch cannot proceed. Can be deleted.                         |
| `queued`     | `batch.process` was called and the batch is waiting for a worker.                                                                                                         |
| `processing` | A worker is actively processing the batch.                                                                                                                                |
| `succeeded`  | Terminal. No item failed. Items may still have been skipped or canceled, so check the `skipped_items` and `canceled_items` counts before treating the import as complete. |
| `partial`    | Terminal. Some items succeeded and others failed. Use `batch.list_items` to see which items failed.                                                                       |
| `failed`     | Terminal. The batch as a whole failed.                                                                                                                                    |
| `canceled`   | Terminal. Every item was canceled because its target graph, user, or thread was deleted while the batch was in flight, so nothing was ingested.                           |

Once a batch reaches `succeeded`, `partial`, `failed`, or `canceled`, no further state changes occur. `invalid` is also non-progressing — the batch never starts processing, but the state persists until you delete the batch. When polling, exit on any of `succeeded`, `partial`, `failed`, `canceled`, or `invalid`.

### Per-item statuses

The `status` field on each `BatchItemDetail` is one of:

| Status       | Meaning                                                                                                                      |
| ------------ | ---------------------------------------------------------------------------------------------------------------------------- |
| `pending`    | The item has been added to the batch but processing has not started.                                                         |
| `queued`     | The item is queued for processing.                                                                                           |
| `processing` | The item is currently being processed.                                                                                       |
| `succeeded`  | The item processed successfully.                                                                                             |
| `failed`     | The item failed to process. The `error` field on the item describes why.                                                     |
| `skipped`    | The item was skipped during processing — for example, a thread message whose role matches a configured `ignore_roles` value. |
| `canceled`   | The item was not processed because its target graph, user, or thread was deleted before the item finished.                   |

```python Python
summary = client.batch.get(batch_id=batch_id)
print(f"Status: {summary.status}")
print(f"Progress: {summary.progress.succeeded_items}/{summary.progress.total_items}")

# Inspect individual items
items = client.batch.list_items(batch_id=batch_id, limit=50)
for item in items.items:
    print(item.item_id, item.status)
```

```typescript TypeScript
const summary = await client.batch.get(batchId);
console.log(`Status: ${summary.status}`);
console.log(`Progress: ${summary.progress?.succeededItems}/${summary.progress?.totalItems}`);

// Inspect individual items
const items = await client.batch.listItems(batchId, { limit: 50 });
for (const item of items.items ?? []) {
    console.log(item.itemId, item.status);
}
```

```go Go
summary, _ := client.Batch.Get(ctx, batchID)
fmt.Printf("Status: %s\n", *summary.Status)
fmt.Printf("Progress: %d/%d\n", *summary.Progress.SucceededItems, *summary.Progress.TotalItems)

// Inspect individual items
items, _ := client.Batch.ListItems(ctx, batchID, &v3.BatchListItemsRequest{Limit: v3.Int(50)})
for _, item := range items.Items {
    fmt.Println(*item.ItemID, *item.Status)
}
```

## Listing and managing batches

Use `batch.list` to enumerate batches in your project, optionally filtered by status. Use `batch.delete` to remove a batch that has not yet been processed — once a batch has been processed, it cannot be deleted.

```python Python
# List recent batches
result = client.batch.list(limit=20)
for b in result.batches:
    print(b.batch_id, b.status, b.item_count)

# List only batches that are still being processed
result = client.batch.list(status="processing")

# Delete a draft batch
client.batch.delete(batch_id=batch_id)
```

```typescript TypeScript
// List recent batches
const result = await client.batch.list({ limit: 20 });
for (const b of result.batches ?? []) {
    console.log(b.batchId, b.status, b.itemCount);
}

// List only batches that are still being processed
await client.batch.list({ status: "processing" });

// Delete a draft batch
await client.batch.delete(batchId);
```

```go Go
// List recent batches
result, _ := client.Batch.List(ctx, &v3.BatchListRequest{Limit: v3.Int(20)})
for _, b := range result.Batches {
    fmt.Println(*b.BatchID, *b.Status, *b.ItemCount)
}

// List only batches that are still being processed
client.Batch.List(ctx, &v3.BatchListRequest{Status: v3.String("processing")})

// Delete a draft batch
client.Batch.Delete(ctx, batchID)
```

## Viewing batches in the dashboard

The Zep web dashboard provides a batches view showing all batches in your project, their status, item counts, and processing progress. Click into a batch to inspect its individual items and any errors. You can delete a `draft` or `invalid` batch from the list or the batch detail page. Batches that have started processing cannot be deleted.

## Deprecated batch methods

The following methods are deprecated and no longer recommended. Use the Batch API described above for all new ingestion work.

| Deprecated method                                                         | Replacement                                    |
| ------------------------------------------------------------------------- | ---------------------------------------------- |
| `graph.add_batch()` (`POST /graph-batch`)                                 | `client.batch.*` with `type: "graph_episode"`  |
| `thread.add_messages_batch()` (`POST /threads/{threadId}/messages-batch`) | `client.batch.*` with `type: "thread_message"` |

The deprecated methods continue to work but will be removed in a future release.