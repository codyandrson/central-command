> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# February 2026 deprecation wave

This page covers four deprecation categories that take effect in February 2026.

## V2 SDK deprecation

The V2 SDK is being deprecated in favor of the V3 SDK. This involves several naming and architectural changes to make the API clearer and more consistent.

The V3 SDK still uses `v2` in the API endpoint URL (e.g., `https://api.getzep.com/api/v2/...`). This is intentional and will not change. The "V3" refers to the SDK version, not the API path.

### Key changes

**Sessions → Threads**

In V2, you worked with sessions to manage conversation history. In V3, these are now called threads.

**Groups → Standalone Graphs**

V2's groups have been replaced with graphs in V3. The name "groups" was confusing, as these were actually arbitrary knowledge graphs that could hold any kind of knowledge. Using these to hold knowledge/context for a group of users was just one possible use case.

In V3, there are two types of graphs:

* **User graphs**: Automatically created for each user to store their personal knowledge
* **Standalone graphs**: Created explicitly via `graph.create()`, functionally equivalent to V2 group graphs, used as arbitrary knowledge graphs for any purpose

**Message role structure changes**

The message role structure has been updated:

* `role_type` is now called `role`
* `role` is now called `name`

**Removed session features**

The following session-related methods have been removed:

* `session.end` / `sessions.end` - No replacement needed, not necessary to end threads in Zep
* `session.classify` - Feature removed, no replacement
* `session.extract` - Feature removed, use external structured output services
* `session.synthesize_question` - Feature removed, no replacement
* `session.search` / `sessions.search` / `memory.search` - Use the default context block or search the graph directly

### Migration table

#### Python

| V2 Method/Variable/Term                                              | V3 Method/Variable/Term                                              |
| -------------------------------------------------------------------- | -------------------------------------------------------------------- |
| `memory.get(session_id)`                                             | `thread.get_user_context(thread_id)`                                 |
| `memory.get_session(session_id)`                                     | `thread.get(thread_id, limit=..., cursor=..., lastn=...)`            |
| `memory.add_session`                                                 | `thread.create`                                                      |
| `memory.add`                                                         | `thread.add_messages`                                                |
| `memory.delete`                                                      | `thread.delete`                                                      |
| `memory.list_sessions`                                               | `thread.list_all`                                                    |
| `memory.get_session_messages`                                        | `thread.get`                                                         |
| `memory.get_session_message(session_id, message_uuid)`               | `graph.episode.get(uuid_=message_uuid)`                              |
| `memory.update_message_metadata(session_id, message_uuid, metadata)` | `thread.message.update(message_uuid, metadata={...})`                |
| `memory.end_sessions`                                                | Loop through `thread.delete(thread_id)` for each thread              |
| `memory.add_session_facts`                                           | `thread.add_messages()`, `graph.add_fact_triple()`, or `graph.add()` |
| `memory.search_sessions`                                             | `graph.search()` or `thread.get_user_context()`                      |
| `group.add`                                                          | `graph.create`                                                       |
| `group.get_all_groups`                                               | `graph.list_all`                                                     |
| `group.get`                                                          | `graph.get`                                                          |
| `group.delete`                                                       | `graph.delete`                                                       |
| `group.update`                                                       | `graph.update`                                                       |
| `session`                                                            | `thread`                                                             |
| `session_id`                                                         | `thread_id`                                                          |
| `group`                                                              | `graph`                                                              |
| `group_id`                                                           | `graph_id`                                                           |
| `role_type`                                                          | `role`                                                               |
| `role`                                                               | `name`                                                               |
| `memory.search_memory`                                               | Use `thread.get_user_context()` or `graph.search()`                  |
| `memory.update_session`                                              | No direct equivalent - thread metadata has been removed              |
| `user.get_sessions`                                                  | `user.get_threads`                                                   |
| `message.get` (by UUID)                                              | `graph.episode.get(uuid_=episode_uuid)`                              |
| `message.update`                                                     | `thread.message.update(message_uuid, metadata={...})`                |
| `group.get_edges`                                                    | `graph.edge.get_by_graph_id(graph_id)` using standalone graphs       |
| `group.get_nodes`                                                    | `graph.node.get_by_graph_id(graph_id)` using standalone graphs       |
| `group.get_episodes`                                                 | `graph.episode.get_by_graph_id(graph_id)` using standalone graphs    |
| `graph.episode.get_by_group_id(group_id, ...)`                       | `graph.episode.get_by_graph_id(graph_id, lastn=...)`                 |
| `user.get_facts`                                                     | Use `thread.get_user_context()`                                      |
| `session.get_facts`                                                  | Use `thread.get_user_context()`                                      |
| `group.get_facts`                                                    | Use `graph.search()`                                                 |
| `fact.get`                                                           | `graph.edge.get(uuid_="uuid")`                                       |
| `fact.delete`                                                        | `graph.edge.delete(uuid)`                                            |

