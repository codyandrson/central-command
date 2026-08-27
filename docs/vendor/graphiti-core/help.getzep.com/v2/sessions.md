> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

Sessions represent a conversation. Each [User](/users) can have multiple sessions, and each session is a sequence of chat messages.

Chat messages are added to sessions using [`memory.add`](/concepts#using-memoryadd), which both adds those messages to the session history and ingests those messages into the user-level knowledge graph. The user knowledge graph contains data from all of that user's sessions to create an integrated understanding of the user.

The knowledge graph does not separate the data from different sessions, but integrates the data together to create a unified picture of the user. So the [get session memory](/sdk-reference/memory/get) endpoint and the associated [`memory.get`](/concepts#using-memoryget) method don't return memory derived only from that session, but instead return whatever user-level memory is most relevant to that session, based on the session's most recent messages.

## Adding a Session

`SessionIDs` are arbitrary identifiers that you can map to relevant business objects in your app, such as users or a
conversation a user might have with your app. Before you create a session, make sure you have [created a user](/users#adding-a-user) first. Then create a session with:

```python Python
client = Zep(
    api_key=API_KEY,
)
session_id = uuid.uuid4().hex # A new session identifier

client.memory.add_session(
    session_id=session_id,
    user_id=user_id,
)
```

```typescript TypeScript
const client = new ZepClient({
  apiKey: API_KEY,
});

const sessionId: string = uuid.v4(); // Generate a new session identifier

await client.memory.addSession({
  sessionId: session_id,
  userId: userId,
});
```

## Getting a Session

```python Python
session = client.memory.get_session(session_id)
print(session.dict())
```

```typescript TypeScript
const session = await client.memory.getSession(sessionId);
console.log(session);
```

## Deleting a Session

Deleting a session deletes it and its associated messages. It does not however delete the associated data in the user's knowledge graph. To remove data from the graph, see [deleting data from the graph](/deleting-data-from-the-graph).

```python Python
client.memory.delete(session_id)
```

```typescript TypeScript
await client.memory.delete(sessionId);
```

## Listing Sessions

You can list all Sessions in the Zep Memory Store with page\_size and page\_number parameters for pagination.

```python Python
# List the first 10 Sessions
result = client.memory.list_sessions(page_size=10, page_number=1)
for session in result.sessions:
    print(session)
```

```typescript TypeScript
// List the first 10 Sessions
const { sessions } = await client.memory.listSessions({
  pageSize: 10,
  pageNumber: 1,
});
console.log("First 10 Sessions:");
sessions.forEach((session) => console.log(session));
```