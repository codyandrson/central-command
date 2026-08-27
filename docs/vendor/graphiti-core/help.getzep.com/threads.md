> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Threads

## Overview

Threads represent a conversation. Each User can have multiple threads, and each thread is a sequence of chat messages.

Chat messages are added to threads using [`thread.add_messages`](/adding-messages), which both adds those messages to the thread history and ingests those messages into the user-level knowledge graph. The user knowledge graph contains data from all of that user's threads to create an integrated understanding of the user.

## Relationship Between Users and Threads

`threadIds` are arbitrary identifiers that you can map to relevant business objects in your app, such as users or a conversation a user might have with your app. Before you create a thread, make sure you have created a user first.

## Automatic Cache Warming

When you create a new thread, Zep automatically warms the cache for that user's graph data in the background. This optimization improves query latency for graph operations on newly created threads by pre-loading the user's data into the hot cache tier.

The warming operation runs asynchronously and does not block the thread creation response. No additional action is required on your part—this happens automatically whenever you create a thread for a user with an existing graph.

For more information about Zep's multi-tier caching architecture and manual cache warming, see [Warming the User Cache](/performance#warming-the-user-cache).

## Next Steps

Now that you understand how Threads work, you can:

* Learn about [Users and User Graphs](/users-and-user-graphs)
* Discover how to [add messages to threads](/adding-messages)
* Learn how to [retrieve context for your agent](/retrieving-context)
* Read per-thread [Thread summaries](/thread-summaries)
* Understand more about the [graph](/graph-overview)
* Use [Documents](/documents) to group graph episodes the way threads group messages