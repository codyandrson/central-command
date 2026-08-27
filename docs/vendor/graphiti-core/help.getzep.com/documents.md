> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Documents

## Overview

A document is a customer-facing grouping for episodes on a graph. Use a `document_id` when you ingest chunks of the same logical source — for example pages of a PDF, sections of a JSON export, or messages from a Slack channel.

Documents are the graph analogue of [threads](/threads) on user graphs. Prior-episode context during ingestion is scoped to the same `document_id`, which improves pronoun resolution and continuity across chunks. Zep also generates incremental document summaries from those episodes.

`document_id` is optional. When present it must be 1 to 100 characters. Use a stable identifier from the source system, such as a file name and version, a Slack channel ID, or an export ID. The same `document_id` on different graphs identifies different documents.

Pass `document_id` on [`graph.add`](/adding-business-data) and on batch `graph_episode` items. You can target a standalone graph with `graph_id` or a user graph with `user_id`. Listing episodes and document summaries takes `graph_id`.

## Add episodes with a document ID

Pass optional `document_id` when calling `graph.add` (or when appending `graph_episode` batch items). Episodes that share a `document_id` on the same graph are associated together.

```python Python
from zep_cloud import Zep

client = Zep(api_key="YOUR_API_KEY")

client.graph.add(
    graph_id="my-graph",
    type="text",
    data="Alice joined Acme Corp as a designer.",
    document_id="handbook-v1",
)
client.graph.add(
    graph_id="my-graph",
    type="text",
    data="She reports to the product team in Austin.",
    document_id="handbook-v1",
)
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({ apiKey: "YOUR_API_KEY" });

await client.graph.add({
  graphId: "my-graph",
  type: "text",
  data: "Alice joined Acme Corp as a designer.",
  documentId: "handbook-v1",
});
await client.graph.add({
  graphId: "my-graph",
  type: "text",
  data: "She reports to the product team in Austin.",
  documentId: "handbook-v1",
});
```

```go Go
import (
    "context"
    v3client "github.com/getzep/zep-go/v3/client"
    "github.com/getzep/zep-go/v3/option"
    "github.com/getzep/zep-go/v3/api"
)

client := v3client.NewClient(
    option.WithAPIKey("YOUR_API_KEY"),
)

docID := "handbook-v1"
_, _ = client.Graph.Add(context.TODO(), &api.AddDataRequest{
    GraphId:    api.String("my-graph"),
    Type:       api.GraphDataTypeText,
    Data:       "Alice joined Acme Corp as a designer.",
    DocumentId: &docID,
})
_, _ = client.Graph.Add(context.TODO(), &api.AddDataRequest{
    GraphId:    api.String("my-graph"),
    Type:       api.GraphDataTypeText,
    Data:       "She reports to the product team in Austin.",
    DocumentId: &docID,
})
```

The [Batch API](/adding-batch-data) accepts the same optional `document_id` on `graph_episode` items.

## List episodes for a document

```python Python
episodes = client.graph.get_episodes_for_document(
    document_id="handbook-v1",
    graph_id="my-graph",
)
```

```typescript TypeScript
const episodes = await client.graph.getEpisodesForDocument("handbook-v1", {
  graphId: "my-graph",
});
```

```go Go
episodes, err := client.Graph.GetEpisodesForDocument(
    context.TODO(),
    "handbook-v1",
    &api.GraphGetEpisodesForDocumentRequest{GraphId: "my-graph"},
)
```

If no episodes have been associated with that `document_id`, the list is empty. A missing document is not an error.

## List document summaries for a graph

Document summaries are generated asynchronously after episodes are associated. Listing returns one summary per document that has been summarized.

```python Python
summaries = client.graph.document_summary.get_by_graph_id(
    graph_id="my-graph",
)
for summary in summaries:
    print(summary.document_id, summary.summary)
```

```typescript TypeScript
const summaries = await client.graph.documentSummary.getByGraphId("my-graph");
for (const summary of summaries) {
  console.log(summary.documentId, summary.summary);
}
```

```go Go
summaries, err := client.Graph.DocumentSummary.GetByGraphID(
    context.TODO(),
    "my-graph",
    &api.GraphDocumentSummariesRequest{},
)
```

## Related

* [Threads](/threads) — message grouping on user graphs
* [Thread summaries](/thread-summaries) — per-thread incremental summaries
* [Adding business data](/adding-business-data) — `graph.add` fields, including `document_id`
* [Prepare data for ingestion](/prepare-data-for-ingestion) — when to group chunks of one source
* [Chunking large documents](/chunking-large-documents) — split sources that exceed the episode size limit