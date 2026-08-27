> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Retrieving Context

> Retrieve a token-efficient, prompt-ready Context Block from a user's Context Graph. Smart Context Assembly selects the most relevant facts and Observations in under 200ms.

Zep provides three methods for retrieving context from a User Graph, each offering different levels of control and customization.

## Choosing a retrieval method

| Method                                                                          | Query Control               | Format Control | Graph Types                      | Best For                                                   |
| ------------------------------------------------------------------------------- | --------------------------- | -------------- | -------------------------------- | ---------------------------------------------------------- |
| [**Zep's Context Block**](#zeps-context-block)                                  | Automatic (last 2 messages) | Fixed          | User graphs only                 | Most use cases - automatic relevance with optimized format |
| [**Custom Context Templates**](#custom-context-templates)                       | Automatic (last 2 messages) | Custom         | User graphs only                 | Consistent custom formatting across threads/users          |
| [**Advanced Context Block Construction**](#advanced-context-block-construction) | Full control                | Full control   | User graphs or standalone graphs | Maximum flexibility - custom queries and formats           |

---

## Zep's Context Block

Zep's Context Block is an optimized, automatically assembled string that you can directly provide as context to your agent. It is built using Smart Context Assembly (i.e. [auto search](/searching-the-graph#auto-search)). The Context Block combines semantic search, full text search, and breadth first search to return context that is highly relevant to the user's current conversation slice, utilizing the past two messages.

The Context Block is returned by the `thread.get_user_context()` method. This method uses the latest messages of the *given thread* to search the (entire) User Graph and then returns the search results in the form of the Context Block.

Note that although `thread.get_user_context()` only requires a thread ID, it is able to return context derived from any thread of that user. The thread is just used to determine what's relevant.

The Context Block provides low latency (P95 \< 200ms) while preserving detailed information from the user's graph.

### Retrieving the Context Block

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

### Context Block Format

The Context Block returns a user summary along with relevant facts in a structured format:

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

The default Context Block can include the user summary, facts, entities, episodes, observations, and thread summaries. Smart Context Assembly selects which [context types](/context-types) appear based on relevance to the current conversation. To pin specific types or counts, use a [context template](/context-templates) or [advanced context block construction](/advanced-context-block-construction).

### Getting the Context Block Sooner

You can get the Context Block sooner by passing in the `return_context=True` flag to the `thread.add_messages()` method. Read more about this in our [performance guide](/performance#get-the-context-block-sooner).

## Custom Context Templates

You can customize the format of the Context Block by using [context templates](/context-templates). Templates allow you to define how context data is structured and presented while keeping Zep's automatic relevance detection.

To use a template, pass the `template_id` parameter when retrieving context:

```python Python
from zep_cloud import Zep

client = Zep(api_key="YOUR_API_KEY")

# Create a custom template
client.context.create_context_template(
    template_id="customer-support",
    template="""# CUSTOMER PROFILE
%{user_summary}

# FACTS
%{edges limit=10}

# KEY ENTITIES
%{entities limit=5}"""
)

# Use the template to retrieve context
user_context = client.thread.get_user_context(
    thread_id="thread_id",
    template_id="customer-support"
)
context_block = user_context.context
```

```typescript TypeScript
import { Zep } from "@getzep/zep-cloud";

const client = new Zep({ apiKey: "YOUR_API_KEY" });

// Create a custom template
await client.context.createContextTemplate({
    templateId: "customer-support",
    template: `# CUSTOMER PROFILE
%{user_summary}

# FACTS
%{edges limit=10}

# KEY ENTITIES
%{entities limit=5}`
});

// Use the template to retrieve context
const userContext = await client.thread.getUserContext("thread_id", {
    templateId: "customer-support"
});
const contextBlock = userContext.context;
```

```go Go
import (
    "context"
    zep "github.com/getzep/zep-go/v3"
    zepclient "github.com/getzep/zep-go/v3/context"
    threadclient "github.com/getzep/zep-go/v3/thread/client"
    "github.com/getzep/zep-go/v3/option"
)

contextClient := zepclient.NewClient(
    option.WithAPIKey("YOUR_API_KEY"),
)

threadClient := threadclient.NewClient(
    option.WithAPIKey("YOUR_API_KEY"),
)

// Create a custom template
_, err := contextClient.CreateContextTemplate(
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

// Use the template to retrieve context
templateID := "customer-support"
userContext, err := threadClient.GetUserContext(
    context.TODO(),
    "thread_id",
    &zep.ThreadGetUserContextRequest{
        TemplateID: &templateID,
    },
)
contextBlock := userContext.Context
```

See the [Context Templates](/context-templates) guide to learn how to create and manage templates.

## Advanced Context Block Construction

For maximum control over context retrieval, see our [Advanced Context Block Construction](/cookbook/advanced-context-block-construction) cookbook. This approach lets you directly [search the graph](/searching-the-graph) and assemble results with complete control over search queries, parameters, and formatting.

## Using Context

### Provide the Context Block in Your System Prompt

Once you've retrieved the [Context Block](#zeps-context-block), used a [custom context template](/context-templates), or [constructed your own context block](/cookbook/advanced-context-block-construction), you can include this string in your system prompt:

| MessageType | Content                                                |
| ----------- | ------------------------------------------------------ |
| `System`    | Your system prompt <br /> <br /> `{Zep context block}` |
| `Assistant` | An assistant message stored in Zep                     |
| `User`      | A user message stored in Zep                           |
| ...         | ...                                                    |
| `User`      | The latest user message                                |

### Provide the Last 4 to 6 Messages of the Thread

You should also include the last 4 to 6 messages of the thread when calling your LLM provider. Because Zep's ingestion can take a few minutes, the context block may not include information from the last few messages; and so the context block acts as the "long-term context," and the last few messages serve as the raw, short-term context.