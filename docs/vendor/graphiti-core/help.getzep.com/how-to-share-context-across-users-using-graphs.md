> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Share context across users using graphs

In this recipe, we will demonstrate how to share context across different users by utilizing graphs. We will set up a user thread, add graph-specific data, and integrate the OpenAI client to show how to use both user and graph context to enhance the context of a chatbot.

## Set up the user and thread

First, we initialize the Zep client, create a user, and create a thread:

```python Python
# Initialize the Zep client
zep_client = Zep(api_key="YOUR_API_KEY")  # Ensure your API key is set appropriately

# Add one example user
user_id = uuid.uuid4().hex
zep_client.user.add(
    user_id=user_id,
    first_name="Alice",
    last_name="Smith",
    email="alice.smith@example.com"
)

# Create a new thread for the user
thread_id = uuid.uuid4().hex
zep_client.thread.create(
    thread_id=thread_id,
    user_id=user_id,
)
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";
import { randomUUID } from "crypto";

// Initialize the Zep client
const zepClient = new ZepClient({ apiKey: "YOUR_API_KEY" });

// Add one example user
const userId = randomUUID().replace(/-/g, "");
await zepClient.user.add({
    userId: userId,
    firstName: "Alice",
    lastName: "Smith",
    email: "alice.smith@example.com"
});

// Create a new thread for the user
const threadId = randomUUID().replace(/-/g, "");
await zepClient.thread.create({
    threadId: threadId,
    userId: userId
});
```

```go Go
import (
    "context"
    "log"

    "github.com/getzep/zep-go/v3"
    "github.com/getzep/zep-go/v3/client"
    "github.com/getzep/zep-go/v3/option"
    "github.com/google/uuid"
)

// Initialize the Zep client
zepClient := client.NewClient(option.WithAPIKey("YOUR_API_KEY"))

// Add one example user
userId := uuid.New().String()
_, err := zepClient.User.Add(context.Background(), &zep.CreateUserRequest{
    UserID:    userId,
    FirstName: zep.String("Alice"),
    LastName:  zep.String("Smith"),
    Email:     zep.String("alice.smith@example.com"),
})
if err != nil {
    log.Fatalf("Error: %v", err)
}

// Create a new thread for the user
threadId := uuid.New().String()
_, err = zepClient.Thread.Create(context.Background(), &zep.CreateThreadRequest{
    ThreadID: threadId,
    UserID:   userId,
})
if err != nil {
    log.Fatalf("Error: %v", err)
}
```

## Create a graph and add business data

Next, we create a new graph and add structured business data to the graph, in the form of a JSON string. This step uses the [Graphs API](/graph-overview).

```python Python
graph_id = uuid.uuid4().hex
zep_client.graph.create(graph_id=graph_id)

product_json_data = [
    {
        "type": "Sedan",
        "gas_mileage": "25 mpg",
        "maker": "Toyota"
    },
    # ... more cars
]

json_string = json.dumps(product_json_data)
zep_client.graph.add(
    graph_id=graph_id,
    type="json",
    data=json_string,
)
```

```typescript TypeScript
const graphId = randomUUID().replace(/-/g, "");
await zepClient.graph.create({ graphId: graphId });

const productJsonData = [
    {
        type: "Sedan",
        gas_mileage: "25 mpg",
        maker: "Toyota"
    },
    // ... more cars
];

const jsonString = JSON.stringify(productJsonData);
await zepClient.graph.add({
    graphId: graphId,
    type: "json",
    data: jsonString
});
```

```go Go
import "encoding/json"

graphId := uuid.New().String()
_, err = zepClient.Graph.Create(context.Background(), &zep.CreateGraphRequest{
    GraphID: graphId,
})
if err != nil {
    log.Fatalf("Error: %v", err)
}

productJsonData := []map[string]string{
    {
        "type":        "Sedan",
        "gas_mileage": "25 mpg",
        "maker":       "Toyota",
    },
    // ... more cars
}

jsonBytes, err := json.Marshal(productJsonData)
if err != nil {
    log.Fatalf("Error: %v", err)
}
jsonString := string(jsonBytes)

_, err = zepClient.Graph.Add(context.Background(), &zep.AddDataRequest{
    GraphID: &graphId,
    Type:    zep.GraphDataTypeJSON,
    Data:    jsonString,
})
if err != nil {
    log.Fatalf("Error: %v", err)
}
```

## Generate a response using both contexts

