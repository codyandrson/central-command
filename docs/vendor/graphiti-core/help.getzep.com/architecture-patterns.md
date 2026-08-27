> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Architecture patterns

Every Zep integration is shaped by a few key architectural choices — what context to store, how to ingest it, and how to retrieve it. This page first walks through those choices, then presents some of the most common architecture patterns that result from combining them. The patterns below are not exhaustive; your architecture may combine these choices differently depending on your use case.

## Key architectural choices

These are the key architectural choices to consider before implementing Zep for your use case:

### Context scope

| Use case condition                                                                                                        | Recommendation                                                          | Relevant Docs                                                                                                   |
| ------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| You want to persist and retrieve **user-specific context** (for example: chat history, preferences, support interactions) | Use a **user context graph**                                            | [Create a Zep user](/quick-start-guide#create-a-zep-user-for-each-of-your-users)                                |
| You want to persist and retrieve **domain context** (for example: company policies, product data, runbooks)               | Use a **standalone context graph**                                      | [Create Graph](/create-graph)                                                                                   |
| You want to persist and retrieve both **user-specific context** and **domain context** in the same workflow               | Use **user + standalone context graphs** and assemble context from both | [Create a Zep user](/quick-start-guide#create-a-zep-user-for-each-of-your-users), [Create Graph](/create-graph) |

### What data you persist to Zep

| Use case condition                                                                     | Recommendation                                              | Relevant Docs                                                                                                                         |
| -------------------------------------------------------------------------------------- | ----------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| You want to persist conversational context from turns in an agent/chat loop            | Use **Thread API** for chat ingestion and thread continuity | [Create a Zep thread](/quick-start-guide#create-a-zep-thread-for-each-of-your-threads)                                                |
| You want to persist non-chat system context (CRM, logs, tickets, emails, docs, events) | Use **Graph API** for ingestion                             | [Adding Business Data](/adding-business-data)                                                                                         |
| You want to persist both conversational context and business/system context            | Use **Thread API + Graph API** together                     | [Create a Zep thread](/quick-start-guide#create-a-zep-thread-for-each-of-your-threads), [Adding Business Data](/adding-business-data) |

### How that data reaches Zep

The table above picks the API by what the data is. This choice is separate, and it turns on whether the data is a live conversation turn, an on-disk import, or an individual in-memory write.

| Use case condition                                                                                 | Recommendation                                   | Relevant Docs                                 |
| -------------------------------------------------------------------------------------------------- | ------------------------------------------------ | --------------------------------------------- |
| A message arrives in a live conversation and you add it as your agent sends and receives it        | Call `thread.add_messages` from your application | [Adding Messages](/adding-messages)           |
| You are loading existing history from files on disk, or a recurring export lands files on disk     | Build an import with **`zep-ingest`**            | [Create an Ingestion Pipeline](/zep-ingest)   |
| Your application already holds the data in memory (webhook payload, API response, single document) | Call `graph.add` directly                        | [Adding Business Data](/adding-business-data) |

### Retrieval strategy

| Use case condition                                                        | Recommendation                                                                                                                                                             | Relevant Docs                                             |
| ------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------- |
| You want context retrieved deterministically, before every agent response | Retrieve or assemble a context block from Zep on each turn. Options include: Zep's default context block, custom context templates, or advanced context block construction | [Retrieve methods](/assembling-context#available-methods) |
| You want retrieval to happen through agent tool calls                     | Expose `graph.search()` as a tool and let the LLM call it when needed                                                                                                      | [Searching the Graph](/searching-the-graph)               |

Retrieval strategy describes *when* and *how* context is pulled. *What kinds* of context exist — facts, entities, episodes, thread summaries, observations, and the user summary — is a separate decision. See [Context Types](/context-types) for descriptions of each primitive and when to reach for it.

### Deployment and governance

Each pattern above runs across Zep's deployment models: Cloud (fully managed), Cloud + BYOK (you supply your own keys), and BYOC (Zep runs inside your VPC). The trust boundary moves with your deployment, so you can adopt any pattern without changing where your context lives. See [Security & Compliance](/security-compliance) for deployment and encryption details, and [Governance](/governance) for team access and policy-based access control to context.

---

## Pattern 1: ingesting conversations + user data

**Problem**: Your conversational agent forgets chat history between sessions and has no context about the user beyond the current conversation.

This is the most common pattern for chatbots and conversational assistants. Chat messages are persisted through the Thread API, and user-specific business data (CRM records, support history, etc.) is sent to the user's context graph via the Graph API. Context is retrieved from the user's context graph before each response.

### Architecture diagram

<defs>
  <polygon points="0 0, 8 4, 0 8" fill="#4226AA" />
</defs>

YOUR APPLICATION

Agent

User Data

(CRM, events)

Zep

Thread

User Graph

<rect x="166" y="214" width="88" height="16" rx="4" fill="#FFFFFF" />

add\_messages()

<rect x="280" y="214" width="108" height="16" rx="4" fill="#FFFFFF" />

get\_user\_context()

<rect x="486" y="214" width="62" height="16" rx="4" fill="#FFFFFF" />

graph.add()

<rect x="255" y="318" width="130" height="16" rx="4" fill="#FFFFFF" />

auto-extract to graph

Messages flow into Zep via the Thread API. Zep extracts facts and entities into the user's context graph. Business data (CRM records, user events, etc.) can be sent directly to the graph via `graph.add()`. When you call `get_user_context()`, Zep uses the latest messages in the thread as a search query to assemble relevant context from the user's context graph.

### When to use

* You need **user-specific context** in a conversational experience.
* You want to persist **chat messages** to Zep using the Thread API.
* You may also want to persist **user-specific business data** (CRM/events) using Graph API.
* You want **automatic context retrieval per turn** via `get_user_context()`.

### How to implement

This is the pattern used in the [Quick Start Guide](/quick-start-guide), which walks through the complete end-to-end setup.

---

## Pattern 2: ingesting domain data

**Problem**: Your agent needs access to company knowledge, business data, or other domain context that doesn't come from a chat conversation.

Use this pattern when you want to populate Zep with domain knowledge, business data, or external data sources that exist outside of conversations. This is common for background data ingestion pipelines and agents that don't have a traditional chat interface.

### Architecture diagram

<defs>
  <polygon points="0 0, 8 4, 0 8" fill="#4226AA" />
</defs>

YOUR APPLICATION

Agent

Docs

(policies, products)

Tickets

(Jira, Zendesk)

Communications

(email, Slack, Teams)

Zep

Standalone Graph

<rect x="319" y="235" width="68" height="18" rx="4" fill="#FFFFFF" />

graph.add()

<rect x="459" y="235" width="68" height="18" rx="4" fill="#FFFFFF" />

graph.add()

<rect x="599" y="235" width="68" height="18" rx="4" fill="#FFFFFF" />

graph.add()

<rect x="141" y="235" width="96" height="18" rx="4" fill="#FFFFFF" />

graph.search()

Data from any source is ingested directly into a context graph via `graph.add()`. The agent retrieves context via `graph.search()` without needing a thread.

### When to use

* You need **domain knowledge** such as company policies, product data, or runbooks.
* You are ingesting **business data, events, logs, or transcripts** directly with Graph API.
* You do not need thread-based chat persistence for this workflow.
* You want **deterministic retrieval** via direct `graph.search()` calls in application code.

### How to implement

This is the pattern used in the [Give Your Agent Domain Knowledge](/give-your-agent-domain-knowledge) cookbook, which walks through ingesting data into a graph and searching it end-to-end.

**Related guides**: [Adding Business Data](/adding-business-data), [Searching the Graph](/searching-the-graph), [Group Chat FAQ](/faq#how-do-i-add-messages-or-manage-context-for-a-group-chat-with-multiple-people)

---

## Pattern 3: ingesting conversations + user data with tool-call retrieval

**Problem**: Your conversational agent forgets chat history between sessions and has no context about the user beyond the current conversation — but you want the agent to decide when to retrieve context rather than injecting it on every turn.

This pattern uses the same ingestion as Pattern 1 — messages are persisted through the Thread API and business data is sent to the graph — but retrieval happens through agent tool calls instead of automatically. The LLM decides whether and when to search based on the conversation context.

### Architecture diagram

<defs>
  <polygon points="0 0, 8 4, 0 8" fill="#4226AA" />
</defs>

YOUR APPLICATION

Agent

User Data

(CRM, events)

Zep

Thread

User Graph

<rect x="166" y="214" width="88" height="16" rx="4" fill="#FFFFFF" />

add\_messages()

<rect x="280" y="214" width="118" height="16" rx="4" fill="#FFFFFF" />

graph.search() (tool)

<rect x="486" y="214" width="62" height="16" rx="4" fill="#FFFFFF" />

graph.add()

<rect x="255" y="318" width="130" height="16" rx="4" fill="#FFFFFF" />

auto-extract to graph

Ingestion is identical to Pattern 1: messages flow into Zep via the Thread API, facts are auto-extracted into the graph, and business data can be sent directly via `graph.add()`. The difference is retrieval — instead of calling `get_user_context()` on every turn, the agent exposes `graph.search()` as a tool and the LLM decides when to call it.

### When to use

* You are ingesting **conversations and user data** the same way as Pattern 1.
* You want the **LLM to decide** when context retrieval is needed, rather than retrieving on every turn.
* You are building a **multi-tool agent** where context search is one of several capabilities.
* Your agent has **variable context needs** per interaction — some turns need retrieval, others don't.

### How to implement

Follow the [Quick Start Guide](/quick-start-guide) for ingestion setup, but instead of calling `get_user_context()` on every turn, expose [`graph.search()`](/searching-the-graph) as a tool in your agent framework and let the LLM call it when needed.

**Related guides**: [Searching the Graph](/searching-the-graph), [LangGraph Integration](/langgraph-memory)

---

## Pattern 4: ingesting conversations, user data, and domain data

**Problem**: Your conversational agent needs both user-specific context (chat history, preferences, account details) and domain context (company policies, product catalog, runbooks) to generate informed responses.

This pattern combines Patterns 1 and 2. Chat messages are persisted through the Thread API, user-specific business data goes to the user graph, and domain knowledge goes to a standalone graph. At retrieval time, the agent assembles context from both graphs and includes both in the LLM's context window.

### Architecture diagram

<defs>
  <polygon points="0 0, 8 4, 0 8" fill="#4226AA" />
</defs>

INGESTION

YOUR APPLICATION

Agent

User Data

(CRM, events)

Domain Data

(docs, tickets, comms)

Zep

Thread

User Graph

Standalone Graph

<rect x="116" y="147" width="88" height="16" rx="3" fill="#FFFFFF" />

add\_messages()

<rect x="316" y="147" width="62" height="16" rx="3" fill="#FFFFFF" />

graph.add()

<rect x="541" y="147" width="62" height="16" rx="3" fill="#FFFFFF" />

graph.add()

<rect x="162" y="212" width="35" height="14" rx="2" fill="#FFFFFF" />

extract

<defs>
  <polygon points="0 0, 8 4, 0 8" fill="#4226AA" />
</defs>

RETRIEVAL

YOUR APPLICATION

Agent

Zep

User Graph

Standalone Graph

<rect x="216" y="139" width="108" height="16" rx="3" fill="#FFFFFF" />

get\_user\_context()

<rect x="456" y="139" width="88" height="16" rx="3" fill="#FFFFFF" />

graph.search()

Chat messages flow into Zep via the Thread API, and facts are auto-extracted into the user graph. User-specific business data is sent to the user graph via `graph.add()`, while domain data (product catalogs, policies, etc.) is sent to a standalone graph. At retrieval time, the agent calls `get_user_context()` for user context and `graph.search()` on the standalone graph for domain context, then includes both in the LLM's context window.

### When to use

* Your agent needs both **user-specific context** and **domain knowledge** in the same conversation.
* You are persisting **chat messages** via the Thread API and **user data** to a user graph.
* You are also ingesting **domain data** into a standalone graph shared across users.
* You want to assemble context from **both graphs** before each agent response.

### How to implement

This pattern combines the implementations from the [Quick Start Guide](/quick-start-guide) (user context and thread ingestion), the [Give Your Agent Domain Knowledge](/give-your-agent-domain-knowledge) cookbook (standalone graph ingestion), and the [Share Context Across Users Using Graphs](/how-to-share-context-across-users-using-graphs) cookbook (retrieving from both graphs and combining them into a single context block).

**Related guides**: [Retrieve overview](/assembling-context), [Advanced Context Block Construction](/advanced-context-block-construction), [Create Graph](/create-graph)

---

## Pattern 5: low latency architecture

**Problem**: Your agent needs to provide fast, responsive interactions — and even small increases in context retrieval latency meaningfully degrade the user experience.

Zep retrieval is sub-200ms regardless of graph size or count. This pattern applies to any of the four architectures above. The difference is that it incorporates Zep's latency optimizations to minimize the time between a user's input and the agent's response. This is especially important for voice and video agents, where latency is immediately perceptible, but it benefits any agent using Zep where user experience matters.

### When to use

* You are building a **voice or video agent** where response latency is directly felt by the user.
* Your agent has strict **latency requirements** regardless of modality.
* You want to minimize round trips and overlap Zep operations with other work in your agent loop.

### How to implement

Follow the [Performance Best Practices](/performance) guide, which covers the key latency optimizations: requesting context in the same call as message ingestion, running graph operations concurrently, and warming the user cache ahead of retrieval.