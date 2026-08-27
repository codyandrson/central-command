> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Working with Context

> Work with agent memory in Zep — ingest data into Context Graphs, customize how graphs are built, and retrieve prompt-ready context.

Zep turns the data you send into agent memory: a temporal Context Graph of entities, relationships, and facts, then a token-efficient context string for your agent's prompt.

Work through the stages in order, or jump to the one you need.

| Stage               | What you do                                                                             | Start here                              |
| ------------------- | --------------------------------------------------------------------------------------- | --------------------------------------- |
| **Ingest**          | Add chat, business data, documents, or JSON so Zep can build the user's Context Graph   | [Ingest](/adding-context)               |
| **Shape the Graph** | Customize extraction — ontology, domain terminology, summaries, and observations        | [Shape the Graph](/customizing-context) |
| **Retrieve**        | Assemble facts, entities, summaries, and other context types into a prompt-ready string | [Retrieve](/assembling-context)         |

Only Ingest and Retrieve are required. Zep extracts entities, relationships, and summaries automatically, so shaping the graph is optional — reach for it when your domain needs a specific ontology or terminology.

Graph search, reads, and CRUD sit outside this lifecycle under Working with Graphs, starting with [searching the graph](/searching-the-graph).