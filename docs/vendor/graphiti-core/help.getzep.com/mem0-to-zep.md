> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Mem0 Migration

Zep delivers agent memory at enterprise scale, unifying chat and business data into a dynamic [temporal Context Graph](/concepts) for each user. It tracks entities, relationships, and facts as they evolve, enabling you to build prompts with only the most relevant information—reducing hallucinations, improving recall, and lowering LLM costs.

Zep provides high-level APIs like `thread.get_user_context` and deep search with `graph.search`, supports custom entity/edge types, hybrid search, and granular graph updates. Mem0, by comparison, offers basic add/get/search APIs and an optional graph, but lacks built-in data unification, ontology customization, temporal fact management, and fine-grained graph control.

Got lots of data to migrate? [Contact us](mailto:sales@getzep.com) for a discount and increased API limits.

## Zep's context model in one minute

### Unified customer record

* Messages sent via [`thread.add_messages`](/adding-messages) go straight into the user's knowledge graph; business objects (JSON, docs, e-mails, CRM rows) flow in through [`graph.add`](graph/adding-data-to-the-graph.mdx). Zep automatically deduplicates entities and keeps every fact's *valid* and *invalid* dates so you always see the latest truth.

### Domain-depth ontology

* You can define Pydantic-style **[custom entity and edge classes](graph/customizing-graph-structure.mdx)** so the graph speaks your business language (Accounts, Policies, Devices, etc.).

### Temporal facts

* Every edge stores when a fact was created, became valid, was invalidated, and (optionally) expired.

### Hybrid & granular search

* [`graph.search`](graph/searching-the-graph.mdx) supports [hybrid BM25 + semantic queries, graph search](graph/searching-the-graph.mdx), with pluggable rerankers (RRF, MMR, cross-encoder) and can target nodes, edges, episodes, or everything at once.

## How Zep differs from Mem0

