> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Give Your Agent Domain Knowledge

This guide shows you how to give your agent searchable knowledge graphs built from any text data using Zep's Graph capabilities. Zep allows you to build knowledge graphs from unstructured text, JSON, or messages—including emails, Slack messages, transcripts, [chunked](/adding-data-to-the-graph#data-size-limit-and-chunking) documents, and inventory data. [Unlike traditional RAG systems](/zep-vs-graph-rag), Zep is designed for evolving, streaming data that continuously updates your agent's knowledge.

Looking for a more in-depth understanding? Check out our [Key Concepts](/concepts) page.

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

## Create a graph

Before adding data, you need to create a standalone graph. This gives you an independent knowledge graph that isn't tied to individual users—useful for shared knowledge bases, domain-specific graphs, or specialized use cases.

```python Python
from zep_cloud.client import Zep

client = Zep(
    api_key=API_KEY,
)

# Create a standalone graph with a custom ID
result = client.graph.create(
    graph_id="my_custom_graph"
)

print(f"Created graph with ID: {result.graph_id}")
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({
  apiKey: API_KEY,
});

// Create a standalone graph with a custom ID
const result = await client.graph.create({
    graphId: "my_custom_graph"
});

console.log(`Created graph with ID: ${result.graphId}`);
```

```go Go
import (
    "context"
    "fmt"
    "log"

    "github.com/getzep/zep-go/v3"
    zepclient "github.com/getzep/zep-go/v3/client"
    "github.com/getzep/zep-go/v3/option"
)

client := zepclient.NewClient(
    option.WithAPIKey(apiKey),
)

// Create a standalone graph with a custom ID
graphID := "my_custom_graph"
result, err := client.Graph.Create(context.TODO(), &zep.CreateGraphRequest{
    GraphID: &graphID,
})
if err != nil {
    log.Fatalf("Failed to create graph: %v", err)
}

fmt.Printf("Created graph with ID: %s\n", *result.GraphID)
```

