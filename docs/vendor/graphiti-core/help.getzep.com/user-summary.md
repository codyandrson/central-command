> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# User summary

## Overview

The **user summary** is a single, derived narrative describing who a user is — what they care about, what they have done, and what is stable about them across conversations. Zep synthesizes it from the facts and entities that have accumulated on the user's Context Graph and attaches it to the user's central entity node.

Because the user summary is stored on the user node, it only exists on user graphs. Standalone graphs do not have user nodes and therefore do not have user summaries.

Because it is grounded in the whole graph rather than any one conversation, the user summary is the right place to look for a baseline picture of the user that does not depend on what they just said.

## Always-on in the Context Block

The user summary plays a role no other context type does: it is included **unconditionally** in every assembled [Context Block](/retrieving-context). The other types — [facts](/facts), [entities](/entities), [episodes](/episodes), [thread summaries](/thread-summaries), and [observations](/observations) — are search-driven and only appear when relevant to the incoming query. The user summary is not filtered by the query at all. It is the same string regardless of what the user just said, and it anchors the start of every conversation.

This makes it the agent's persistent "who am I talking to" reference, while the search-driven sections cover "what is relevant right now."

In the Context Block, the user summary is rendered first, wrapped in `<USER_SUMMARY>` tags:

```text
# This is the user summary
<USER_SUMMARY>
Emily Painter is a user with account ID Emily0e62 who uses digital art tools for creative work...
</USER_SUMMARY>
```

## Retrieval

The user summary lives on the user's central entity node. Fetch the node and read its `summary` field.

```python Python
from zep_cloud.client import Zep

client = Zep(api_key=API_KEY)

response = client.user.get_node(user_id="emily-painter")

if response.node:
    print(response.node.summary)
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({ apiKey: API_KEY });

const response = await client.user.getNode("emily-painter");

if (response.node) {
    console.log(response.node.summary);
}
```

```go Go
import (
    "context"
    "fmt"
    v3client "github.com/getzep/zep-go/v3/client"
    "github.com/getzep/zep-go/v3/option"
)

client := v3client.NewClient(option.WithAPIKey(API_KEY))

response, err := client.User.GetNode(context.TODO(), "emily-painter", nil)
if err != nil {
    // handle error
}

if response.Node != nil {
    fmt.Println(response.Node.Summary)
}
```

In most agent loops you will not need to fetch the summary directly — `thread.get_user_context()` already embeds it in the `<USER_SUMMARY>` section of the Context Block. The direct `get_node` route is for cases where you are assembling a [custom context block](/advanced-context-block-construction#example-4-using-user-summary-in-context-block) and want the summary string on its own.

## Generation

The user summary is auto-generated and maintained by Zep — developers do not write to it directly. As new facts and entities accrue on the user's graph, Zep regenerates the summary so it continues to reflect what is known about the user.

## Customization

You can shape what the user summary emphasizes — professional background, communication preferences, anything specific to your domain — by providing instructions per user or as project-wide defaults. See [User summary instructions](/user-summary-instructions) for the full method reference and best practices.