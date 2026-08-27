> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Thread summaries

## Overview

A thread summary is a natural-language summary of the messages in a single thread, generated and incrementally updated by Zep. There is one summary per thread, and it is persisted on the user's Context Graph alongside the data Zep already extracts from messages.

Thread summaries are useful as an extra type of context to give your agent a different view of a user's history — for example, what problem the user had in a given conversation and how it was resolved. Where [facts](/facts) and [entity summaries](/entities) describe the user across all their threads, a thread summary describes the arc of one specific conversation.

## How they're generated

Thread summaries are generated and updated automatically as new messages arrive in a thread. There is no manual "summarize now" call — clients only read summaries, they do not create them.

A thread that has never received messages will not have a summary, and the single-thread endpoint will return `404` until the first summary has been generated.

## Get the summary for a thread

Use this when you have a specific thread in hand and want its summary.

```python Python
from zep_cloud import Zep

client = Zep(api_key="YOUR_API_KEY")

summary = client.thread.get_summary(thread_id="thread-42")

print(summary.summary)             # the natural-language summary
print(summary.last_summarized_at)  # timestamp of the most recent update
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({ apiKey: "YOUR_API_KEY" });

const summary = await client.thread.getSummary("thread-42");

console.log(summary.summary);
console.log(summary.lastSummarizedAt);
```

```go Go
import (
    "context"
    "fmt"
    v3client "github.com/getzep/zep-go/v3/client"
    "github.com/getzep/zep-go/v3/option"
)

client := v3client.NewClient(
    option.WithAPIKey("YOUR_API_KEY"),
)

summary, err := client.Thread.GetSummary(context.TODO(), "thread-42")
if err != nil {
    // handle 404 if no summary has been generated yet
}

fmt.Println(summary.Summary)
fmt.Println(summary.LastSummarizedAt)
```

The `last_summarized_at` field on the returned object is the timestamp of the most recent summary update. A `404` response means Zep has not yet generated a summary for this thread (for example, a thread with no messages yet).

## List summaries for a user or graph

Use these endpoints to retrieve summaries across many threads — for example, when building a per-user dashboard. Both endpoints return a flat array of `ThreadSummary` objects and accept an optional pagination body.

```python Python
# All thread summaries across a user's threads.
page = client.graph.thread_summary.get_by_user_id(
    user_id="user-42",
    limit=20,
)

for s in page:
    print(s.thread_id, "—", s.summary)

# Continue with a cursor (the uuid of the last item from the previous page).
next_page = client.graph.thread_summary.get_by_user_id(
    user_id="user-42",
    limit=20,
    uuid_cursor=page[-1].uuid_,
)

# Or list summaries on a named (non-user) graph.
graph_summaries = client.graph.thread_summary.get_by_graph_id(
    graph_id="my-graph",
    limit=20,
)
```

```typescript TypeScript
const page = await client.graph.threadSummary.getByUserId("user-42", {
    limit: 20,
});

for (const s of page) {
    console.log(s.threadId, "—", s.summary);
}

const nextPage = await client.graph.threadSummary.getByUserId("user-42", {
    limit: 20,
    uuidCursor: page[page.length - 1].uuid,
});
```

```go Go
page, err := client.Graph.ThreadSummary.GetByUserId(
    context.TODO(),
    "user-42",
    &v3.GraphThreadSummariesRequest{
        Limit: v3.Int(20),
    },
)
```

## Search thread summaries

[`graph.search`](/searching-the-graph) accepts `scope="thread_summaries"` to search over thread summary content directly. Results are returned in a `thread_summaries` field on the search response.

```python Python
results = client.graph.search(
    user_id="emily-painter",
    query="payment failures and account recovery",
    scope="thread_summaries",
    limit=5,
)

for s in results.thread_summaries or []:
    print(s.thread_id, "—", s.summary)
```

```typescript TypeScript
const results = await client.graph.search({
  userId: "emily-painter",
  query: "payment failures and account recovery",
  scope: "thread_summaries",
  limit: 5,
});

for (const s of results.threadSummaries ?? []) {
  console.log(s.threadId, "—", s.summary);
}
```

```go Go
results, err := client.Graph.Search(context.TODO(), &v3.GraphSearchQuery{
    UserID: v3.String("emily-painter"),
    Query:  "payment failures and account recovery",
    Scope:  v3.GraphSearchScopeThreadSummaries.Ptr(),
    Limit:  v3.Int(5),
})

for _, s := range results.ThreadSummaries {
    fmt.Println(s.ThreadID, "—", s.Summary)
}
```

## Thread summaries and the Context Block

Thread summaries may be included in the default [Context Block](/retrieving-context) when Smart Context Assembly selects them as relevant to the current conversation. To always include them — or to pin a specific count — use a [context template](/context-templates). For full control over which summaries appear and how they're formatted, retrieve them directly with the SDK methods above, or build a custom block via [advanced context block construction](/advanced-context-block-construction).

## Related

* [Retrieving Context](/retrieving-context) — the user-wide Context Block.
* [Threads](/threads) — the underlying primitive being summarized.
* [Documents](/documents) — document summaries, the analogue for grouped graph episodes.
* [Entities](/entities) — entity-level summaries, a separate concept.
* [Context Types](/context-types) — overview of the other context types.