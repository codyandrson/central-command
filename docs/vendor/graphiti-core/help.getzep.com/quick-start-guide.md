> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Quick Start Guide

> Add agent memory to your app in three lines of code. This Zep quick start covers users, threads, ingesting data, and retrieving a Context Block in under 200ms.

Zep delivers agent memory at enterprise scale, giving your AI agents the right context at the right time. From a temporal Context Graph, Zep assembles relevant context from chat history, business data, and user behavior—so agents make better decisions with accurate, up-to-date information. With a simple three-line API and sub-200ms retrieval, Zep helps you build personalized, reliable agents without building a context pipeline.

Get started with the example in the video using:

```bash
git clone https://github.com/getzep/zep.git
cd zep/examples/python/agent-memory-full-example
```

This guide shows you how to integrate Zep into your AI application to provide personalized context for every user interaction. You'll learn how to ingest user messages and business data, then retrieve assembled context that includes user preferences, traits, and relevant facts—all optimized for your LLM's context window.

Looking for a more in-depth understanding? Check out our [Key Concepts](/concepts) page.

Migrating from Mem0? Check out our [Mem0 Migration](/mem0-to-zep) guide.

## Install the Zep SDK

#### Python

Set up your Python project, ideally with [a virtual environment](https://medium.com/@vkmauryavk/managing-python-virtual-environments-with-uv-a-comprehensive-guide-ac74d3ad8dff), and then:

```bash pip
pip install zep-cloud
```

```bash uv
uv pip install zep-cloud
```

#### TypeScript

Set up your TypeScript project and then:

```bash npm
npm install @getzep/zep-cloud
```

```bash yarn
yarn add @getzep/zep-cloud
```

```bash pnpm
pnpm install @getzep/zep-cloud
```

#### Go

Set up your Go project and then:

```bash
go get github.com/getzep/zep-go/v3
```

## Initialize the Zep client

After [creating a Zep account](https://app.getzep.com/), obtaining an API key, and setting the API key as an environment variable, initialize the client once at application startup and reuse it throughout your application.

#### Initialize Zep client

```python Python
import os
from zep_cloud.client import Zep

API_KEY = os.environ.get('ZEP_API_KEY')

client = Zep(
    api_key=API_KEY,
)
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const API_KEY = process.env.ZEP_API_KEY;

const client = new ZepClient({
  apiKey: API_KEY,
});
```

```go Go
import (
    zepclient "github.com/getzep/zep-go/v3/client"
    "github.com/getzep/zep-go/v3/option"
)

client := zepclient.NewClient(
    option.WithAPIKey(os.Getenv("ZEP_API_KEY")),
)
```

#### .env

```
ZEP_API_KEY=your_api_key_here
```

## Create a Zep user for each of your users

Whenever users are created in your application, you need to trigger the creation of a Zep user. Make sure to include at least their first name, and ideally also their last name and email to ensure correct identification of the user in future messages. We recommend setting the Zep user ID equal to your internal user ID.

**Backfilling existing users:** For existing users, you will need to run a one-time migration to create a user for each of the existing users (simply loop through and call `user.add` for each).

Provide at least the first name and ideally the last name when calling `user.add` to ensure Zep correctly associates the user with references in your data. If needed, add this information later using the [update user](/sdk-reference/user/update) method.

```python Python
from zep_cloud.client import Zep

client = Zep(api_key=API_KEY)

# You can choose any user ID, but we recommend using your internal user ID
user_id = "your_internal_user_id"

new_user = client.user.add(
    user_id=user_id,
    email="jane.smith@example.com",
    first_name="Jane",
    last_name="Smith",
)
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({
  apiKey: API_KEY,
});

// You can choose any user ID, but we recommend using your internal user ID
const userId = "your_internal_user_id";

const user = await client.user.add({
  userId: userId,
  email: "jane.smith@example.com",
  firstName: "Jane",
  lastName: "Smith",
});
```

```go Go
import (
	"context"
	"log"

	"github.com/getzep/zep-go/v3"
	zepclient "github.com/getzep/zep-go/v3/client"
	"github.com/getzep/zep-go/v3/option"
)

client := zepclient.NewClient(option.WithAPIKey(apiKey))

// You can choose any user ID, but we recommend using your internal user ID
userID := "your_internal_user_id"

newUser, err := client.User.Add(context.TODO(), &zep.CreateUserRequest{
	UserID:    userID,
	Email:     zep.String("jane.smith@example.com"),
	FirstName: zep.String("Jane"),
	LastName:  zep.String("Smith"),
})
if err != nil {
	log.Fatalf("Failed to add user: %v", err)
}
```

## Create a Zep thread for each of your threads

Whenever a user starts a new conversation with your agent, you need to trigger the creation of a Zep thread. Learn more about [adding messages](/adding-messages).

**Backfilling prior conversations:** For prior conversations, you will need to run a one-time migration to create Zep threads for those conversations and add the prior messages to the respective Zep threads. [`zep-ingest`](/zep-ingest#backfill-thread-messages) is the recommended way to do this: it validates each row, creates the threads, preserves message order, and monitors the import. You can also drive the [Batch API](/adding-batch-data) yourself.

```python Python
client = Zep(
    api_key=API_KEY,
)
thread_id = uuid.uuid4().hex # A new thread identifier

client.thread.create(
    thread_id=thread_id,
    user_id=user_id,
)
```

```typescript TypeScript
const client = new ZepClient({
  apiKey: API_KEY,
});

const threadId: string = uuid.v4(); // Generate a new thread identifier

await client.thread.create({
  threadId: threadId,
  userId: userId,
});
```

```go Go
import (
	"context"
	"log"

	"github.com/getzep/zep-go/v3"
	zepclient "github.com/getzep/zep-go/v3/client"
	"github.com/getzep/zep-go/v3/option"
	"github.com/google/uuid"
)

client := zepclient.NewClient(option.WithAPIKey(apiKey))

threadID := uuid.New().String() // Generate a new thread identifier

_, err := client.Thread.Create(context.TODO(), &zep.CreateThreadRequest{
	ThreadID: threadID,
	UserID:   userID,
})
if err != nil {
	log.Fatalf("Failed to create thread: %v", err)
}
```

## Add incoming user messages to Zep

When a new user message comes in, add the user message to Zep, providing the user's name in the message if possible.

It is important to provide the name of the user in the name field if possible, to help with graph construction.

Include the `created_at` timestamp (RFC3339 format) representing when the message was originally sent. This ensures accurate temporal understanding in the knowledge graph. See [Setting message timestamps](/adding-messages#setting-message-timestamps) for more details.

```python Python
from zep_cloud.client import Zep
from zep_cloud.types import Message
from datetime import datetime, timezone

zep_client = Zep(
    api_key=API_KEY,
)

messages = [
    Message(
        created_at=datetime.now(timezone.utc).isoformat(),
        name="Jane Smith",
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
    {
        createdAt: new Date().toISOString(),
        name: "Jane Smith",
        role: "user",
        content: "Who was Octavia Butler?"
    },
];

const response = await zepClient.thread.addMessages(threadId, { messages });
```

```go Go
import (
    "time"
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
                CreatedAt: v3.String(time.Now().UTC().Format(time.RFC3339)),
                Name: v3.String("Jane Smith"),
                Role: "user",
                Content: "Who was Octavia Butler?",
            },
        },
    },
)
```

## Add streaming business data to Zep

Beyond chat messages, you can provide Zep with additional context about your users by sending business data directly to their knowledge graphs. This includes user interactions with your application, transactions, support tickets, emails, transcripts—essentially any information that gives context about the user and can be represented as text.

Use the `graph.add` method to send structured, semi-structured, or unstructured text data to Zep. Include a reference to the user—their full name, user ID, or both—so Zep can correctly associate the data with the user in their knowledge graph. Read more about [adding business data](/adding-business-data).

Any text can be sent to Zep—structured JSON, semi-structured logs, or plain text descriptions. The example below shows a JSON event, but you could also send `"User Jane Smith listened to 'Bohemian Rhapsody' by Queen"` as plain text. See [Adding business data](/adding-business-data) for more data type options.

```python Python
from zep_cloud.client import Zep
import json

client = Zep(api_key=API_KEY)

# Example: User listened to a song in your application
event_data = {
    "user_id": "user123",
    "user_name": "Jane Smith",
    "event_type": "song_played",
    "song_title": "Bohemian Rhapsody",
    "artist": "Queen",
    "duration_seconds": 354
}

client.graph.add(
    user_id="user123",
    type="json",
    data=json.dumps(event_data)
)
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({ apiKey: API_KEY });

// Example: User listened to a song in your application
const eventData = {
    user_id: "user123",
    user_name: "Jane Smith",
    event_type: "song_played",
    song_title: "Bohemian Rhapsody",
    artist: "Queen",
    duration_seconds: 354
};

await client.graph.add({
    userId: "user123",
    type: "json",
    data: JSON.stringify(eventData)
});
```

```go Go
import (
    "context"
    "encoding/json"
    "log"

    "github.com/getzep/zep-go/v3"
    zepclient "github.com/getzep/zep-go/v3/client"
    "github.com/getzep/zep-go/v3/option"
)

client := zepclient.NewClient(option.WithAPIKey(apiKey))

// Example: User listened to a song in your application
eventData := map[string]interface{}{
    "user_id":          "user123",
    "user_name":        "Jane Smith",
    "event_type":       "song_played",
    "song_title":       "Bohemian Rhapsody",
    "artist":           "Queen",
    "duration_seconds": 354,
}

jsonBytes, err := json.Marshal(eventData)
if err != nil {
    log.Fatalf("Failed to marshal JSON: %v", err)
}

userID := "user123"
_, err = client.Graph.Add(context.TODO(), &zep.AddDataRequest{
    UserID: &userID,
    Type:   zep.GraphDataTypeJSON,
    Data:   string(jsonBytes),
})
if err != nil {
    log.Fatalf("Failed to add event data: %v", err)
}
```

## Retrieve Zep context block

After adding the user message to the thread and before generating the AI response, retrieve the Zep context block, which will contain the most relevant information to the user's message from the user's knowledge graph.

### Use the default context block

Zep's default Context Block is an optimized, automatically assembled string that combines semantic search, full text search, and breadth first search to return context that is highly relevant to the user's current conversation slice, utilizing the past two messages.

The Context Block provides low latency (P95 \< 200ms) while preserving detailed information from the user's graph.

```python Python
# Get context for the thread
user_context = client.thread.get_user_context(thread_id=thread_id)

# Access the context block (for use in prompts)
context_block = user_context.context
print(context_block)
```

```typescript TypeScript
// Get context for the thread
const userContext = await client.thread.getUserContext(threadId);

// Access the context block (for use in prompts)
const contextBlock = userContext.context;
console.log(contextBlock);
```

```go Go
import (
    "context"
    v3 "github.com/getzep/zep-go/v3"
)

// Get context for the thread
userContext, err := client.Thread.GetUserContext(context.TODO(), threadId, nil)
if err != nil {
    log.Fatal("Error getting context:", err)
}
// Access the context block (for use in prompts)
contextBlock := userContext.Context
fmt.Println(contextBlock)
```

The Context Block includes the user summary along with the most relevant [context types](/context-types) from the user's graph (facts, entities, and episodes by default). The example below shows a Context Block with a user summary and facts:

```text
# This is the user summary
<USER_SUMMARY>
Emily Painter is a user with account ID Emily0e62 who uses digital art tools for creative work. She maintains an active account with the service, though has recently experienced technical issues with the Magic Pen Tool. Emily values reliable payment processing and seeks prompt resolution for account-related issues. She expects clear communication and efficient support when troubleshooting technical problems.
</USER_SUMMARY>

# These are the most relevant facts and their valid date ranges
# format: FACT (Date range: from - to)
<FACTS>
  - Emily is experiencing issues with logging in. (2024-11-14 02:13:19+00:00 - present)
  - User account Emily0e62 has a suspended status due to payment failure. (2024-11-14 02:03:58+00:00 - present)
  - user has the id of Emily0e62 (2024-11-14 02:03:54 - present)
  - The failed transaction used a card with last four digits 1234. (2024-09-15 00:00:00+00:00 - present)
  - The reason for the transaction failure was 'Card expired'. (2024-09-15 00:00:00+00:00 - present)
  - user has the name of Emily Painter (2024-11-14 02:03:54 - present)
  - Account Emily0e62 made a failed transaction of 99.99. (2024-07-30 00:00:00+00:00 - 2024-08-30 00:00:00+00:00)
</FACTS>
```

### Use a custom context block

Using [custom context templates](/context-templates), you can easily design your own custom context block type and retrieve that from the `thread.get_user_context()` method instead.

#### Create your custom context template

Create your custom context template for your Zep project and save the template ID. See the [Context Templates](/context-templates) guide for more information on template syntax and variables.

```python Python
from zep_cloud import Zep

client = Zep(api_key="YOUR_API_KEY")

client.context.create_context_template(
    template_id="customer-support",
    template="""# CUSTOMER PROFILE
%{user_summary}

# FACTS
%{edges limit=10}

# KEY ENTITIES
%{entities limit=5}"""
)
```

```typescript TypeScript
import { Zep } from "@getzep/zep-cloud";

const client = new Zep({ apiKey: "YOUR_API_KEY" });

await client.context.createContextTemplate({
    templateId: "customer-support",
    template: `# CUSTOMER PROFILE
%{user_summary}

# FACTS
%{edges limit=10}

# KEY ENTITIES
%{entities limit=5}`
});
```

```go Go
import (
    "context"
    zep "github.com/getzep/zep-go/v3"
    zepclient "github.com/getzep/zep-go/v3/context"
    "github.com/getzep/zep-go/v3/option"
)

client := zepclient.NewClient(
    option.WithAPIKey("YOUR_API_KEY"),
)

_, err := client.CreateContextTemplate(
    context.TODO(),
    &zep.CreateContextTemplateRequest{
        TemplateID: "customer-support",
        Template: `# CUSTOMER PROFILE
%{user_summary}

# FACTS
%{edges limit=10}

# KEY ENTITIES
%{entities limit=5}`,
    },
)
```

#### Retrieve custom context block using thread.get\_user\_context()

Retrieve your custom context block using the `thread.get_user_context()` method, passing in your template ID.

```python Python
from zep_cloud import Zep

client = Zep(api_key="YOUR_API_KEY")

user_context = client.thread.get_user_context(
    thread_id="thread_id",
    template_id="customer-support"
)
context_block = user_context.context
```

```typescript TypeScript
import { Zep } from "@getzep/zep-cloud";

const client = new Zep({ apiKey: "YOUR_API_KEY" });

const userContext = await client.thread.getUserContext("thread_id", {
    templateId: "customer-support"
});
const contextBlock = userContext.context;
```

```go Go
import (
    "context"
    zep "github.com/getzep/zep-go/v3"
    threadclient "github.com/getzep/zep-go/v3/thread/client"
    "github.com/getzep/zep-go/v3/option"
)

client := threadclient.NewClient(
    option.WithAPIKey("YOUR_API_KEY"),
)

templateID := "customer-support"
userContext, err := client.GetUserContext(
    context.TODO(),
    "thread_id",
    &zep.ThreadGetUserContextRequest{
        TemplateID: &templateID,
    },
)
contextBlock := userContext.Context
```

## Add context block to agent context window

As outlined in our [retrieval philosophy](/retrieval-philosophy), Zep optimizes for high recall over precision, meaning we err on the side of including more results even if some are less relevant. Most agents will automatically reference only the most relevant information when responding to the user message.

Once you've retrieved the Context Block, you can include this string in your agent's context window.

### Option 1: Add context block to system prompt

You can append the context block directly to your system prompt. Note that this means the system prompt dynamically updates on every chat turn.

| MessageType | Content                                                |
| ----------- | ------------------------------------------------------ |
| `System`    | Your system prompt <br /> <br /> `{Zep context block}` |
| `Assistant` | An assistant message stored in Zep                     |
| `User`      | A user message stored in Zep                           |
| ...         | ...                                                    |
| `User`      | The latest user message                                |

### Option 2: Append context block as "context message"

Dynamically updating the system prompt on every chat turn has the downside of preventing [prompt caching](https://platform.openai.com/docs/guides/prompt-caching) with LLM providers. In order to reap the benefits of prompt caching while still adding a new Zep context block in every chat, you can append the context block as a "context message" (technically a tool message) just after the user message in the chat history. On each new chat turn, remove the prior context message and replace it with the new one. This allows everything before the context message to be cached.

| MessageType | Content                                |
| ----------- | -------------------------------------- |
| `System`    | Your system prompt (static, cacheable) |
| `Assistant` | An assistant message stored in Zep     |
| `User`      | A user message stored in Zep           |
| ...         | ...                                    |
| `User`      | The latest user message                |
| `Tool`      | `{Zep context block}`                  |

## Add assistant response to Zep

After generating the assistant response, add it to Zep to continue building the user's knowledge graph.

```python Python
from zep_cloud.types import Message
from datetime import datetime, timezone

messages = [
    Message(
        created_at=datetime.now(timezone.utc).isoformat(),
        name="AI Assistant",
        role="assistant",
        content="Octavia Butler was an influential American science fiction writer...",
    )
]

response = zep_client.thread.add_messages(thread_id, messages=messages)
```

```typescript TypeScript
import type { Message } from "@getzep/zep-cloud/api";

const messages: Message[] = [
    {
        createdAt: new Date().toISOString(),
        name: "AI Assistant",
        role: "assistant",
        content: "Octavia Butler was an influential American science fiction writer...",
    },
];

const response = await zepClient.thread.addMessages(threadId, { messages });
```

```go Go
import (
    "time"
    v3 "github.com/getzep/zep-go/v3"
)

assistantName := "AI Assistant"
messages := []*v3.Message{
    {
        CreatedAt: v3.String(time.Now().UTC().Format(time.RFC3339)),
        Name:    &assistantName,
        Role:    "assistant",
        Content: "Octavia Butler was an influential American science fiction writer...",
    },
}

response, err := zepClient.Thread.AddMessages(
    context.TODO(),
    threadId,
    &v3.AddThreadMessagesRequest{
        Messages: messages,
    },
)
```

## Next steps

Now that you've integrated Zep into your application, you can explore additional features:

* **[Customize graph structure to your domain](/customizing-graph-structure)** - Define custom entity and edge types to structure domain-specific information.
* **[Add user interactions and metadata](/adding-data-to-the-graph)** - Any ongoing user interactions or one-time user profile information can be added to the user's knowledge graph.
* **[Custom context templates](/context-templates)** - Design custom context block formats tailored to your application's needs.
* **[User summary instructions](/users#user-summary-instructions)** - Customize how Zep generates summaries of user data in their knowledge graph.