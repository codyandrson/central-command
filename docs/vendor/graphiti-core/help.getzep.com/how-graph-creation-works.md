> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# How Graph Creation Works

> How Zep builds a Context Graph from episodes: extract entities and facts, resolve them against existing graph state, summarize, and persist.

When you send data to Zep, it is stored as an episode — the raw source material remains searchable and retrievable. An LLM-driven pipeline also extracts structure from that episode and writes it into a temporal Context Graph: entities (nodes), relationships and facts (edges), tied back to the episodes they came from.

Zep also derives other context artifacts from the graph over time — for example [observations](/observations), [thread summaries](/thread-summaries), and [document summaries](/documents). This page focuses on the core ingestion path that produces entities and edges.

## From episode to graph

An **episode** is one unit of source material you send to Zep — a message, a document chunk, a JSON record, or similar. Chunks of the same source can share a [`document_id`](/documents), which scopes prior-episode context during extraction. After Zep accepts the episode, each major step below uses a language model, guided by the graph's [ontology](/customizing-graph-structure) and any [custom instructions](/custom-instructions).

| Step                                 | What happens                                                                                                                                                        |
| ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **1. Extract entities**              | The model finds candidate entities in the episode (people, teams, products, and so on).                                                                             |
| **2. Extract relationships / facts** | The model finds relationships between those entities and the facts that describe them.                                                                              |
| **3. Date facts**                    | A separate model pass assigns temporal bounds when the source supports them (`valid_at` / `invalid_at`), using the episode's event time as reference.               |
| **4. Resolve entities**              | Each extracted entity is matched against nodes already in the graph. Unresolved mentions become new nodes.                                                          |
| **5. Resolve relationships / facts** | Each extracted fact is compared to existing edges: duplicates merge, and contradictions invalidate older facts rather than leaving conflicting truths side by side. |
| **6. Summarize entities**            | Node summaries (and related user-graph summaries) are updated from the new evidence so later retrieval has concise entity context.                                  |

Zep then embeds and persists the episode, nodes, and edges into the Context Graph.

## How entity resolution works

Entity resolution is best-effort. Natural language varies (`Sarah` vs `Sarah Brown`, nicknames, abbreviations), and Zep will not always collapse every alias into one node.

When identity is unclear, Zep prefers **under-merge** over **over-merge**: better to leave two nodes than to fuse the wrong ones. A wrong merge corrupts identity in ways that are hard to undo, while duplicate nodes are usually recoverable — [high-recall retrieval](/retrieval-philosophy) often still surfaces both `Sarah` and `Sarah Brown` for the same query, so incomplete identity resolution often does not break the agent path.

Directly added nodes (`graph.add_nodes`) are not deduplicated by name: each call creates new nodes. Keep the UUIDs Zep returns if you need to update those nodes later.

## Manually updating the graph

Alongside Zep's automatic extraction process, you can write manual updates to the graph — for example adding known nodes or fact triples directly. See [Manually Updating the Graph](/adding-fact-triplets).

## What to do before ingest

For best practices on preparing source data before ingestion, see [Prepare Data for Ingestion](/prepare-data-for-ingestion).

## Related

* [Graph Overview](/graph-overview) — nodes, edges, and episodes
* [Documents](/documents) — grouping chunks of the same source
* [Prepare Data for Ingestion](/prepare-data-for-ingestion) — alias canonicalization, identity properties, timestamps, and limits
* [Customizing Graph Structure](/customizing-graph-structure) — ontology and extraction focus
* [Custom Instructions](/custom-instructions) — domain context for extraction