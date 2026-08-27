> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Key Concepts

Looking to just get coding? Check out our [Quickstart](/quickstart).

## Summary

| Concept                       | Description                                                                                                                                                                                                      | Docs                                                  |
| ----------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------- |
| Knowledge Graph               | Zep's memory store for agents. Nodes represent entities, edges represent facts/relationships. The graph updates dynamically in response to new data.                                                             | [Docs](/understanding-the-graph)                      |
| Memory Context String         | Optimized string containing facts and entities from the knowledge graph most relevant to the current session. Also contains dates when facts became valid and invalid. Provide this to your chatbot as "memory". | [Docs](/concepts#memory-context)                      |
| Fact Invalidation             | When new data invalidates a prior fact, the time the fact became invalid is stored on that fact's edge in the knowledge graph.                                                                                   | [Docs](/facts)                                        |
| JSON/text/message             | Types of data that can be ingested into the knowledge graph. Can represent business data, documents, chat messages, emails, etc.                                                                                 | [Docs](/adding-data-to-the-graph#adding-message-data) |
| Custom entity types           | Feature allowing use of Pydantic-like classes to customize creation/retrieval of entities in the knowledge graph.                                                                                                | [Docs](/entity-types)                                 |
| User                          | Create a user in Zep to represent an individual using your application. Each user has their own knowledge graph.                                                                                                 | [Docs](/users)                                        |
| Sessions                      | Conversation threads of a user. By default, all messages added to any session of that user are ingested into that user's knowledge graph.                                                                        | [Docs](/sessions)                                     |
| Group                         | Used for creating an arbitrary knowledge graph that is not necessarily tied to a user. Useful for memory shared by a "group" of users.                                                                           | [Docs](/groups)                                       |
| `memory.add` & `graph.add`    | High level and low level methods for adding data to the knowledge graph.                                                                                                                                         | [Docs](/concepts#using-memoryadd)                     |
| `memory.get` & `graph.search` | High level and low level methods for retrieving from the knowledge graph.                                                                                                                                        | [Docs](/concepts#using-memoryget)                     |
| Fact Ratings                  | Feature for rating and filtering facts by relevance to your use case.                                                                                                                                            | [Docs](/facts#rating-facts-for-relevancy)             |

Zep is a context engineering platform that systematically assembles personalized context—user preferences, traits, and business data—for reliable agent applications. Zep combines agent memory, Graph RAG, and context assembly capabilities to deliver comprehensive personalized context that reduces hallucinations and improves accuracy.

## What is Context Engineering?

Context Engineering is the discipline of assembling all necessary information, instructions, and tools around a LLM to help it accomplish tasks reliably. Unlike simple prompt engineering, context engineering involves building dynamic systems that provide the right information in the right format so LLMs can perform consistently.

The core challenge: LLMs are stateless and only know what's in their immediate context window. Context engineering bridges this gap by systematically providing relevant background knowledge, user history, business data, and tool outputs.

Using [user chat histories and business data](#business-data-vs-chat-message-data), Zep automatically constructs a [temporal knowledge graph](#the-knowledge-graph) for each of your users. The knowledge graph contains entities, relationships, and facts related to your user. As facts change or are superseded, [Zep updates the graph](#managing-changes-in-facts-over-time) to reflect their new state. Through systematic context engineering, Zep provides your agent with the comprehensive information needed to deliver personalized responses and solve problems. This reduces hallucinations, improves accuracy, and reduces the cost of LLM calls.

<lite-vimeo videoid="1021963693" />

This guide covers key concepts for using Zep effectively:

* [How Zep fits into your application](#how-zep-fits-into-your-application)
* [The Zep Knowledge Graph](#the-knowledge-graph)
* [User vs Group graphs](#user-vs-group-graphs)
* [Managing changes in facts over time](#managing-changes-in-facts-over-time)
* [Business data vs Chat Message data](#business-data-vs-chat-message-data)
* [Users and Chat Sessions](#users-and-chat-sessions)
* [Adding Memory](#adding-memory)
* [Retrieving memory](#retrieving-memory)
* [Improving Fact Quality](#improving-fact-quality)
* [Using Zep as an agentic tool](#using-zep-as-an-agentic-tool)

## How Zep fits into your application

Your application sends Zep messages and other interactions your agent has with a human. Zep can also ingest data from your business sources in JSON, text, or chat message format. These sources may include CRM applications, emails, billing data, or conversations on other communication platforms like Slack.

<img src="https://fdr-prod-docs-files-public.s3.us-east-1.amazonaws.com/zep.docs.buildwithfern.com/1899117353e1c147f3c01a019909835ac622ad2642c52618014020d8cc13e174/images/how-zep-fits-into-app-diagram.png?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=AKIA6KXJSKKNFOCF7G4B%2F20260825%2Fus-east-1%2Fs3%2Faws4_request&X-Amz-Date=20260825T142211Z&X-Amz-Expires=604800&X-Amz-Signature=4a4177bc236ef261cc1fdda5421c8f5654257c09cc0030a19303bbc1b1d7f0a7&X-Amz-SignedHeaders=host&x-amz-checksum-mode=ENABLED&x-id=GetObject" />

Zep automatically fuses this data together on a temporal knowledge graph, building a holistic view of the user's world and the relationships between entities. Zep offers a number of APIs for [adding and retrieving memory](#retrieving-memory). In addition to populating a prompt with Zep's engineered context, Zep's search APIs can be used to build [agentic tools](#using-zep-as-an-agentic-tool).

The example below shows Zep's `memory.context` field resulting from a call to `memory.get()`. This is Zep's engineered context string that can be added to your prompt and contains facts and graph entities relevant to the current conversation with a user. For more about the temporal context of facts, see [Managing changes in facts over time](#managing-changes-in-facts-over-time).

Zep also returns a number of other artifacts in the `memory.get()` response, including raw `facts` objects. Zep's search methods can also be used to retrieve nodes, edges, and facts.

### Memory Context

Memory context is Zep's engineered context string containing relevant facts and entities for the session. It is always present in the result of `memory.get()`
call and can be optionally [received with the response of `memory.add()` call](/docs/performance/performance-best-practices#get-the-memory-context-string-sooner).

```python Python
# pass in the session ID of the conversation thread
memory = zep_client.memory.get(session_id="session_id") 
print(memory.context)
```

```text
FACTS and ENTITIES represent relevant context to the current conversation.

# These are the most relevant facts and their valid date ranges

# format: FACT (Date range: from - to)

<FACTS>
  - Emily is experiencing issues with logging in. (2024-11-14 02:13:19+00:00 -
    present) 
  - User account Emily0e62 has a suspended status due to payment failure. 
    (2024-11-14 02:03:58+00:00 - present) 
  - user has the id of Emily0e62 (2024-11-14 02:03:54 - present)
  - The failed transaction used a card with last four digits 1234. (2024-09-15
    00:00:00+00:00 - present)
  - The reason for the transaction failure was 'Card expired'. (2024-09-15
    00:00:00+00:00 - present)
  - user has the name of Emily Painter (2024-11-14 02:03:54 - present) 
  - Account Emily0e62 made a failed transaction of 99.99. (2024-07-30 
    00:00:00+00:00 - 2024-08-30 00:00:00+00:00)
</FACTS>

# These are the most relevant entities

# ENTITY_NAME: entity summary

<ENTITIES>
  - Emily0e62: Emily0e62 is a user account associated with a transaction,
    currently suspended due to payment failure, and is also experiencing issues
    with logging in. 
  - Card expired: The node represents the reason for the transaction failure, 
    which is indicated as 'Card expired'. 
  - Magic Pen Tool: The tool being used by the user that is malfunctioning. 
  - User: user 
  - Support Agent: Support agent responding to the user's bug report. 
  - SupportBot: SupportBot is the virtual assistant providing support to the user, 
    Emily, identified as SupportBot. 
  - Emily Painter: Emily is a user reporting a bug with the magic pen tool, 
    similar to Emily Painter, who is expressing frustration with the AI art
    generation tool and seeking assistance regarding issues with the PaintWiz app.
</ENTITIES>
```

You can then include this context in your system prompt:

| MessageType | Content                                                 |
| ----------- | ------------------------------------------------------- |
| `System`    | Your system prompt <br /> <br /> `{Zep context string}` |
| `Assistant` | An assistant message stored in Zep                      |
| `User`      | A user message stored in Zep                            |
| ...         | ...                                                     |
| `User`      | The latest user message                                 |

## The Knowledge Graph

#### What is a Knowledge Graph?

A knowledge graph is a network of interconnected facts, such as *"Kendra loves
Adidas shoes."* Each fact is a *"triplet"* represented by two entities, or
nodes (*"Kendra", "Adidas shoes"*), and their relationship, or edge
(*"loves"*).

<br />

Knowledge Graphs have been explored extensively for information retrieval.
What makes Zep unique is its ability to autonomously build temporal knowledge graphs
while handling changing relationships and maintaining historical context.

Zep automatically constructs a temporal knowledge graph for each of your users. The knowledge graph contains entities, relationships, and facts related to your user, while automatically handling changing relationships and facts over time.

Here's an example of how Zep might extract graph data from a chat message, and then update the graph once new information is available:

![graphiti intro slides](https://fdr-prod-docs-files-public.s3.us-east-1.amazonaws.com/zep.docs.buildwithfern.com/204428cea99ae5c2c2055235e6cab2c0b5bc5aad3d7fdfbb1e3792cf4cd9e8fb/images/graphiti-graph-intro.gif?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=AKIA6KXJSKKNFOCF7G4B%2F20260825%2Fus-east-1%2Fs3%2Faws4_request&X-Amz-Date=20260825T142211Z&X-Amz-Expires=604800&X-Amz-Signature=9e63bfc017771372bde90c69d2195ff5c37f61ab4f9aa2192b09676216c85e1e&X-Amz-SignedHeaders=host&x-amz-checksum-mode=ENABLED&x-id=GetObject)

Each node and edge contains certain attributes - notably, a fact is always stored as an edge attribute. There are also datetime attributes for when the fact becomes [valid and when it becomes invalid](#managing-changes-in-facts-over-time).

## User vs Group graphs

Zep automatically creates a knowledge graph for each User of your application. You as the developer can also create a ["group graph"](/groups) (which is best thought of as an "arbitrary graph") for memory to be used by a group of Users, or for a more complicated use case.

For example, you could create a group graph for your company's product information or even messages related to a group chat. This avoids having to add the same data to each user graph. To do so, you'd use the `graph.add()` and `graph.search()` methods (see [Retrieving memory](#retrieving-memory)).

Group knowledge is not retrieved via the `memory.get()` method and is not included in the `memory.context` string. To use user and group graphs simultaneously, you need to add group-specific context to your prompt alongside the `memory.context` string.

Read more about groups [here](/groups).

## Managing changes in facts over time

When incorporating new data, Zep looks for existing nodes and edges in graph and decides whether to add new nodes/edges or to update existing ones. An update could mean updating an edge (for example, indicating the previous fact is no longer valid).

For example, in the [animation above](#the-knowledge-graph), Kendra initially loves Adidas shoes. She later is angry that the shoes broke and states a preference for Puma shoes. As a result, Zep attempts to invalidate the fact that Kendra loves Adidas shoes and creates two new facts: "Kendra's Adidas shoes broke" and "Kendra likes Puma shoes".

Zep also looks for dates in all ingested data, such as the timestamp on a chat message or an article's publication date, informing how Zep sets the following edge attributes. This assists your agent in reasoning with time.

| Edge attribute  | Example                                         |
| :-------------- | :---------------------------------------------- |
| **created\_at** | The time Zep learned that the user got married  |
| **valid\_at**   | The time the user got married                   |
| **invalid\_at** | The time the user got divorced                  |
| **expired\_at** | The time Zep learned that the user got divorced |

The `valid_at` and `invalid_at` attributes for each fact are then included in the `memory.context` string which is given to your agent:

```text
# format: FACT (Date range: from - to)
User account Emily0e62 has a suspended status due to payment failure. (2024-11-14 02:03:58+00:00 - present)
```

## Business data vs Chat Message data

Zep can ingest either unstructured text (e.g. documents, articles, chat messages) or JSON data (e.g. business data, or any other form of structured data). Conversational data is ingested through `memory.add()` in structured chat message format, and all other data is ingested through the `graph.add()` method.

## Users and Chat Sessions

A Session is a series of chat messages (e.g., between a user and your agent). [Users](/users) may have multiple Sessions.

Entities, relationships, and facts are extracted from the messages in a Session and added to the user's knowledge graph. All of a user's Sessions contribute to a single, shared knowledge graph for that user. Read more about sessions [here](/sessions).

`SessionIDs` are arbitrary identifiers that you can map to relevant business objects in your app, such as users or a
conversation a user might have with your app.

For code examples of how to create users and sessions, see the [Quickstart Guide](/quickstart#create-a-user-and-session).

## Adding Memory

There are two ways to add data to Zep: `memory.add()` and `graph.add()`.

### Using `memory.add()`

Add your chat history to Zep using the `memory.add()` method. `memory.add` is session-specific and expects data in chat message format, including a `role` name (e.g., user's real name), `role_type` (AI, human, tool), and message `content`. Zep stores the chat history and builds a user-level knowledge graph from the messages.

For code examples of how to add messages to Zep's memory, see the [Quickstart Guide](/quickstart#adding-messages-and-retrieving-context).

For best results, add chat history to Zep on every chat turn. That is, add both the AI and human messages in a single operation and in the order that the messages were created.

Additionally, for latency-sensitive applications, you can request the memory context directly in the response to the `memory.add` call. Read more [here](/docs/performance/performance-best-practices#get-the-memory-context-string-sooner).

### Using `graph.add()`

The `graph.add()` method enables you to add business data as a JSON object or unstructured text. It also supports adding data to Group graphs by passing in a `group_id` as opposed to a `user_id`.

For code examples of how to add business data to the graph, see the [Quickstart Guide](/quickstart#adding-business-data-to-a-graph).

## Retrieving memory

There are three ways to retrieve memory from Zep: `memory.get()`, `graph.search()`, and methods for retrieving specific nodes, edges, or episodes using UUIDs.

### Using `memory.get()`

The `memory.get()` method is a user-friendly, high-level API for retrieving relevant context from Zep. It uses the latest messages of the *given session* to determine what information is most relevant from the user's knowledge graph and returns that information in a [context string](#memory-context) for your prompt. Note that although `memory.get()` only requires a session ID, it is able to return memory derived from any session of that user. The session is just used to determine what's relevant.

`memory.get` also returns recent chat messages and raw facts that may provide additional context for your agent. It is user and session-specific and cannot retrieve data from group graphs.

For code examples of how to retrieve memory context for a session, see the [Quickstart Guide](/quickstart#retrieving-context-with-memoryget).

### Using `graph.search()`

The `graph.search()` method lets you search the graph directly, returning raw edges and/or nodes (defaults to edges), as opposed to facts. You can customize search parameters, such as the reranker used. For more on how search works, visit the [Graph Search](/searching-the-graph) guide. This method works for both User and Group graphs.

For code examples of how to search the graph, see the [Quickstart Guide](/quickstart#searching-the-graph).

### Retrieving specific nodes, edges, and episodes

Zep offers several utility methods for retrieving specific nodes, edges, or episodes by UUID, or all elements for a user or group. To retrieve a fact, you just need to retrieve its edge, since a fact is always the attribute of some edge. See the [Graph SDK reference](/sdk-reference/graph) for more.

## Improving Fact Quality

By using Zep's fact rating feature, you can make Zep automatically assign a rating to every fact using your own custom rating instruction. Then, when retrieving memory, you can set a minimum rating threshold so that the memory only contains the highest quality facts for your use case. Read more [here](/facts#rating-facts-for-relevancy).

## Using Zep as an agentic tool

Zep's memory retrieval methods can be used as agentic tools, enabling your agent to query Zep for relevant information. This allows your agent to access the user's knowledge graph and retrieve facts, entities, and relationships that are relevant to the current conversation.

For a complete code example of how to use Zep as an agentic tool, see the [Quickstart Guide](/quickstart#using-zep-as-an-agentic-tool).