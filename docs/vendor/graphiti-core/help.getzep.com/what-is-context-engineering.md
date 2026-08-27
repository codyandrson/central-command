> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# What is context engineering?

> Context engineering assembles the right information around an LLM for reliable agents. Zep does it with temporal knowledge graphs and Smart Context Assembly.

Context Engineering is the discipline of assembling all necessary information, instructions, and tools around a LLM to help it accomplish tasks reliably. Unlike simple prompt engineering, context engineering involves building dynamic systems that provide the right information in the right format so LLMs can perform consistently.

The core challenge: LLMs are stateless and only know what's in their immediate context window. Context engineering bridges this gap by systematically providing relevant background knowledge, user history, business data, and tool outputs.

Using [business data and/or user chat histories](/concepts#business-data-vs-chat-message-data), Zep automatically constructs a [temporal knowledge graph](/graph-overview) — its Context Graph — to reflect the state of an object/system or a user. The Context Graph contains entities, relationships, and facts related to your object/system or user. As facts change or are superseded, [Zep updates the graph](/concepts#managing-changes-in-facts-over-time) to reflect their new state. Through systematic context engineering, Zep provides your agent with the information needed to deliver personalized responses and solve problems. This reduces hallucinations, improves accuracy, and reduces the cost of LLM calls.

<lite-vimeo videoid="1021963693" />