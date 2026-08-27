> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Memory

Zep's agent memory capabilities make context engineering simple: you add memory with a single line, retrieve memory with a single line, and then can immediately use the retrieved memory in your next LLM call.

The Memory API is high-level and opinionated. For a more customizable, low-level way to add and retrieve memory, see the [Graph API](/understanding-the-graph).

## Adding memory

Add your chat history to Zep using the `memory.add` method. `memory.add` is session-specific and expects data in chat message format, including a `role` name (e.g., user's real name), `role_type` (AI, human, tool), and message `content`. Zep stores the chat history and builds a user-level knowledge graph from the messages.

For best results, add chat history to Zep on every chat turn. That is, add both the AI and human messages in a single operation and in the order that the messages were created.

The example below adds messages to Zep's memory for the user in the given session:

```python Python
from zep_cloud.client import AsyncZep
from zep_cloud.types import Message

zep_client = AsyncZep(
    api_key=API_KEY,
)

messages = [
    Message(
        role="Jane",
        role_type="user",
        content="Who was Octavia Butler?",
    )
]

await zep_client.memory.add(session_id, messages=messages)
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";
import type { Message } from "@getzep/zep-cloud/api";

const zepClient = new ZepClient({
  apiKey: API_KEY,
});

const messages: Message[] = [
    { role: "Jane", role_type: "user", content: "Who was Octavia Butler?" },
];

await zepClient.memory.add(sessionId, { messages });
```

```go Go
import (
    "github.com/getzep/zep-go/v2"
    zepclient "github.com/getzep/zep-go/v2/client"
    "github.com/getzep/zep-go/v2/option"
)

zepClient := zepclient.NewClient(
    option.WithAPIKey("<YOUR_API_KEY>"),
)

response, err := zepClient.Memory.Add(
	context.TODO(),
	"sessionId",
	&zepgo.AddMemoryRequest{
		Messages: []*zepgo.Message{
			&zepgo.Message{
				Role: "Jane",
				RoleType: "user",
				Content: "Who was Octavia Butler?",
			},
		},
	},
)
```

You can find additional arguments to `memory.add` in the [SDK reference](/sdk-reference/memory/add). Notably, for latency sensitive applications, you can set `return_context` to true which will make `memory.add` return a context string in the way that `memory.get` does (discussed below).

If you are looking to add JSON or unstructured text as memory to the graph, you will need to use our [Graph API](/adding-data-to-the-graph).

### Ignore assistant messages

You can also pass in a list of role types to ignore when adding data to the graph using the `ignore_roles` argument. For example, you may not want assistant messages to be added to the user graph; providing the assistant messages in the `memory.add` call while setting `ignore_roles` to include "assistant" will make it so that only the user messages are ingested into the graph, but the assistant messages are still used to contextualize the user messages. This is important in case the user message itself does not have enough context, such as the message "Yes." Additionally, the assistant messages will still be added to the session's message history.

## Retrieving memory

The `memory.get()` method is a user-friendly, high-level API for retrieving relevant context from Zep. It uses the latest messages of the *given session* to determine what information is most relevant from the user's knowledge graph and returns that information in a [context string](/concepts#memory-context) for your prompt. Note that although `memory.get()` only requires a session ID, it is able to return memory derived from any session of that user. The session is just used to determine what's relevant.

`memory.get` also returns recent chat messages and raw facts that may provide additional context for your agent. We recommend using these raw messages when you call your LLM provider (see below). The `memory.get` method is user and session-specific and cannot retrieve data from group graphs.

The example below gets the `memory.context` string for the given session:

```python Python
memory = zep_client.memory.get(session_id="session_id")
# the context field described above
context = memory.context
```

```typescript TypeScript
const memory = await zep_client.memory.get("sessionId");
// the context field described above
const context = memory.context;
```

```go Go
memory, err := zep_client.Memory.Get(context.TODO(), "sessionId", nil)
// the context field described above
context := memory.Context
```

You can find additional arguments to `memory.get` in the [SDK reference](/sdk-reference/memory/get). Notably, you can specify a minimum [fact rating](/facts#rating-facts-for-relevancy) which will filter out any retrieved facts with a rating below the threshold, if you are using fact ratings.

If you are looking to customize how memory is retrieved, you will need to [search the graph](/searching-the-graph) and construct a [custom memory context string](/cookbook/customize-your-memory-context-string). For example, `memory.get` uses the last few messages as the search query on the graph, but using the graph API you can use whatever query you want, as well as experiment with other search parameters such as re-ranker used.

## Using memory

Once you've retrieved the [memory context string](/concepts#memory-context), or [constructed your own context string](/cookbook/customize-your-memory-context-string) by [searching the graph](/searching-the-graph), you can include this string in your system prompt:

| MessageType | Content                                                 |
| ----------- | ------------------------------------------------------- |
| `System`    | Your system prompt <br /> <br /> `{Zep context string}` |
| `Assistant` | An assistant message stored in Zep                      |
| `User`      | A user message stored in Zep                            |
| ...         | ...                                                     |
| `User`      | The latest user message                                 |

You should also include the last 4 to 6 messages of the session when calling your LLM provider. Because Zep's ingestion can take a few minutes, the context string may not include information from the last few messages; and so the context string acts as the "long-term memory," and the last few messages serve as the raw, short-term memory.

In latency sensitive applications such as voice chat bots, you can use the context string returned from `memory.add` to avoid making two API calls.

## Customizing memory

The Memory API is our high level, easy-to-use API for adding and retrieving memory. If you want to add business data or documents to memory, or further customize how memory is retrieved, you should refer to our Guides on using the graph, such as [adding data to the graph](/adding-data-to-the-graph) and [searching the graph](/searching-the-graph). We also have a cookbook on [creating a custom context string](/cookbook/customize-your-memory-context-string) using the graph API.

Additionally, [group graphs](/groups) can be used to store non-user-specific memory.