> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Zep vs Graphiti

> Graphiti is the open-source temporal knowledge graph framework that powers Zep. Zep is agent memory at enterprise scale — a governed Context Lake served in under 200ms.

Graphiti is the open-source temporal knowledge graph framework — the engine that turns your data into a temporal Context Graph. It builds one Context Graph per subject (a user, customer, team, or topic) and runs locally.

Zep is agent memory at enterprise scale. It runs Graphiti inside a managed system — extraction, retrieval, storage, and governance on the proprietary Context Graph Engine — and serves millions of governed Context Graphs as one Context Lake.

In short: Graphiti builds the graph; Zep operates it at scale.

| Aspect                 | Graphiti (open source)                                                          | Zep (managed, enterprise scale)                                                                                                                                             |
| ---------------------- | ------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **What it is**         | Temporal knowledge graph framework — one Context Graph per subject, run locally | Agent memory at enterprise scale — a governed Context Lake of millions of Context Graphs                                                                                    |
| **Extraction & graph** | Entity and edge extraction, bi-temporal model, fact invalidation                | Adds Observations, graph analysis, and proprietary extraction LLMs, reranker, and embedding models                                                                          |
| **Graph storage**      | Pluggable backends — Neo4j, FalkorDB, Amazon Neptune                            | Proprietary, highly scalable Context Graph Engine graph database and managed runtime                                                                                        |
| **Retrieval**          | Hybrid retrieval (vector, full-text, graph); performance depends on your setup  | Token-optimized smart retrieval and context assembly, sub-200ms at scale                                                                                                    |
| **Users & storage**    | Build your own                                                                  | Managed users, threads, and message storage                                                                                                                                 |
| **Developer tools**    | Build your own; MCP server for Claude, Cursor, and other clients                | Dashboard with graph visualization, debug logs, and API logs; SDKs for Python, TypeScript, and Go                                                                           |
| **Governance**         | Self-managed                                                                    | [Team access](/role-based-access-control) (RBAC), [agent access](/attribute-based-access-control) (ABAC), audit, retention, multi-tenant isolation, customer-key encryption |
| **Compliance**         | Self-managed                                                                    | SOC 2 Type II, HIPAA                                                                                                                                                        |
| **Deployment**         | Self-hosted                                                                     | Cloud / BYOK / BYOC                                                                                                                                                         |

## When to choose which

**Choose Graphiti** if you want a flexible OSS core and you are comfortable building and operating the surrounding system.

**Choose Zep** if you want turnkey agent memory at enterprise scale — a managed Context Lake with governance, performance, and support baked in.