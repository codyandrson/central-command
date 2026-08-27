> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Episodes

## Overview

An **episode** is a raw data artifact a developer hands to Zep — a chat message, a freeform text chunk, or a JSON object. Zep stores each episode verbatim alongside the entities, edges, and summaries it derives from that data, so the original source remains available even after extraction has finished. Thread messages are also episodes: each call to [`thread.add_messages`](/adding-messages) persists a `message` episode on the user's graph.

Reach for episodes when an agent needs **grounded context with the exact source truth** — quoting the original wording behind a fact, citing a source, or recovering surrounding context that did not become a fact in its own right.

## Ingestion

Episodes enter Zep in two ways:

* [`thread.add_messages`](/adding-messages) for conversational data — each message becomes a `message` episode on a [thread](/threads).
* [`graph.add`](/adding-business-data) for non-conversational data, with `type` set to `text` (a document excerpt, note, or transcript) or `json` (a structured record such as a CRM entry, ticket, or event). Pass a [`document_id`](/documents) when several episodes are chunks of the same source.

## Retrieval

The SDK lets you list recent episodes for a user, fetch one by UUID, list episodes for a [document](/documents), or search a graph specifically for episodes.

### List and fetch

```python Python
from zep_cloud.client import Zep

client = Zep(api_key=API_KEY)

# List the most recent episodes on a user's graph.
recent = client.graph.episode.get_by_user_id(user_id="emily-painter", lastn=20)
for ep in recent.episodes:
    print(ep.created_at, ep.source, ep.content)

# Fetch a single episode by UUID.
ep = client.graph.episode.get(uuid_="...")
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({ apiKey: API_KEY });

// List the most recent episodes on a user's graph.
const recent = await client.graph.episode.getByUserId("emily-painter", { lastn: 20 });
for (const ep of recent.episodes ?? []) {
  console.log(ep.createdAt, ep.source, ep.content);
}

// Fetch a single episode by UUID.
const ep = await client.graph.episode.get("...");
```

```go Go
import (
    "context"
    "fmt"
    v3 "github.com/getzep/zep-go/v3"
    v3client "github.com/getzep/zep-go/v3/client"
    "github.com/getzep/zep-go/v3/option"
)

client := v3client.NewClient(option.WithAPIKey(API_KEY))

// List the most recent episodes on a user's graph.
recent, err := client.Graph.Episode.GetByUserID(
    context.TODO(),
    "emily-painter",
    &v3.EpisodesByUserIDRequest{Lastn: v3.Int(20)},
)

for _, ep := range recent.Episodes {
    fmt.Println(ep.CreatedAt, ep.Source, ep.Content)
}

// Fetch a single episode by UUID.
ep, err := client.Graph.Episode.Get(context.TODO(), "...")
```

Episode listing uses `lastn` (the most recent N items) rather than cursor pagination.

### Search

To pull episodes most relevant to a query — for example, the source quotes behind a fact in the Context Block — use [graph search](/searching-the-graph) with `scope="episodes"`. Results land on `results.episodes`.

## Episodes and the Context Block

Episodes are included in the default [Context Block](/retrieving-context): the episodes most relevant to the user's recent messages are rendered alongside facts and entities. To customize which episodes appear or how they are formatted, define a [context template](/context-templates) using the `%{episodes}` variable, or build a custom block via [advanced context block construction](/advanced-context-block-construction).