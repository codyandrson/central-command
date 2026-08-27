> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Zep vs Graph RAG

> How Zep compares to GraphRAG. GraphRAG suits static documents; Zep handles dynamic, streaming data with bi-temporal facts and sub-200ms hybrid retrieval.

While traditional [GraphRAG](https://arxiv.org/abs/2404.16130) excels at static document summarization, Zep is designed for dynamic and frequently updated datasets with continuous data updates, temporal fact tracking, and sub-200ms query latency. This makes Zep particularly suitable for providing an agent with up-to-date knowledge about an object/system or user.

GraphRAG builds a static knowledge structure through batch processing and answers queries by summarizing entity clusters with an LLM. That design fits document corpora that rarely change. Zep instead constructs a Context Graph that updates incrementally as new data arrives, tracks when each fact becomes valid or invalid using bi-temporal modeling, and retrieves with combined semantic, keyword, and graph search rather than sequential LLM summarization. The result is up-to-date context returned with sub-200ms retrieval latency, even as the underlying data continues to change.

The table below summarizes how the two approaches differ across data handling, retrieval, temporal modeling, and scalability.

| Aspect                 | GraphRAG                              | Zep                                              |
| ---------------------- | ------------------------------------- | ------------------------------------------------ |
| Primary Use            | Static document summarization         | Dynamic data management                          |
| Data Handling          | Batch-oriented processing             | Continuous, incremental updates                  |
| Knowledge Structure    | Entity clusters & community summaries | Episodic data, semantic entities, communities    |
| Retrieval Method       | Sequential LLM summarization          | Hybrid semantic, keyword, and graph-based search |
| Adaptability           | Low                                   | High                                             |
| Temporal Handling      | Basic timestamp tracking              | Explicit bi-temporal tracking                    |
| Contradiction Handling | LLM-driven summarization judgments    | Temporal edge invalidation                       |
| Query Latency          | Seconds to tens of seconds            | Sub-200ms retrieval latency                      |
| Custom Entity Types    | No                                    | Yes, customizable                                |
| Scalability            | Moderate                              | High, optimized for large datasets               |