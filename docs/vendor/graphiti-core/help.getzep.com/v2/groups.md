> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Groups

A user graph is tied to a specific user; a group graph is just like a user graph, except it is not tied to a specific user. It is best thought of as an "arbitrary graph" which, for example, can be used as memory for a group of users, or for a more complex use case.

For example, a group graph could store information about a company's product, which you might not want to add to every user's graph, because that would be redundant. And when your chatbot responds, it could utilize a memory context string from both that user's graph as well as from the product group graph. See our [cookbook on this](/cookbook/how-to-share-memory-across-users-using-group-graphs) for an example.

A more complicated use case could be to create a group graph which is used when a certain topic is mentioned as opposed to when certain users require a response. For instance, anytime any user mentions "pizza" in a chat, that could trigger a call to a group graph about pizza.

You do not need to add/register users with a group. Instead, you just retrieve memory from the group graph when responding to any of the users you want in the group.

## Creating a Group

```python Python
group = client.group.add(
    group_id="some-group-id", 
    description="This is a description.", 
    name="Group Name"
)
```

```typescript TypeScript
const group = await client.group.add({
    groupId: "some-group-id",
    description: "This is a description.",
    name: "Group Name"
});
```

## Adding Data to a Group Graph

Adding data to a group graph requires using the `graph.add` method. Below is an example, and for more on this method, see [Adding Data to the Graph](/adding-data-to-the-graph) and our [SDK Reference](/sdk-reference/graph/add).

```python Python
client.graph.add(
    group_id=group_id,
    data="Hello world!",
    type="text",
)
```

```typescript TypeScript
await client.graph.add({
    groupId: "some-group-id",
    data: "Hello world!",
    type: "text",
});
```

## Searching a Group Graph

Searching a group graph requires using the `graph.search` method. Below is an example, and for more on this method, see [Searching the Graph](/searching-the-graph) and our [SDK Reference](/sdk-reference/graph/search).

```python Python
search_results = client.graph.search(
    group_id=group_id,
    query="Banana",
    scope="nodes",
)
```

```typescript TypeScript
const searchResults = await client.graph.search({
    groupId: groupId,
    query: "Banana",
    scope: "nodes",
});
```

## Deleting a Group

```python Python
client.group.delete(group_id)
```

```typescript TypeScript
await client.group.delete("some-group-id");
```