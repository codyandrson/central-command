> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Adding Messages

You can add both messages and business data to User Graphs.

## Adding Messages

Add your chat history to Zep using the `thread.add_messages` method. `thread.add_messages` is thread-specific and expects data in chat message format, including a `name` (e.g., user's real name), a `role` of `user`, `assistant`, `system`, `function`, `tool`, or `norole`, and message `content`. Zep stores the chat history and builds a user-level Context Graph from the messages.

For best results, add chat history to Zep on every chat turn. That is, add both the human and AI messages as you receive them and in the order that the messages were created. See the [Quick Start Guide](/quick-start-guide#add-incoming-user-messages-to-zep) for an example.

`thread.add_messages` is the recommended path for live conversation turns. To backfill past conversations from files on disk, use [`zep-ingest`](/zep-ingest). For individual business-data writes your application already holds in memory, use [`graph.add`](/adding-business-data). [Ingest](/adding-context) compares the paths.

It is important to provide the name of the user in the name field if possible, to help with graph construction. It's also helpful to provide a meaningful name for the assistant in its name field.

#### [Backfill historical conversations](/zep-ingest#backfill-thread-messages)

Use `ingest_thread_messages` to validate and load historical conversations while preserving thread order and timestamps.

### Basic example

The example below adds messages to Zep for the user in the given thread:

```python Python
from zep_cloud.client import Zep
from zep_cloud.types import Message

zep_client = Zep(
    api_key=API_KEY,
)

messages = [
    Message(
        name="Jane",
        role="user",
        content="Who was Octavia Butler?",
    )
]

response = zep_client.thread.add_messages(thread_id, messages=messages)
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";
import type { Message } from "@getzep/zep-cloud/api";

const zepClient = new ZepClient({
  apiKey: API_KEY,
});

const messages: Message[] = [
    { name: "Jane", role: "user", content: "Who was Octavia Butler?" },
];

const response = await zepClient.thread.addMessages(threadId, { messages });
```

```go Go
import (
    v3 "github.com/getzep/zep-go/v3"
    zepclient "github.com/getzep/zep-go/v3/client"
)

zepClient := zepclient.NewClient(
    option.WithAPIKey("<YOUR_API_KEY>"),
)
response, err := zepClient.Thread.AddMessages(
    context.TODO(),
    "threadId",
    &v3.AddThreadMessagesRequest{
        Messages: []*v3.Message{
            {
                Name: v3.String("Jane"),
                Role: "user",
                Content: "Who was Octavia Butler?",
            },
        },
    },
)
```

You can find additional arguments to `thread.add_messages` in the [SDK reference](/sdk-reference/thread/add-messages). Notably, for latency sensitive applications, you can set `return_context` to true which will make `thread.add_messages` return a context block in the way that `thread.get_user_context` does (discussed below).

### Ignore assistant messages

You can also pass in a list of roles to ignore when adding messages to a User Graph using the `ignore_roles` argument. For example, you may not want assistant messages to be added to the user graph; providing the assistant messages in the `thread.add_messages` call while setting `ignore_roles` to include "assistant" will make it so that only the user messages are ingested into the graph, but the assistant messages are still used to contextualize the user messages. This is important in case the user message itself does not have enough context, such as the message "Yes." Additionally, the assistant messages will still be added to the thread's message history.

```python Python
response = zep_client.thread.add_messages(
    thread_id,
    messages=messages,
    ignore_roles=["assistant"]
)
```

```typescript TypeScript
const response = await zepClient.thread.addMessages(threadId, {
    messages,
    ignoreRoles: ["assistant"]
});
```

```go Go
response, err := zepClient.Thread.AddMessages(
    context.TODO(),
    "threadId",
    &v3.AddThreadMessagesRequest{
        Messages: messages,
        IgnoreRoles: []string{"assistant"},
    },
)
```

### Creating messages with metadata

Messages can have metadata attached to store additional information like sentiment scores, source identifiers, processing flags, or other custom data. Metadata is preserved when getting threads, individual messages, and when searching episodes.

Message metadata is separate from [episode metadata](/episode-metadata-projection). Zep stores it on the message and returns it when you read messages or search episodes, but does not project it onto the graph or support [filtering](/searching-the-graph#episode-metadata-filtering) over it. For metadata that projects onto derived facts and entities and can be filtered in graph search, [attach it when adding data via `graph.add`](/adding-business-data#episode-metadata).

You can attach metadata when creating messages by including a `metadata` field in your message objects:

```python Python
from zep_cloud.client import Zep
from zep_cloud.types import Message

zep_client = Zep(
    api_key=API_KEY,
)

messages = [
    Message(
        name="Jane",
        role="user",
        content="I need help with my account.",
        metadata={
            "sentiment": "frustrated",
            "source": "mobile_app",
            "priority": "high"
        }
    )
]

response = zep_client.thread.add_messages(thread_id, messages=messages)
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";
import type { Message } from "@getzep/zep-cloud/api";

const zepClient = new ZepClient({
  apiKey: API_KEY,
});

const messages: Message[] = [
    {
        name: "Jane",
        role: "user",
        content: "I need help with my account.",
        metadata: {
            sentiment: "frustrated",
            source: "mobile_app",
            priority: "high"
        }
    },
];

const response = await zepClient.thread.addMessages(threadId, { messages });
```

```go Go
import (
    v3 "github.com/getzep/zep-go/v3"
    zepclient "github.com/getzep/zep-go/v3/client"
)

zepClient := zepclient.NewClient(
    option.WithAPIKey("<YOUR_API_KEY>"),
)
response, err := zepClient.Thread.AddMessages(
    context.TODO(),
    "threadId",
    &v3.AddThreadMessagesRequest{
        Messages: []*v3.Message{
            {
                Name: v3.String("Jane"),
                Role: "user",
                Content: "I need help with my account.",
                Metadata: map[string]interface{}{
                    "sentiment": "frustrated",
                    "source":    "mobile_app",
                    "priority":  "high",
                },
            },
        },
    },
)
```

### Updating message metadata

You can update the metadata of an existing message using the message UUID. This is useful for adding or modifying metadata after a message has been created, such as updating sentiment analysis results or processing status.

```python Python
from zep_cloud.client import Zep

zep_client = Zep(
    api_key=API_KEY,
)

# Update message metadata
updated_message = zep_client.thread.message.update(
    message_uuid="message-uuid-here",
    metadata={
        "sentiment": "positive",
        "resolved": True,
        "resolution_time": "2m 30s"
    }
)
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const zepClient = new ZepClient({
  apiKey: API_KEY,
});

// Update message metadata
const updatedMessage = await zepClient.thread.message.update(
    "message-uuid-here",
    {
        metadata: {
            sentiment: "positive",
            resolved: true,
            resolutionTime: "2m 30s"
        }
    }
);
```

```go Go
import (
    "context"
    v3 "github.com/getzep/zep-go/v3"
    zepclient "github.com/getzep/zep-go/v3/client"
    "github.com/getzep/zep-go/v3/option"
    "github.com/getzep/zep-go/v3/thread"
)

zepClient := zepclient.NewClient(
    option.WithAPIKey("<YOUR_API_KEY>"),
)

// Update message metadata
updatedMessage, err := zepClient.Thread.Message.Update(
    context.TODO(),
    "message-uuid-here",
    &thread.ThreadMessageUpdate{
        Metadata: map[string]interface{}{
            "sentiment":       "positive",
            "resolved":        true,
            "resolution_time": "2m 30s",
        },
    },
)
if err != nil {
    // Handle error
}
```

### Setting message timestamps

When creating messages via the API, you should provide the `created_at` timestamp in RFC3339 format. The `created_at` timestamp represents the time when the message was originally sent by the user. Setting the `created_at` timestamp is important to ensure the user's Context Graph has accurate temporal understanding of user history (since this time is used in our fact invalidation process).

```python Python
from zep_cloud.client import Zep
from zep_cloud.types import Message

zep_client = Zep(
    api_key=API_KEY,
)

messages = [
    Message(
        created_at="2025-06-01T13:11:12Z",
        name="Jane",
        role="user",
        content="What's the weather like today?",
    )
]

response = zep_client.thread.add_messages(thread_id, messages=messages)
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";
import type { Message } from "@getzep/zep-cloud/api";

const zepClient = new ZepClient({
  apiKey: API_KEY,
});

const messages: Message[] = [
    { 
        createdAt: "2025-06-01T13:11:12Z",
        name: "Jane", 
        role: "user", 
        content: "What's the weather like today?" 
    },
];

const response = await zepClient.thread.addMessages(threadId, { messages });
```

```go Go
import (
    v3 "github.com/getzep/zep-go/v3"
    zepclient "github.com/getzep/zep-go/v3/client"
)

zepClient := zepclient.NewClient(
    option.WithAPIKey("<YOUR_API_KEY>"),
)
response, err := zepClient.Thread.AddMessages(
    context.TODO(),
    "threadId",
    &v3.AddThreadMessagesRequest{
        Messages: []*v3.Message{
            {
                CreatedAt: v3.String("2025-06-01T13:11:12Z"),
                Name: v3.String("Jane"),
                Role: "user",
                Content: "What's the weather like today?",
            },
        },
    },
)
```

### Message limits

When adding messages to a thread, there are limits on both the number of messages and message size:

* **Messages per call**: You can add at most 30 messages in a single `thread.add_messages` call
* **Message size limit**: Each message can be at most 4,096 characters

If you exceed these limits, the API will return a 400 Bad Request error. If you need to add more than 30 messages or have messages exceeding the character limits, you'll need to split them across multiple API calls or truncate the content accordingly. Our additional recommendations include:

* Have users attach documents rather than paste them into the message, and then process documents separately with `graph.add`
* Reduce the max message size for your users to match our max message size
* Optional: allow users to paste in documents with an auto detection algorithm that turns it into an attachment as opposed to part of the message

### Check when messages are finished processing

You can use the message UUIDs from the response to poll the messages and check when they are finished processing:

```python
response = zep_client.thread.add_messages(thread_id, messages=messages)
message_uuids = response.message_uuids
```

An example of this can be found in the [check data ingestion status cookbook](/cookbook/check-data-ingestion-status).

## Adding Business Data

You can also add JSON or unstructured text to a User Graph using our [Graph API](/adding-business-data).

## Customizing Graph Creation

Zep offers two ways to customize how context is created. You can read more about these features at their guide pages:

* [**Custom entity and edge types**](/customizing-graph-structure#custom-entity-and-edge-types): Feature allowing use of Pydantic-like classes to customize creation/retrieval of entities and relations in the Context Graph.
* [**User summary instructions**](/users#user-summary-instructions): Customize how Zep generates entity summaries for users in their Context Graph with up to 5 custom instructions per user.