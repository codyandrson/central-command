> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Quickstart

Looking for a more in-depth understanding? Check out our [Key Concepts](/concepts) page.

This quickstart guide will help you get up and running with Zep quickly. We will:

* Obtain an API key
* Install the SDK
* Initialize the client
* Create a user and session
* Add and retrieve messages
* View your knowledge graph
* Add business data to a user or group graph
* Search for edges or nodes in the graph

Migrating from Mem0? Check out our [Mem0 Migration](/mem0-to-zep) guide.

## Obtain an API Key

[Create a free Zep account](https://app.getzep.com/) and you will be prompted to create an API key.

## Install the SDK

### Python

Set up your Python project, ideally with [a virtual environment](https://medium.com/@vkmauryavk/managing-python-virtual-environments-with-uv-a-comprehensive-guide-ac74d3ad8dff), and then:

#### pip

```Bash
pip install zep-cloud
```

#### uv

```Bash
uv pip install zep-cloud
```

### TypeScript

Set up your TypeScript project and then:

#### npm

```Bash
npm install @getzep/zep-cloud
```

#### yarn

```Bash
yarn add @getzep/zep-cloud
```

#### pnpm

```Bash
pnpm install @getzep/zep-cloud
```

### Go

Set up your Go project and then:

```Bash
go get github.com/getzep/zep-go/v2
```

## Initialize the Client

First, make sure you have a .env file with your API key:

```
ZEP_API_KEY=your_api_key_here
```

After creating your .env file, you'll need to source it in your terminal session:

```bash
source .env
```

Then, initialize the client with your API key:

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
    "github.com/getzep/zep-go/v2"
    zepclient "github.com/getzep/zep-go/v2/client"
    "github.com/getzep/zep-go/v2/option"
    "log"
)

client := zepclient.NewClient(
    option.WithAPIKey(os.Getenv("ZEP_API_KEY")),
)
```

**The Python SDK Supports Async Use**

The Python SDK supports both synchronous and asynchronous usage. For async operations, import `AsyncZep` instead of `Zep` and remember to `await` client calls in your async code.

## Create a User and Session

Before adding messages, you need to create a user and a session. A session is a chat thread - a container for messages between a user and an assistant. A user can have multiple sessions (different conversation threads).

While messages are stored in sessions, the knowledge extracted from these messages is stored at the user level. This means that facts and entities learned in one session are available across all of the user's sessions. When you use `memory.get()`, Zep returns the most relevant memory from the user's entire knowledge graph, not just from the current session.

### Create a User

It is important to provide at least the first name and ideally the last name of the user when calling `user.add`. Otherwise, Zep may not be able to correctly associate the user with references to the user in the data you add. If you don't have this information at the time the user is created, you can add it later with our [update user](/sdk-reference/user/update) method.

```python Python
# Create a new user
user_id = "user123"
new_user = client.user.add(
    user_id=user_id,
    email="user@example.com",
    first_name="Jane",
    last_name="Smith",
)
```

```typescript TypeScript
// Create a new user
const userId = "user123";
const user = await client.user.add({
  userId: userId,
  email: "user@example.com",
  firstName: "Jane",
  lastName: "Smith",
});
```

```go Go
import (
    "context"
    v2 "github.com/getzep/zep-go/v2"
)

// Create a new user
userId := "user123"
email := "user@example.com"
firstName := "Jane"
lastName := "Smith"
user, err := client.User.Add(context.TODO(), &v2.CreateUserRequest{
    UserID:    &userId,
    Email:     &email,
    FirstName: &firstName,
    LastName:  &lastName,
})
if err != nil {
    log.Fatal("Error creating user:", err)
}
fmt.Println("User created:", user)
```

### Create a Session

```python Python
import uuid

# Generate a unique session ID
session_id = uuid.uuid4().hex

# Create a new session for the user
client.memory.add_session(
    session_id=session_id,
    user_id=user_id,
)
```

```typescript TypeScript
import { v4 as uuid } from "uuid";

// Generate a unique session ID
const sessionId = uuid();

// Create a new session for the user
await client.memory.addSession({
  sessionId: sessionId,
  userId: userId,
});
```

```go Go
import (
    "context"
    "github.com/google/uuid"
    "github.com/getzep/zep-go/v2/models"
)

// Generate a unique session ID
sessionId := uuid.New().String()

// Create a new session for the user
session, err := client.Memory.AddSession(context.TODO(), &v2.CreateSessionRequest{
    SessionID: sessionId,
    UserID:    userId,
})
if err != nil {
    log.Fatal("Error creating session:", err)
}
fmt.Println("Session created:", session)
```

## Add Messages with memory.add

Add chat messages to a session using the `memory.add` method. These messages will be stored in the session history and used to build the user's knowledge graph.

It is important to provide the name of the user in the role field if possible, to help with graph construction. It's also helpful to provide a meaningful name for the assistant in its role field.

```python Python
# Define messages to add
from zep_cloud.types import Message

messages = [
    Message(
        role="Jane",
        content="Hi, my name is Jane Smith and I work at Acme Corp.",
        role_type="user",
    ),
    Message(
        role="AI Assistant",
        content="Hello Jane! Nice to meet you. How can I help you with Acme Corp today?",
        role_type="assistant",
    )
]

# Add messages to the session
client.memory.add(session_id, messages=messages)
```

```typescript TypeScript
// Define messages to add
import type { Message } from "@getzep/zep-cloud/api";

const messages: Message[] = [
  {
    role: "Jane",
    content: "Hi, my name is Jane Smith and I work at Acme Corp.",
    roleType: "user",
  },
  {
    role: "AI Assistant",
    content: "Hello Jane! Nice to meet you. How can I help you with Acme Corp today?",
    roleType: "assistant",
  }
];

// Add messages to the session
await client.memory.add(sessionId, { messages });
```

```go Go
import (
    "context"
    "github.com/getzep/zep-go/v2/models"
)

// Define messages to add
userRole := "Jane"
assistantRole := "AI Assistant"
messages := []*v2.Message{
    {
        Role:     &userRole,
        Content:  "Hi, my name is Jane Smith and I work at Acme Corp.",
        RoleType: "user",
    },
    {
        Role:     &assistantRole,
        Content:  "Hello Jane! Nice to meet you. How can I help you with Acme Corp today?",
        RoleType: "assistant",
    },
}

// Add messages to the session
_, err = client.Memory.Add(
    context.TODO(),
    sessionId,
    &v2.AddMemoryRequest{
        Messages: messages,
    },
)
if err != nil {
    log.Fatal("Error adding messages:", err)
}
```

## Retrieve Context with memory.get

Use the `memory.get` method to retrieve relevant context for a session. This includes a context string with facts and entities and recent messages that can be used in your prompt.

```python Python
# Get memory for the session
memory = client.memory.get(session_id=session_id)

# Access the context string (for use in prompts)
context_string = memory.context
print(context_string)

# Access recent messages
recent_messages = memory.messages
for msg in recent_messages:
    print(f"{msg.role}: {msg.content}")
```

```typescript TypeScript
// Get memory for the session
const memory = await client.memory.get(sessionId);

// Access the context string (for use in prompts)
const contextString = memory.context;
console.log(contextString);

// Access recent messages
if (memory.messages) {
  memory.messages.forEach(msg => {
    console.log(`${msg.role}: ${msg.content}`);
  });
}
```

```go Go
import (
    "context"
    "fmt"
)

// Get memory for the session
memory, err := client.Memory.Get(context.TODO(), sessionId, nil)
if err != nil {
    log.Fatal("Error getting memory:", err)
}

// Access the context string (for use in prompts)
contextString := memory.Context
fmt.Println(contextString)

// Access recent messages
recentMessages := memory.Messages
for _, msg := range recentMessages {
    fmt.Printf("%s: %s\n", *msg.Role, msg.Content)
}
```

## View your Knowledge Graph

Since you've created memory, you can view your knowledge graph by navigating to [the Zep Dashboard](https://app.getzep.com/), then Users > "user123" > View Graph. You can also click the "View Episodes" button to see when data is finished being added to the knowledge graph.

## Add Business Data to a Graph

You can add business data directly to a user's graph or to a group graph using the `graph.add` method. This data can be in the form of messages, text, or JSON.

```python Python
# Add text data to a user's graph
new_episode = client.graph.add(
    user_id=user_id,
    type="text",
    data="Jane Smith is a senior software engineer who has been with Acme Corp for 5 years."
)
print("New episode created:", new_episode)
# Add JSON data to a user's graph
import json
json_data = {
    "employee": {
        "name": "Jane Smith",
        "position": "Senior Software Engineer",
        "department": "Engineering",
        "projects": ["Project Alpha", "Project Beta"]
    }
}
client.graph.add(
    user_id=user_id,
    type="json",
    data=json.dumps(json_data)
)

# Add data to a group graph (shared across users)
group_id = "engineering_team"
client.graph.add(
    group_id=group_id,
    type="text",
    data="The engineering team is working on Project Alpha and Project Beta."
)
```

```typescript TypeScript
// Add text data to a user's graph
const newEpisode = await client.graph.add({
  userId: userId,
  type: "text",
  data: "Jane Smith is a senior software engineer who has been with Acme Corp for 5 years."
});
console.log("New episode created:", newEpisode);
// Add JSON data to a user's graph
const jsonData = {
  employee: {
    name: "Jane Smith",
    position: "Senior Software Engineer",
    department: "Engineering",
    projects: ["Project Alpha", "Project Beta"]
  }
};
await client.graph.add({
  userId: userId,
  type: "json",
  data: JSON.stringify(jsonData)
});

// Add data to a group graph (shared across users)
const groupId = "engineering_team";
await client.graph.add({
  groupId: groupId,
  type: "text",
  data: "The engineering team is working on Project Alpha and Project Beta."
});
```

```go Go
import (
    "context"
    "encoding/json"
    "github.com/getzep/zep-go/v2/models"
)

// Add text data to a user's graph
data := "Jane Smith is a senior software engineer who has been with Acme Corp for 5 years."
newEpisode, err := client.Graph.Add(context.TODO(), &v2.AddDataRequest{
    UserID: &userId,
    Type:   v2.GraphDataTypeText.Ptr(),
    Data:   &data,
})
if err != nil {
    log.Fatal("Error adding text data:", err)
}
fmt.Println("New episode added:", newEpisode)

// Add JSON data to a user's graph
type Employee struct {
    Name       string   `json:"name"`
    Position   string   `json:"position"`
    Department string   `json:"department"`
    Projects   []string `json:"projects"`
}
jsonData := map[string]Employee{
    "employee": {
        Name:       "Jane Smith",
        Position:   "Senior Software Engineer",
        Department: "Engineering",
        Projects:   []string{"Project Alpha", "Project Beta"},
    },
}
jsonBytes, err := json.Marshal(jsonData)
if err != nil {
    log.Fatal("Error marshaling JSON data:", err)
}
jsonString := string(jsonBytes)
_, err = client.Graph.Add(context.TODO(), &v2.AddDataRequest{
    UserID: &userId,
    Type:   v2.GraphDataTypeJSON.Ptr(),
    Data:   &jsonString,
})
if err != nil {
    log.Fatal("Error adding JSON data:", err)
}

// Add data to a group graph (shared across users)
groupId := "engineering_team"
groupData := "The engineering team is working on Project Alpha and Project Beta."
_, err = client.Graph.Add(context.TODO(), &v2.AddDataRequest{
    GroupID: &groupId,
    Type:    v2.GraphDataTypeText.Ptr(),
    Data:    &groupData,
})
if err != nil {
    log.Fatal("Error adding group data:", err)
}

```

## Search the Graph

Use the `graph.search` method to search for edges or nodes in the graph. This is useful for finding specific information about a user or group.

```python Python
# Search for edges in a user's graph
edge_results = client.graph.search(
    user_id=user_id,
    query="What projects is Jane working on?",
    scope="edges",  # Default is "edges"
    limit=5
)

# Search for nodes in a user's graph
node_results = client.graph.search(
    user_id=user_id,
    query="Jane Smith",
    scope="nodes",
    limit=5
)

# Search in a group graph
group_results = client.graph.search(
    group_id=group_id,
    query="Project Alpha",
    scope="edges",
    limit=5
)
```

```typescript TypeScript
// Search for edges in a user's graph
const edgeResults = await client.graph.search({
  userId: userId,
  query: "What projects is Jane working on?",
  scope: "edges",  // Default is "edges"
  limit: 5
});

// Search for nodes in a user's graph
const nodeResults = await client.graph.search({
  userId: userId,
  query: "Jane Smith",
  scope: "nodes",
  limit: 5
});

// Search in a group graph
const groupResults = await client.graph.search({
  groupId: groupId,
  query: "Project Alpha",
  scope: "edges",
  limit: 5
});
```

```go Go
import (
    "context"
    "github.com/getzep/zep-go/v2/models"
)

// Search for edges in a user's graph
limit := 5
edgeResults, err := client.Graph.Search(context.TODO(), &v2.GraphSearchQuery{
    UserID: &userId,
    Query:  "What projects is Jane working on?",
    Scope:  v2.GraphSearchScopeEdges.Ptr(),
    Limit:  &limit,
})
if err != nil {
    log.Fatal("Error searching graph:", err)
}
fmt.Println("Edge search results:", edgeResults)

// Search for nodes in a user's graph
nodeResults, err := client.Graph.Search(context.TODO(), &v2.GraphSearchQuery{
    UserID: &userId,
    Query:  "Jane Smith",
    Scope:  v2.GraphSearchScopeNodes.Ptr(),
    Limit:  &limit,
})
if err != nil {
    log.Fatal("Error searching graph:", err)
}
fmt.Println("Node search results:", nodeResults)

// Search in a group graph
groupResults, err := client.Graph.Search(context.TODO(), &v2.GraphSearchQuery{
    GroupID: &groupId,
    Query:   "Project Alpha",
    Scope:   v2.GraphSearchScopeEdges.Ptr(),
    Limit:   &limit,
})
if err != nil {
    log.Fatal("Error searching graph:", err)
}
fmt.Println("Group search results:", groupResults)
```

## Use Zep as an Agentic Tool

Zep's memory retrieval methods can be used as agentic tools, enabling your agent to query Zep for relevant information.
The example below shows how to create a LangChain LangGraph tool to search for facts in a user's graph.

```python Python
from zep_cloud.client import AsyncZep

from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, MessagesState
from langgraph.prebuilt import ToolNode

zep = AsyncZep(api_key=os.environ.get('ZEP_API_KEY'))

@tool
async def search_facts(state: MessagesState, query: str, limit: int = 5):
    """Search for facts in all conversations had with a user.
    
    Args:
        state (MessagesState): The Agent's state.
        query (str): The search query.
        limit (int): The number of results to return. Defaults to 5.
    Returns:
        list: A list of facts that match the search query.
    """
    search_results = await zep.graph.search(
      user_id=state['user_name'], 
      query=query, 
      limit=limit, 
    )

    return [edge.fact for edge in search_results.edges]

tools = [search_facts]
tool_node = ToolNode(tools)
llm = ChatOpenAI(model='gpt-4o-mini', temperature=0).bind_tools(tools)
```

## Next Steps

Now that you've learned the basics of using Zep, you can:

* Learn more about [Key Concepts](/concepts)
* Explore the [Graph API](/adding-data-to-the-graph) for adding and retrieving data
* Understand [Users and Sessions](/users) in more detail
* Learn about [Memory Context](/concepts#memory-context) for building better prompts
* Explore [Graph Search](/searching-the-graph) for advanced search capabilities