#### TypeScript

| V2 Method/Variable/Term                                          | V3 Method/Variable/Term                                           |
| ---------------------------------------------------------------- | ----------------------------------------------------------------- |
| `memory.get(sessionId)`                                          | `thread.getUserContext(threadId)`                                 |
| `memory.getSession(sessionId)`                                   | `thread.get(threadId, { limit: ..., cursor: ..., lastn: ... })`   |
| `memory.addSession`                                              | `thread.create`                                                   |
| `memory.add`                                                     | `thread.addMessages`                                              |
| `memory.delete`                                                  | `thread.delete`                                                   |
| `memory.listSessions`                                            | `thread.listAll`                                                  |
| `memory.getSessionMessages`                                      | `thread.get`                                                      |
| `memory.getSessionMessage(sessionId, messageUuid)`               | `graph.episode.get(messageUuid)`                                  |
| `memory.updateMessageMetadata(sessionId, messageUuid, metadata)` | `thread.message.update(messageUuid, { metadata: {...} })`         |
| `memory.endSessions`                                             | Loop through `thread.delete(threadId)` for each thread            |
| `memory.addSessionFacts`                                         | `thread.addMessages()`, `graph.addFactTriple()`, or `graph.add()` |
| `memory.searchSessions`                                          | `graph.search()` or `thread.getUserContext()`                     |
| `group.add`                                                      | `graph.create`                                                    |
| `group.getAllGroups`                                             | `graph.listAll`                                                   |
| `group.get`                                                      | `graph.get`                                                       |
| `group.delete`                                                   | `graph.delete`                                                    |
| `group.update`                                                   | `graph.update`                                                    |
| `session`                                                        | `thread`                                                          |
| `sessionId`                                                      | `threadId`                                                        |
| `group`                                                          | `graph`                                                           |
| `groupId`                                                        | `graphId`                                                         |
| `roleType`                                                       | `role`                                                            |
| `role`                                                           | `name`                                                            |
| `memory.searchMemory`                                            | Use `thread.getUserContext()` or `graph.search()`                 |
| `memory.updateSession`                                           | No direct equivalent - thread metadata has been removed           |
| `user.getSessions`                                               | `user.getThreads`                                                 |
| `message.get` (by UUID)                                          | `graph.episode.get(episodeUuid)`                                  |
| `message.update`                                                 | `thread.message.update(messageUuid, { metadata: {...} })`         |
| `group.getEdges`                                                 | `graph.edge.getByGraphId(graphId)` using standalone graphs        |
| `group.getNodes`                                                 | `graph.node.getByGraphId(graphId)` using standalone graphs        |
| `group.getEpisodes`                                              | `graph.episode.getByGraphId(graphId)` using standalone graphs     |
| `graph.episode.getByGroupId(groupId, ...)`                       | `graph.episode.getByGraphId(graphId, { lastn: ... })`             |
| `user.getFacts`                                                  | Use `thread.getUserContext()`                                     |
| `session.getFacts`                                               | Use `thread.getUserContext()`                                     |
| `group.getFacts`                                                 | Use `graph.search()`                                              |
| `fact.get`                                                       | `graph.edge.get({ uuid: "uuid" })`                                |
| `fact.delete`                                                    | `graph.edge.delete(edgeUuid)`                                     |

