> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Shape the Graph

This is the **Shape the Graph** stage of [Working with Context](/working-with-context). Zep automatically extracts entities, relationships, and summaries from the data you ingest. Customize that process to fit your domain and use case.

## Available options

| Option                                                                                          | Purpose                                                                                           | Scope                             |
| ----------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------- | --------------------------------- |
| [**Default entity and edge types**](/customizing-graph-structure#default-entity-and-edge-types) | Built-in classifications for common entities and relationships                                    | Applied to user graphs by default |
| [**Custom entity and edge types**](/customizing-graph-structure#custom-entity-and-edge-types)   | Domain-specific data structures with custom attributes                                            | Project-wide or per-user/graph    |
| [**Custom instructions**](/custom-instructions)                                                 | Describe your domain and its terminology so Zep can better understand your data (Enterprise only) | Project-wide or per-user/graph    |
| [**User summary instructions**](/user-summary-instructions)                                     | Customize how user summaries are generated                                                        | Project-wide or per-user          |
| [**Observation steering**](/steering-observations) *(experimental)*                             | Guide how Zep words and categorizes generated observations                                        | Project-wide or per-user/graph    |

## When to customize

**Custom ontology** is recommended for most production use cases. When a custom ontology is defined, Zep's extraction process focuses on the entity and relationship types most important to your domain, often producing better results than defaults alone.

**Custom instructions** are useful when your domain has specialized terminology or concepts that Zep may not understand without additional context. For example, legal, medical, or financial domains each have domain-specific language that custom instructions help Zep interpret correctly.

Custom ontology and custom instructions serve different purposes. **Custom ontology** defines the *types of entities and relationships* your graph should contain (e.g., a `Restaurant` entity or a `RESTAURANT_VISIT` relationship). **Custom instructions** describe *the domain itself* — its terminology, concepts, and conventions — so Zep can better understand and interpret your data during extraction.

**User summary instructions** let you control what information is captured in each user's summary. This is especially valuable for cold-start scenarios — when a thread begins with a greeting or a low-information message, Zep doesn't have much to go on when retrieving relevant context from the graph. However, the user summary is always included in the Context Block regardless of the current conversation, so it serves as a reliable baseline of the most important information about each user. By customizing the summary instructions, you ensure that baseline contains the information most relevant to your use case.

**Observation steering** (experimental) guides how Zep words and categorizes the [observations](/observations) it generates. Provide an instruction to shape wording and relevance, and define custom observation types to label observations so you can filter for them during search. Steering changes how observations are generated, not the evidence behind them.