Before adding streaming data, consider seeding the graph with the core facts about its subject (for example, a company's name and industry) so later data attaches to a well-formed subject. See [Seed the graph](/create-graph#seed-the-graph).

## Add streaming data to Zep

Zep excels at building knowledge graphs from streaming data that evolves over time. You can add any unstructured text, JSON, or message data to build your knowledge graph. Common examples include:

* Customer support conversations (emails, chat logs, Slack messages)
* Meeting transcripts and notes
* [Chunked](/adding-data-to-the-graph#data-size-limit-and-chunking) documents and knowledge base articles
* Inventory data and business records (JSON format)
* Any ongoing communication or evolving business data

**Zep is optimized for evolving data, not static RAG.** While you can add static documents, Zep's knowledge graph construction shines when tracking relationships and facts that change over time.

**One-time data uploads:** If you have existing data to backfill (such as a set of documents or historical data), [`zep-ingest`](/zep-ingest) is the recommended path. It loads your sources, prepares them, submits them in order, and monitors processing. You can also loop through your data calling `graph.add` for each item, or drive the [Batch API](/adding-batch-data) yourself for large imports.

Zep supports three data types when adding data to a graph:

### Message data

Use message data when you have communications with designated speakers, such as emails or chat logs. See our [Adding Data to the Graph](/adding-data-to-the-graph#adding-message-data) guide for details.

```python Python
from zep_cloud.client import Zep

client = Zep(api_key=API_KEY)

# Add message data to a graph
message = "Sarah (customer): I need help configuring my API keys for production"

new_episode = client.graph.add(
    graph_id="customer-support",
    type="message",
    data=message
)
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({
  apiKey: API_KEY,
});

// Add message data to a graph
const message = "Sarah (customer): I need help configuring my API keys for production";

const newEpisode = await client.graph.add({
    graphId: "customer-support",
    type: "message",
    data: message
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

client := zepclient.NewClient(
    option.WithAPIKey(apiKey),
)

message := "Sarah (customer): I need help configuring my API keys for production"
graphID := "customer-support"

newEpisode, err := client.Graph.Add(context.TODO(), &zep.AddDataRequest{
    GraphID: &graphID,
    Type:    zep.GraphDataTypeMessage,
    Data:    message,
})
if err != nil {
    log.Fatalf("Failed to add message data: %v", err)
}
```

### Text data

Use text data for raw text without speaker attribution, like internal documents or wiki articles. See our [Adding Data to the Graph](/adding-data-to-the-graph#adding-text-data) guide for details. When you split a longer source into chunks, pass a [`document_id`](/documents) on every `graph.add` call.

```python Python
from zep_cloud.client import Zep

client = Zep(api_key=API_KEY)

# Add text data to a graph
text_data = "Production API keys must be configured with rate limiting enabled."

new_episode = client.graph.add(
    graph_id="company-knowledge",
    type="text",
    data=text_data
)
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({
  apiKey: API_KEY,
});

// Add text data to a graph
const textData = "Production API keys must be configured with rate limiting enabled.";

const newEpisode = await client.graph.add({
    graphId: "company-knowledge",
    type: "text",
    data: textData
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

client := zepclient.NewClient(
    option.WithAPIKey(apiKey),
)

textData := "Production API keys must be configured with rate limiting enabled."
graphID := "company-knowledge"

newEpisode, err := client.Graph.Add(context.TODO(), &zep.AddDataRequest{
    GraphID: &graphID,
    Type:    zep.GraphDataTypeText,
    Data:    textData,
})
if err != nil {
    log.Fatalf("Failed to add text data: %v", err)
}
```

### JSON data

Use JSON data for structured business data, REST API responses, or any JSON-formatted records. See our [Adding Data to the Graph](/adding-data-to-the-graph#adding-json-data) guide for details.

```python Python
from zep_cloud.client import Zep
import json

client = Zep(api_key=API_KEY)

# Add JSON data to a graph
json_data = {
    "product": {
        "id": "prod_123",
        "name": "Enterprise Plan",
        "features": ["Priority Support", "Custom Integration", "99.9% SLA"],
        "price": 299
    }
}

new_episode = client.graph.add(
    graph_id="product-catalog",
    type="json",
    data=json.dumps(json_data)
)
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({
  apiKey: API_KEY,
});

// Add JSON data to a graph
const jsonData = {
  product: {
    id: "prod_123",
    name: "Enterprise Plan",
    features: ["Priority Support", "Custom Integration", "99.9% SLA"],
    price: 299
  }
};

const newEpisode = await client.graph.add({
    graphId: "product-catalog",
    type: "json",
    data: JSON.stringify(jsonData)
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

client := zepclient.NewClient(
    option.WithAPIKey(apiKey),
)

// Add JSON data to a graph
type Product struct {
    ID       string   `json:"id"`
    Name     string   `json:"name"`
    Features []string `json:"features"`
    Price    int      `json:"price"`
}

jsonData := map[string]Product{
    "product": {
        ID:       "prod_123",
        Name:     "Enterprise Plan",
        Features: []string{"Priority Support", "Custom Integration", "99.9% SLA"},
        Price:    299,
    },
}

jsonBytes, err := json.Marshal(jsonData)
if err != nil {
    log.Fatalf("Failed to marshal JSON: %v", err)
}

graphID := "product-catalog"
newEpisode, err := client.Graph.Add(context.TODO(), &zep.AddDataRequest{
    GraphID: &graphID,
    Type:    zep.GraphDataTypeJSON,
    Data:    string(jsonBytes),
})
if err != nil {
    log.Fatalf("Failed to add JSON data: %v", err)
}
```

## Retrieve Zep context block

After adding data to your knowledge graph and before generating the AI response, you need to construct a custom context block from graph search results. Unlike user-specific context retrieval, knowledge graphs require you to manually search the graph and build the context block.

**Why context block construction?**

Knowledge graphs don't have the concept of threads or conversation history, so you need to explicitly search for relevant information and format it into a context block. This gives you full control over what information is included and how it's structured.

To build a custom context block, you'll:

1. Search the graph for relevant edges (facts) and nodes (entities) using your query
2. Format the search results into a structured context block
3. Include this context block in your agent's prompt

See our [Advanced Context Block Construction](/cookbook/advanced-context-block-construction) guide for complete examples, helper functions, and best practices for building custom context blocks from graph search results.

### Constructed context block example

Here's a simplified example of searching a knowledge graph and building a context block:

```python Python
from zep_cloud.client import Zep

client = Zep(api_key=API_KEY)

# Search for relevant edges (facts) and nodes (entities)
query = "What are the API key configuration requirements?"

edge_results = client.graph.search(
    graph_id="company-knowledge",
    query=query,
    scope="edges",
    limit=10
)

node_results = client.graph.search(
    graph_id="company-knowledge",
    query=query,
    scope="nodes",
    limit=5
)

# Build context block from results
facts = "\n".join([f"  - {edge.fact}" for edge in edge_results.edges])
entities = "\n".join([f"  - {node.name}: {node.summary}" for node in node_results.nodes])

context_block = f"""# These are relevant facts from the knowledge base
<FACTS>
{facts}
</FACTS>

# These are relevant entities from the knowledge base
<ENTITIES>
{entities}
</ENTITIES>
"""

print(context_block)
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({
  apiKey: API_KEY,
});

// Search for relevant edges (facts) and nodes (entities)
const query = "What are the API key configuration requirements?";

const edgeResults = await client.graph.search({
  graphId: "company-knowledge",
  query: query,
  scope: "edges",
  limit: 10
});

const nodeResults = await client.graph.search({
  graphId: "company-knowledge",
  query: query,
  scope: "nodes",
  limit: 5
});

// Build context block from results
const facts = edgeResults.edges.map(edge => `  - ${edge.fact}`).join("\n");
const entities = nodeResults.nodes.map(node => `  - ${node.name}: ${node.summary}`).join("\n");

const contextBlock = `# These are relevant facts from the knowledge base
<FACTS>
${facts}
</FACTS>

# These are relevant entities from the knowledge base
<ENTITIES>
${entities}
</ENTITIES>
`;

console.log(contextBlock);
```

```go Go
import (
    "context"
    "fmt"
    "strings"

    "github.com/getzep/zep-go/v3"
    zepclient "github.com/getzep/zep-go/v3/client"
    "github.com/getzep/zep-go/v3/option"
)

client := zepclient.NewClient(
    option.WithAPIKey(apiKey),
)

// Search for relevant edges (facts) and nodes (entities)
query := "What are the API key configuration requirements?"
graphID := "company-knowledge"
limit := 10

edgeResults, err := client.Graph.Search(context.TODO(), &v3.GraphSearchQuery{
    GraphID: &graphID,
    Query:   query,
    Scope:   v3.GraphSearchScopeEdges.Ptr(),
    Limit:   &limit,
})
if err != nil {
    log.Fatal("Error searching edges:", err)
}

nodeLimit := 5
nodeResults, err := client.Graph.Search(context.TODO(), &v3.GraphSearchQuery{
    GraphID: &graphID,
    Query:   query,
    Scope:   v3.GraphSearchScopeNodes.Ptr(),
    Limit:   &nodeLimit,
})
if err != nil {
    log.Fatal("Error searching nodes:", err)
}

// Build context block from results
var facts []string
for _, edge := range edgeResults.Edges {
    facts = append(facts, fmt.Sprintf("  - %s", edge.Fact))
}

var entities []string
for _, node := range nodeResults.Nodes {
    entities = append(entities, fmt.Sprintf("  - %s: %s", node.Name, *node.Summary))
}

contextBlock := fmt.Sprintf(`# These are relevant facts from the knowledge base
<FACTS>
%s
</FACTS>

# These are relevant entities from the knowledge base
<ENTITIES>
%s
</ENTITIES>
`, strings.Join(facts, "\n"), strings.Join(entities, "\n"))

fmt.Println(contextBlock)
```

For production use cases, refer to the [Advanced Context Block Construction](/cookbook/advanced-context-block-construction) guide, which includes:

* Helper functions for formatting edges and nodes
* Breadth-first search integration for recent context
* Custom entity and edge type filtering
* Temporal validity information handling
* User summary integration

## Add context block to agent context window

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

## Next steps

Now that you've learned how to give your agent knowledge through graph capabilities, you can explore additional features:

* **[Customize graph structure to your domain](/customizing-graph-structure)** - Define custom entity and edge types to structure domain-specific information.
* **[Advanced context block construction](/cookbook/advanced-context-block-construction)** - Learn advanced techniques for building optimized context blocks with helper functions, BFS integration, and custom type filtering.
* **[Searching the graph](/searching-the-graph)** - Explore advanced search capabilities and customizable parameters for querying your knowledge graphs.