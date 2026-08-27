> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Add User Specific Business Data to User Graphs

This guide demonstrates how to add user-specific business data to a user's knowledge graph. We'll create a user, fetch their business data, and add it to their graph.

## Create a user

First, we will initialize our client and create a new user:

```python Python
# Initialize the Zep client
zep_client = Zep(api_key=API_KEY)

# Add one example user
user_id_zep = uuid.uuid4().hex
zep_client.user.add(
    user_id=user_id_zep,
    email="cookbook@example.com"
)
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";
import { randomUUID } from "crypto";

// Initialize the Zep client
const client = new ZepClient({ apiKey: API_KEY });

// Add one example user
const userIdZep = randomUUID().replace(/-/g, "");
await client.user.add({
    userId: userIdZep,
    email: "cookbook@example.com"
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
zepClient := client.NewClient(option.WithAPIKey(API_KEY))

// Add one example user
userIDZep := uuid.New().String()
user, err := zepClient.User.Add(
    context.TODO(),
    &zep.CreateUserRequest{
        UserID: userIDZep,
        Email:  zep.String("cookbook@example.com"),
    },
)
if err != nil {
    log.Fatalf("Error: %v", err)
}
```

## Fetch and format the business data

Then, we will fetch and format the user's business data. Note that the functionality to fetch a users business data will depend on your codebase.

Also note that you could make your Zep user IDs equal to whatever internal user IDs you use to make things easier to manage. Generally, Zep user IDs, thread IDs, Graph IDs, etc. can be arbitrary strings, and can map to your app's data schema.

```python Python
# Define the function to fetch user business data
def get_user_business_data(user_id_business):
    # This function returns JSON data for the given user
    # This would vary based on your codebase
    return {}

# Placeholder for business user id
user_id_business = "placeholder_user_id"  # This would vary based on your codebase

# Retrieve the user-specific business data
user_data_json = get_user_business_data(user_id_business)

# Convert the business data to a string
json_string = json.dumps(user_data_json)
```

```typescript TypeScript
// Define the function to fetch user business data
function getUserBusinessData(userIdBusiness: string): Record<string, any> {
    // This function returns JSON data for the given user
    // This would vary based on your codebase
    return {};
}

// Placeholder for business user id
const userIdBusiness = "placeholder_user_id";  // This would vary based on your codebase

// Retrieve the user-specific business data
const userDataJson = getUserBusinessData(userIdBusiness);

// Convert the business data to a string
const jsonString = JSON.stringify(userDataJson);
```

```go Go
import (
    "encoding/json"
)

// Define the function to fetch user business data
func getUserBusinessData(userIDBusiness string) map[string]interface{} {
    // This function returns JSON data for the given user
    // This would vary based on your codebase
    return map[string]interface{}{}
}

// Placeholder for business user id
userIDBusiness := "placeholder_user_id"  // This would vary based on your codebase

// Retrieve the user-specific business data
userDataJSON := getUserBusinessData(userIDBusiness)

// Convert the business data to a string
jsonBytes, err := json.Marshal(userDataJSON)
if err != nil {
    log.Fatalf("Error: %v", err)
}
jsonString := string(jsonBytes)
```

## Add the data to the user's graph

Lastly, we will add the formatted data to the user's graph using the [graph API](/adding-data-to-the-graph):

```python Python
# Add the JSON data to the user's graph
zep_client.graph.add(
    user_id=user_id_zep,
    type="json",
    data=json_string,
)
```

```typescript TypeScript
// Add the JSON data to the user's graph
await client.graph.add({
    userId: userIdZep,
    type: "json",
    data: jsonString,
});
```

```go Go
// Add the JSON data to the user's graph
episode, err := zepClient.Graph.Add(
    context.TODO(),
    &zep.AddDataRequest{
        UserID: zep.String(userIDZep),
        Type:   zep.GraphDataTypeJSON,
        Data:   jsonString,
    },
)
if err != nil {
    log.Fatalf("Error: %v", err)
}
```

Here, we use `type="json"`, but the graph API also supports `type="text"` and `type="message"`. The `type="text"` option is useful for adding background information that is in unstructured text such as internal documents or web copy. The `type="message"` option is useful for adding data that is in a message format but is not your user's chat history, such as emails. [Read more about this here](/adding-data-to-the-graph).

Also, note that when adding data to the graph, you should consider the size of the data you are adding and our payload limits. [Read more about this here](/docs/performance/performance-best-practices#optimizing-memory-operations).

## Summary

You have now successfully added user-specific business data to a user's knowledge graph, which can be used alongside chat history to create user context.