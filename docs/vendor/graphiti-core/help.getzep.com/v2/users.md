> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

A User represents an individual interacting with your application. Each User can have multiple Sessions associated with them, allowing you to track and manage their interactions over time.

The unique identifier for each user is their `UserID`. This can be any string value, such as a username, email address, or UUID.

The User object and its associated Sessions provide a powerful way to manage and understand user behavior. By associating Sessions with Users, you can track the progression of conversations and interactions over time, providing valuable context and history.

In the following sections, you will learn how to manage Users and their associated Sessions.

**Users Enable Simple User Privacy Management**

Deleting a User will delete all Sessions and session artifacts associated with that User with a single API call, making it easy to handle Right To Be Forgotten requests.

## Ensuring your User data is correctly mapped to the Zep knowledge graph

Adding your user's `email`, `first_name`, and `last_name` ensures that chat messages and business data are correctly mapped to the user node in the Zep knowledge graph.

For e.g., if business data contains your user's email address, it will be related directly to the user node.

You can associate rich business context with a User:

* `user_id`: A unique identifier of the user that maps to your internal User ID.
* `email`: The user's email.
* `first_name`: The user's first name.
* `last_name`: The user's last name.

## Adding a User

You can add a new user by providing the user details.

```python Python
from zep_cloud.client import Zep

client = Zep(api_key=API_KEY)

new_user = client.user.add(
    user_id=user_id,
    email="user@example.com",
    first_name="Jane",
    last_name="Smith",
)
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({
  apiKey: API_KEY,
});

const user = await client.user.add({
  userId: user_id,
  email: "user@example.com",
  firstName: "Jane",
  lastName: "Smith",
});
```

> Learn how to associate [Sessions with Users](/sessions)

## Getting a User

You can retrieve a user by their ID.

```python Python
user = client.user.get("user123")
```

```typescript TypeScript
const user = await client.user.get("user123");
```

## Updating a User

You can update a user's details by providing the updated user details.

```python Python
updated_user = client.user.update(
    user_id=user_id,
    email="updated_user@example.com",
    first_name="Jane",
    last_name="Smith",
)
```

```typescript TypeScript
const updated_user = await client.user.update(user_id, {
  email: "updated_user@example.com",
  firstName: "Jane",
  lastName: "Smith",
  metadata: { foo: "updated_bar" },
});
```

## Deleting a User

You can delete a user by their ID.

```python Python
client.user.delete("user123")
```

```typescript TypeScript
await client.user.delete("user123");
```

## Getting a User's Sessions

You can retrieve all Sessions for a user by their ID.

```python Python
sessions = client.user.get_sessions("user123")
```

```typescript TypeScript
const sessions = await client.user.getSessions("user123");
```

## Listing Users

You can list all users, with optional limit and cursor parameters for pagination.

```python Python
# List the first 10 users
result = client.user.list_ordered(page_size=10, page_number=1)
```

```typescript TypeScript
// List the first 10 users
const result = await client.user.listOrdered({
  pageSize: 10,
  pageNumber: 1,
});
```

## Get the User Node

You can also retrieve the user's node from their graph:

```python Python
results = client.user.get_node(user_id=user_id)
user_node = results.node
print(user_node.summary)
```

```typescript TypeScript
const results = await client.user.getNode(userId);
const userNode = results.node;
console.log(userNode?.summary);
```

The user node might be used to get a summary of the user or to get facts related to the user (see ["How to find facts relevant to a specific node"](/cookbook/how-to-find-facts-relevant-to-a-specific-node)).