#### Go

| V2 Method/Variable/Term                                          | V3 Method/Variable/Term                                                              |
| ---------------------------------------------------------------- | ------------------------------------------------------------------------------------ |
| `Memory.Get(sessionId)`                                          | `Thread.GetUserContext(threadId, &v3.ThreadGetUserContextRequest{})`                 |
| `Memory.GetSession(sessionId)`                                   | `Thread.Get(threadId, &v3.ThreadGetRequest{Limit: ..., Cursor: ..., Lastn: ...})`    |
| `Memory.AddSession`                                              | `Thread.Create`                                                                      |
| `Memory.Add`                                                     | `Thread.AddMessages`                                                                 |
| `Memory.Delete`                                                  | `Thread.Delete`                                                                      |
| `Memory.ListSessions`                                            | `Thread.ListAll`                                                                     |
| `Memory.GetSessionMessages`                                      | `Thread.Get`                                                                         |
| `Memory.GetSessionMessage(sessionId, messageUUID)`               | `Graph.Episode.Get(messageUUID)`                                                     |
| `Memory.UpdateMessageMetadata(sessionId, messageUUID, metadata)` | `Thread.Message.Update(messageUUID, &thread.ThreadMessageUpdate{Metadata: ...})`     |
| `Memory.EndSessions`                                             | Loop through `Thread.Delete(context.TODO(), threadID)` for each thread               |
| `Memory.AddSessionFacts`                                         | `Thread.AddMessages()`, `Graph.AddFactTriple()`, or `Graph.Add()`                    |
| `Memory.SearchSessions`                                          | `Graph.Search()` or `Thread.GetUserContext()`                                        |
| `Group.Add`                                                      | `Graph.Create`                                                                       |
| `Group.GetAllGroups`                                             | `Graph.ListAll`                                                                      |
| `Group.Get`                                                      | `Graph.Get`                                                                          |
| `Group.Delete`                                                   | `Graph.Delete`                                                                       |
| `Group.Update`                                                   | `Graph.Update`                                                                       |
| `session`                                                        | `thread`                                                                             |
| `SessionID`                                                      | `ThreadID`                                                                           |
| `group`                                                          | `graph`                                                                              |
| `GroupID`                                                        | `GraphID`                                                                            |
| `RoleType`                                                       | `Role`                                                                               |
| `Role`                                                           | `Name`                                                                               |
| `Memory.SearchMemory`                                            | Use `Thread.GetUserContext()` or `Graph.Search()`                                    |
| `Memory.UpdateSession`                                           | No direct equivalent - thread metadata has been removed                              |
| `User.GetSessions`                                               | `User.GetThreads`                                                                    |
| `Message.Get` (by UUID)                                          | `Graph.Episode.Get(episodeUUID)`                                                     |
| `Message.Update`                                                 | `Thread.Message.Update(messageUUID, metadata)`                                       |
| `Group.GetEdges`                                                 | `Graph.Edge.GetByGraphID(graphID)` using standalone graphs                           |
| `Group.GetNodes`                                                 | `Graph.Node.GetByGraphID(graphID)` using standalone graphs                           |
| `Group.GetEpisodes`                                              | `Graph.Episode.GetByGraphID(graphID)` using standalone graphs                        |
| `Graph.Episode.GetByGroupID(groupID, ...)`                       | `Graph.Episode.GetByGraphID(graphID, &graph.EpisodeGetByGraphIDRequest{Lastn: ...})` |
| `User.GetFacts`                                                  | Use `Thread.GetUserContext()`                                                        |
| `Session.GetFacts`                                               | Use `Thread.GetUserContext()`                                                        |
| `Group.GetFacts`                                                 | Use `Graph.Search()`                                                                 |
| `Fact.Get`                                                       | `Graph.Edge.Get(edgeUUID)`                                                           |
| `Fact.Delete`                                                    | `Graph.Edge.Delete(edgeUUID)`                                                        |

