> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Performance Optimization Guide

> Best practices for optimizing Zep performance in production

This guide covers best practices for optimizing Zep's performance in production environments.

Zep delivers sub-200ms context retrieval regardless of graph size or number of graphs. On public benchmarks, Zep records 155ms retrieval latency on [LoCoMo](https://arxiv.org/abs/2402.17753) (94.7% accuracy) and 162ms on [LongMemEval](https://arxiv.org/abs/2410.10813) (90.2% accuracy). The optimizations below are application-side techniques that preserve this baseline and minimize end-to-end latency in your agent loop.

## Reuse the Zep SDK Client

The Zep SDK client maintains an HTTP connection pool that enables connection reuse, significantly reducing latency by avoiding the overhead of establishing new connections. To optimize performance:

* Create a single client instance and reuse it across your application
* Avoid creating new client instances for each request or function
* Consider implementing a client singleton pattern in your application
* For serverless environments, initialize the client outside the handler function

## Optimizing Context Operations

The `thread.add_messages` and `thread.get_user_context` methods are optimized for conversational messages and low-latency retrieval. For optimal performance:

* Use `graph.add` for larger documents, tool outputs, or business data (up to 10,000 characters per call)
* [Chunk large documents](/chunking-large-documents) before adding them to the graph
* Remove unnecessary metadata or content before persistence
* For bulk document ingestion, use [`zep-ingest`](/zep-ingest) or the [Batch API](/adding-batch-data) rather than parallel `graph.add` calls

```python
# Recommended for conversations
zep_client.thread.add_messages(
    thread_id="thread_123",
    message={
        "role": "user",
        "name": "Alice",
        "content": "What's the weather like today?"
    }
)

# Recommended for large documents
await zep_client.graph.add(
    data=document_content,  # Your chunked document content
    user_id=user_id,       # Or graph_id
    type="text"            # Can be "text", "message", or "json"
)
```

### Get the Context Block sooner

You can request the Context Block directly in the response to the `thread.add_messages()` call.
This optimization eliminates the need for a separate `thread.get_user_context()` call.
Read more about our [Context Block](/retrieving-context#zeps-context-block).

In this scenario you can pass in the `return_context=True` flag to the `thread.add_messages()` method.
Zep will perform a user graph search right after persisting the data and return the context relevant to the recently added messages.

```python Python
memory_response = await zep_client.thread.add_messages(
    thread_id=thread_id,
    messages=messages,
    return_context=True
)

context = memory_response.context
```

```typescript TypeScript
const memoryResponse = await zepClient.thread.addMessages(threadId, {
    messages: messages,
    returnContext: true
});

const context = memoryResponse.context;
```

```go Go
memoryResponse, err := zepClient.Thread.AddMessages(
    context.TODO(),
    threadId,
    &zep.AddThreadMessagesRequest{
        Messages: messages,
        ReturnContext: zep.Bool(true),
    },
)
if err != nil {
    // handle error
}
contextBlock := memoryResponse.Context
```

Read more in the [Thread SDK Reference](/sdk-reference/thread/add-messages)

### Searching the Graph Sooner

Instead of using `thread.get_user_context`, you might want to [search the graph](/searching-the-graph) directly with custom parameters and construct your own [custom context block](/cookbook/advanced-context-block-construction). When doing this, you can search the graph and add data to the graph concurrently.

```python
import asyncio
from zep_cloud.client import AsyncZep
from zep_cloud.types import Message

client = AsyncZep(api_key="your-zep-api-key")

async def add_and_retrieve_from_zep(messages):
    # Concatenate message content to create query string
    query = " ".join([msg.content for msg in messages])
    
    # Execute all operations concurrently
    add_result, edges_result, nodes_result = await asyncio.gather(
        client.thread.add_messages(
            thread_id=thread_id,
            messages=messages
        ),
        client.graph.search(
            user_id=user_id,
            query=query,
            scope="edges"
        ),
        client.graph.search(
            user_id=user_id,
            query=query,
            scope="nodes"
        )
    )
    
    return add_result, edges_result, nodes_result
```

You would then need to construct a custom context block using the search results. Learn more about [customizing your context block](/cookbook/advanced-context-block-construction).

## Optimizing Search Queries

Zep uses hybrid retrieval combining semantic (vector) similarity, BM25 full-text search, and graph traversal in a single ranked result. For optimal performance:

* Keep your queries concise. Queries are automatically truncated to 8,192 tokens (approximately 32,000 Latin characters)
* Longer queries may not improve search quality and will increase latency
* Consider breaking down complex searches into smaller, focused queries
* Use specific, contextual queries rather than generic ones

Best practices for search:

* Keep search queries concise and specific
* Structure queries to target relevant information
* Use natural language queries for better semantic matching
* Consider the scope of your search (graphs versus user graphs)

```python
# Recommended - concise query
results = await zep_client.graph.search(
    user_id=user_id,  # Or graph_id
    query="project requirements discussion"
)

# Not recommended - overly long query
results = await zep_client.graph.search(
    user_id=user_id,
    query="very long text with multiple paragraphs..."  # Will be truncated
)
```

## Warming the User Cache

Zep's proprietary runtime, the Context Graph Engine, serves retrieval over a three-tier data layer. The highest tier is a "hot" cache where a user's context retrieval is fastest. After several hours of no activity, a user's data will be moved to a lower tier.

You can hint to Zep that a retrieval may be made soon, allowing Zep to move user data into cache ahead of this retrieval. A good time to do this is when a user logs in to your service or opens your app.

```python Python
# Warm the user's cache when they log in
client.user.warm(user_id=user_id)
```

```typescript TypeScript
// Warm the user's cache when they log in
await client.user.warm(userId);
```

```go Go
// Warm the user's cache when they log in
_, err := client.User.Warm(context.TODO(), userId)
if err != nil {
    log.Printf("Error warming user cache: %v", err)
}
```

Read more in the [User SDK Reference](/sdk-reference/user/warm)

## Summary

* Reuse Zep SDK client instances to optimize connection management
* Use appropriate methods for different types of content (`thread.add_messages` for conversations, `graph.add` for large documents)
* Keep search queries focused and under the token limit for optimal performance
* Warm the user cache when users log in or open your app for faster retrieval