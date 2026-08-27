> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Ingest

> Add data to Zep from any source — chat messages, business data, documents, and JSON — and Zep builds the user's Context Graph automatically.

Zep builds Context Graphs from the data you provide. Which method you use depends on whether the data is a live conversation turn, an on-disk import, or an individual write from your application.

This is the **Ingest** stage of [Working with Context](/working-with-context). Before designing any import, review [Prepare Data for Ingestion](/prepare-data-for-ingestion) to preserve entity identity, source context, and event time.

## Choose an ingestion path

| What you are adding                                                                                                             | Use                                                      |
| ------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------- |
| A message in a live conversation, as your agent sends and receives it                                                           | [`thread.add_messages`](/adding-messages) in the Zep SDK |
| Historical data on disk you are loading for the first time: documents, transcripts, email, Slack exports, or past conversations | [`zep-ingest`](/zep-ingest)                              |
| A recurring export that lands files on disk (hourly or nightly dump, ETL output)                                                | [`zep-ingest`](/zep-ingest)                              |
| An individual document, API response, or webhook payload your application already holds in memory                               | [`graph.add`](/adding-business-data)                     |

### Live conversation turns: use the SDK

Add each message to Zep as your agent sends and receives it, using `thread.add_messages`. The SDK already runs inside the service handling the conversation, and a chat turn needs no preparation beyond the message itself. See [Adding messages](/adding-messages).

### Backfills and on-disk imports: use `zep-ingest`

[`zep-ingest`](/zep-ingest) is the recommended path when the data is already on disk — a one-time backfill of your history, or a recurring job that writes an export to a folder and then ingests it. Every built-in loader takes a path or glob: the package prepares, previews, submits in order, and monitors.

You do not have to use `zep-ingest`. It is a convenience layer over the Zep SDK, so calling [`graph.add`](/adding-business-data) or the [Batch API](/adding-batch-data) directly is fully supported.

### Individual writes and in-memory payloads: use `graph.add`

When your application already holds the data — a webhook body, an API response, a single document — call [`graph.add`](/adding-business-data) directly. Built-in `zep-ingest` loaders do not accept in-memory payloads today, so routing those through the package means writing a custom loader yourself; for most event-driven paths the SDK call is simpler. See [Adding business data](/adding-business-data).

When several `graph.add` calls are chunks of the same source, pass a [`document_id`](/documents) so Zep groups them for extraction context and summarization.

## Available methods

| Method                                            | Data Type                                  | Best For                                                                 |
| ------------------------------------------------- | ------------------------------------------ | ------------------------------------------------------------------------ |
| [**Create an Ingestion Pipeline**](/zep-ingest)   | Multiple source types                      | Backfills and on-disk imports, with preparation, preview, and monitoring |
| [**Adding messages**](/adding-messages)           | Chat messages                              | Live conversation turns from your agent                                  |
| [**Adding business data**](/adding-business-data) | Text, JSON, or message format              | Individual documents, API responses, emails, or other business data      |
| [**Batch ingestion**](/adding-batch-data)         | Many episodes and/or messages in one batch | The transport `zep-ingest` submits through, or your own large imports    |

## Next: shape the graph

After ingesting data, Zep processes it to build Context Graphs. Customize extraction and summaries under [Shape the Graph](/customizing-context).