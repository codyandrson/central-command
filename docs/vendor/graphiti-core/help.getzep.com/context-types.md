> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Context types

> The context types Zep produces from a Context Graph — facts, entities, episodes, thread summaries, observations, and the user summary — and when to use each.

A single retrieval mode loses information at some scale. A granular fact answers a precise question but loses the surrounding narrative. A long summary captures a conversation arc but obscures the specifics. A raw quote is faithful to the source but says nothing about how the user has changed over time. Zep produces several distinct types of context from a user's graph so an agent can reach for the one that fits the task at hand.

## At a glance

| Type                                  | What it captures                                                                        | In default Context Block | Status                         |
| ------------------------------------- | --------------------------------------------------------------------------------------- | ------------------------ | ------------------------------ |
| [Facts](/facts)                       | A discrete, time-scoped relationship between two entities                               | Yes (when relevant)      | Stable                         |
| [Entities](/entities)                 | A noun (person, place, thing, concept) plus a narrative summary of its history          | Yes (when relevant)      | Stable                         |
| [Episodes](/episodes)                 | The raw text, message, or JSON the developer ingested                                   | Yes (when relevant)      | Stable                         |
| [Thread summaries](/thread-summaries) | An incremental summary of a single thread's messages                                    | No                       | Stable                         |
| [Observations](/observations)         | A durable, evidence-backed pattern, decision, or commitment across one or more entities | No                       | Stable, Flex Plus / Enterprise |
| [User summary](/user-summary)         | A persistent, baseline picture of who the user is                                       | Yes (always)             | Stable                         |

## The types

[**Facts**](/facts) are granular knowledge snippets — a relationship between two entities with precise temporal validity. Reach for facts when the agent needs an exact, citable claim and the dates it was true.

[**Entities**](/entities) are graph nodes representing the nouns Zep has extracted from ingested data. Each entity carries a name and an incrementally maintained narrative summary of the facts and relationships involving it — useful when you want a contextualized history of a specific person, place, or thing rather than isolated facts.

[**Episodes**](/episodes) are the raw artifacts handed to Zep — chat messages, text chunks, JSON records — stored verbatim. Reach for episodes when an agent needs to ground a response in the original wording, cite a source quote, or recover surrounding context that did not become a fact in its own right.

[**Thread summaries**](/thread-summaries) are summaries of the messages in a single thread. Useful as an extra type of context to give your agent a different view — for example, what problem the user had in a given conversation and how it was resolved. [Document summaries](/documents) are the analogue for episodes grouped by `document_id`.

[**Observations**](/observations) are durable, evidence-backed patterns Zep derives from the graph — a recurring loop, a stable preference, a commitment, a state transition that spans multiple episodes. Reach for observations when granular facts would miss the shape of behavior across a long history.

[**User summary**](/user-summary) is an auto-generated narrative attached to the user's central entity node. It is the one type of context that is always included in the default Context Block, providing a baseline picture of the user regardless of what they just said. User summaries only exist on user graphs.

## How to assemble these into a prompt

The default [Context Block](/retrieving-context) — returned by `thread.get_user_context()` — includes the user summary plus the facts, entities, and episodes most relevant to the user's recent messages. To customize what appears, or to include thread summaries or observations, you have three options:

* **Retrieve them directly with the SDK** using the per-type APIs documented on each page.
* **Define a [context template](/context-templates)** that includes `%{user_summary}`, `%{edges}` (facts), `%{entities}`, or `%{episodes}` to control which types appear in `thread.get_user_context()` while keeping Zep's automatic relevance detection.
* **Build a custom block via [advanced context block construction](/advanced-context-block-construction)** to retrieve any combination of types — including thread summaries and observations — using your own queries and formatting.

See the [Retrieve overview](/assembling-context) for a comparison of these methods.