## Fact rating deprecation

Fact ratings are being deprecated entirely. This includes:

* The `minRating` query parameter
* The `fact_rating_instruction` field on users, sessions, groups, and graphs
* The `min_fact_rating` field in graph search queries
* Methods for retrieving facts directly by rating

### What to use instead

**For customizing what facts are extracted**

Use custom ontology and/or custom user summary instructions to guide fact extraction. These provide more precise control over what information Zep extracts and stores.

**For retrieving relevant facts**

Use the default Zep context block via `getUserContext` / `get_user_context`, or create custom context templates. These methods return the most relevant facts based on semantic similarity, full text search, and graph-based search methods rather than an arbitrary rating threshold. Custom context templates allow filtering to the most relevant custom entity or edge types for your domain. See [Retrieving Context](/retrieving-context) for details.

### Migration table

| Deprecated                          | Replacement                                      |
| ----------------------------------- | ------------------------------------------------ |
| `minRating` query parameter         | Remove - use context block relevance instead     |
| `fact_rating_instruction` field     | Use custom ontology or user summary instructions |
| `min_fact_rating` in search queries | Remove - rely on default relevance ranking       |

## Mode parameter deprecation

The `mode` parameter on `getUserContext` / `get_user_context` is being deprecated. Previously this parameter could be set to "summary" or "basic" to control how context was returned. The summarization logic has been removed in favor of a fast, structured context format.

### What to do

Remove the `mode` parameter from your `getUserContext` calls. The context block now returns a structured format with user summary and structured facts. See [Retrieving Context](/retrieving-context) for details on the new format.

### Migration table

#### Python

| Deprecated                                           | Replacement                          |
| ---------------------------------------------------- | ------------------------------------ |
| `thread.get_user_context(thread_id, mode="summary")` | `thread.get_user_context(thread_id)` |
| `thread.get_user_context(thread_id, mode="basic")`   | `thread.get_user_context(thread_id)` |

#### TypeScript

| Deprecated                                             | Replacement                       |
| ------------------------------------------------------ | --------------------------------- |
| `thread.getUserContext(threadId, { mode: "summary" })` | `thread.getUserContext(threadId)` |
| `thread.getUserContext(threadId, { mode: "basic" })`   | `thread.getUserContext(threadId)` |

#### Go

| Deprecated                                                                             | Replacement                                                          |
| -------------------------------------------------------------------------------------- | -------------------------------------------------------------------- |
| `Thread.GetUserContext(threadId, &v3.ThreadGetUserContextRequest{Mode: &modeSummary})` | `Thread.GetUserContext(threadId, &v3.ThreadGetUserContextRequest{})` |
| `Thread.GetUserContext(threadId, &v3.ThreadGetUserContextRequest{Mode: &modeBasic})`   | `Thread.GetUserContext(threadId, &v3.ThreadGetUserContextRequest{})` |

## Min score parameter deprecation

The `min_score` parameter in graph search queries is being deprecated. This parameter was used to filter search results by a minimum relevance score threshold.

### What to do

Remove the `min_score` parameter from your `graph.search()` calls. Zep returns a re-ranker score with search results that you can use to manually filter results if needed. However, there is no minimum re-ranker score parameter because the interpretation of the re-ranker score can vary dramatically depending on which re-ranker is used. For more information about re-ranker scores, see [Searching the Graph - Reranker Score](/searching-the-graph#reranker-score).

You should rely on the default relevance ranking rather than filtering by an arbitrary score threshold.

### Migration table

| Deprecated                                | Replacement                                |
| ----------------------------------------- | ------------------------------------------ |
| `min_score` parameter in `graph.search()` | Remove - rely on default relevance ranking |