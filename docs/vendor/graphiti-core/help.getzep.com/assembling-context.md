> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Retrieve

> Retrieve agent context from a user's Context Graph. Compare Zep's automatic Context Block, custom context templates, and advanced construction.

This is the **Retrieve** stage of [Working with Context](/working-with-context). After ingesting data (and optionally [shaping the graph](/customizing-context)), retrieve relevant context for your agent. Zep produces several distinct [types of context](/context-types) from a user's graph — facts, entities, episodes, thread summaries, observations, and the user summary — and offers three methods for assembling them into a string for your agent's prompt.

## Context types

Each method below assembles one or more context types into the final string. Before choosing a method, it helps to understand what each type captures and when to reach for it. See the [Context Types overview](/context-types) for the full comparison; in short, the default Context Block returned by `thread.get_user_context()` includes the user summary plus facts, entities, and episodes, while thread summaries and observations can be added via context templates or advanced construction.

## Available methods

| Method                                                            | Query Control               | Format Control | Best For                                        |
| ----------------------------------------------------------------- | --------------------------- | -------------- | ----------------------------------------------- |
| [**Zep's context block**](/retrieving-context#zeps-context-block) | Automatic (last 2 messages) | Fixed          | Most use cases - optimized relevance and format |
| [**Context templates**](/context-templates)                       | Automatic (last 2 messages) | Custom         | Consistent custom formatting across threads     |
| [**Advanced construction**](/advanced-context-block-construction) | Full control                | Full control   | Maximum flexibility with custom queries         |

## Choosing a method

**Use Zep's context block** when you want the simplest integration with optimized defaults. This works well for most conversational agents.

**Use context templates** when you need consistent custom formatting but want Zep to handle relevance detection. This is useful for structured contexts (customer support, sales) where you always want specific sections.

**Use advanced construction** when you need full control over what context is retrieved and how it's formatted. This is ideal for complex applications with custom entity types or specialized retrieval logic.