Finally, we initialize the OpenAI client and define a `chatbot_response` function that retrieves user and graph context, constructs a system/developer message, and generates a contextual response. This leverages the [Threads API](/retrieving-context#zeps-context-block), [graph API](/searching-the-graph), and the OpenAI chat completions endpoint.

```python Python
# Initialize the OpenAI client
oai_client = OpenAI()

def chatbot_response(user_message, thread_id):
    # Retrieve user context
    user_context = zep_client.thread.get_user_context(thread_id)

    # Search the graph using the user message as the query
    results = zep_client.graph.search(
        graph_id=graph_id, query=user_message, scope="edges"
    )
    relevant_graph_edges = results.edges
    product_context_block = (
        "Below are some facts related to our car inventory "
        "that may help you respond to the user: \n"
    )
    for edge in relevant_graph_edges:
        product_context_block += f"{edge.fact}\n"

    # Combine context blocks for the developer message
    developer_message = (
        "You are a helpful chat bot assistant for a car sales company. "
        "Answer the user's message while taking into account the following "
        f"background information:\n{user_context.context}\n{product_context_block}"
    )

    # Generate a response using the OpenAI API
    completion = oai_client.chat.completions.create(
        model="gpt-5-mini",
        messages=[
            {"role": "developer", "content": developer_message},
            {"role": "user", "content": user_message}
        ]
    )
    response = completion.choices[0].message.content

    # Add the conversation to the thread
    messages = [
        Message(name="Alice", role="user", content=user_message),
        Message(name="AI assistant", role="assistant", content=response)
    ]
    zep_client.thread.add_messages(thread_id, messages=messages)

    return response
```

```typescript TypeScript
import OpenAI from "openai";

// Initialize the OpenAI client
const oaiClient = new OpenAI();

async function chatbotResponse(userMessage: string, threadId: string): Promise<string> {
    // Retrieve user context
    const userContext = await zepClient.thread.getUserContext(threadId);

    // Search the graph using the user message as the query
    const results = await zepClient.graph.search({
        graphId: graphId,
        query: userMessage,
        scope: "edges"
    });

    const relevantGraphEdges = results.edges || [];
    let productContextBlock =
        "Below are some facts related to our car inventory " +
        "that may help you respond to the user: \n";
    for (const edge of relevantGraphEdges) {
        productContextBlock += `${edge.fact}\n`;
    }

    // Combine context blocks for the developer message
    const developerMessage =
        "You are a helpful chat bot assistant for a car sales company. " +
        "Answer the user's message while taking into account the following " +
        `background information:\n${userContext.context}\n${productContextBlock}`;

    // Generate a response using the OpenAI API
    const completion = await oaiClient.chat.completions.create({
        model: "gpt-5-mini",
        messages: [
            { role: "developer", content: developerMessage },
            { role: "user", content: userMessage }
        ]
    });
    const response = completion.choices[0].message.content || "";

    // Add the conversation to the thread
    await zepClient.thread.addMessages(threadId, {
        messages: [
            { name: "Alice", role: "user", content: userMessage },
            { name: "AI assistant", role: "assistant", content: response }
        ]
    });

    return response;
}
```

```go Go
import (
    "context"
    "log"

    "github.com/sashabaranov/go-openai"
)

// Initialize the OpenAI client
oaiClient := openai.NewClient("YOUR_OPENAI_API_KEY")

func chatbotResponse(userMessage, threadId string) (string, error) {
    ctx := context.Background()

    // Retrieve user context
    userContext, err := zepClient.Thread.GetUserContext(
        ctx, threadId, &zep.ThreadGetUserContextRequest{},
    )
    if err != nil {
        return "", err
    }

    // Search the graph using the user message as the query
    results, err := zepClient.Graph.Search(ctx, &zep.GraphSearchQuery{
        GraphID: &graphId,
        Query:   userMessage,
        Scope:   zep.GraphSearchScopeEdges.Ptr(),
    })
    if err != nil {
        return "", err
    }

    relevantGraphEdges := results.Edges
    productContextBlock := "Below are some facts related to our car inventory " +
        "that may help you respond to the user: \n"
    for _, edge := range relevantGraphEdges {
        productContextBlock += edge.Fact + "\n"
    }

    // Combine context blocks for the developer message
    developerMessage := "You are a helpful chat bot assistant for a car sales company. " +
        "Answer the user's message while taking into account the following " +
        "background information:\n" + userContext.Context + "\n" + productContextBlock

    // Generate a response using the OpenAI API
    completion, err := oaiClient.CreateChatCompletion(ctx, openai.ChatCompletionRequest{
        Model: openai.GPT4oMini,
        Messages: []openai.ChatCompletionMessage{
            {
                Role:    "developer",
                Content: developerMessage,
            },
            {
                Role:    "user",
                Content: userMessage,
            },
        },
    })
    if err != nil {
        return "", err
    }
    response := completion.Choices[0].Message.Content

    // Add the conversation to the thread
    _, err = zepClient.Thread.AddMessages(ctx, threadId, &zep.AddThreadMessagesRequest{
        Messages: []*zep.Message{
            {
                Name:    zep.String("Alice"),
                Role:    zep.RoleTypeUserRole,
                Content: userMessage,
            },
            {
                Name:    zep.String("AI assistant"),
                Role:    zep.RoleTypeAssistantRole,
                Content: response,
            },
        },
    })
    if err != nil {
        return "", err
    }

    return response, nil
}
```

## Summary

This recipe demonstrated how to share context across users by utilizing graphs with Zep. We set up user threads, added structured graph data, and integrated the OpenAI client to generate contextual responses, sharing context across different users.