| Capability                          | **Zep**                                                                                                                                                           | **Mem0**                                                                                                   |
| ----------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| **Business-data ingestion**         | Native via [`graph.add`](graph/adding-data-to-the-graph.mdx) (JSON or text); [business facts merge with user graph](/concepts#business-data-vs-chat-message-data) | No direct ingestion API; business data must be rewritten as "memories" or loaded into external graph store |
| **Knowledge-graph storage**         | Built-in [temporal graph](/concepts#managing-changes-in-facts-over-time); zero infra for developers                                                               | Optional "Graph Memory" layer that *requires* Neo4j/Memgraph and extra config                              |
| **Custom ontology**                 | First-class [entity/edge type system](graph/customizing-graph-structure.mdx)                                                                                      | Not exposed; relies on generic nodes/relationships                                                         |
| **Fact life-cycle (valid/invalid)** | [Automatic and queryable](/concepts#managing-changes-in-facts-over-time)                                                                                          | Not documented / not supported                                                                             |
| **User summary customization**      | [User summary instructions](users.mdx#user-summary-instructions) customize entity summaries per user                                                              | Not available                                                                                              |
| **Search**                          | [Hybrid vector + graph search](graph/searching-the-graph.mdx) with multiple rerankers                                                                             | Vector search with filters; basic Cypher queries if graph layer enabled                                    |
| **Graph CRUD**                      | Full [node/edge CRUD](graph/deleting-data-from-the-graph.mdx) & [bulk episode ingest](graph/adding-data-to-the-graph.mdx)                                         | Add/Delete memories; no low-level edge ops                                                                 |
| **Context block**                   | [Auto-generated, temporal, prompt-ready](/retrieving-context#zeps-context-block)                                                                                  | You assemble snippets manually from `search` output                                                        |
| **LLM integration**                 | Returns [ready-made context](/retrieving-context#zeps-context-block); easily integrates with agentic tools                                                        | Returns raw strings you must format                                                                        |

## SDK support

Zep offers Python, TypeScript, and Go SDKs. See [Installation Instructions](./quickstart.mdx) for more details.

## Migrating your code

### Basic flows

| **What you do in Mem0**                                           | **Do this in Zep**                                                                                                                                                                            |
| ----------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `client.add(messages, user_id=ID)` → stores conversation snippets | `zep.thread.add_messages(thread_id, messages=[...])` – keeps chat sequence **and** updates graph                                                                                              |
| `client.add("json...", user_id=ID)` (not really supported)        | `zep.graph.add(data=<JSON>, type="json", user_id=user_id)` – drop raw business records right in                                                                                               |
| `client.search(query, user_id=ID)` – vector+filter search         | *Easy path*: `zep.thread.get_user_context(thread_id)` returns the `user_context.context` + recent messages<br />*Deep path*: `zep.graph.search(query="...", user_id=user_id, reranker="rrf")` |
| `client.get_all(user_id=ID)` – list memories                      | `zep.graph.node.get_by_user_id(user_id)` to get all nodes, or `zep.graph.edge.get_by_user_id(user_id)` to get all edges                                                                       |
| `client.update(memory_id, ...)` / `delete`                        | `zep.graph.edge.delete(uuid_="edge_uuid")` or `zep.graph.episode.delete(uuid_="episode_uuid")` for granular edits. Facts may not be updated directly; new data automatically invalidates old. |

### Practical tips

* **Thread mapping:** Map Mem0's `user_id` → Zep `user_id`, and create `thread_id` per conversation thread.
* **Business objects:** Convert external records to JSON or text and feed them through `graph.add`; Zep will handle entity linking automatically.
* **Prompting:** Replace your custom "summary builder" with the context block; it already embeds temporal ranges and entity summaries.
* **Summary customization:** Use [user summary instructions](users.mdx#user-summary-instructions) to guide how Zep generates entity summaries for each user, tailoring the context to your specific use case.
* **Search tuning:** Start with the default `rrf` reranker; switch to `mmr`, `node_distance`, `cross_encoder`, or `episode_mentions` when you need speed or precision tweaks.

## Side-by-side SDK cheat-sheet

| **Operation**            | Mem0 Method (Python)           | Zep Method (Python)                                                                  | Notes                                         |
| ------------------------ | ------------------------------ | ------------------------------------------------------------------------------------ | --------------------------------------------- |
| Add chat messages        | `m.add(messages, user_id=...)` | `zep.thread.add_messages(thread_id, messages)`                                       | Zep expects *ordered* AI + user msgs per turn |
| Add business record      | *n/a* (work-around)            | `zep.graph.add(data, type, user_id=user_id)`                                         | Direct ingestion of JSON/text                 |
| Retrieve context         | `m.search(query,... )`         | `zep.thread.get_user_context(thread_id)`                                             | Zep auto-selects facts; no prompt assembly    |
| Semantic / hybrid search | `m.search(query, ...)`         | `zep.graph.search(query="...", user_id=user_id, reranker=...)`                       | Multiple rerankers, node/edge scopes          |
| List memories            | `m.get_all(user_id)`           | `zep.graph.node.get_by_user_id(user_id)` or `zep.graph.edge.get_by_user_id(user_id)` | Get all nodes or edges for a user             |
| Update fact              | `m.update(id, ...)`            | *Not directly supported* - add new data to supersede                                 | Facts are temporal; new data invalidates old  |
| Delete fact              | `m.delete(id)`                 | `zep.graph.edge.delete(uuid_="edge_uuid")`                                           | Episode deletion removes associated edges     |
| Customize user summaries | *not supported*                | `zep.user.add_user_summary_instructions(instructions=[...], user_ids=[user_id])`     | Up to 5 custom instructions per user          |

## Where to dig deeper

* [**Quickstart**](quickstart.mdx)
* [**Graph Search guide**](graph/searching-the-graph.mdx)
* [**Entity / Edge customization**](graph/customizing-graph-structure.mdx)
* [**User summary instructions**](users.mdx#user-summary-instructions)
* **Graph CRUD**: [Reading from the Graph](graph/reading-data-from-the-graph.mdx) | [Adding to the Graph](graph/adding-data-to-the-graph.mdx) | [Deleting from the Graph](graph/deleting-data-from-the-graph.mdx)

For any questions, contact your account manager or email [support@getzep.com](mailto:support@getzep.com). Happy migrating!